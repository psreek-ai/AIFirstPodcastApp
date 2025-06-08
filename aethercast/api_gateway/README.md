# API Gateway

## Purpose

The API Gateway is the primary entry point for external clients (like the frontend UI) to interact with the Aethercast system. It provides a unified interface for discovering content, generating podcasts, managing user sessions, and retrieving podcast information and audio.

Key Responsibilities:

1.  **Request Routing & Validation:** Receives HTTP requests from clients, validates them, and routes them to the appropriate internal services (primarily CPOA) or handles them directly.
2.  **Service Orchestration (Delegation):** For most operations, it delegates to the Central Podcast Orchestrator Agent (CPOA), which handles complex tasks like podcast generation, snippet creation, and search.
3.  **Frontend Serving:** Serves the static files (HTML, CSS, JavaScript) for the Aethercast web frontend.
4.  **Database Interaction:** Manages interactions with a shared database (typically SQLite) for tasks such as:
    *   Creating and updating records in the `podcasts` table for tracking podcast generation tasks.
    *   Managing user session data in the `user_sessions` table.
    *   (Note: Direct caching from `topics_snippets` by the API Gateway for the main snippets endpoint has been removed in favor of CPOA-led generation.)
5.  **Response Formatting:** Consolidates responses from CPOA (or its own data) and formats them into user-friendly JSON responses for the client.
6.  **Session Management:** Provides endpoints for initializing user sessions and managing user preferences.

## Configuration

The API Gateway is configured via environment variables, typically managed in a `.env` file within the `aethercast/api_gateway/` directory. Copy `.env.example` to `.env` and modify as needed.

Key Environment Variables:

-   `SHARED_DATABASE_PATH`: Path to the SQLite database file. This **must** be the same path used by CPOA and other services for shared data access.
    -   *Docker Default:* `/app/database/aethercast_podcasts.db`
    -   *Local Default in `main.py` if not set:* `/app/database/aethercast_podcasts.db` (effective path depends on execution context).
-   `TDA_SERVICE_URL`: The URL of the Topic Discovery Agent (TDA) service. (Note: Direct calls to TDA from API Gateway might be deprecated if CPOA handles all topic/snippet interactions).
    -   *Default:* `http://localhost:5000/discover_topics`
-   `API_GW_HOST`: Host for the API Gateway's Flask development server when run directly.
    -   *Default in `main.py` if run directly:* `0.0.0.0`
-   `API_GW_PORT`: Port for the API Gateway's Flask development server when run directly.
    -   *Default in `main.py` if run directly:* `5001`
-   `API_GW_DEBUG_MODE`: Enables or disables Flask debug mode when run directly (`True` or `False`).
    -   *Default in `main.py` if run directly:* `True`
-   `FEND_DIR`: (Optional) Path to the frontend static files directory.
    -   *Note:* This is typically derived in `main.py` relative to its own location (e.g., `../fend`). Setting this environment variable can override the derivation.

**Deprecated Environment Variables (for Snippet Fetching):**
The following variables were previously used for API Gateway-level snippet caching but are no longer utilized by the `/api/v1/snippets` endpoint, as this logic is now handled by CPOA:
-   `API_GW_SNIPPET_CACHE_SIZE`
-   `API_GW_SNIPPET_CACHE_MAX_AGE_HOURS`

**Standard Flask Environment Variables:**
When using the `flask` command directly, you might set:
-   `FLASK_APP=aethercast/api_gateway/main.py`
-   `FLASK_RUN_HOST` (overrides `API_GW_HOST` for flask command)
-   `FLASK_RUN_PORT` (overrides `API_GW_PORT` for flask command)
-   `FLASK_DEBUG` (overrides `API_GW_DEBUG_MODE` for flask command)

*(Note: For a production deployment, a proper WSGI server like Gunicorn or uWSGI should be used instead of the Flask development server.)*

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `requests`, and `python-dotenv`.

## Running the Service

1.  Ensure all backend services that the API Gateway depends on (primarily CPOA and its underlying services) are running and configured.
2.  Set up the necessary environment variables.
3.  Initialize the database: When `main.py` is run directly, `init_db()` is called, creating the schema if the database file doesn't exist.
4.  Run the Flask development server:
    ```bash
    python aethercast/api_gateway/main.py
    ```
    This will start the service, typically on `http://0.0.0.0:5001`.

## API Endpoints

### Frontend

-   **`GET /`**: Serves `index.html`.
-   **`GET /style.css`**: Serves CSS.
-   **`GET /app.js`**: Serves JavaScript.

### Health Check

-   **`GET /health`**
    -   **Description:** Returns API Gateway health, CPOA import status, and database connectivity.
    -   **Success Response (200 OK):** (Structure includes `status`, `cpoa_module_status`, individual CPOA function statuses, `database_status`).

### Snippets & Categories

