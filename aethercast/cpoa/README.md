# Central Podcast Orchestrator Agent (CPOA)

## Purpose

The Central Podcast Orchestrator Agent (CPOA) is the core component responsible for managing the entire podcast generation lifecycle within the Aethercast system. It receives requests (typically from the API Gateway) and coordinates a series of specialized agents to produce the final podcast audio or snippets.

Key responsibilities include:

-   **Workflow Management:** Orchestrating multi-step workflows involving other agents:
    -   **Full Podcast Generation:** Coordinates with WebContentHarvesterAgent (WCHA), PodcastScriptWeaverAgent (PSWA), and VoiceForgeAgent (VFA).
        -   Receives a structured JSON script from PSWA, which it then forwards to VFA.
        -   Accepts optional voice parameters (e.g., voice name, language, speaking rate, pitch) and an optional `client_id` (for UI updates) from the caller (API Gateway). Voice parameters are forwarded to VFA.
        -   It also notifies the AudioStreamFeeder (ASF) when new audio is ready for streaming.
    -   **Individual Snippet Generation (`orchestrate_snippet_generation`):** Coordinates with SnippetCraftAgent (SCA) to generate snippet text and a `cover_art_prompt` based on input `topic_info`. After SCA, it calls the Image Generation Agent (IGA) with the `cover_art_prompt` to get an `image_url`. The final snippet (potentially with `image_url`) is saved to the database and returned. This function is used by both search result generation and landing page snippet orchestration.
    -   **Search Results Generation (`orchestrate_search_results_generation`):**
        -   Accepts a search query from the API Gateway.
        -   Calls the Topic Discovery Agent (TDA) to find relevant topics based on the query.
        -   For each topic found by TDA, it then calls the `orchestrate_snippet_generation` function (which includes IGA call) to generate a descriptive snippet.
        -   Returns a list of these generated snippets to the API Gateway to be used as search results.
    -   **Landing Page Snippet Orchestration (`orchestrate_landing_page_snippets`):**
        -   Orchestrates the generation of multiple diverse snippets, typically for the application's landing page.
        -   Calls the Topic Discovery Agent (TDA) to obtain a list of current or relevant topics (accepts a `limit` parameter).
        -   For each topic retrieved from TDA, it then calls the `orchestrate_snippet_generation` function. This ensures each snippet is fully formed, including a title, summary, and an `image_url` obtained by calling the Image Generation Agent (IGA) with the `cover_art_prompt` from SCA.
        -   Returns a list of these `SnippetDataObjects`.
    -   **Popular Category Provisioning (`get_popular_categories`):**
        -   Provides a predefined list of popular podcast categories. This list is intended for use by the frontend, for example, to display category filters or navigation.
        -   Currently, the list is hardcoded within CPOA. Future enhancements could involve dynamic category determination based on content trends from TDA or other metrics.
-   **Task State Management:** Updates the status of podcast generation tasks in a shared database (specifically, the `podcasts` table). The API Gateway initiates tasks, and CPOA updates their progress.
-   **Agent Communication:** Makes HTTP requests to downstream services (PSWA, VFA, SCA, ASF, TDA, IGA). For VFA, this includes the structured script from PSWA and any optional voice parameters.
-   **Real-time UI Updates:** If a `client_id` is provided for a podcast generation task, CPOA sends status update messages (e.g., "Fetching content...", "Synthesizing audio...", "Task completed/failed") to an internal endpoint on ASF. ASF then relays these messages to the specific frontend client identified by `client_id` via WebSockets.
-   **Error Handling and Resilience:** Implements retry mechanisms for service calls and manages failures within the orchestration process, providing detailed error feedback (including to the UI if `client_id` is available).

CPOA itself is not a directly exposed service with its own API endpoints for external clients. Instead, it's a Python module whose functions are called by the API Gateway.
        -   For each topic found by TDA, it then calls the Snippet Craft Agent (SCA) (via the internal `orchestrate_snippet_generation` function) to generate a descriptive snippet.
        -   Returns a list of these generated snippets to the API Gateway to be used as search results.

## Configuration

CPOA is configured via environment variables, typically managed in a `.env` file within the `aethercast/cpoa/` directory. Create a `.env` file by copying the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file with your desired settings. The following variables are used:

-   `PSWA_SERVICE_URL`: URL of the PodcastScriptWeaverAgent.
    -   *Default:* `http://localhost:5004/weave_script`
-   `VFA_SERVICE_URL`: URL of the VoiceForgeAgent.
    -   *Default:* `http://localhost:5005/forge_voice`
-   `ASF_NOTIFICATION_URL`: URL for notifying the AudioStreamFeeder about new audio.
    -   *Default:* `http://localhost:5006/asf/internal/notify_new_audio`
-   `ASF_WEBSOCKET_BASE_URL`: Base WebSocket URL for ASF, used to construct client-facing URLs.
    -   *Default:* `ws://localhost:5006/api/v1/podcasts/stream`
-   `SCA_SERVICE_URL`: URL of the SnippetCraftAgent.
    -   *Default:* `http://localhost:5002/craft_snippet`
