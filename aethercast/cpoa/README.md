# Central Podcast Orchestrator Agent (CPOA)

## Purpose

The Central Podcast Orchestrator Agent (CPOA) is the core component responsible for managing the entire podcast generation lifecycle within the Aethercast system. It receives requests (typically from the API Gateway) and coordinates a series of specialized agents to produce the final podcast audio or snippets.

Key responsibilities include:

-   **Workflow Management:** Orchestrating multi-step workflows involving other agents:
    -   **Full Podcast Generation (`orchestrate_podcast_generation`):** Coordinates with WebContentHarvesterAgent (WCHA - as a library), PodcastScriptWeaverAgent (PSWA - service), and VoiceForgeAgent (VFA - service).
        -   Receives a structured JSON script from PSWA, which it then forwards to VFA.
        -   VFA returns a GCS URI for the generated audio (e.g., `gs://bucket/audio.mp3`), which is stored as `final_audio_filepath` in the database.
        -   Accepts optional voice parameters and an optional `client_id` (for UI updates). Voice parameters are forwarded to VFA.
        -   Notifies the AudioStreamFeeder (ASF) when new audio is ready, providing the GCS URI as the `filepath`.
    -   **Individual Snippet Generation (`orchestrate_snippet_generation`):** Coordinates with SnippetCraftAgent (SCA - service) to generate snippet text and a `cover_art_prompt`. After SCA, it calls the Image Generation Agent (IGA - service) with the `cover_art_prompt` to get an `image_url`. This `image_url` is a GCS URI (e.g., `gs://bucket/image.png`). The final `SnippetDataObject` (containing snippet text, `cover_art_prompt`, and the GCS URI for the image) is **returned by this function**. This `SnippetDataObject` is then used by calling functions (like search or topic exploration). The `_save_snippet_to_db` helper function within CPOA saves this snippet data, including the GCS URI for `image_url`, to the `topics_snippets` table.
    -   **Search Results Generation (`orchestrate_search_results_generation`):**
        -   Accepts a search query. Calls the Topic Discovery Agent (TDA - service) to find relevant topics.
        -   For each topic, calls `orchestrate_snippet_generation` (which includes IGA call to get a GCS URI for the image) to generate a descriptive snippet.
        -   Returns a list of these generated snippets.
    -   **Landing Page Snippet Orchestration (`orchestrate_landing_page_snippets`):**
        -   Orchestrates the generation of multiple diverse snippets for the application's landing page.
        -   It calls the Topic Discovery Agent (TDA) to fetch a list of diverse topics based on general keywords or user preferences.
        -   For each relevant topic from TDA, it then calls `orchestrate_snippet_generation` (which internally calls SCA for text and IGA for an image URL) to create a complete snippet.
        -   The function returns a list of these fully generated snippets, ready for display on the landing page.
        -   This function is implemented in `aethercast/cpoa/main.py` and used by the API Gateway's `/api/v1/snippets` endpoint.
    -   **Popular Category Provisioning (`get_popular_categories`):**
        -   Provides a predefined list of popular podcast categories.
-   **Task State Management:** Updates the status of podcast generation tasks in the `podcasts` table of a shared database.
-   **Agent Communication:** Makes HTTP requests to downstream services (PSWA, VFA, SCA, ASF, TDA, IGA).
-   **Real-time UI Updates:** Sends status updates to ASF if a `client_id` is provided.
-   **Error Handling and Resilience:** Implements retry mechanisms for service calls and manages failures.

CPOA itself is not a directly exposed service but a Python module called by the API Gateway.

## Configuration

CPOA is configured via environment variables, typically in an `.env` file in `aethercast/cpoa/`.

-   `PSWA_SERVICE_URL`: URL of PodcastScriptWeaverAgent. Default: `http://localhost:5004/weave_script`.
-   `VFA_SERVICE_URL`: URL of VoiceForgeAgent. Default: `http://localhost:5005/forge_voice`.
-   `ASF_NOTIFICATION_URL`: URL for notifying ASF about new audio. Default: `http://localhost:5006/asf/internal/notify_new_audio`.
-   `ASF_WEBSOCKET_BASE_URL`: Base WebSocket URL for ASF. Default: `ws://localhost:5006/api/v1/podcasts/stream`.
-   `SCA_SERVICE_URL`: URL of SnippetCraftAgent. Default: `http://localhost:5002/craft_snippet`.
-   `CPOA_ASF_SEND_UI_UPDATE_URL`: Internal URL on ASF for CPOA to send UI updates. Default: `http://localhost:5006/asf/internal/send_ui_update`.
-   `IGA_SERVICE_URL`: URL of Image Generation Agent. Default: `http://localhost:5007`.
-   `TDA_SERVICE_URL`: URL of Topic Discovery Agent. Used by functions like `orchestrate_search_results_generation` and `orchestrate_topic_exploration`. Default: `http://localhost:5000/discover_topics`.
-   `SHARED_DATABASE_PATH`: Path to the shared SQLite database (used if `DATABASE_TYPE` is `sqlite`). Default: `/app/database/aethercast_podcasts.db`.
-   `DATABASE_TYPE`: Specifies the database type (`sqlite` or `postgres`).
-   `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`: PostgreSQL connection details (used if `DATABASE_TYPE` is `postgres`).
-   `CPOA_SERVICE_RETRY_COUNT`: Number of retries for failed HTTP requests. Default: `3`.
-   `CPOA_SERVICE_RETRY_BACKOFF_FACTOR`: Base factor for exponential backoff (seconds). Default: `0.5`.
-   `# WCHA_SERVICE_URL`: (Commented out) WCHA is used as a library.