-   **`GET /api/v1/snippets`**
    -   **Description:** Fetches dynamically generated podcast snippets via CPOA, suitable for a landing page. CPOA orchestrates topic discovery (TDA), snippet text generation (SCA), and image generation (IGA), returning snippets that include an `image_url`.
    -   **Query Parameters:** `limit` (optional, integer, default 6, max 20): Number of snippets.
    -   **Success Response (200 OK):** `{"snippets": [...], "source": "generation"}` (content from CPOA, where each snippet object can contain an `image_url`).
    -   **Error Responses:** 503 (CPOA/downstream unavailable), 500 (other errors).

-   **`GET /api/v1/categories`**
    -   **Description:** Fetches a list of predefined podcast categories from CPOA.
    -   **Success Response (200 OK):** `{"categories": ["Technology", "Science", ...]}`.
    -   **Error Responses:** 503 (CPOA unavailable), 500 (CPOA error).

-   **`POST /api/v1/topics/explore`**
    -   **Description:** Explores topics related to a given `current_topic_id` or a set of `keywords`. Delegates to CPOA's `orchestrate_topic_exploration` which generates new snippets (including text and image URLs).
    -   **Request Payload (JSON):**
        ```json
        {
            "current_topic_id": "topic_abc123", // Optional
            "keywords": ["space travel", "mars colonization"], // Optional, list of strings
            "depth_mode": "broader", // Optional, string, e.g., "deeper", "broader". Default: "deeper"
            "client_id": "your_session_id" // Optional, for fetching user preferences
        }
        ```
        *Note: At least `current_topic_id` or `keywords` must be provided.*
    -   **Success Response (200 OK):** `{"explored_topics": [...]}` (list of snippet objects from CPOA).
    -   **Error Responses:**
        -   **400 Bad Request:** For issues like:
            -   Missing or malformed JSON payload (`API_GW_PAYLOAD_REQUIRED`, `API_GW_MALFORMED_JSON`).
            -   Neither `current_topic_id` nor `keywords` provided (`API_GW_EXPLORE_INPUT_REQUIRED`).
            -   `keywords` is not a list, or contains non-string/empty items (`API_GW_EXPLORE_INVALID_KEYWORDS_TYPE`, `API_GW_EXPLORE_INVALID_KEYWORD_ITEM`).
            -   `current_topic_id`, `depth_mode`, or `client_id` (if provided) are not non-empty strings (`API_GW_EXPLORE_INVALID_TOPIC_ID`, `API_GW_EXPLORE_INVALID_DEPTH_MODE`, `API_GW_CLIENT_ID_INVALID`).
        -   **503 Service Unavailable:** If CPOA service is unavailable or reports an issue with its downstream dependencies (TDA, SCA).
        -   **500 Internal Server Error:** For other unexpected errors.

### Podcast Task Management

-   **`POST /api/v1/podcasts`**
    -   **Description:** Initiates a new podcast generation task. Calls CPOA to orchestrate generation. If `client_id` is provided, user preferences from the session are fetched and passed to CPOA.
    -   **Request Payload (JSON):**
        ```json
        {
            "topic": "The History of Podcasting",
            "voice_params": { /* Optional: VFA voice settings */ },
            "client_id": "your_session_id", // Optional
            "test_scenarios": { /* Optional: For CPOA test scenarios */ }
        }
        ```
    -   **Success Response (201 Created or 200 OK):** Includes `podcast_id`, `topic`, `generation_status`, `audio_url` (if successful), and `details` (full CPOA result). Status code depends on CPOA outcome.
    -   **Error Responses:**
        -   **400 Bad Request:** For issues like:
            -   Missing or malformed JSON payload (`API_GW_PAYLOAD_REQUIRED`, `API_GW_MALFORMED_JSON`).
            -   `topic` is missing or not a non-empty string (`API_GW_PODCAST_TOPIC_INVALID`).
            -   `voice_params` (if provided) is not an object (`API_GW_PODCAST_INVALID_VOICE_PARAMS_TYPE`).
            -   `client_id` (if provided) is not a non-empty string (`API_GW_PODCAST_INVALID_CLIENT_ID`).
            -   `test_scenarios` (if provided) is not an object (`API_GW_PODCAST_INVALID_TEST_SCENARIOS_TYPE`).
        -   **503 Service Unavailable:** If CPOA service is unavailable.
        -   **500 Internal Server Error:** For database errors during task creation or other unexpected errors.

-   **`GET /api/v1/podcasts`**
    -   **Description:** Lists all podcast tasks with pagination.
    -   **Query Parameters:** `page` (int, default 1), `per_page` (int, default 10, max 100).
    -   **Success Response (200 OK):** `{"podcasts": [...], "page": ..., "total_podcasts": ..., "total_pages": ...}`.

-   **`GET /api/v1/podcasts/<podcast_id>`**
    -   **Description:** Retrieves detailed information for a specific podcast task.
    -   **Success Response (200 OK):** Full podcast details including status, logs, filepaths, and `tts_settings_used`.

-   **`GET /api/v1/podcasts/<podcast_id>/audio.mp3`**
    -   **Description:** Serves the generated audio file.
    -   **Success Response (200 OK):** Raw audio data.
    -   **Error Responses:** 404 (not found), 500 (DB error).

### Search

