import os
import uuid
import logging
import json # Added for potential error payload creation
from flask import Flask, jsonify, request
from dotenv import load_dotenv
from google.cloud import texttospeech
from google.api_core import exceptions as google_exceptions

# --- Load Environment Variables ---
load_dotenv()

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
if app.logger and app.logger.name != 'root':
    logger = app.logger
else:
    logger = logging.getLogger(__name__)
    if not logger.hasHandlers():
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - AIMS_TTS - %(message)s')

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
SHARED_AUDIO_DIR_CONTAINER = os.getenv('SHARED_AUDIO_DIR_CONTAINER', '/shared_audio/aims_tts') # Ensure this path is writable

logger.info("--- AIMS_TTS Service Configuration ---")
logger.info(f"  AIMS_TTS_HOST: {AIMS_TTS_HOST}")
logger.info(f"  AIMS_TTS_PORT: {AIMS_TTS_PORT}")
logger.info(f"  FLASK_DEBUG: {FLASK_DEBUG}")
logger.info(f"  GOOGLE_APPLICATION_CREDENTIALS: {'Set (path not logged)' if GOOGLE_APPLICATION_CREDENTIALS else 'Not Set'}")
logger.info(f"  AIMS_TTS_DEFAULT_VOICE_ID: {AIMS_TTS_DEFAULT_VOICE_ID}")
logger.info(f"  AIMS_TTS_DEFAULT_LANGUAGE_CODE: {AIMS_TTS_DEFAULT_LANGUAGE_CODE}")
logger.info(f"  AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR: {AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR}")
logger.info(f"  AIMS_TTS_DEFAULT_SPEAKING_RATE: {AIMS_TTS_DEFAULT_SPEAKING_RATE}")
logger.info(f"  AIMS_TTS_DEFAULT_PITCH: {AIMS_TTS_DEFAULT_PITCH}")
logger.info(f"  SHARED_AUDIO_DIR_CONTAINER: {SHARED_AUDIO_DIR_CONTAINER}")
logger.info("--- End AIMS_TTS Service Configuration ---")

if not GOOGLE_APPLICATION_CREDENTIALS:
    logger.critical("CRITICAL: GOOGLE_APPLICATION_CREDENTIALS is not set. Real TTS calls will fail. Application cannot start.")
    raise ValueError("AIMS_TTS Critical Error: GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")

# --- Audio Encoding Mapping ---
AUDIO_ENCODING_MAP = {
    "MP3": texttospeech.AudioEncoding.MP3,
    "LINEAR16": texttospeech.AudioEncoding.LINEAR16,
    "OGG_OPUS": texttospeech.AudioEncoding.OGG_OPUS,
    # Add other encodings as needed
}
DEFAULT_AUDIO_ENCODING_ENUM = AUDIO_ENCODING_MAP.get(AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR, texttospeech.AudioEncoding.MP3)

# --- Helper to estimate duration (simple version) ---
def estimate_audio_duration(text_length: int, rate: float = 1.0) -> float:
    # Very rough estimate: average 15 characters per second at normal rate (1.0)
    # Adjust based on rate. Faster rate = shorter duration.
    chars_per_second_at_normal_rate = 15
    estimated_duration = (text_length / chars_per_second_at_normal_rate) / rate
    return round(max(0.5, estimated_duration), 2) # Ensure at least 0.5s

