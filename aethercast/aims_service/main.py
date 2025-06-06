import os
import uuid
import logging
from flask import Flask, jsonify, request
from dotenv import load_dotenv

# --- Google Cloud Vertex AI specific imports ---
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, GenerationConfig, Part, FinishReason
from google.api_core import exceptions as google_exceptions # For specific error handling

# --- Load Environment Variables ---
load_dotenv()

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
if app.logger and app.logger.name != 'root':
    logger = app.logger
else:
    logger = logging.getLogger(__name__)
    if not logger.hasHandlers():
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - AIMS - %(message)s')

# --- AIMS Configuration for Google Cloud Vertex AI ---
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCP_LOCATION = os.getenv("GCP_LOCATION")
AIMS_GOOGLE_LLM_MODEL_ID = os.getenv("AIMS_GOOGLE_LLM_MODEL_ID", "gemini-1.0-pro")

logger.info("--- AIMS Service Configuration (Google Vertex AI) ---")
if GOOGLE_APPLICATION_CREDENTIALS:
    logger.info(f"  GOOGLE_APPLICATION_CREDENTIALS: Path Set ('{os.path.basename(GOOGLE_APPLICATION_CREDENTIALS) if GOOGLE_APPLICATION_CREDENTIALS else 'Not Set'}')")
else:
    logger.critical("CRITICAL: GOOGLE_APPLICATION_CREDENTIALS is not set. Vertex AI calls will fail.")
if not GCP_PROJECT_ID:
    logger.critical("CRITICAL: GCP_PROJECT_ID is not set. Vertex AI calls will fail.")
if not GCP_LOCATION:
    logger.critical("CRITICAL: GCP_LOCATION is not set. Vertex AI calls will fail.")

logger.info(f"  GCP_PROJECT_ID: {GCP_PROJECT_ID}")
logger.info(f"  GCP_LOCATION: {GCP_LOCATION}")
logger.info(f"  AIMS_GOOGLE_LLM_MODEL_ID (default): {AIMS_GOOGLE_LLM_MODEL_ID}")
logger.info("--- End AIMS Service Configuration ---")

# Critical Startup Checks & Vertex AI Initialization
if not GOOGLE_APPLICATION_CREDENTIALS:
    raise ValueError("AIMS Critical Error: GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")
if not GCP_PROJECT_ID:
    raise ValueError("AIMS Critical Error: GCP_PROJECT_ID environment variable not set.")
if not GCP_LOCATION:
    raise ValueError("AIMS Critical Error: GCP_LOCATION environment variable not set.")

try:
    aiplatform.init(project=GCP_PROJECT_ID, location=GCP_LOCATION)
    logger.info(f"Vertex AI initialized successfully for project '{GCP_PROJECT_ID}' in location '{GCP_LOCATION}'.")
except Exception as e:
    logger.error(f"Failed to initialize Vertex AI: {e}", exc_info=True)
    raise ValueError(f"AIMS Critical Error: Failed to initialize Vertex AI: {e}")


def map_finish_reason_to_str(gemini_finish_reason: FinishReason) -> str:
    """Maps Gemini's FinishReason enum to a string."""
    if gemini_finish_reason == FinishReason.STOP: return "STOP"
    if gemini_finish_reason == FinishReason.MAX_TOKENS: return "MAX_TOKENS"
    if gemini_finish_reason == FinishReason.SAFETY: return "SAFETY"
    if gemini_finish_reason == FinishReason.RECITATION: return "RECITATION"
    if gemini_finish_reason == FinishReason.OTHER: return "OTHER"
    return "UNSPECIFIED"


