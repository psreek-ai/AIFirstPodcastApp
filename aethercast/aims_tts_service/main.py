import os
import uuid
import logging
import json
from flask import Flask, jsonify, request
from dotenv import load_dotenv
from celery import Celery
from celery.result import AsyncResult
from google.cloud import texttospeech
from google.cloud import storage # Added for GCS
from google.api_core import exceptions as google_exceptions

# --- Database and Idempotency specific imports ---
import psycopg2
from psycopg2 import pool as psycopg2_pool
from psycopg2 import extras as psycopg2_extras # For DictCursor
# json is imported at the top

# --- Load Environment Variables ---
load_dotenv()

import time # Added for metric logging & idempotency stale check
from typing import Optional

from aethercast.common.celery import create_celery_app
from aethercast.common.db import get_db_connection, release_db_connection, init_db_connection_pool
from aethercast.common.idempotency import check_idempotency, acquire_idempotency_lock, update_idempotency_record


# --- Celery Configuration ---
celery_app = create_celery_app('aims_tts_tasks')

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="aims-tts-service"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

# Configure JSON logging for the Flask app
def setup_json_logging(flask_app):
    flask_app.logger.handlers.clear() # Clear existing default Flask handlers
    logHandler = logging.StreamHandler()
    service_filter = ServiceNameFilter("aims-tts-service")
    logHandler.addFilter(service_filter)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s"
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("Standard logging configured for AIMS_TTS service.")

setup_json_logging(app)

# Make the global logger use the configured app.logger
logger = app.logger

# --- Idempotency Configuration ---
# Using the common library for DB and idempotency now.
# The lock timeout is specific to the task, so we define it here.
IDEMPOTENCY_LOCK_TIMEOUT_SECONDS = 300 # 5 minutes (adjust as needed for TTS tasks)

# --- AIMS_TTS Configuration ---
AIMS_TTS_HOST = os.getenv('AIMS_TTS_HOST', '0.0.0.0')
AIMS_TTS_PORT = int(os.getenv('AIMS_TTS_PORT', 9000))
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

GOOGLE_APPLICATION_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
AIMS_TTS_DEFAULT_VOICE_ID = os.getenv('AIMS_TTS_DEFAULT_VOICE_ID', 'en-US-Wavenet-D')
AIMS_TTS_DEFAULT_LANGUAGE_CODE = os.getenv('AIMS_TTS_DEFAULT_LANGUAGE_CODE', 'en-US')
AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR = os.getenv('AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR', 'MP3').upper()
AIMS_TTS_DEFAULT_SPEAKING_RATE = float(os.getenv('AIMS_TTS_DEFAULT_SPEAKING_RATE', 1.0))
AIMS_TTS_DEFAULT_PITCH = float(os.getenv('AIMS_TTS_DEFAULT_PITCH', 0.0))

# GCS Configuration
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
AIMS_TTS_GCS_AUDIO_PREFIX = os.getenv("AIMS_TTS_GCS_AUDIO_PREFIX", "audio/")


# SHARED_AUDIO_DIR_CONTAINER is now for temporary local storage if needed, not the final URL.
# It might be removed if direct GCS upload is always performed without local temp files.
SHARED_AUDIO_DIR_CONTAINER = os.getenv('SHARED_AUDIO_DIR_CONTAINER', '/tmp/aims_tts_temp_audio')

logger.info("--- AIMS_TTS Service Configuration ---")
logger.info(f"  AIMS_TTS_HOST: {AIMS_TTS_HOST}")
logger.info(f"  AIMS_TTS_PORT: {AIMS_TTS_PORT}")
logger.info(f"  FLASK_DEBUG: {FLASK_DEBUG}")
logger.info(f"  GOOGLE_APPLICATION_CREDENTIALS: {'Set (path not logged)' if GOOGLE_APPLICATION_CREDENTIALS else 'Not Set'}")
logger.info(f"  GCS_BUCKET_NAME: {GCS_BUCKET_NAME}")
logger.info(f"  AIMS_TTS_GCS_AUDIO_PREFIX: {AIMS_TTS_GCS_AUDIO_PREFIX}")
logger.info(f"  AIMS_TTS_DEFAULT_VOICE_ID: {AIMS_TTS_DEFAULT_VOICE_ID}")
# ... (rest of logging for default params)
logger.info(f"  AIMS_TTS_DEFAULT_LANGUAGE_CODE: {AIMS_TTS_DEFAULT_LANGUAGE_CODE}")
logger.info(f"  AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR: {AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR}")
logger.info(f"  AIMS_TTS_DEFAULT_SPEAKING_RATE: {AIMS_TTS_DEFAULT_SPEAKING_RATE}")
logger.info(f"  AIMS_TTS_DEFAULT_PITCH: {AIMS_TTS_DEFAULT_PITCH}")
logger.info(f"  SHARED_AUDIO_DIR_CONTAINER (temp local): {SHARED_AUDIO_DIR_CONTAINER}")
logger.info("--- End AIMS_TTS Service Configuration ---")

