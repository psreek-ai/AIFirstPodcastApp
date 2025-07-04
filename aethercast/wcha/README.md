# Web Content Harvester Agent (WCHA)

## Purpose

The Web Content Harvester Agent (WCHA) is a component of the Aethercast system responsible for fetching and extracting textual content from web pages. Given a topic, it uses web search (via DuckDuckGo or NewsAPI) to find relevant URLs and then employs content extraction (via Trafilatura) to get the main text from those pages. Some operations are now asynchronous via Celery.

Key Responsibilities:

1.  **Topic-Based Search:** Uses `duckduckgo_search` or NewsAPI (if configured) to find relevant web pages for a given topic string.
2.  **Content Fetching:** Makes HTTP GET requests to fetch the content of identified URLs.
3.  **Text Extraction:** Utilizes the `trafilatura` library to extract the main textual content from the fetched HTML, aiming to remove boilerplate like ads, navigation, and footers.
4.  **Content Consolidation:** Combines text extracted from multiple sources into a single string, usually demarcated by source URL (for DDGS path).
5.  **Error Handling:** Manages errors related to web requests (timeouts, HTTP errors), search failures, and content extraction issues.
6.  **Asynchronous Task Execution:** For NewsAPI searches and direct URL harvesting, operations are performed asynchronously using Celery.

WCHA is primarily used as a library module by CPOA (`get_content_for_topic` function) but also includes a Flask endpoint for direct testing and initiating its harvesting capabilities.

## Logging

WCHA uses structured JSON logging for its operations, leveraging the `python-json-logger` library. This allows for easier parsing, searching, and analysis of logs.

-   **Format:** Logs are output in JSON format.
-   **`ServiceNameFilter`:** A custom logging filter (`ServiceNameFilter`) is used to automatically inject a `service_name` attribute into every log record. For WCHA, this is set to `wcha-service`.
-   **Key Log Fields:** Common fields in the structured logs include:
    -   `asctime` (timestamp, e.g., `2023-10-27T10:30:00.123Z`)
    -   `levelname` (e.g., `INFO`, `WARNING`, `ERROR`)
    -   `name` (logger name, typically the module name like `aethercast.wcha.main`)
    -   `service_name` (e.g., `wcha-service`)
    -   `module` (module where the log originated)
    -   `funcName` (function name where the log originated)
    -   `lineno` (line number of the log statement)
    -   `message` (the log message string)
    -   `workflow_id` (Contextual ID for a larger workflow, defaults to "N/A" if not provided)
    -   `task_id` (Contextual ID for a specific task, often the Celery task ID or a request ID, defaults to "N/A")
-   **Metric Logging:** WCHA also implements structured logging for key metrics (e.g., harvest success/failure counts). These are logged at INFO level with a message like "WCHA metric" and include `metric_name`, `value`, and `tags` in the `extra` part of the log.

## Idempotency

To prevent redundant processing and ensure that operations can be safely retried, key Celery tasks in WCHA are designed to be idempotent.

-   **Tasks:**
    -   `fetch_news_articles_task`
    -   `harvest_url_content_task`
-   **Mechanism:** Idempotency is achieved using a shared `idempotency_keys` table in a PostgreSQL database.
-   **Idempotency Key:** The `request_id` provided when dispatching these Celery tasks is used as the idempotency key.
-   **Pattern:** A two-phase pattern is used:
    1.  **Check/Lock:** Before executing the core logic, the task checks the `idempotency_keys` table.
        -   If the key + task name combination indicates a 'completed' status, the stored result is returned immediately.
        -   If it's 'processing' and not stale (within `IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`), a conflict is indicated.
        -   Otherwise, the task attempts to acquire a lock by setting the status to 'processing' and updating a `locked_at` timestamp.
    2.  **Execute & Update:**
        -   The core task logic is executed.
        -   Upon completion, the idempotency record is updated to 'completed' with the result payload.
        -   If an error occurs, the record is updated to 'failed' with error details.
        -   The `locked_at` timestamp is cleared upon final update.

