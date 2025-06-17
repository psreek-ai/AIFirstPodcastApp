import os
import uuid
import logging
from flask import Flask, jsonify, request
from dotenv import load_dotenv

# --- Celery and Google Cloud Vertex AI specific imports ---
from celery import Celery
from celery.result import AsyncResult
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, GenerationConfig, Part, FinishReason
from google.api_core import exceptions as google_exceptions # For specific error handling
import time # Added for metric logging

# --- Load Environment Variables ---
load_dotenv()

# --- Celery Configuration ---
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

celery_app = Celery(
    'aims_tasks',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND
)
# Optional: Update Celery app config if needed, e.g., task serializer
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],  # Ensure tasks accept json
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)


# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
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
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s"
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("Standard logging configured for AIMS (LLM) service.")

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


@celery_app.task(bind=True, name='invoke_llm_vertex_ai_task')
def invoke_llm_vertex_ai_task(self, request_id: str, prompt_text: str, model_name_to_use: str, temperature: float, max_output_tokens: int, response_mime_type_req: str = None):
    """
    Celery task to invoke Google Vertex AI LLM.
    'self' is the task instance.
    """
    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id}): Starting LLM call. Model: {model_name_to_use}")
    # Ensure Vertex AI is initialized if this task runs in a separate worker process context
    # This might be redundant if worker imports main.py and init happens there, but good for safety.
    # However, aiplatform.init() should ideally be called once per process.
    # If workers are forked, it might be okay. If they are separate processes, each needs init.
    # For now, assuming init in main app startup is sufficient if workers share that context.
    # If issues arise, explicit re-initialization or passing client might be needed.

    try:
        model = GenerativeModel(model_name_to_use)
        gemini_contents = [Part.from_text(prompt_text)]
        generation_config_params = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if response_mime_type_req == "json_object":
            generation_config_params["response_mime_type"] = "application/json"
        generation_config = GenerationConfig(**generation_config_params)

        call_start_time = time.time()
        response = model.generate_content(gemini_contents, generation_config=generation_config)
        call_end_time = time.time()
        vertex_ai_call_duration_ms = (call_end_time - call_start_time) * 1000
        logger.info(f"Celery Task {self.request.id}: Vertex AI call successful. Duration: {vertex_ai_call_duration_ms:.2f} ms.")
        logger.info("AIMS Vertex AI call processed (async)", extra=dict(metric_name="aims_vertexai_call_latency_ms", value=round(vertex_ai_call_duration_ms, 2), tags={"model_id_used": model_name_to_use}))

        if not response.candidates:
            logger.error(f"Celery Task {self.request.id}: No candidates from Gemini.")
            # Celery tasks should raise exceptions for errors to be stored in backend
            raise ValueError("LLM returned no candidates.")

        candidate = response.candidates[0]
        generated_text = ""
        if candidate.content and candidate.content.parts:
            generated_text = candidate.content.parts[0].text if candidate.content.parts[0].text else ""
        finish_reason_str = map_finish_reason_to_str(candidate.finish_reason)

        if candidate.finish_reason == FinishReason.SAFETY:
            logger.warning(f"Celery Task {self.request.id}: Content generation blocked by safety. Finish Reason: {finish_reason_str}")
            logger.warning("Vertex AI content blocked by safety (async)", extra=dict(metric_name="aims_vertexai_error_count", value=1, tags={"model_id_used": model_name_to_use, "error_type": "safety_blocked"}))
            # Return a specific error structure for safety issues
            return {"error": {"type": "generation_blocked_safety", "message": "Content generation blocked by safety filters."}, "model_id": model_name_to_use}

        prompt_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata else 0
        completion_tokens = response.usage_metadata.candidates_token_count if response.usage_metadata else 0
        total_tokens = response.usage_metadata.total_token_count if response.usage_metadata else 0

        logger.info("AIMS token usage (async)", extra=dict(metric_name="aims_token_usage_input_tokens", value=prompt_tokens, tags={"model_id_used": model_name_to_use}))
        logger.info("AIMS token usage (async)", extra=dict(metric_name="aims_token_usage_output_tokens", value=completion_tokens, tags={"model_id_used": model_name_to_use}))

        return {
            "request_id": request_id, # Original request ID
            "model_id": model_name_to_use,
            "choices": [{"text": generated_text, "finish_reason": finish_reason_str}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens}
        }
    except google_exceptions.GoogleAPIError as e:
        logger.error(f"Celery Task {self.request.id}: Google Vertex AI API Error: {e}", exc_info=True)
        logger.error("Vertex AI API error (async)", extra=dict(metric_name="aims_vertexai_error_count", value=1, tags={"model_id_used": model_name_to_use, "error_type": "google_api_error"}))
        # Re-raise to let Celery mark task as FAILED and store exception
        raise self.retry(exc=e, countdown=5, max_retries=3) # Example retry
    except Exception as e:
        logger.error(f"Celery Task {self.request.id}: Unexpected error during LLM call: {e}", exc_info=True)
        logger.error(f"Celery Task {self.request.id}: Non-GoogleAPIError, will retry: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=5, max_retries=3) # Example retry


@app.route('/v1/generate', methods=['POST'])
def generate_text_async():
    request_id = f"aims_req_{uuid.uuid4().hex}"
    logger.info(f"Request {request_id}: Received async /v1/generate request.")

    if not GCP_PROJECT_ID or not GCP_LOCATION or not GOOGLE_APPLICATION_CREDENTIALS: # Basic config check
        logger.error(f"Request {request_id}: Service not configured. GCP Project/Location/Credentials missing.")
        return jsonify({"request_id": request_id, "error": {"type": "configuration_error", "message": "Service configuration incomplete."}}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "No JSON payload received."}}), 400
    except Exception as e:
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"Invalid JSON payload: {str(e)}"}}), 400

    prompt_text = data.get("prompt")
    if not prompt_text or not isinstance(prompt_text, str) or not prompt_text.strip():
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Validation failed: 'prompt' must be a non-empty string."}}), 400

    model_id_override = data.get("model_id_override", data.get("model"))
    model_name_to_use = model_id_override if model_id_override else AIMS_GOOGLE_LLM_MODEL_ID

    # Simplified validation for brevity, assuming other params like temperature, max_tokens are optional with defaults in task
    temperature = float(data.get("temperature", 0.7))
    max_output_tokens = int(data.get("max_tokens", 2048))
    response_format_req = data.get("response_format", {})
    response_mime_type_req = response_format_req.get("type")


    logger.info(f"Request {request_id}: Dispatching LLM call to Celery task. Model: '{model_name_to_use}'.")

    task = invoke_llm_vertex_ai_task.delay(
        request_id=request_id,
        prompt_text=prompt_text,
        model_name_to_use=model_name_to_use,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        response_mime_type_req=response_mime_type_req
    )

    return jsonify({"task_id": task.id, "status_url": f"/v1/tasks/{task.id}"}), 202


