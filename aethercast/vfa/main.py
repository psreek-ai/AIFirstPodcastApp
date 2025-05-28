import logging
import os
import uuid

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


# --- Logging Configuration ---
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - VFA - %(message)s')

# --- Constants ---
TEMP_AUDIO_DIR = "/tmp/aethercast_audio"
DEFAULT_TTS_VOICE_NAME = "en-US-Wavenet-D"
DEFAULT_TTS_LANG_CODE = "en-US"
DEFAULT_AUDIO_ENCODING_TYPE = texttospeech.AudioEncoding.MP3
MIN_SCRIPT_LENGTH_FOR_AUDIO = 20 # Minimum characters in a script to "generate" audio

# PSWA Error Prefixes to identify scripts that are actually error messages
PSWA_ERROR_PREFIXES = (
    "OpenAI library not available", 
    "Error: OPENAI_API_KEY environment variable",
    "OpenAI API Error:", 
    "An unexpected error occurred during LLM call:",
    "[ERROR] Insufficient content provided"
)


def forge_voice(script: str) -> dict:
    """
    Generates audio from a script using Google Cloud Text-to-Speech
    and saves it to a local temporary file.
    Returns a dictionary with audio generation details or error information.
    """
    script_char_count = len(script)
    logger.info(f"[VFA_TTS_LOGIC] forge_voice called. Script character count: {script_char_count}")

    if not VFA_IMPORTS_SUCCESSFUL:
        error_msg = f"Google Cloud Text-to-Speech library not available. {VFA_MISSING_IMPORT_ERROR}"
        logger.error(f"[VFA_TTS_LOGIC] {error_msg}")
        return {
            "status": "error", "message": error_msg, "audio_filepath": None,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts_unavailable"
        }

    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        error_msg = "Error: GOOGLE_APPLICATION_CREDENTIALS environment variable not set."
        logger.error(f"[VFA_TTS_LOGIC] {error_msg}")
        return {
            "status": "error", "message": error_msg, "audio_filepath": None,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts_no_credentials"
        }

    # Check for PSWA error strings or very short scripts
    is_pswa_error = any(script.startswith(prefix) for prefix in PSWA_ERROR_PREFIXES)
    if is_pswa_error:
        message = "Script appears to be an error message from PSWA, audio generation skipped."
        logger.warning(f"[VFA_TTS_LOGIC] {message} Script: '{script[:100]}...'")
        return {
            "status": "skipped", "message": message, "audio_filepath": None,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts"
        }
    
    if not script or script_char_count < MIN_SCRIPT_LENGTH_FOR_AUDIO:
        message = f"Script too short (length {script_char_count} < {MIN_SCRIPT_LENGTH_FOR_AUDIO} chars), audio generation skipped."
        logger.warning(f"[VFA_TTS_LOGIC] {message}")
        return {
            "status": "skipped", "message": message, "audio_filepath": None,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts"
        }

    # If script is valid for TTS
    try:
        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=script)
        voice = texttospeech.VoiceSelectionParams(
            language_code=DEFAULT_TTS_LANG_CODE, name=DEFAULT_TTS_VOICE_NAME
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=DEFAULT_AUDIO_ENCODING_TYPE
        )

        logger.info("[VFA_TTS_LOGIC] Requesting audio synthesis from Google Cloud TTS...")
        response = client.synthesize_speech(
            request={"input": synthesis_input, "voice": voice, "audio_config": audio_config}
        )
        logger.info("[VFA_TTS_LOGIC] Audio synthesis successful.")

        os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
        # Determine file extension based on encoding
        file_extension = ".mp3" # Default for MP3
        if DEFAULT_AUDIO_ENCODING_TYPE == texttospeech.AudioEncoding.LINEAR16:
            file_extension = ".wav"
        elif DEFAULT_AUDIO_ENCODING_TYPE == texttospeech.AudioEncoding.OGG_OPUS:
            file_extension = ".ogg"
        
        filename = f"aethercast_audio_{uuid.uuid4().hex}{file_extension}"
        filepath = os.path.join(TEMP_AUDIO_DIR, filename)

        with open(filepath, "wb") as out_file:
            out_file.write(response.audio_content)
        
        logger.info(f"[VFA_TTS_LOGIC] Audio content written to file: {filepath}")

        return {
            "status": "success",
            "message": "Audio successfully synthesized and saved to temporary file.",
            "audio_filepath": filepath,
            "audio_format": texttospeech.AudioEncoding.Name(DEFAULT_AUDIO_ENCODING_TYPE).lower(), # e.g., "mp3"
            "script_char_count": script_char_count,
            "engine_used": "google_cloud_tts"
        }

    except google_exceptions.GoogleAPIError as e:
        error_msg = f"Google TTS API Error: {type(e).__name__} - {e}"
        logger.error(f"[VFA_TTS_LOGIC] {error_msg}", exc_info=True)
        return {
            "status": "error", "message": error_msg, "audio_filepath": None,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts"
        }
    except Exception as e:
        error_msg = f"Unexpected error during TTS synthesis: {type(e).__name__} - {e}"
        logger.error(f"[VFA_TTS_LOGIC] {error_msg}", exc_info=True)
        return {
            "status": "error", "message": error_msg, "audio_filepath": None,
            "script_char_count": script_char_count, "engine_used": "google_cloud_tts"
        }


