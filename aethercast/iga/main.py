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
import json # For idempotency payloads
from datetime import datetime, timezone, timedelta # For idempotency lock timeout
from typing import Optional, Dict, Any # For type hinting

# Conditional import for psycopg2
PSYCOPG2_AVAILABLE = False
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    logging.warning("IGA: psycopg2-binary not found. PostgreSQL functionality for idempotency will be disabled.")

load_dotenv()

# --- Idempotency Constants ---
IDEMPOTENCY_KEY_HEADER = "X-Idempotency-Key"


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
celery_app.finalize() # Explicitly finalize the app

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
    # iga_config['IGA_DEBUG_MODE'] will be replaced by direct use of FLASK_DEBUG

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

    # Load PostgreSQL and Idempotency configurations
    iga_config['POSTGRES_HOST'] = os.getenv('POSTGRES_HOST')
    iga_config['POSTGRES_PORT'] = os.getenv('POSTGRES_PORT', '5432')
    iga_config['POSTGRES_USER'] = os.getenv('POSTGRES_USER')
    iga_config['POSTGRES_PASSWORD'] = os.getenv('POSTGRES_PASSWORD')
    iga_config['POSTGRES_DB'] = os.getenv('POSTGRES_DB')
    # Load the consolidated DB URL for IGA
    iga_config['IGA_POSTGRES_DB_URL'] = os.getenv('IGA_POSTGRES_DB_URL')

    iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'] = os.getenv('IGA_IDEMPOTENCY_STATUS_PROCESSING', 'processing')
    iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'] = os.getenv('IGA_IDEMPOTENCY_STATUS_COMPLETED', 'completed')
    iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'] = os.getenv('IGA_IDEMPOTENCY_STATUS_FAILED', 'failed')
    iga_config['IGA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS'] = int(os.getenv('IGA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS', '3600'))

    app.logger.info("--- IGA Configuration (Vertex AI & GCS & Idempotency) ---")
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
    # Continue if Vertex AI init fails, but log critical error. Task will fail if it tries to use it.
    # raise ValueError(f"IGA Critical Error: Failed to initialize Vertex AI: {e}") # Or decide to fail startup