-   `CPOA_ASF_SEND_UI_UPDATE_URL`: The internal URL on the AudioStreamFeeder (ASF) service that CPOA calls to send real-time UI status updates for a specific client.
    -   *Default:* `http://localhost:5006/asf/internal/send_ui_update`
-   `IGA_SERVICE_URL`: URL of the Image Generation Agent (IGA) placeholder service. Used by CPOA to fetch image URLs for snippets based on prompts from SCA.
    -   *Default (in code if env var not set):* `http://localhost:5007`
    -   *Docker Compose value (from common.env via CPOA's .env):* `http://iga:5007`
-   `SHARED_DATABASE_PATH`: Path to the shared SQLite database file (formerly CPOA_DATABASE_PATH). This **must** be the same path used by the API Gateway and TDA. CPOA uses this database to update podcast task statuses in the `podcasts` table and to save generated snippets into the `topics_snippets` table.
    -   *Default (in code if env var not set):* `/app/database/aethercast_podcasts.db` (This aligns with recent standardization).
-   `CPOA_SERVICE_RETRY_COUNT`: Number of times to retry failed HTTP requests to downstream services.
    -   *Default:* `3`
-   `CPOA_SERVICE_RETRY_BACKOFF_FACTOR`: Base factor for exponential backoff between retries (in seconds).
    -   *Default:* `0.5`
-   `# WCHA_SERVICE_URL`: (Commented out by default) URL if WCHA were run as a separate service. Currently, WCHA is used as a direct library import.
    -   *Example if used:* `http://localhost:5003/harvest_content_endpoint`

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This typically includes libraries like `requests` and `python-dotenv`.

## Running and Testing

CPOA is primarily a library module invoked by the API Gateway. However, its `main.py` script includes a `if __name__ == "__main__":` block that allows for direct testing of the orchestration logic.

To run these tests:

1.  Ensure all dependent services (PSWA, VFA, ASF, SCA, as per the configured URLs) are running.
2.  Set up the necessary environment variables (e.g., in a `.env` file).
3.  Execute the script directly:
    ```bash
    python aethercast/cpoa/main.py
    ```
This will simulate a few podcast generation scenarios and print detailed output. The test block also initializes a local SQLite database (`cpoa_test_orchestration.db` or as configured by `CPOA_DATABASE_PATH` for the test) if it doesn't exist, using the schema expected by CPOA for its updates.

For formal unit tests, see the files in the `aethercast/cpoa/tests/` directory. These can be run using Python's `unittest` module:
```bash
python -m unittest discover aethercast/cpoa/tests
```

## Database Interaction & Output Structure

-   CPOA expects the API Gateway to create an initial record for a podcast task in the shared database.
-   CPOA's `orchestrate_podcast_generation` function receives:
    -   `task_id` (which is the `podcast_id`)
    -   `db_path`
    -   `voice_params_input` (optional): Specific voice parameters for this generation task.
    -   `client_id` (optional): For sending UI updates via ASF.
    -   `user_preferences` (optional, dict): User-specific preferences (e.g., preferred VFA voice name) fetched by the API Gateway. CPOA uses these to influence agent calls if not overridden by direct inputs (e.g., `voice_params_input`). For example, `user_preferences["preferred_vfa_voice_name"]` can set the voice if `voice_params_input` doesn't specify one.
    -   `test_scenarios` (optional, dict): For integration testing. Allows specifying test scenarios for downstream services (e.g., `{"pswa": "insufficient_content"}`). CPOA passes these as `X-Test-Scenario` headers to the respective services.
-   If `client_id` is provided, CPOA will attempt to send status updates (e.g., "Fetching content...", "Synthesizing audio...", "Task completed/failed") to the configured `CPOA_ASF_SEND_UI_UPDATE_URL`. These updates are intended to be relayed by ASF to the specific frontend client.
-   During its operation, CPOA updates the `cpoa_status`, `cpoa_error_message`, and `last_updated_timestamp` fields of the existing record in the `podcasts` table using its internal `_update_task_status_in_db` function.
-   The final dictionary returned by `orchestrate_podcast_generation` provides comprehensive details for the API Gateway. This includes:
    -   `task_id`, `topic`
    -   `status`: The final CPOA status (e.g., "completed", "failed_vfa_error").
    -   `error_message`: Any final error message.
    -   `asf_notification_status`: Status of the notification sent to ASF.
    -   `asf_websocket_url`: The WebSocket URL for clients to connect to ASF for this podcast.
    -   `final_audio_details`: A dictionary containing details from VFA's response, including:
        -   `audio_filepath`: Path to the generated audio file.
        -   `stream_id`: The stream ID for ASF.
        -   `tts_settings_used`: A dictionary of the actual TTS settings (voice name, language, rate, pitch, encoding) that VFA used for synthesis. This is passed through from VFA's response.
    -   `orchestration_log`: A detailed log of the orchestration steps.
-   The `orchestrate_topic_exploration` function also accepts `user_preferences` and `test_scenarios` for similar logging and potential future use, though `test_scenarios` are not currently passed to TDA/SCA by this function.
-   The `script` CPOA receives from PSWA and sends to VFA is a structured JSON object.
```
