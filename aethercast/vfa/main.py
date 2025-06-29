import logging
import os
import uuid
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from typing import Optional, Dict, Any
import requests # Added for AIMS_TTS service call
import time # Added for retry logic (if needed for AIMS_TTS)
from celery import Celery
from celery.result import AsyncResult
import psycopg2 # For Idempotency DB
from psycopg2.extras import RealDictCursor # For Idempotency DB
from datetime import datetime, timezone # For Idempotency locked_at

# --- Load Environment Variables ---
load_dotenv()

# --- Global HTTP Session for AIMS_TTS calls ---
GLOBAL_REQUESTS_SESSION = requests.Session()

# --- Idempotency Constants ---
IDEMPOTENCY_KEY_HEADER = "X-Idempotency-Key" # Added

# --- Celery Configuration ---
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

celery_app = Celery(
    'vfa_tasks', # Unique name for VFA tasks
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

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="vfa"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

def setup_json_logging(flask_app):
    flask_app.logger.handlers.clear()
    logHandler = logging.StreamHandler()
    service_filter = ServiceNameFilter("vfa")
    logHandler.addFilter(service_filter)

    # Use JsonFormatter
    from python_json_logger import jsonlogger # Ensure import
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s %(task_id)s %(workflow_id)s %(idempotency_key)s %(topic)s %(script_id)s"
    )
    logHandler.setFormatter(formatter)

    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    # Add default extra fields for the initial log message
    flask_app.logger.info("JSON logging configured for VFA service.", extra={'task_id': 'N/A', 'workflow_id': 'N/A', 'idempotency_key': 'N/A', 'topic': 'N/A', 'script_id': 'N/A'})

setup_json_logging(app)
logger = app.logger # This logger is now JSON configured.

# --- VFA Configuration ---
vfa_config = {}

def load_vfa_configuration():
    global vfa_config
    vfa_config['AIMS_TTS_SERVICE_URL'] = os.getenv("AIMS_TTS_SERVICE_URL", "http://aims_tts_service:9000/v1/synthesize")
    vfa_config['AIMS_TTS_REQUEST_TIMEOUT_SECONDS'] = int(os.getenv("AIMS_TTS_REQUEST_TIMEOUT_SECONDS", "10")) # Timeout for initial task submission
    vfa_config['AIMS_TTS_POLLING_INTERVAL_SECONDS'] = int(os.getenv("AIMS_TTS_POLLING_INTERVAL_SECONDS", "3"))
    vfa_config['AIMS_TTS_POLLING_TIMEOUT_SECONDS'] = int(os.getenv("AIMS_TTS_POLLING_TIMEOUT_SECONDS", "180"))

    vfa_config['VFA_SHARED_AUDIO_DIR'] = os.getenv("VFA_SHARED_AUDIO_DIR", "/app/tests/output/vfa_generated_audio/")
    vfa_config['VFA_MIN_SCRIPT_LENGTH'] = int(os.getenv("VFA_MIN_SCRIPT_LENGTH", "20"))
    vfa_config['VFA_TEST_MODE_ENABLED'] = os.getenv("VFA_TEST_MODE_ENABLED", "False").lower() == 'true'
    vfa_config['VFA_HOST'] = os.getenv("VFA_HOST", "0.0.0.0")
    vfa_config['VFA_PORT'] = int(os.getenv("VFA_PORT", 5005))
    # vfa_config['VFA_DEBUG_MODE'] will be replaced by direct use of FLASK_DEBUG

    # Database Configuration (for Idempotency)
    vfa_config['POSTGRES_HOST'] = os.getenv("POSTGRES_HOST")
    vfa_config['POSTGRES_PORT'] = os.getenv("POSTGRES_PORT", "5432")
    vfa_config['POSTGRES_USER'] = os.getenv("POSTGRES_USER")
    vfa_config['POSTGRES_PASSWORD'] = os.getenv("POSTGRES_PASSWORD")
    vfa_config['POSTGRES_DB'] = os.getenv("POSTGRES_DB")
    vfa_config['VFA_POSTGRES_DB_URL'] = os.getenv("VFA_POSTGRES_DB_URL") # Load new consolidated URL

    # Idempotency Configuration from .env.example
    vfa_config['IDEMPOTENCY_LOCK_TIMEOUT_SECONDS'] = int(os.getenv("VFA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS", "300"))
    vfa_config['IDEMPOTENCY_STATUS_PROCESSING'] = os.getenv("VFA_IDEMPOTENCY_STATUS_PROCESSING", "processing")
    vfa_config['IDEMPOTENCY_STATUS_COMPLETED'] = os.getenv("VFA_IDEMPOTENCY_STATUS_COMPLETED", "completed")
    vfa_config['IDEMPOTENCY_STATUS_FAILED'] = os.getenv("VFA_IDEMPOTENCY_STATUS_FAILED", "failed")

    logger.info("--- VFA Configuration (AIMS_TTS Client) ---")
    for key, value in vfa_config.items(): logger.info(f"  {key}: {value}")
    logger.info("--- End VFA Configuration ---")

    if not vfa_config.get('AIMS_TTS_SERVICE_URL') and not vfa_config.get('VFA_TEST_MODE_ENABLED'):
        error_msg = "CRITICAL: AIMS_TTS_SERVICE_URL is not set and VFA is not in test mode."
        logger.critical(error_msg)
        raise ValueError(error_msg)

