# Voice Forge Agent (VFA)

## Purpose

The Voice Forge Agent (VFA) is responsible for synthesizing audio from a structured podcast script using a Text-to-Speech (TTS) service (currently Google Cloud Text-to-Speech). It saves the generated audio to a shared filesystem location and provides metadata, including a `stream_id` and filepath, for other services like the AudioStreamFeeder (ASF).

Key Responsibilities:

1.  **Input Processing:** Receives a structured `PodcastScript` dictionary (typically from PSWA via CPOA). This dictionary contains the title, individual segments (intro, main content, outro), and other metadata.
2.  **Text Preparation for TTS:**
    *   Extracts and concatenates the relevant text portions from the structured script (e.g., main title, intro content, segment titles, segment content, outro content) to form a single cohesive text block for synthesis.
    *   Handles scripts that might be error messages from upstream services (e.g., PSWA indicating insufficient content) by skipping TTS generation.
    *   Skips TTS for scripts that are too short after concatenation, based on a configurable minimum length.
3.  **TTS Service Interaction (Google Cloud Text-to-Speech):**
    *   Uses the `google-cloud-texttospeech` library to call the TTS service.
    *   Synthesizes audio using configured voice name, language code, and audio encoding (e.g., MP3).
    *   Requires Google Cloud credentials to be configured via `GOOGLE_APPLICATION_CREDENTIALS`.
4.  **Audio File Management:**
    *   Saves the generated audio content to a file in a configured shared directory (`VFA_SHARED_AUDIO_DIR`).
    *   Filenames include a unique `stream_id` and a UUID for uniqueness.
5.  **Output:** Returns a JSON dictionary to the CPOA containing:
    *   `status`: "success", "skipped", or "error".
    *   `message`: A descriptive message.
    *   `audio_filepath`: The absolute path to the saved audio file (if successful).
    *   `stream_id`: A unique ID generated for this audio stream.
    *   `audio_format`: The encoding of the audio (e.g., "mp3").
    *   `script_char_count`: Length of the text actually synthesized.
    *   `engine_used`: Indicates "google_cloud_tts" or an error state.

## Configuration

VFA is configured via environment variables, typically managed in a `.env` file within the `aethercast/vfa/` directory. Create one by copying the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file. Key variables include:

-   `GOOGLE_APPLICATION_CREDENTIALS`: Path to your Google Cloud service account key JSON file. **Required** for actual TTS functionality.
    -   *Example:* `/path/to/your/gcp-credentials.json`
-   `VFA_SHARED_AUDIO_DIR`: Directory where generated audio files will be saved. Ensure this directory is writable by the VFA service and readable by the ASF service.
    -   *Default:* `/srv/aethercast/generated_audio/`
-   `VFA_TTS_VOICE_NAME`: The specific voice model to use from Google Cloud TTS.
    -   *Default:* `en-US-Wavenet-D`
-   `VFA_TTS_LANG_CODE`: Language code for TTS.
    -   *Default:* `en-US`
-   `VFA_TTS_AUDIO_ENCODING`: Desired audio encoding. Supported values: "MP3", "LINEAR16", "OGG_OPUS".
    -   *Default:* `MP3`
-   `VFA_MIN_SCRIPT_LENGTH`: Minimum character length of the concatenated script text to attempt audio generation.
    -   *Default:* `20`
-   `VFA_HOST`: Host for the Flask development server.
    -   *Default:* `0.0.0.0`
-   `VFA_PORT`: Port for the Flask development server.
    -   *Default:* `5005`
-   `VFA_DEBUG`: Enables/disables Flask debug mode.
    -   *Default:* `True`

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `google-cloud-texttospeech`, and `python-dotenv`.

## Running the Service

1.  Ensure environment variables are correctly set, especially `GOOGLE_APPLICATION_CREDENTIALS` if real TTS is desired.
2.  Make sure the `VFA_SHARED_AUDIO_DIR` is created and writable by the user running the VFA service.
3.  Run the Flask development server:
    ```bash
    python aethercast/vfa/main.py
    ```
    This will start the service, typically on `http://0.0.0.0:5005`.

Alternatively, using the `flask` command:
```bash
export FLASK_APP=aethercast/vfa/main.py
export FLASK_DEBUG=1 # Optional
flask run --host=0.0.0.0 --port=5005
```

## API Endpoints

### Forge Voice

-   **HTTP Method:** `POST`
-   **URL Path:** `/forge_voice`
-   **Description:** Receives a structured podcast script (from PSWA, via CPOA) and synthesizes it into an audio file.
-   **Request Payload Example (JSON):**
    The `script` field must contain a structured script dictionary as produced by PSWA.
    ```json
    {
        "script": {
            "script_id": "pswa_script_abcdef123",
            "topic": "The Future of AI",
            "title": "AI: Our New Reality",
            "full_raw_script": "[TITLE]AI: Our New Reality\n[INTRO]Welcome to an exploration of AI.\n[SEGMENT_1_TITLE]Current Trends\n[SEGMENT_1_CONTENT]AI is everywhere, from your phone to your car.\n[OUTRO]Thanks for tuning in to AI insights!",
            "segments": [
                {"segment_title": "INTRO", "content": "Welcome to an exploration of AI."},
                {"segment_title": "Current Trends", "content": "AI is everywhere, from your phone to your car."},
                {"segment_title": "OUTRO", "content": "Thanks for tuning in to AI insights!"}
            ],
            "llm_model_used": "gpt-3.5-turbo"
        }
    }
    ```
-   **Success Response (200 OK - JSON):**
    ```json
    {
        "status": "success",
        "message": "Audio successfully synthesized and saved to shared directory.",
        "audio_filepath": "/srv/aethercast/generated_audio/aethercast_audio_strm_xxxx_yyyy.mp3",
        "stream_id": "strm_xxxx",
        "audio_format": "mp3",
        "script_char_count": 250, // Length of the text actually synthesized
        "engine_used": "google_cloud_tts"
    }
    ```
-   **Skipped Response (200 OK - JSON):**
    If the script is too short, or appears to be an error message from PSWA.
    ```json
    {
        "status": "skipped",
        "message": "Script too short (length 15 < 20 chars), audio generation skipped.", // Or PSWA error message
        "audio_filepath": null,
        "stream_id": "strm_zzzz",
        "script_char_count": 15,
        "engine_used": "google_cloud_tts"
    }
    ```
-   **Error Response Examples (JSON):**
    -   **400 Bad Request (Missing/Invalid `script` parameter):**
        ```json
        {
            "status": "error",
            "message": "Missing 'script' parameter"
        }
        ```
        ```json
        {
            "status": "error",
            "message": "'script' parameter must be a valid JSON object (dictionary)."
        }
        ```
    -   **500 Internal Server Error (TTS Failure / Config Error):**
        ```json
        {
            "status": "error",
            "message": "Error: GOOGLE_APPLICATION_CREDENTIALS environment variable not set.",
            "audio_filepath": null,
            "stream_id": "strm_errored",
            // ... other fields
        }
        ```
        ```json
        {
            "status": "error",
            "message": "Google TTS API Error: PermissionDenied - API key not valid...",
            // ...
        }
        ```