if not GOOGLE_APPLICATION_CREDENTIALS:
    logger.critical("CRITICAL: GOOGLE_APPLICATION_CREDENTIALS is not set. Google Cloud services (TTS, GCS) will fail.")
    raise ValueError("AIMS_TTS Critical Error: GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")
if not GCS_BUCKET_NAME:
    logger.critical("CRITICAL: GCS_BUCKET_NAME is not set. Audio file uploads to GCS will fail.")
    raise ValueError("AIMS_TTS Critical Error: GCS_BUCKET_NAME environment variable not set.")

# --- Global Google Cloud Clients ---
GLOBAL_TTS_CLIENT = None
try:
    logger.info("AIMS_TTS: Initializing global Google TTS Client...")
    GLOBAL_TTS_CLIENT = texttospeech.TextToSpeechClient()
    logger.info("AIMS_TTS: Successfully initialized global Google TTS Client.")
except Exception as e_tts_client_init:
    logger.error(f"AIMS_TTS: Failed to initialize global Google TTS Client: {e_tts_client_init}", exc_info=True)
    # GLOBAL_TTS_CLIENT remains None, tasks will fail if they try to use it.

GLOBAL_STORAGE_CLIENT_TTS = None
try:
    logger.info("AIMS_TTS: Initializing global Google Cloud Storage Client...")
    GLOBAL_STORAGE_CLIENT_TTS = storage.Client()
    logger.info("AIMS_TTS: Successfully initialized global Google Cloud Storage Client.")
except Exception as e_storage_client_init:
    logger.error(f"AIMS_TTS: Failed to initialize global Google Cloud Storage Client: {e_storage_client_init}", exc_info=True)
    # GLOBAL_STORAGE_CLIENT_TTS remains None.

# --- Audio Encoding Mapping ---
AUDIO_ENCODING_MAP = {
    "MP3": {"enum": texttospeech.AudioEncoding.MP3, "mimetype": "audio/mpeg"},
    "LINEAR16": {"enum": texttospeech.AudioEncoding.LINEAR16, "mimetype": "audio/L16; rate=24000"}, # Example, rate depends on synthesis
    "OGG_OPUS": {"enum": texttospeech.AudioEncoding.OGG_OPUS, "mimetype": "audio/ogg"},
}
DEFAULT_AUDIO_ENCODING_DETAILS = AUDIO_ENCODING_MAP.get(AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR, AUDIO_ENCODING_MAP["MP3"])

# --- Helper to estimate duration (simple version) ---
def estimate_audio_duration(text_length: int, rate: float = 1.0) -> float:
    chars_per_second_at_normal_rate = 15
    estimated_duration = (text_length / chars_per_second_at_normal_rate) / rate
    return round(max(0.5, estimated_duration), 2)

