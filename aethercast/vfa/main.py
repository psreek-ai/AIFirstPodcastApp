import logging
import os
import uuid
from dotenv import load_dotenv # Added
from flask import Flask, request, jsonify
from typing import Optional, Dict, Any # For type hinting

# --- Load Environment Variables ---
load_dotenv() # Added

# --- Attempt to import Google Cloud Text-to-Speech library ---
try:
    from google.cloud import texttospeech
    from google.api_core import exceptions as google_exceptions # For specific API error handling
    VFA_IMPORTS_SUCCESSFUL = True
    VFA_MISSING_IMPORT_ERROR = None
except ImportError as e:
    VFA_IMPORTS_SUCCESSFUL = False
    VFA_MISSING_IMPORT_ERROR = e
    # Define placeholder for texttospeech.AudioEncoding if library failed to import
    # This allows constants to be defined without crashing the script at parse time.
    class PlaceholderAudioEncoding:
        MP3 = 2 # Value for MP3, common default
        LINEAR16 = 3
        OGG_OPUS = 4
    texttospeech = type('texttospeech', (object,), {'AudioEncoding': PlaceholderAudioEncoding})()
    # Define placeholder for google_exceptions
    google_exceptions = type('google_exceptions', (object,), {'GoogleAPIError': Exception})()

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
# Use Flask's logger if available and not the root logger to avoid duplicate messages when running with Flask.
if app.logger and app.logger.name != 'root':
    logger = app.logger
else:
    logger = logging.getLogger(__name__)
    if not logger.hasHandlers():
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - VFA - %(message)s')

# --- VFA Configuration ---
vfa_config = {}
google_audio_encoding_map = {}

def load_vfa_configuration():
    """Loads VFA configurations from environment variables with defaults."""
    global vfa_config, google_audio_encoding_map

    vfa_config['GOOGLE_APPLICATION_CREDENTIALS'] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    vfa_config['VFA_SHARED_AUDIO_DIR'] = os.getenv("VFA_SHARED_AUDIO_DIR", "/srv/aethercast/generated_audio/")
    vfa_config['VFA_TTS_VOICE_NAME'] = os.getenv("VFA_TTS_VOICE_NAME", "en-US-Wavenet-D")
    vfa_config['VFA_TTS_LANG_CODE'] = os.getenv("VFA_TTS_LANG_CODE", "en-US")
    vfa_config['VFA_TTS_AUDIO_ENCODING_STR'] = os.getenv("VFA_TTS_AUDIO_ENCODING", "MP3").upper()
    vfa_config['VFA_MIN_SCRIPT_LENGTH'] = int(os.getenv("VFA_MIN_SCRIPT_LENGTH", "20"))
    # Added new default voice parameters
    vfa_config['VFA_TTS_DEFAULT_SPEAKING_RATE'] = float(os.getenv("VFA_TTS_DEFAULT_SPEAKING_RATE", "1.0"))
    vfa_config['VFA_TTS_DEFAULT_PITCH'] = float(os.getenv("VFA_TTS_DEFAULT_PITCH", "0.0"))
    vfa_config['VFA_TEST_MODE_ENABLED'] = os.getenv("VFA_TEST_MODE_ENABLED", "False").lower() == 'true' # Added Test Mode

    vfa_config['VFA_HOST'] = os.getenv("VFA_HOST", "0.0.0.0")
    vfa_config['VFA_PORT'] = int(os.getenv("VFA_PORT", 5005))
    vfa_config['VFA_DEBUG_MODE'] = os.getenv("VFA_DEBUG_MODE", "True").lower() == "true"

    logger.info("--- VFA Configuration ---")
    for key, value in vfa_config.items():
        if key == "GOOGLE_APPLICATION_CREDENTIALS" and value:
            logger.info(f"  {key}: Loaded (Path: {value})")
        # Logging for unset GOOGLE_APPLICATION_CREDENTIALS will be handled by the check below
        elif key != "GOOGLE_APPLICATION_CREDENTIALS": # Avoid double logging if it's not set
            logger.info(f"  {key}: {value}")
    logger.info("--- End VFA Configuration ---")

    # Critical check for Google Credentials if imports were successful (i.e., real TTS is expected)
    if VFA_IMPORTS_SUCCESSFUL and not vfa_config.get("GOOGLE_APPLICATION_CREDENTIALS"):
        error_msg = "CRITICAL: GOOGLE_APPLICATION_CREDENTIALS is not set, but Google Cloud SDK is installed. VFA cannot function for real TTS. Please set this environment variable."
        logger.error(error_msg)
        raise ValueError(error_msg)
    elif not vfa_config.get("GOOGLE_APPLICATION_CREDENTIALS"):
        logger.warning("GOOGLE_APPLICATION_CREDENTIALS is NOT SET. Real TTS operations will fail if attempted.")
    else: # Credentials are set
        logger.info("GOOGLE_APPLICATION_CREDENTIALS path is configured.")


    if VFA_IMPORTS_SUCCESSFUL:
        google_audio_encoding_map.update({
            "MP3": texttospeech.AudioEncoding.MP3,
            "LINEAR16": texttospeech.AudioEncoding.LINEAR16,
            "OGG_OPUS": texttospeech.AudioEncoding.OGG_OPUS
        })
        encoding_str = vfa_config['VFA_TTS_AUDIO_ENCODING_STR']
        if encoding_str not in google_audio_encoding_map:
            logger.warning(f"Invalid VFA_TTS_AUDIO_ENCODING value '{encoding_str}'. Defaulting to MP3.")
            vfa_config['VFA_TTS_AUDIO_ENCODING_STR'] = "MP3"
    else:
        logger.warning("Google Cloud SDK not available. Audio encoding map not populated.")

