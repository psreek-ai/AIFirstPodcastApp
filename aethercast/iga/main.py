import os
import logging
import uuid
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# --- Google Cloud specific imports ---
from google.cloud import aiplatform
from vertexai.preview.vision_models import ImageGenerationModel
from google.cloud import storage # Added for GCS
from google.api_core import exceptions as google_exceptions

load_dotenv()

# --- Logging Setup ---
from python_json_logger import jsonlogger # Added for JSON logging

# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="iga"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

app = Flask(__name__)

# Configure JSON logging for the Flask app
def setup_json_logging(flask_app):
    flask_app.logger.handlers.clear() # Clear existing default Flask handlers
    logHandler = logging.StreamHandler()
    service_filter = ServiceNameFilter("iga")
    logHandler.addFilter(service_filter)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s",
        rename_fields={"levelname": "level", "name": "logger_name", "asctime": "timestamp"}
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("JSON logging configured for IGA service.")

setup_json_logging(app)


iga_config = {}

def load_iga_configuration():
    global iga_config
    iga_config['IGA_HOST'] = os.getenv("IGA_HOST", "0.0.0.0")
    iga_config['IGA_PORT'] = int(os.getenv("IGA_PORT", 5007))
    iga_config['IGA_DEBUG_MODE'] = os.getenv("IGA_DEBUG_MODE", "True").lower() == "true"

    # Vertex AI Configurations
    iga_config['IGA_VERTEXAI_PROJECT_ID'] = os.getenv("IGA_VERTEXAI_PROJECT_ID", os.getenv("GCP_PROJECT_ID"))
    iga_config['IGA_VERTEXAI_LOCATION'] = os.getenv("IGA_VERTEXAI_LOCATION", os.getenv("GCP_LOCATION"))
    iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'] = os.getenv("IGA_VERTEXAI_IMAGE_MODEL_ID", "imagegeneration@006")

    # GCS Configuration
    iga_config['GCS_BUCKET_NAME'] = os.getenv("GCS_BUCKET_NAME")
    iga_config['IGA_GCS_IMAGE_PREFIX'] = os.getenv("IGA_GCS_IMAGE_PREFIX", "images/iga/") # Default GCS prefix

    # Local image directory (might be used for temp storage or if GCS is disabled, though current plan is GCS primary)
    iga_config['IGA_GENERATED_IMAGE_DIR'] = os.getenv("IGA_GENERATED_IMAGE_DIR", "/shared_audio/iga_images")

    iga_config['IGA_DEFAULT_ASPECT_RATIO'] = os.getenv("IGA_DEFAULT_ASPECT_RATIO", "1:1")
    iga_config['IGA_ADD_WATERMARK'] = os.getenv("IGA_ADD_WATERMARK", "True").lower() == "true"

    iga_config['GOOGLE_APPLICATION_CREDENTIALS'] = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

    app.logger.info("--- IGA Configuration (Vertex AI & GCS) ---")
    for key, value in iga_config.items():
        if "CREDENTIALS" in key and value:
            app.logger.info(f"  {key}: Path Set ('{os.path.basename(value) if value else 'Not Set'}')")
        elif "PASSWORD" in key and value: # Example if any passwords were here
            app.logger.info(f"  {key}: ********")
        else:
            app.logger.info(f"  {key}: {value}")
    app.logger.info("--- End IGA Configuration ---")

    # Critical startup checks
    if not iga_config['IGA_VERTEXAI_PROJECT_ID']:
        app.logger.critical("CRITICAL: IGA_VERTEXAI_PROJECT_ID is not set.")
        raise ValueError("IGA_VERTEXAI_PROJECT_ID is not set.")
    if not iga_config['IGA_VERTEXAI_LOCATION']:
        app.logger.critical("CRITICAL: IGA_VERTEXAI_LOCATION is not set.")
        raise ValueError("IGA_VERTEXAI_LOCATION is not set.")
    if not iga_config['GCS_BUCKET_NAME']: # Check for GCS bucket name
        app.logger.critical("CRITICAL: GCS_BUCKET_NAME is not set for IGA. Image uploads will fail.")
        raise ValueError("GCS_BUCKET_NAME is not set for IGA.")
    if not iga_config['GOOGLE_APPLICATION_CREDENTIALS']:
        app.logger.warning("IGA WARNING: GOOGLE_APPLICATION_CREDENTIALS not explicitly set. Using ADC if configured.")

