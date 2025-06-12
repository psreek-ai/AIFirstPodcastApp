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

# --- Load Environment Variables ---
load_dotenv()

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
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s"
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("Standard logging configured for VFA service.")

setup_json_logging(app)
logger = app.logger

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
    vfa_config['VFA_DEBUG_MODE'] = os.getenv("VFA_DEBUG_MODE", "True").lower() == "true"

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


@celery_app.task(bind=True, name='forge_voice_task')
def forge_voice_task(self, request_id_celery: str, script_input: dict, voice_params_input: Optional[dict] = None, test_scenario_header: Optional[str] = None) -> dict:
    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id_celery}): Starting voice forging. Topic: {script_input.get('topic', 'N/A')}")
    stream_id = f"strm_{self.request.id}"
    original_topic = script_input.get("topic", "Unknown Topic")
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
    # ... (logic to extract text_to_synthesize from script_input - kept same as original forge_voice) ...
    if not isinstance(script_input, dict): text_to_synthesize = str(script_input)
    else:
        full_raw_script = script_input.get("full_raw_script", "")
        if any(full_raw_script.startswith(prefix) for prefix in PSWA_ERROR_PREFIXES):
            return {"status": VFA_STATUS_SKIPPED, "message": "PSWA script error, TTS skipped.", "audio_filepath": None, "stream_id": stream_id, "tts_settings_used": None}
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
        else: return {"error_code": "VFA_SCRIPT_ERROR_NO_TEXT", "message": "Script has no usable text.", "stream_id": stream_id, "tts_settings_used": None}

    synthesized_char_count = len(text_to_synthesize)
    if synthesized_char_count < vfa_config.get('VFA_MIN_SCRIPT_LENGTH', 20):
        return {"status": VFA_STATUS_SKIPPED, "message": f"Text too short ({synthesized_char_count} chars), TTS skipped.", "audio_filepath": None, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}

    aims_tts_payload = {"text": text_to_synthesize, **requested_tts_settings}
    aims_tts_url = vfa_config['AIMS_TTS_SERVICE_URL']
    aims_tts_initial_request_timeout = vfa_config.get('AIMS_TTS_REQUEST_TIMEOUT_SECONDS')
    polling_interval = vfa_config.get('AIMS_TTS_POLLING_INTERVAL_SECONDS')
    polling_timeout = vfa_config.get('AIMS_TTS_POLLING_TIMEOUT_SECONDS')
    
    logger.info(f"Celery Task {self.request.id}: Sending initial request to AIMS_TTS. URL: {aims_tts_url}")
    aims_tts_task_submission_start_time = time.time()

    try:
        initial_response = requests.post(aims_tts_url, json=aims_tts_payload, timeout=aims_tts_initial_request_timeout)
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
        logger.info(f"Celery Task {self.request.id}: AIMS_TTS task {task_id_from_aims_tts} submitted. Polling: {poll_status_url}")

        polling_start_time = time.time()
        aims_tts_data = None
        while True:
            if time.time() - polling_start_time > polling_timeout:
                raise Exception(f"Polling AIMS_TTS task {task_id_from_aims_tts} timed out.")
            try:
                poll_response = requests.get(poll_status_url, timeout=10)
                poll_response.raise_for_status()
                task_status_data = poll_response.json()
                task_state = task_status_data.get("status")
                logger.info(f"Celery Task {self.request.id}: AIMS_TTS task {task_id_from_aims_tts} status: {task_state}")
                if task_state == "SUCCESS":
                    aims_tts_data = task_status_data.get("result")
                    if not aims_tts_data or not aims_tts_data.get("audio_url"):
                        raise ValueError(f"AIMS_TTS task {task_id_from_aims_tts} result invalid: {task_status_data}")
                    break
                elif task_state == "FAILURE":
                    task_error_details = task_status_data.get("result", {}).get("error", {})
                    raise Exception(f"AIMS_TTS task failed: {task_error_details.get('message', 'Unknown AIMS_TTS task error')}")
                time.sleep(polling_interval)
            except requests.exceptions.RequestException as e_poll:
                logger.warning(f"Celery Task {self.request.id}: Polling AIMS_TTS task {task_id_from_aims_tts} failed: {e_poll}. Retrying.")
                time.sleep(polling_interval)

        total_duration_ms = (time.time() - aims_tts_task_submission_start_time) * 1000
        logger.info(f"VFA AIMS_TTS interaction processed (async polling via Celery)", extra=dict(metric_name="vfa_aims_tts_total_duration_ms", value=round(total_duration_ms, 2)))

        final_tts_settings_used = {
            "voice_name": aims_tts_data.get("voice_id", requested_tts_settings.get("voice_id")),
            "audio_encoding": aims_tts_data.get("audio_format", requested_tts_settings.get("audio_format")),
            "speaking_rate": requested_tts_settings.get("speech_rate"), "pitch": requested_tts_settings.get("pitch")
        }
        final_tts_settings_used = {k:v for k,v in final_tts_settings_used.items() if v is not None}

        return {"status": VFA_STATUS_SUCCESS, "message": "Audio successfully synthesized via AIMS_TTS (async).",
                "audio_filepath": aims_tts_data.get("audio_url"), "stream_id": stream_id,
                "audio_format": aims_tts_data.get("audio_format", "unknown").lower(),
                "script_char_count": synthesized_char_count,
                "engine_used": f"aims_tts_via_{aims_tts_data.get('voice_id', 'unknown_voice')}",
                "tts_settings_used": final_tts_settings_used}

    except Exception as e: # Catch-all for task submission or polling logic errors
        logger.error(f"Celery Task {self.request.id}: Error in forge_voice_task: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=10, max_retries=2) # Celery retry


@app.route('/v1/forge_voice', methods=['POST'])
def handle_forge_voice_async():
    request_id_main = f"vfa_req_{uuid.uuid4().hex[:8]}"
    logger.info(f"Request {request_id_main}: Received async /v1/forge_voice request.")

    try:
        data = request.get_json()
        if not data: return jsonify({"error_code": "VFA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json: return jsonify({"error_code": "VFA_MALFORMED_JSON", "message": f"Malformed JSON: {str(e_json)}"}), 400

    script_payload = data.get('script')
    voice_params_payload = data.get('voice_params')
    test_scenario_header = request.headers.get('X-Test-Scenario') # For test mode in task

    if not isinstance(script_payload, dict) or not script_payload.get("script_id"): # Basic validation
        return jsonify({"error_code": "VFA_INVALID_SCRIPT_PAYLOAD", "message": "Valid 'script' object with 'script_id' is required."}), 400
    if voice_params_payload is not None and not isinstance(voice_params_payload, dict):
        return jsonify({"error_code": "VFA_INVALID_VOICE_PARAMS_TYPE", "message": "'voice_params' must be an object if provided."}), 400

    logger.info(f"Request {request_id_main}: Dispatching forge_voice task. Topic: '{script_payload.get('topic', 'N/A')}'")
    task = forge_voice_task.delay(
        request_id_celery=request_id_main,
        script_input=script_payload,
        voice_params_input=voice_params_payload,
        test_scenario_header=test_scenario_header
    )
    return jsonify({"task_id": task.id, "status_url": f"/v1/tasks/{task.id}", "message": "Voice forging task accepted."}), 202

@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_vfa_task_status(task_id: str):
    logger.info(f"Received request for VFA task status: {task_id}")
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}

    if task_result.successful():
        task_output = task_result.result
        response_data["result"] = task_output
        http_status = 200
        if isinstance(task_output, dict) and task_output.get("error_code"): http_status = 500 # Internal error from task logic
        elif isinstance(task_output, dict) and task_output.get("status") == VFA_STATUS_SKIPPED: http_status = 200 # Skipped is a valid outcome
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
    debug_mode = vfa_config.get("VFA_DEBUG_MODE", True)
    logger.info(f"--- VFA Service (AIMS_TTS Client & Celery Producer) starting on {host}:{port} (Debug: {debug_mode}) ---")
    if not vfa_config.get("AIMS_TTS_SERVICE_URL") and not vfa_config.get("VFA_TEST_MODE_ENABLED"):
        logger.critical("CRITICAL ERROR: AIMS_TTS_SERVICE_URL is not set and VFA is not in test mode. VFA will not function correctly.")
    app.run(host=host, port=port, debug=debug_mode)