## Dependencies

Listed in `requirements.txt` (e.g., `requests`, `python-dotenv`).

## Running and Testing

CPOA is a library module. Its `main.py` includes a `if __name__ == "__main__":` block for direct testing of orchestration logic.
To run these tests:
1. Ensure dependent services are running.
2. Set environment variables.
3. Execute: `python aethercast/cpoa/main.py`.
This simulates podcast generation scenarios.

Formal unit tests are in `aethercast/cpoa/tests/`. Run with `python -m unittest discover aethercast/cpoa/tests`.

## Database Interaction & Data Persistence Notes

-   CPOA updates the `podcasts` table for task tracking.
-   **Snippet Data Persistence:**
    -   The `orchestrate_snippet_generation` function calls SCA and IGA. The `image_url` obtained from IGA is a GCS URI and is part of the returned `SnippetDataObject`.
    -   The helper `_save_snippet_to_db` saves snippet information (title, summary, text, cover_art_prompt, `image_url` as GCS URI, etc.) to the `topics_snippets` table.
-   CPOA functions accept `task_id` and optional `voice_params_input`, `client_id`, `user_preferences`, and `test_scenarios`. The `db_path` parameter has been removed as CPOA now uses its configured `DATABASE_TYPE`.
-   `user_preferences` can influence agent calls (e.g., preferred VFA voice).
-   `test_scenarios` allow passing headers like `X-Test-Scenario` to downstream services for testing.
-   The final dictionary from `orchestrate_podcast_generation` includes status, error messages, ASF details, the GCS URI for the audio (`final_audio_filepath`), stream ID, actual TTS settings used, a detailed CPOA internal orchestration log, and now also a `workflow_id`.

## Workflow State Management

CPOA now implements robust state management for its orchestration flows using two primary PostgreSQL tables: `workflow_instances` and `task_instances`. This provides enhanced observability, debugging capabilities, and a foundation for future features like workflow resumption.

-   **`workflow_instances`**: Each call to a major CPOA orchestration function (e.g., `orchestrate_podcast_generation`, `orchestrate_landing_page_snippets`) creates a record here. This table tracks the overall workflow, including:
    -   `workflow_id` (unique identifier for the entire process).
    -   `user_id` (if provided from an authenticated API Gateway call).
    -   `trigger_event_type` (e.g., "podcast_generation", "landing_page_snippets").
    -   `trigger_event_details_json` (initial parameters of the request).
    -   `overall_status` ("pending", "in_progress", "completed", "failed", "completed_with_errors").
    -   Timestamps, evolving context data (like generated GCS URIs), and top-level error messages.
-   **`task_instances`**: Each call to an external agent (TDA, SCA, PSWA, VFA, IGA) or significant internal step within a workflow is logged as a task instance. This table tracks:
    -   `task_id` (unique identifier for the specific task).
    -   `workflow_id` (linking back to the parent workflow).
    -   `agent_name` (e.g., "TDA", "PSWA").
    -   `task_order`, `status`, `input_params_json`, `output_result_summary_json`, `error_details_json`, timestamps, and `retry_count`.

**Interaction Flow:**
1.  When a CPOA orchestration function is called, it first creates a `workflow_instance` record.
2.  Before each call to an agent (e.g., TDA, PSWA), a `task_instance` record is created.
3.  After the agent call completes or fails, the corresponding `task_instance` record is updated with the status and outcome.
4.  Once all steps in the workflow are done, or if a critical error occurs, the `workflow_instances` record is updated to its final status.

**Key Changes to Orchestration Functions:**
-   They now accept an optional `user_id: Optional[str]` parameter, which is passed by the API Gateway for authenticated requests and stored in `workflow_instances`.
-   They now return a `workflow_id` (string) as part of their primary response dictionary, allowing callers (like the API Gateway) to reference the specific workflow instance.

This detailed state tracking occurs in the PostgreSQL database, managed by internal CPOA helper functions (`_create_workflow_instance`, `_update_task_instance_status`, etc.). For more details on the schema, see `docs/architecture/CPOA_State_Management.md`.
The legacy `podcasts` table is still updated by `orchestrate_podcast_generation` for backward compatibility with existing API Gateway logic, but the new `workflow_instances` and `task_instances` tables provide a more granular and comprehensive state management solution.
```