load_vfa_configuration()

VFA_TEST_SCENARIO_AIMS_TTS_ERROR_MSG = "Test scenario: Simulated AIMS_TTS service error."
VFA_TEST_SCENARIO_FILE_SAVE_ERROR_MSG = "Test scenario: Simulated file saving IO error in VFA."
PSWA_ERROR_PREFIXES = ("OpenAI library not available", "Error: OPENAI_API_KEY", "[ERROR] Insufficient content")
VFA_STATUS_NOT_RUN = "not_run" # Added from CPOA
VFA_STATUS_SUCCESS = "success" # Added from CPOA
VFA_STATUS_SKIPPED = "skipped" # Added from CPOA
VFA_STATUS_ERROR = "error" # Added from CPOA

# --- Idempotency DB Helpers (VFA specific) ---
def _get_vfa_db_connection():
    """Establishes a direct connection to PostgreSQL for VFA idempotency checks using consolidated URL."""
    db_url = vfa_config.get('VFA_POSTGRES_DB_URL')
    if not db_url:
        # Fallback to individual components if consolidated URL is not set (for backward compatibility during transition)
        # However, ideally, the service should rely on the consolidated URL.
        logger.warning("VFA: VFA_POSTGRES_DB_URL not set. Attempting to use individual PostgreSQL components.")
        required_vars = [vfa_config.get('POSTGRES_HOST'), vfa_config.get('POSTGRES_USER'), vfa_config.get('POSTGRES_PASSWORD'), vfa_config.get('POSTGRES_DB')]
        if not all(required_vars):
            logger.error("VFA: PostgreSQL individual connection variables for idempotency not fully set in vfa_config.")
            raise ConnectionError("VFA: PostgreSQL environment variables for idempotency not configured.")
        try:
            conn = psycopg2.connect(
                host=vfa_config['POSTGRES_HOST'], port=vfa_config['POSTGRES_PORT'],
                user=vfa_config['POSTGRES_USER'], password=vfa_config['POSTGRES_PASSWORD'],
                dbname=vfa_config['POSTGRES_DB'],
                cursor_factory=RealDictCursor
            )
            logger.info("VFA successfully connected to PostgreSQL for idempotency using individual components.")
            return conn
        except psycopg2.Error as e:
            logger.error(f"VFA: Unable to connect to PostgreSQL using individual components: {e}", exc_info=True)
            raise ConnectionError(f"VFA: PostgreSQL connection failed (individual components): {e}") from e

    try: # Try with consolidated URL first
        conn = psycopg2.connect(dsn=db_url, cursor_factory=RealDictCursor)
        logger.info("VFA successfully connected to PostgreSQL for idempotency using VFA_POSTGRES_DB_URL.")
        return conn
    except psycopg2.Error as e:
        logger.error(f"VFA: Unable to connect to PostgreSQL for idempotency: {e}", exc_info=True)
        raise ConnectionError(f"VFA: PostgreSQL connection for idempotency failed: {e}") from e

# --- Custom Celery Task Class for VFA with Idempotency ---
class VfaCeleryTask(celery_app.Task): # Inherit from celery_app.Task
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f'Celery Task {task_id} (VFA ForgeVoice) failed: {exc}', exc_info=einfo)
        idempotency_key = kwargs.get('idempotency_key')
        workflow_id = kwargs.get('workflow_id') # Retrieve workflow_id
        task_name = self.name # self.name will be 'forge_voice_task'

        # Check for PSYCOPG2_AVAILABLE - defined globally in main.py
        # For simplicity, assuming it's accessible here or rely on _get_vfa_db_connection to check/raise.
        # A more explicit check: if not vfa_main.PSYCOPG2_AVAILABLE: logger.error(...); return

        if idempotency_key: # Attempt to mark idempotency record as failed if key is present
            db_conn = None
            try:
                db_conn = _get_vfa_db_connection()
                if db_conn:
                    db_conn.autocommit = False # Manage transaction
                    error_payload = {"error_type": type(exc).__name__, "error_message": str(exc), "traceback": str(einfo)}
                    # Use the correct config key for failed status
                    _store_vfa_idempotency_result(db_conn, idempotency_key, task_name,
                                              vfa_config['IDEMPOTENCY_STATUS_FAILED'], # Use config
                                              error_payload=error_payload,
                                              workflow_id=workflow_id, # Pass workflow_id
                                              is_new_key=False) # Should exist if task started
                    db_conn.commit()
                    logger.info(f"Idempotency record for key {idempotency_key} marked as FAILED for VFA task {task_name}.")
            except Exception as db_err:
                logger.error(f"Failed to update idempotency record to FAILED for key {idempotency_key} (VFA task {task_name}) after task failure: {db_err}", exc_info=True)
                if db_conn: db_conn.rollback()
            finally:
                if db_conn and not db_conn.closed:
                    try: db_conn.close()
                    except Exception: pass # Ignore errors on close during failure handling
        # Default Celery failure handling will still occur

