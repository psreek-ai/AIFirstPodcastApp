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
from google.cloud import storage
from google.api_core import exceptions as google_exceptions
import time
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from aethercast.common.celery import create_celery_app
from aethercast.common.db import get_db_connection, release_db_connection, init_db_connection_pool
from aethercast.common.idempotency import check_idempotency, acquire_idempotency_lock, update_idempotency_record

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
celery_app = create_celery_app('iga_tasks')

# --- Logging Setup ---
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="iga"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

app = Flask(__name__)

def setup_json_logging(flask_app):
    flask_app.logger.handlers.clear()
    logHandler = logging.StreamHandler()
    service_filter = ServiceNameFilter("iga")
    logHandler.addFilter(service_filter)

    from python_json_logger import jsonlogger
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s %(task_id)s %(workflow_id)s %(idempotency_key)s %(prompt_preview)s"
    )
    logHandler.setFormatter(formatter)

    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("JSON logging configured for IGA service.", extra={'task_id': 'N/A', 'workflow_id': 'N/A', 'idempotency_key': 'N/A', 'prompt_preview': 'N/A'})

setup_json_logging(app)


# --- IGA Configuration ---
IGA_HOST = os.getenv("IGA_HOST", "0.0.0.0")
IGA_PORT = int(os.getenv("IGA_PORT", 5007))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == 'true'

IGA_VERTEXAI_PROJECT_ID = os.getenv("IGA_VERTEXAI_PROJECT_ID", os.getenv("GCP_PROJECT_ID"))
IGA_VERTEXAI_LOCATION = os.getenv("IGA_VERTEXAI_LOCATION", os.getenv("GCP_LOCATION"))
IGA_VERTEXAI_IMAGE_MODEL_ID = os.getenv("IGA_VERTEXAI_IMAGE_MODEL_ID", "imagegeneration@006")

GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
IGA_GCS_IMAGE_PREFIX = os.getenv("IGA_GCS_IMAGE_PREFIX", "images/iga/")
IGA_GENERATED_IMAGE_DIR = os.getenv("IGA_GENERATED_IMAGE_DIR", "/shared_audio/iga_images")
IGA_DEFAULT_ASPECT_RATIO = os.getenv("IGA_DEFAULT_ASPECT_RATIO", "1:1")
IGA_ADD_WATERMARK = os.getenv("IGA_ADD_WATERMARK", "True").lower() == "true"
GOOGLE_APPLICATION_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

IDEMPOTENCY_LOCK_TIMEOUT_SECONDS = int(os.getenv('IGA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS', '3600'))


try:
    app.logger.info(f"Initializing Vertex AI for project '{IGA_VERTEXAI_PROJECT_ID}' in location '{IGA_VERTEXAI_LOCATION}'...")
    aiplatform.init(project=IGA_VERTEXAI_PROJECT_ID, location=IGA_VERTEXAI_LOCATION)
    app.logger.info("Vertex AI initialized successfully for IGA.")
except Exception as e:
    app.logger.error(f"Failed to initialize Vertex AI for IGA: {e}", exc_info=True)

GLOBAL_IMAGE_MODEL = None
if IGA_VERTEXAI_IMAGE_MODEL_ID:
    try:
        app.logger.info(f"IGA: Pre-loading Vertex AI Image Generation Model: {IGA_VERTEXAI_IMAGE_MODEL_ID}")
        GLOBAL_IMAGE_MODEL = ImageGenerationModel.from_pretrained(IGA_VERTEXAI_IMAGE_MODEL_ID)
        app.logger.info("IGA: Vertex AI Image Generation Model pre-loaded successfully.")
    except Exception as e_model_load:
        app.logger.critical(f"IGA CRITICAL: Failed to pre-load Vertex AI Image Generation Model '{IGA_VERTEXAI_IMAGE_MODEL_ID}': {e_model_load}", exc_info=True)
else:
    app.logger.warning("IGA: IGA_VERTEXAI_IMAGE_MODEL_ID not configured. Image model will not be pre-loaded.")

GLOBAL_STORAGE_CLIENT = None
try:
    app.logger.info("IGA: Initializing Google Cloud Storage client...")
    GLOBAL_STORAGE_CLIENT = storage.Client()
    app.logger.info("IGA: Google Cloud Storage client initialized successfully.")
