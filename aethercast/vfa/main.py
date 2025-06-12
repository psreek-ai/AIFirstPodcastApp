import logging
import os
import uuid
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from typing import Optional, Dict, Any
import requests # Added for AIMS_TTS service call
import time # Added for retry logic (if needed for AIMS_TTS)

# --- Load Environment Variables ---
load_dotenv()

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="vfa"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

# Configure JSON logging for the Flask app
def setup_json_logging(flask_app):
    flask_app.logger.handlers.clear() # Clear existing default Flask handlers
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

# Make the global logger use the configured app.logger
logger = app.logger

# --- VFA Configuration ---
vfa_config = {}

def load_vfa_configuration():
    """Loads VFA configurations from environment variables with defaults."""
    global vfa_config

    # Removed Google Cloud TTS specific configs
    # vfa_config['GOOGLE_APPLICATION_CREDENTIALS'] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    # vfa_config['VFA_TTS_VOICE_NAME'] = os.getenv("VFA_TTS_VOICE_NAME", "en-US-Wavenet-D")
    # vfa_config['VFA_TTS_LANG_CODE'] = os.getenv("VFA_TTS_LANG_CODE", "en-US")
    # vfa_config['VFA_TTS_AUDIO_ENCODING_STR'] = os.getenv("VFA_TTS_AUDIO_ENCODING", "MP3").upper()
    # vfa_config['VFA_TTS_DEFAULT_SPEAKING_RATE'] = float(os.getenv("VFA_TTS_DEFAULT_SPEAKING_RATE", "1.0"))
    # vfa_config['VFA_TTS_DEFAULT_PITCH'] = float(os.getenv("VFA_TTS_DEFAULT_PITCH", "0.0"))

    vfa_config['AIMS_TTS_SERVICE_URL'] = os.getenv("AIMS_TTS_SERVICE_URL", "http://aims_tts_service:9000/v1/synthesize")
    vfa_config['AIMS_TTS_REQUEST_TIMEOUT_SECONDS'] = int(os.getenv("AIMS_TTS_REQUEST_TIMEOUT_SECONDS", "120"))

    # VFA_SHARED_AUDIO_DIR might still be used if AIMS_TTS returns a path on a shared volume
    # Or it might be where VFA downloads/stores a file if AIMS_TTS returns raw audio data (not current plan for AIMS_TTS)
    # For test mode, it's where dummy files are created.
    vfa_config['VFA_SHARED_AUDIO_DIR'] = os.getenv("VFA_SHARED_AUDIO_DIR", "/app/tests/output/vfa_generated_audio/") # Adjusted for consistency

    vfa_config['VFA_MIN_SCRIPT_LENGTH'] = int(os.getenv("VFA_MIN_SCRIPT_LENGTH", "20"))
    vfa_config['VFA_TEST_MODE_ENABLED'] = os.getenv("VFA_TEST_MODE_ENABLED", "False").lower() == 'true'

    vfa_config['VFA_HOST'] = os.getenv("VFA_HOST", "0.0.0.0")
    vfa_config['VFA_PORT'] = int(os.getenv("VFA_PORT", 5005))
    vfa_config['VFA_DEBUG_MODE'] = os.getenv("VFA_DEBUG_MODE", "True").lower() == "true"

    logger.info("--- VFA Configuration (AIMS_TTS Client) ---")
    for key, value in vfa_config.items():
        logger.info(f"  {key}: {value}")
    logger.info("--- End VFA Configuration ---")

    if not vfa_config.get('AIMS_TTS_SERVICE_URL') and not vfa_config.get('VFA_TEST_MODE_ENABLED'):
        error_msg = "CRITICAL: AIMS_TTS_SERVICE_URL is not set and VFA is not in test mode. VFA cannot function."
        logger.error(error_msg)
        raise ValueError(error_msg)

load_vfa_configuration()

