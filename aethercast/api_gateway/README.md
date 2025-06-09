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
    *   Managing user accounts in the `users` table for authentication.
    *   (Note: Direct caching from `topics_snippets` by the API Gateway for the main snippets endpoint has been removed in favor of CPOA-led generation.)
5.  **Response Formatting:** Consolidates responses from CPOA (or its own data) and formats them into user-friendly JSON responses for the client.
6.  **Session Management:** Provides endpoints for initializing user sessions and managing user preferences.
7.  **User Authentication:** Provides endpoints for user registration and login, issuing JWTs for accessing protected routes.

## Authentication

Several API endpoints require authentication using JSON Web Tokens (JWT).

**Authentication Flow:**

1.  **Registration:** New users should register using the `POST /auth/register` endpoint.
2.  **Login:** Registered users can log in using the `POST /auth/login` endpoint to obtain an access token.
3.  **Accessing Protected Routes:** For endpoints marked with "**Authentication Required**", the client must include the obtained access token in the `Authorization` header of their HTTP request, using the "Bearer" scheme.
    -   Example: `Authorization: Bearer <your_jwt_token>`
4.  **Token Expiration:** Access tokens are short-lived (currently 1 hour). If a token expires, the API will return a 401 Unauthorized error, and the client will need to re-authenticate (login again) to obtain a new token. (A refresh token mechanism is not part of the current implementation).

If a token is missing, invalid, or expired, the API will respond with a 401 Unauthorized error, usually with an `error_code` like `AUTH_MISSING_TOKEN`, `AUTH_INVALID_TOKEN`, or `AUTH_EXPIRED_TOKEN`.

## Configuration

The API Gateway is configured via environment variables, typically managed in a `.env` file within the `aethercast/api_gateway/` directory. Copy `.env.example` to `.env` and modify as needed.

Key Environment Variables:

-   `SHARED_DATABASE_PATH`: Path to the SQLite database file. This **must** be the same path used by CPOA and other services for shared data access.
    -   *Docker Default:* `/app/database/aethercast_podcasts.db`
    -   *Local Default in `main.py` if not set:* `/app/database/aethercast_podcasts.db` (effective path depends on execution context).
-   `FLASK_SECRET_KEY`: **Required for JWT signing and session security.** A strong, random secret key. It's critical to set this to a persistent random value in your `.env` file for production. If not set, a temporary key is generated at startup (which is insecure and will invalidate tokens/sessions on restart).
    -   *Example in `.env`:* `FLASK_SECRET_KEY=your_random_generated_secret_string`
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
-   `GCS_BUCKET_NAME`: (Optional) The name of the Google Cloud Storage bucket. While the signed URL generation can derive the bucket name from the `gs://` URI, this can be used for validation or as a default. Required if such validation is active.
    -   *Example:* `GCS_BUCKET_NAME=your-aethercast-gcs-bucket`
-   `GOOGLE_APPLICATION_CREDENTIALS`: Path to the GCP service account key file. This is essential for the API Gateway to authenticate with GCS and generate signed URLs.
    -   *Example (in Docker):* `/app/gcp-credentials.json` (assuming the key file is mounted there).

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
This includes `Flask`, `requests`, `python-dotenv`, `PyJWT` (for JWT handling), `Werkzeug` (for password hashing, often a Flask sub-dependency), and `google-cloud-storage` (for GCS signed URL generation).

## Running the Service

1.  Ensure all backend services that the API Gateway depends on (primarily CPOA and its underlying services) are running and configured.
2.  Set up the necessary environment variables, including a `FLASK_SECRET_KEY`.
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

### Authentication Endpoints

-   **`POST /auth/register`**
    -   **Description:** Registers a new user.
    -   **Request Payload (JSON):**
        ```json
        {
            "username": "newuser",
            "email": "user@example.com",
            "password": "securepassword123"
        }
        ```
        - `username` (string, required): Must be unique.
        - `email` (string, required): Must be unique.
        - `password` (string, required): Minimum 8 characters.
    -   **Success Response (201 Created):** `{"message": "User registered successfully.", "user_id": "..."}`.
    -   **Error Responses:**
        -   400 Bad Request: If validation fails (e.g., missing fields, weak password) (`API_GW_AUTH_INVALID_USERNAME`, `API_GW_AUTH_INVALID_EMAIL`, `API_GW_AUTH_INVALID_PASSWORD`).
        -   409 Conflict: If username or email already exists (`API_GW_AUTH_USER_EXISTS`).
        -   500 Internal Server Error: For database errors (`API_GW_AUTH_DB_ERROR_REGISTER`).