if __name__ == "__main__":
    print("--- Testing VoiceForgeAgent (VFA) with Google Cloud TTS ---")

    # Check for import success and credentials first
    if not VFA_IMPORTS_SUCCESSFUL:
        print(f"\nSKIPPING TESTS: Google Cloud Text-to-Speech library not available. {VFA_MISSING_IMPORT_ERROR}")
    elif not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        print("\nSKIPPING REAL TTS CALLS: GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")
        print("You can still see the output for skipped/error cases based on script content.")
        # Test a case that doesn't require API call but uses the new logic
        print("\n--- Test: PSWA Error Script ---")
        pswa_error_script = "OpenAI API Error: Rate limit exceeded."
        print(f"Input script (PSWA error): \"{pswa_error_script}\"")
        result_pswa_error = forge_voice(script=pswa_error_script)
        print(f"Result: {result_pswa_error}\n")

        print("\n--- Test: Short Script ---")
        short_script = "Too short."
        print(f"Input script (short): \"{short_script}\"")
        result_short = forge_voice(script=short_script)
        print(f"Result: {result_short}\n")
    else:
        print("\nGOOGLE_APPLICATION_CREDENTIALS found. Attempting REAL TTS calls.")
        
        # Example 1: Valid, reasonably long script
        sample_script_1 = (
            "Hello from Aethercast! This is a test of the Google Cloud Text-to-Speech integration. "
            "We are generating this audio as part of a test run for the Voice Forge Agent. "
            "Hopefully, this sounds natural and clear. Let's add a bit more text to make sure it's "
            "long enough for a proper synthesis and to avoid any minimum length issues. This should do it."
        )
        print(f"\n--- Test 1: Standard Valid Script (length: {len(sample_script_1)}) ---")
        print(f"Input script: \"{sample_script_1[:100]}...\"")
        result_1 = forge_voice(script=sample_script_1)
        print(f"Result 1: {result_1}\n")
        if result_1.get("status") == "success":
            print(f"SUCCESS: Audio file should be at: {result_1.get('audio_filepath')}")
        else:
            print(f"ERROR/Skipped: {result_1.get('message')}")

        # Example 2: Script that is an error message from PSWA
        sample_script_2 = "OpenAI API Error: The model is currently overloaded. Please try again later."
        print(f"\n--- Test 2: PSWA Error Script (length: {len(sample_script_2)}) ---")
        print(f"Input script: \"{sample_script_2}\"")
        result_2 = forge_voice(script=sample_script_2)
        print(f"Result 2: {result_2}\n")
        if result_2.get("status") == "skipped":
            print("CORRECTLY SKIPPED: VFA identified PSWA error string.")
        else:
            print(f"UNEXPECTED: {result_2}")


        # Example 3: Script that is too short
        sample_script_3 = "Hello."
        print(f"\n--- Test 3: Too Short Script (length: {len(sample_script_3)}) ---")
        print(f"Input script: \"{sample_script_3}\"")
        result_3 = forge_voice(script=sample_script_3)
        print(f"Result 3: {result_3}\n")
        if result_3.get("status") == "skipped":
            print("CORRECTLY SKIPPED: VFA identified short script.")
        else:
            print(f"UNEXPECTED: {result_3}")

        # Example 4: Empty script
        sample_script_4 = ""
        print(f"\n--- Test 4: Empty Script (length: {len(sample_script_4)}) ---")
        print(f"Input script: \"{sample_script_4}\"")
        result_4 = forge_voice(script=sample_script_4)
        print(f"Result 4: {result_4}\n")
        if result_4.get("status") == "skipped":
            print("CORRECTLY SKIPPED: VFA identified empty script.")
        else:
            print(f"UNEXPECTED: {result_4}")

    print("\n--- VFA Google Cloud TTS integration testing complete ---")