# Load configuration at startup
load_vfa_configuration()

# --- Test Mode Scenario Constants ---
# These define the content of the JSON response for different test scenarios in VFA
VFA_TEST_SCENARIO_TTS_ERROR_MSG = "Test scenario: Simulated TTS API error from VFA."
VFA_TEST_SCENARIO_FILE_SAVE_ERROR_MSG = "Test scenario: Simulated file saving IO error in VFA."


# PSWA Error Prefixes to identify scripts that are actually error messages
PSWA_ERROR_PREFIXES = (
    "OpenAI library not available", 
    "Error: OPENAI_API_KEY environment variable",
    "Error: OPENAI_API_KEY is not configured.",
    "OpenAI API Error:", 
    "An unexpected error occurred during LLM call:",
    "[ERROR] Insufficient content provided"
)


def get_current_audio_encoding():
    """Returns the configured audio encoding enum member, defaulting to MP3 if not available or invalid."""
    if not VFA_IMPORTS_SUCCESSFUL:
        return PlaceholderAudioEncoding.MP3

    encoding_str = vfa_config.get('VFA_TTS_AUDIO_ENCODING_STR', "MP3")
    selected_encoding = google_audio_encoding_map.get(encoding_str)

    if selected_encoding is None:
        logger.warning(f"Audio encoding '{encoding_str}' map failed or invalid during get_current_audio_encoding. Defaulting to MP3 enum.")
        return google_audio_encoding_map.get("MP3", texttospeech.AudioEncoding.MP3)
    return selected_encoding


