# Snippet Craft Agent (SCA)

## Purpose

The Snippet Craft Agent (SCA) is a specialized microservice within the Aethercast system. Its primary function is to generate short, engaging podcast snippets (title, text content, and a cover art prompt) based on topic information provided by the Central Podcast Orchestrator Agent (CPOA). SCA achieves this by calling the **AIMS (AI Model Service)** for LLM-based text generation.

SCA now operates **asynchronously using a Celery task queue** for the core snippet crafting process. When a request to craft a snippet is received, a task is dispatched, and clients can poll for the result. The service also features **idempotency** for its snippet crafting task; if the same request (identified by an `X-Idempotency-Key`) is submitted multiple times, it will be processed only once, with state managed in a shared PostgreSQL database.

Key Responsibilities:

1.  **Input Processing:** Receives topic information (e.g., `topic_id`, `content_brief`, `topic_info`) from CPOA.
2.  **Prompt Engineering:** Formulates a detailed prompt for AIMS.
3.  **AIMS Service Interaction:** Calls AIMS with the engineered prompt, handling asynchronous polling if AIMS operates that way.
4.  **Snippet Structuring:** Parses the AIMS response and assembles a `SnippetDataObject`.
5.  **Output:** The Celery task returns the `SnippetDataObject`.
6.  **Idempotent Task Processing:** Ensures that identical snippet crafting requests (with the same `X-Idempotency-Key`) are processed only once, returning the original result for subsequent identical requests. State is managed in a shared PostgreSQL `idempotency_keys` table.

## Configuration

SCA is configured via environment variables, typically managed in a `.env` file within the `aethercast/sca/` directory. Copy `.env.example` to `.env` and customize.

```bash
cp .env.example .env
```

Key environment variables:

-   `AIMS_SERVICE_URL`: **Required if `USE_REAL_LLM_SERVICE=true`.** URL for AIMS.
-   `AIMS_REQUEST_TIMEOUT_SECONDS`: Timeout for AIMS requests. *Default: `60`*.
-   `AIMS_POLLING_INTERVAL_SECONDS`: Interval for polling AIMS if it operates asynchronously. *Default: `5`*.
-   `AIMS_POLLING_TIMEOUT_SECONDS`: Overall timeout for polling AIMS task results. *Default: `120`*.
-   `SCA_LLM_MODEL_ID`: LLM model ID to request from AIMS. *Default: `gpt-3.5-turbo`*.
-   `SCA_LLM_MAX_TOKENS_SNIPPET`: Max tokens for snippet (passed to AIMS). *Default: `150`*.
-   `SCA_LLM_TEMPERATURE_SNIPPET`: LLM temperature (passed to AIMS). *Default: `0.7`*.
-   `USE_REAL_LLM_SERVICE`: `true` for real LLM (via AIMS), `false` for simulated responses. *Default: `false`*.
-   **Flask Application Parameters:**
    -   `SCA_HOST` / `FLASK_RUN_HOST`: Host for Flask. *Default: `0.0.0.0`*.
    -   `SCA_PORT` / `FLASK_RUN_PORT`: Port for Flask. *Default: `5002`*.
    -   `FLASK_DEBUG`: Standard Flask debug mode. *Default: `True`*.
-   **Celery Configuration:**
    -   `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`: URLs for Celery broker and backend.
-   **PostgreSQL Database for Idempotency:** SCA uses a shared PostgreSQL database. Variables (e.g., `POSTGRES_HOST`) are typically from `common.env`.
-   **Idempotency Behavior Configuration (SCA-specific):**
    -   These are typically managed by constants within `main.py` but can be overridden by environment variables if `main.py` is adapted to load them into `sca_config` (e.g., `SCA_IDEMPOTENCY_STATUS_PROCESSING`, `SCA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`). The `.env.example` file shows the default string values used by the application code. Refer to `sca_config` initialization in `main.py` for specifics.
        -   `SCA_IDEMPOTENCY_STATUS_PROCESSING`: Default "processing"
        -   `SCA_IDEMPOTENCY_STATUS_COMPLETED`: Default "completed"
        -   `SCA_IDEMPOTENCY_STATUS_FAILED`: Default "failed"
        -   `SCA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`: Default 1800 seconds (30 minutes)

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `requests`, `python-dotenv`, `celery`, `redis`, and `psycopg2-binary`.

## Running the Service (Standalone)

1.  Set Environment Variables.
2.  Start PostgreSQL & Redis.
3.  Apply `idempotency_keys` table migration.
4.  Run Flask App: `python aethercast/sca/main.py`
5.  Run Celery Worker: `celery -A aethercast.sca.main.celery_app worker -l info` (from project root)

## Docker

SCA is included in `docker-compose.yml`. Ensure environment variables in `.env` files are set, especially for PostgreSQL and AIMS communication. The `idempotency_keys` table migration must be applied to the `postgres_db` service.

## API Endpoints

SCA operates asynchronously using Celery.

### 1. Initiate Snippet Crafting

-   **HTTP Method:** `POST`
-   **URL Path:** `/craft_snippet`
-   **Description:** Dispatches a Celery task to generate a podcast snippet.
-   **Headers:**
    -   `X-Idempotency-Key` (string, **Required**): Unique key for idempotent processing.
    -   `X-Workflow-ID` (string, Optional): Identifier for correlation.
-   **Request Payload Example (JSON):**
    ```json
    {
        "topic_id": "topic_12345",
        "content_brief": "The Future of Renewable Energy",
        "topic_info": {
            "title_suggestion": "The Future of Renewable Energy",
            "summary": "Exploring advancements in solar, wind, and geothermal power.",
            "keywords": ["solar", "wind", "geothermal", "sustainability"]
        }
    }
    ```
-   **Success Response (202 Accepted - JSON):**
    ```json
    {
        "message": "Snippet crafting task accepted.",
        "task_id": "celery_task_uuid_string",
        "status_url": "/v1/tasks/celery_task_uuid_string",
        "idempotency_key_processed": "client_provided_idempotency_key"
    }
    ```
-   **Error Responses (JSON):**
    -   **400 Bad Request**: If `X-Idempotency-Key` is missing, or payload is invalid.

### 2. Get Task Status / Result

-   **Endpoint:** `GET /v1/tasks/<task_id>`
-   **Description:** Poll for task status and result.
-   **Success Response (200 OK - JSON, if task completed successfully):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "SUCCESS",
        "result": { /* SnippetDataObject */ }
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
        "status": "PENDING", // Or STARTED, RETRY
        "result": null
    }
    ```

## Monitoring and Logging

Structured JSON logs are output by this service. Refer to main project documentation for details.

---

*For overarching project details, see the main [README.md](../../../README.md).*