# --- Test Mode Scenario Constants ---
VFA_TEST_SCENARIO_AIMS_TTS_ERROR_MSG = "Test scenario: Simulated AIMS_TTS service error."
# VFA_TEST_SCENARIO_FILE_SAVE_ERROR_MSG remains relevant for test mode's dummy file creation.
VFA_TEST_SCENARIO_FILE_SAVE_ERROR_MSG = "Test scenario: Simulated file saving IO error in VFA."

# PSWA Error Prefixes (remains the same)
PSWA_ERROR_PREFIXES = ("OpenAI library not available", "Error: OPENAI_API_KEY", "[ERROR] Insufficient content")


def forge_voice(script_input: dict, voice_params_input: Optional[dict] = None) -> dict: # Will now return a dict with result_data, status_for_metric, etc.
    stream_id = f"strm_{uuid.uuid4().hex}"
    original_topic = script_input.get("topic", "Unknown Topic")
    voice_params_input = voice_params_input or {}
    status_for_metric = "unknown_error"
    aims_tts_latency_ms = None
    script_char_count_for_metric = 0


    # These settings will be passed to AIMS_TTS
    # AIMS_TTS will have its own defaults if these are not provided.
    requested_tts_settings = {
        "voice_id": voice_params_input.get("voice_name"), # VFA's "voice_name" maps to AIMS_TTS "voice_id"
        "audio_format": voice_params_input.get("audio_encoding"), # VFA's "audio_encoding" maps to AIMS_TTS "audio_format"
        "speech_rate": voice_params_input.get("speaking_rate"),
        "pitch": voice_params_input.get("pitch"),
        # language_code might be part of voice_id in AIMS_TTS, or a separate param.
        # For now, AIMS_TTS placeholder doesn't explicitly use it in request but returns it.
    }
    # Filter out None values, so AIMS_TTS uses its defaults for those
    requested_tts_settings = {k: v for k, v in requested_tts_settings.items() if v is not None}


    if vfa_config.get('VFA_TEST_MODE_ENABLED'):
        scenario = request.headers.get('X-Test-Scenario', 'default')
        logger.info(f"[VFA_MAIN_LOGIC] Test mode enabled. Scenario: '{scenario}' for stream {stream_id}, topic '{original_topic}'.")

        # Simulate what AIMS_TTS might return in test mode
        simulated_aims_tts_audio_format = requested_tts_settings.get("audio_format", "mp3")
        simulated_aims_tts_voice_id = requested_tts_settings.get("voice_id", "test-mode-voice")
        script_char_count_for_metric = len(str(script_input)) # For test mode, use full input length

        if scenario == 'vfa_error_aims_tts':
            status_for_metric = "test_mode_aims_tts_error"
            result_data = {
                "error_code": "VFA_TEST_MODE_AIMS_TTS_ERROR",
                "message": "Simulated AIMS_TTS service error from VFA (Test Mode).",
                "details": VFA_TEST_SCENARIO_AIMS_TTS_ERROR_MSG,
                "audio_filepath": None, "stream_id": stream_id,
                "script_char_count": script_char_count_for_metric, "engine_used": "test_mode_aims_tts_error",
                "tts_settings_used": {"voice_name": simulated_aims_tts_voice_id, "audio_encoding": simulated_aims_tts_audio_format, **requested_tts_settings}
            }
            return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": None, "script_char_count_for_metric": script_char_count_for_metric}

        # Re-use existing file save error for test mode's dummy file
        shared_audio_dir_for_test_dummy = vfa_config.get('VFA_SHARED_AUDIO_DIR')
        try:
            os.makedirs(shared_audio_dir_for_test_dummy, exist_ok=True)
            file_extension = f".{simulated_aims_tts_audio_format.lower()}"
            dummy_filename = f"aethercast_audio_vfa_testmode_{stream_id}_{uuid.uuid4().hex[:6]}{file_extension}"
            dummy_filepath = os.path.join(shared_audio_dir_for_test_dummy, dummy_filename)

            if scenario == 'vfa_error_file_save':
                logger.info(f"[VFA_MAIN_LOGIC] Test mode (vfa_error_file_save): Simulating file save error for dummy path {dummy_filepath}")
                status_for_metric = "test_mode_file_save_error"
                result_data = {
                    "error_code": "VFA_TEST_MODE_FILE_SAVE_ERROR", "message": VFA_TEST_SCENARIO_FILE_SAVE_ERROR_MSG,
                    "details": "Simulated error writing dummy audio file in test mode.",
                    "audio_filepath": dummy_filepath, "stream_id": stream_id,
                    "script_char_count": script_char_count_for_metric, "engine_used": "test_mode_dummy_file_error",
                    "tts_settings_used": {"voice_name": simulated_aims_tts_voice_id, "audio_encoding": simulated_aims_tts_audio_format, **requested_tts_settings}
                }
                return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": None, "script_char_count_for_metric": script_char_count_for_metric}

            # Default success scenario for test mode (creates a dummy file)
            with open(dummy_filepath, "wb") as f: f.write(b"dummy audio data")
            logger.info(f"[VFA_MAIN_LOGIC] Test mode (default): Created dummy audio file at {dummy_filepath}")
            status_for_metric = "test_mode_success"
            result_data = {
                "status": "success", "message": "Audio successfully synthesized (VFA TEST MODE - dummy file, AIMS_TTS call bypassed).",
                "audio_filepath": dummy_filepath, "stream_id": stream_id,
                "audio_format": simulated_aims_tts_audio_format.lower(),
                "script_char_count": script_char_count_for_metric, "engine_used": "test_mode_bypassed_aims_tts",
                "tts_settings_used": {"voice_name": simulated_aims_tts_voice_id, "audio_encoding": simulated_aims_tts_audio_format, **requested_tts_settings}
            }
            return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": None, "script_char_count_for_metric": script_char_count_for_metric}
        except IOError as e:
            logger.error(f"[VFA_MAIN_LOGIC] Test mode: Failed during dummy directory/file operation: {e}", exc_info=True)
            status_for_metric = "test_mode_io_error"
            result_data = {"error_code": "VFA_TEST_MODE_IO_ERROR", "message": "Test mode failed during disk op.", "details": str(e), "audio_filepath": None, "stream_id": stream_id, "script_char_count": 0, "engine_used": "test_mode_io_error", "tts_settings_used": requested_tts_settings}
            return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": None, "script_char_count_for_metric": 0}

    # --- Real AIMS_TTS Call Logic ---
    text_to_synthesize = ""
    script_char_count_for_metric = 0
    if not isinstance(script_input, dict):
        text_to_synthesize = str(script_input) # Should not happen based on API spec
        script_char_count_for_metric = len(text_to_synthesize)
    else:
        original_topic = script_input.get("topic", original_topic)
        full_raw_script = script_input.get("full_raw_script", "")
        script_char_count_for_metric = len(full_raw_script) # Base count on raw script
        if any(full_raw_script.startswith(prefix) for prefix in PSWA_ERROR_PREFIXES):
            msg = f"Script for topic '{original_topic}' appears to be an error message from PSWA, audio generation skipped."
            logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: {msg}")
            status_for_metric = "skipped_pswa_error"
            result_data = {"status": "skipped", "message": msg, "audio_filepath": None, "stream_id": stream_id, "script_char_count": script_char_count_for_metric, "tts_settings_used": None}
            return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": None, "script_char_count_for_metric": script_char_count_for_metric}

        segments = script_input.get("segments", [])
        if segments:
            tts_parts = []; title = script_input.get("title", original_topic)
            if title and not title.startswith("Error: Insufficient Content"): tts_parts.append(f"{title}.")
            for segment in segments:
                seg_title = segment.get("segment_title", ""); seg_content = segment.get("content", "")
                if seg_title and seg_title not in ["INTRO", "OUTRO", "ERROR"]: tts_parts.append(f"{seg_title}.")
                if seg_content: tts_parts.append(seg_content)
            text_to_synthesize = "\n\n".join(tts_parts)
        elif full_raw_script:
            text_to_synthesize = full_raw_script
        else:
            logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: No usable text in script_input for topic '{original_topic}'.")
            status_for_metric = "script_error_no_text"
            result_data = {"error_code": "VFA_SCRIPT_ERROR_NO_TEXT", "message": "Script does not contain usable text.", "details": "Invalid script structure.", "audio_filepath": None, "stream_id": stream_id, "script_char_count": 0, "tts_settings_used": None}
            return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": None, "script_char_count_for_metric": 0}

    # Use length of text_to_synthesize for this specific metric, as it's what's sent to TTS
    # script_char_count_for_metric is already set to raw script length, which is fine for overall context.
    # This specific metric is about what was *actually* synthesized.
    synthesized_char_count = len(text_to_synthesize)
    logger.info("AIMS_TTS characters to be synthesized", extra=dict(metric_name="vfa_script_char_count_for_synthesis", value=synthesized_char_count, tags={"voice_id_used": requested_tts_settings.get("voice_id", "default")}))


    if synthesized_char_count < vfa_config.get('VFA_MIN_SCRIPT_LENGTH', 20):
        msg = f"Text too short (length {synthesized_char_count}), audio generation skipped."
        logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: {msg}")
        status_for_metric = "skipped_script_too_short"
        result_data = {"status": "skipped", "message": msg, "audio_filepath": None, "stream_id": stream_id, "script_char_count": synthesized_char_count, "tts_settings_used": requested_tts_settings}
        return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": None, "script_char_count_for_metric": synthesized_char_count}

    aims_tts_payload = {"text": text_to_synthesize, **requested_tts_settings}
    aims_tts_url = vfa_config['AIMS_TTS_SERVICE_URL']
    aims_tts_timeout = vfa_config['AIMS_TTS_REQUEST_TIMEOUT_SECONDS']

    logger.info(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: Calling AIMS_TTS service. URL: {aims_tts_url}, Payload: {aims_tts_payload}")
    
    aims_tts_call_start_time = time.time()
    try:
        response = requests.post(aims_tts_url, json=aims_tts_payload, timeout=aims_tts_timeout)
        response.raise_for_status()
        aims_tts_data = response.json()
        aims_tts_latency_ms = (time.time() - aims_tts_call_start_time) * 1000
        logger.info("VFA AIMS_TTS call processed", extra=dict(metric_name="vfa_aims_tts_call_latency_ms", value=round(aims_tts_latency_ms, 2), tags={"voice_id_used": requested_tts_settings.get("voice_id", "default")}))


        logger.info(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: AIMS_TTS call successful. Response: {aims_tts_data}")

        audio_filepath_from_aims = aims_tts_data.get("audio_url")
        if not audio_filepath_from_aims:
            raise ValueError("AIMS_TTS response missing 'audio_url'.")

        # Ensure the directory for the audio file exists if VFA needs to confirm/access it.
        # If AIMS_TTS guarantees the path is valid on a shared volume, this might be optional.
        # For now, let's assume VFA should ensure its part of the path is okay if audio_filepath_from_aims
        # is within a sub-directory VFA manages under a shared root.
        # However, if AIMS_TTS returns a path like /shared_audio/aims_tts/file.mp3, VFA assumes /shared_audio/aims_tts exists.
        # And if VFA_SHARED_AUDIO_DIR is /shared_audio/vfa_files, it wouldn't create /shared_audio/aims_tts.
        # Let's simplify: VFA trusts the audio_url path from AIMS_TTS is directly usable.
        # os.makedirs(os.path.dirname(audio_filepath_from_aims), exist_ok=True)

        final_tts_settings_used = {
            "voice_name": aims_tts_data.get("voice_id", requested_tts_settings.get("voice_id")),
            "audio_encoding": aims_tts_data.get("audio_format", requested_tts_settings.get("audio_format")),
            "speaking_rate": requested_tts_settings.get("speech_rate"),
            "pitch": requested_tts_settings.get("pitch")
        }
        final_tts_settings_used = {k:v for k,v in final_tts_settings_used.items() if v is not None}

        status_for_metric = "success"
        result_data = {
            "status": "success",
            "message": "Audio successfully synthesized via AIMS_TTS.",
            "audio_filepath": audio_filepath_from_aims,
            "stream_id": stream_id,
            "audio_format": aims_tts_data.get("audio_format", "unknown").lower(),
            "script_char_count": synthesized_char_count, # Use synthesized_char_count here
            "engine_used": f"aims_tts_via_{aims_tts_data.get('voice_id', 'unknown_voice')}",
            "tts_settings_used": final_tts_settings_used
        }
        return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": aims_tts_latency_ms, "script_char_count_for_metric": synthesized_char_count}

    except requests.exceptions.Timeout as e_timeout:
        if aims_tts_call_start_time: aims_tts_latency_ms = (time.time() - aims_tts_call_start_time) * 1000
        err_msg = f"AIMS_TTS service request timed out after {aims_tts_timeout}s: {str(e_timeout)}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True)
        logger.error("VFA AIMS_TTS call failure", extra=dict(metric_name="vfa_aims_tts_call_failure_count", value=1, tags={"error_type": "timeout"}))
        status_for_metric = "aims_tts_timeout"
        result_data = {"error_code": "VFA_AIMS_TTS_TIMEOUT", "message": "AIMS_TTS request timed out.", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
        return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": aims_tts_latency_ms, "script_char_count_for_metric": synthesized_char_count}
    except requests.exceptions.HTTPError as e_http:
        if aims_tts_call_start_time: aims_tts_latency_ms = (time.time() - aims_tts_call_start_time) * 1000
        aims_tts_error_details = e_http.response.text
        try: aims_tts_error_details = e_http.response.json()
        except ValueError: pass
        err_msg = f"AIMS_TTS service returned HTTP error {e_http.response.status_code}. Details: {aims_tts_error_details}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True)
        logger.error("VFA AIMS_TTS call failure", extra=dict(metric_name="vfa_aims_tts_call_failure_count", value=1, tags={"error_type": f"http_error_{e_http.response.status_code}"}))
        status_for_metric = f"aims_tts_http_error_{e_http.response.status_code}"
        result_data = {"error_code": "VFA_AIMS_TTS_HTTP_ERROR", "message": f"AIMS_TTS request failed (HTTP {e_http.response.status_code}).", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
        return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": aims_tts_latency_ms, "script_char_count_for_metric": synthesized_char_count}
    except requests.exceptions.RequestException as e_req:
        if aims_tts_call_start_time: aims_tts_latency_ms = (time.time() - aims_tts_call_start_time) * 1000
        err_msg = f"Error calling AIMS_TTS service: {type(e_req).__name__} - {str(e_req)}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True)
        logger.error("VFA AIMS_TTS call failure", extra=dict(metric_name="vfa_aims_tts_call_failure_count", value=1, tags={"error_type": "request_exception"}))
        status_for_metric = "aims_tts_request_error"
        result_data = {"error_code": "VFA_AIMS_TTS_REQUEST_ERROR", "message": "Failed to communicate with AIMS_TTS.", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
        return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": aims_tts_latency_ms, "script_char_count_for_metric": synthesized_char_count}
    except (ValueError, KeyError, json.JSONDecodeError) as e_parse:
        if aims_tts_call_start_time: aims_tts_latency_ms = (time.time() - aims_tts_call_start_time) * 1000
        err_msg = f"Error parsing AIMS_TTS response or missing critical data: {str(e_parse)}. Response: {response.text[:200] if 'response' in locals() else 'N/A'}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True)
        logger.error("VFA AIMS_TTS call failure", extra=dict(metric_name="vfa_aims_tts_call_failure_count", value=1, tags={"error_type": "parse_error"}))
        status_for_metric = "aims_tts_bad_response"
        result_data = {"error_code": "VFA_AIMS_TTS_BAD_RESPONSE", "message": "AIMS_TTS returned an invalid response.", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
        return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": aims_tts_latency_ms, "script_char_count_for_metric": synthesized_char_count}
    except Exception as e:
        if aims_tts_call_start_time: aims_tts_latency_ms = (time.time() - aims_tts_call_start_time) * 1000
        err_msg = f"Unexpected error during AIMS_TTS interaction: {type(e).__name__} - {str(e)}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True)
        logger.error("VFA AIMS_TTS call failure", extra=dict(metric_name="vfa_aims_tts_call_failure_count", value=1, tags={"error_type": "unknown_aims_tts_error"}))
        status_for_metric = "aims_tts_unknown_error"
        result_data = {"error_code": "VFA_UNEXPECTED_AIMS_TTS_ERROR", "message": "Unexpected error with AIMS_TTS.", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
        return {"result_data": result_data, "status_for_metric": status_for_metric, "aims_tts_latency_ms": aims_tts_latency_ms, "script_char_count_for_metric": synthesized_char_count}

# --- Flask Endpoint ---
@app.route('/forge_voice', methods=['POST'])
def handle_forge_voice():
    request_start_time = time.time()
    final_status_str = "unknown_error"

    logger.info("[VFA_FLASK_ENDPOINT] Received request for /forge_voice")

    try:
        data = request.get_json()
        if not data:
            logger.error("[VFA_FLASK_ENDPOINT] Invalid or empty JSON payload received.")
            final_status_str = "validation_error_payload"
            logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))
            return jsonify({"error_code": "VFA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload.", "details": "Request body must be a valid non-empty JSON object."}), 400
    except Exception as e_json_decode:
        logger.error(f"[VFA_FLASK_ENDPOINT] Failed to decode JSON payload: {e_json_decode}", exc_info=True)
        final_status_str = "validation_error_bad_json"
        logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))
        return jsonify({"error_code": "VFA_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json_decode)}), 400

    script_payload = data.get('script')
    voice_params_payload = data.get('voice_params')

    # Validate script payload
    if script_payload is None or not isinstance(script_payload, dict):
        logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: 'script' must be a non-empty JSON object. Received: {script_payload}")
        final_status_str = "validation_error_script_payload"
        logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))
        return jsonify({"error_code": "VFA_INVALID_SCRIPT_PAYLOAD", "message": "Validation failed: 'script' must be a non-empty JSON object."}), 400

    required_script_keys = ["script_id", "topic", "title", "segments"]
    for key in required_script_keys:
        if key not in script_payload or not script_payload[key]:
            if key == "segments" and isinstance(script_payload.get(key), list): pass
            else:
                logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: 'script.{key}' is missing or empty. Received: {script_payload.get(key)}")
                final_status_str = f"validation_error_script_{key}"
                logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))
                return jsonify({"error_code": f"VFA_INVALID_SCRIPT_{key.upper()}", "message": f"Validation failed: 'script.{key}' is missing or invalid."}), 400

    if not isinstance(script_payload["segments"], list):
        logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: 'script.segments' must be a list. Received type: {type(script_payload['segments'])}")
        final_status_str = "validation_error_segments_type"
        logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))
        return jsonify({"error_code": "VFA_INVALID_SCRIPT_SEGMENTS_TYPE", "message": "Validation failed: 'script.segments' must be a list."}), 400

    for i, segment in enumerate(script_payload["segments"]):
        if not isinstance(segment, dict):
            logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: script.segments[{i}] is not an object. Received: {segment}")
            final_status_str = "validation_error_segment_structure"
            logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))
            return jsonify({"error_code": "VFA_INVALID_SEGMENT_STRUCTURE", "message": f"Validation failed: script.segments[{i}] must be an object."}), 400
        if not segment.get("segment_title") or not isinstance(segment.get("segment_title"), str):
            logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: script.segments[{i}].segment_title is missing or not a string.")
            final_status_str = "validation_error_segment_title"
            logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))
            return jsonify({"error_code": "VFA_INVALID_SEGMENT_TITLE", "message": f"Validation failed: script.segments[{i}].segment_title is missing or not a string."}), 400
        if not segment.get("content") or not isinstance(segment.get("content"), str):
            logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: script.segments[{i}].content is missing or not a string.")
            final_status_str = "validation_error_segment_content"
            logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))
            return jsonify({"error_code": "VFA_INVALID_SEGMENT_CONTENT", "message": f"Validation failed: script.segments[{i}].content is missing or not a string."}), 400

    if voice_params_payload is not None:
        if not isinstance(voice_params_payload, dict):
            logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: 'voice_params' must be a JSON object if provided. Received: {voice_params_payload}")
            final_status_str = "validation_error_voice_params_type"
            logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))
            return jsonify({"error_code": "VFA_INVALID_VOICE_PARAMS_TYPE", "message": "Validation failed: 'voice_params' must be a JSON object if provided."}), 400
        # Further voice_params validation... (assuming valid for brevity of this diff)

    logger.info(f"[VFA_FLASK_ENDPOINT] Calling forge_voice with script topic: '{script_payload.get('topic', 'N/A')}', voice_params: {voice_params_payload}")

    forge_voice_response = forge_voice(script_payload, voice_params_input=voice_params_payload)
    result_data = forge_voice_response.get("result_data")
    final_status_str = forge_voice_response.get("status_for_metric", "unknown_forge_voice_status")
    # aims_tts_latency_ms = forge_voice_response.get("aims_tts_latency_ms") # Already logged in forge_voice
    # script_char_count_for_metric = forge_voice_response.get("script_char_count_for_metric") # Already logged in forge_voice

    overall_latency_ms = (time.time() - request_start_time) * 1000
    logger.info("VFA forge_voice overall request latency", extra=dict(metric_name="vfa_forge_voice_latency_ms", value=round(overall_latency_ms, 2)))
    logger.info("VFA forge_voice request completed", extra=dict(metric_name="vfa_forge_voice_request_count", value=1, tags={"status": final_status_str}))

    status_code = 500 # Default for errors
    if "error_code" in result_data:
        logger.error(f"[VFA_FLASK_ENDPOINT] forge_voice returned error: {result_data.get('error_code')} - {result_data.get('message')}")
        if result_data["error_code"] in ["VFA_SCRIPT_ERROR_NO_TEXT", "VFA_VALIDATION_ERROR"]: status_code = 400 # Should be caught by validation above
        elif result_data["error_code"].startswith("VFA_AIMS_TTS_"): status_code = 502
    elif result_data.get("status") == "success": status_code = 200
    elif result_data.get("status") == "skipped": status_code = 200

    return jsonify(result_data), status_code

if __name__ == "__main__":
    host = vfa_config.get("VFA_HOST", "0.0.0.0")
    port = vfa_config.get("VFA_PORT", 5005)
    debug_mode = vfa_config.get("VFA_DEBUG_MODE", True)
    logger.info(f"--- VFA Service (AIMS_TTS Client) starting on {host}:{port} (Debug: {debug_mode}) ---")
    if not vfa_config.get("AIMS_TTS_SERVICE_URL") and not vfa_config.get("VFA_TEST_MODE_ENABLED"):
        logger.critical("CRITICAL ERROR: AIMS_TTS_SERVICE_URL is not set and VFA is not in test mode. VFA will not function correctly.")
    app.run(host=host, port=port, debug=debug_mode)
