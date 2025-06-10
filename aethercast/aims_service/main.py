import os
import uuid
import logging
from flask import Flask, jsonify, request
from dotenv import load_dotenv

# --- Google Cloud Vertex AI specific imports ---
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, GenerationConfig, Part, FinishReason
from google.api_core import exceptions as google_exceptions # For specific error handling
import time # Added for metric logging

# --- Load Environment Variables ---
load_dotenv()

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
from python_json_logger import jsonlogger # Added for JSON logging

# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="aims-llm-service"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

# Configure JSON logging for the Flask app
def setup_json_logging(flask_app):
    flask_app.logger.handlers.clear() # Clear existing default Flask handlers
    logHandler = logging.StreamHandler()
    service_filter = ServiceNameFilter("aims-llm-service")
    logHandler.addFilter(service_filter)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s",
        rename_fields={"levelname": "level", "name": "logger_name", "asctime": "timestamp"}
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("JSON logging configured for AIMS (LLM) service.")

setup_json_logging(app)

# Make the global logger use the configured app.logger
logger = app.logger

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
    request_start_time = time.time()
    request_id = f"aims_req_{uuid.uuid4().hex}"
    final_status_str = "unknown_error" # Default status for request count metric
    # Use default model ID for status tag in case of early exit before model_name_to_use is determined
    model_name_to_use_for_status_tag = AIMS_GOOGLE_LLM_MODEL_ID

    logger.info(f"Request {request_id}: Received /v1/generate request.")

    # Re-check config at request time
    if not GCP_PROJECT_ID or not GCP_LOCATION or not GOOGLE_APPLICATION_CREDENTIALS:
        logger.error(f"Request {request_id}: Service not configured correctly. GCP Project/Location/Credentials missing.")
        final_status_str = "config_error"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "configuration_error", "message": "Service configuration incomplete."}}), 503

    try:
        data = request.get_json()
        if not data:
            final_status_str = "validation_error_no_payload"
            logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
            return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "No JSON payload received."}}), 400
    except Exception as e:
        final_status_str = "validation_error_bad_json"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"Invalid JSON payload: {str(e)}"}}), 400

    prompt_text = data.get("prompt")
    if not prompt_text or not isinstance(prompt_text, str) or not prompt_text.strip():
        logger.warning(f"Request {request_id}: Validation failed: 'prompt' is missing, not a string, or empty.")
        final_status_str = "validation_error_prompt"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Validation failed: 'prompt' must be a non-empty string."}}), 400

    model_id_override = data.get("model_id_override", data.get("model"))
    model_name_to_use = model_id_override if model_id_override else AIMS_GOOGLE_LLM_MODEL_ID
    model_name_to_use_for_status_tag = model_name_to_use # Update for the final log

    if model_id_override is not None and not isinstance(model_id_override, str):
        logger.warning(f"Request {request_id}: Validation failed: 'model_id_override' is not a string.")
        final_status_str = "validation_error_model_id"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Validation failed: 'model_id_override' must be a string if provided."}}), 400

    # Validate max_output_tokens
    raw_max_tokens = data.get("max_tokens")
    if raw_max_tokens is not None:
        try:
            max_output_tokens = int(raw_max_tokens)
            if max_output_tokens <= 0:
                logger.warning(f"Request {request_id}: Validation failed: 'max_tokens' must be positive. Received: {max_output_tokens}")
                final_status_str = "validation_error_max_tokens"
                logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
                return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Validation failed: 'max_tokens' must be a positive integer."}}), 400
        except ValueError:
            logger.warning(f"Request {request_id}: Validation failed: 'max_tokens' is not a valid integer. Received: {raw_max_tokens}")
            final_status_str = "validation_error_max_tokens_type"
            logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
            return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Validation failed: 'max_tokens' must be a valid integer."}}), 400
    else:
        max_output_tokens = 2048 # Default

    # Validate temperature
    raw_temperature = data.get("temperature")
    if raw_temperature is not None:
        try:
            temperature = float(raw_temperature)
            if not (0.0 <= temperature <= 2.0):
                logger.warning(f"Request {request_id}: Validation failed: 'temperature' out of range [0.0, 2.0]. Received: {temperature}")
                final_status_str = "validation_error_temperature_range"
                logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
                return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Validation failed: 'temperature' must be a float between 0.0 and 2.0."}}), 400
        except ValueError:
            logger.warning(f"Request {request_id}: Validation failed: 'temperature' is not a valid float. Received: {raw_temperature}")
            final_status_str = "validation_error_temperature_type"
            logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
            return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Validation failed: 'temperature' must be a valid float."}}), 400
    else:
        temperature = 0.7 # Default

    response_format_req = data.get("response_format", {})
    if not isinstance(response_format_req, dict):
        logger.warning(f"Request {request_id}: Validation failed: 'response_format' must be an object. Received: {response_format_req}")
        final_status_str = "validation_error_response_format_type"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Validation failed: 'response_format' must be an object."}}), 400

    response_mime_type_req = response_format_req.get("type")
    if response_mime_type_req is not None and not isinstance(response_mime_type_req, str):
        logger.warning(f"Request {request_id}: Validation failed: 'response_format.type' must be a string. Received: {response_mime_type_req}")
        final_status_str = "validation_error_response_format_type_field"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Validation failed: 'response_format.type' must be a string if provided."}}), 400

    logger.info(f"Request {request_id}: Using model '{model_name_to_use}'. Prompt (first 80 chars): '{prompt_text[:80]}...'")

    try:
        model = GenerativeModel(model_name_to_use)
        gemini_contents = [Part.from_text(prompt_text)]

        # Parameters already validated and converted, directly use them
        generation_config_params = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        # if top_p is not None: generation_config_params["top_p"] = top_p # Assuming top_p, top_k are pre-validated if added
        # if top_k is not None: generation_config_params["top_k"] = top_k

        if response_mime_type_req == "json_object": # Already validated as string or None
            generation_config_params["response_mime_type"] = "application/json"
            logger.info(f"Request {request_id}: Requesting JSON object response format from Gemini model.")

        generation_config = GenerationConfig(**generation_config_params)

        logger.debug(f"Request {request_id}: Making Vertex AI Gemini call. Model: {model_name_to_use}")

        call_start_time = time.time()
        response = model.generate_content(gemini_contents, generation_config=generation_config)
        call_end_time = time.time()
        vertex_ai_call_duration_ms = (call_end_time - call_start_time) * 1000

        logger.info(f"Request {request_id}: Vertex AI Gemini call successful. Duration: {vertex_ai_call_duration_ms:.2f} ms.")
        logger.info("AIMS Vertex AI call processed", extra=dict(metric_name="aims_vertexai_call_latency_ms", value=round(vertex_ai_call_duration_ms, 2), tags={"model_id_used": model_name_to_use}))

        if not response.candidates:
            logger.error(f"Request {request_id}: No candidates returned from Gemini model.")
            final_status_str = "vertexai_no_candidates"
            logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
            return jsonify({"request_id": request_id, "error": {"type": "no_content_generated", "message": "LLM returned no candidates."}}), 500

        candidate = response.candidates[0]
        generated_text = ""
        if candidate.content and candidate.content.parts:
            # Assuming the first part is the text response we want
            generated_text = candidate.content.parts[0].text if candidate.content.parts[0].text else ""

        finish_reason_str = map_finish_reason_to_str(candidate.finish_reason)

        if candidate.finish_reason == FinishReason.SAFETY:
            logger.warning(f"Request {request_id}: Content generation blocked by safety filters. Finish Reason: {finish_reason_str}")
            logger.warning("Vertex AI content blocked by safety", extra=dict(metric_name="aims_vertexai_error_count", value=1, tags={"model_id_used": model_name_to_use, "error_type": "safety_blocked"}))
            final_status_str = "vertexai_safety_blocked"
            logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
            return jsonify({
                "request_id": request_id, "model_id": model_name_to_use,
                "error": {"type": "generation_blocked_safety", "message": "Content generation blocked by safety filters."}
            }), 400

        prompt_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata else 0
        completion_tokens = response.usage_metadata.candidates_token_count if response.usage_metadata else 0
        total_tokens = response.usage_metadata.total_token_count if response.usage_metadata else 0

        # Log token usage metrics
        logger.info("AIMS token usage", extra=dict(metric_name="aims_token_usage_input_tokens", value=prompt_tokens, tags={"model_id_used": model_name_to_use}))
        logger.info("AIMS token usage", extra=dict(metric_name="aims_token_usage_output_tokens", value=completion_tokens, tags={"model_id_used": model_name_to_use}))

        response_payload = {
            "request_id": request_id,
            "model_id": model_name_to_use,
            "choices": [{"text": generated_text, "finish_reason": finish_reason_str}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens
            }
        }

        overall_latency_ms = (time.time() - request_start_time) * 1000
        logger.info("AIMS request processed", extra=dict(metric_name="aims_request_latency_ms", value=round(overall_latency_ms, 2), tags={"model_id_requested": model_name_to_use}))
        final_status_str = "success"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify(response_payload), 200

    except google_exceptions.InvalidArgument as e:
        logger.error(f"Request {request_id}: Vertex AI Invalid Argument: {e}", exc_info=True)
        logger.error("Vertex AI API error", extra=dict(metric_name="aims_vertexai_error_count", value=1, tags={"model_id_used": model_name_to_use, "error_type": "invalid_argument"}))
        final_status_str = "vertexai_invalid_argument"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_invalid_argument", "message": str(e)}}), 400
    except google_exceptions.PermissionDenied as e:
        logger.error(f"Request {request_id}: Vertex AI Permission Denied: {e}", exc_info=True)
        logger.error("Vertex AI API error", extra=dict(metric_name="aims_vertexai_error_count", value=1, tags={"model_id_used": model_name_to_use, "error_type": "permission_denied"}))
        final_status_str = "vertexai_permission_denied"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_permission_denied", "message": str(e)}}), 403
    except google_exceptions.ResourceExhausted as e:
        logger.error(f"Request {request_id}: Vertex AI Resource Exhausted (Rate Limit): {e}", exc_info=True)
        logger.error("Vertex AI API error", extra=dict(metric_name="aims_vertexai_error_count", value=1, tags={"model_id_used": model_name_to_use, "error_type": "rate_limit"}))
        final_status_str = "vertexai_rate_limit"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_rate_limit", "message": str(e)}}), 429
    except google_exceptions.ServiceUnavailable as e:
        logger.error(f"Request {request_id}: Vertex AI Service Unavailable: {e}", exc_info=True)
        logger.error("Vertex AI API error", extra=dict(metric_name="aims_vertexai_error_count", value=1, tags={"model_id_used": model_name_to_use, "error_type": "service_unavailable"}))
        final_status_str = "vertexai_service_unavailable"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_service_unavailable", "message": str(e)}}), 503
    except google_exceptions.GoogleAPIError as e: # Catch other Google API errors
        logger.error(f"Request {request_id}: Google Vertex AI API Error: {e}", exc_info=True)
        logger.error("Vertex AI API error", extra=dict(metric_name="aims_vertexai_error_count", value=1, tags={"model_id_used": model_name_to_use, "error_type": "google_api_error"}))
        final_status_str = "vertexai_google_api_error"
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "google_vertex_ai_error", "message": str(e)}}), 500
    except Exception as e:
        logger.error(f"Request {request_id}: Unexpected error during LLM call: {e}", exc_info=True)
        # final_status_str is already "unknown_error" by default
        logger.info("AIMS request completed", extra=dict(metric_name="aims_request_count", value=1, tags={"model_id_requested": model_name_to_use_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "internal_server_error", "message": "An unexpected error occurred."}}), 500

if __name__ == '__main__':
    host = os.getenv('AIMS_HOST', '0.0.0.0')
    port = int(os.getenv('AIMS_PORT', 8000))
    debug_mode_str = os.getenv('FLASK_DEBUG', 'False').lower()
    debug_mode = debug_mode_str == 'true'

    # Startup checks for GCP variables are done above and will raise ValueError if missing.
    logger.info(f"--- AIMS Service (Vertex AI) starting on {host}:{port} (Debug: {debug_mode}) ---")
    app.run(host=host, port=port, debug=debug_mode)
