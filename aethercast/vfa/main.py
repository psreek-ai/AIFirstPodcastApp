import logging
import os
import uuid
from dotenv import load_dotenv # Added
from flask import Flask, request, jsonify

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

    vfa_config['VFA_HOST'] = os.getenv("VFA_HOST", "0.0.0.0")
    vfa_config['VFA_PORT'] = int(os.getenv("VFA_PORT", 5005))
    vfa_config['VFA_DEBUG'] = os.getenv("VFA_DEBUG", "True").lower() == "true"

    logger.info("--- VFA Configuration ---")
    for key, value in vfa_config.items():
        if key == "GOOGLE_APPLICATION_CREDENTIALS" and value:
            logger.info(f"  {key}: Loaded (Path: {value})")
        elif key == "GOOGLE_APPLICATION_CREDENTIALS" and not value:
            logger.warning(f"  {key}: NOT SET. Real TTS will fail.")
        else:
            logger.info(f"  {key}: {value}")
    logger.info("--- End VFA Configuration ---")

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


# PSWA Error Prefixes to identify scripts that are actually error messages
PSWA_ERROR_PREFIXES = (
    "OpenAI library not available", 
    "Error: OPENAI_API_KEY environment variable", # From PSWA config
    "Error: OPENAI_API_KEY is not configured.", # From PSWA config after changes
    "OpenAI API Error:", 
    "An unexpected error occurred during LLM call:",
    "[ERROR] Insufficient content provided"
)


def get_current_audio_encoding():
    """Returns the configured audio encoding enum member, defaulting to MP3 if not available or invalid."""
    if not VFA_IMPORTS_SUCCESSFUL:
        return PlaceholderAudioEncoding.MP3 # Fallback if SDK not loaded

    encoding_str = vfa_config.get('VFA_TTS_AUDIO_ENCODING_STR', "MP3")
    selected_encoding = google_audio_encoding_map.get(encoding_str)

    if selected_encoding is None: # Should have been caught by load_vfa_configuration, but as a safeguard
        logger.warning(f"Audio encoding '{encoding_str}' map failed or invalid during get_current_audio_encoding. Defaulting to MP3 enum.")
        return google_audio_encoding_map.get("MP3", texttospeech.AudioEncoding.MP3) # Final fallback
    return selected_encoding


