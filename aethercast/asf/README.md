# Audio Stream Feeder (ASF)

## Purpose

The Audio Stream Feeder (ASF) service is responsible for two main functions:
1.  Streaming generated podcast audio to connected clients (e.g., the Aethercast web frontend) in real-time using WebSockets.
2.  Relaying real-time UI updates from backend services (like CPOA) to specific clients, also via WebSockets.

This allows clients to start receiving audio data as it's being processed or as soon as it's fully available, and to receive timely updates about ongoing processes or new information relevant to their session.

Key Responsibilities:

1.  **WebSocket Server:** Provides WebSocket endpoints for audio streaming (`/api/v1/podcasts/stream`) and UI updates (`/ui_updates`).
2.  **Audio Stream Management:**
    *   Allows clients to `join_stream` using a unique `stream_id`.
    *   Maintains a mapping from `stream_id` to the actual audio file path.
3.  **Audio Chunk Streaming:**
    *   Reads audio files in chunks and sends them as binary WebSocket messages (`audio_chunk`).
    *   Sends `audio_control` messages for `start_of_stream` and `end_of_stream`.
4.  **UI Update Relaying:**
    *   Allows clients to `subscribe_to_ui_updates` for a specific `client_id`.
    *   Receives UI update messages from other services (like CPOA) via an internal HTTP endpoint.
    *   Emits these updates to the relevant client(s) over the UI updates WebSocket namespace.
5.  **Stream & Update Lifecycle & Control:**
    *   Handles client connections, disconnections, subscriptions, and potential errors, emitting status or error messages (e.g., `stream_error`, `ui_error`).

## Configuration

ASF is configured via environment variables, typically managed in a `.env` file within the `aethercast/asf/` directory.

Key variables include:

-   `ASF_SECRET_KEY`: Secret key for Flask session management. **Important: Change this in production!**
-   `ASF_CORS_ALLOWED_ORIGINS`: Origins allowed for CORS with SocketIO (e.g., `*` for dev, specific origins for prod).
    -   *Default:* `*`
-   `ASF_CHUNK_SIZE`: Size of audio data (bytes) for each WebSocket audio message.
    -   *Default:* `4096`
-   `ASF_STREAM_SLEEP_INTERVAL`: Delay (seconds) between sending audio chunks.
    -   *Default:* `0.01`
-   `ASF_HOST`: Host address for the ASF Flask-SocketIO server.
    -   *Default:* `0.0.0.0`
-   `ASF_PORT`: Port number for the ASF server.
    -   *Default:* `5006`
-   `ASF_DEBUG_MODE`: Enables/disables Flask debug mode.
    -   *Default:* `True`
-   `ASF_UI_UPDATES_NAMESPACE`: The Socket.IO namespace dedicated to UI status updates and real-time event relay.
    -   *Default:* `/ui_updates`

## Dependencies

Project dependencies are listed in `requirements.txt` (includes `Flask`, `Flask-SocketIO`, `python-dotenv`, `eventlet`). Install with `pip install -r requirements.txt`.

## Running the Service

1.  Set environment variables.
2.  Ensure shared audio directory is accessible.
3.  Run: `python aethercast/asf/main.py`.
    Service typically starts on `http://0.0.0.0:5006`.

## WebSocket API

Clients connect to ASF via WebSockets for audio streaming and UI updates using different namespaces.

### Audio Streaming Namespace

-   **Namespace:** `/api/v1/podcasts/stream`
    -   Connect to: `your_asf_host:your_asf_port/api/v1/podcasts/stream`.

#### Client-to-Server Events (Audio Streaming):

-   **`join_stream`**
    -   **Description:** Client subscribes to a specific audio stream.
    -   **Payload Example (JSON):** `{"stream_id": "strm_abcdef12345"}`

#### Server-to-Client Events (Audio Streaming):

-   **`connection_ack`**: Confirms connection to audio namespace. Payload: `{"message": "Connected to ASF. Please send join_stream..."}`.
-   **`stream_status`**: Confirms joining a stream room. Payload: `{"status": "joined", "stream_id": "...", "message": "..."}`.
-   **`audio_control`**: Signals stream start/end. Payload: `{"event": "start_of_stream" | "end_of_stream", "stream_id": "...", "timestamp": ...}`.
-   **`audio_chunk`**: Transmits a binary chunk of audio data (raw binary).
-   **`stream_error`**: Error related to a specific stream (e.g., file not found). Payload: `{"message": "..."}`.
-   **`error`**: Generic error for issues like missing `stream_id`. Payload: `{"message": "..."}`.

### UI Updates Namespace

-   **Namespace:** `/ui_updates` (default, configurable via `ASF_UI_UPDATES_NAMESPACE`)
    -   Connect to: `your_asf_host:your_asf_port/ui_updates`.

#### Client-to-Server Events (UI Updates):

-   **`subscribe_to_ui_updates`**
    -   **Description:** Client subscribes to receive UI updates for a specific session/client ID. This allows ASF to route messages sent by CPOA (via the internal HTTP API) to this particular client.
    -   **Payload Example (JSON):**
        ```json
        {
            "client_id": "your_client_session_id"
        }
        ```

