import flask
import uuid
import datetime
import logging
import json
import requests # For calling AIMS_TTS

app = flask.Flask(__name__)

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- AIMS_TTS Placeholder Configuration ---
AIMS_TTS_PLACEHOLDER_URL = "http://localhost:8001/v1/synthesize" # Assuming AIMS_TTS placeholder runs here
# This is the hardcoded response from aethercast/aims_tts/tts_api_placeholder.md (for response_type: "url")
AIMS_TTS_HARDCODED_RESPONSE_URL_MODE = {
  "request_id": "aims-tts-placeholder-req-456",
  "voice_id": "AetherVoice-Placeholder",
  "audio_url": "https://aethercast.com/placeholder_audio/sample.mp3", # This URL is fictional
  "audio_duration_seconds": 2.5, # Short, as it's just a sample
  "audio_format": "mp3"
}
# For actual interaction with a running AIMS_TTS placeholder, set this to True
SIMULATE_AIMS_TTS_CALL = False

# --- AudioStreamFeeder (ASF) Configuration (Conceptual) ---
# This is the base URL where the ASF WebSocket server will be running.
# The stream_id will be appended to this URL by VFA/CPOA.
ASF_WEBSOCKET_BASE_URL = "ws://localhost:5005/api/v1/podcasts/stream" # Port for ASF


# --- Helper Functions ---
def generate_stream_id() -> str:
    """Generates a unique stream ID."""
    return f"stream_{uuid.uuid4().hex}"

def concatenate_script_text(podcast_script: dict) -> str:
    """
    Concatenates all script content from the podcast script segments.
    """
    full_text = []
    if podcast_script and isinstance(podcast_script.get("script"), list):
        for segment in podcast_script.get("script", []):
            if isinstance(segment, dict) and segment.get("script_content"):
                full_text.append(segment.get("script_content"))
    
    concatenated = "\n".join(full_text)
    logging.info(f"[VFA_LOGIC] Concatenated script text. Total length: {len(concatenated)} characters.")
    if not concatenated:
        logging.warning("[VFA_LOGIC] Script for TTS was empty or improperly formatted.")
        return "Podcast script content is missing or empty." # Default text if script is empty
    return concatenated

def call_aims_tts_placeholder(text_to_synthesize: str, voice_id: str = "AetherVoice-Nova") -> dict:
    """
    Simulates calling the AIMS_TTS placeholder or calls it if SIMULATE_AIMS_TTS_CALL is True.
    """
    logging.info(f"[VFA_AIMS_TTS_CALL] Calling AIMS_TTS. Text length: {len(text_to_synthesize)}, Voice ID: {voice_id}")
    
    if SIMULATE_AIMS_TTS_CALL:
        payload = {
            "text": text_to_synthesize,
            "voice_id": voice_id,
            "output_format": "mp3",
            "response_type": "url" # VFA expects a URL to the (simulated) full audio for now
        }
        try:
            response = requests.post(AIMS_TTS_PLACEHOLDER_URL, json=payload, timeout=20) # TTS can take time
            response.raise_for_status()
            tts_response = response.json()
            logging.info(f"[VFA_AIMS_TTS_CALL_SUCCESS] Received response from AIMS_TTS: {tts_response}")
            return tts_response
        except requests.exceptions.RequestException as e:
            logging.error(f"[VFA_AIMS_TTS_CALL_ERROR] Error calling AIMS_TTS: {e}. Falling back to hardcoded response.")
            return AIMS_TTS_HARDCODED_RESPONSE_URL_MODE # Fallback
        except json.JSONDecodeError as e:
            logging.error(f"[VFA_AIMS_TTS_CALL_ERROR] Error decoding JSON from AIMS_TTS: {e}. Falling back to hardcoded response.")
            return AIMS_TTS_HARDCODED_RESPONSE_URL_MODE # Fallback
    else:
        logging.info("[VFA_AIMS_TTS_CALL] Using hardcoded AIMS_TTS response (SIMULATE_AIMS_TTS_CALL is False).")
        import time
        time.sleep(0.2) 
        # Modify the hardcoded response slightly to reflect the input script's estimated duration better
        # For simulation, let's base duration on text length. A real TTS would provide this.
        estimated_duration = int(len(text_to_synthesize) / 10) # Rough estimate: 10 chars per second
        modified_response = AIMS_TTS_HARDCODED_RESPONSE_URL_MODE.copy()
        modified_response["audio_duration_seconds"] = max(2.5, estimated_duration) # Ensure at least 2.5s
        modified_response["voice_id"] = voice_id # Use requested voice
        return modified_response

