# AIMS TTS (AI Model Text-to-Speech Service) - Google Cloud TTS Edition

## Purpose

The AIMS TTS (Text-to-Speech) service is responsible for converting text scripts into audible speech. This version of AIMS TTS utilizes **Google Cloud Text-to-Speech** to provide high-quality voice synthesis. It is primarily used by the Voice Forge Agent (VFA) to generate audio for podcasts. The service saves generated audio to a shared volume accessible by other services.

## API Endpoints

### Synthesize Speech

-   **HTTP Method:** `POST`
-   **URL Path:** `/v1/synthesize`
-   **Description:** Receives text and synthesis parameters, calls the Google Cloud Text-to-Speech API, saves the resulting audio to a shared file path, and returns metadata about the audio.
-   **Request Payload (JSON):**
    *   `text` (string, required): The text content to be synthesized. Must be a non-empty string. Max length approx. 5000 characters. Invalid input results in a 400 error.
    *   `voice_id` (string, optional): The specific Google Cloud TTS voice name (e.g., "en-US-Wavenet-D"). Defaults to `AIMS_TTS_DEFAULT_VOICE_ID`. If provided, must be a string, otherwise results in a 400 error.
    *   `language_code` (string, optional): The language code (e.g., "en-US"). Defaults to `AIMS_TTS_DEFAULT_LANGUAGE_CODE`. If provided, must be a non-empty string, otherwise results in a 400 error.
    *   `audio_format` (string, optional): Desired audio encoding (e.g., "MP3", "LINEAR16", "OGG_OPUS"). Defaults to `AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR`. Must be one of the supported formats ("MP3", "LINEAR16", "OGG_OPUS"), otherwise results in a 400 error.
    *   `speech_rate` (float, optional): Speaking rate (0.25 to 4.0). Defaults to `AIMS_TTS_DEFAULT_SPEAKING_RATE`. Values outside this range are clamped. Non-float values result in a 400 error.
    *   `pitch` (float, optional): Speaking pitch (-20.0 to 20.0). Defaults to `AIMS_TTS_DEFAULT_PITCH`. Values outside this range are clamped. Non-float values result in a 400 error.
-   **Success Response (JSON):**
    *   `request_id` (string): A unique identifier for this synthesis request.
    *   `voice_id` (string): The voice ID that was used for the synthesis.
    *   `audio_url` (string): The file path of the generated audio within the container's shared audio volume (e.g., `/shared_audio/aims_tts/aims-tts-req-xxxx_yyyy.mp3`). **Note: This is a file path, not an HTTP URL, intended for inter-service access via a shared volume.**
    *   `audio_duration_seconds` (float): An estimated duration of the generated audio in seconds.
    *   `audio_format` (string): The actual audio format (file extension) of the saved audio file (e.g., "mp3").
-   **Error Responses (JSON):**
    *   **400 Bad Request:** Returned for invalid request payloads, such as:
        *   Missing or empty `text`.
        *   `text` exceeding maximum length.
        *   Invalid type for `voice_id` (if provided).
        *   Invalid type or empty `language_code` (if provided).
        *   Unsupported `audio_format`.
        *   Non-float `speech_rate` or `pitch`.
        *   Example: `{"request_id": "...", "error": {"type": "invalid_request_error", "message": "Validation failed: <specific_reason>"}}`
    *   **500 Internal Server Error:** For unexpected errors during TTS synthesis or file system I/O errors.
        *   Example: `{"request_id": "...", "error": {"type": "tts_failure", "message": "Google TTS API error: Details..."}}`
        *   Example: `{"request_id": "...", "error": {"type": "file_system_error", "message": "Could not save audio file: Details..."}}`
    *   **503 Service Unavailable:** If the service is not configured correctly (e.g., missing `GOOGLE_APPLICATION_CREDENTIALS`).

*For the conceptual API contract that AIMS TTS aims to fulfill, including potential future features like direct audio streaming, refer to `aethercast/aims_tts/tts_api_placeholder.md`. The current implementation focuses on file-based generation via Google TTS.*

## Configuration

Configuration is managed via environment variables, typically set in an `.env` file (created from `.env.example`) located in the `aethercast/aims_tts_service/` directory.

Key environment variables:

-   `AIMS_TTS_HOST`: Host address for the Flask server to bind to.
    -   *Default:* `0.0.0.0`
-   `AIMS_TTS_PORT`: Port on which the AIMS TTS service will listen.
    -   *Default:* `9000`
-   `FLASK_DEBUG`: Enables or disables Flask's debug mode.
    -   *Default:* `False` (as per `main.py`, but `Dockerfile` sets to `True`; `main.py` value takes precedence if `.env` is used)
-   `GOOGLE_APPLICATION_CREDENTIALS`: **Required.** Path to your Google Cloud service account key JSON file. This file grants the service permission to access Google Cloud Text-to-Speech.
    -   *Example (local):* `GOOGLE_APPLICATION_CREDENTIALS=./your-gcp-service-account-key.json`
    -   *Example (Docker Compose):* `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json` (mounted path)
-   `AIMS_TTS_DEFAULT_VOICE_ID`: Default Google TTS voice name if not specified in the request.
    -   *Default:* `en-US-Wavenet-D`
-   `AIMS_TTS_DEFAULT_LANGUAGE_CODE`: Default language code if not specified in the request.
    -   *Default:* `en-US`
-   `AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR`: Default audio format if not specified (e.g., "MP3", "LINEAR16").
    -   *Default:* `MP3`
-   `AIMS_TTS_DEFAULT_SPEAKING_RATE`: Default speaking rate if not specified.
    -   *Default:* `1.0`
-   `AIMS_TTS_DEFAULT_PITCH`: Default speaking pitch if not specified.
    -   *Default:* `0.0`
-   `SHARED_AUDIO_DIR_CONTAINER`: **Required.** The directory path inside the container where generated audio files will be saved. This path should be part of a shared volume for other services to access the audio.
    -   *Default:* `/shared_audio/aims_tts`

## Dependencies

Service dependencies are listed in `requirements.txt`:
-   `Flask`: Web framework.
-   `python-dotenv`: For loading environment variables from `.env` files.
-   `google-cloud-texttospeech`: The Google Cloud Text-to-Speech client library.

Install dependencies using:
```bash
pip install -r requirements.txt
```

## Running Standalone

To run the AIMS TTS service directly for development or testing:

1.  Ensure all required environment variables are set (e.g., in an `.env` file in this directory). Crucially, `GOOGLE_APPLICATION_CREDENTIALS` must point to a valid GCP key file, and `SHARED_AUDIO_DIR_CONTAINER` must be a writable path on your local system.
2.  Create the directory specified by `SHARED_AUDIO_DIR_CONTAINER` if it doesn't exist (e.g., `mkdir -p ./audio_files/aims_tts` and set `SHARED_AUDIO_DIR_CONTAINER=./audio_files/aims_tts`).
3.  Execute the main script:
    ```bash
    python aethercast/aims_tts_service/main.py
    ```
    The service will typically start on `http://0.0.0.0:9000` (or as configured).

## Docker

The AIMS TTS service is designed to be run as a Docker container and is included in the project's `docker-compose.yml` file.

-   **Building the Image:** If changes are made, you might need to rebuild the service's image: `docker-compose build aims_tts_service`.
-   **Credentials in Docker:** The `docker-compose.yml` is configured to mount a local Google Cloud credentials file into the container.
    1.  Place your GCP service account key JSON file (e.g., `gcp-credentials.json`) into the `./aethercast/aims_tts_service/` directory.
    2.  In your `aethercast/aims_tts_service/.env` file, ensure `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json` (this is the path inside the container where the file will be mounted).
-   **Shared Volume for Audio:** The `docker-compose.yml` file defines a named volume `aethercast_audio_data` which is mounted to `/shared_audio` inside the `aims_tts_service` container (and other services like VFA and ASF). The `SHARED_AUDIO_DIR_CONTAINER` (defaulting to `/shared_audio/aims_tts`) will reside within this shared volume, allowing other services to access the generated audio files.
-   **Running with Docker Compose:**
    ```bash
    docker-compose up -d aims_tts_service
    ```
    Or, to run all services:
    ```bash
    docker-compose up -d
    ```

The service will then be accessible to other Dockerized services (like VFA) via its service name and internal port (e.g., `http://aims_tts_service:9000`).
