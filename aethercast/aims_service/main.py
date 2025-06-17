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
import time # Added for metric logging & idempotency stale check

# --- Database and Idempotency specific imports ---
import psycopg2
from psycopg2 import pool as psycopg2_pool
from psycopg2 import extras as psycopg2_extras # For DictCursor
import json # For serializing payloads for DB

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

# --- Idempotency Configuration & DB Connection Pool ---
IDEMPOTENCY_LOCK_TIMEOUT_SECONDS = 300 # 5 minutes (adjust as needed)
db_connection_pool = None

def get_db_connection():
    """Establishes and returns a database connection from the pool."""
    global db_connection_pool
    if db_connection_pool is None:
        try:
            db_connection_pool = psycopg2_pool.SimpleConnectionPool(
                minconn=1,
                maxconn=int(os.getenv("DB_POOL_MAX_CONNECTIONS", 5)), # Pool size from env
                user=os.getenv("POSTGRES_USER"),
                password=os.getenv("POSTGRES_PASSWORD"),
                host=os.getenv("POSTGRES_HOST"),
                port=os.getenv("POSTGRES_PORT", "5432"),
                database=os.getenv("POSTGRES_DB")
            )
            logger.info("Database connection pool created successfully for AIMS.")
        except (Exception, psycopg2.Error) as error:
            logger.error(f"Error while creating PostgreSQL connection pool for AIMS: {error}", exc_info=True)
            raise
    try:
        return db_connection_pool.getconn()
    except Exception as error:
        logger.error(f"Error getting connection from AIMS pool: {error}", exc_info=True)
        raise

def release_db_connection(conn):
    """Releases a database connection back to the pool."""
    global db_connection_pool
    if db_connection_pool and conn:
        db_connection_pool.putconn(conn)

def check_idempotency(db_conn, idempotency_key: str, task_name: str):
    log_extra_idem = {'idempotency_key': idempotency_key, 'task_name': task_name, 'service_name': 'aims-service'}
    try:
        with db_conn.cursor(cursor_factory=psycopg2_extras.DictCursor) as cursor:
            cursor.execute(
                "SELECT status, result_payload, locked_at, error_payload FROM idempotency_keys WHERE key = %s AND task_name = %s",
                (idempotency_key, task_name)
            )
            record = cursor.fetchone()
            if record:
                logger.info(f"Idempotency record found: Status - {record['status']}", extra=log_extra_idem)
                if record['status'] == 'completed':
                    # Make sure result_payload (which is JSONB in DB) is loaded as dict
                    return {'status': 'completed', 'result': record['result_payload'] if isinstance(record['result_payload'], dict) else json.loads(record['result_payload'])}
                elif record['status'] == 'processing':
                    if record['locked_at'] and (time.time() - record['locked_at'].timestamp()) < IDEMPOTENCY_LOCK_TIMEOUT_SECONDS:
                        logger.warning("Task is already processing (lock not expired).", extra=log_extra_idem)
                        return {'status': 'conflict', 'message': 'Task already processing'}
                    else:
                        logger.warning("Task was 'processing' but lock expired. Will attempt to re-acquire.", extra=log_extra_idem)
                        return None # Stale lock
                elif record['status'] == 'failed':
                    logger.warning("Previous attempt for this task failed. Will attempt to re-run.", extra=log_extra_idem)
                    return None # Failed, proceed
            return None
    except (Exception, psycopg2.Error) as error:
        logger.error(f"Error checking idempotency: {error}", exc_info=True, extra=log_extra_idem)
        raise

