# AIMS TTS (AI Model Text-to-Speech Service) - Google Cloud TTS Edition

## Purpose

The AIMS TTS (Text-to-Speech) service is responsible for converting text scripts into audible speech. This version of AIMS TTS utilizes **Google Cloud Text-to-Speech** to provide high-quality voice synthesis. It is primarily used by the Voice Forge Agent (VFA) to generate audio for podcasts. The service saves generated audio to Google Cloud Storage (GCS). Operations are performed asynchronously using Celery.

## API Endpoints

### Synthesize Speech

-   **HTTP Method:** `POST`
-   **URL Path:** `/v1/synthesize`
-   **Description:** Accepts text and synthesis parameters, then dispatches an asynchronous Celery task (`invoke_tts_google_task`) to invoke the Google Cloud Text-to-Speech API and upload the resulting audio to GCS. Returns immediately with a task ID for status polling.
-   **Headers:**
    -   `X-Idempotency-Key` (string, Optional but Recommended): If provided, this key is used as the `request_id` for the Celery task, enabling idempotent execution. If not provided, AIMS_TTS will generate a unique `request_id`.
    -   `X-Workflow-ID` (string, Optional): An identifier to correlate this task with a larger workflow. This is passed to the Celery task if provided.
-   **Request Payload (JSON):**
    *   `text` (string, required): The text content to be synthesized. Must be a non-empty string. Max length approx. 5000 characters.
    *   `voice_id` (string, optional): The specific Google Cloud TTS voice name (e.g., "en-US-Wavenet-D"). Defaults to `AIMS_TTS_DEFAULT_VOICE_ID`.
    *   `language_code` (string, optional): The language code (e.g., "en-US"). Defaults to `AIMS_TTS_DEFAULT_LANGUAGE_CODE`.
    *   `audio_format` (string, optional): Desired audio encoding (e.g., "MP3", "LINEAR16", "OGG_OPUS"). Defaults to `AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR`.
    *   `speech_rate` (float, optional): Speaking rate (0.25 to 4.0). Defaults to `AIMS_TTS_DEFAULT_SPEAKING_RATE`.
    *   `pitch` (float, optional): Speaking pitch (-20.0 to 20.0). Defaults to `AIMS_TTS_DEFAULT_PITCH`.
-   **Success Response (202 Accepted - Task Dispatched):**
    ```json
    {
        "task_id": "some_celery_task_id",
        "status_url": "/v1/tasks/some_celery_task_id",
        "idempotency_key_processed": "actual_idempotency_key_used_or_generated"
    }
    ```
-   **Error Responses (JSON - Before Task Dispatch):**
    *   **400 Bad Request:** For invalid request payloads (missing `text`, invalid parameters, etc.).
        *   Example: `{"request_id": "...", "error": {"type": "invalid_request_error", "message": "Validation failed: <specific_reason>"}}`
    *   **503 Service Unavailable:** If the service is not configured correctly (e.g., missing `GOOGLE_APPLICATION_CREDENTIALS` or `GCS_BUCKET_NAME`).

### Get Task Status

-   **URL Path:** `/v1/tasks/<task_id>`
-   **HTTP Method:** `GET`
-   **Description:** Retrieves the status and result of an asynchronous TTS synthesis task dispatched by `/v1/synthesize`.
-   **Success Response (200 OK - Task Completed Successfully):**
    ```json
    {
        "task_id": "some_celery_task_id",
        "status": "SUCCESS",
        "result": {
            "request_id": "aims-tts-req-...",
            "voice_id": "en-US-Wavenet-D",
            "audio_url": "gs://your-bucket-name/audio/aims_tts/aims-tts-req-xxxx_yyyy.mp3",
            "audio_duration_seconds": 15.7,
            "audio_format": "mp3"
        }
    }
    ```
-   **Conflict Response (200 OK - JSON, if task execution determined an idempotency conflict):**
    If the task execution determined a conflict (e.g., another task with the same idempotency key is currently processing and not timed out), the task itself might complete successfully by returning this conflict information.
    ```json
    {
        "task_id": "some_celery_task_id",
        "status": "SUCCESS", // Celery task successfully determined and reported the conflict.
        "result": {
            "status": "PROCESSING_CONFLICT",
            "message": "Task with this idempotency key is already processing or recently completed with a conflict.",
            "idempotency_key": "actual_idempotency_key_used"
        }
    }
    ```