def forge_voice(script_input: dict, voice_params_input: Optional[dict] = None) -> dict:
    """
    Generates audio from a structured script dictionary using Google Cloud Text-to-Speech
    and saves it to a shared directory. Optional voice_params_input can override defaults.
    Returns a dictionary with audio generation details, including a stream_id, or error information.
    """
    stream_id = f"strm_{uuid.uuid4().hex}"
    original_topic = script_input.get("topic", "Unknown Topic") if isinstance(script_input, dict) else "Unknown Topic (from non-dict input)"
    voice_params_input = voice_params_input or {}

    # Determine TTS parameters early for potential use in test mode response
    used_tts_settings = {
        "voice_name": voice_params_input.get("voice_name", vfa_config.get('VFA_TTS_VOICE_NAME')),
        "language_code": voice_params_input.get("language_code", vfa_config.get('VFA_TTS_LANG_CODE')),
        "speaking_rate": voice_params_input.get("speaking_rate", vfa_config.get('VFA_TTS_DEFAULT_SPEAKING_RATE')),
        "pitch": voice_params_input.get("pitch", vfa_config.get('VFA_TTS_DEFAULT_PITCH')),
        "audio_encoding": vfa_config.get('VFA_TTS_AUDIO_ENCODING_STR')
    }

    if vfa_config.get('VFA_TEST_MODE_ENABLED'):
        scenario = request.headers.get('X-Test-Scenario', 'default')
        logger.info(f"[VFA_MAIN_LOGIC] Test mode enabled. Scenario: '{scenario}' for stream {stream_id}, topic '{original_topic}'.")
        shared_audio_dir = vfa_config.get('VFA_SHARED_AUDIO_DIR')

        if scenario == 'vfa_error_tts':
            return {
                "error_code": "VFA_TEST_MODE_TTS_API_ERROR",
                "message": "Simulated TTS API error from VFA (Test Mode).",
                "details": VFA_TEST_SCENARIO_TTS_ERROR_MSG,
                "audio_filepath": None, "stream_id": stream_id,
                "script_char_count": len(str(script_input)), "engine_used": "test_mode_tts_api_error",
                "tts_settings_used": used_tts_settings
            }
        elif scenario == 'vfa_logical_error_response': # New scenario
            return {
                "error_code": "VFA_TEST_LOGICAL_ERROR",
                "message": "Simulated VFA logical error from test scenario",
                "details": "This is a logical error simulated via X-Test-Scenario.",
                "audio_filepath": None,
                "stream_id": stream_id,
                "script_char_count": len(str(script_input)),
                "engine_used": "test_mode_vfa_logical_error",
                "tts_settings_used": used_tts_settings
            }

        # For default success and file_save_error, we attempt to create the directory and dummy file.
        try:
            os.makedirs(shared_audio_dir, exist_ok=True)
            file_extension = f".{used_tts_settings['audio_encoding'].lower()}" if used_tts_settings['audio_encoding'] else ".mp3"
            dummy_filename = f"aethercast_audio_testmode_{stream_id}_{uuid.uuid4().hex[:6]}{file_extension}"
            dummy_filepath = os.path.join(shared_audio_dir, dummy_filename)

            if scenario == 'vfa_error_file_save':
                # Don't actually write the file, or simulate write failure after this block
                logger.info(f"[VFA_MAIN_LOGIC] Test mode (vfa_error_file_save): Simulating file save error for path {dummy_filepath}")
                return {
                    "error_code": "VFA_TEST_MODE_FILE_SAVE_ERROR",
                    "message": "Simulated file saving IO error in VFA (Test Mode).",
                    "details": VFA_TEST_SCENARIO_FILE_SAVE_ERROR_MSG,
                    "audio_filepath": dummy_filepath, # Filepath might be determined but saving fails
                    "stream_id": stream_id,
                    "script_char_count": len(str(script_input)), "engine_used": "test_mode_tts_file_error",
                    "tts_settings_used": used_tts_settings
                }

            # Default success scenario
            with open(dummy_filepath, "wb") as f:
                f.write(b"ID3\x03\x00\x00\x00\x00\x0fThis is a test MP3 file.")
            logger.info(f"[VFA_MAIN_LOGIC] Test mode (default): Created dummy audio file at {dummy_filepath}")
            return {
                "status": "success",
                "message": "Audio successfully synthesized (TEST MODE - dummy file).",
                "audio_filepath": dummy_filepath,
                "stream_id": stream_id,
                "audio_format": used_tts_settings['audio_encoding'].lower(),
                "script_char_count": len(str(script_input)),
                "engine_used": "test_mode_tts_success",
                "tts_settings_used": used_tts_settings
            }
        except IOError as e: # Covers makedirs error or error during open() for default success
            logger.error(f"[VFA_MAIN_LOGIC] Test mode: Failed during directory/file operation: {e}")
            return {
                "error_code": "VFA_TEST_MODE_IO_ERROR",
                "message": "Test mode failed during disk operation.",
                "details": f"Test mode failed during disk op: {str(e)}", # Make sure e is string
                "audio_filepath": None, "stream_id": stream_id,
                "script_char_count": 0, "engine_used": "test_mode_tts_io_error",
                "tts_settings_used": used_tts_settings
            }

    text_to_synthesize = ""

    if not isinstance(script_input, dict):
        logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: Received script_input is not a dictionary. Input type: {type(script_input)}")
        text_to_synthesize = str(script_input)
    else:
        original_topic = script_input.get("topic", original_topic)
        full_raw_script = script_input.get("full_raw_script", "")
        if any(full_raw_script.startswith(prefix) for prefix in PSWA_ERROR_PREFIXES):
            message = f"Script for topic '{original_topic}' appears to be an error message from PSWA, audio generation skipped."
            logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: {message} Raw Script: '{full_raw_script[:100]}...'")
            return {
                "status": "skipped", "message": message, "audio_filepath": None, "stream_id": stream_id,
                "script_char_count": len(full_raw_script), "engine_used": "google_cloud_tts",
                "tts_settings_used": None
            }
        segments = script_input.get("segments", [])
        if segments:
            tts_parts = []
            podcast_title_from_script = script_input.get("title", original_topic)
            if podcast_title_from_script and not podcast_title_from_script.startswith("Error: Insufficient Content"):
                 tts_parts.append(f"{podcast_title_from_script}.")
            for segment in segments:
                seg_title = segment.get("segment_title", "")
                seg_content = segment.get("content", "")
                if seg_title and seg_title not in ["INTRO", "OUTRO", "ERROR"]:
                    tts_parts.append(f"{seg_title}.")
                if seg_content:
                    tts_parts.append(seg_content)
            text_to_synthesize = "\n\n".join(tts_parts)
            logger.info(f"[VFA_TTS_LOGIC] Stream {stream_id}: Extracted text for TTS from structured script. Topic: '{original_topic}'.")
        elif full_raw_script:
            logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: No segments found for topic '{original_topic}'. Falling back to 'full_raw_script'.")
            text_to_synthesize = full_raw_script
        else:
            logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: No usable text in script_input for topic '{original_topic}'.")
            return {
                "error_code": "VFA_SCRIPT_ERROR_NO_TEXT",
                "message": "Script does not contain usable text for synthesis.",
                "details": "Invalid script structure: Missing text content.",
                "audio_filepath": None, "stream_id": stream_id,
                "script_char_count": 0, "engine_used": "google_cloud_tts", "tts_settings_used": None
            }

    script_char_count = len(text_to_synthesize)
    logger.info(f"[VFA_TTS_LOGIC] Stream {stream_id}. Topic: '{original_topic}'. Effective script char count for TTS: {script_char_count}")


    shared_audio_dir = vfa_config.get('VFA_SHARED_AUDIO_DIR')
    min_script_length = vfa_config.get('VFA_MIN_SCRIPT_LENGTH')
    current_audio_encoding_enum = get_current_audio_encoding()

    if not VFA_IMPORTS_SUCCESSFUL:
        error_msg = f"Google Cloud Text-to-Speech library not available. {VFA_MISSING_IMPORT_ERROR}"
        logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: {error_msg}")
        return {
            "error_code": "VFA_IMPORT_ERROR_GOOGLE_SDK",
            "message": "Google Cloud Text-to-Speech library not available.",
            "details": error_msg,
            "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts_unavailable",
            "tts_settings_used": used_tts_settings
        }

    if not vfa_config.get("GOOGLE_APPLICATION_CREDENTIALS"):
        error_msg = "Error: GOOGLE_APPLICATION_CREDENTIALS environment variable not set."
        logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id} (Topic: {original_topic}): {error_msg}")
        return {
            "error_code": "VFA_CONFIG_ERROR_NO_CREDENTIALS",
            "message": "Google Cloud TTS credentials are not set.",
            "details": error_msg,
            "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts_no_credentials",
            "tts_settings_used": used_tts_settings
        }
    
    if not text_to_synthesize or script_char_count < min_script_length:
        message = f"Text to synthesize is too short (length {script_char_count} < {min_script_length} chars) for topic '{original_topic}', audio generation skipped."
        logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: {message}")
        return {
            "status": "skipped", "message": message, "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts",
            "tts_settings_used": used_tts_settings
        }

    try:
        # Specific try-except for os.makedirs
        try:
            os.makedirs(shared_audio_dir, exist_ok=True)
            logger.info(f"[VFA_TTS_LOGIC] Stream {stream_id}: Ensured shared audio directory exists: {shared_audio_dir}")
        except IOError as e_mkdir:
            error_msg = f"VFA failed to create output directory {shared_audio_dir}: {str(e_mkdir)}"
            logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: {error_msg}", exc_info=True)
            return {
                "error_code": "VFA_FILE_SYSTEM_ERROR_MKDIR",
                "message": "VFA failed to create output directory.",
                "details": str(e_mkdir),
                "audio_filepath": None, "stream_id": stream_id,
                "script_char_count": script_char_count, "engine_used": "google_cloud_tts", # Assuming this path is for google_cloud_tts
                "tts_settings_used": used_tts_settings
            }

        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text_to_synthesize)

        voice = texttospeech.VoiceSelectionParams(
            language_code=used_tts_settings["language_code"],
            name=used_tts_settings["voice_name"]
        )
        # Ensure speaking_rate and pitch are within valid Google TTS ranges if provided
        # Google TTS speaking_rate: [0.25, 4.0], pitch: [-20.0, 20.0]
        speaking_rate = max(0.25, min(used_tts_settings["speaking_rate"], 4.0))
        pitch = max(-20.0, min(used_tts_settings["pitch"], 20.0))

        if speaking_rate != used_tts_settings["speaking_rate"]:
            logger.warning(f"Requested speaking_rate {used_tts_settings['speaking_rate']} out of range. Clamped to {speaking_rate}.")
            used_tts_settings["speaking_rate_adjusted"] = speaking_rate # Log adjusted value
        if pitch != used_tts_settings["pitch"]:
            logger.warning(f"Requested pitch {used_tts_settings['pitch']} out of range. Clamped to {pitch}.")
            used_tts_settings["pitch_adjusted"] = pitch # Log adjusted value


        audio_config = texttospeech.AudioConfig(
            audio_encoding=current_audio_encoding_enum,
            speaking_rate=speaking_rate,
            pitch=pitch
        )

        logger.info(f"[VFA_TTS_LOGIC] Requesting audio synthesis. Settings Used: {used_tts_settings}")
        response = client.synthesize_speech(
            request={"input": synthesis_input, "voice": voice, "audio_config": audio_config}
        )
        logger.info(f"[VFA_TTS_LOGIC] Stream {stream_id}: Audio synthesis successful from Google Cloud TTS.")

        file_extension = ".mp3"
        if current_audio_encoding_enum == texttospeech.AudioEncoding.LINEAR16:
            file_extension = ".wav"
        elif current_audio_encoding_enum == texttospeech.AudioEncoding.OGG_OPUS:
            file_extension = ".ogg"
        
        filename = f"aethercast_audio_{stream_id}_{uuid.uuid4().hex}{file_extension}"
        filepath = os.path.join(shared_audio_dir, filename)

        # Specific try-except for file open/write
        try:
            with open(filepath, "wb") as out_file:
                out_file.write(response.audio_content)
            logger.info(f"[VFA_TTS_LOGIC] Stream {stream_id}: Audio content written to file: {filepath}")
        except IOError as e_write:
            error_msg = f"VFA failed to write synthesized audio to file {filepath}: {str(e_write)}"
            logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: {error_msg}", exc_info=True)
            return {
                "error_code": "VFA_FILE_SYSTEM_ERROR_WRITE_AUDIO",
                "message": "VFA failed to write synthesized audio to file.",
                "details": str(e_write),
                "audio_filepath": filepath, # Filepath was determined, but write failed
                "stream_id": stream_id,
                "script_char_count": script_char_count, "engine_used": "google_cloud_tts",
                "tts_settings_used": used_tts_settings
            }
        
        return {
            "status": "success",
            "message": "Audio successfully synthesized and saved to shared directory.",
            "audio_filepath": filepath,
            "stream_id": stream_id,
            "audio_format": vfa_config.get('VFA_TTS_AUDIO_ENCODING_STR').lower(),
            "script_char_count": script_char_count,
            "engine_used": "google_cloud_tts",
            "tts_settings_used": used_tts_settings
        }

    except google_exceptions.GoogleAPIError as e:
        error_msg = f"Google TTS API Error: {type(e).__name__} - {str(e)}" # str(e) for details
        logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: {error_msg}", exc_info=True)
        return {
            "error_code": "VFA_TTS_API_ERROR",
            "message": "An error occurred with the Google TTS API.",
            "details": error_msg,
            "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts",
            "tts_settings_used": used_tts_settings
        }
    except Exception as e:
        error_msg = f"Unexpected error during TTS synthesis or file saving: {type(e).__name__} - {str(e)}" # str(e) for details
        logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: {error_msg}", exc_info=True)
        return {
            "error_code": "VFA_UNEXPECTED_ERROR",
            "message": "An unexpected error occurred during voice forging.",
            "details": error_msg,
            "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts",
            "tts_settings_used": used_tts_settings
        }