except Exception as e_storage_client_init:
    app.logger.critical(f"IGA CRITICAL: Failed to initialize Google Cloud Storage client: {e_storage_client_init}", exc_info=True)

class GenerateImageTask(Celery.Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        app.logger.error(f'Celery Task {task_id} (IGA GenerateImage) failed: {exc}', exc_info=einfo)
        idempotency_key = kwargs.get('idempotency_key')
        task_name = self.name
        if idempotency_key and PSYCOPG2_AVAILABLE:
            db_conn = None
            try:
                db_conn = get_db_connection(service_name='iga')
                if db_conn:
                    error_payload = {"error_type": type(exc).__name__, "error_message": str(exc), "traceback": str(einfo)}
                    update_idempotency_record(db_conn, idempotency_key, task_name, 'failed',
                                              error_payload=error_payload, service_name='iga')
                    app.logger.info(f"Idempotency record for key {idempotency_key} marked as FAILED for IGA task.")
            except Exception as db_err:
                app.logger.error(f"Failed to update idempotency record to FAILED for key {idempotency_key} (IGA task) after task failure: {db_err}", exc_info=True)
            finally:
                if db_conn:
                    release_db_connection(db_conn, service_name='iga')

@celery_app.task(bind=True, base=GenerateImageTask, name='generate_image_vertex_ai_task')
def generate_image_vertex_ai_task(self, request_id: str, prompt: str, aspect_ratio: str, add_watermark: bool, model_id: str, gcs_bucket_name: str, gcs_image_prefix: str, idempotency_key: Optional[str] = None, workflow_id: Optional[str] = None, test_scenario: Optional[str] = None):
    task_log_id = self.request.id
    log_extra_base = {
        "orig_req_id": request_id, "task_id": task_log_id,
        "idempotency_key": idempotency_key, "workflow_id": workflow_id,
        "prompt_preview": (prompt[:50] + "..." if len(prompt) > 50 else prompt) if isinstance(prompt, str) else "N/A"
    }
    app.logger.info(f"IGA Celery Task {task_log_id}: Starting. Prompt: '{prompt[:50]}...'", extra=log_extra_base)

    if not idempotency_key:
        app.logger.error(f"IGA Celery Task {task_log_id}: Idempotency key not provided. This is required.", extra=log_extra_base)
        raise ValueError("Idempotency key is required for IGA task execution.")

    if not PSYCOPG2_AVAILABLE:
        app.logger.error(f"IGA Celery Task {task_log_id}: psycopg2 not available, cannot perform idempotency checks. Failing task.", extra=log_extra_base)
        raise ConnectionError("IGA Task: psycopg2 is required for idempotency but not available.")

    db_conn = None
    try:
        db_conn = get_db_connection(service_name='iga')
        task_name = self.name

        idempotency_check = check_idempotency(db_conn, idempotency_key, task_name, IDEMPOTENCY_LOCK_TIMEOUT_SECONDS, service_name='iga')

        if idempotency_check:
            if idempotency_check['status'] == 'completed':
                app.logger.info(f"IGA Task {task_log_id}: Idempotency key '{idempotency_key}' already COMPLETED. Returning stored result.", extra=log_extra_base)
                return idempotency_check['result']
            elif idempotency_check['status'] == 'conflict':
                app.logger.warning(f"IGA Task {task_log_id}: Idempotency key '{idempotency_key}' is already PROCESSING and lock not timed out. Conflict.", extra=log_extra_base)
                return {"status": "PROCESSING_CONFLICT", "message": "Task with this idempotency key is already processing.", "idempotency_key": idempotency_key}

        if not acquire_idempotency_lock(db_conn, idempotency_key, task_name, workflow_id, service_name='iga'):
            app.logger.error(f"IGA Task {task_log_id}: Failed to acquire idempotency lock for key '{idempotency_key}'. Aborting.", extra=log_extra_base)
            return {"status": "ERROR", "message": "Failed to acquire idempotency lock.", "idempotency_key": idempotency_key}


        if test_scenario:
            if test_scenario == 'success_placeholder':
                app.logger.info(f"IGA Task {task_log_id}: Test mode 'success_placeholder' active.", extra=log_extra_base)
                placeholder_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
                task_result_payload = {"status": "success", "image_base64": placeholder_base64, "image_format": "png", "gcs_uri": None, "signed_url": None, "message": "Placeholder image generated successfully."}
                update_idempotency_record(db_conn, idempotency_key, task_name, 'completed', result_payload=task_result_payload, service_name='iga')
                return task_result_payload
            elif test_scenario == 'error_vertex_ai':
                app.logger.warning(f"IGA Task {task_log_id}: Test mode 'error_vertex_ai' active. Simulating Vertex AI failure.", extra=log_extra_base)
                error_payload_for_idempotency = {"error_type": "SimulatedVertexAIError", "message": "Test mode: Simulated Vertex AI image generation failure.", "details": "Vertex AI unavailable (test scenario)"}
                update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload_for_idempotency, service_name='iga')
                raise RuntimeError("Simulated Vertex AI error in IGA test mode")

        app.logger.info(f"IGA Task {task_log_id}: Proceeding with image generation for key '{idempotency_key}'.", extra=log_extra_base)
        model_to_use = None
        if model_id == IGA_VERTEXAI_IMAGE_MODEL_ID and GLOBAL_IMAGE_MODEL:
            model_to_use = GLOBAL_IMAGE_MODEL
            app.logger.info(f"IGA Task {task_log_id}: Using pre-loaded default model: {model_id}", extra=log_extra_base)
        elif GLOBAL_IMAGE_MODEL is None and model_id == IGA_VERTEXAI_IMAGE_MODEL_ID:
            app.logger.error(f"IGA Task {task_log_id}: Default global Vertex AI image model ('{model_id}') is not available (failed pre-load). Cannot generate image.", extra=log_extra_base)
            raise RuntimeError(f"IGA Critical: Default Vertex AI Image Model ('{model_id}') not loaded at startup and needed for this task.")
        else:
            app.logger.info(f"IGA Task {task_log_id}: Model '{model_id}' not pre-loaded or not default. Attempting on-demand load.", extra=log_extra_base)
            try:
                model_to_use = ImageGenerationModel.from_pretrained(model_id)
            except Exception as e_model_demand:
                app.logger.error(f"IGA Task {task_log_id}: Failed to initialize model '{model_id}' on demand: {e_model_demand}", exc_info=True, extra=log_extra_base)
                raise RuntimeError(f"IGA Critical: Failed to load model '{model_id}' on demand.") from e_model_demand

        if model_to_use is None:
             app.logger.error(f"IGA Task {task_log_id}: Model '{model_id}' could not be loaded/retrieved.", extra=log_extra_base)
             raise RuntimeError(f"IGA Critical: Model '{model_id}' unavailable for generation.")

        images_response = model_to_use.generate_images(prompt=prompt, number_of_images=1, aspect_ratio=aspect_ratio, add_watermark=add_watermark)

        if not images_response or not images_response.images:
            app.logger.error(f"IGA Task {task_log_id}: No images from Vertex AI for prompt: '{prompt}'", extra=log_extra_base)
            error_payload = {"error_type": "NoImageGenerated", "message": "Vertex AI returned no images."}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload, service_name='iga')
            return {"status": "error", "message": "Image generation failed: No images returned from Vertex AI."}

        image_object = images_response.images[0]
        if not hasattr(image_object, '_image_bytes') or not image_object._image_bytes:
            app.logger.error(f"IGA Task {task_log_id}: Vertex AI image bytes missing for prompt: '{prompt}'", extra=log_extra_base)
            error_payload = {"error_type": "EmptyImageBytes", "message": "Vertex AI produced empty image bytes."}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload, service_name='iga')
            return {"status": "error", "message": "Image generation failed: Empty image data."}

        image_bytes = image_object._image_bytes
        app.logger.info(f"IGA Task {task_log_id}: Image bytes accessed from Vertex AI.", extra=log_extra_base)

        if not gcs_bucket_name or not GLOBAL_STORAGE_CLIENT:
            app.logger.error(f"IGA Task {task_log_id}: GCS bucket name or storage client not available. Cannot upload image.", extra=log_extra_base)
            raise ValueError("GCS configuration error: Bucket name or client missing.")

        prefix = gcs_image_prefix.strip('/') + '/' if gcs_image_prefix.strip('/') else ''
        image_filename = f"{prefix}{idempotency_key or request_id}_{uuid.uuid4().hex[:8]}.png"
        storage_client = GLOBAL_STORAGE_CLIENT
        bucket = storage_client.bucket(gcs_bucket_name)
        blob = bucket.blob(image_filename)
        upload_start_time = time.time()

        try:
            blob.upload_from_string(image_bytes, content_type="image/png")
            gcs_uri = f"gs://{gcs_bucket_name}/{image_filename}"
            upload_duration_ms = (time.time() - upload_start_time) * 1000
            app.logger.info(f"IGA Task {task_log_id}: Image successfully uploaded to {gcs_uri}. Upload duration: {upload_duration_ms:.2f}ms",
                            extra={**log_extra_base, "metric_name": "iga_gcs_upload_duration_ms", "value": round(upload_duration_ms, 2), "gcs_uri": gcs_uri})
        except google_exceptions.GoogleAPIError as e_gcs:
            app.logger.error(f"IGA Task {task_log_id}: GCS upload failed for {image_filename}: {e_gcs}", exc_info=True, extra=log_extra_base)
            raise

        task_result_payload = {"status": "success", "image_url": gcs_uri, "prompt_used": prompt, "model_version": model_id }
        update_idempotency_record(db_conn, idempotency_key, task_name, 'completed', result_payload=task_result_payload, service_name='iga')
        app.logger.info(f"IGA Task {task_log_id}: Successfully processed and stored COMPLETED status for key '{idempotency_key}'. Image GCS URI: {gcs_uri}.", extra=log_extra_base)
        return task_result_payload

    except google_exceptions.GoogleAPIError as e:
        app.logger.error(f"IGA Task {task_log_id}: Google Vertex AI/GCS API Error for key '{idempotency_key}': {e}", exc_info=True, extra=log_extra_base)
        update_idempotency_record(db_conn, idempotency_key, self.name, 'failed', error_payload={'error': str(e)}, service_name='iga')
        raise self.retry(exc=e, countdown=20, max_retries=3)
    except Exception as e:
        app.logger.error(f"IGA Task {task_log_id}: Unexpected error for key '{idempotency_key}': {e}", exc_info=True, extra=log_extra_base)
        update_idempotency_record(db_conn, idempotency_key, self.name, 'failed', error_payload={'error': str(e)}, service_name='iga')
        raise
    finally:
        if db_conn:
            release_db_connection(db_conn, service_name='iga')