def forge_voice(script_input: dict) -> dict: # Changed input from script: str to script_input: dict
    """
    Generates audio from a structured script dictionary using Google Cloud Text-to-Speech
    and saves it to a shared directory.
    Returns a dictionary with audio generation details, including a stream_id, or error information.
    """
    stream_id = f"strm_{uuid.uuid4().hex}" # Generate stream_id early
    text_to_synthesize = ""
    original_topic = "Unknown Topic" # Default if not found in script_input

    if not isinstance(script_input, dict):
        logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: Received script_input is not a dictionary. Attempting to process as raw string. Input type: {type(script_input)}")
        # This path handles backward compatibility or direct string tests, but logs a warning.
        text_to_synthesize = str(script_input) # Convert to string just in case
        # We don't have segment info here, so script_char_count will be based on the raw string.
    else:
        original_topic = script_input.get("topic", original_topic)
        # Check for PSWA error messages first using full_raw_script
        full_raw_script = script_input.get("full_raw_script", "")
        if any(full_raw_script.startswith(prefix) for prefix in PSWA_ERROR_PREFIXES):
            message = f"Script for topic '{original_topic}' appears to be an error message from PSWA, audio generation skipped."
            logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: {message} Raw Script: '{full_raw_script[:100]}...'")
            return {
                "status": "skipped", "message": message, "audio_filepath": None, "stream_id": stream_id,
                "script_char_count": len(full_raw_script), "engine_used": "google_cloud_tts"
            }

        segments = script_input.get("segments", [])
        if segments:
            # Concatenate content from all segments, ensuring title and content are joined.
            # Segments from PSWA should have 'segment_title' (which is the actual title string)
            # and 'content'.
            # We only want to synthesize the actual content parts.
            # The prompt for PSWA was: "[TITLE]Title\n[INTRO]Intro content\n[SEGMENT_1_TITLE]Seg1 Title\n[SEGMENT_1_CONTENT]Seg1 Content..."
            # The parser in PSWA now produces:
            # "segments": [ {"segment_title": "INTRO", "content": "..."}, {"segment_title": "Seg1 Title string", "content": "Seg1 Content string"}, ...]
            # So, we should synthesize the title (if it's part of the audible script, e.g. a segment title) and then the content.
            # For a podcast, typically you'd read out the main title, then intro, then segment titles and their content.
            # Let's synthesize: Title + Intro Content + Seg1 Title + Seg1 Content + ... + Outro Content

            tts_parts = []
            podcast_title_from_script = script_input.get("title", original_topic)
            if podcast_title_from_script and not podcast_title_from_script.startswith("Error: Insufficient Content"):
                 tts_parts.append(f"{podcast_title_from_script}.") # Announce the main title

            for segment in segments:
                # segment_title from PSWA's parser is the actual title string of the segment (e.g. "Personalized Learning")
                # or "INTRO", "OUTRO".
                seg_title = segment.get("segment_title", "")
                seg_content = segment.get("content", "")

                if seg_title and seg_title not in ["INTRO", "OUTRO"] and seg_title != "ERROR": # For named segments
                    tts_parts.append(f"{seg_title}.") # Announce segment title
                if seg_content:
                    tts_parts.append(seg_content)

            text_to_synthesize = "\n\n".join(tts_parts)
            logger.info(f"[VFA_TTS_LOGIC] Stream {stream_id}: Extracted text for TTS from structured script. Topic: '{original_topic}'.")
        elif full_raw_script: # Fallback to full_raw_script if no segments
            logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: No segments found in structured script for topic '{original_topic}'. Falling back to 'full_raw_script'.")
            text_to_synthesize = full_raw_script
        else:
            logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: No 'segments' or 'full_raw_script' found in script_input for topic '{original_topic}'. Cannot synthesize.")
            return {
                "status": "error", "message": "Invalid script structure: Missing segments and raw script.",
                "audio_filepath": None, "stream_id": stream_id,
                "script_char_count": 0, "engine_used": "google_cloud_tts"
            }

    script_char_count = len(text_to_synthesize)
    logger.info(f"[VFA_TTS_LOGIC] forge_voice called for stream_id: {stream_id}. Topic: '{original_topic}'. Effective script char count for TTS: {script_char_count}")

    # Use configurations from vfa_config
    shared_audio_dir = vfa_config.get('VFA_SHARED_AUDIO_DIR')
    tts_voice_name = vfa_config.get('VFA_TTS_VOICE_NAME')
    tts_lang_code = vfa_config.get('VFA_TTS_LANG_CODE')
    min_script_length = vfa_config.get('VFA_MIN_SCRIPT_LENGTH')
    current_audio_encoding_enum = get_current_audio_encoding()

    if not VFA_IMPORTS_SUCCESSFUL:
        error_msg = f"Google Cloud Text-to-Speech library not available. {VFA_MISSING_IMPORT_ERROR}"
        logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: {error_msg}")
        return {
            "status": "error", "message": error_msg, "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts_unavailable"
        }

    if not vfa_config.get("GOOGLE_APPLICATION_CREDENTIALS"):
        error_msg = "Error: GOOGLE_APPLICATION_CREDENTIALS environment variable not set."
        logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id} (Topic: {original_topic}): {error_msg}")
        return {
            "status": "error", "message": error_msg, "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts_no_credentials"
        }
    
    # The PSWA error check is now done above based on structured input.
    # This check is for the final text_to_synthesize.
    if not text_to_synthesize or script_char_count < min_script_length:
        message = f"Text to synthesize is too short (length {script_char_count} < {min_script_length} chars) for topic '{original_topic}', audio generation skipped."
        logger.warning(f"[VFA_TTS_LOGIC] Stream {stream_id}: {message}")
        return {
            "status": "skipped", "message": message, "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts"
        }

    # If text_to_synthesize is valid for TTS
    try:
        os.makedirs(shared_audio_dir, exist_ok=True)
        logger.info(f"[VFA_TTS_LOGIC] Stream {stream_id}: Ensured shared audio directory exists: {shared_audio_dir}")

        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text_to_synthesize)
        voice = texttospeech.VoiceSelectionParams(
            language_code=tts_lang_code, name=tts_voice_name
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=current_audio_encoding_enum
        )

        logger.info(f"[VFA_TTS_LOGIC] Requesting audio synthesis from Google Cloud TTS. Voice: {tts_voice_name}, Lang: {tts_lang_code}, Encoding: {vfa_config.get('VFA_TTS_AUDIO_ENCODING_STR')}")
        response = client.synthesize_speech(
            request={"input": synthesis_input, "voice": voice, "audio_config": audio_config}
        )
        logger.info(f"[VFA_TTS_LOGIC] Stream {stream_id}: Audio synthesis successful from Google Cloud TTS.")

        # Determine file extension based on encoding
        file_extension = ".mp3" # Default
        if current_audio_encoding_enum == texttospeech.AudioEncoding.LINEAR16:
            file_extension = ".wav"
        elif current_audio_encoding_enum == texttospeech.AudioEncoding.OGG_OPUS:
            file_extension = ".ogg"
        
        # Use stream_id in the filename for better traceability, along with UUID for uniqueness
        filename = f"aethercast_audio_{stream_id}_{uuid.uuid4().hex}{file_extension}"
        filepath = os.path.join(shared_audio_dir, filename)

        with open(filepath, "wb") as out_file:
            out_file.write(response.audio_content)
        
        logger.info(f"[VFA_TTS_LOGIC] Stream {stream_id}: Audio content written to file: {filepath}")

        return {
            "status": "success",
            "message": "Audio successfully synthesized and saved to shared directory.",
            "audio_filepath": filepath,
            "stream_id": stream_id,
            "audio_format": vfa_config.get('VFA_TTS_AUDIO_ENCODING_STR').lower(), # e.g., "mp3"
            "script_char_count": script_char_count,
            "engine_used": "google_cloud_tts"
        }

    except google_exceptions.GoogleAPIError as e:
        error_msg = f"Google TTS API Error: {type(e).__name__} - {e}"
        logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: {error_msg}", exc_info=True)
        return {
            "status": "error", "message": error_msg, "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts"
        }
    except Exception as e: # Catch other potential errors (file system, etc.)
        error_msg = f"Unexpected error during TTS synthesis or file saving: {type(e).__name__} - {e}"
        logger.error(f"[VFA_TTS_LOGIC] Stream {stream_id}: {error_msg}", exc_info=True)
        return {
            "status": "error", "message": error_msg, "audio_filepath": None, "stream_id": stream_id,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts"
        }