# --- Idempotency Database Helper Functions ---
def _get_iga_db_connection():
    """Establishes a connection to the PostgreSQL database for IGA idempotency."""
    if not PSYCOPG2_AVAILABLE:
        app.logger.error("IGA Idempotency: psycopg2-binary is not available. Cannot connect to PostgreSQL.")
        raise ConnectionError("IGA Idempotency: Missing psycopg2-binary library.")

    iga_db_url = iga_config.get('IGA_POSTGRES_DB_URL')

    if iga_db_url:
        try:
            conn = psycopg2.connect(dsn=iga_db_url, cursor_factory=RealDictCursor)
            app.logger.info("IGA Idempotency: Successfully connected to PostgreSQL using IGA_POSTGRES_DB_URL.")
            return conn
        except psycopg2.Error as e:
            app.logger.error(f"IGA Idempotency: Failed to connect using IGA_POSTGRES_DB_URL ('{iga_db_url}'): {e}. Falling back to individual components if configured.", exc_info=True)
            # Fallback to individual components only if the URL connection failed, and they are all present.
            # This behavior could be debated: if a specific URL is given and fails, should it always hard-fail?
            # For now, allowing fallback as per subtask instructions.

    # Fallback to individual components if IGA_POSTGRES_DB_URL is not set or connection with it failed (and we decided to allow fallback)
    app.logger.info("IGA Idempotency: IGA_POSTGRES_DB_URL not used or failed. Attempting connection with individual PostgreSQL components.")
    required_pg_vars = ['POSTGRES_HOST', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB']
    if not all(iga_config.get(var) for var in required_pg_vars):
        app.logger.error("IGA Idempotency: PostgreSQL individual connection variables not fully configured for fallback.")
        raise ConnectionError("IGA Idempotency: PostgreSQL individual environment variables not fully configured for fallback.")

    try:
        conn = psycopg2.connect(
            host=iga_config['POSTGRES_HOST'],
            port=iga_config['POSTGRES_PORT'],
            user=iga_config['POSTGRES_USER'],
            password=iga_config['POSTGRES_PASSWORD'],
            dbname=iga_config['POSTGRES_DB'],
            cursor_factory=RealDictCursor
        )
        app.logger.info("IGA Idempotency: Successfully connected to PostgreSQL using individual components as fallback.")
        return conn
    except psycopg2.Error as e:
        app.logger.error(f"IGA Idempotency: Unable to connect to PostgreSQL using individual components: {e}", exc_info=True)
        raise ConnectionError(f"IGA Idempotency: PostgreSQL connection failed (individual components): {e}") from e

def _check_idempotency_key(db_conn, idempotency_key: str, task_name: str) -> Optional[Dict[str, Any]]:
    """Checks for an existing idempotency key record."""
    log_extra = {"task_id": "IGAIdempotencyCheck", "idempotency_key": idempotency_key, "task_name": task_name}
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT idempotency_key, task_name, workflow_id, created_at, locked_at, status, result_payload, error_payload FROM idempotency_keys WHERE idempotency_key = %s AND task_name = %s",
                (idempotency_key, task_name)
            )
            record = cur.fetchone()
            if record:
                app.logger.info(f"Idempotency key found. Status: '{record['status']}'.", extra=log_extra)
                # Parse JSON payloads if they are strings
                if isinstance(record.get('result_payload'), str):
                    record['result_payload'] = json.loads(record['result_payload'])
                if isinstance(record.get('error_payload'), str):
                    record['error_payload'] = json.loads(record['error_payload'])
                return dict(record)
            app.logger.info("No existing idempotency key found.", extra=log_extra)
            return None
    except (psycopg2.Error, json.JSONDecodeError) as e:
        app.logger.error(f"IGA Idempotency: DB/JSON error checking key: {e}", exc_info=True, extra=log_extra)
        raise # Re-raise to be handled by the task logic