-   **`POST /auth/login`**
    -   **Description:** Logs in an existing user and returns a JWT access token.
    -   **Request Payload (JSON):**
        ```json
        {
            "login_identifier": "user@example.com", // Can be username or email
            "password": "securepassword123"
        }
        ```
    -   **Success Response (200 OK):** `{"access_token": "your_jwt_here", "user_id": "...", "username": "..."}`.
    -   **Error Responses:**
        -   400 Bad Request: If validation fails (e.g., missing fields) (`API_GW_AUTH_INVALID_LOGIN_ID`, `API_GW_AUTH_INVALID_PASSWORD_LOGIN`).
        -   401 Unauthorized: If credentials are invalid (`API_GW_AUTH_INVALID_CREDENTIALS`).
        -   500 Internal Server Error: For JWT generation or database errors (`API_GW_AUTH_JWT_GENERATION_FAILED`, `API_GW_AUTH_DB_ERROR_LOGIN`).

### Snippets & Categories

-   **`GET /api/v1/snippets`**
    -   **Description:** Fetches dynamically generated podcast snippets via CPOA, suitable for a landing page. CPOA orchestrates topic discovery (TDA), snippet text generation (SCA), and image generation (IGA). `image_url`s for snippets (originally GCS URIs from IGA via CPOA) are converted to short-lived signed GCS HTTP URLs by the API Gateway before being sent to the client. If a valid `Authorization: Bearer <token>` is provided, the `user_id` is opportunistically passed to CPOA for potential personalization and is associated with the created workflow.
    -   **Query Parameters:** `limit` (optional, integer, default 6, max 20): Number of snippets.
    -   **Success Response (200 OK):**
        ```json
        {
            "workflow_id": "uuid-of-the-cpoa-workflow",
            "snippets": [
                {
                    "snippet_id": "...",
                    "title": "...",
                    "image_url": "https://signed-gcs-url-for-image...",
                    "..."
                }
            ],
            "source": "generation"
        }
        ```
    -   **Error Responses:** 503 (CPOA/downstream unavailable), 500 (other errors, including signed URL generation failure).

-   **`GET /api/v1/categories`**
    -   **Description:** Fetches a list of predefined podcast categories from CPOA.
    -   **Success Response (200 OK):** `{"categories": ["Technology", "Science", ...]}`.
    -   **Error Responses:** 503 (CPOA unavailable), 500 (CPOA error).

-   **`POST /api/v1/topics/explore`**
    -   **Description:** (**Authentication Required.**) Explores topics related to a given `current_topic_id` or a set of `keywords`. Delegates to CPOA's `orchestrate_topic_exploration`. The authenticated user's `user_id` is passed to CPOA and associated with the workflow. `image_url`s in the returned snippets are converted to short-lived signed GCS HTTP URLs. Requires a valid Bearer token in the `Authorization` header.
    -   **Request Payload (JSON):**
        ```json
        {
            "current_topic_id": "topic_abc123", // Optional
            "keywords": ["space travel", "mars colonization"], // Optional, list of strings
            "depth_mode": "broader", // Optional, string, e.g., "deeper", "broader". Default: "deeper"
            "client_id": "your_session_id" // Optional, for fetching user preferences for CPOA
        }
        ```
        *Note: At least `current_topic_id` or `keywords` must be provided.*
    -   **Success Response (200 OK):**
        ```json
        {
            "workflow_id": "uuid-of-the-cpoa-workflow",
            "explored_topics": [
                {
                    "snippet_id": "...",
                    "title": "...",
                    "image_url": "https://signed-gcs-url-for-image...",
                    "..."
                }
            ]
        }
        ```
    -   **Error Responses:**
        -   **400 Bad Request:** For issues like:
            -   Missing or malformed JSON payload (`API_GW_PAYLOAD_REQUIRED`, `API_GW_MALFORMED_JSON`).
            -   Neither `current_topic_id` nor `keywords` provided (`API_GW_EXPLORE_INPUT_REQUIRED`).
            -   `keywords` is not a list, or contains non-string/empty items (`API_GW_EXPLORE_INVALID_KEYWORDS_TYPE`, `API_GW_EXPLORE_INVALID_KEYWORD_ITEM`).
            -   `current_topic_id`, `depth_mode`, or `client_id` (if provided) are not non-empty strings (`API_GW_EXPLORE_INVALID_TOPIC_ID`, `API_GW_EXPLORE_INVALID_DEPTH_MODE`, `API_GW_CLIENT_ID_INVALID`).
        -   **401 Unauthorized:** If authentication token is missing, invalid, or expired.
        -   **503 Service Unavailable:** If CPOA service is unavailable or reports an issue with its downstream dependencies (TDA, SCA).
        -   **500 Internal Server Error:** For other unexpected errors.

