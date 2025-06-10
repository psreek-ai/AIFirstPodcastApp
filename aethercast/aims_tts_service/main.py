import os
import uuid
import logging
import json
from flask import Flask, jsonify, request
from dotenv import load_dotenv
from google.cloud import texttospeech
from google.cloud import storage # Added for GCS
from google.api_core import exceptions as google_exceptions

# --- Load Environment Variables ---
load_dotenv()

import time # Added for metric logging

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
from python_json_logger import jsonlogger # Added for JSON logging

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
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s",
        rename_fields={"levelname": "level", "name": "logger_name", "asctime": "timestamp"}
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("JSON logging configured for AIMS_TTS service.")

setup_json_logging(app)

# Make the global logger use the configured app.logger
logger = app.logger

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

@app.route('/v1/synthesize', methods=['POST'])
def synthesize_speech():
    request_start_time = time.time()
    request_id = f"aims-tts-req-{uuid.uuid4().hex}"
    final_status_str = "unknown_error" # Default status for request count metric
    # Initialize tags for the final request_count log, to be updated after parsing
    voice_id_for_status_tag = "unknown"
    output_format_str_for_status_tag = "unknown"

    logger.info(f"Request {request_id}: Received /v1/synthesize request.")

    if not GOOGLE_APPLICATION_CREDENTIALS or not GCS_BUCKET_NAME:
        logger.error(f"Request {request_id}: Service not configured. GOOGLE_APPLICATION_CREDENTIALS or GCS_BUCKET_NAME is missing.")
        final_status_str = "config_error"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "configuration_error", "message": "TTS service not fully configured."}}), 503

    try:
        data = request.get_json()
        if not data:
            final_status_str = "validation_error_no_payload"
            logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
            return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "No JSON payload."}}), 400
    except Exception as e:
        final_status_str = "validation_error_bad_json"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"Invalid JSON: {e}"}}), 400

    text_to_synthesize = data.get("text")
    if not text_to_synthesize or not isinstance(text_to_synthesize, str) or not text_to_synthesize.strip():
        final_status_str = "validation_error_text"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "'text' must be non-empty string."}}), 400

    TEXT_MAX_LENGTH = 5000
    if len(text_to_synthesize) > TEXT_MAX_LENGTH:
        final_status_str = "validation_error_text_length"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"'text' exceeds max length {TEXT_MAX_LENGTH}."}}), 400

    voice_id = data.get("voice_id", AIMS_TTS_DEFAULT_VOICE_ID)
    voice_id_for_status_tag = voice_id # Update for final log
    if data.get("voice_id") is not None and not isinstance(data.get("voice_id"), str):
        final_status_str = "validation_error_voice_id"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "'voice_id' must be string."}}), 400

    language_code = data.get("language_code", AIMS_TTS_DEFAULT_LANGUAGE_CODE)
    if data.get("language_code") is not None and (not isinstance(data.get("language_code"), str) or not data.get("language_code").strip()):
        final_status_str = "validation_error_lang_code"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": "'language_code' must be non-empty string."}}), 400

    output_format_str = data.get("audio_format", AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR).upper()
    output_format_str_for_status_tag = output_format_str # Update for final log
    if output_format_str not in AUDIO_ENCODING_MAP:
        final_status_str = "validation_error_audio_format"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"Unsupported audio_format. Supported: {list(AUDIO_ENCODING_MAP.keys())}"}}), 400

    try:
        speech_rate = float(data.get("speech_rate", AIMS_TTS_DEFAULT_SPEAKING_RATE))
        pitch = float(data.get("pitch", AIMS_TTS_DEFAULT_PITCH))
    except ValueError as ve:
        final_status_str = "validation_error_rate_pitch"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"Invalid speech_rate or pitch: {ve}"}}), 400

    speech_rate = max(0.25, min(speech_rate, 4.0))
    pitch = max(-20.0, min(pitch, 20.0))

    selected_audio_encoding_details = AUDIO_ENCODING_MAP[output_format_str]
    file_extension = output_format_str.lower()

    try:
        # Synthesize Speech with Google TTS
        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text_to_synthesize)
        voice_params = texttospeech.VoiceSelectionParams(language_code=language_code, name=voice_id)
        audio_config = texttospeech.AudioConfig(
            audio_encoding=selected_audio_encoding_details["enum"],
            speaking_rate=speech_rate,
            pitch=pitch
        )
        logger.info(f"Request {request_id}: Calling Google TTS. Voice: {voice_id}, Lang: {language_code}, Rate: {speech_rate}, Pitch: {pitch}, Format: {output_format_str}")

        gcp_tts_call_start_time = time.time()
        tts_response = client.synthesize_speech(request={"input": synthesis_input, "voice": voice_params, "audio_config": audio_config})
        gcp_tts_call_duration_ms = (time.time() - gcp_tts_call_start_time) * 1000
        logger.info("AIMS_TTS GCP TTS call processed", extra=dict(metric_name="aims_tts_gcp_tts_call_latency_ms", value=round(gcp_tts_call_duration_ms, 2), tags={"voice_id_used": voice_id}))

        # Upload to GCS
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)

        gcs_object_name = f"{AIMS_TTS_GCS_AUDIO_PREFIX}{request_id}_{uuid.uuid4().hex[:8]}.{file_extension}"
        blob = bucket.blob(gcs_object_name)

        # Determine content_type for GCS upload
        # For LINEAR16, the sample rate might be needed if not default. Google TTS usually defaults to 24kHz for LINEAR16.
        # The mimetype in AUDIO_ENCODING_MAP can be used directly.
        gcs_content_type = selected_audio_encoding_details["mimetype"]
        if output_format_str == "LINEAR16" and "rate=" not in gcs_content_type: # Add rate if not specified for LINEAR16
             # Assuming tts_response.audio_config.sample_rate_hertz exists and is populated by Google SDK,
             # otherwise, use a known default like 24000.
             sample_rate = tts_response.audio_config.sample_rate_hertz if hasattr(tts_response, 'audio_config') and tts_response.audio_config.sample_rate_hertz else 24000
             gcs_content_type = f"audio/L16; rate={sample_rate}"


        logger.info(f"Request {request_id}: Uploading to GCS. Bucket: {GCS_BUCKET_NAME}, Object: {gcs_object_name}, Content-Type: {gcs_content_type}")
        gcs_upload_start_time = time.time()
        blob.upload_from_string(tts_response.audio_content, content_type=gcs_content_type)
        gcs_upload_duration_ms = (time.time() - gcs_upload_start_time) * 1000
        logger.info("AIMS_TTS GCS upload processed", extra=dict(metric_name="aims_tts_gcs_upload_latency_ms", value=round(gcs_upload_duration_ms, 2)))
        logger.info(f"Request {request_id}: Successfully uploaded audio to GCS: gs://{GCS_BUCKET_NAME}/{gcs_object_name}")

        audio_gcs_uri = f"gs://{GCS_BUCKET_NAME}/{gcs_object_name}"
        estimated_duration = estimate_audio_duration(len(text_to_synthesize), speech_rate)

        logger.info("AIMS_TTS characters synthesized", extra=dict(metric_name="aims_tts_synthesized_chars_count", value=len(text_to_synthesize), tags={"voice_id_used": voice_id}))

        response_data = {
            "request_id": request_id,
            "voice_id": voice_id,
            "audio_url": audio_gcs_uri,
            "audio_duration_seconds": estimated_duration,
            "audio_format": file_extension
        }

        overall_latency_ms = (time.time() - request_start_time) * 1000
        logger.info("AIMS_TTS request processed", extra=dict(metric_name="aims_tts_request_latency_ms", value=round(overall_latency_ms, 2), tags={"voice_id_requested": voice_id, "audio_format_requested": output_format_str}))
        final_status_str = "success"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify(response_data), 200

    except google_exceptions.GoogleAPIError as e:
        logger.error(f"Request {request_id}: Google Cloud API Error (TTS or GCS): {e}", exc_info=True)
        error_type_tag = "gcp_api_error" # Default tag for general GCP API errors
        is_gcs_error = False
        if "storage.googleapis.com" in str(e).lower() or isinstance(e, (google_exceptions.NotFound, google_exceptions.Forbidden)): # Heuristic for GCS
             is_gcs_error = True
             if isinstance(e, google_exceptions.NotFound): error_type_tag = "gcs_bucket_not_found"
             elif isinstance(e, google_exceptions.Forbidden): error_type_tag = "gcs_permission_denied"
             else: error_type_tag = "gcs_other_error"
             logger.error("AIMS_TTS GCS upload error", extra=dict(metric_name="aims_tts_gcs_upload_failure_count", value=1, tags={"error_detail": str(e)[:100]})) # Log GCS specific failure
             final_status_str = f"gcs_upload_error_{error_type_tag}"
        else: # Assume TTS error
             logger.error("AIMS_TTS GCP TTS API error", extra=dict(metric_name="aims_tts_gcp_error_count", value=1, tags={"error_type": "gcp_api_error_tts"}))
             final_status_str = "gcp_tts_error"

        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": error_type_tag, "message": f"Google Cloud API error: {str(e)}" }}), 500
    except IOError as e:
        logger.error(f"Request {request_id}: File system I/O Error: {e}", exc_info=True)
        final_status_str = "io_error"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "file_system_error", "message": f"I/O error: {str(e)}" }}), 500
    except Exception as e:
        logger.error(f"Request {request_id}: Unexpected error during TTS/GCS processing: {e}", exc_info=True)
        # final_status_str is already "unknown_error"
        logger.info("AIMS_TTS request completed", extra=dict(metric_name="aims_tts_request_count", value=1, tags={"voice_id_requested": voice_id_for_status_tag, "audio_format_requested": output_format_str_for_status_tag, "status": final_status_str}))
        return jsonify({"request_id": request_id, "error": {"type": "internal_server_error", "message": f"An unexpected error occurred: {str(e)}" }}), 500

if __name__ == '__main__':
    if not GOOGLE_APPLICATION_CREDENTIALS:
        logger.warning("WARNING: GOOGLE_APPLICATION_CREDENTIALS is not set. TTS & GCS calls might fail if ADC not configured.")
    if not GCS_BUCKET_NAME:
        logger.warning("WARNING: GCS_BUCKET_NAME is not set. Audio uploads will fail.")

    # Create temp audio directory if still used for temporary files (currently not in main flow)
    # try:
    #     os.makedirs(SHARED_AUDIO_DIR_CONTAINER, exist_ok=True)
    #     logger.info(f"Ensured shared audio directory exists (for temp files if used): {SHARED_AUDIO_DIR_CONTAINER}")
    # except OSError as e:
    #     logger.error(f"Could not create shared audio directory {SHARED_AUDIO_DIR_CONTAINER} on startup: {e}")

    logger.info(f"--- AIMS_TTS Service starting on {AIMS_TTS_HOST}:{AIMS_TTS_PORT} (Debug: {FLASK_DEBUG}) ---")
    app.run(host=AIMS_TTS_HOST, port=AIMS_TTS_PORT, debug=FLASK_DEBUG)

[end of aethercast/aims_tts_service/main.py]