-   **`POST /api/v1/search/podcasts`**
    -   **Description:** Searches for podcast topics via CPOA based on a query. If `client_id` is provided, user preferences from the session are fetched and passed to CPOA.
    -   **Request Payload (JSON):**
        ```json
        {
            "query": "artificial intelligence in healthcare",
            "client_id": "your_session_id" // Optional
        }
        ```
    -   **Success Response (200 OK):** `{"search_results": [...]}` (content from CPOA).
    -   **Error Responses:**
        -   **400 Bad Request:** For issues like:
            -   Missing or malformed JSON payload (`API_GW_PAYLOAD_REQUIRED`, `API_GW_MALFORMED_JSON`).
            -   `query` is missing or not a non-empty string (`API_GW_SEARCH_QUERY_INVALID`).
            -   `client_id` (if provided) is not a non-empty string (`API_GW_CLIENT_ID_INVALID`).
        -   **503 Service Unavailable:** If CPOA service or its downstream dependencies (TDA, SCA) are unavailable.
        -   **500 Internal Server Error:** For other unexpected errors.

### Session Management Endpoints

-   **`POST /api/v1/session/init`**
    -   **Description:** Initializes or acknowledges a user session. Creates a session record if `client_id` is new, or updates `last_seen_timestamp` if existing. Returns current preferences.
    -   **Request Payload (JSON):** `{"client_id": "your_frontend_generated_id"}`.
    -   **Success Response (200 OK):** `{"client_id": "...", "preferences": {...}}`.
    -   **Error Responses:** 400 (missing `client_id`), 500 (DB error).

-   **`GET /api/v1/session/preferences`**
    -   **Description:** Retrieves current preferences for a given user session.
    -   **Query Parameters:** `client_id` (string, required).
    -   **Success Response (200 OK):** `{"client_id": "...", "preferences": {...}}`.
    -   **Error Responses:** 400 (missing `client_id`), 404 (session not found), 500 (DB error).

-   **`POST /api/v1/session/preferences`**
    -   **Description:** Updates (replaces) preferences for a given user session.
    -   **Request Payload (JSON):** `{"client_id": "your_session_id", "preferences": {"key": "value", ...}}`.
        -   `client_id` (string, required): Must be a non-empty string.
        -   `preferences` (object, required): Must be a valid JSON object (dictionary).
    -   **Success Response (200 OK):** `{"client_id": "...", "message": "Preferences updated successfully."}`.
    -   **Error Responses:**
        -   **400 Bad Request:** For issues like:
            -   Missing or malformed JSON payload (`API_GW_PAYLOAD_REQUIRED`, `API_GW_MALFORMED_JSON`).
            -   `client_id` is missing or not a non-empty string (`API_GW_SESSION_CLIENT_ID_INVALID`).
            -   `preferences` is missing or not an object (`API_GW_SESSION_INVALID_PREFERENCES_PAYLOAD`).
        -   **404 Not Found:** If the session for the given `client_id` does not exist.
        -   **500 Internal Server Error:** For database errors.

## Database Schema Details

The API Gateway, CPOA, and other services may utilize a shared SQLite database. Key tables include:

### `podcasts` Table
-   **Purpose:** Tracks the status and metadata of each podcast generation task.
-   **Key Columns:** `podcast_id` (PK), `topic`, `cpoa_status`, `cpoa_error_message`, `final_audio_filepath`, `stream_id`, `asf_websocket_url`, `asf_notification_status`, `task_created_timestamp`, `last_updated_timestamp`, `cpoa_full_orchestration_log` (JSON), `tts_settings_used` (JSON).

### `topics_snippets` Table
-   **Purpose:** Stores discovered topics (e.g., from TDA) and generated snippets (e.g., by SCA via CPOA). While the API Gateway's `/api/v1/snippets` endpoint now relies on CPOA for on-demand generation rather than direct caching from this table, this table may still be used by CPOA or other backend agents for their internal caching or operational needs.
-   **Key Columns:** `id` (PK), `type` ('topic' or 'snippet'), `title`, `summary`, `keywords` (JSON), `source_url`, `source_name`, `original_topic_details` (JSON for snippets), `llm_model_used_for_snippet`, `cover_art_prompt`, `image_url TEXT`, `generation_timestamp`, `last_accessed_timestamp`, `relevance_score`.

### `generated_scripts` Table
-   **Purpose:** Serves as a cache for podcast scripts generated by the Podcast Script Weaver Agent (PSWA), typically managed by CPOA. This helps avoid re-generating scripts for identical topics if a fresh script is already available.
-   **Key Columns:** `script_id` (PK), `topic_hash` (UNIQUE, hash of topic identifiers), `structured_script_json` (JSON of the script), `generation_timestamp`, `llm_model_used`, `last_accessed_timestamp`.

### `user_sessions` Table
-   **Purpose:** Stores user session identifiers (client IDs) and their associated preferences, allowing for personalized experiences across requests.
-   **Key Columns:** `session_id` (PK, corresponds to `client_id`), `created_timestamp`, `last_seen_timestamp`, `preferences_json` (JSON string for user preferences).