-   **Pending Response (202 Accepted - Task Still Processing):**
    ```json
    {
        "task_id": "some_celery_task_id",
        "status": "PENDING",
        "result": null
    }
    ```
-   **Failed Response (200 OK - Task Failed in Celery):**
    If the task execution resulted in a failure, the status endpoint successfully retrieves this failure state.
    ```json
    {
        "task_id": "some_celery_task_id",
        "status": "FAILURE",
        "result": {
            "error": {"type": "task_failed_exception_type", "message": "Google Cloud TTS API error: ..."}
        }
    }
    ```

## Idempotency

The `/v1/synthesize` endpoint, through its underlying Celery task `invoke_tts_google_task`, is designed to be idempotent. This prevents redundant processing for identical synthesis requests and allows for safe retries.

-   **Mechanism:** Idempotency is managed using the shared `aethercast/common` library, which uses a shared `idempotency_keys` table in a PostgreSQL database.
-   **Idempotency Key:** The `request_id` for the Celery task is used as the idempotency key.

## Configuration

Configuration is managed via environment variables, typically set in an `.env` file.

### Core Service Configuration:
-   `AIMS_TTS_HOST`: Host for the Flask server. Default: `0.0.0.0`.
-   `AIMS_TTS_PORT`: Port for the service. Default: `9000`.
-   `FLASK_DEBUG`: Enable Flask debug mode. Default: `False`.
-   `CELERY_BROKER_URL`: Celery message broker URL. Default: `redis://redis:6379/0`.
-   `CELERY_RESULT_BACKEND`: Celery result backend URL. Default: `redis://redis:6379/0`.

### Google Cloud Configuration:
Refer to the main project README for GCP setup.
-   `GOOGLE_APPLICATION_CREDENTIALS`: Path to GCP service account key (e.g., `/app/gcp-credentials.json` in Docker).
-   `GCS_BUCKET_NAME`: **Required.** Name of the Google Cloud Storage bucket for audio uploads.
-   `AIMS_TTS_GCS_AUDIO_PREFIX`: Prefix within the GCS bucket for AIMS_TTS files. Default: `audio/aims_tts/`.

### TTS Defaults:
-   `AIMS_TTS_DEFAULT_VOICE_ID`: Default voice. Default: `en-US-Wavenet-D`.
-   `AIMS_TTS_DEFAULT_LANGUAGE_CODE`: Default language. Default: `en-US`.
-   `AIMS_TTS_DEFAULT_AUDIO_ENCODING_STR`: Default audio format. Default: `MP3`.
-   `AIMS_TTS_DEFAULT_SPEAKING_RATE`: Default speaking rate. Default: `1.0`.
-   `AIMS_TTS_DEFAULT_PITCH`: Default pitch. Default: `0.0`.

### PostgreSQL Database Configuration (for Idempotency):
This service uses the shared PostgreSQL database configuration defined in the main `README.md` and `common.env`.
-   `IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`: Timeout for idempotency lock. Default: `300`.

## Dependencies

Service dependencies are listed in `requirements.txt`:
-   `Flask`
-   `python-dotenv`
-   `google-cloud-texttospeech`
-   `google-cloud-storage`
-   `celery`
-   `redis`
-   `python-json-logger`
-   `psycopg2-binary` (for PostgreSQL idempotency)

Install using: `pip install -r requirements.txt`

## Running Standalone

1.  Set required environment variables (GCP credentials, GCS bucket, PostgreSQL, Celery broker).
2.  Start the Flask application:
    ```bash
    python aethercast/aims_tts_service/main.py
    ```
3.  Start a Celery worker:
    ```bash
    celery -A aethercast.aims_tts_service.main.celery_app worker -l info
    ```

## Docker

AIMS TTS is included in `docker-compose.yml`.
-   **Build:** `docker-compose build aims_tts_service`
-   **Run:** `docker-compose up -d aims_tts_service`
-   Ensure GCP credentials and GCS bucket are correctly configured in the environment for the Docker container. Celery workers may need a separate service definition in `docker-compose.yml` or to be run as separate containers/processes configured to connect to the shared Celery broker.

## Monitoring and Logging

This service uses structured JSON logging. Metrics related to TTS generation, GCS uploads, and task processing are logged. Refer to the main project's [Logging Guide](../../../docs/operational/Logging_Guide.md) and [Metrics Definition](../../../docs/operational/Metrics_Definition.md).

---

*For overarching Aethercast project details, see the main [README.md](../../../README.md).*