# --- Flask Endpoint ---
@app.route('/forge_voice', methods=['POST'])
def handle_forge_voice():
    logger.info("[VFA_FLASK_ENDPOINT] Received request for /forge_voice")
    data = request.get_json()

    if not data:
        logger.error("[VFA_FLASK_ENDPOINT] No JSON payload received.")
        return jsonify({"status": "error", "message": "No JSON payload received"}), 400

    script_payload = data.get('script') # This should be the structured script dict

    if script_payload is None:
        logger.error("[VFA_FLASK_ENDPOINT] 'script' parameter missing from JSON payload.")
        return jsonify({"status": "error", "message": "Missing 'script' parameter"}), 400

    if not isinstance(script_payload, dict):
        logger.error(f"[VFA_FLASK_ENDPOINT] 'script' parameter is not a dictionary (type: {type(script_payload)}). Payload: {str(script_payload)[:200]}")
        return jsonify({"status": "error", "message": "'script' parameter must be a valid JSON object (dictionary)."}), 400


    logger.info(f"[VFA_FLASK_ENDPOINT] Calling forge_voice with script data for topic: '{script_payload.get('topic', 'N/A')}'")
    result = forge_voice(script_payload) # Pass the whole dict

    status_code = 500 # Default for "error" status from forge_voice
    if result.get("status") == "success":
        status_code = 200
        logger.info(f"[VFA_FLASK_ENDPOINT] forge_voice returned success: {result.get('message')}")
    elif result.get("status") == "skipped":
        status_code = 200 # As per requirement, skipped is not a server error. Could also be 202.
        logger.warning(f"[VFA_FLASK_ENDPOINT] forge_voice returned skipped: {result.get('message')}")
    else: # Covers "error" status explicitly and any other unknown status
        logger.error(f"[VFA_FLASK_ENDPOINT] forge_voice returned error: {result.get('message')}")

    return jsonify(result), status_code