# --- API Endpoint ---
@app.route("/forge_audio", methods=["POST"])
def forge_audio_endpoint():
    """
    API endpoint for CPOA to request audio generation from a podcast script.
    Accepts JSON payload with:
    - 'podcast_script': dict (the PodcastScript object from PSWA)
    - 'voice_preferences': dict (e.g., {"voice_id": "AetherVoice-Nova"})
    - 'error_trigger': string (optional, e.g., "vfa_error")
    """
    try:
        request_data = flask.request.get_json()
        if not request_data:
            return flask.jsonify({"error": "Invalid JSON payload"}), 400

        podcast_script = request_data.get("podcast_script")
        voice_preferences = request_data.get("voice_preferences", {})
        voice_id_to_use = voice_preferences.get("voice_id", "AetherVoice-Default") # Default if not specified
        error_trigger = request_data.get("error_trigger")

        if not podcast_script or not isinstance(podcast_script.get("script"), list):
            return flask.jsonify({"error": "'podcast_script' with a list of script segments is required."}), 400

        logging.info(f"[VFA_REQUEST] Received /forge_audio request for script_id: '{podcast_script.get('script_id') or podcast_script.get('podcast_id')}', ErrorTrigger: '{error_trigger}'")

        if error_trigger == "vfa_error":
            logging.warning(f"[VFA_SIMULATED_ERROR] Simulating an error for /forge_audio based on error_trigger: {error_trigger}")
            return flask.jsonify({
                "error": "Simulated VFA Error",
                "details": "This is a controlled error triggered for testing purposes in VoiceForgeAgent."
            }), 500

        # 1. Prepare text for TTS (concatenate all segments for now)
        full_text_for_tts = concatenate_script_text(podcast_script)

        # 2. Call AIMS_TTS Placeholder
        tts_response = call_aims_tts_placeholder(full_text_for_tts, voice_id=voice_id_to_use)

        # 3. Prepare output for CPOA
        stream_id = generate_stream_id()
        # The actual audio_url from tts_response is for the (simulated) full audio file.
        # For streaming, we provide the ASF WebSocket URL.
        audio_stream_url_for_client = f"{ASF_WEBSOCKET_BASE_URL}/{stream_id}"
        
        vfa_response_to_cpoa = {
            "podcast_id": podcast_script.get('podcast_id') or podcast_script.get('script_id'), 
            "final_audio_url_placeholder": tts_response.get("audio_url"), 
            "stream_id": stream_id,
            "audio_stream_url_for_client": audio_stream_url_for_client, 
            "estimated_duration_seconds": tts_response.get("audio_duration_seconds", 0),
            "voice_used": tts_response.get("voice_id", voice_id_to_use),
            "audio_format": tts_response.get("audio_format", "mp3")
        }
        
        logging.info(f"[VFA_ASF_CONCEPT] Stream '{stream_id}' created. ASF should be notified with audio from: {tts_response.get('audio_url')}")
        
        logging.info(f"[VFA_RESPONSE] Audio forging complete for script '{vfa_response_to_cpoa['podcast_id']}'. Stream ID: '{stream_id}'.")
        return flask.jsonify(vfa_response_to_cpoa), 200

    except Exception as e:
        logging.error(f"Error in /forge_audio endpoint: {e}", exc_info=True)
        return flask.jsonify({"error": f"Internal server error in VFA: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006, debug=True)
```
Error simulation added to VFA. All specialized agents (TDA, SCA, WCHA, PSWA, VFA) now have the `error_trigger` mechanism.

**2. CPOA Error Handling:**

Now, I'll modify CPOA's `main.py` to:
- Add a new `error_simulation_config` field to the `/api/v1/podcasts/generate` endpoint's request body. This field will be a dictionary specifying which agent to trigger an error in (e.g., `{"trigger_error_in": "tda"}`).
- Modify the `call_*_agent` functions to accept an `error_trigger_value` and pass it to the respective agent if needed.
- Update workflow functions (`run_snippet_generation_workflow`, `run_full_podcast_generation_workflow`) to extract this `error_simulation_config` and pass the appropriate `error_trigger` value to the agent calls.
- Ensure existing error handling in `call_*_agent` functions (checking `response.raise_for_status()`, handling `requests.exceptions`) correctly identifies HTTP errors and returns an `{"error": ...}` dict.
- The workflow functions will then check for this `{"error": ...}` dict from the `call_*_agent` functions. If an error is detected:
    - They will call `_update_workflow_status` with "FAILED" and the error details.
    - They will `return` early to stop the workflow for that task.

I'll read `aethercast/cpoa/main.py` and then apply these modifications.
