# Central Podcast Orchestrator Agent (CPOA)

## Purpose

The Central Podcast Orchestrator Agent (CPOA) is the core component responsible for managing the entire podcast generation lifecycle within the Aethercast system. It receives requests (typically from the API Gateway) and coordinates a series of specialized agents to produce the final podcast audio or snippets.

Key responsibilities include:

-   **Workflow Management:** Orchestrating multi-step workflows involving other agents:
    -   **Full Podcast Generation (`orchestrate_podcast_generation`):** Coordinates with WebContentHarvesterAgent (WCHA - as a library), PodcastScriptWeaverAgent (PSWA - service), and VoiceForgeAgent (VFA - service).
    -   **Individual Snippet Generation (`orchestrate_snippet_generation`):** Coordinates with SnippetCraftAgent (SCA - service) and Image Generation Agent (IGA - service).
    -   **Search Results Generation (`orchestrate_search_results_generation`):** Calls Topic Discovery Agent (TDA - service) and then `orchestrate_snippet_generation` for each topic.
    -   **Landing Page Snippet Orchestration (`orchestrate_landing_page_snippets`):** Calls TDA and then `orchestrate_snippet_generation`.
    -   **Popular Category Provisioning (`get_popular_categories`):** Provides a predefined list of popular podcast categories.
-   **Task State Management:** Manages the state of podcast generation and other orchestrated workflows using a PostgreSQL database (see "Workflow State Management" below).
-   **Agent Communication:** Makes HTTP requests to downstream services (PSWA, VFA, SCA, ASF, TDA, IGA).
-   **Idempotency Key Propagation:** When initiating operations that involve backend Celery tasks in downstream services (TDA, SCA, PSWA, IGA), CPOA is responsible for forwarding the `X-Idempotency-Key` (if received from its caller, e.g., the API Gateway) and typically uses its own generated `workflow_id` as the `X-Workflow-ID` for these calls. This facilitates end-to-end idempotent processing.
-   **Real-time UI Updates:** Sends status updates to ASF if a `client_id` is provided.
-   **Error Handling and Resilience:** Implements retry mechanisms for service calls and manages failures within its orchestrated workflows.

CPOA itself is not a directly exposed service but a Python module called by the API Gateway. Its logic runs within the API Gateway's process.

## Configuration

CPOA is configured via environment variables, typically inherited from the API Gateway's environment (as it runs in the same process) or set in an `.env` file if run standalone for testing. Many of these are service URLs for the agents it orchestrates.

-   **Service URLs:** (e.g., `PSWA_SERVICE_URL`, `VFA_SERVICE_URL`, `SCA_SERVICE_URL`, `IGA_SERVICE_URL`, `TDA_SERVICE_URL`, `ASF_NOTIFICATION_URL`, `CPOA_ASF_SEND_UI_UPDATE_URL`). These should point to the correct addresses of the respective services (e.g., `http://pswa:5004/v1/weave_script` in a Docker environment).
-   **Database Configuration:** CPOA uses PostgreSQL for its workflow state management and this database also hosts the shared `idempotency_keys` table used by backend services. These are critical:
    -   `DATABASE_TYPE`: Must be set to `postgres`.
    -   `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`: Standard PostgreSQL connection details. These are typically sourced from `common.env`.
-   `CPOA_SERVICE_RETRY_COUNT`: Number of retries for failed HTTP requests to downstream services. Default: `3`.
-   `CPOA_SERVICE_RETRY_BACKOFF_FACTOR`: Base factor for exponential backoff (seconds). Default: `0.5`.

## Dependencies

Listed in `requirements.txt` (e.g., `requests`, `python-dotenv`, `psycopg2-binary`).

## Running and Testing

CPOA is a library module. Its `main.py` includes a `if __name__ == "__main__":` block for direct testing of orchestration logic.
To run these tests:
1. Ensure dependent services are running.
2. Set environment variables.
3. Execute: `python aethercast/cpoa/main.py`.
This simulates podcast generation scenarios.

Formal unit tests are in `aethercast/cpoa/tests/`. Run with `python -m unittest discover aethercast/cpoa/tests`.

## Database Interaction & Data Persistence Notes

-   CPOA updates the `podcasts` table (legacy) and primarily uses `workflow_instances` and `task_instances` tables for task tracking.
-   **Snippet Data Persistence:**
    -   The `orchestrate_snippet_generation` function calls SCA and IGA. The `image_url` obtained from IGA is a GCS URI and is part of the returned `SnippetDataObject`.
-   The helper `_save_snippet_to_db` saves snippet information (including the GCS URI for `image_url`) to the `topics_snippets` table in the PostgreSQL database.
-   CPOA functions accept `task_id` (legacy, new primary key is `workflow_id` from `workflow_instances` table), optional `voice_params_input`, `client_id`, `user_preferences`, and `test_scenarios`.
-   When calling backend services that support idempotency, CPOA passes the `X-Idempotency-Key` (if provided by the client to the API Gateway) and uses its own `workflow_id` as the `X-Workflow-ID` for those calls.
-   `user_preferences` can influence agent calls (e.g., preferred VFA voice).
-   `test_scenarios` allow passing headers like `X-Test-Scenario` to downstream services for testing.
-   The final dictionary from `orchestrate_podcast_generation` includes status, error messages, ASF details, the GCS URI for the audio (`final_audio_filepath`), stream ID, actual TTS settings used, a detailed CPOA internal orchestration log, and the `workflow_id`.

## Workflow State Management

CPOA implements robust state management for its orchestration flows using two primary PostgreSQL tables: `workflow_instances` and `task_instances`. This provides enhanced observability, debugging capabilities, and a foundation for future features like workflow resumption. The same PostgreSQL database also hosts the `idempotency_keys` table used by backend services (TDA, SCA, PSWA, IGA).

-   **`workflow_instances`**: Each call to a major CPOA orchestration function creates a record here. This table tracks the overall workflow. The `workflow_id` from this table is also passed as the `X-Workflow-ID` header to downstream services.
-   **`task_instances`**: Each call to an external agent or significant internal step is logged here, linked to the parent `workflow_id`.

**Interaction Flow:**
1.  A CPOA orchestration function creates a `workflow_instance`.
2.  Before each agent call, a `task_instance` is created.
3.  After the agent call, the `task_instance` is updated.
4.  The `workflow_instances` record is updated to its final status upon completion or critical failure.

This detailed state tracking occurs in the PostgreSQL database. For more details on the schema, see `docs/architecture/CPOA_State_Management.md`.

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
