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
-   The final dictionary from `orchestrate_podcast_generation` includes status, error messages, ASF details, the GCS URI for the audio (`final_audio_filepath`), stream ID, actual TTS settings used, and a detailed orchestration log.
```