# --- Flask Endpoint ---
@app.route('/forge_voice', methods=['POST'])
def handle_forge_voice():
    logger.info("[VFA_FLASK_ENDPOINT] Received request for /forge_voice")
    data = request.get_json()

    if not data:
        logger.error("[VFA_FLASK_ENDPOINT] No JSON payload received.")
        return jsonify({"status": "error", "message": "No JSON payload received"}), 400

    script_payload = data.get('script')
    voice_params_payload = data.get('voice_params') # Optional

    if script_payload is None:
        logger.error("[VFA_FLASK_ENDPOINT] 'script' parameter missing from JSON payload.")
        return jsonify({"status": "error", "message": "Missing 'script' parameter"}), 400

    if not isinstance(script_payload, dict):
        logger.error(f"[VFA_FLASK_ENDPOINT] 'script' parameter is not a dictionary (type: {type(script_payload)}). Payload: {str(script_payload)[:200]}")
        return jsonify({"status": "error", "message": "'script' parameter must be a valid JSON object (dictionary)."}), 400

    if voice_params_payload is not None and not isinstance(voice_params_payload, dict):
        logger.error(f"[VFA_FLASK_ENDPOINT] 'voice_params' parameter, if provided, must be a dictionary. Type: {type(voice_params_payload)}")
        return jsonify({"status": "error", "message": "'voice_params' parameter must be a valid JSON object if provided."}), 400

    logger.info(f"[VFA_FLASK_ENDPOINT] Calling forge_voice with script data for topic: '{script_payload.get('topic', 'N/A')}' and voice_params: {voice_params_payload}")
    result = forge_voice(script_payload, voice_params_input=voice_params_payload)

    status_code = 500 # Default for errors
    if "error_code" in result: # This is the new way to check for an error from forge_voice
        logger.error(f"[VFA_FLASK_ENDPOINT] forge_voice returned error: {result.get('error_code')} - {result.get('message')}")
        # Potentially map specific VFA error_codes to HTTP status codes if needed
        if result.get("error_code") == "VFA_SCRIPT_ERROR_NO_TEXT":
            status_code = 400 # Bad Request for script errors
        elif result.get("error_code") == "VFA_CONFIG_ERROR_NO_CREDENTIALS":
            status_code = 503 # Service Unavailable
        else:
            status_code = 500 # Default for other VFA errors
    elif result.get("status") == "success": # "status" key still used for success/skipped
        status_code = 200
        logger.info(f"[VFA_FLASK_ENDPOINT] forge_voice returned success: {result.get('message')}")
    elif result.get("status") == "skipped":
        status_code = 200
        logger.warning(f"[VFA_FLASK_ENDPOINT] forge_voice returned skipped: {result.get('message')}")
    # else: remains 500 if status is missing and no error_code (should ideally not happen with new structure)

    return jsonify(result), status_code


