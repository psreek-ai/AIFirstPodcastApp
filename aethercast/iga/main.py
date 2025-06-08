import os
import logging
import uuid # Added
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# --- Google Cloud Vertex AI specific imports ---
from google.cloud import aiplatform # Added
from vertexai.preview.vision_models import ImageGenerationModel # Added
from google.api_core import exceptions as google_exceptions # Added

load_dotenv()

app = Flask(__name__)

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - IGA - %(message)s')

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
    iga_config['IGA_GENERATED_IMAGE_DIR'] = os.getenv("IGA_GENERATED_IMAGE_DIR", "/shared_audio/iga_images") # Standardized shared path
    iga_config['IGA_DEFAULT_ASPECT_RATIO'] = os.getenv("IGA_DEFAULT_ASPECT_RATIO", "1:1")
    iga_config['IGA_ADD_WATERMARK'] = os.getenv("IGA_ADD_WATERMARK", "True").lower() == "true"

    # Ensure GOOGLE_APPLICATION_CREDENTIALS is loaded if set (for Vertex AI)
    # Although aiplatform.init() uses ADC by default if this is not explicitly passed to client.
    # For clarity, we can log its presence.
    iga_config['GOOGLE_APPLICATION_CREDENTIALS'] = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')


    logging.info("--- IGA Configuration (Vertex AI) ---")
    for key, value in iga_config.items():
        if "CREDENTIALS" in key and value:
            logging.info(f"  {key}: Path Set ('{os.path.basename(value) if value else 'Not Set'}')")
        else:
            logging.info(f"  {key}: {value}")
    logging.info("--- End IGA Configuration ---")

    # Critical startup checks for Vertex AI
    if not iga_config['IGA_VERTEXAI_PROJECT_ID']:
        error_msg = "CRITICAL: IGA_VERTEXAI_PROJECT_ID is not set. Vertex AI image generation will fail."
        logging.critical(error_msg)
        raise ValueError(error_msg)
    if not iga_config['IGA_VERTEXAI_LOCATION']:
        error_msg = "CRITICAL: IGA_VERTEXAI_LOCATION is not set. Vertex AI image generation will fail."
        logging.critical(error_msg)
        raise ValueError(error_msg)
    if not iga_config['GOOGLE_APPLICATION_CREDENTIALS']:
        logging.warning("IGA WARNING: GOOGLE_APPLICATION_CREDENTIALS is not explicitly set for IGA. Vertex AI will attempt to use Application Default Credentials (ADC). Ensure ADC are configured if this is intended.")


load_iga_configuration()

# --- Vertex AI Initialization ---
try:
    logging.info(f"Initializing Vertex AI for project '{iga_config['IGA_VERTEXAI_PROJECT_ID']}' in location '{iga_config['IGA_VERTEXAI_LOCATION']}'...")
    aiplatform.init(project=iga_config['IGA_VERTEXAI_PROJECT_ID'], location=iga_config['IGA_VERTEXAI_LOCATION'])
    logging.info("Vertex AI initialized successfully for IGA.")
except Exception as e:
    logging.error(f"Failed to initialize Vertex AI for IGA: {e}", exc_info=True)
    raise ValueError(f"IGA Critical Error: Failed to initialize Vertex AI: {e}")


# IGA_MODEL_VERSION is removed; version will be part of the response dynamically.