@celery_app.task(bind=True, name='invoke_tts_google_task')
def invoke_tts_google_task(self, request_id: str, text_to_synthesize: str, voice_id: str, language_code: str, speech_rate: float, pitch: float, output_format_str: str, selected_audio_encoding_details: dict, file_extension: str):
    """
    Celery task to invoke Google TTS and upload to GCS.
    'self' is the task instance.
    """
    celery_task_internal_id = self.request.id or f"sync_tts_{uuid.uuid4().hex[:8]}"
    log_extra = {
        'request_id': request_id,
        'celery_task_id': celery_task_internal_id,
        'workflow_id': "N/A", # workflow_id is not explicitly passed here yet
        'voice_id_used': voice_id, # Renamed for clarity from 'voice_id' in original log
        'language_code_used': language_code, # Renamed for clarity
        'service_name': 'aims-tts-service'
    }
    logger.info(f"AIMS-TTS Task {celery_task_internal_id}: Starting TTS synthesis for user request {request_id}. Voice: {voice_id}", extra=log_extra)

    idempotency_key_str = request_id # Use user-provided request_id
    task_name_str = "aims_invoke_tts_google_task" # Explicit task name for idempotency table
    db_conn = None
    task_final_result = None

    try:
        db_conn = get_db_connection(service_name='aims-tts-service')
        idempotency_check = check_idempotency(db_conn, idempotency_key_str, task_name_str, IDEMPOTENCY_LOCK_TIMEOUT_SECONDS, service_name='aims-tts-service')

        if idempotency_check:
            if idempotency_check['status'] == 'completed':
                logger.info(f"Task '{task_name_str}' (req: {request_id}) already completed. Returning stored result.", extra=log_extra)
                return idempotency_check['result']
            elif idempotency_check['status'] == 'conflict':
                logger.warning(f"Task '{task_name_str}' (req: {request_id}) conflict: {idempotency_check['message']}.", extra=log_extra)
                return {"error": {"type": "idempotency_conflict", "message": idempotency_check['message']}, "request_id": request_id}

        if not acquire_idempotency_lock(db_conn, idempotency_key_str, task_name_str, log_extra['workflow_id'], service_name='aims-tts-service'):
            logger.error(f"Failed to acquire idempotency lock for task '{task_name_str}' (req: {request_id}). Aborting.", extra=log_extra)
            return {"error": {"type": "lock_acquisition_failed", "message": "Failed to acquire idempotency lock."}, "request_id": request_id}

        # Core TTS and GCS Upload Logic
        client = texttospeech.TextToSpeechClient() # Consider initializing client once if worker reuses context
        synthesis_input = texttospeech.SynthesisInput(text=text_to_synthesize)
        voice_params = texttospeech.VoiceSelectionParams(language_code=language_code, name=voice_id)
        audio_config = texttospeech.AudioConfig(
            audio_encoding=selected_audio_encoding_details["enum"],
            speaking_rate=speech_rate,
            pitch=pitch
        )

        gcp_tts_call_start_time = time.time()
        tts_response = client.synthesize_speech(request={"input": synthesis_input, "voice": voice_params, "audio_config": audio_config})
        gcp_tts_call_duration_ms = (time.time() - gcp_tts_call_start_time) * 1000
        logger.info("AIMS_TTS GCP TTS call processed (async)", extra={**log_extra, "metric_name":"aims_tts_gcp_tts_call_latency_ms", "value":round(gcp_tts_call_duration_ms, 2)})

        storage_client_instance = storage.Client() # Ensure client is instantiated if not global or passed
        bucket = storage_client_instance.bucket(GCS_BUCKET_NAME)
        gcs_object_name = f"{AIMS_TTS_GCS_AUDIO_PREFIX}{request_id}_{uuid.uuid4().hex[:8]}.{file_extension}"
        blob = bucket.blob(gcs_object_name)
        gcs_content_type = selected_audio_encoding_details["mimetype"]
        if output_format_str == "LINEAR16" and "rate=" not in gcs_content_type: # Adjust content type for LINEAR16 if needed
             sample_rate_hz = tts_response.audio_config.sample_rate_hertz if hasattr(tts_response, 'audio_config') and tts_response.audio_config.sample_rate_hertz else 24000
             gcs_content_type = f"audio/L16; rate={sample_rate_hz}"

        gcs_upload_start_time = time.time()
        blob.upload_from_string(tts_response.audio_content, content_type=gcs_content_type)
        gcs_upload_duration_ms = (time.time() - gcs_upload_start_time) * 1000
        logger.info("AIMS_TTS GCS upload processed (async)", extra={**log_extra, "metric_name":"aims_tts_gcs_upload_latency_ms", "value":round(gcs_upload_duration_ms, 2)})

        audio_gcs_uri = f"gs://{GCS_BUCKET_NAME}/{gcs_object_name}"
        estimated_duration = estimate_audio_duration(len(text_to_synthesize), speech_rate)
        logger.info("AIMS_TTS characters synthesized (async)", extra={**log_extra, "metric_name":"aims_tts_synthesized_chars_count", "value":len(text_to_synthesize)})

        task_final_result = {
            "request_id": request_id, "voice_id": voice_id, "audio_url": audio_gcs_uri,
            "audio_duration_seconds": estimated_duration, "audio_format": file_extension
        }
        update_idempotency_record(db_conn, idempotency_key_str, task_name_str, 'completed', result_payload=task_final_result, service_name='aims-tts-service')
        return task_final_result

    except google_exceptions.GoogleAPIError as e:
        logger.error(f"AIMS-TTS Task {celery_task_internal_id}: Google Cloud API Error for req {request_id}: {e}", exc_info=True, extra=log_extra)
        logger.error("AIMS_TTS GCP API error (async)", extra={**log_extra, "metric_name":"aims_tts_gcp_error_count", "value":1, "tags_metric":{"error_type": "gcp_api_error"}})
        error_payload_db = {"error_type": type(e).__name__, "message": str(e), "details": e.args[0] if e.args else "N/A"}
        if db_conn:
            update_idempotency_record(db_conn, idempotency_key_str, task_name_str, 'failed', error_payload=error_payload_db, service_name='aims-tts-service')
        raise self.retry(exc=e, countdown=10, max_retries=2) # Adjusted retry

    except Exception as e:
        logger.error(f"AIMS-TTS Task {celery_task_internal_id}: Unexpected error for req {request_id}: {e}", exc_info=True, extra=log_extra)
        error_payload_db = {"error_type": type(e).__name__, "message": str(e)}
        if db_conn:
             update_idempotency_record(db_conn, idempotency_key_str, task_name_str, 'failed', error_payload=error_payload_db, service_name='aims-tts-service')
        raise self.retry(exc=e, countdown=5, max_retries=1) # Adjusted retry

    finally:
        if db_conn:
            release_db_connection(db_conn, service_name='aims-tts-service')