@app.route('/v1/generate', methods=['POST'])
def generate_text():
    request_id = f"aims_req_{uuid.uuid4().hex}"
    logger.info(f"Request {request_id}: Received /v1/generate request.")

    # Re-check config at request time, though startup checks should prevent app start on critical missing config
    if not GCP_PROJECT_ID or not GCP_LOCATION or not GOOGLE_APPLICATION_CREDENTIALS:
        logger.error(f"Request {request_id}: Service not configured correctly. GCP Project/Location/Credentials missing.")
        return jsonify({"request_id": request_id, "error": {"type": "configuration_error", "message": "Service configuration incomplete."}}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "No JSON payload received."}}), 400
    except Exception as e:
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"Invalid JSON payload: {str(e)}"}}), 400

    prompt_text = data.get("prompt")
    if not prompt_text:
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Missing 'prompt' in request payload."}}), 400

    model_id_override = data.get("model_id_override", data.get("model"))
    max_output_tokens = data.get("max_tokens", 2048) # Default for Gemini, adjust as needed
    temperature = data.get("temperature", 0.7)
    # top_p = data.get("top_p") # Example if AIMS API supports it
    # top_k = data.get("top_k") # Example if AIMS API supports it
    response_format_req = data.get("response_format", {})
    response_mime_type_req = response_format_req.get("type") if isinstance(response_format_req, dict) else None


    model_name_to_use = model_id_override if model_id_override else AIMS_GOOGLE_LLM_MODEL_ID
    logger.info(f"Request {request_id}: Using model '{model_name_to_use}'. Prompt (first 80 chars): '{prompt_text[:80]}...'")

    try:
        model = GenerativeModel(model_name_to_use)

        gemini_contents = [Part.from_text(prompt_text)] # Simple text prompt

        generation_config_params = {
            "temperature": float(temperature), # Ensure float
            "max_output_tokens": int(max_output_tokens), # Ensure int
        }
        # if top_p is not None: generation_config_params["top_p"] = float(top_p)
        # if top_k is not None: generation_config_params["top_k"] = int(top_k)

        if response_mime_type_req == "json_object":
            generation_config_params["response_mime_type"] = "application/json"
            logger.info(f"Request {request_id}: Requesting JSON object response format from Gemini model.")

        generation_config = GenerationConfig(**generation_config_params)

        logger.debug(f"Request {request_id}: Making Vertex AI Gemini call. Model: {model_name_to_use}")

        # For streaming: generate_content(..., stream=True)
        response = model.generate_content(gemini_contents, generation_config=generation_config)
        logger.info(f"Request {request_id}: Vertex AI Gemini call successful.")

        if not response.candidates:
            logger.error(f"Request {request_id}: No candidates returned from Gemini model.")
            return jsonify({"request_id": request_id, "error": {"type": "no_content_generated", "message": "LLM returned no candidates."}}), 500

        candidate = response.candidates[0]
        generated_text = ""
        if candidate.content and candidate.content.parts:
            # Assuming the first part is the text response we want
            generated_text = candidate.content.parts[0].text if candidate.content.parts[0].text else ""

        finish_reason_str = map_finish_reason_to_str(candidate.finish_reason)

        if candidate.finish_reason == FinishReason.SAFETY:
            logger.warning(f"Request {request_id}: Content generation blocked by safety filters. Finish Reason: {finish_reason_str}")
            # safety_ratings_dict = [sr.__dict__ for sr in candidate.safety_ratings] # If needed
            return jsonify({
                "request_id": request_id, "model_id": model_name_to_use,
                "error": {"type": "generation_blocked_safety", "message": "Content generation blocked by safety filters."}
            }), 400

        prompt_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata else 0
        completion_tokens = response.usage_metadata.candidates_token_count if response.usage_metadata else 0
        total_tokens = response.usage_metadata.total_token_count if response.usage_metadata else 0

        response_payload = {
            "request_id": request_id,
            "model_id": model_name_to_use, # Gemini API doesn't return model string in response object easily
            "choices": [{"text": generated_text, "finish_reason": finish_reason_str}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens
            }
        }
        return jsonify(response_payload), 200

    except google_exceptions.InvalidArgument as e:
        logger.error(f"Request {request_id}: Vertex AI Invalid Argument: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_invalid_argument", "message": str(e)}}), 400
    except google_exceptions.PermissionDenied as e:
        logger.error(f"Request {request_id}: Vertex AI Permission Denied: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_permission_denied", "message": str(e)}}), 403
    except google_exceptions.ResourceExhausted as e:
        logger.error(f"Request {request_id}: Vertex AI Resource Exhausted (Rate Limit): {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_rate_limit", "message": str(e)}}), 429
    except google_exceptions.ServiceUnavailable as e:
        logger.error(f"Request {request_id}: Vertex AI Service Unavailable: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_service_unavailable", "message": str(e)}}), 503
    except google_exceptions.GoogleAPIError as e: # Catch other Google API errors
        logger.error(f"Request {request_id}: Google Vertex AI API Error: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_error", "message": str(e)}}), 500
    except Exception as e:
        logger.error(f"Request {request_id}: Unexpected error during LLM call: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "internal_server_error", "message": "An unexpected error occurred."}}), 500

if __name__ == '__main__':
    host = os.getenv('AIMS_HOST', '0.0.0.0')
    port = int(os.getenv('AIMS_PORT', 8000))
    debug_mode_str = os.getenv('FLASK_DEBUG', 'False').lower()
    debug_mode = debug_mode_str == 'true'

    # Startup checks for GCP variables are done above and will raise ValueError if missing.
    print(f"--- AIMS Service (Vertex AI) starting on {host}:{port} (Debug: {debug_mode}) ---")
    app.run(host=host, port=port, debug=debug_mode)