def _store_idempotency_record(db_conn, idempotency_key: str, task_name: str, status: str, workflow_id: Optional[str] = None, result_payload: Optional[dict] = None, error_payload: Optional[dict] = None, is_new_key: bool = True):
    """Stores or updates an idempotency record."""
    log_extra = {"task_id": "IGAIdempotencyStore", "idempotency_key": idempotency_key, "task_name": task_name, "new_status": status}
    current_ts_utc = datetime.now(timezone.utc)
    locked_at_val = current_ts_utc if status == iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'] else None

    try:
        with db_conn.cursor() as cur:
            if is_new_key:
                app.logger.info("Storing new idempotency key.", extra=log_extra)
                cur.execute(
                    """
                    INSERT INTO idempotency_keys (idempotency_key, task_name, workflow_id, locked_at, status, result_payload, error_payload, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO UPDATE SET
                        task_name = EXCLUDED.task_name, workflow_id = EXCLUDED.workflow_id,
                        locked_at = EXCLUDED.locked_at, status = EXCLUDED.status,
                        result_payload = EXCLUDED.result_payload, error_payload = EXCLUDED.error_payload,
                        created_at = idempotency_keys.created_at;
                    """,
                    (idempotency_key, task_name, workflow_id, locked_at_val, status,
                     json.dumps(result_payload) if result_payload else None,
                     json.dumps(error_payload) if error_payload else None, current_ts_utc)
                )
            else: # Update existing key
                app.logger.info("Updating existing idempotency key.", extra=log_extra)
                set_clauses = ["status = %s", "result_payload = %s", "error_payload = %s"]
                params_update = [status, json.dumps(result_payload) if result_payload else None, json.dumps(error_payload) if error_payload else None]

                if status == iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING']:
                    set_clauses.append("locked_at = %s")
                    params_update.append(current_ts_utc)
                elif status in [iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'], iga_config['IGA_IDEMPOTENCY_STATUS_FAILED']]:
                    set_clauses.append("locked_at = NULL") # Unlock on final states

                params_update.extend([idempotency_key, task_name])
                cur.execute(
                    f"UPDATE idempotency_keys SET {', '.join(set_clauses)} WHERE idempotency_key = %s AND task_name = %s;",
                    tuple(params_update)
                )
            app.logger.info("Successfully stored/updated IGA idempotency key.", extra=log_extra)
    except (psycopg2.Error, json.JSONDecodeError) as e:
        app.logger.error(f"IGA Idempotency: DB/JSON error storing key: {e}", exc_info=True, extra=log_extra)
        raise # Re-raise for task handling


# --- Celery Task Definition ---
class GenerateImageTask(Celery.Task): # Inherit from Celery.Task for on_failure
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        app.logger.error(f'Celery Task {task_id} (IGA GenerateImage) failed: {exc}', exc_info=einfo)
        idempotency_key = kwargs.get('idempotency_key')
        task_name = self.name # e.g., 'generate_image_vertex_ai_task'

        if idempotency_key and PSYCOPG2_AVAILABLE:
            db_conn = None
            try:
                db_conn = _get_iga_db_connection()
                if db_conn:
                    db_conn.autocommit = False # Manage transaction
                    error_payload = {"error_type": type(exc).__name__, "error_message": str(exc), "traceback": str(einfo)}
                    _store_idempotency_record(db_conn, idempotency_key, task_name,
                                              iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'],
                                              error_payload=error_payload, is_new_key=False)
                    db_conn.commit()
                    app.logger.info(f"Idempotency record for key {idempotency_key} marked as FAILED for IGA task.")
            except Exception as db_err:
                app.logger.error(f"Failed to update idempotency record to FAILED for key {idempotency_key} (IGA task) after task failure: {db_err}", exc_info=True)
                if db_conn: db_conn.rollback()
            finally:
                if db_conn and not db_conn.closed:
                    try: db_conn.close()
                    except Exception: pass # Ignore errors on close during failure handling

@celery_app.task(bind=True, base=GenerateImageTask, name='generate_image_vertex_ai_task')
def generate_image_vertex_ai_task(self, request_id: str, prompt: str, aspect_ratio: str, add_watermark: bool, model_id: str, gcs_bucket_name: str, gcs_image_prefix: str, idempotency_key: Optional[str] = None, workflow_id: Optional[str] = None):
    """
    Celery task to generate an image using Vertex AI, upload to GCS, with idempotency.
    """
    task_log_id = self.request.id # Celery's unique ID for this task execution
    log_extra_base = {"orig_req_id": request_id, "celery_task_id": task_log_id, "idempotency_key": idempotency_key}
    app.logger.info(f"IGA Celery Task {task_log_id}: Starting. Prompt: '{prompt[:50]}...'", extra=log_extra_base)

    if not idempotency_key:
        app.logger.error(f"IGA Celery Task {task_log_id}: Idempotency key not provided. This is required.", extra=log_extra_base)
        # This case should ideally be prevented by the calling endpoint.
        # If it happens, it's a system error, not a user error for this task.
        raise ValueError("Idempotency key is required for IGA task execution.")

    if not PSYCOPG2_AVAILABLE:
        app.logger.error(f"IGA Celery Task {task_log_id}: psycopg2 not available, cannot perform idempotency checks. Failing task.", extra=log_extra_base)
        raise ConnectionError("IGA Task: psycopg2 is required for idempotency but not available.")

    db_conn = None
    try:
        db_conn = _get_iga_db_connection()
        db_conn.autocommit = False # Explicit transaction management

        existing_record = _check_idempotency_key(db_conn, idempotency_key, self.name)

        if existing_record:
            status = existing_record['status']
            locked_at = existing_record.get('locked_at') # May be None

            if status == iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED']:
                app.logger.info(f"IGA Task {task_log_id}: Idempotency key '{idempotency_key}' already COMPLETED. Returning stored result.", extra=log_extra_base)
                db_conn.rollback() # Release connection, no changes made
                return existing_record['result_payload']

            elif status == iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING']:
                timeout_seconds = iga_config['IGA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']
                if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < timeout_seconds:
                    app.logger.warning(f"IGA Task {task_log_id}: Idempotency key '{idempotency_key}' is already PROCESSING and lock not timed out. Conflict.", extra=log_extra_base)
                    db_conn.rollback()
                    # For Celery, it's better to return a specific state/error object than raise a "business logic" exception here,
                    # unless this should trigger Celery's retry mechanisms for other reasons.
                    return {"status": "PROCESSING_CONFLICT", "message": "Task with this idempotency key is already processing.", "idempotency_key": idempotency_key}
                else:
                    app.logger.warning(f"IGA Task {task_log_id}: Idempotency key '{idempotency_key}' was PROCESSING but lock timed out or missing. Re-processing.", extra=log_extra_base)
                    _store_idempotency_record(db_conn, idempotency_key, self.name, iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)

            elif status == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED']:
                app.logger.info(f"IGA Task {task_log_id}: Idempotency key '{idempotency_key}' previously FAILED. Retrying.", extra=log_extra_base)
                _store_idempotency_record(db_conn, idempotency_key, self.name, iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
        else: # No existing record
            app.logger.info(f"IGA Task {task_log_id}: New idempotency key '{idempotency_key}'. Storing as PROCESSING.", extra=log_extra_base)
            _store_idempotency_record(db_conn, idempotency_key, self.name, iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=True)

        db_conn.commit() # Commit the PROCESSING state

        # --- Main Task Logic ---
        app.logger.info(f"IGA Task {task_log_id}: Proceeding with image generation for key '{idempotency_key}'.", extra=log_extra_base)
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

        images_response = model.generate_images(
            prompt=prompt, number_of_images=1,
            aspect_ratio=aspect_ratio, add_watermark=add_watermark
        )

        if not images_response or not images_response.images:
            app.logger.error(f"IGA Task {task_log_id}: No images from Vertex AI for prompt: '{prompt}'", extra=log_extra_base)
            raise ValueError("Vertex AI returned no images.") # This will trigger on_failure

        image_object = images_response.images[0]
        if not hasattr(image_object, '_image_bytes') or not image_object._image_bytes:
            app.logger.error(f"IGA Task {task_log_id}: Vertex AI image bytes missing for prompt: '{prompt}'", extra=log_extra_base)
            raise ValueError("Vertex AI produced empty image bytes.") # Triggers on_failure

        storage_client = storage.Client()
        bucket = storage_client.bucket(gcs_bucket_name)
        file_extension = "png"
        gcs_object_name = f"{gcs_image_prefix.strip('/')}/{request_id}_{uuid.uuid4().hex[:8]}.{file_extension}"
        blob = bucket.blob(gcs_object_name)
        gcs_content_type = 'image/png'

        blob.upload_from_string(image_object._image_bytes, content_type=gcs_content_type)
        image_gcs_uri = f"gs://{gcs_bucket_name}/{gcs_object_name}"
        app.logger.info(f"IGA Task {task_log_id}: Image uploaded to GCS: {image_gcs_uri}", extra=log_extra_base)

        task_result_payload = {
            "image_url": image_gcs_uri, "prompt_used": prompt,
            "model_version": f"vertex-ai-{model_id}"
        }

        # Store successful result in idempotency table
        _store_idempotency_record(db_conn, idempotency_key, self.name,
                                  iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'],
                                  workflow_id=workflow_id, result_payload=task_result_payload, is_new_key=False)
        db_conn.commit()
        app.logger.info(f"IGA Task {task_log_id}: Successfully processed and stored COMPLETED status for key '{idempotency_key}'.", extra=log_extra_base)
        return task_result_payload

    except google_exceptions.GoogleAPIError as e: # Specific retryable error for Google APIs
        app.logger.error(f"IGA Task {task_log_id}: Google Vertex AI/GCS API Error for key '{idempotency_key}': {e}", exc_info=True, extra=log_extra_base)
        # Let on_failure handle marking idempotency as FAILED. Celery will manage retries.
        raise self.retry(exc=e, countdown=20, max_retries=3) # Increased countdown for API errors
    except Exception as e: # Catch-all for other unexpected errors
        app.logger.error(f"IGA Task {task_log_id}: Unexpected error for key '{idempotency_key}': {e}", exc_info=True, extra=log_extra_base)
        # Let on_failure handle marking idempotency as FAILED. Celery will manage retries if configured, or task fails.
        # For non-API errors, retry might be less useful, but depends on error.
        # Default Celery task retry is often 3 times. We can customize if needed.
        raise # Re-raise to trigger on_failure and standard Celery error handling/retry.
    finally:
        if db_conn:
            if not db_conn.closed: # Ensure not to operate on a closed connection
                # If autocommit was false and an unhandled exception occurred before commit/rollback for PROCESSING,
                # the transaction might be open. Rollback to be safe, though commits are explicit.
                # psycopg2 typically requires a rollback after an error in a transaction.
                # However, our commits are explicit for idempotency state changes.
                # A simple close should be fine here as commit/rollback is handled per state change.
                db_conn.close()
                app.logger.debug(f"IGA Task {task_log_id}: Closed DB connection for key '{idempotency_key}'.", extra=log_extra_base)


@app.route("/generate_image", methods=["POST"])
def generate_image_async_endpoint():
    request_id = f"iga_req_{uuid.uuid4().hex[:8]}"
    app.logger.info(f"IGA Request {request_id}: Received async /generate_image request.")

    idempotency_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
    workflow_id = request.headers.get("X-Workflow-ID") # Optional workflow ID

    if not idempotency_key:
        app.logger.warning(f"IGA Request {request_id}: Missing X-Idempotency-Key header.")
        return jsonify({"error_code": "IGA_MISSING_IDEMPOTENCY_KEY", "message": "X-Idempotency-Key header is required."}), 400

    # --- Idempotency Pre-check at Endpoint Level ---
    # Task name should match the Celery task name for consistency in the idempotency_keys table
    idem_task_name_for_db = 'generate_image_vertex_ai_task' # Should match celery_app.task name
    db_conn_http = None
    if PSYCOPG2_AVAILABLE:
        try:
            db_conn_http = _get_iga_db_connection()
            # For read or single write operations not needing rollback for this pre-check.
            # If we write 'processing', we commit it.
            db_conn_http.autocommit = False

            existing_record = _check_idempotency_key(db_conn_http, idempotency_key, idem_task_name_for_db)
            if existing_record:
                status = existing_record['status']
                locked_at = existing_record.get('locked_at')
                lock_timeout = iga_config['IGA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']

                if status == iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED']:
                    app.logger.info(f"IGA Request {request_id}: Idempotency key '{idempotency_key}' already COMPLETED. Returning stored result.", extra={'workflow_id': workflow_id})
                    db_conn_http.rollback() # No changes needed
                    return jsonify(existing_record['result_payload']), 200 # OK
                elif status == iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING']:
                    if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < lock_timeout:
                        app.logger.warning(f"IGA Request {request_id}: Idempotency key '{idempotency_key}' is PROCESSING. Returning conflict.", extra={'workflow_id': workflow_id})
                        db_conn_http.rollback() # No changes needed
                        return jsonify({"error_code": "IGA_IDEMPOTENCY_CONFLICT", "message": "Request with this idempotency key is currently processing."}), 409 # Conflict
                    else: # Lock expired or null
                        app.logger.info(f"IGA Request {request_id}: Idempotency key '{idempotency_key}' was PROCESSING but lock expired/null. Will re-process.", extra={'workflow_id': workflow_id})
                        _store_idempotency_record(db_conn_http, idempotency_key, idem_task_name_for_db, iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
                        db_conn_http.commit()
                elif status == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED']:
                    app.logger.info(f"IGA Request {request_id}: Idempotency key '{idempotency_key}' previously FAILED. Will re-process.", extra={'workflow_id': workflow_id})
                    _store_idempotency_record(db_conn_http, idempotency_key, idem_task_name_for_db, iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
                    db_conn_http.commit()
            else: # No existing record, so store a new one as "processing"
                app.logger.info(f"IGA Request {request_id}: New idempotency key '{idempotency_key}'. Storing as PROCESSING.", extra={'workflow_id': workflow_id})
                _store_idempotency_record(db_conn_http, idempotency_key, idem_task_name_for_db, iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=True)
                db_conn_http.commit()
        except psycopg2.Error as db_err_http:
            app.logger.error(f"IGA Request {request_id}: Database error during HTTP idempotency pre-check for key '{idempotency_key}': {db_err_http}", exc_info=True, extra={'workflow_id': workflow_id})
            if db_conn_http: db_conn_http.rollback()
            # Allow to proceed, Celery task will handle idempotency. Or, could return 503.
            app.logger.warning(f"IGA Request {request_id}: Proceeding to Celery dispatch despite DB error in pre-check. Celery task will manage idempotency.")
        except Exception as e_idem_http:
            app.logger.error(f"IGA Request {request_id}: Unexpected error during HTTP idempotency pre-check for key '{idempotency_key}': {e_idem_http}", exc_info=True, extra={'workflow_id': workflow_id})
            if db_conn_http: db_conn_http.rollback()
            # Allow Celery task to handle it to ensure task submission if DB issue is transient.
            app.logger.warning(f"IGA Request {request_id}: Proceeding to Celery dispatch despite unexpected error in pre-check. Celery task will manage idempotency.")
        finally:
            if db_conn_http and not db_conn_http.closed:
                db_conn_http.close()
    else: # psycopg2 not available at endpoint
        app.logger.warning(f"IGA Request {request_id}: psycopg2 not available. Skipping HTTP endpoint idempotency pre-check for key '{idempotency_key}'. Celery task will handle.", extra={'workflow_id': workflow_id})

    if not iga_config.get("GCS_BUCKET_NAME"):
        app.logger.error(f"IGA Request {request_id}: GCS_BUCKET_NAME not configured.")
        # This is a critical configuration error, should be caught before dispatching task.
        # If idempotency key was set to "processing", this might leave it in that state until timeout.
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

    aspect_ratio = data.get("aspect_ratio", iga_config['IGA_DEFAULT_ASPECT_RATIO'])
    add_watermark = data.get("add_watermark", iga_config['IGA_ADD_WATERMARK'])
    model_id_to_use = data.get("model_id_override", iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'])

    app.logger.info(f"IGA Request {request_id}: Dispatching image generation to Celery task. Prompt: '{prompt[:50]}...', Idempotency-Key: {idempotency_key}")

    task = generate_image_vertex_ai_task.delay(
        request_id=request_id,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        add_watermark=add_watermark,
        model_id=model_id_to_use,
        gcs_bucket_name=iga_config['GCS_BUCKET_NAME'],
        gcs_image_prefix=iga_config['IGA_GCS_IMAGE_PREFIX'],
        idempotency_key=idempotency_key, # Pass the key
        workflow_id=workflow_id
    )

    return jsonify({
        "message": "Image generation task accepted.",
        "task_id": task.id,
        "status_url": f"/v1/tasks/{task.id}",
        "idempotency_key_processed": idempotency_key
        }), 202


@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    app.logger.info(f"Received request for IGA task status: {task_id}")
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}

    if task_result.successful():
        task_output = task_result.result
        response_data["result"] = task_output
        http_status = 200
        # Check if the task result itself indicates a business logic conflict for idempotency
        if isinstance(task_output, dict) and task_output.get("status") == "PROCESSING_CONFLICT":
            http_status = 409 # Conflict
        return jsonify(response_data), http_status
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
    # Read FLASK_DEBUG directly for running the app
    flask_debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == 'true'

    app.logger.info(f"--- IGA Service (Vertex AI & GCS) starting on {host}:{port} (Debug: {flask_debug_mode}) ---")
    app.run(host=host, port=port, debug=flask_debug_mode)

[end of aethercast/iga/main.py]