if __name__ == "__main__":
    # Start Flask app using configured values
    host = vfa_config.get("VFA_HOST", "0.0.0.0")
    port = vfa_config.get("VFA_PORT", 5005)
    debug_mode = vfa_config.get("VFA_DEBUG", True)

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
    #     result_pswa_error = forge_voice(script=pswa_error_script)
    #     print(f"Result: {result_pswa_error}\n")

    #     print("\n--- Test: Short Script ---")
    #     short_script = "Too short."
    #     print(f"Input script (short): \"{short_script}\"")
    #     result_short = forge_voice(script=short_script)
    #     print(f"Result: {result_short}\n")
    # else:
    #     print("\nGOOGLE_APPLICATION_CREDENTIALS found. Attempting REAL TTS calls.")
        
    #     # Example 1: Valid, reasonably long script
    #     sample_script_1 = (
    #         "Hello from Aethercast! This is a test of the Google Cloud Text-to-Speech integration. "
    #         "We are generating this audio as part of a test run for the Voice Forge Agent. "
    #         "Hopefully, this sounds natural and clear. Let's add a bit more text to make sure it's "
    #         "long enough for a proper synthesis and to avoid any minimum length issues. This should do it."
    #     )
    #     print(f"\n--- Test 1: Standard Valid Script (length: {len(sample_script_1)}) ---")
    #     print(f"Input script: \"{sample_script_1[:100]}...\"")
    #     result_1 = forge_voice(script=sample_script_1)
    #     print(f"Result 1: {result_1}\n")
    #     if result_1.get("status") == "success":
    #         print(f"SUCCESS: Audio file should be at: {result_1.get('audio_filepath')}")
    #     else:
    #         print(f"ERROR/Skipped: {result_1.get('message')}")

    #     # Example 2: Script that is an error message from PSWA
    #     sample_script_2 = "OpenAI API Error: The model is currently overloaded. Please try again later."
    #     print(f"\n--- Test 2: PSWA Error Script (length: {len(sample_script_2)}) ---")
    #     print(f"Input script: \"{sample_script_2}\"")
    #     result_2 = forge_voice(script=sample_script_2)
    #     print(f"Result 2: {result_2}\n")
    #     if result_2.get("status") == "skipped":
    #         print("CORRECTLY SKIPPED: VFA identified PSWA error string.")
    #     else:
    #         print(f"UNEXPECTED: {result_2}")


    #     # Example 3: Script that is too short
    #     sample_script_3 = "Hello."
    #     print(f"\n--- Test 3: Too Short Script (length: {len(sample_script_3)}) ---")
    #     print(f"Input script: \"{sample_script_3}\"")
    #     result_3 = forge_voice(script=sample_script_3)
    #     print(f"Result 3: {result_3}\n")
    #     if result_3.get("status") == "skipped":
    #         print("CORRECTLY SKIPPED: VFA identified short script.")
    #     else:
    #         print(f"UNEXPECTED: {result_3}")

    #     # Example 4: Empty script
    #     sample_script_4 = ""
    #     print(f"\n--- Test 4: Empty Script (length: {len(sample_script_4)}) ---")
    #     print(f"Input script: \"{sample_script_4}\"")
    #     result_4 = forge_voice(script=sample_script_4)
    #     print(f"Result 4: {result_4}\n")
    #     if result_4.get("status") == "skipped":
    #         print("CORRECTLY SKIPPED: VFA identified empty script.")
    #     else:
    #         print(f"UNEXPECTED: {result_4}")

    # print("\n--- VFA Google Cloud TTS integration testing complete (CLI) ---")
