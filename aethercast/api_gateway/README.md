# API Gateway

## Purpose

The API Gateway is the primary entry point for external clients (like the frontend UI) to interact with the Aethercast system. It provides a unified interface for discovering content, generating podcasts, managing user sessions, and retrieving podcast information and audio.

Key Responsibilities:

1.  **Request Routing & Validation:** Receives HTTP requests from clients, validates them (including request payloads using Pydantic models), and routes them to the appropriate internal services (primarily CPOA) or handles them directly.
2.  **Service Orchestration (Delegation):** For most operations, it delegates to the Central Podcast Orchestrator Agent (CPOA), which handles complex tasks like podcast generation, snippet creation, and search. The CPOA logic runs within the API Gateway's process and utilizes a PostgreSQL database for state management.
3.  **Frontend Serving:** Serves the static files (HTML, CSS, JavaScript) for the Aethercast web frontend.
-   **Idempotency Key Forwarding:** For client-initiated operations that trigger asynchronous backend tasks (e.g., podcast generation, topic exploration), the API Gateway forwards the `X-Idempotency-Key` and `X-Workflow-ID` headers (if provided by the client) to CPOA, which then propagates them to the relevant backend services (TDA, WCHA, SCA, PSWA, IGA) to ensure idempotent processing.
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

-   **PostgreSQL Database Configuration:** The API Gateway (including the CPOA logic it runs) relies on a PostgreSQL database.
    -   It uses `psycopg2.pool.SimpleConnectionPool` for managing connections.
    -   Connection parameters (typically defined in `common.env` and sourced):
        -   `POSTGRES_HOST`: Hostname of the PostgreSQL server.
        -   `POSTGRES_PORT`: Port of the PostgreSQL server.
        -   `POSTGRES_USER`: Username for database connection.
        -   `POSTGRES_PASSWORD`: Password for database connection.
        -   `POSTGRES_DB`: Name of the database.
    -   Pool configuration specific to API Gateway:
        -   `API_GW_DB_POOL_MIN_CONN`: Minimum connections in the pool. Default: `1`.
        -   `API_GW_DB_POOL_MAX_CONN`: Maximum connections in the pool. Default: `5`.
-   `FLASK_SECRET_KEY`: **Required for JWT signing and session security.** A strong, random secret key.
-   **Service URLs:** URLs for backend services that CPOA calls.
    -   `WCHA_SERVICE_URL`, `TDA_SERVICE_URL`, `SCA_SERVICE_URL`, `PSWA_SERVICE_URL`, `VFA_SERVICE_URL`, `IGA_SERVICE_URL`, `AIMS_SERVICE_BASE_URL_CONTAINER`, `AIMS_TTS_SERVICE_BASE_URL_CONTAINER`.
-   `API_GW_HOST`, `API_GW_PORT`, `API_GW_DEBUG_MODE`: For Flask development server.
-   `FEND_DIR`: Path to frontend static files.
-   `GCS_BUCKET_NAME`, `GOOGLE_APPLICATION_CREDENTIALS`: For GCS operations.

*(Note: For production, use a WSGI server like Gunicorn.)*

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `requests`, `python-dotenv`, `PyJWT`, `Werkzeug`, `psycopg2-binary`, `google-cloud-storage`, and `pydantic` (for request validation).

## Running the Service

