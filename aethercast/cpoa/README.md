# Central Podcast Orchestrator Agent (CPOA)

## Purpose

The Central Podcast Orchestrator Agent (CPOA) is the core component responsible for managing the entire podcast generation lifecycle within the Aethercast system. It receives requests (typically from the API Gateway) and coordinates a series of specialized agents to produce the final podcast audio or snippets.

Key responsibilities include:

-   **Workflow Management:** Orchestrating multi-step workflows involving other agents.
-   **Task State Management:** Managing the state of its workflows using a PostgreSQL database.
-   **Agent Communication:** Making HTTP requests to downstream services.
-   **Idempotency:** Ensuring its own orchestration tasks and calls to downstream services are idempotent.
-   **Real-time UI Updates:** Sending status updates to ASF.
-   **Error Handling and Resilience:** Managing failures and retries within its workflows.

CPOA itself is not a directly exposed service but a Python module called by the API Gateway. Its logic runs within the API Gateway's process.

## Logging

CPOA's logging is integrated with the Aethercast project's standard logging practices. When CPOA functions are executed (e.g., as part of an API Gateway request), its log messages are typically handled by the root logger configured in the calling application (e.g., API Gateway).

-   **Format:** Assumes the calling application (API Gateway) configures structured JSON logging.
-   **Contextual Information:** CPOA uses a `logging.LoggerAdapter` to automatically inject key contextual information into its log records:
    -   `workflow_id`: The unique ID of the CPOA orchestration workflow.
    -   `task_id`: The ID of the specific CPOA internal task or sub-step within the workflow (e.g., the database ID of a `task_instances` record).
-   This ensures that CPOA logs are rich in context, making it easier to trace the execution flow of a particular podcast generation request or snippet creation process.

## Configuration

CPOA is configured via environment variables, typically inherited from the API Gateway's environment.

-   **Service URLs:** URLs for downstream services it orchestrates (e.g., `WCHA_SERVICE_URL`, `TDA_SERVICE_URL`, `SCA_SERVICE_URL`, `PSWA_SERVICE_URL`, `IGA_SERVICE_URL`, `VFA_SERVICE_URL`, `ASF_NOTIFICATION_URL`, `CPOA_ASF_SEND_UI_UPDATE_URL`).
-   **Database Configuration:** CPOA uses PostgreSQL for workflow state management and accessing the shared `idempotency_keys` table.
    -   Connection Parameters (typically from `common.env`):
        -   `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.
    -   Connection Pooling (CPOA specific):
        -   CPOA uses `psycopg2.pool.SimpleConnectionPool` for efficient database connection management.
        -   `DB_POOL_MIN_CONN`: Minimum connections in the CPOA DB pool. Default: `1`.
        -   `DB_POOL_MAX_CONN`: Maximum connections in the CPOA DB pool. Default: `5`.
-   **Retry and Timeout Configuration:**
    -   `CPOA_SERVICE_RETRY_COUNT`: Retries for HTTP requests to services. Default: `3`.
    -   `CPOA_SERVICE_RETRY_BACKOFF_FACTOR`: Backoff factor for retries. Default: `0.5`.
    -   Polling Intervals (e.g., `CPOA_WCHA_POLLING_INTERVAL_SECONDS`, `CPOA_PSWA_POLLING_INTERVAL_SECONDS`, etc.): Time between status checks for asynchronous agent tasks. Defaults vary (e.g., 3-10 seconds).
    -   Polling Timeouts (e.g., `CPOA_WCHA_POLLING_TIMEOUT_SECONDS`, `CPOA_PSWA_POLLING_TIMEOUT_SECONDS`, etc.): Maximum time to wait for an agent task to complete. Defaults vary (e.g., 180-600 seconds).
-   **Idempotency Configuration:**
    -   `IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`: Timeout for CPOA's own orchestration task idempotency locks. Default: `3600` seconds (1 hour). *(Note: Individual services like WCHA, TDA, SCA, PSWA, and IGA have their own shorter `IDEMPOTENCY_LOCK_TIMEOUT_SECONDS` for their specific Celery tasks, typically 300-1800 seconds).*

## Dependencies

CPOA's dependencies are managed as part of the API Gateway's `requirements.txt`. Key libraries it utilizes include `requests`, `psycopg2-binary`, and `celery` (for interacting with Celery task results).

## Running and Testing

CPOA is a library module. Its `main.py` includes a `if __name__ == "__main__":` block for direct testing of orchestration logic. This requires dependent services to be running and environment variables to be correctly set.
Formal unit tests are in `aethercast/cpoa/tests/`.

## Idempotency Key Propagation and Usage

CPOA plays a crucial role in ensuring end-to-end idempotency.
-   **CPOA's Own Orchestration Tasks:** The main Celery task `cpoa_orchestrate_podcast_task` (and potentially future CPOA-level async tasks) uses an idempotency key (typically its own Celery task ID or a key provided by the API Gateway) to ensure that the entire orchestration it manages is idempotent. This is recorded in the shared `idempotency_keys` table with a specific `task_name` (e.g., `cpoa_orchestrate_podcast_task`).
-   **Downstream Agent Calls:** When CPOA calls other services (like TDA, SCA, PSWA, IGA, WCHA, AIMS, AIMS_TTS) that perform asynchronous operations:
    -   It forwards the `X-Idempotency-Key` header if one was provided by its caller (e.g., API Gateway).
    -   It typically uses its own `workflow_id` (from the `workflow_instances` table) as the `X-Workflow-ID` header for these calls.
    -   The downstream services then use their received `X-Idempotency-Key` (which is often the `request_id` they receive in their task payload, originating from CPOA or API Gateway) to manage their own task idempotency in the *same* shared `idempotency_keys` table, but with their own specific `task_name` (e.g., `wcha_fetch_news_articles_task`, `aims_invoke_llm_vertex_ai_task`).
-   This layered approach ensures that both high-level CPOA orchestrations and individual agent operations can be safely retried without unintended side effects, all managed via the central `idempotency_keys` table.

## Error Handling and Resilience

CPOA implements retry mechanisms with exponential backoff for HTTP calls to downstream services. It also includes polling logic with timeouts to handle asynchronous tasks performed by these services.
Recent enhancements to the polling loops ensure that when errors occur (either the polled service's status endpoint itself returns an error, or the service's task reports a failure), detailed contextual information is logged. This includes the CPOA `workflow_id`, CPOA's internal sub-task ID, the specific service being polled, the polled service's own task ID, the poll URL, and relevant error messages or response snippets. This improved logging aids significantly in diagnosing issues within the distributed workflow.

Failures at critical stages of the orchestration are logged, and the overall workflow status in the `workflow_instances` table is updated to reflect the failure, often with an error message.

## Workflow State Management

CPOA uses PostgreSQL tables (`workflow_instances`, `task_instances`) for detailed state tracking of its orchestration flows, providing observability and debugging capabilities. This is the same database that hosts the `idempotency_keys` table.

-   **`workflow_instances`**: Tracks overall CPOA workflows.
-   **`task_instances`**: Tracks individual agent calls or significant steps within a CPOA workflow.

For more details on the schema, see `docs/architecture/CPOA_State_Management.md`.

---

*For overarching Aethercast project details, see the main [README.md](../../../README.md).*
