# Podcast Script Weaver Agent (PSWA)

## Purpose

The Podcast Script Weaver Agent (PSWA) is a specialized microservice within the Aethercast system. Its primary role is to take raw textual content and a topic, and generate a well-structured podcast script. It achieves this by calling the **AIMS (AI Model Service)**, which handles the direct interaction with a Large Language Model (LLM).

PSWA now operates **asynchronously using a Celery task queue** for the core script generation process. When a request to weave a script is received, a task is dispatched, and clients can poll for the result. The service also features **idempotency** for its script weaving task; if the same request (identified by an `X-Idempotency-Key`) is submitted multiple times, it will be processed only once, with state managed in a shared PostgreSQL database.

Key Responsibilities:

1.  **Input Processing:** Receives textual `content`, a `topic` string, and optional `persona` and `narrative_guidance` from its caller (typically CPOA).
2.  **Prompt Engineering & Persona Application:** Constructs detailed prompts for AIMS, applying personas and narrative guidance.
3.  **AIMS Service Interaction:** Calls AIMS with the engineered prompt and parameters, handling asynchronous task polling if AIMS operates that way.
4.  **Script Parsing & Structuring:** Parses the AIMS response (JSON or tag-based fallback) into a structured script.
5.  **Output:** The Celery task returns the structured script JSON object. This object includes a `source` field indicating if the script was from `"generation_via_aims"` or `"cache"`.
6.  **Script Caching (Optional):** If enabled, PSWA checks a local cache (SQLite or PostgreSQL) before calling AIMS and saves newly generated scripts.
7.  **Idempotent Task Processing:** Ensures that identical script weaving requests (with the same `X-Idempotency-Key`) are processed only once, returning the original result for subsequent identical requests. State is managed in a shared PostgreSQL `idempotency_keys` table.

## Configuration

PSWA is configured via environment variables, typically managed in a `.env` file within the `aethercast/pswa/` directory. Create one by copying `.env.example`:

```bash
cp .env.example .env
```

Then, edit the `.env` file. The following variables are used:

-   `AIMS_SERVICE_URL`: **Required.** The URL for the AIMS (AI Model Service) endpoint for text generation.
    -   *Example:* `http://aims_service:8000/v1/generate` (if AIMS is synchronous) or `http://aims_service:8000/v1/generate_content_async` (if AIMS is asynchronous).
-   `AIMS_REQUEST_TIMEOUT_SECONDS`: Timeout in seconds for initial requests to the AIMS service.
    -   *Default:* `180`
-   `AIMS_POLLING_INTERVAL_SECONDS`: Interval for polling AIMS if it operates asynchronously.
    -   *Default:* `5`
-   `AIMS_POLLING_TIMEOUT_SECONDS`: Overall timeout for polling AIMS task results.
    -   *Default:* `300`
-   `PSWA_LLM_MODEL`: The LLM model ID to *request* from AIMS.
    -   *Default:* `gpt-3.5-turbo-0125`
-   `PSWA_LLM_TEMPERATURE`: Temperature setting for the LLM response (passed to AIMS).
    -   *Default:* `0.7`
-   `PSWA_LLM_MAX_TOKENS`: Maximum number of tokens to generate in the LLM response (passed to AIMS).
    -   *Default:* `1500`
-   `PSWA_LLM_JSON_MODE`: Set to `true` to request JSON output from AIMS.
    -   *Default:* `true`
-   `PSWA_DEFAULT_PROMPT_USER_TEMPLATE`: Template for the user message to the LLM.
    -   *Default:* (See `.env.example`)
-   `PSWA_DEFAULT_PERSONA`: Default persona for script generation.
    -   *Default:* `InformativeHost`
-   `PSWA_PERSONA_PROMPTS_JSON`: JSON string mapping persona IDs to system message additions.
    -   *Default:* (See `.env.example`)
