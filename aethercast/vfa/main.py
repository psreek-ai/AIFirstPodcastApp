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
from python_json_logger import jsonlogger # Added for JSON logging

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
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s",
        rename_fields={"levelname": "level", "name": "logger_name", "asctime": "timestamp"}
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("JSON logging configured for VFA service.")

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


def forge_voice(script_input: dict, voice_params_input: Optional[dict] = None) -> dict:
    stream_id = f"strm_{uuid.uuid4().hex}"
    original_topic = script_input.get("topic", "Unknown Topic")
    voice_params_input = voice_params_input or {}

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

        if scenario == 'vfa_error_aims_tts': # New scenario to simulate AIMS_TTS failure
            return {
                "error_code": "VFA_TEST_MODE_AIMS_TTS_ERROR",
                "message": "Simulated AIMS_TTS service error from VFA (Test Mode).",
                "details": VFA_TEST_SCENARIO_AIMS_TTS_ERROR_MSG,
                "audio_filepath": None, "stream_id": stream_id,
                "script_char_count": len(str(script_input)), "engine_used": "test_mode_aims_tts_error",
                "tts_settings_used": {"voice_name": simulated_aims_tts_voice_id, "audio_encoding": simulated_aims_tts_audio_format, **requested_tts_settings}
            }

        # Re-use existing file save error for test mode's dummy file
        shared_audio_dir_for_test_dummy = vfa_config.get('VFA_SHARED_AUDIO_DIR')
        try:
            os.makedirs(shared_audio_dir_for_test_dummy, exist_ok=True)
            file_extension = f".{simulated_aims_tts_audio_format.lower()}"
            dummy_filename = f"aethercast_audio_vfa_testmode_{stream_id}_{uuid.uuid4().hex[:6]}{file_extension}"
            dummy_filepath = os.path.join(shared_audio_dir_for_test_dummy, dummy_filename)

            if scenario == 'vfa_error_file_save':
                logger.info(f"[VFA_MAIN_LOGIC] Test mode (vfa_error_file_save): Simulating file save error for dummy path {dummy_filepath}")
                return {
                    "error_code": "VFA_TEST_MODE_FILE_SAVE_ERROR", "message": VFA_TEST_SCENARIO_FILE_SAVE_ERROR_MSG,
                    "details": "Simulated error writing dummy audio file in test mode.",
                    "audio_filepath": dummy_filepath, "stream_id": stream_id,
                    "script_char_count": len(str(script_input)), "engine_used": "test_mode_dummy_file_error",
                    "tts_settings_used": {"voice_name": simulated_aims_tts_voice_id, "audio_encoding": simulated_aims_tts_audio_format, **requested_tts_settings}
                }

            # Default success scenario for test mode (creates a dummy file)
            with open(dummy_filepath, "wb") as f: f.write(b"dummy audio data")
            logger.info(f"[VFA_MAIN_LOGIC] Test mode (default): Created dummy audio file at {dummy_filepath}")
            return {
                "status": "success", "message": "Audio successfully synthesized (VFA TEST MODE - dummy file, AIMS_TTS call bypassed).",
                "audio_filepath": dummy_filepath, "stream_id": stream_id,
                "audio_format": simulated_aims_tts_audio_format.lower(),
                "script_char_count": len(str(script_input)), "engine_used": "test_mode_bypassed_aims_tts",
                "tts_settings_used": {"voice_name": simulated_aims_tts_voice_id, "audio_encoding": simulated_aims_tts_audio_format, **requested_tts_settings}
            }
        except IOError as e:
            logger.error(f"[VFA_MAIN_LOGIC] Test mode: Failed during dummy directory/file operation: {e}", exc_info=True)
            return {"error_code": "VFA_TEST_MODE_IO_ERROR", "message": "Test mode failed during disk op.", "details": str(e), "audio_filepath": None, "stream_id": stream_id, "script_char_count": 0, "engine_used": "test_mode_io_error", "tts_settings_used": requested_tts_settings}

    # --- Real AIMS_TTS Call Logic ---
    text_to_synthesize = ""
    if not isinstance(script_input, dict):
        text_to_synthesize = str(script_input)
    else: # Extract text from structured script (existing logic)
        original_topic = script_input.get("topic", original_topic)
        full_raw_script = script_input.get("full_raw_script", "")
        if any(full_raw_script.startswith(prefix) for prefix in PSWA_ERROR_PREFIXES):
            msg = f"Script for topic '{original_topic}' appears to be an error message from PSWA, audio generation skipped."
            logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: {msg}")
            return {"status": "skipped", "message": msg, "audio_filepath": None, "stream_id": stream_id, "script_char_count": len(full_raw_script), "tts_settings_used": None}
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
            logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: No usable text in script_input for topic '{original_topic}'.")
            return {"error_code": "VFA_SCRIPT_ERROR_NO_TEXT", "message": "Script does not contain usable text.", "details": "Invalid script structure.", "audio_filepath": None, "stream_id": stream_id, "script_char_count": 0, "tts_settings_used": None}

    script_char_count = len(text_to_synthesize)
    if script_char_count < vfa_config.get('VFA_MIN_SCRIPT_LENGTH', 20):
        msg = f"Text too short (length {script_char_count}), audio generation skipped."
        logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: {msg}")
        return {"status": "skipped", "message": msg, "audio_filepath": None, "stream_id": stream_id, "script_char_count": script_char_count, "tts_settings_used": requested_tts_settings}

    aims_tts_payload = {"text": text_to_synthesize, **requested_tts_settings}
    aims_tts_url = vfa_config['AIMS_TTS_SERVICE_URL']
    aims_tts_timeout = vfa_config['AIMS_TTS_REQUEST_TIMEOUT_SECONDS']

    logger.info(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: Calling AIMS_TTS service. URL: {aims_tts_url}, Payload: {aims_tts_payload}")
    
    try:
        response = requests.post(aims_tts_url, json=aims_tts_payload, timeout=aims_tts_timeout)
        response.raise_for_status()
        aims_tts_data = response.json()

        logger.info(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: AIMS_TTS call successful. Response: {aims_tts_data}")

        audio_filepath_from_aims = aims_tts_data.get("audio_url") # This is expected to be a usable path for VFA
        if not audio_filepath_from_aims:
            raise ValueError("AIMS_TTS response missing 'audio_url'.")

        # Ensure the directory for the audio file exists if VFA needs to confirm/access it.
        # If AIMS_TTS guarantees the path is valid on a shared volume, this might be optional.
        # For now, let's assume VFA should ensure its part of the path is okay if audio_filepath_from_aims
        # is within a sub-directory VFA manages under a shared root.
        # However, if AIMS_TTS returns a path like /shared_audio/aims_tts/file.mp3, VFA assumes /shared_audio/aims_tts exists.
        # And if VFA_SHARED_AUDIO_DIR is /shared_audio/vfa_files, it wouldn't create /shared_audio/aims_tts.
        # Let's simplify: VFA trusts the audio_url path from AIMS_TTS is directly usable.
        # os.makedirs(os.path.dirname(audio_filepath_from_aims), exist_ok=True) # This might be problematic if paths are absolute and different mounts

        # Construct tts_settings_used based on what was sent and what AIMS_TTS returned
        final_tts_settings_used = {
            "voice_name": aims_tts_data.get("voice_id", requested_tts_settings.get("voice_id")), # Prefer AIMS reported voice
            "audio_encoding": aims_tts_data.get("audio_format", requested_tts_settings.get("audio_format")), # Prefer AIMS reported format
            "speaking_rate": requested_tts_settings.get("speech_rate"), # AIMS_TTS doesn't currently return these
            "pitch": requested_tts_settings.get("pitch")
        }
        # Filter out None values from final_tts_settings_used
        final_tts_settings_used = {k:v for k,v in final_tts_settings_used.items() if v is not None}


        return {
            "status": "success",
            "message": "Audio successfully synthesized via AIMS_TTS.",
            "audio_filepath": audio_filepath_from_aims, # This is the path from AIMS_TTS
            "stream_id": stream_id,
            "audio_format": aims_tts_data.get("audio_format", "unknown").lower(),
            "script_char_count": script_char_count,
            "engine_used": f"aims_tts_via_{aims_tts_data.get('voice_id', 'unknown_voice')}",
            "tts_settings_used": final_tts_settings_used
        }

    except requests.exceptions.Timeout as e_timeout:
        err_msg = f"AIMS_TTS service request timed out after {aims_tts_timeout}s: {str(e_timeout)}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True)
        return {"error_code": "VFA_AIMS_TTS_TIMEOUT", "message": "AIMS_TTS request timed out.", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
    except requests.exceptions.HTTPError as e_http:
        aims_tts_error_details = e_http.response.text
        try: aims_tts_error_details = e_http.response.json()
        except ValueError: pass
        err_msg = f"AIMS_TTS service returned HTTP error {e_http.response.status_code}. Details: {aims_tts_error_details}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True) # Added exc_info
        return {"error_code": "VFA_AIMS_TTS_HTTP_ERROR", "message": f"AIMS_TTS request failed (HTTP {e_http.response.status_code}).", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
    except requests.exceptions.RequestException as e_req:
        err_msg = f"Error calling AIMS_TTS service: {type(e_req).__name__} - {str(e_req)}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True)
        return {"error_code": "VFA_AIMS_TTS_REQUEST_ERROR", "message": "Failed to communicate with AIMS_TTS.", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
    except (ValueError, KeyError, json.JSONDecodeError) as e_parse: # Covers missing audio_url or bad JSON from AIMS_TTS
        err_msg = f"Error parsing AIMS_TTS response or missing critical data: {str(e_parse)}. Response: {response.text[:200] if 'response' in locals() else 'N/A'}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True) # Added exc_info
        return {"error_code": "VFA_AIMS_TTS_BAD_RESPONSE", "message": "AIMS_TTS returned an invalid response.", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}
    except Exception as e:
        err_msg = f"Unexpected error during AIMS_TTS interaction: {type(e).__name__} - {str(e)}"
        logger.error(f"[VFA_AIMS_TTS_CALL] Stream {stream_id}: {err_msg}", exc_info=True)
        return {"error_code": "VFA_UNEXPECTED_AIMS_TTS_ERROR", "message": "Unexpected error with AIMS_TTS.", "details": err_msg, "stream_id": stream_id, "tts_settings_used": requested_tts_settings}

# --- Flask Endpoint (remains largely the same, error mapping might need adjustment) ---
@app.route('/forge_voice', methods=['POST'])
def handle_forge_voice():
    logger.info("[VFA_FLASK_ENDPOINT] Received request for /forge_voice")
    data = request.get_json()
    try:
        data = request.get_json()
        if not data:
            logger.error("[VFA_FLASK_ENDPOINT] Invalid or empty JSON payload received.")
            return jsonify({"error_code": "VFA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload.", "details": "Request body must be a valid non-empty JSON object."}), 400
    except Exception as e_json_decode:
        logger.error(f"[VFA_FLASK_ENDPOINT] Failed to decode JSON payload: {e_json_decode}", exc_info=True)
        return jsonify({"error_code": "VFA_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json_decode)}), 400

    script_payload = data.get('script')
    voice_params_payload = data.get('voice_params')

    # Validate script payload
    if script_payload is None or not isinstance(script_payload, dict):
        logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: 'script' must be a non-empty JSON object. Received: {script_payload}")
        return jsonify({"error_code": "VFA_INVALID_SCRIPT_PAYLOAD", "message": "Validation failed: 'script' must be a non-empty JSON object."}), 400

    required_script_keys = ["script_id", "topic", "title", "segments"]
    for key in required_script_keys:
        if key not in script_payload or not script_payload[key]: # Check for presence and non-empty (for strings)
            if key == "segments" and isinstance(script_payload.get(key), list): # Segments can be an empty list
                pass
            else:
                logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: 'script.{key}' is missing or empty. Received: {script_payload.get(key)}")
                return jsonify({"error_code": f"VFA_INVALID_SCRIPT_{key.upper()}", "message": f"Validation failed: 'script.{key}' is missing or invalid."}), 400

    if not isinstance(script_payload["segments"], list):
        logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: 'script.segments' must be a list. Received type: {type(script_payload['segments'])}")
        return jsonify({"error_code": "VFA_INVALID_SCRIPT_SEGMENTS_TYPE", "message": "Validation failed: 'script.segments' must be a list."}), 400

    for i, segment in enumerate(script_payload["segments"]):
        if not isinstance(segment, dict):
            logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: script.segments[{i}] is not an object. Received: {segment}")
            return jsonify({"error_code": "VFA_INVALID_SEGMENT_STRUCTURE", "message": f"Validation failed: script.segments[{i}] must be an object."}), 400
        if not segment.get("segment_title") or not isinstance(segment.get("segment_title"), str):
            logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: script.segments[{i}].segment_title is missing or not a string.")
            return jsonify({"error_code": "VFA_INVALID_SEGMENT_TITLE", "message": f"Validation failed: script.segments[{i}].segment_title is missing or not a string."}), 400
        if not segment.get("content") or not isinstance(segment.get("content"), str): # Allow empty string for content, but must be present and string
            logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: script.segments[{i}].content is missing or not a string.")
            return jsonify({"error_code": "VFA_INVALID_SEGMENT_CONTENT", "message": f"Validation failed: script.segments[{i}].content is missing or not a string."}), 400

    # Validate voice_params payload (if provided)
    if voice_params_payload is not None:
        if not isinstance(voice_params_payload, dict):
            logger.warning(f"[VFA_FLASK_ENDPOINT] Validation failed: 'voice_params' must be a JSON object if provided. Received: {voice_params_payload}")
            return jsonify({"error_code": "VFA_INVALID_VOICE_PARAMS_TYPE", "message": "Validation failed: 'voice_params' must be a JSON object if provided."}), 400

        if "voice_name" in voice_params_payload and not isinstance(voice_params_payload["voice_name"], str):
            return jsonify({"error_code": "VFA_INVALID_VOICE_NAME", "message": "Validation failed: 'voice_params.voice_name' must be a string."}), 400
        if "audio_encoding" in voice_params_payload and not isinstance(voice_params_payload["audio_encoding"], str):
            return jsonify({"error_code": "VFA_INVALID_AUDIO_ENCODING", "message": "Validation failed: 'voice_params.audio_encoding' must be a string."}), 400
        if "speaking_rate" in voice_params_payload:
            try: float(voice_params_payload["speaking_rate"]) # Check if convertible
            except ValueError: return jsonify({"error_code": "VFA_INVALID_SPEAKING_RATE", "message": "Validation failed: 'voice_params.speaking_rate' must be a float."}), 400
        if "pitch" in voice_params_payload:
            try: float(voice_params_payload["pitch"]) # Check if convertible
            except ValueError: return jsonify({"error_code": "VFA_INVALID_PITCH", "message": "Validation failed: 'voice_params.pitch' must be a float."}), 400

    logger.info(f"[VFA_FLASK_ENDPOINT] Calling forge_voice with script topic: '{script_payload.get('topic', 'N/A')}', voice_params: {voice_params_payload}")
    result = forge_voice(script_payload, voice_params_input=voice_params_payload)

    status_code = 500 # Default for errors
    if "error_code" in result:
        logger.error(f"[VFA_FLASK_ENDPOINT] forge_voice returned error: {result.get('error_code')} - {result.get('message')}")
        if result["error_code"] in ["VFA_SCRIPT_ERROR_NO_TEXT", "VFA_VALIDATION_ERROR"]: status_code = 400
        elif result["error_code"] in ["VFA_AIMS_TTS_TIMEOUT", "VFA_AIMS_TTS_HTTP_ERROR", "VFA_AIMS_TTS_REQUEST_ERROR", "VFA_AIMS_TTS_BAD_RESPONSE"]: status_code = 502 # Bad Gateway for AIMS_TTS issues
        elif result["error_code"] == "VFA_CONFIG_ERROR_NO_CREDENTIALS": status_code = 503 # If it were direct Google TTS
        # Add other specific mappings if needed
    elif result.get("status") == "success": status_code = 200
    elif result.get("status") == "skipped": status_code = 200

    return jsonify(result), status_code

if __name__ == "__main__":
    host = vfa_config.get("VFA_HOST", "0.0.0.0")
    port = vfa_config.get("VFA_PORT", 5005)
    debug_mode = vfa_config.get("VFA_DEBUG_MODE", True)
    logger.info(f"--- VFA Service (AIMS_TTS Client) starting on {host}:{port} (Debug: {debug_mode}) ---")
    if not vfa_config.get("AIMS_TTS_SERVICE_URL") and not vfa_config.get("VFA_TEST_MODE_ENABLED"):
        logger.critical("CRITICAL ERROR: AIMS_TTS_SERVICE_URL is not set and VFA is not in test mode. VFA will not function correctly.")
    app.run(host=host, port=port, debug=debug_mode)
