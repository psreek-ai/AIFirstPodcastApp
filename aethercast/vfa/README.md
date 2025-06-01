# Voice Forge Agent (VFA)

## Purpose

The Voice Forge Agent (VFA) is responsible for synthesizing audio from a structured podcast script using a Text-to-Speech (TTS) service (currently Google Cloud Text-to-Speech). It saves the generated audio to a shared filesystem location and provides metadata, including a `stream_id` and filepath, for other services like the AudioStreamFeeder (ASF).

Key Responsibilities:

1.  **Input Processing:** Receives a structured `PodcastScript` dictionary (typically from PSWA via CPOA) and optional voice synthesis parameters.
2.  **Text Preparation for TTS:**
    *   Extracts and concatenates the relevant text portions from the structured script (e.g., main title, intro content, segment titles, segment content, outro content) to form a single cohesive text block for synthesis.
    *   Handles scripts that might be error messages from upstream services (e.g., PSWA indicating insufficient content) by skipping TTS generation.
    *   Skips TTS for scripts that are too short after concatenation, based on a configurable minimum length.
3.  **TTS Service Interaction (Google Cloud Text-to-Speech):**
    *   Uses the `google-cloud-texttospeech` library to call the TTS service.
    *   Synthesizes audio using voice name, language code, audio encoding, speaking rate, and pitch. These parameters can be partially or fully overridden by the input request; otherwise, configured defaults are used.
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
    *   `tts_settings_used`: A dictionary detailing the actual TTS parameters (voice name, language code, speaking rate, pitch, encoding) applied for the synthesis.

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
-   `VFA_TTS_VOICE_NAME`: Default Google Cloud TTS voice name. Can be overridden by request.
    -   *Default:* `en-US-Wavenet-D`
-   `VFA_TTS_LANG_CODE`: Default language code for TTS. Can be overridden by request.
    -   *Default:* `en-US`
-   `VFA_TTS_AUDIO_ENCODING`: Desired audio encoding. Supported values: "MP3", "LINEAR16", "OGG_OPUS".
    -   *Default:* `MP3`
-   `VFA_MIN_SCRIPT_LENGTH`: Minimum character length of the concatenated script text to attempt audio generation.
    -   *Default:* `20`
-   `VFA_TTS_DEFAULT_SPEAKING_RATE`: Default speaking rate for TTS (1.0 is normal). Google Cloud range: [0.25, 4.0]. Can be overridden by request.
    -   *Default:* `1.0`
-   `VFA_TTS_DEFAULT_PITCH`: Default pitch for TTS (0.0 is normal). Google Cloud range: [-20.0, 20.0]. Can be overridden by request.
    -   *Default:* `0.0`
-   `VFA_HOST`: Host for the Flask development server.
    -   *Default:* `0.0.0.0`
-   `VFA_PORT`: Port for the Flask development server.
    -   *Default:* `5005`
-   `VFA_DEBUG`: Enables/disables Flask debug mode.
    -   *Default:* `True`
-   `VFA_TEST_MODE_ENABLED`: Set to `true` to enable a simplified test mode that bypasses actual TTS calls and returns predefined responses or simulates errors. Useful for integration testing. See "Testing" section.
    -   *Default (in code):* `false`

## Testing

