import os
import logging
import uuid
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from celery import Celery
from celery.result import AsyncResult

# --- Google Cloud specific imports ---
from google.cloud import aiplatform
from vertexai.preview.vision_models import ImageGenerationModel
from google.cloud import storage # Added for GCS
from google.api_core import exceptions as google_exceptions
import time # Added for metric logging

load_dotenv()

# --- Celery Configuration ---
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

celery_app = Celery(
    'iga_tasks',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND
)
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

# --- Logging Setup ---
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
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s"
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("Standard logging configured for IGA service.")

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

@celery_app.task(bind=True, name='generate_image_vertex_ai_task')
def generate_image_vertex_ai_task(self, request_id: str, prompt: str, aspect_ratio: str, add_watermark: bool, model_id: str, gcs_bucket_name: str, gcs_image_prefix: str):
    """
    Celery task to generate an image using Vertex AI and upload to GCS.
    """
    app.logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id}): Starting image generation. Prompt: '{prompt[:50]}...'")

    try:
        model = ImageGenerationModel.from_pretrained(model_id)
        images_response = model.generate_images(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio=aspect_ratio,
            add_watermark=add_watermark
        )

        if not images_response or not images_response.images:
            app.logger.error(f"Celery Task {self.request.id}: No images from Vertex AI for prompt: '{prompt}'")
            raise ValueError("Vertex AI returned no images.")

        image_object = images_response.images[0]
        if not hasattr(image_object, '_image_bytes') or not image_object._image_bytes:
            app.logger.error(f"Celery Task {self.request.id}: Vertex AI image bytes missing for prompt: '{prompt}'")
            raise ValueError("Vertex AI produced empty image bytes.")

        storage_client = storage.Client()
        bucket = storage_client.bucket(gcs_bucket_name)
        file_extension = "png"
        gcs_object_name = f"{gcs_image_prefix.strip('/')}/{request_id}_{uuid.uuid4().hex[:8]}.{file_extension}"
        blob = bucket.blob(gcs_object_name)
        gcs_content_type = 'image/png'

        blob.upload_from_string(image_object._image_bytes, content_type=gcs_content_type)
        image_gcs_uri = f"gs://{gcs_bucket_name}/{gcs_object_name}"
        app.logger.info(f"Celery Task {self.request.id}: Image uploaded to GCS: {image_gcs_uri}")

        return {
            "image_url": image_gcs_uri,
            "prompt_used": prompt,
            "model_version": f"vertex-ai-{model_id}"
        }
    except google_exceptions.GoogleAPIError as e:
        app.logger.error(f"Celery Task {self.request.id}: Google Vertex AI/GCS API Error: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=10, max_retries=2) # Retry for Google API errors
    except Exception as e:
        app.logger.error(f"Celery Task {self.request.id}: Unexpected error in image generation: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=10, max_retries=2)


@app.route("/generate_image", methods=["POST"])
def generate_image_async_endpoint():
    request_id = f"iga_req_{uuid.uuid4().hex[:8]}"
    app.logger.info(f"IGA Request {request_id}: Received async /generate_image request.")

    if not iga_config.get("GCS_BUCKET_NAME"): # Basic config check
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

    # Parameters for the task, using defaults from iga_config if not provided in request
    # (Assuming for now the request structure for async matches direct call, or is simplified)
    aspect_ratio = data.get("aspect_ratio", iga_config['IGA_DEFAULT_ASPECT_RATIO'])
    add_watermark = data.get("add_watermark", iga_config['IGA_ADD_WATERMARK'])
    model_id_to_use = data.get("model_id_override", iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'])


    app.logger.info(f"IGA Request {request_id}: Dispatching image generation to Celery task. Prompt: '{prompt[:50]}...'")

    task = generate_image_vertex_ai_task.delay(
        request_id=request_id,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        add_watermark=add_watermark,
        model_id=model_id_to_use,
        gcs_bucket_name=iga_config['GCS_BUCKET_NAME'],
        gcs_image_prefix=iga_config['IGA_GCS_IMAGE_PREFIX']
    )

    return jsonify({"task_id": task.id, "status_url": f"/v1/tasks/{task.id}"}), 202


@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    app.logger.info(f"Received request for IGA task status: {task_id}")
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}

    if task_result.successful():
        response_data["result"] = task_result.result
        return jsonify(response_data), 200
    elif task_result.failed():
        error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
        response_data["result"] = error_info
        return jsonify(response_data), 500 # Or 200
    else: # PENDING, STARTED, RETRY
        return jsonify(response_data), 202


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