-   `PSWA_BASE_SYSTEM_MESSAGE_JSON_SCHEMA_INSTRUCTION`: Base system message detailing required JSON output schema.
    -   *Default:* (See `.env.example`)
-   `PSWA_NARRATIVE_GUIDANCE_USER_PROMPT_ADDITION`: General narrative guidance text for user prompts.
    -   *Default:* (See `.env.example`)
-   `PSWA_HOST`: Host for the Flask development server.
    -   *Default:* `0.0.0.0`
-   `PSWA_PORT`: Port for the Flask development server.
    -   *Default:* `5004`
-   `FLASK_DEBUG` / `PSWA_DEBUG_MODE`: Enables/disables Flask debug mode.
    -   *Default:* `True`
-   **Database for Idempotency & Caching:**
    -   PSWA requires a **PostgreSQL database** for idempotency tracking (shared `idempotency_keys` table). The following variables (typically from `common.env`) are used:
        -   `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.
    -   For **Script Caching**, PSWA can use either SQLite or PostgreSQL, configured by:
        -   `DATABASE_TYPE`: 'sqlite' or 'postgres'. *Default: 'sqlite'*.
        -   If 'sqlite': `SHARED_DATABASE_PATH` (path to SQLite file, e.g., from `common.env`).
        -   If 'postgres' for caching: The same `POSTGRES_*` variables above will be used.
    -   `PSWA_SCRIPT_CACHE_ENABLED`: 'true' or 'false'. *Default: 'true'*.
    -   `PSWA_SCRIPT_CACHE_MAX_AGE_HOURS`: Max age for cached scripts. *Default: 720*.
-   `PSWA_TEST_MODE_ENABLED`: Set to `true` to enable a simplified test mode that bypasses AIMS calls.
    -   *Default (in code):* `false` (but often overridden to `true` in `common.env` for dev/test).
-   **Celery Configuration:**
    -   `CELERY_BROKER_URL`: URL for the Celery message broker (e.g., `redis://redis:6379/0`).
    -   `CELERY_RESULT_BACKEND`: URL for the Celery result backend (e.g., `redis://redis:6379/0`).
-   **Idempotency Behavior Configuration (PSWA-specific):**
    -   These are typically managed by constants within `main.py` but can be overridden by environment variables if `main.py` is adapted to load them into `pswa_config` (e.g., `IDEMPOTENCY_STATUS_PROCESSING`, `IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`). The `.env.example` file shows the default string values used by the application code. Refer to `pswa_config` initialization in `main.py` for specifics.
        -   `IDEMPOTENCY_STATUS_PROCESSING`: Default "processing"
        -   `IDEMPOTENCY_STATUS_COMPLETED`: Default "completed"
        -   `IDEMPOTENCY_STATUS_FAILED`: Default "failed"
        -   `IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`: Default 3600 seconds (1 hour)

## Testing

When `PSWA_TEST_MODE_ENABLED` is `true`, the `weave_script_task` (triggered by the `/v1/weave_script` endpoint) behaves as follows:
- It **does not** call the AIMS service.
- It returns predefined, structured script data based on an optional `X-Test-Scenario` HTTP header provided in the request to the endpoint (scenarios: `default_success`, `insufficient_content`, `llm_error`, `malformed_json`).
- The `source` field in the returned script data will indicate `"test_mode_generation"`.
- The `llm_model_used` will be `"test-mode-model"`.

This test mode is crucial for integration tests, allowing verification of CPOA and other services' handling of PSWA outputs without actual LLM costs or variability.

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `requests` (for AIMS calls), `python-dotenv`, `psycopg2-binary` (for PostgreSQL), and `celery` with a `redis` backend.

## Running the Service

1.  Ensure environment variables are set, especially `AIMS_SERVICE_URL` and PostgreSQL database configurations.
2.  Run the Flask development server:
    ```bash
    python aethercast/pswa/main.py
    ```