@app.route('/v1/synthesize', methods=['POST'])
def synthesize_speech():
    request_id = f"aims-tts-req-{uuid.uuid4().hex}"
    logger.info(f"Request {request_id}: Received /v1/synthesize request.")

    if not GOOGLE_APPLICATION_CREDENTIALS:
        logger.error(f"Request {request_id}: Service not configured. GOOGLE_APPLICATION_CREDENTIALS is missing.")
        return jsonify({
            "request_id": request_id,
            "error": {"type": "configuration_error", "message": "TTS service not configured by administrator."}
        }), 503

    try:
        data = request.get_json()
        if not data:
            logger.warning(f"Request {request_id}: No JSON payload received.")
            return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "No JSON payload received."}}), 400
    except Exception as e:
        logger.warning(f"Request {request_id}: Error parsing JSON payload: {e}")
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"Invalid JSON payload: {str(e)}"}}), 400

    text_to_synthesize = data.get("text")
    if not text_to_synthesize:
        logger.warning(f"Request {request_id}: Missing 'text' in request payload.")
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "Missing 'text' in request payload."}}), 400

    voice_id = data.get("voice_id", AIMS_TTS_DEFAULT_VOICE_ID)
    language_code = data.get("language_code", AIMS_TTS_DEFAULT_LANGUAGE_CODE) # Assuming voice_id implies language, or allow separate
    output_format_str = data.get("audio_format", AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR).upper()
    speech_rate = float(data.get("speech_rate", AIMS_TTS_DEFAULT_SPEAKING_RATE))
    pitch = float(data.get("pitch", AIMS_TTS_DEFAULT_PITCH))
    # response_type = data.get("response_type", "url") # For now, only "url" is implemented

    # Validate and clamp parameters
    speech_rate = max(0.25, min(speech_rate, 4.0))
    pitch = max(-20.0, min(pitch, 20.0))

    selected_audio_encoding_enum = AUDIO_ENCODING_MAP.get(output_format_str, DEFAULT_AUDIO_ENCODING_ENUM)
    file_extension = output_format_str.lower() if output_format_str in AUDIO_ENCODING_MAP else AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR.lower()


    try:
        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text_to_synthesize)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            name=voice_id
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=selected_audio_encoding_enum,
            speaking_rate=speech_rate,
            pitch=pitch
        )

        logger.info(f"Request {request_id}: Calling Google TTS. Voice: {voice_id}, Lang: {language_code}, Rate: {speech_rate}, Pitch: {pitch}, Format: {output_format_str}")
        tts_response = client.synthesize_speech(
            request={"input": synthesis_input, "voice": voice_params, "audio_config": audio_config}
        )

        # Ensure shared audio directory exists
        os.makedirs(SHARED_AUDIO_DIR_CONTAINER, exist_ok=True)

        filename = f"{request_id}_{uuid.uuid4().hex[:8]}.{file_extension}"
        filepath_in_container = os.path.join(SHARED_AUDIO_DIR_CONTAINER, filename)

        with open(filepath_in_container, "wb") as out_file:
            out_file.write(tts_response.audio_content)
        logger.info(f"Request {request_id}: Audio content successfully written to: {filepath_in_container}")

        # Construct audio_url - for now, this is a path within the shared volume.
        # How this is accessed by other services (like VFA) depends on how volumes are mapped.
        # If VFA maps the same SHARED_AUDIO_DIR_CONTAINER to its own path, it can use this.
        # A true "URL" might involve another service to serve these files.
        audio_url = filepath_in_container

        estimated_duration = estimate_audio_duration(len(text_to_synthesize), speech_rate)

        response_data = {
            "request_id": request_id,
            "voice_id": voice_id,
            "audio_url": audio_url,
            "audio_duration_seconds": estimated_duration,
            "audio_format": file_extension
        }
        return jsonify(response_data), 200

    except google_exceptions.GoogleAPIError as e:
        logger.error(f"Request {request_id}: Google TTS API Error: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "tts_failure", "message": f"Google TTS API error: {str(e)}" }}), 500
    except IOError as e:
        logger.error(f"Request {request_id}: File system I/O Error: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "file_system_error", "message": f"Could not save audio file: {str(e)}" }}), 500
    except Exception as e:
        logger.error(f"Request {request_id}: Unexpected error during TTS synthesis: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "internal_server_error", "message": f"An unexpected error occurred: {str(e)}" }}), 500

if __name__ == '__main__':
    if not GOOGLE_APPLICATION_CREDENTIALS:
        logger.warning("WARNING: GOOGLE_APPLICATION_CREDENTIALS is not set. TTS calls will fail if attempted.")

    # Create shared audio directory if it doesn't exist at startup (best effort)
    try:
        os.makedirs(SHARED_AUDIO_DIR_CONTAINER, exist_ok=True)
        logger.info(f"Ensured shared audio directory exists: {SHARED_AUDIO_DIR_CONTAINER}")
    except OSError as e:
        logger.error(f"Could not create shared audio directory {SHARED_AUDIO_DIR_CONTAINER} on startup: {e}")

    logger.info(f"--- AIMS_TTS Service starting on {AIMS_TTS_HOST}:{AIMS_TTS_PORT} (Debug: {FLASK_DEBUG}) ---")
    app.run(host=AIMS_TTS_HOST, port=AIMS_TTS_PORT, debug=FLASK_DEBUG)