When `VFA_TEST_MODE_ENABLED` is set to `true`, the `/forge_voice` endpoint behaves differently:
- It does not call the actual Google Cloud TTS service.
- It can return predefined responses based on an optional `X-Test-Scenario` HTTP header:
    - **No header or `default`**: Simulates a successful TTS operation. It creates a small, dummy audio file in the `VFA_SHARED_AUDIO_DIR` and returns a success JSON response including a filepath and stream ID. The `tts_settings_used` in the response will reflect the default or input voice parameters.
    - **`X-Test-Scenario: vfa_error_tts`**: Simulates an error occurring during the TTS API call. It returns a JSON response like:
      ```json
      {
          "status": "error",
          "message": "Test scenario: Simulated TTS API error from VFA.",
          "audio_filepath": null,
          "stream_id": "strm_...",
          // ... other fields ...
          "engine_used": "test_mode_tts_api_error"
      }
      ```
      No dummy audio file is created. The endpoint returns an HTTP 500 status.
    - **`X-Test-Scenario: vfa_error_file_save`**: Simulates an error during the file saving stage (after a conceptual successful TTS). It returns a JSON response like:
      ```json
      {
          "status": "error",
          "message": "Test scenario: Simulated file saving IO error in VFA.",
          "audio_filepath": "/path/to/where/file/would/be.mp3", // Filepath might be determined
          "stream_id": "strm_...",
          // ... other fields ...
          "engine_used": "test_mode_tts_file_error"
      }
      ```
      No dummy audio file is actually saved. The endpoint returns an HTTP 500 status.
- The `engine_used` field in the response will indicate the test mode scenario (e.g., `"test_mode_tts_success"`, `"test_mode_tts_api_error"`).

This test mode helps verify how CPOA and other services handle various outcomes from VFA without incurring TTS costs or relying on external service availability.

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
-   **Description:** Receives a structured podcast script (specifically, the JSON object output from PSWA, typically passed via CPOA) and optional voice parameters, then synthesizes it into an audio file.
-   **Request Payload Example (JSON):**
    The `script` field must contain the structured JSON script object as generated by PSWA. `voice_params` is optional.
    ```json
    {
        "script": {
            "script_id": "pswa_script_abcdef123",
            "topic": "The Future of AI",
            "title": "AI: Our New Reality",
            "full_raw_script": "[TITLE]AI: Our New Reality\n[INTRO]Welcome to an exploration of AI...",
            "segments": [
                {"segment_title": "INTRO", "content": "Welcome to an exploration of AI."},
                {"segment_title": "Current Trends", "content": "AI is everywhere..."}
            ],
            "llm_model_used": "gpt-3.5-turbo"
        },
        "voice_params": { // Optional
            "voice_name": "en-AU-Wavenet-B",
            "language_code": "en-AU",
            "speaking_rate": 1.1,
            "pitch": -2.0
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
        "script_char_count": 250,
        "engine_used": "google_cloud_tts",
        "tts_settings_used": { // Details the actual TTS parameters applied
            "voice_name": "en-AU-Wavenet-B",
            "language_code": "en-AU",
            "speaking_rate": 1.1,
            "pitch": -2.0,
            "audio_encoding": "MP3"
        }
    }
    ```
-   **Skipped Response (200 OK - JSON):**
    If the script is too short, or appears to be an error message from PSWA. `tts_settings_used` will reflect parameters that would have been used or `null`.
    ```json
    {
        "status": "skipped",
        "message": "Script too short (length 15 < 20 chars), audio generation skipped.",
        "audio_filepath": null,
        "stream_id": "strm_zzzz",
        "script_char_count": 15,
        "engine_used": "google_cloud_tts",
        "tts_settings_used": { // Parameters that would have been used
            "voice_name": "en-US-Wavenet-D",
            "language_code": "en-US",
            "speaking_rate": 1.0,
            "pitch": 0.0,
            "audio_encoding": "MP3"
        }
    }
    ```
-   **Error Response Examples (JSON):**
    -   **400 Bad Request (Missing/Invalid `script` or `voice_params`):**
        ```json
        {"status": "error", "message": "Missing 'script' parameter"}
        ```
        ```json
        {"status": "error", "message": "'voice_params' parameter must be a valid JSON object if provided."}
        ```
    -   **500 Internal Server Error (TTS Failure / Config Error):** `tts_settings_used` will reflect parameters attempted.
        ```json
        {
            "status": "error",
            "message": "Error: GOOGLE_APPLICATION_CREDENTIALS environment variable not set.",
            // ... other fields ...
            "tts_settings_used": { ... }
        }
        ```
