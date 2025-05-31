# Central Podcast Orchestrator Agent (CPOA)

## Purpose

The Central Podcast Orchestrator Agent (CPOA) is the core component responsible for managing the entire podcast generation lifecycle within the Aethercast system. It receives requests (typically from the API Gateway) and coordinates a series of specialized agents to produce the final podcast audio or snippets.

Key responsibilities include:

-   **Workflow Management:** Orchestrating multi-step workflows involving other agents:
    -   **Full Podcast Generation:** Coordinates with WebContentHarvesterAgent (WCHA), PodcastScriptWeaverAgent (PSWA), and VoiceForgeAgent (VFA).
        -   Receives a structured JSON script from PSWA, which it then forwards to VFA.
        -   Accepts optional voice parameters (e.g., voice name, language, speaking rate, pitch) and an optional `client_id` (for UI updates) from the caller (API Gateway). Voice parameters are forwarded to VFA.
        -   It also notifies the AudioStreamFeeder (ASF) when new audio is ready for streaming.
    -   **Snippet Generation:** Coordinates with SnippetCraftAgent (SCA) (which might internally use a TopicDiscoveryAgent or similar logic). After SCA generates a snippet, CPOA saves this snippet to the shared `topics_snippets` database (using the `CPOA_DATABASE_PATH` configuration).
-   **Task State Management:** Updates the status of podcast generation tasks in a shared database (specifically, the `podcasts` table). The API Gateway initiates tasks, and CPOA updates their progress.
-   **Agent Communication:** Makes HTTP requests to downstream services (PSWA, VFA, SCA, ASF). For VFA, this includes the structured script from PSWA and any optional voice parameters.
-   **Real-time UI Updates:** If a `client_id` is provided for a podcast generation task, CPOA sends status update messages (e.g., "Fetching content...", "Synthesizing audio...", "Task completed/failed") to an internal endpoint on ASF. ASF then relays these messages to the specific frontend client identified by `client_id` via WebSockets.
-   **Error Handling and Resilience:** Implements retry mechanisms for service calls and manages failures within the orchestration process, providing detailed error feedback (including to the UI if `client_id` is available).

CPOA itself is not a directly exposed service with its own API endpoints for external clients. Instead, it's a Python module whose functions are called by the API Gateway.

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
-   `CPOA_DATABASE_PATH`: Path to the shared SQLite database file. This **must** be the same path used by the API Gateway and TDA. CPOA uses this database to update podcast task statuses in the `podcasts` table and to save generated snippets into the `topics_snippets` table.
    -   *Default:* `cpoa_orchestration_tasks.db` (Note: The default name set in `os.getenv` might differ from the API Gateway's default `aethercast_podcasts.db`. Ensure the actual environment variable in `.env` or the deployment environment points to the correct shared database, e.g., `../api_gateway/aethercast_podcasts.db`.)
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
-   CPOA's `orchestrate_podcast_generation` function receives a `task_id` (which is the `podcast_id`), `db_path`, optional `voice_params_input`, and an optional `client_id` from the API Gateway.
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
-   The `script` CPOA receives from PSWA and sends to VFA is a structured JSON object.
```