def _check_vfa_idempotency_key(db_conn, idempotency_key: str, task_name: str) -> Optional[Dict[str, Any]]:
    logger_extra_info = {"task_id": "VFAIdempotencyCheck", "idempotency_key": idempotency_key, "check_task_name": task_name}
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT idempotency_key, task_name, workflow_id, created_at, locked_at, status, result_payload, error_payload FROM idempotency_keys WHERE idempotency_key = %s AND task_name = %s",
                (idempotency_key, task_name)
            )
            record = cur.fetchone()
            if record:
                logger.info(f"Idempotency key found with status '{record['status']}'.", extra=logger_extra_info)
                return dict(record)
            logger.info("No existing idempotency key found.", extra=logger_extra_info)
            return None
    except psycopg2.Error as e:
        logger.error(f"DB error checking idempotency key: {e}", exc_info=True, extra=logger_extra_info)
        raise
    except Exception as e_unexp:
        logger.error(f"Unexpected error checking idempotency key: {e_unexp}", exc_info=True, extra=logger_extra_info)
        raise

def _store_vfa_idempotency_result(db_conn, idempotency_key: str, task_name: str, status: str, result_payload: Optional[dict] = None, error_payload: Optional[dict] = None, workflow_id: Optional[str] = None, is_new_key: bool = True):
    logger_extra_info = {"task_id": "VFAIdempotencyStore", "idempotency_key": idempotency_key, "store_task_name": task_name, "new_status": status}
    try:
        with db_conn.cursor() as cur:
            current_ts_utc = datetime.now(timezone.utc)
            if is_new_key:
                logger.info("Storing new idempotency key.", extra=logger_extra_info)
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
                    (idempotency_key, task_name, workflow_id,
                     current_ts_utc if status == vfa_config['IDEMPOTENCY_STATUS_PROCESSING'] else None,
                     status, json.dumps(result_payload) if result_payload else None,
                     json.dumps(error_payload) if error_payload else None, current_ts_utc)
                )
            else: # Update existing key
                logger.info("Updating existing idempotency key.", extra=logger_extra_info)
                set_clauses = ["status = %s", "result_payload = %s", "error_payload = %s"]
                params = [status, json.dumps(result_payload) if result_payload else None, json.dumps(error_payload) if error_payload else None]

                if status == vfa_config['IDEMPOTENCY_STATUS_PROCESSING']:
                    set_clauses.append("locked_at = %s")
                    params.append(current_ts_utc)
                elif status in [vfa_config['IDEMPOTENCY_STATUS_COMPLETED'], vfa_config['IDEMPOTENCY_STATUS_FAILED']]:
                    set_clauses.append("locked_at = NULL")

                params.extend([idempotency_key, task_name])
                cur.execute(
                    f"UPDATE idempotency_keys SET {', '.join(set_clauses)} WHERE idempotency_key = %s AND task_name = %s;",
                    tuple(params)
                )
            logger.info("Successfully stored/updated idempotency key.", extra=logger_extra_info)
    except psycopg2.Error as e:
        logger.error(f"DB error storing idempotency key: {e}", exc_info=True, extra=logger_extra_info)
        raise
    except Exception as e_unexp:
        logger.error(f"Unexpected error storing idempotency key: {e_unexp}", exc_info=True, extra=logger_extra_info)
        raise