### Podcast Task Management

-   **`POST /api/v1/podcasts`**
    -   **Description:** (**Authentication Required.**) Initiates a new podcast generation task. Calls CPOA to orchestrate generation. The authenticated user's `user_id` is passed to CPOA and associated with the workflow. If `client_id` is provided, user preferences from the session are fetched and passed to CPOA. Requires a valid Bearer token in the `Authorization` header.
    -   **Request Payload (JSON):**
        ```json
        {
            "topic": "The History of Podcasting",
            "voice_params": { /* Optional: VFA voice settings */ },
            "client_id": "your_session_id", // Optional, for fetching user preferences for CPOA
            "test_scenarios": { /* Optional: For CPOA test scenarios */ }
        }
        ```
    -   **Success Response (201 Created or 200 OK):** Includes `podcast_id` (original task ID for CPOA), `workflow_id` (from CPOA state management), `topic`, `generation_status`, `audio_url` (if successful), and `details` (full CPOA result). Status code depends on CPOA outcome.
        ```json
        // Example Success Response
        {
            "podcast_id": "uuid-of-the-podcast-task",
            "workflow_id": "uuid-of-the-cpoa-workflow",
            "topic": "The History of Podcasting",
            "generation_status": "completed", // Or other CPOA status
            "audio_url": "/api/v1/podcasts/uuid-of-the-podcast-task/audio.mp3",
            "details": { /* Full CPOA result object */ }
        }
        ```
    -   **Error Responses:**
        -   **400 Bad Request:** For issues like:
            -   Missing or malformed JSON payload (`API_GW_PAYLOAD_REQUIRED`, `API_GW_MALFORMED_JSON`).
            -   `topic` is missing or not a non-empty string (`API_GW_PODCAST_TOPIC_INVALID`).
            -   `voice_params` (if provided) is not an object (`API_GW_PODCAST_INVALID_VOICE_PARAMS_TYPE`).
            -   `client_id` (if provided) is not a non-empty string (`API_GW_PODCAST_INVALID_CLIENT_ID`).
            -   `test_scenarios` (if provided) is not an object (`API_GW_PODCAST_INVALID_TEST_SCENARIOS_TYPE`).
        -   **401 Unauthorized:** If authentication token is missing, invalid, or expired.
        -   **503 Service Unavailable:** If CPOA service is unavailable.
        -   **500 Internal Server Error:** For database errors during task creation or other unexpected errors.

-   **`GET /api/v1/podcasts`**
    -   **Description:** Lists all podcast tasks with pagination.
    -   **Query Parameters:** `page` (int, default 1), `per_page` (int, default 10, max 100).
    -   **Success Response (200 OK):** `{"podcasts": [...], "page": ..., "total_podcasts": ..., "total_pages": ...}`.

-   **`GET /api/v1/podcasts/<podcast_id>`**
    -   **Description:** Retrieves detailed information for a specific podcast task.
    -   **Success Response (200 OK):** Full podcast details including status, logs, GCS URI for `final_audio_filepath`, and `tts_settings_used`. The `audio_url` field will point to the `/audio.mp3` streaming endpoint.