@app.route("/generate_image", methods=["POST"])
def generate_image_async_endpoint():
    request_id = f"iga_req_{uuid.uuid4().hex[:8]}"
    app.logger.info(f"IGA Request {request_id}: Received async /generate_image request.")
    idempotency_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
    workflow_id = request.headers.get("X-Workflow-ID")

    if not idempotency_key:
        app.logger.warning(f"IGA Request {request_id}: Missing X-Idempotency-Key header.")
        return jsonify({"error_code": "IGA_MISSING_IDEMPOTENCY_KEY", "message": "X-Idempotency-Key header is required."}), 400

    idem_task_name_for_db = 'generate_image_vertex_ai_task'
    db_conn_http = None
    if PSYCOPG2_AVAILABLE:
        try:
            db_conn_http = get_db_connection(service_name='iga-http')
            idempotency_check = check_idempotency(db_conn_http, idempotency_key, idem_task_name_for_db, IDEMPOTENCY_LOCK_TIMEOUT_SECONDS, service_name='iga-http')

            if idempotency_check:
                if idempotency_check['status'] == 'completed':
                    app.logger.info(f"IGA Request {request_id}: Idempotency key '{idempotency_key}' already COMPLETED. Returning stored result.", extra={'workflow_id': workflow_id})
                    return jsonify(idempotency_check['result']), 200
                elif idempotency_check['status'] == 'conflict':
                    app.logger.warning(f"IGA Request {request_id}: Idempotency key '{idempotency_key}' is PROCESSING. Returning conflict.", extra={'workflow_id': workflow_id})
                    return jsonify({"error_code": "IGA_IDEMPOTENCY_CONFLICT", "message": "Request with this idempotency key is currently processing."}), 409

        except Exception as e_idem_http:
            app.logger.error(f"IGA Request {request_id}: Unexpected error during HTTP idempotency pre-check for key '{idempotency_key}': {e_idem_http}", exc_info=True, extra={'workflow_id': workflow_id})
            app.logger.warning(f"IGA Request {request_id}: Proceeding to Celery dispatch despite unexpected error in pre-check. Celery task will manage idempotency.")
        finally:
            if db_conn_http:
                release_db_connection(db_conn_http, service_name='iga-http')
    else:
        app.logger.warning(f"IGA Request {request_id}: psycopg2 not available. Skipping HTTP endpoint idempotency pre-check for key '{idempotency_key}'. Celery task will handle.", extra={'workflow_id': workflow_id})

    if not GCS_BUCKET_NAME:
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

    aspect_ratio = data.get("aspect_ratio", IGA_DEFAULT_ASPECT_RATIO)
    add_watermark = data.get("add_watermark", IGA_ADD_WATERMARK)
    model_id_to_use = data.get("model_id_override", IGA_VERTEXAI_IMAGE_MODEL_ID)
    test_scenario_header = request.headers.get('X-Test-Scenario')

    app.logger.info(f"IGA Request {request_id}: Dispatching image generation to Celery task. Prompt: '{prompt[:50]}...', Idempotency-Key: {idempotency_key}, Test-Scenario: {test_scenario_header}")
    task = generate_image_vertex_ai_task.delay(
        request_id=request_id, prompt=prompt, aspect_ratio=aspect_ratio, add_watermark=add_watermark,
        model_id=model_id_to_use, gcs_bucket_name=GCS_BUCKET_NAME, gcs_image_prefix=IGA_GCS_IMAGE_PREFIX,
        idempotency_key=idempotency_key, workflow_id=workflow_id, test_scenario=test_scenario_header
    )
    return jsonify({"message": "Image generation task accepted.", "task_id": task.id, "status_url": f"/v1/tasks/{task.id}", "idempotency_key_processed": idempotency_key }), 202