@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    logger.info(f"Received request for task status: {task_id}")
    task_result = AsyncResult(task_id, app=celery_app)

    response_data = {
        "task_id": task_id,
        "status": task_result.status,
        "result": None
    }

    if task_result.successful():
        response_data["result"] = task_result.result
        return jsonify(response_data), 200
    elif task_result.failed():
        # Store error information
        error_info = {
            "error": {"type": "task_failed", "message": str(task_result.info)}, # task_result.info contains the exception
            # "traceback": task_result.traceback # Optionally include traceback
        }
        response_data["result"] = error_info
        return jsonify(response_data), 500 # Or 200 if you want to deliver the error within result
    else: # PENDING, STARTED, RETRY
        return jsonify(response_data), 202 # Accepted, processing not complete

if __name__ == '__main__':
    host = os.getenv('AIMS_HOST', '0.0.0.0')
    port = int(os.getenv('AIMS_PORT', 8000))
    debug_mode_str = os.getenv('FLASK_DEBUG', 'False').lower()
    debug_mode = debug_mode_str == 'true'

    # Startup checks for GCP variables are done above and will raise ValueError if missing.
    logger.info(f"--- AIMS Service (Vertex AI) starting on {host}:{port} (Debug: {debug_mode}) ---")
    app.run(host=host, port=port, debug=debug_mode)
