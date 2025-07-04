# Topic Discovery Agent (TDA)

## Purpose

The Topic Discovery Agent (TDA) is a component of the Aethercast system designed to identify and suggest potential podcast topics. It can scan various data sources (currently focusing on NewsAPI.org if configured) or use simulated data to find relevant and timely subjects.

Key Responsibilities:

1.  **Data Source Interaction:**
    *   If configured for real API use (`USE_REAL_NEWS_API=true`), its Celery sub-task (`fetch_news_from_newsapi_task`) fetches articles from a news API (e.g., NewsAPI.org).
    *   Otherwise, it uses simulated data (`identify_topics_from_sources`).
2.  **Topic Identification & Transformation:** Processes data and transforms it into `TopicObject` format.
3.  **Output:** The main Celery task (`discover_topics_task`) returns a list of these `TopicObject` dictionaries.
4.  **Database Interaction:** Saves all successfully identified `TopicObject`s to a PostgreSQL database (`topics_snippets` table).
5.  **Idempotent Task Processing:** The primary `discover_topics_task` is idempotent. If the same discovery request (identified by an `X-Idempotency-Key`) is submitted multiple times, it will be processed only once, with state managed in a shared PostgreSQL `idempotency_keys` table.

The service operates **asynchronously** using Celery for its main topic discovery process.

## Configuration

TDA is configured via environment variables, typically managed in a `.env` file within the `aethercast/tda/` directory. Copy `.env.example` to `.env` and customize.

```bash
cp .env.example .env
```

Key environment variables:

-   `TDA_NEWS_API_KEY`: API key for the news provider. Required if `USE_REAL_NEWS_API=true`.
-   `TDA_NEWS_API_BASE_URL`: Base URL for the news API. *Default: `https://newsapi.org/v2/`*.
-   `TDA_NEWS_API_ENDPOINT`: Specific endpoint. *Default: `everything`*.
-   `TDA_NEWS_DEFAULT_KEYWORDS`: Comma-separated default keywords. *Default: `AI,technology,science,innovation`*.
-   `TDA_NEWS_DEFAULT_LANGUAGE`: Default language for news. *Default: `en`*.
-   `USE_REAL_NEWS_API`: `true` for real News API; `false` for simulated data. *Default: `false`*.
-   `TDA_NEWS_PAGE_SIZE`: Articles to fetch per request. *Default: `25`*.
-   `TDA_NEWS_REQUEST_TIMEOUT`: Timeout for news API requests. *Default: `15`*.
-   `TDA_NEWS_USER_AGENT`: User-Agent for HTTP requests. *Default: `AethercastTopicDiscovery/0.1`*.
-   **Database Configuration:** TDA uses PostgreSQL for storing discovered topics (`topics_snippets` table) and for idempotency. Variables are typically from `common.env`:
    -   `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.
-   **Flask Application Parameters:**
    -   `TDA_HOST`, `TDA_PORT`, `TDA_DEBUG_MODE`.
-   **Celery Configuration:**
    -   `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`.
-   **NewsAPI Sub-Task Polling:**
    -   `TDA_NEWSAPI_POLLING_INTERVAL_SECONDS`, `TDA_NEWSAPI_POLLING_TIMEOUT_SECONDS`.
-   **Idempotency Behavior Configuration (TDA-specific):**
    -   These are typically managed by constants within `main.py` but can be overridden by environment variables if `main.py` is adapted to load them into `tda_config` (e.g., `TDA_IDEMPOTENCY_STATUS_PROCESSING`, `TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`). The `.env.example` file shows the default string values used by the application code. Refer to `tda_config` initialization in `main.py` for specifics.
        -   `TDA_IDEMPOTENCY_STATUS_PROCESSING`: Default "processing"
        -   `TDA_IDEMPOTENCY_STATUS_COMPLETED`: Default "completed"
        -   `TDA_IDEMPOTENCY_STATUS_FAILED`: Default "failed"
        -   `TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`: Default 1800 seconds (30 minutes)

## Dependencies

Project dependencies are listed in `requirements.txt`. Key dependencies include `Flask`, `requests`, `python-dotenv`, `celery`, `redis`, and `psycopg2-binary`. Install with:
```bash
pip install -r requirements.txt
```

## Running the Service (Standalone)

1.  Set Environment Variables.
2.  Start PostgreSQL & Redis.
3.  Apply Database Migrations (`topics_snippets` table is initialized by TDA on startup if needed; `idempotency_keys` table needs manual migration).
4.  Run Flask App: `python aethercast/tda/main.py`
5.  Run Celery Worker: `celery -A aethercast.tda.main.celery_app worker -l info` (from project root)

## API Endpoints

TDA operates asynchronously using Celery.

### 1. Initiate Topic Discovery

-   **HTTP Method:** `POST`
-   **URL Path:** `/discover_topics`
-   **Description:** Dispatches a Celery task to discover topics.
-   **Headers:**
    -   `X-Idempotency-Key` (string, **Required**): Unique key for idempotent processing.
    -   `X-Workflow-ID` (string, Optional): Identifier for correlation.
-   **Request Payload Example (JSON):**
    ```json
    {
        "query": "artificial intelligence in education",
        "limit": 5,
        "error_trigger": null
    }
    ```
-   **Success Response (202 Accepted - JSON):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status_url": "/v1/tasks/celery_task_uuid_string",
        "message": "Topic discovery task initiated. Poll task ID for results.",
        "idempotency_key_processed": "client_provided_idempotency_key"
    }
    ```
-   **Error Responses (JSON):**
    -   **400 Bad Request**: If `X-Idempotency-Key` is missing, or for payload validation errors.

### 2. Get Task Status / Result

-   **Endpoint:** `GET /v1/tasks/<task_id>`
-   **Description:** Poll this endpoint to check the status of the topic discovery task.
-   **Success Response (200 OK - JSON, if task completed successfully):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "SUCCESS",
        "result": { /* Result from discover_topics_task, e.g., list of TopicObjects */ }
    }
    ```
-   **Conflict Response (200 OK - JSON, if task execution determined an idempotency conflict):**
    If the task execution determined a conflict (e.g., another task with the same idempotency key is currently processing and not timed out), the task itself might complete successfully by returning this conflict information.
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "SUCCESS", // Celery task successfully determined and reported the conflict.
        "result": {
            "status": "PROCESSING_CONFLICT",
            "message": "Task with this idempotency key is already processing or recently completed with a conflict.",
            "idempotency_key": "client_provided_idempotency_key"
        }
    }
    ```
-   **Error Response (200 OK or 500 Internal Server Error - JSON, if task failed):**
    If the task failed, the status endpoint might return 200 OK with status "FAILURE", or 500 if the endpoint itself has an issue retrieving the state.
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