## Configuration

WCHA is configured via environment variables, typically managed in a `.env` file within the `aethercast/wcha/` directory.

-   `WCHA_SEARCH_MAX_RESULTS`: Max search results for DuckDuckGo. Default: `3`.
-   `WCHA_REQUEST_TIMEOUT`: HTTP request timeout in seconds. Default: `10`.
-   `WCHA_USER_AGENT`: User-Agent for HTTP requests. Default: `AethercastContentHarvester/0.2`.
-   `CELERY_BROKER_URL`: URL for the Celery message broker (e.g., Redis). Default: `redis://redis:6379/0`.
-   `CELERY_RESULT_BACKEND`: URL for the Celery result backend. Default: `redis://redis:6379/0`.
-   `USE_REAL_NEWS_API`: Set to `true` to use NewsAPI for topic searches, `false` to use DuckDuckGo. Default: `false`.
-   `TDA_NEWS_API_KEY`: API key for NewsAPI (if `USE_REAL_NEWS_API=true`).
-   `TDA_NEWS_API_BASE_URL`: Base URL for NewsAPI. Default: `https://newsapi.org/v2/`.
-   `POSTGRES_HOST`: Hostname for the PostgreSQL database (for idempotency).
-   `POSTGRES_PORT`: Port for the PostgreSQL database. Default: `5432`.
-   `POSTGRES_USER`: Username for PostgreSQL.
-   `POSTGRES_PASSWORD`: Password for PostgreSQL.
-   `POSTGRES_DB`: PostgreSQL database name.
-   `WCHA_DB_POOL_MIN_CONN`: Minimum connections for the PostgreSQL pool. Default: `1`.
-   `WCHA_DB_POOL_MAX_CONN`: Maximum connections for the PostgreSQL pool. Default: `5`.
-   `IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`: Timeout in seconds for an idempotency lock to be considered stale. Default: `300`.


## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
Key dependencies include: `Flask`, `python-dotenv`, `requests`, `beautifulsoup4`, `duckduckgo_search`, `trafilatura`, `celery`, `redis`, `python-json-logger`, and `psycopg2-binary` (for PostgreSQL idempotency).

## Running and Testing

**As a Library:**
WCHA's core functionality (`get_content_for_topic`, `harvest_from_url`, and Celery tasks) is designed to be used by other Python modules, primarily CPOA.

**Standalone Testing (`if __name__ == "__main__":`)**
The `main.py` script includes an `if __name__ == "__main__":` block for direct testing of its core logic (synchronous parts and URL safety checks).
1.  Ensure dependencies are installed.
2.  Set up environment variables (e.g., in a `.env` file).
3.  Execute the script directly:
    ```bash
    python aethercast/wcha/main.py
    ```
This runs local tests. For full testing of asynchronous operations, Celery workers must be running.

**Flask Endpoint & Celery Workers:**
WCHA's Flask app (`main.py`) provides an API to initiate harvesting tasks. For asynchronous operations, Celery workers need to be started:
1.  Set environment variables (especially for Celery broker/backend and PostgreSQL if using idempotency).
2.  Run the Flask development server:
    ```bash
    export FLASK_APP=aethercast/wcha/main.py
    flask run --port=5003
    ```
3.  Start Celery worker(s) for WCHA (from the project root or a directory where `aethercast.wcha.main` is importable):
    ```bash
    celery -A aethercast.wcha.main.celery_app worker -l info
    ```
    Ensure your Python path allows Celery to find the `aethercast.wcha.main.celery_app` instance.

## API Endpoints

### Harvest Content