if __name__ == "__main__":
    # Start Flask app using configured values
    host = vfa_config.get("VFA_HOST", "0.0.0.0")
    port = vfa_config.get("VFA_PORT", 5005)
    debug_mode = vfa_config.get("VFA_DEBUG_MODE", True)

    print(f"\n--- VFA Service starting on {host}:{port} (Debug: {debug_mode}) ---")
    if not vfa_config.get("GOOGLE_APPLICATION_CREDENTIALS") and VFA_IMPORTS_SUCCESSFUL :
        print("WARNING: GOOGLE_APPLICATION_CREDENTIALS is not set. Real TTS calls will fail.")
        print("The service will run, but expect errors if TTS is attempted without credentials.")

    app.run(host=host, port=port, debug=debug_mode)

    # Original CLI test logic (can be commented out or removed if Flask is the sole interface)
    # print("--- Testing VoiceForgeAgent (VFA) with Google Cloud TTS (CLI) ---")
    # # Check for import success and credentials first
    # if not VFA_IMPORTS_SUCCESSFUL:
    #     print(f"\nSKIPPING TESTS: Google Cloud Text-to-Speech library not available. {VFA_MISSING_IMPORT_ERROR}")
    # elif not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
    #     print("\nSKIPPING REAL TTS CALLS: GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")
    #     print("You can still see the output for skipped/error cases based on script content.")
    #     # Test a case that doesn't require API call but uses the new logic
    #     print("\n--- Test: PSWA Error Script ---")
    #     pswa_error_script = "OpenAI API Error: Rate limit exceeded."
    #     print(f"Input script (PSWA error): \"{pswa_error_script}\"")
    #     result_pswa_error = forge_voice(script=pswa_error_script) # This needs to be dict
    #     print(f"Result: {result_pswa_error}\n")

    #     print("\n--- Test: Short Script ---")
    #     short_script = "Too short."
    #     print(f"Input script (short): \"{short_script}\"")
    #     result_short = forge_voice(script=short_script) # This needs to be dict
    #     print(f"Result: {result_short}\n")
    # else:
    #     print("\nGOOGLE_APPLICATION_CREDENTIALS found. Attempting REAL TTS calls.")
        
    #     # Example 1: Valid, reasonably long script (needs to be structured_script dict)
    #     sample_script_dict_1 = {
    #         "script_id": "s_cli1", "topic": "CLI Test", "title": "CLI Test Title",
    #         "full_raw_script": "CLI full script",
    #         "segments": [
    #             {"segment_title": "INTRO", "content": "Hello from Aethercast VFA CLI test."},
    #             {"segment_title": "Main Point", "content": "This demonstrates voice parameter passing."}
    #         ]
    #     }
    #     print(f"\n--- Test 1: Standard Valid Script ---")
    #     result_1 = forge_voice(script_input=sample_script_dict_1, voice_params_input={"speaking_rate": 1.1, "pitch": -1.0})
    #     print(f"Result 1: {result_1}\n")
    #     if result_1.get("status") == "success":
    #         print(f"SUCCESS: Audio file should be at: {result_1.get('audio_filepath')}")
    #         print(f"TTS Settings Used: {result_1.get('tts_settings_used')}")
    #     else:
    #         print(f"ERROR/Skipped: {result_1.get('message')}")
    # print("\n--- VFA Google Cloud TTS integration testing complete (CLI) ---")