@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    app.logger.info(f"Received request for IGA task status: {task_id}")
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}
    if task_result.successful():
        task_output = task_result.result
        response_data["result"] = task_output
        http_status = 200
        if isinstance(task_output, dict) and task_output.get("status") == "PROCESSING_CONFLICT":
            http_status = 409
        return jsonify(response_data), http_status
    elif task_result.failed():
        error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
        response_data["result"] = error_info
        return jsonify(response_data), 500
    else:
        return jsonify(response_data), 202

if __name__ == "__main__":
    # Validate critical configurations at startup
    if not IGA_VERTEXAI_PROJECT_ID:
        app.logger.critical("CRITICAL: IGA_VERTEXAI_PROJECT_ID is not set.")
        raise ValueError("IGA_VERTEXAI_PROJECT_ID is not set.")
    if not IGA_VERTEXAI_LOCATION:
        app.logger.critical("CRITICAL: IGA_VERTEXAI_LOCATION is not set.")
        raise ValueError("IGA_VERTEXAI_LOCATION is not set.")
    if not GCS_BUCKET_NAME:
        app.logger.critical("CRITICAL: GCS_BUCKET_NAME is not set for IGA. Image uploads will fail.")
        raise ValueError("GCS_BUCKET_NAME is not set for IGA.")
    if not GOOGLE_APPLICATION_CREDENTIALS:
        app.logger.warning("IGA WARNING: GOOGLE_APPLICATION_CREDENTIALS not explicitly set. Using ADC if configured.")

    # Initialize the common DB connection pool.
    init_db_connection_pool(service_name='iga')

    app.logger.info(f"--- IGA Service (Vertex AI & GCS) starting on {IGA_HOST}:{IGA_PORT} (Debug: {FLASK_DEBUG}) ---")
    app.run(host=IGA_HOST, port=IGA_PORT, debug=FLASK_DEBUG)
