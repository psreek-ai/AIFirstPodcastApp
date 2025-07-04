# Voice Forge Agent (VFA)

## Purpose

The Voice Forge Agent (VFA) is responsible for synthesizing audio from a structured podcast script. It achieves this by calling the **AIMS_TTS (AI Model Text-to-Speech Service)**, which handles the direct interaction with a TTS engine (e.g., Google Cloud Text-to-Speech).

VFA operates **asynchronously using a Celery task queue** for the main voice forging process (`forge_voice_task`). When a request is received, a Celery task is dispatched. The service implements **idempotency** for this task, ensuring that if the same request (identified by an `X-Idempotency-Key`) is submitted multiple times, the TTS operation and file handling are performed only once. Idempotency state is managed using a shared PostgreSQL database.

Key Responsibilities:

1.  **Input Processing:** Receives a structured `PodcastScript` and optional voice synthesis parameters.
2.  **Text Preparation for TTS:** Extracts and concatenates text from the script.
3.  **AIMS_TTS Service Interaction:** Asynchronously calls the AIMS_TTS service to get the synthesized audio.
4.  **Audio Information Handling:** Processes the response from AIMS_TTS, which includes the GCS URI of the audio file.
5.  **Output:** The Celery task returns a JSON dictionary containing status, audio metadata (including GCS URI), and TTS settings used.
6.  **Idempotent Task Processing:** Ensures that identical voice forging requests (with the same `X-Idempotency-Key`) are processed only once, using a shared PostgreSQL `idempotency_keys` table.

## Configuration

VFA is configured via environment variables. An `.env.example` file is provided; copy it to `.env` and customize.

Key environment variables:

-   `AIMS_TTS_SERVICE_URL`: **Required if not in test mode.** URL for the AIMS_TTS service.
-   `AIMS_TTS_REQUEST_TIMEOUT_SECONDS`: Timeout for initial AIMS_TTS requests. *Default: `10`*.
-   `AIMS_TTS_POLLING_INTERVAL_SECONDS`: Interval for polling AIMS_TTS. *Default: `3`*.
-   `AIMS_TTS_POLLING_TIMEOUT_SECONDS`: Overall timeout for AIMS_TTS polling. *Default: `180`*.
-   `VFA_SHARED_AUDIO_DIR`: Path for VFA's test mode dummy audio files. *Default: `/srv/aethercast/generated_audio/vfa_test_files`*.
-   `VFA_MIN_SCRIPT_LENGTH`: Minimum script length for TTS. *Default: `20`*.
-   `VFA_HOST`, `VFA_PORT`, `VFA_DEBUG_MODE` / `FLASK_DEBUG`: Standard Flask server settings.
-   `VFA_TEST_MODE_ENABLED`: `true` to bypass AIMS_TTS calls. *Default: `false`*.
-   **Celery Configuration:**
    -   `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`.
-   **PostgreSQL Database for Idempotency:** VFA uses a shared PostgreSQL database. Variables (e.g., `POSTGRES_HOST`) are typically from `common.env`.
-   **Idempotency Behavior Configuration:**
    -   `IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`: For stale lock detection. *Default: `300` seconds (in `vfa_config`)*.
    -   Status strings (`IDEMPOTENCY_STATUS_PROCESSING`, etc.) are defined as constants in `main.py` and loaded into `vfa_config`.

## Testing

When `VFA_TEST_MODE_ENABLED` is `true`, the `/v1/forge_voice` endpoint (and its Celery task) can simulate different scenarios using the `X-Test-Scenario` header, such as successful TTS, AIMS_TTS errors, or file save errors (for dummy files).

## Dependencies

Listed in `requirements.txt`. Key dependencies include `Flask`, `requests`, `python-dotenv`, `celery`, `redis`, `psycopg2-binary` (for PostgreSQL idempotency), and `python-json-logger` (for structured logging).

## Running the Service

1.  Set Environment Variables.
2.  Start PostgreSQL & Redis.
3.  Apply `idempotency_keys` table migration.
4.  Run Flask App: `python aethercast/vfa/main.py`
5.  Run Celery Worker: `celery -A aethercast.vfa.main.celery_app worker -l info` (from project root)

## API Endpoints

VFA operates asynchronously using Celery.

### 1. Initiate Voice Forging

-   **HTTP Method:** `POST`
-   **URL Path:** `/v1/forge_voice`
-   **Description:** Dispatches a Celery task (`forge_voice_task`) to synthesize audio.
-   **Headers:**
    -   `X-Idempotency-Key` (string, **Required**): Unique key for idempotent processing.
    -   `X-Workflow-ID` (string, Optional): Identifier for correlation.
    -   `X-Test-Scenario` (string, Optional): For test mode.
-   **Request Payload Example (JSON):**
    ```json
    {
        "script": { /* ... PodcastScript object ... */ },
        "voice_params": { /* ... Optional voice parameters ... */ }
    }
    ```
-   **Responses (JSON):**
    -   **200 OK**: If the request is successfully processed and the result is available synchronously (e.g., idempotency pre-check found a completed task). The body will contain the final result from the `forge_voice_task`.
        ```json
        { /* Result from forge_voice_task, e.g., audio_filepath, stream_id */ }
        ```
    -   **202 Accepted**: If the task is accepted for asynchronous processing (Celery task dispatched).
        ```json
        {
            "task_id": "celery_task_uuid_string",
            "status_url": "/v1/tasks/celery_task_uuid_string",
            "message": "Voice forging task accepted.",
            "idempotency_key_processed": "client_provided_idempotency_key"
        }
        ```
    -   **400 Bad Request**: If `X-Idempotency-Key` header is missing, or for payload validation errors.
    -   **409 Conflict**: If the `X-Idempotency-Key` refers to a task that is currently processing and not timed out (detected by endpoint pre-check).
        ```json
        {
            "error_code": "VFA_IDEMPOTENCY_CONFLICT",
            "message": "Request with this idempotency key is currently processing."
        }
        ```
    -   **500 Internal Server Error / 503 Service Unavailable**: For other server-side issues (e.g., database error during pre-check).

### 2. Get Task Status / Result

-   **Endpoint:** `GET /v1/tasks/<task_id>`
-   **Description:** Poll this endpoint to check task status and retrieve results.
-   **Success Response (200 OK - JSON, if task completed successfully):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "SUCCESS",
        "result": { /* Result from forge_voice_task, e.g., audio_filepath, stream_id */ }
    }
    ```
-   **Conflict Response (409 Conflict - JSON, if idempotency conflict):**
    If the task execution determined a conflict (e.g., another task with the same idempotency key is currently processing and not timed out). The Celery task itself finishes with a "SUCCESS" status but its result payload indicates the conflict.
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "SUCCESS",
        "result": {
            "status": "PROCESSING_CONFLICT",
            "message": "Task with this idempotency key is already processing.",
            "idempotency_key": "client_provided_idempotency_key"
        }
    }
    ```
-   **Error Response (200 OK - JSON, if task failed):**
    If the task execution resulted in a failure, the status endpoint successfully retrieves this failure state.
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "FAILURE",
        "result": { "error": {"type": "task_failed_exception_type", "message": "Details of the error..."} }
    }
    ```
-   **Response (202 Accepted - JSON, if task is still pending/processing without conflict):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "PENDING", // Or STARTED, RETRY, PROGRESS
        "result": null // Or metadata
    }
    ```

## Monitoring and Logging

Structured JSON logs are output. See main project documentation for details.

---

*For overarching project details, see the main [README.md](../../../README.md).*