#### Server-to-Client Events (UI Updates):

-   **`ui_connection_ack`**
    -   **Description:** Sent by the server upon successful WebSocket connection to the UI updates namespace.
    -   **Payload Example (JSON):**
        ```json
        {
            "message": "Connected to ASF UI updates on namespace /ui_updates."
        }
        ```
-   **`subscribed_ui_updates`**
    -   **Description:** Confirms that the client has successfully subscribed to UI updates for the given `client_id` by joining the corresponding room.
    -   **Payload Example (JSON):**
        ```json
        {
            "status": "success",
            "client_id": "your_client_session_id",
            "subscribed_to_room": "your_client_session_id"
        }
        ```
-   **`ui_error`**
    -   **Description:** Sent if there's an issue with a UI update request from the client (e.g., `subscribe_to_ui_updates` without a `client_id`).
    -   **Payload Example (JSON):**
        ```json
        {
            "message": "client_id is required for UI update subscription."
        }
        ```
-   **Dynamic Events (Relayed from CPOA):**
    -   **Description:** This namespace is also used by ASF to relay dynamic events to the client. These events are sent from CPOA (or other backend services) to ASF via the `/asf/internal/send_ui_update` HTTP endpoint. The `event_name` and `data` payload are defined by CPOA.
    -   **Example Event Name (from CPOA):** `generation_progress`
    -   **Example Payload (from CPOA for `generation_progress` event):**
        ```json
        {
            // Custom data structure defined by CPOA for this event
            "percentage": 50,
            "current_step": "script_generation",
            "podcast_id": "task_uuid_123"
        }
        ```
    -   Another example event from CPOA could be `task_completed` with details of the completed task.

## Internal HTTP API

ASF exposes internal HTTP endpoints for other services.

### Notify New Audio

-   **HTTP Method:** `POST`
-   **URL Path:** `/asf/internal/notify_new_audio`
-   **Description:** Used by CPOA (after VFA generates audio) to inform ASF about the `stream_id` and `filepath` of the new audio file.
-   **Request Payload Example (JSON):** `{"stream_id": "strm_abcdef12345", "filepath": "/path/to/audio.mp3"}`
    -   `stream_id` (string, required): Must be a non-empty string.
    -   `filepath` (string, required): Must be a non-empty string.
-   **Success Response (200 OK - JSON):** `{"message": "Notification received successfully", "stream_id": "..."}`.
-   **Error Responses (400 Bad Request - JSON):**
    -   `{"error_code": "ASF_NOTIFY_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": "..."}`
    -   `{"error_code": "ASF_NOTIFY_INVALID_PAYLOAD", "message": "Request payload is missing or not valid JSON.", "details": "..."}`
    -   `{"error_code": "ASF_NOTIFY_INVALID_STREAM_ID", "message": "Validation failed: 'stream_id' must be a non-empty string."}`
    -   `{"error_code": "ASF_NOTIFY_INVALID_FILEPATH", "message": "Validation failed: 'filepath' must be a non-empty string."}`

### Send UI Update

-   **HTTP Method:** `POST`
-   **URL Path:** `/asf/internal/send_ui_update`
-   **Description:** Used by backend services like CPOA to send real-time UI updates to specific clients (identified by `client_id`) connected to the UI updates WebSocket namespace. ASF relays this message to the target client via WebSockets.
-   **Request Payload Example (JSON):**
    ```json
    {
        "client_id": "your_client_session_id",
        "event_name": "generation_progress", // Dynamically defined by the sending service (e.g., CPOA)
        "data": { // Custom data payload for the event
            "percentage": 50,
            "current_step": "script_generation",
            "message": "Script generation is 50% complete."
        }
    }
    ```
-   **Success Response (200 OK - JSON):**
    ```json
    {
        "status": "success",
        "message": "UI update sent to client."
    }
    ```
-   **Error Responses (JSON):**
    -   **400 Bad Request:** If payload is malformed, or `client_id`, `event_name`, or the `data` field are missing/invalid.
        -   `{"error_code": "ASF_SENDUI_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": "..."}`
        -   `{"error_code": "ASF_SENDUI_INVALID_PAYLOAD", "message": "Request payload is missing or not valid JSON for sending UI update.", "details": "..."}`
        -   `{"error_code": "ASF_SENDUI_INVALID_CLIENT_ID", "message": "Validation failed: 'client_id' must be a non-empty string."}`
        -   `{"error_code": "ASF_SENDUI_INVALID_EVENT_NAME", "message": "Validation failed: 'event_name' must be a non-empty string."}`
        -   `{"error_code": "ASF_SENDUI_MISSING_DATA", "message": "Validation failed: 'data' field is required."}`
    -   **500 Internal Server Error:** If ASF server configuration is missing (e.g., UI namespace not loaded) or if the SocketIO emit fails internally.
        -   Example: `{"error_code": "ASF_CONFIG_ERROR_UI_NAMESPACE", "message": "...", "details": "..."}`
        -   Example: `{"error_code": "ASF_SOCKETIO_EMIT_FAILED", "message": "...", "details": "..."}`