-   **HTTP Method:** `POST`
-   **URL Path:** `/harvest`
-   **Description:** Initiates content harvesting. The behavior depends on the payload and configuration:
    *   **Direct URL Harvest (Asynchronous):** If a `url` is provided, the `harvest_url_content_task` Celery task is dispatched.
    *   **Topic-Based Harvest (NewsAPI - Asynchronous):** If `topic` and `use_search: true` are provided, AND `USE_REAL_NEWS_API=true` (env var), the `fetch_news_articles_task` Celery task is dispatched.
    *   **Topic-Based Harvest (DuckDuckGo - Synchronous):** If `topic` and `use_search: true` are provided, AND `USE_REAL_NEWS_API=false` (env var), a synchronous search using DuckDuckGo is performed, followed by content harvesting.
    *   Legacy mock data retrieval is also a synchronous path if specific mock parameters are sent.
-   **Headers:**
    -   `X-Idempotency-Key` (string, Optional but Recommended for async tasks): If provided for an operation that dispatches an asynchronous Celery task (like direct URL harvest or NewsAPI search), this key is used as the `request_id` for that Celery task, enabling idempotent execution of the underlying task.
    -   `X-Workflow-ID` (string, Optional): An identifier to correlate this task with a larger workflow. This is passed to the Celery task if provided.
-   **Request Payload Example (JSON) - Direct URL Harvest (Async):**
    ```json
    {
        "url": "https://en.wikipedia.org/wiki/Python_(programming_language)"
    }
    ```
-   **Request Payload Example (JSON) - Topic Search & Harvest (Behavior depends on `USE_REAL_NEWS_API`):**
    ```json
    {
        "topic": "benefits of regular exercise",
        "use_search": true
    }
    ```
-   **Asynchronous Task Accepted Response (202 Accepted):**
    Returned when an asynchronous Celery task is successfully dispatched.
    ```json
    {
        "task_id": "some_celery_task_id",
        "status_url": "/v1/tasks/some_celery_task_id",
        "message": "Harvest task accepted.",
        "idempotency_key_processed": "client_provided_idempotency_key_if_any"
    }
    ```
-   **Success Response (200 OK - Synchronous DDGS Path):**
    Returned if `USE_REAL_NEWS_API=false` and `use_search=true` for a topic.
    ```json
    {
        "status": "success",
        "content": "Source: http://example.com/article1\nText from article 1...",
        "source_urls": ["http://example.com/article1"],
        "message": "Successfully consolidated content..."
    }
    ```
-   **Error Response Examples (JSON):**
    -   **400 Bad Request (Invalid Payload):**
        ```json
        {
            "error_code": "WCHA_MISSING_PARAMETERS",
            "message": "Invalid input",
            "details": "'topic' (with use_search=true) or 'url' must be provided."
        }
        ```
    -   (Other error responses for synchronous path remain similar to previous documentation, e.g., 404 for no DDGS results, 500 for all DDGS harvest attempts failing.)

### Get Task Status

-   **HTTP Method:** `GET`
-   **URL Path:** `/v1/tasks/<task_id>`
-   **Description:** Retrieves the status and result of an asynchronous WCHA Celery task (e.g., `harvest_url_content_task` or `fetch_news_articles_task`).
-   **Success Response (200 OK - Task Completed Successfully):**
    ```json
    {
        "task_id": "some_celery_task_id",
        "status": "SUCCESS",
        "result": {
            "url": "http://example.com/harvested_page",
            "content": "Extracted text...",
            "error_type": null,
            "error_message": null
        }
    }
    ```
    *(Note: The structure of `result` will vary based on the Celery task. For `fetch_news_articles_task`, it would contain articles list.)*
-   **Pending Response (202 Accepted - Task Still Processing):**
    ```json
    {
        "task_id": "some_celery_task_id",
        "status": "PENDING",
        "result": null
    }
    ```
-   **Failed Response (200 OK or 500 - Task Failed):**
    *(Status code might be 200 if Celery reports failure as a successful retrieval of failure state, or 500 if the endpoint itself has an issue processing the request)*
    ```json
    {
        "task_id": "some_celery_task_id",
        "status": "FAILURE",
        "result": {
            "error": {"type": "task_failed", "message": "Exception details from Celery task..."}
        }
    }
    ```