@app.route('/v1/synthesize', methods=['POST'])
def synthesize_speech_async():
    request_id = f"aims-tts-req-{uuid.uuid4().hex}"
    logger.info(f"Request {request_id}: Received async /v1/synthesize request.")

    if not GOOGLE_APPLICATION_CREDENTIALS or not GCS_BUCKET_NAME:
        logger.error(f"Request {request_id}: Service not configured.")
        return jsonify({"request_id": request_id, "error": {"type": "configuration_error", "message": "TTS service not fully configured."}}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "No JSON payload."}}), 400
    except Exception as e:
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"Invalid JSON: {e}"}}), 400

    text_to_synthesize = data.get("text")
    if not text_to_synthesize or not isinstance(text_to_synthesize, str) or not text_to_synthesize.strip():
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "'text' must be non-empty string."}}), 400

    TEXT_MAX_LENGTH = 5000 # Consider making this configurable
    if len(text_to_synthesize) > TEXT_MAX_LENGTH:
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"'text' exceeds max length {TEXT_MAX_LENGTH}."}}), 400

    voice_id = data.get("voice_id", AIMS_TTS_DEFAULT_VOICE_ID)
    language_code = data.get("language_code", AIMS_TTS_DEFAULT_LANGUAGE_CODE)
    output_format_str = data.get("audio_format", AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR).upper()
    speech_rate = float(data.get("speech_rate", AIMS_TTS_DEFAULT_SPEAKING_RATE))
    pitch = float(data.get("pitch", AIMS_TTS_DEFAULT_PITCH))

    # Basic validation for critical params before task dispatch
    if output_format_str not in AUDIO_ENCODING_MAP:
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"Unsupported audio_format. Supported: {list(AUDIO_ENCODING_MAP.keys())}"}}), 400
    speech_rate = max(0.25, min(speech_rate, 4.0))
    pitch = max(-20.0, min(pitch, 20.0))

    selected_audio_encoding_details = AUDIO_ENCODING_MAP[output_format_str]
    file_extension = output_format_str.lower()

    logger.info(f"Request {request_id}: Dispatching TTS synthesis to Celery task. Voice: {voice_id}")
    task = invoke_tts_google_task.delay(
        request_id=request_id,
        text_to_synthesize=text_to_synthesize,
        voice_id=voice_id,
        language_code=language_code,
        speech_rate=speech_rate,
        pitch=pitch,
        output_format_str=output_format_str,
        selected_audio_encoding_details=selected_audio_encoding_details,
        file_extension=file_extension
    )

    return jsonify({"task_id": task.id, "status_url": f"/v1/tasks/{task.id}"}), 202


@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    logger.info(f"Received request for task status: {task_id}")
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}
    if task_result.successful():
        response_data["result"] = task_result.result
        return jsonify(response_data), 200
    elif task_result.failed():
        error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
        response_data["result"] = error_info
        return jsonify(response_data), 500 # Or 200 if preferred
    else: # PENDING, STARTED, RETRY
        return jsonify(response_data), 202

if __name__ == '__main__':
    if not GOOGLE_APPLICATION_CREDENTIALS:
        logger.warning("WARNING: GOOGLE_APPLICATION_CREDENTIALS is not set. TTS & GCS calls might fail if ADC not configured.")
    if not GCS_BUCKET_NAME:
        logger.warning("WARNING: GCS_BUCKET_NAME is not set. Audio uploads will fail.")

    logger.info(f"--- AIMS_TTS Service starting on {AIMS_TTS_HOST}:{AIMS_TTS_PORT} (Debug: {FLASK_DEBUG}) ---")
    app.run(host=AIMS_TTS_HOST, port=AIMS_TTS_PORT, debug=FLASK_DEBUG)
