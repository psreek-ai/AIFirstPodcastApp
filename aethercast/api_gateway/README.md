# API Gateway

## Purpose

The API Gateway is the primary entry point for external clients (like the frontend UI) to interact with the Aethercast system. It provides a unified interface for discovering content, generating podcasts, managing user sessions, and retrieving podcast information and audio.

Key Responsibilities:

1.  **Request Routing & Validation:** Receives HTTP requests from clients, validates them, and routes them to the appropriate internal services (primarily CPOA) or handles them directly.
2.  **Service Orchestration (Delegation):** For most operations, it delegates to the Central Podcast Orchestrator Agent (CPOA), which handles complex tasks like podcast generation, snippet creation, and search. The CPOA logic runs within the API Gateway's process and utilizes a PostgreSQL database for state management.
3.  **Frontend Serving:** Serves the static files (HTML, CSS, JavaScript) for the Aethercast web frontend.
4.  **Idempotency Key Forwarding:** For client-initiated operations that trigger asynchronous backend tasks (e.g., podcast generation, topic exploration), the API Gateway forwards the `X-Idempotency-Key` and `X-Workflow-ID` headers (if provided by the client) to CPOA, which then propagates them to the relevant backend services (TDA, SCA, PSWA, IGA) to ensure idempotent processing.
5.  **Database Interaction (Direct):** Manages interactions with its configured database (PostgreSQL, which also hosts CPOA's tables and the shared `idempotency_keys` table) for tasks such as:
    *   Managing user session data in the `user_sessions` table.
    *   Managing user accounts in the `users` table for authentication.
    *   Managing email subscriptions in the `subscribers` table.
    *   (Note: CPOA manages the `podcasts` table for task tracking directly within the same database.)
6.  **Response Formatting:** Consolidates responses from CPOA (or its own data) and formats them into user-friendly JSON responses for the client.
7.  **Session Management:** Provides endpoints for initializing user sessions and managing user preferences.
8.  **User Authentication:** Provides endpoints for user registration and login, issuing JWTs for accessing protected routes.

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

-   **PostgreSQL Database Configuration:** The API Gateway (including the CPOA logic it runs) relies on a PostgreSQL database for CPOA state management, user accounts, sessions, subscriptions, and for accessing the shared `idempotency_keys` table used by backend services. These variables are typically defined in `common.env` and sourced by the API Gateway's `.env` file:
    -   `POSTGRES_HOST`: Hostname of the PostgreSQL server.
    -   `POSTGRES_PORT`: Port of the PostgreSQL server.
    -   `POSTGRES_USER`: Username for database connection.
    -   `POSTGRES_PASSWORD`: Password for database connection.
    -   `POSTGRES_DB`: Name of the database.
-   `FLASK_SECRET_KEY`: **Required for JWT signing and session security.** A strong, random secret key.
    -   *Example in `.env`:* `FLASK_SECRET_KEY=your_random_generated_secret_string`
-   **Service URLs:** URLs for backend services that CPOA (running within API Gateway) might call. These should point to the Docker service names when running with Docker Compose.
    -   `TDA_SERVICE_URL`: URL for the Topic Discovery Agent.
    -   `SCA_SERVICE_URL`: URL for the Snippet Craft Agent.
    -   `PSWA_SERVICE_URL`: URL for the Podcast Script Weaver Agent.
    -   `VFA_SERVICE_URL`: URL for the Voice Forge Agent.
    -   `IGA_SERVICE_URL`: URL for the Image Generation Agent.
    -   `AIMS_SERVICE_BASE_URL_CONTAINER`: Base URL for the AIMS service (used by PSWA, SCA).
    -   `AIMS_TTS_SERVICE_BASE_URL_CONTAINER`: Base URL for the AIMS TTS service (used by VFA).
-   `API_GW_HOST`: Host for the API Gateway's Flask development server when run directly.
    -   *Default in `main.py` if run directly:* `0.0.0.0`
-   `API_GW_PORT`: Port for the API Gateway's Flask development server when run directly.
    -   *Default in `main.py` if run directly:* `5001`
-   `API_GW_DEBUG_MODE`: Enables or disables Flask debug mode when run directly (`True` or `False`).
    -   *Default in `main.py` if run directly:* `True`
-   `FEND_DIR`: (Optional) Path to the frontend static files directory. Default is derived relative to `main.py`.
-   `GCS_BUCKET_NAME`: Name of the GCS bucket for media assets. Essential for generating signed URLs. See main project README for setup.
-   `GOOGLE_APPLICATION_CREDENTIALS`: Path to GCP service account key JSON file (e.g., `/app/gcp-credentials.json` in Docker). Required for GCS operations like signed URL generation. See main project README for setup.

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
    -   **Description:** Fetches dynamically generated podcast snippets via CPOA. CPOA orchestrates topic discovery (TDA), snippet text generation (SCA), and image generation (IGA). `image_url`s (GCS URIs) are converted to signed GCS HTTP URLs by API Gateway. If authenticated, `user_id` is passed to CPOA.
    -   **Headers (Optional):** `X-Idempotency-Key` (string) can be provided by clients if they wish to ensure this potentially expensive read-like operation (which might trigger new CPOA workflows if data is stale or not found) is idempotent from their perspective, though the primary benefit of idempotency is for state-changing operations. CPOA may use this key if it decides to initiate a new workflow.
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
    -   **Description:** (**Authentication Required.**) Explores topics related to a given `current_topic_id` or `keywords`. Delegates to CPOA. Authenticated `user_id` is passed to CPOA. `image_url`s are converted to signed GCS URLs.
    -   **Headers (Recommended):** Include `X-Idempotency-Key` (string, UUID recommended) to ensure that if the request is retried (e.g., due to network issues), the underlying asynchronous topic exploration and generation tasks managed by CPOA are processed idempotently by backend services. `X-Workflow-ID` (string, optional) can also be provided for end-to-end tracking.
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
    -   **Description:** (**Authentication Required.**) Initiates a new podcast generation task via CPOA. Authenticated `user_id` is passed to CPOA.
    -   **Headers (Strongly Recommended):** Include `X-Idempotency-Key` (string, UUID recommended) to ensure that the entire podcast generation workflow (which involves multiple asynchronous Celery tasks in backend services like PSWA, VFA, IGA) is processed idempotently. `X-Workflow-ID` (string, optional) can also be provided for end-to-end tracking.
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
    -   **Description:** (**Authentication Required.**) Searches for podcast topics via CPOA based on a query. Authenticated `user_id` is passed to CPOA. `image_url`s are converted to signed GCS URLs.
    -   **Headers (Recommended):** Include `X-Idempotency-Key` (string, UUID recommended) if the search operation might trigger new CPOA workflows that involve asynchronous backend tasks, to ensure those are processed idempotently. `X-Workflow-ID` (string, optional) can also be provided.
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

### Subscription Endpoint

-   **`POST /api/v1/subscribe`**
    -   **Description:** Allows users to subscribe with their email address for updates. This is a public endpoint.
    -   **Request Payload (JSON):**
        ```json
        {
            "email": "user@example.com"
        }
        ```
        - `email` (string, required): Must be a valid email format.
    -   **Success Response (201 Created):**
        ```json
        {
            "message": "Successfully subscribed! Thank you."
        }
        ```
    -   **Error Responses:**
        -   400 Bad Request:
            -   `{"error_code": "SUBSCRIBE_PAYLOAD_REQUIRED", "message": "JSON payload is required."}` (If payload is missing/malformed)
            -   `{"error_code": "SUBSCRIBE_EMAIL_REQUIRED", "message": "Email is required."}`
            -   `{"error_code": "SUBSCRIBE_INVALID_EMAIL_FORMAT", "message": "Invalid email format."}`
        -   409 Conflict: `{"error_code": "SUBSCRIBE_EMAIL_EXISTS", "message": "This email is already subscribed."}`
        -   500 Internal Server Error: `{"error_code": "SUBSCRIBE_DB_ERROR", "message": "Could not process subscription due to a database issue."}` or `{"error_code": "SUBSCRIBE_UNEXPECTED_ERROR", ...}`

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
-   **Purpose:** Serves as a cache for podcast scripts generated by PSWA, managed by CPOA. Stored in the PostgreSQL database if PSWA is configured for it.
-   **Key Columns:** `script_id` (PK), `topic_hash` (UNIQUE), `structured_script_json` (JSONB), `generation_timestamp`, `llm_model_used`, `last_accessed_timestamp`.

### `idempotency_keys` Table
-   **Purpose:** Shared table used by TDA, SCA, PSWA, and IGA services to track the state of idempotent operations. Ensures that tasks initiated with the same `X-Idempotency-Key` are not processed multiple times. Stored in the main PostgreSQL database.
-   **Key Columns (PostgreSQL types):** `idempotency_key` (TEXT PK), `task_name` (TEXT), `workflow_id` (TEXT, nullable), `created_at` (TIMESTAMPTZ), `locked_at` (TIMESTAMPTZ, nullable), `status` (TEXT), `result_payload` (JSONB, nullable), `error_payload` (JSONB, nullable).

### `user_sessions` Table
-   **Purpose:** Stores user session identifiers (`client_id`) and their associated preferences. Stored in the PostgreSQL database.
-   **Key Columns (PostgreSQL types):** `session_id` (TEXT PK), `user_id` (UUID FK, nullable), `created_timestamp` (TIMESTAMPTZ), `last_seen_timestamp` (TIMESTAMPTZ), `preferences_json` (JSONB).

### `users` Table
-   **Purpose:** Stores user account information for authentication. Stored in the PostgreSQL database.
-   **Key Columns (PostgreSQL types):** `user_id` (UUID PK), `username` (TEXT UNIQUE), `email` (TEXT UNIQUE), `hashed_password` (TEXT), `created_at` (TIMESTAMPTZ).

### `subscribers` Table
-   **Purpose:** Stores email addresses of users who have subscribed for updates. Stored in the PostgreSQL database.
-   **Key Columns (PostgreSQL types):** `email` (TEXT PK), `subscribed_at` (TIMESTAMPTZ).