load_iga_configuration()

# --- Vertex AI Initialization ---
try:
    app.logger.info(f"Initializing Vertex AI for project '{iga_config['IGA_VERTEXAI_PROJECT_ID']}' in location '{iga_config['IGA_VERTEXAI_LOCATION']}'...")
    aiplatform.init(project=iga_config['IGA_VERTEXAI_PROJECT_ID'], location=iga_config['IGA_VERTEXAI_LOCATION'])
    app.logger.info("Vertex AI initialized successfully for IGA.")
except Exception as e:
    app.logger.error(f"Failed to initialize Vertex AI for IGA: {e}", exc_info=True)
    raise ValueError(f"IGA Critical Error: Failed to initialize Vertex AI: {e}")


@app.route("/generate_image", methods=["POST"])
def generate_image_endpoint():
    request_id = f"iga_req_{uuid.uuid4().hex[:8]}"
    app.logger.info(f"IGA Request {request_id}: Received /generate_image request.")

    # Configuration check (GCS bucket is essential now)
    if not iga_config.get("GCS_BUCKET_NAME"):
        app.logger.error(f"IGA Request {request_id}: GCS_BUCKET_NAME not configured.")
        return jsonify({"error_code": "IGA_CONFIG_ERROR_GCS_BUCKET", "message": "IGA service GCS bucket not configured."}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error_code": "IGA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json_decode:
        return jsonify({"error_code": "IGA_MALFORMED_JSON", "message": f"Malformed JSON: {str(e_json_decode)}"}), 400

    prompt = data.get("prompt")
    if not prompt or not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error_code": "IGA_BAD_REQUEST_PROMPT_MISSING", "message": "Prompt is required."}), 400

    app.logger.info(f"IGA Request {request_id}: Processing prompt: '{prompt}' with model {iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID']}")

    try:
        model = ImageGenerationModel.from_pretrained(iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'])

        images_response = model.generate_images(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio=iga_config['IGA_DEFAULT_ASPECT_RATIO'],
            add_watermark=iga_config['IGA_ADD_WATERMARK']
        )
        app.logger.info(f"IGA Request {request_id}: Vertex AI call completed.")

        if not images_response or not images_response.images:
            app.logger.error(f"IGA Request {request_id}: No images from Vertex AI for prompt: '{prompt}'")
            return jsonify({"error_code": "IGA_VERTEXAI_NO_IMAGES_RETURNED", "message": "Image generation failed."}), 500

        image_object = images_response.images[0]
        if not hasattr(image_object, '_image_bytes') or not image_object._image_bytes:
            app.logger.error(f"IGA Request {request_id}: Vertex AI image bytes missing for prompt: '{prompt}'")
            return jsonify({"error_code": "IGA_VERTEXAI_EMPTY_IMAGE_BYTES", "message": "Image generation produced empty image."}), 500

        # Upload to GCS
        storage_client = storage.Client()
        bucket = storage_client.bucket(iga_config['GCS_BUCKET_NAME'])

        # Assuming PNG format from Vertex AI Imagen default. Could be made configurable or detected.
        file_extension = "png"
        gcs_object_name = f"{iga_config['IGA_GCS_IMAGE_PREFIX'].strip('/')}/{request_id}_{uuid.uuid4().hex[:8]}.{file_extension}"

        blob = bucket.blob(gcs_object_name)

        # Determine content type for GCS (image/png for .png)
        gcs_content_type = 'image/png'
        # Add other types if image format can vary:
        # if file_extension == "jpeg" or file_extension == "jpg":
        #     gcs_content_type = 'image/jpeg'

        app.logger.info(f"IGA Request {request_id}: Uploading to GCS. Bucket: {iga_config['GCS_BUCKET_NAME']}, Object: {gcs_object_name}")
        blob.upload_from_string(image_object._image_bytes, content_type=gcs_content_type)
        image_gcs_uri = f"gs://{iga_config['GCS_BUCKET_NAME']}/{gcs_object_name}"
        app.logger.info(f"IGA Request {request_id}: Image uploaded to GCS: {image_gcs_uri}")

        response_data = {
            "image_url": image_gcs_uri,
            "prompt_used": prompt,
            "model_version": f"vertex-ai-{iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID']}"
        }
        return jsonify(response_data), 200

    except google_exceptions.InvalidArgument as e:
        app.logger.error(f"IGA Request {request_id}: Vertex AI Invalid Argument: {e}", exc_info=True)
        return jsonify({"error_code": "IGA_VERTEXAI_INVALID_ARGUMENT", "message": f"Invalid argument for Vertex AI: {e}"}), 400
    except google_exceptions.PermissionDenied as e:
        app.logger.error(f"IGA Request {request_id}: Vertex AI Permission Denied: {e}", exc_info=True)
        return jsonify({"error_code": "IGA_VERTEXAI_PERMISSION_DENIED", "message": f"Vertex AI Permission Denied: {e}"}), 403
    except google_exceptions.ResourceExhausted as e:
        app.logger.error(f"IGA Request {request_id}: Vertex AI Resource Exhausted: {e}", exc_info=True)
        return jsonify({"error_code": "IGA_VERTEXAI_RESOURCE_EXHAUSTED", "message": f"Vertex AI Resource Exhausted (quota): {e}"}), 429
    except google_exceptions.FailedPrecondition as e:
        app.logger.warning(f"IGA Request {request_id}: Vertex AI Failed Precondition (often safety filters): {e}", exc_info=True)
        error_message = f"Vertex AI: {e}"
        error_code = "IGA_VERTEXAI_FAILED_PRECONDITION"
        if "blocked" in str(e).lower() and ("safety" in str(e).lower() or "policy" in str(e).lower()):
            error_code = "IGA_VERTEXAI_PROMPT_BLOCKED_SAFETY"
            error_message = f"Prompt blocked by safety filters: {e}"
        return jsonify({"error_code": error_code, "message": error_message}), 400
    except google_exceptions.GoogleCloudError as e: # Catch other GCS or general Google API errors
        app.logger.error(f"IGA Request {request_id}: Google Cloud API Error (Vertex AI or GCS): {e}", exc_info=True)
        return jsonify({"error_code": "IGA_GOOGLE_CLOUD_ERROR", "message": f"Google Cloud API error: {e}"}), 500
    except IOError as e: # Should be less likely now with direct GCS upload
        app.logger.error(f"IGA Request {request_id}: I/O Error (unexpected if not saving locally): {e}", exc_info=True)
        return jsonify({"error_code": "IGA_IO_ERROR", "message": f"I/O error: {e}"}), 500
    except Exception as e:
        app.logger.error(f"IGA Request {request_id}: Unexpected error in /generate_image: {e}", exc_info=True)
        return jsonify({"error_code": "IGA_INTERNAL_SERVER_ERROR", "message": f"Unexpected error: {e}"}), 500

if __name__ == "__main__":
    if not iga_config.get('GOOGLE_APPLICATION_CREDENTIALS') and not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
        app.logger.warning("GOOGLE_APPLICATION_CREDENTIALS not set. Vertex AI/GCS will use ADC if available.")
    if not iga_config.get('GCS_BUCKET_NAME'):
         app.logger.warning("GCS_BUCKET_NAME not set. IGA will fail to upload images.")

    # Local temp dir creation is no longer essential for primary flow
    # local_image_dir = iga_config.get('IGA_GENERATED_IMAGE_DIR')
    # if local_image_dir: # Only create if configured (e.g. for temp files)
    #     try: os.makedirs(local_image_dir, exist_ok=True); app.logger.info(f"Ensured local dir exists (for temp): {local_image_dir}")
    #     except OSError as e: app.logger.error(f"Could not create local dir {local_image_dir}: {e}")

    host = iga_config.get("IGA_HOST")
    port = iga_config.get("IGA_PORT")
    is_debug_mode = iga_config.get("IGA_DEBUG_MODE")

    app.logger.info(f"--- IGA Service (Vertex AI & GCS) starting on {host}:{port} (Debug: {is_debug_mode}) ---")
    app.run(host=host, port=port, debug=is_debug_mode)

[end of aethercast/iga/main.py]