@app.route("/generate_image", methods=["POST"])
def generate_image_endpoint():
    request_id = f"iga_req_{uuid.uuid4().hex[:8]}"
    logging.info(f"IGA Request {request_id}: Received /generate_image request.")
    try:
        try:
            data = request.get_json()
            if not data:
                logging.warning(f"IGA Request {request_id}: Invalid or empty JSON payload.")
                return jsonify({
                    "error_code": "IGA_INVALID_PAYLOAD",
                    "message": "Invalid or empty JSON payload.",
                    "details": "Request body must be a valid non-empty JSON object."
                }), 400
        except Exception as e_json_decode:
            logging.warning(f"IGA Request {request_id}: Failed to decode JSON payload: {e_json_decode}", exc_info=True)
            return jsonify({
                "error_code": "IGA_MALFORMED_JSON",
                "message": "Malformed JSON payload.",
                "details": str(e_json_decode)
            }), 400

        prompt = data.get("prompt")
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            logging.warning(f"IGA Request {request_id}: Missing or empty 'prompt'.")
            return jsonify({
                "error_code": "IGA_BAD_REQUEST_PROMPT_MISSING",
                "message": "Prompt is required for image generation.",
                "details": "Missing or empty 'prompt' in request body."
            }), 400

        logging.info(f"IGA Request {request_id}: Processing prompt: '{prompt}'")

        model = ImageGenerationModel.from_pretrained(iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'])

        # Parameters for image generation
        # For safety_settings, using Vertex AI defaults unless specific needs arise.
        # Example: safety_settings = { "person_presence": "block_all", "violence": "block_medium_and_above" }
        # For now, relying on model's default safety filters.

        logging.info(f"IGA Request {request_id}: Calling Vertex AI ImageGenerationModel.generate_images(). Model: {iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID']}")
        images_response = model.generate_images(
            prompt=prompt,
            number_of_images=1, # Fixed at 1 image per prompt for now
            aspect_ratio=iga_config['IGA_DEFAULT_ASPECT_RATIO'],
            add_watermark=iga_config['IGA_ADD_WATERMARK']
            # seed= can be used for reproducibility if needed
        )
        logging.info(f"IGA Request {request_id}: Vertex AI call completed.")

        if not images_response or not images_response.images: # Check if images_response itself is None or empty, or if images list is empty
            logging.error(f"IGA Request {request_id}: No images returned from Vertex AI for prompt: '{prompt}'")
            return jsonify({
                "error_code": "IGA_VERTEXAI_NO_IMAGES_RETURNED",
                "message": "Image generation failed to produce an image.",
                "details": "The Vertex AI model did not return any image data."
            }), 500

        image_object = images_response.images[0] # Assuming first image if multiple were somehow generated

        if not hasattr(image_object, '_image_bytes') or not image_object._image_bytes:
            logging.error(f"IGA Request {request_id}: Image object from Vertex AI missing image bytes for prompt: '{prompt}'")
            return jsonify({
                "error_code": "IGA_VERTEXAI_EMPTY_IMAGE_BYTES",
                "message": "Image generation produced an empty image.",
                "details": "The image data from Vertex AI was empty or inaccessible."
            }), 500

        # Save the image
        generated_image_dir = iga_config['IGA_GENERATED_IMAGE_DIR']
        os.makedirs(generated_image_dir, exist_ok=True)

        filename = f"{request_id}_{uuid.uuid4().hex[:8]}.png" # Assuming PNG, Vertex AI usually returns PNG
        filepath_in_container = os.path.join(generated_image_dir, filename)

        with open(filepath_in_container, "wb") as f:
            f.write(image_object._image_bytes)
        logging.info(f"IGA Request {request_id}: Image successfully saved to: {filepath_in_container}")

        response_data = {
            "image_url": filepath_in_container, # This is the path inside the container, accessible via shared volume
            "prompt_used": prompt,
            "model_version": f"vertex-ai-{iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID']}"
        }
        return jsonify(response_data), 200

    except google_exceptions.InvalidArgument as e:
        logging.error(f"IGA Request {request_id}: Vertex AI Invalid Argument: {e}", exc_info=True)
        return jsonify({"error_code": "IGA_VERTEXAI_INVALID_ARGUMENT", "message": "Invalid argument provided to Vertex AI.", "details": str(e)}), 400
    except google_exceptions.PermissionDenied as e:
        logging.error(f"IGA Request {request_id}: Vertex AI Permission Denied: {e}", exc_info=True)
        return jsonify({"error_code": "IGA_VERTEXAI_PERMISSION_DENIED", "message": "Permission denied for Vertex AI operation.", "details": str(e)}), 403
    except google_exceptions.ResourceExhausted as e: # Often means quota issues
        logging.error(f"IGA Request {request_id}: Vertex AI Resource Exhausted (e.g., quota): {e}", exc_info=True)
        return jsonify({"error_code": "IGA_VERTEXAI_RESOURCE_EXHAUSTED", "message": "Vertex AI resource exhausted (e.g., quota exceeded).", "details": str(e)}), 429
    except google_exceptions.FailedPrecondition as e: # Can indicate safety policy violation / blocked prompt
        logging.warning(f"IGA Request {request_id}: Vertex AI Failed Precondition (often safety filters): {e}", exc_info=True)
        # Check if the error message contains details about safety policy violation
        if "blocked" in str(e).lower() and ("safety" in str(e).lower() or "policy" in str(e).lower()):
            return jsonify({"error_code": "IGA_VERTEXAI_PROMPT_BLOCKED_SAFETY", "message": "Prompt blocked by safety filters.", "details": str(e)}), 400
        return jsonify({"error_code": "IGA_VERTEXAI_FAILED_PRECONDITION", "message": "Vertex AI operation failed due to a precondition.", "details": str(e)}), 400
    except google_exceptions.GoogleAPIError as e: # Catch other Google API errors
        logging.error(f"IGA Request {request_id}: Google Vertex AI API Error: {e}", exc_info=True)
        return jsonify({"error_code": "IGA_VERTEXAI_API_ERROR", "message": "An error occurred with the Vertex AI service.", "details": str(e)}), 500
    except IOError as e:
        logging.error(f"IGA Request {request_id}: File system I/O Error during image saving: {e}", exc_info=True)
        return jsonify({"error_code": "IGA_FILE_SAVE_ERROR", "message": "Could not save generated image.", "details": str(e)}), 500
    except Exception as e:
        logging.error(f"IGA Request {request_id}: Error in /generate_image endpoint: {e}", exc_info=True)
        return jsonify({
            "error_code": "IGA_INTERNAL_SERVER_ERROR",
            "message": "IGA encountered an unexpected error.",
            "details": str(e)
        }), 500

if __name__ == "__main__":
    # Ensure GOOGLE_APPLICATION_CREDENTIALS is checked at startup if required by local ADC flow
    if not iga_config.get('GOOGLE_APPLICATION_CREDENTIALS') and not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
        logging.warning("GOOGLE_APPLICATION_CREDENTIALS is not set in the environment. Vertex AI will rely on Application Default Credentials (ADC).")

    # Create shared image directory if it doesn't exist at startup (best effort)
    try:
        os.makedirs(iga_config['IGA_GENERATED_IMAGE_DIR'], exist_ok=True)
        logging.info(f"Ensured shared image directory exists: {iga_config['IGA_GENERATED_IMAGE_DIR']}")
    except OSError as e:
        logging.error(f"Could not create shared image directory {iga_config['IGA_GENERATED_IMAGE_DIR']} on startup: {e}")

    host = iga_config.get("IGA_HOST")
    port = iga_config.get("IGA_PORT")
    is_debug_mode = iga_config.get("IGA_DEBUG_MODE")

    logging.info(f"--- IGA Service (Vertex AI Image Generation) starting on {host}:{port} (Debug: {is_debug_mode}) ---")
    app.run(host=host, port=port, debug=is_debug_mode)