1.  Ensure dependent services are running.
2.  Set environment variables.
3.  Initialize the database (schema is applied on startup if tables don't exist).
4.  Run the Flask development server:
    ```bash
    python aethercast/api_gateway/main.py
    ```

## API Endpoints

Request payloads for relevant POST/PUT endpoints are validated using Pydantic models. If validation fails, a **422 Unprocessable Entity** response is returned. Malformed JSON (unparsable) will result in a **400 Bad Request**.

**Example 422 Validation Error Response:**
```json
{
    "error_code": "API_GW_VALIDATION_ERROR",
    "message": "Input validation failed",
    "details": [
        {
            "type": "missing",
            "loc": ["body", "fieldName"],
            "msg": "Field required",
            "input": {}
        },
        {
            "type": "string_too_short",
            "loc": ["body", "anotherField"],
            "msg": "String should have at least 3 characters",
            "input": "hi",
            "ctx": {"min_length": 3}
        }
    ]
}
```

### Frontend, Health Check
(These sections remain largely the same as before.)

### Authentication Endpoints

-   **`POST /auth/register`**
    -   **Description:** Registers a new user. Payloads validated by Pydantic.
    -   **Request Payload (JSON):** (username min 3 chars, valid email, password min 8 chars)
        ```json
        {
            "username": "newuser",
            "email": "user@example.com",
            "password": "securepassword123"
        }
        ```
    -   **Success Response (201 Created):** `{"message": "User registered successfully.", "user_id": "..."}`.
    -   **Error Responses:**
        -   422 Unprocessable Entity: If payload validation fails (e.g., missing fields, weak password, invalid email format). See example 422 response structure above.
        -   409 Conflict: If username or email already exists (`API_GW_USER_EXISTS`).
        -   500 Internal Server Error: For database errors (`API_GW_REGISTER_DB_ERROR`).

-   **`POST /auth/login`**
    -   **Description:** Logs in an existing user. Payloads validated by Pydantic.
    -   **Request Payload (JSON):**
        ```json
        {
            "identifier": "user@example.com", // Username or email
            "password": "securepassword123"
        }
        ```
    -   **Success Response (200 OK):** `{"access_token": "your_jwt_here", ...}`.
    -   **Error Responses:**
        -   422 Unprocessable Entity: If payload validation fails.
        -   401 Unauthorized: If credentials are invalid (`API_GW_LOGIN_INVALID_CREDS`).
        -   500 Internal Server Error.

### Snippets & Categories
(Largely the same, but add Pydantic note if applicable to any future POST/PUT here)

-   **`POST /api/v1/topics/explore`**
    -   **Description:** (**Authentication Required.**) Explores topics. Payloads validated by Pydantic.
        *Note: At least `current_topic_id` or non-empty `keywords` (list of strings) must be provided. This is enforced and will result in a 422 if not met.*
    -   **Request Payload (JSON):** Validated by `TopicExplorePayload` model.
        ```json
        {
            "current_topic_id": "topic_abc123", // Optional
            "keywords": ["space travel", "mars colonization"], // Optional
            "depth_mode": "broader", // Optional, default "deeper"
            "client_id": "your_session_id" // Optional
        }
        ```
    -   **Success Response (200 OK):** (As before)
    -   **Error Responses:**
        -   422 Unprocessable Entity: For payload validation failures.
        -   400 Bad Request: For malformed JSON.
        -   401 Unauthorized.
        -   503 Service Unavailable.
        -   500 Internal Server Error.

### Podcast Task Management

-   **`POST /api/v1/podcasts`**
    -   **Description:** (**Authentication Required.**) Initiates podcast generation. Payloads validated by Pydantic.
    -   **Request Payload (JSON):** Validated by `CreatePodcastPayload` model. (`topic` min 1 char).
        ```json
        {
            "topic": "The History of Podcasting",
            "voice_params": { /* Optional */ },
            "client_id": "your_session_id", // Optional
            "test_scenarios": { /* Optional */ }
        }
        ```
    -   **Success Response (201 Created or 200 OK):** (As before)
    -   **Error Responses:**
        -   422 Unprocessable Entity: For payload validation failures.
        -   400 Bad Request: For malformed JSON.
        -   401 Unauthorized.
        -   503 Service Unavailable.
        -   500 Internal Server Error.

(GET endpoints for podcasts remain the same as they don't process request bodies that need Pydantic validation)

### Search

-   **`POST /api/v1/search/podcasts`**
    -   **Description:** (**Authentication Required.**) Searches topics. Payloads validated by Pydantic.
    -   **Request Payload (JSON):** Validated by `SearchPodcastsPayload` model. (`query` min 1 char).
        ```json
        {
            "query": "artificial intelligence in healthcare",
            "client_id": "your_session_id" // Optional
        }
        ```
    -   **Success Response (200 OK):** (As before)
    -   **Error Responses:**
        -   422 Unprocessable Entity: For payload validation failures.
        -   400 Bad Request: For malformed JSON.
        -   401 Unauthorized.
        -   503 Service Unavailable.
        -   500 Internal Server Error.

### Session Management Endpoints

-   **`POST /api/v1/session/init`**
    -   **Description:** Initializes/acknowledges session. Payloads validated by Pydantic.
    -   **Request Payload (JSON):** Validated by `SessionInitPayload` model.
        ```json
        {
            "client_id": "your_frontend_generated_id", // Optional
            "initial_preferences": {} // Optional
        }
        ```
    -   **Success Response (200 OK):** (As before)
    -   **Error Responses:**
        -   422 Unprocessable Entity: For payload validation failures.
        -   400 Bad Request: For malformed JSON.
        -   500 Internal Server Error.

-   **`PUT /api/v1/session/preferences`** (Note: Method changed from POST to PUT in previous subtask, though not explicitly stated in this one, assuming PUT is correct for updates)
    -   **Description:** (**Authentication Required.**) Updates preferences. Payloads validated by Pydantic.
    -   **Request Payload (JSON):** Validated by `SessionPreferencesUpdatePayload` model.
        ```json
        {
            "client_id": "your_session_id",
            "preferences": {"key": "value"}
        }
        ```
    -   **Success Response (200 OK):** (As before)
    -   **Error Responses:**
        -   422 Unprocessable Entity: For payload validation failures.
        -   400 Bad Request: For malformed JSON.
        -   401 Unauthorized.
        -   403 Forbidden: If token `session_id` doesn't match payload `client_id`.
        -   404 Not Found (session not found).
        -   500 Internal Server Error.

### Subscription Endpoint

-   **`POST /api/v1/subscribe`**
    -   **Description:** Subscribes email. Payloads validated by Pydantic.
    -   **Request Payload (JSON):** Validated by `SubscribePayload` model. (`email` must be valid format).
        ```json
        {
            "email": "user@example.com"
        }
        ```
    -   **Success Response (201 Created):** (As before)
    -   **Error Responses:**
        -   422 Unprocessable Entity: For payload validation failures (e.g., invalid email format).
        -   400 Bad Request: For malformed JSON.
        -   409 Conflict (`SUBSCRIBE_EMAIL_EXISTS`).
        -   500 Internal Server Error.

### Internal Endpoints
(Remains the same as it's GET and doesn't use Pydantic for request body)

## Database Schema Details
(This section's general descriptions are fine. The key change is how API GW *connects* to the DB, not the schema itself for these tables from API GW's direct usage perspective. The `idempotency_keys` table description is accurate for its shared nature.)

The API Gateway, CPOA, and other services may utilize a shared database (PostgreSQL recommended, SQLite for basic local dev). Key tables include:

### `podcasts` Table
-   **Purpose:** Tracks the status and metadata of each podcast generation task. `final_audio_filepath` now stores a GCS URI.

### `topics_snippets` Table
-   **Purpose:** Stores discovered topics and generated snippets. `image_url` now stores a GCS URI.

### `generated_scripts` Table
-   **Purpose:** Serves as a cache for podcast scripts generated by PSWA, managed by CPOA.

### `idempotency_keys` Table
-   **Purpose:** Shared table used by TDA, WCHA, SCA, PSWA, IGA, and CPOA orchestration tasks to track the state of idempotent operations.

### `user_sessions` Table
-   **Purpose:** Stores user session identifiers (`client_id`) and their associated preferences.

### `users` Table
-   **Purpose:** Stores user account information for authentication.

### `subscribers` Table
-   **Purpose:** Stores email addresses of users who have subscribed for updates.