-   **`GET /api/v1/podcasts/<podcast_id>/audio.mp3`**
    -   **Description:** Redirects to a short-lived, signed GCS HTTP URL for the generated audio file. The actual audio content is served from GCS.
    -   **Success Response (302 Found):** Redirects to the signed GCS URL.
    -   **Error Responses:** 404 (podcast or audio GCS URI not found), 500 (DB error or failure to generate signed URL).

### Search

-   **`POST /api/v1/search/podcasts`**
    -   **Description:** (**Authentication Required.**) Searches for podcast topics via CPOA based on a query. The authenticated user's `user_id` is passed to CPOA and associated with the workflow. `image_url`s in the returned snippets are converted to short-lived signed GCS HTTP URLs. If `client_id` is provided, user preferences from the session are fetched and passed to CPOA. Requires a valid Bearer token in the `Authorization` header.
    -   **Request Payload (JSON):**
        ```json
        {
            "query": "artificial intelligence in healthcare",
            "client_id": "your_session_id" // Optional, for fetching user preferences for CPOA
        }
        ```
    -   **Success Response (200 OK):**
        ```json
        {
            "workflow_id": "uuid-of-the-cpoa-workflow",
            "search_results": [
                {
                    "snippet_id": "...",
                    "title": "...",
                    "image_url": "https://signed-gcs-url-for-image...",
                    "..."
                }
            ]
        }
        ```
    -   **Error Responses:**
        -   **400 Bad Request:** For issues like:
            -   Missing or malformed JSON payload (`API_GW_PAYLOAD_REQUIRED`, `API_GW_MALFORMED_JSON`).
            -   `query` is missing or not a non-empty string (`API_GW_SEARCH_QUERY_INVALID`).
            -   `client_id` (if provided) is not a non-empty string (`API_GW_CLIENT_ID_INVALID`).
        -   **401 Unauthorized:** If authentication token is missing, invalid, or expired.
        -   **503 Service Unavailable:** If CPOA service or its downstream dependencies (TDA, SCA) are unavailable.
        -   **500 Internal Server Error:** For other unexpected errors.

### Session Management Endpoints

-   **`POST /api/v1/session/init`**
    -   **Description:** Initializes or acknowledges a user session using a client-generated ID. Creates a session record if `client_id` is new, or updates `last_seen_timestamp` if existing. Returns current preferences associated with that `client_id`. This endpoint does not require JWT authentication itself.
    -   **Request Payload (JSON):** `{"client_id": "your_frontend_generated_id"}`.
    -   **Success Response (200 OK):** `{"client_id": "...", "preferences": {...}}`.
    -   **Error Responses:** 400 (missing `client_id`), 500 (DB error).

-   **`GET /api/v1/session/preferences`**
    -   **Description:** Retrieves current preferences for a given user session `client_id`. This endpoint does not require JWT authentication itself but relies on the `client_id` to fetch session-specific data.
    -   **Query Parameters:** `client_id` (string, required).
    -   **Success Response (200 OK):** `{"client_id": "...", "preferences": {...}}`.
    -   **Error Responses:** 400 (missing `client_id`), 404 (session not found), 500 (DB error).

-   **`POST /api/v1/session/preferences`**
    -   **Description:** (**Authentication Required.**) Updates (replaces) preferences for a given user session. The `client_id` in the payload identifies the session record. The Bearer token authenticates the user making the request. It's implied the authenticated user should have rights to modify the preferences associated with the given `client_id`. Requires a valid Bearer token.
    -   **Request Payload (JSON):** `{"client_id": "your_session_id", "preferences": {"key": "value", ...}}`.
        -   `client_id` (string, required): Must be a non-empty string. This is the identifier for the session record.
        -   `preferences` (object, required): Must be a valid JSON object (dictionary).
    -   **Success Response (200 OK):** `{"client_id": "...", "message": "Preferences updated successfully."}`.
    -   **Error Responses:**
        -   **400 Bad Request:** For issues like:
            -   Missing or malformed JSON payload (`API_GW_PAYLOAD_REQUIRED`, `API_GW_MALFORMED_JSON`).
            -   `client_id` is missing or not a non-empty string (`API_GW_SESSION_CLIENT_ID_INVALID`).
            -   `preferences` is missing or not an object (`API_GW_SESSION_INVALID_PREFERENCES_PAYLOAD`).
        -   **401 Unauthorized:** If authentication token is missing, invalid, or expired.
        -   **404 Not Found:** If the session for the given `client_id` does not exist.
        -   **500 Internal Server Error:** For database errors.

