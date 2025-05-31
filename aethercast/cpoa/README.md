# Central Podcast Orchestrator Agent (CPOA)

## Purpose

The Central Podcast Orchestrator Agent (CPOA) is the core component responsible for managing the entire podcast generation lifecycle within the Aethercast system. It receives requests (typically from the API Gateway) and coordinates a series of specialized agents to produce the final podcast audio or snippets.

Key responsibilities include:

-   **Workflow Management:** Orchestrating multi-step workflows involving other agents:
    -   **Full Podcast Generation:** Coordinates with WebContentHarvesterAgent (WCHA), PodcastScriptWeaverAgent (PSWA), and VoiceForgeAgent (VFA). It also notifies the AudioStreamFeeder (ASF) when new audio is ready.
    -   **Snippet Generation:** Coordinates with SnippetCraftAgent (SCA) (which might internally use a TopicDiscoveryAgent or similar logic).
-   **Task State Management:** Updates the status of podcast generation tasks in a shared database. The API Gateway initiates tasks, and CPOA updates their progress.
-   **Agent Communication:** Makes HTTP requests to downstream services (PSWA, VFA, SCA, ASF).
-   **Error Handling and Resilience:** Implements retry mechanisms for service calls and manages failures within the orchestration process, providing detailed error feedback.

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
-   `CPOA_DATABASE_PATH`: Path to the SQLite database file used for storing podcast task information. This **must** be the same path used by the API Gateway.
    -   *Default:* `cpoa_orchestration_tasks.db` (Note: The default name might differ from the API Gateway's default `aethercast_podcasts.db`. Ensure they match in a deployment.)
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

## Database Interaction

-   CPOA expects the API Gateway to create an initial record for a podcast task in the shared database.
-   CPOA's `orchestrate_podcast_generation` function receives a `task_id` (which is the `podcast_id`) and `db_path` from the API Gateway.
-   During its operation, CPOA updates the `cpoa_status`, `cpoa_error_message`, and `last_updated_timestamp` fields of the existing record in the `podcasts` table using its internal `_update_task_status_in_db` function.
-   The final dictionary returned by `orchestrate_podcast_generation` provides comprehensive details that the API Gateway uses for its final update to the podcast record.