def acquire_idempotency_lock(db_conn, idempotency_key: str, task_name: str, workflow_id: Optional[str] = None):
    log_extra_idem = {'idempotency_key': idempotency_key, 'task_name': task_name, 'workflow_id': workflow_id or "N/A", 'service_name': 'aims-service'}
    try:
        with db_conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO idempotency_keys (key, task_name, workflow_id, status, locked_at, created_at, updated_at)
                VALUES (%s, %s, %s, 'processing', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (key, task_name) DO UPDATE SET
                    status = 'processing',
                    locked_at = CURRENT_TIMESTAMP,
                    workflow_id = EXCLUDED.workflow_id,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id;
                """,
                (idempotency_key, task_name, workflow_id)
            )
            lock_id = cursor.fetchone()
            db_conn.commit()
            if lock_id:
                logger.info("Idempotency lock acquired.", extra=log_extra_idem)
                return True
            logger.error("Failed to acquire idempotency lock (no id returned).", extra=log_extra_idem) # Should not happen
            return False
    except (Exception, psycopg2.Error) as error:
        db_conn.rollback()
        logger.error(f"Error acquiring idempotency lock: {error}", exc_info=True, extra=log_extra_idem)
        raise

def update_idempotency_record(db_conn, idempotency_key: str, task_name: str, final_status: str, result_payload: Optional[dict] = None, error_payload: Optional[dict] = None):
    log_extra_idem = {'idempotency_key': idempotency_key, 'task_name': task_name, 'final_status': final_status, 'service_name': 'aims-service'}
    try:
        with db_conn.cursor() as cursor:
            result_payload_db = json.dumps(result_payload) if result_payload is not None else None
            error_payload_db = json.dumps(error_payload) if error_payload is not None else None
            cursor.execute(
                """
                UPDATE idempotency_keys
                SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE key = %s AND task_name = %s
                """,
                (final_status, result_payload_db, error_payload_db, idempotency_key, task_name)
            )
            db_conn.commit()
            logger.info("Idempotency record updated.", extra=log_extra_idem)
    except (Exception, psycopg2.Error) as error:
        db_conn.rollback()
        logger.error(f"Error updating idempotency record: {error}", exc_info=True, extra=log_extra_idem)
        raise

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

# --- Database Initialization ---
try:
    conn_init = get_db_connection() # Prime the pool
    if conn_init:
        logger.info("Successfully connected to PostgreSQL and primed the AIMS connection pool.")
        release_db_connection(conn_init)
    else: # Should not happen if get_db_connection raises on failure
        logger.warning("Failed to get a DB connection to prime the AIMS pool at startup (get_db_connection returned None).")
except Exception as e_db_init:
    logger.error(f"Failed to initialize AIMS database connection pool at startup: {e_db_init}", exc_info=True)
    # Decide if app should stop or continue with DB features potentially failing. For now, log and continue.


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
    celery_task_internal_id = self.request.id or f"sync_{uuid.uuid4().hex[:8]}"
    log_extra = {
        'request_id': request_id, # User-provided request_id
        'celery_task_id': celery_task_internal_id, # Celery's internal task ID
        'workflow_id': "N/A", # workflow_id is not explicitly passed to this task yet
        'model_id_used': model_name_to_use,
        'service_name': 'aims-service'
    }
    logger.info(f"AIMS Task {celery_task_internal_id}: Starting LLM call for user request {request_id}. Model: {model_name_to_use}", extra=log_extra)

    idempotency_key_str = request_id # Use the original request_id for idempotency
    task_name_str = "aims_invoke_llm_vertex_ai_task"
    db_conn = None
    task_final_result = None # To hold the result that will be stored and returned

    try:
        db_conn = get_db_connection()
        idempotency_check = check_idempotency(db_conn, idempotency_key_str, task_name_str)

        if idempotency_check:
            if idempotency_check['status'] == 'completed':
                logger.info(f"Task '{task_name_str}' (req: {request_id}) already completed. Returning stored result.", extra=log_extra)
                return idempotency_check['result']
            elif idempotency_check['status'] == 'conflict':
                logger.warning(f"Task '{task_name_str}' (req: {request_id}) conflict: {idempotency_check['message']}.", extra=log_extra)
                return {"error": {"type": "idempotency_conflict", "message": idempotency_check['message']}, "request_id": request_id}

        if not acquire_idempotency_lock(db_conn, idempotency_key_str, task_name_str, log_extra['workflow_id']):
            logger.error(f"Failed to acquire idempotency lock for task '{task_name_str}' (req: {request_id}). Aborting.", extra=log_extra)
            return {"error": {"type": "lock_acquisition_failed", "message": "Failed to acquire idempotency lock."}, "request_id": request_id}

        # Core LLM Invocation Logic
        model = GenerativeModel(model_name_to_use)
        gemini_contents = [Part.from_text(prompt_text)]
        generation_config_params = {"temperature": temperature, "max_output_tokens": max_output_tokens}
        if response_mime_type_req == "json_object":
            generation_config_params["response_mime_type"] = "application/json"
        generation_config = GenerationConfig(**generation_config_params)

        call_start_time = time.time()
        response = model.generate_content(gemini_contents, generation_config=generation_config)
        call_end_time = time.time()
        vertex_ai_call_duration_ms = (call_end_time - call_start_time) * 1000

        logger.info(f"AIMS Task {celery_task_internal_id}: Vertex AI call successful. Duration: {vertex_ai_call_duration_ms:.2f} ms.", extra=log_extra)
        logger.info("AIMS Vertex AI call processed (async)", extra={**log_extra, "metric_name": "aims_vertexai_call_latency_ms", "value": round(vertex_ai_call_duration_ms, 2)})

        if not response.candidates:
            logger.error(f"AIMS Task {celery_task_internal_id}: No candidates from Gemini for req {request_id}.", extra=log_extra)
            raise ValueError("LLM returned no candidates.") # This will be caught by general Exception block

        candidate = response.candidates[0]
        generated_text = candidate.content.parts[0].text if candidate.content and candidate.content.parts and candidate.content.parts[0].text else ""
        finish_reason_str = map_finish_reason_to_str(candidate.finish_reason)

        if candidate.finish_reason == FinishReason.SAFETY:
            logger.warning(f"AIMS Task {celery_task_internal_id}: Content generation blocked by safety for req {request_id}. Reason: {finish_reason_str}", extra=log_extra)
            logger.warning("Vertex AI content blocked by safety (async)", extra={**log_extra, "metric_name": "aims_vertexai_error_count", "value": 1, "tags_metric": {"error_type": "safety_blocked"}})
            task_final_result = {"error": {"type": "generation_blocked_safety", "message": "Content generation blocked by safety filters."}, "model_id": model_name_to_use, "request_id": request_id}
            update_idempotency_record(db_conn, idempotency_key_str, task_name_str, 'completed', result_payload=task_final_result) # 'completed' as API call finished
            return task_final_result

        prompt_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata else 0
        completion_tokens = response.usage_metadata.candidates_token_count if response.usage_metadata else 0
        total_tokens = response.usage_metadata.total_token_count if response.usage_metadata else 0

        logger.info("AIMS token usage (async)", extra={**log_extra, "metric_name": "aims_token_usage_input_tokens", "value": prompt_tokens})
        logger.info("AIMS token usage (async)", extra={**log_extra, "metric_name": "aims_token_usage_output_tokens", "value": completion_tokens})

        task_final_result = {
            "request_id": request_id, "model_id": model_name_to_use,
            "choices": [{"text": generated_text, "finish_reason": finish_reason_str}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens}
        }
        update_idempotency_record(db_conn, idempotency_key_str, task_name_str, 'completed', result_payload=task_final_result)
        return task_final_result

    except google_exceptions.GoogleAPIError as e:
        logger.error(f"AIMS Task {celery_task_internal_id}: Google Vertex AI API Error for req {request_id}: {e}", exc_info=True, extra=log_extra)
        logger.error("Vertex AI API error (async)", extra={**log_extra, "metric_name": "aims_vertexai_error_count", "value": 1, "tags_metric": {"error_type": "google_api_error"}})
        error_payload_db = {"error_type": type(e).__name__, "message": str(e), "details": e.args[0] if e.args else "N/A"}
        if db_conn: # Ensure db_conn is available before trying to update
            update_idempotency_record(db_conn, idempotency_key_str, task_name_str, 'failed', error_payload=error_payload_db)
        raise self.retry(exc=e, countdown=15, max_retries=2) # Adjusted retry params

    except Exception as e:
        logger.error(f"AIMS Task {celery_task_internal_id}: Unexpected error for req {request_id}: {e}", exc_info=True, extra=log_extra)
        error_payload_db = {"error_type": type(e).__name__, "message": str(e)}
        if db_conn: # Ensure db_conn is available
             update_idempotency_record(db_conn, idempotency_key_str, task_name_str, 'failed', error_payload=error_payload_db)
        # Let Celery handle retries based on task decorator
        raise self.retry(exc=e, countdown=10, max_retries=1) # Adjusted retry params for general errors

    finally:
        if db_conn:
            release_db_connection(db_conn)


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