### Internal Endpoints (Primarily for Service-to-Service Communication)

-   **`GET /api/v1/internal/media_access_url`**
    -   **Description:** Provides a short-lived signed GCS HTTP URL for a given GCS URI. This is intended for internal services (like ASF) to securely access GCS resources without needing direct GCS credentials.
    -   **Query Parameters:**
        -   `gcs_uri` (string, required): The GCS URI of the resource (e.g., `gs://your-bucket/path/to/object.mp3`).
    -   **Success Response (200 OK):**
        ```json
        {
            "signed_url": "https://storage.googleapis.com/your-bucket/..."
        }
        ```
    -   **Error Responses:**
        -   400 Bad Request: If `gcs_uri` is missing or invalid (`MISSING_GCS_URI`, `INVALID_GCS_URI_FORMAT`).
        -   500 Internal Server Error: If signed URL generation fails (`SIGNED_URL_GENERATION_FAILED`, `INTERNAL_SERVER_ERROR`).

## Database Schema Details

The API Gateway, CPOA, and other services may utilize a shared database (PostgreSQL recommended, SQLite for basic local dev). Key tables include:

### `podcasts` Table
-   **Purpose:** Tracks the status and metadata of each podcast generation task. `final_audio_filepath` now stores a GCS URI.
-   **Key Columns (PostgreSQL types):** `podcast_id` (UUID PK), `topic` (TEXT), `cpoa_status` (TEXT), `cpoa_error_message` (TEXT), `final_audio_filepath` (TEXT - GCS URI), `stream_id` (TEXT), `asf_websocket_url` (TEXT), `asf_notification_status` (TEXT), `task_created_timestamp` (TIMESTAMPTZ), `last_updated_timestamp` (TIMESTAMPTZ), `cpoa_full_orchestration_log` (JSONB), `tts_settings_used` (JSONB).

### `topics_snippets` Table
-   **Purpose:** Stores discovered topics and generated snippets. `image_url` now stores a GCS URI.
-   **Key Columns (PostgreSQL types):** `id` (UUID PK), `type` (TEXT), `title` (TEXT), `summary` (TEXT), `keywords` (JSONB), `source_url` (TEXT), `source_name` (TEXT), `original_topic_details` (JSONB), `llm_model_used_for_snippet` (TEXT), `cover_art_prompt` (TEXT), `image_url` (TEXT - GCS URI), `generation_timestamp` (TIMESTAMPTZ), `last_accessed_timestamp` (TIMESTAMPTZ), `relevance_score` (REAL).

### `generated_scripts` Table
-   **Purpose:** Serves as a cache for podcast scripts generated by the Podcast Script Weaver Agent (PSWA), typically managed by CPOA. This helps avoid re-generating scripts for identical topics if a fresh script is already available.
-   **Key Columns:** `script_id` (PK), `topic_hash` (UNIQUE, hash of topic identifiers), `structured_script_json` (JSON of the script), `generation_timestamp`, `llm_model_used`, `last_accessed_timestamp`.

### `user_sessions` Table
-   **Purpose:** Stores user session identifiers (client IDs) and their associated preferences, allowing for personalized experiences across requests.
-   **Key Columns:** `session_id` (PK, corresponds to `client_id`), `created_timestamp`, `last_seen_timestamp`, `preferences_json` (JSON string for user preferences).

### `users` Table
-   **Purpose:** Stores user account information for authentication.
-   **Key Columns:** `user_id` (PK), `username` (UNIQUE), `email` (UNIQUE), `hashed_password`, `created_at`.