@celery_app.task(bind=True, base=VfaCeleryTask, name='forge_voice_task') # Use VfaCeleryTask as base
def forge_voice_task(self, request_id_celery: str, script_input: dict, voice_params_input: Optional[dict] = None, test_scenario_header: Optional[str] = None, idempotency_key: Optional[str] = None, workflow_id: Optional[str] = None) -> dict:
    task_celery_id = self.request.id
    log_extra_base = {
        "orig_req_id": request_id_celery,
        "task_id": task_celery_id,
        "idempotency_key": idempotency_key,
        "workflow_id": workflow_id,
        "topic": script_input.get('topic', 'N/A'),
        "script_id": script_input.get('script_id', 'N/A')
    }
    logger.info(f"Starting voice forging.", extra=log_extra_base)

    stream_id = f"strm_{task_celery_id}" # Use task_celery_id for stream_id
    original_topic = script_input.get("topic", "Unknown Topic") # Keep for internal logic if needed
    voice_params_input = voice_params_input or {}

    requested_tts_settings = {
        "voice_id": voice_params_input.get("voice_name"),
        "audio_format": voice_params_input.get("audio_encoding"),
        "speech_rate": voice_params_input.get("speaking_rate"),
        "pitch": voice_params_input.get("pitch"),
    }
    requested_tts_settings = {k: v for k, v in requested_tts_settings.items() if v is not None}

    if vfa_config.get('VFA_TEST_MODE_ENABLED'):
        scenario = test_scenario_header if test_scenario_header else 'default'
        logger.info(f"Celery Task {self.request.id}: Test mode enabled. Scenario: '{scenario}' for stream {stream_id}, topic '{original_topic}'.")
        simulated_audio_format = requested_tts_settings.get("audio_format", "mp3")
        simulated_voice_id = requested_tts_settings.get("voice_id", "test-mode-voice")
        script_char_count = len(str(script_input))

        if scenario == 'vfa_error_aims_tts':
            return {"error_code": "VFA_TEST_MODE_AIMS_TTS_ERROR", "message": VFA_TEST_SCENARIO_AIMS_TTS_ERROR_MSG, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}

        shared_audio_dir = vfa_config.get('VFA_SHARED_AUDIO_DIR')
        try:
            os.makedirs(shared_audio_dir, exist_ok=True)
            dummy_filename = f"vfa_test_{stream_id}.{simulated_audio_format.lower()}"
            dummy_filepath = os.path.join(shared_audio_dir, dummy_filename)
            if scenario == 'vfa_error_file_save':
                raise IOError(VFA_TEST_SCENARIO_FILE_SAVE_ERROR_MSG) # Simulate IO Error
            with open(dummy_filepath, "wb") as f: f.write(b"dummy audio data")
            return {"status": VFA_STATUS_SUCCESS, "message": "Audio successfully synthesized (VFA TEST MODE - dummy file).",
                    "audio_filepath": dummy_filepath, "stream_id": stream_id, "audio_format": simulated_audio_format.lower(),
                    "script_char_count": script_char_count, "engine_used": "test_mode", "tts_settings_used": requested_tts_settings}
        except IOError as e:
            logger.error(f"Celery Task {self.request.id}: Test mode IO error: {e}", exc_info=True)
            raise Exception(f"Test mode IO error: {e}") # Let Celery mark as FAILED

    # --- Real AIMS_TTS Call Logic (within Celery task) ---
    text_to_synthesize = ""
    voice_params_input = voice_params_input or {}
    # Define task name for idempotency. It's better if this matches self.name if available,
    # but using a fixed string is also okay if the task name in Celery decorator is stable.
    vfa_task_name_for_idempotency = self.name # "forge_voice_task"

    if not idempotency_key:
        # Use log_extra_base which is already defined with all context fields
        logger.error(f"Idempotency key not provided by CPOA. This is required.", extra=log_extra_base)
        raise ValueError("Idempotency key is required for VFA task execution.")

    # PSYCOPG2_AVAILABLE check was removed in previous refactors as direct import implies availability.
    # If direct import fails, service won't start. If it's uninstalled while running, _get_vfa_db_connection will fail.
    # if not PSYCOPG2_AVAILABLE:
    #     logger.error(f"psycopg2 not available, cannot perform idempotency checks. Failing task.", extra=log_extra_base)
    #     raise ConnectionError("VFA Task: psycopg2 is required for idempotency but not available.")

    # Initial log message already uses log_extra_base.
    # The message "Starting voice forging." is general. Specific details like topic are in log_extra_base.
    self.update_state(state='PENDING', meta={'message': 'Initiated, checking idempotency.'})

    db_conn = None
    try:
        db_conn = _get_vfa_db_connection()
        db_conn.autocommit = False

        existing_record = _check_vfa_idempotency_key(db_conn, idempotency_key, vfa_task_name_for_idempotency)
        if existing_record:
            status = existing_record['status']
            locked_at = existing_record['locked_at']
            if status == vfa_config['IDEMPOTENCY_STATUS_COMPLETED']:
                logger.info(f"Idempotency: Found completed record for key '{idempotency_key}'. Returning stored result.", extra={"orig_req_id": request_id_celery})
                db_conn.rollback()
                return existing_record['result_payload']
            elif status == vfa_config['IDEMPOTENCY_STATUS_PROCESSING']:
                if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < vfa_config['IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']:
                    logger.warning(f"Idempotency: Key '{idempotency_key}' is already processing. Returning conflict.", extra={"orig_req_id": request_id_celery, "workflow_id": workflow_id})
                    db_conn.rollback()
                    return {"status": "PROCESSING_CONFLICT", "message": "Task with this idempotency key is already processing.", "idempotency_key": idempotency_key}
                else:
                    logger.warning(f"Idempotency: Key '{idempotency_key}' was 'processing' but lock timed out. Re-processing.", extra={"orig_req_id": request_id_celery, "workflow_id": workflow_id})
                    _store_vfa_idempotency_result(db_conn, idempotency_key, vfa_task_name_for_idempotency, vfa_config['IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
            elif status == vfa_config['IDEMPOTENCY_STATUS_FAILED']:
                logger.info(f"Idempotency: Key '{idempotency_key}' previously failed. Retrying.", extra={"orig_req_id": request_id_celery, "workflow_id": workflow_id})
                _store_vfa_idempotency_result(db_conn, idempotency_key, vfa_task_name_for_idempotency, vfa_config['IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
        else:
            _store_vfa_idempotency_result(db_conn, idempotency_key, vfa_task_name_for_idempotency, vfa_config['IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=True)
        db_conn.commit()
        self.update_state(state='PROGRESS', meta={'message': 'Idempotency check passed. Starting main logic.'})

        # --- Original Task Logic (after idempotency check) ---
        requested_tts_settings = {k: v for k, v in {"voice_id": voice_params_input.get("voice_name"), "audio_format": voice_params_input.get("audio_encoding"), "speech_rate": voice_params_input.get("speaking_rate"), "pitch": voice_params_input.get("pitch")}.items() if v is not None}

        if vfa_config.get('VFA_TEST_MODE_ENABLED'):
            # ... (existing test mode logic - kept for brevity, ensure it returns a dict that can be stored as result_payload)
            # Example success:
            sim_result = {"status": VFA_STATUS_SUCCESS, "message": "Audio successfully synthesized (VFA TEST MODE - dummy file).", "audio_filepath": "/dummy/path.mp3", "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
            _store_vfa_idempotency_result(db_conn, idempotency_key, vfa_task_name_for_idempotency, vfa_config['IDEMPOTENCY_STATUS_COMPLETED'], result_payload=sim_result, workflow_id=workflow_id, is_new_key=False)
            db_conn.commit()
            return sim_result
            # Example error:
            # test_error_payload = {"error_code": "VFA_TEST_MODE_AIMS_TTS_ERROR", "message": VFA_TEST_SCENARIO_AIMS_TTS_ERROR_MSG}
            # _store_vfa_idempotency_result(db_conn, idempotency_key, vfa_task_name_for_idempotency, vfa_config['IDEMPOTENCY_STATUS_FAILED'], error_payload=test_error_payload, workflow_id=workflow_id, is_new_key=False)
            # db_conn.commit()
            # return test_error_payload


        if not isinstance(script_input, dict): text_to_synthesize = str(script_input)
        else:
            full_raw_script = script_input.get("full_raw_script", "")
            if any(full_raw_script.startswith(prefix) for prefix in PSWA_ERROR_PREFIXES):
                vfa_skip_result = {"status": VFA_STATUS_SKIPPED, "message": "PSWA script error, TTS skipped.", "audio_filepath": None, "stream_id": stream_id, "tts_settings_used": None}
                _store_vfa_idempotency_result(db_conn, idempotency_key, vfa_task_name_for_idempotency, vfa_config['IDEMPOTENCY_STATUS_COMPLETED'], result_payload=vfa_skip_result, workflow_id=workflow_id, is_new_key=False)
                db_conn.commit()
                return vfa_skip_result
            segments = script_input.get("segments", [])
            if segments:
                tts_parts = []; title = script_input.get("title", original_topic)
                if title and not title.startswith("Error: Insufficient Content"): tts_parts.append(f"{title}.")
                for segment in segments:
                    seg_title = segment.get("segment_title", ""); seg_content = segment.get("content", "")
                    if seg_title and seg_title not in ["INTRO", "OUTRO", "ERROR"]: tts_parts.append(f"{seg_title}.")
                    if seg_content: tts_parts.append(seg_content)
                text_to_synthesize = "\n\n".join(tts_parts)
            elif full_raw_script: text_to_synthesize = full_raw_script
            else:
                no_text_error = {"error_code": "VFA_SCRIPT_ERROR_NO_TEXT", "message": "Script has no usable text.", "stream_id": stream_id, "tts_settings_used": None}
                _store_vfa_idempotency_result(db_conn, idempotency_key, vfa_task_name_for_idempotency, vfa_config['IDEMPOTENCY_STATUS_FAILED'], error_payload=no_text_error, workflow_id=workflow_id, is_new_key=False)
                db_conn.commit()
                return no_text_error


        synthesized_char_count = len(text_to_synthesize)
        if synthesized_char_count < vfa_config.get('VFA_MIN_SCRIPT_LENGTH', 20):
            too_short_result = {"status": VFA_STATUS_SKIPPED, "message": f"Text too short ({synthesized_char_count} chars), TTS skipped.", "audio_filepath": None, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
            _store_vfa_idempotency_result(db_conn, idempotency_key, vfa_task_name_for_idempotency, vfa_config['IDEMPOTENCY_STATUS_COMPLETED'], result_payload=too_short_result, workflow_id=workflow_id, is_new_key=False)
            db_conn.commit()
            return too_short_result

        aims_tts_payload = {"text": text_to_synthesize, **requested_tts_settings}
        aims_tts_url = vfa_config['AIMS_TTS_SERVICE_URL']
        aims_tts_initial_request_timeout = vfa_config.get('AIMS_TTS_REQUEST_TIMEOUT_SECONDS')
        polling_interval = vfa_config.get('AIMS_TTS_POLLING_INTERVAL_SECONDS')
        polling_timeout = vfa_config.get('AIMS_TTS_POLLING_TIMEOUT_SECONDS')

        logger.info(f"Celery Task {self.request.id}: Sending initial request to AIMS_TTS. URL: {aims_tts_url}", extra={"orig_req_id": request_id_celery})
        # ... (AIMS_TTS call and polling logic as before) ...
        # --- Start of AIMS_TTS interaction (copied and adapted from original) ---
        initial_response = GLOBAL_REQUESTS_SESSION.post(aims_tts_url, json=aims_tts_payload, timeout=aims_tts_initial_request_timeout)
        initial_response.raise_for_status()
        if initial_response.status_code != 202:
            raise Exception(f"AIMS_TTS task not accepted: {initial_response.status_code} - {initial_response.text}")
        aims_tts_task_init_data = initial_response.json()
        task_id_from_aims_tts = aims_tts_task_init_data.get("task_id")
        status_url_suffix = aims_tts_task_init_data.get("status_url")
        if not task_id_from_aims_tts or not status_url_suffix:
            raise ValueError(f"AIMS_TTS task submission response invalid: {aims_tts_task_init_data}")
        aims_tts_base_url = '/'.join(aims_tts_url.split('/')[:-2])
        poll_status_url = f"{aims_tts_base_url}{status_url_suffix}"
        polling_start_time = time.time()
        aims_tts_data = None
        while True:
            if time.time() - polling_start_time > polling_timeout:
                raise Exception(f"Polling AIMS_TTS task {task_id_from_aims_tts} timed out.")
            try:
                    poll_response = GLOBAL_REQUESTS_SESSION.get(poll_status_url, timeout=10)
                poll_response.raise_for_status(); task_status_data = poll_response.json(); task_state = task_status_data.get("status")
                if task_state == "SUCCESS":
                    aims_tts_data = task_status_data.get("result")
                    if not aims_tts_data or not aims_tts_data.get("audio_url"): raise ValueError(f"AIMS_TTS result invalid: {task_status_data}")
                    break
                elif task_state == "FAILURE": raise Exception(f"AIMS_TTS task failed: {task_status_data.get('result', {}).get('error', {}).get('message', 'Unknown')}")
                time.sleep(polling_interval)
            except requests.exceptions.RequestException as e_poll: logger.warning(f"Polling AIMS_TTS task {task_id_from_aims_tts} failed: {e_poll}. Retrying.", extra={"orig_req_id": request_id_celery}); time.sleep(polling_interval)
        # --- End of AIMS_TTS interaction ---

        final_tts_settings_used = {k:v for k,v in {"voice_name": aims_tts_data.get("voice_id", requested_tts_settings.get("voice_id")), "audio_encoding": aims_tts_data.get("audio_format", requested_tts_settings.get("audio_format")), "speaking_rate": requested_tts_settings.get("speech_rate"), "pitch": requested_tts_settings.get("pitch")}.items() if v is not None}
        vfa_success_payload = {"status": VFA_STATUS_SUCCESS, "message": "Audio successfully synthesized via AIMS_TTS (async).", "audio_filepath": aims_tts_data.get("audio_url"), "stream_id": stream_id, "audio_format": aims_tts_data.get("audio_format", "unknown").lower(), "script_char_count": synthesized_char_count, "engine_used": f"aims_tts_via_{aims_tts_data.get('voice_id', 'unknown_voice')}", "tts_settings_used": final_tts_settings_used}

        _store_vfa_idempotency_result(db_conn, idempotency_key, vfa_task_name_for_idempotency, vfa_config['IDEMPOTENCY_STATUS_COMPLETED'], result_payload=vfa_success_payload, workflow_id=workflow_id, is_new_key=False)
        db_conn.commit()
        self.update_state(state='SUCCESS', meta=vfa_success_payload)
        return vfa_success_payload

    except Exception as e:
        logger.error(f"Celery Task {self.request.id} (Idempotency Key: {idempotency_key}): Error in forge_voice_task: {e}", exc_info=True, extra={"orig_req_id": request_id_celery, "workflow_id": workflow_id})
        # The on_failure handler will now manage updating the idempotency record.
        # Re-raise the exception so Celery calls on_failure.
        # If using self.retry, on_failure is only called if retries are exhausted or it's not a retryable exception.
        # For simplicity here, just re-raise. If specific retry logic for certain exceptions is needed
        # before marking as FAILED, that would be more complex.
        raise # This will trigger VfaCeleryTask.on_failure
    finally:
        if db_conn:
            try:
                if not db_conn.closed: db_conn.close()
            except Exception as e_close: logger.error(f"Error closing VFA DB connection: {e_close}", exc_info=True, extra={"orig_req_id": request_id_celery})


@app.route('/v1/forge_voice', methods=['POST'])
def handle_forge_voice_async():
    request_id_main = f"vfa_req_{uuid.uuid4().hex[:8]}"
    idempotency_key_header = request.headers.get('X-Idempotency-Key')
    workflow_id_header = request.headers.get('X-Workflow-ID') # Extract Workflow ID
    logger.info(f"Request {request_id_main}: Received async /v1/forge_voice request. X-Idempotency-Key: {idempotency_key_header}, X-Workflow-ID: {workflow_id_header}")

    try:
        data = request.get_json()
        if not data: return jsonify({"error_code": "VFA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json: return jsonify({"error_code": "VFA_MALFORMED_JSON", "message": f"Malformed JSON: {str(e_json)}"}), 400

    script_payload = data.get('script')
    voice_params_payload = data.get('voice_params')
    test_scenario_header = request.headers.get('X-Test-Scenario')

    if not isinstance(script_payload, dict) or not script_payload.get("script_id"):
        return jsonify({"error_code": "VFA_INVALID_SCRIPT_PAYLOAD", "message": "Valid 'script' object with 'script_id' is required."}), 400
    if voice_params_payload is not None and not isinstance(voice_params_payload, dict):
        return jsonify({"error_code": "VFA_INVALID_VOICE_PARAMS_TYPE", "message": "'voice_params' must be an object if provided."}), 400

    if not idempotency_key_header:
        logger.warning(f"Request {request_id_main}: X-Idempotency-Key header is missing. This is required.")
        return jsonify({"error_code": "VFA_MISSING_IDEMPOTENCY_KEY", "message": "X-Idempotency-Key header is required."}), 400

    # --- Idempotency Pre-check at Endpoint Level ---
    idem_task_name_for_db = 'forge_voice_task' # Matches Celery task name
    db_conn_http = None
    # Assuming PSYCOPG2_AVAILABLE is defined (it is, based on imports)
    # For VFA, psycopg2 is imported directly, so we can assume it's available if no import error.
    # A more robust check would be `if 'psycopg2' in sys.modules:` or a PSYCOPG2_AVAILABLE flag if set.
    # For now, let's assume it's available if the service starts and _get_vfa_db_connection handles it.
    try:
        db_conn_http = _get_vfa_db_connection()
        db_conn_http.autocommit = False # Manage transaction for pre-check

        existing_record = _check_vfa_idempotency_key(db_conn_http, idempotency_key_header, idem_task_name_for_db)
        if existing_record:
            status = existing_record['status']
            locked_at = existing_record.get('locked_at')
            lock_timeout = vfa_config['IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']

            if status == vfa_config['IDEMPOTENCY_STATUS_COMPLETED']:
                logger.info(f"VFA Request {request_id_main}: Idempotency key '{idempotency_key_header}' already COMPLETED. Returning stored result.", extra={'workflow_id': workflow_id_header})
                db_conn_http.rollback()
                return jsonify(existing_record['result_payload']), 200
            elif status == vfa_config['IDEMPOTENCY_STATUS_PROCESSING']:
                if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < lock_timeout:
                    logger.warning(f"VFA Request {request_id_main}: Idempotency key '{idempotency_key_header}' is PROCESSING. Returning conflict.", extra={'workflow_id': workflow_id_header})
                    db_conn_http.rollback()
                    return jsonify({"error_code": "VFA_IDEMPOTENCY_CONFLICT", "message": "Request with this idempotency key is currently processing."}), 409
                else: # Lock expired
                    logger.info(f"VFA Request {request_id_main}: Idempotency key '{idempotency_key_header}' was PROCESSING but lock expired. Re-processing.", extra={'workflow_id': workflow_id_header})
                    _store_vfa_idempotency_result(db_conn_http, idempotency_key_header, idem_task_name_for_db, vfa_config['IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id_header, is_new_key=False)
                    db_conn_http.commit()
            elif status == vfa_config['IDEMPOTENCY_STATUS_FAILED']:
                logger.info(f"VFA Request {request_id_main}: Idempotency key '{idempotency_key_header}' previously FAILED. Re-processing.", extra={'workflow_id': workflow_id_header})
                _store_vfa_idempotency_result(db_conn_http, idempotency_key_header, idem_task_name_for_db, vfa_config['IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id_header, is_new_key=False)
                db_conn_http.commit()
        else: # No existing record
            logger.info(f"VFA Request {request_id_main}: New idempotency key '{idempotency_key_header}'. Storing as PROCESSING.", extra={'workflow_id': workflow_id_header})
            _store_vfa_idempotency_result(db_conn_http, idempotency_key_header, idem_task_name_for_db, vfa_config['IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id_header, is_new_key=True)
            db_conn_http.commit()
    except psycopg2.Error as db_err_http: # More specific error catch
        logger.error(f"VFA Request {request_id_main}: Database error during HTTP idempotency pre-check: {db_err_http}", exc_info=True, extra={'workflow_id': workflow_id_header})
        if db_conn_http: db_conn_http.rollback()
        logger.warning(f"VFA Request {request_id_main}: Proceeding to Celery dispatch despite DB error in pre-check.")
    except Exception as e_idem_http: # Catch any other error during pre-check
        logger.error(f"VFA Request {request_id_main}: Unexpected error during HTTP idempotency pre-check: {e_idem_http}", exc_info=True, extra={'workflow_id': workflow_id_header})
        if db_conn_http: db_conn_http.rollback()
        logger.warning(f"VFA Request {request_id_main}: Proceeding to Celery dispatch despite unexpected error in pre-check.")
    finally:
        if db_conn_http and not db_conn_http.closed:
            db_conn_http.close()
    # Continue to dispatch Celery task

    logger.info(f"Request {request_id_main}: Dispatching forge_voice task. Topic: '{script_payload.get('topic', 'N/A')}', Idempotency Key: {idempotency_key_header}, Workflow ID: {workflow_id_header}")
    task = forge_voice_task.delay(
        request_id_celery=request_id_main,
        script_input=script_payload,
        voice_params_input=voice_params_payload,
        test_scenario_header=test_scenario_header,
        idempotency_key=idempotency_key_header,
        workflow_id=workflow_id_header # Pass workflow_id
    )
    return jsonify({"task_id": task.id, "status_url": f"/v1/tasks/{task.id}", "message": "Voice forging task accepted.", "idempotency_key_processed": idempotency_key_header, "workflow_id_processed": workflow_id_header}), 202

@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_vfa_task_status(task_id: str):
    logger.info(f"Received request for VFA task status: {task_id}")
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}

    if task_result.successful():
        task_output = task_result.result
        response_data["result"] = task_output
        http_status = 200
        if isinstance(task_output, dict) and task_output.get("error_code"):
            http_status = 500 # Internal error from task logic
        elif isinstance(task_output, dict) and task_output.get("status") == VFA_STATUS_SKIPPED:
            http_status = 200 # Skipped is a valid outcome
        elif isinstance(task_output, dict) and task_output.get("status") == "PROCESSING_CONFLICT": # Idempotency conflict
            http_status = 409 # Conflict
        return jsonify(response_data), http_status
    elif task_result.failed():
        error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
        response_data["result"] = error_info
        return jsonify(response_data), 500
    else: # PENDING, STARTED, RETRY
        return jsonify(response_data), 202

if __name__ == '__main__':
    host = vfa_config.get("VFA_HOST", "0.0.0.0")
    port = vfa_config.get("VFA_PORT", 5005)
    # Read FLASK_DEBUG directly for running the app
    flask_debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == 'true'
    logger.info(f"--- VFA Service (AIMS_TTS Client & Celery Producer) starting on {host}:{port} (Debug: {flask_debug_mode}) ---")
    if not vfa_config.get("AIMS_TTS_SERVICE_URL") and not vfa_config.get("VFA_TEST_MODE_ENABLED"):
        logger.critical("CRITICAL ERROR: AIMS_TTS_SERVICE_URL is not set and VFA is not in test mode. VFA will not function correctly.")
    app.run(host=host, port=port, debug=flask_debug_mode)