3.  **Run Celery Worker:** In a separate terminal, from the project root:
    ```bash
    celery -A aethercast.pswa.main.pswa_celery_app worker -l info
    ```
    Ensure Redis (or your chosen broker) and PostgreSQL are running and accessible. The `idempotency_keys` table migration must be applied to the PostgreSQL database.

Alternatively, using the `flask` command for the web server:
```bash
export FLASK_APP=aethercast/pswa/main.py
export FLASK_DEBUG=1 # Optional
flask run --host=0.0.0.0 --port=5004
# Remember to run the Celery worker separately.
```

## API Endpoints

PSWA operates asynchronously using Celery for the core script weaving task.

### 1. Initiate Script Weaving

-   **HTTP Method:** `POST`
-   **URL Path:** `/v1/weave_script`
-   **Description:** Receives content, topic, and optional persona/guidance, then dispatches a Celery task to generate a podcast script.
-   **Headers:**
    -   `X-Idempotency-Key` (string, **Required**): A unique key (e.g., UUID) provided by the client to ensure idempotent processing.
    -   `X-Workflow-ID` (string, Optional): An identifier to correlate this task with a larger workflow.
    -   `X-Test-Scenario` (string, Optional): For test mode, can specify scenarios like `default_success`, `insufficient_content`, `llm_error`, `malformed_json`.
-   **Request Payload Example (JSON):**
    ```json
    {
        "content": "Detailed textual content...",
        "topic": "The Future of Artificial Intelligence",
        "persona": "InformativeHost",
        "narrative_guidance": "Start with a hook..."
    }
    ```
-   **Success Response (202 Accepted - JSON):**
    ```json
    {
        "message": "Script weaving task accepted.",
        "task_id": "celery_task_uuid_string",
        "status_url": "/tasks/celery_task_uuid_string",
        "idempotency_key_processed": "client_provided_idempotency_key"
    }
    ```
-   **Error Responses (JSON):**
    -   **400 Bad Request**: If `X-Idempotency-Key` header is missing (`PSWA_MISSING_IDEMPOTENCY_KEY`), or if payload is invalid.

### 2. Get Task Status / Result

-   **Endpoint:** `GET /tasks/<task_id>`
-   **Description:** Poll this endpoint to check the status of the script weaving task and retrieve the result.
-   **Success Response (200 OK - JSON, if task completed successfully):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "SUCCESS",
        "result": { /* Actual result from the Celery task, e.g., script_data or error structure */ }
    }
    ```
    Example `result` for successful script generation:
    ```json
    {
        "script_data": {
            "script_id": "pswa_script_abcdef123456", /* ... other script fields ... */
        },
        "status_for_metric": "success_generation_async"
    }
    ```
-   **Conflict Response (409 Conflict - JSON, if idempotency conflict):**
    If the task execution determined a conflict (e.g., another task with the same idempotency key is currently processing and not timed out).
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "SUCCESS", // Celery task itself finished by returning the conflict info
        "result": {
            "status": "PROCESSING_CONFLICT",
            "message": "Task with this idempotency key is already processing.",
            "idempotency_key": "client_provided_idempotency_key"
        }
    }
    ```
    *(Note: The HTTP status code for this scenario from the `/tasks/<task_id>` endpoint should be 409 if the task result payload indicates `PROCESSING_CONFLICT`.)*
-   **Error Response (500 Internal Server Error - JSON, if task failed):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "FAILURE",
        "result": { "error": {"type": "task_failed", "message": "Details of the exception..."} }
    }
    ```
-   **Response (202 Accepted - JSON, if task is still pending/processing without conflict):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "PENDING", // Or STARTED, PROGRESS, RETRY
        "result": { /* Optional metadata about progress */ }
    }
    ```

## Monitoring and Logging

This service outputs logs in a structured JSON format. Key operational metrics are logged as part of these structured logs. For details, refer to the main project documentation on Logging and Metrics.

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
