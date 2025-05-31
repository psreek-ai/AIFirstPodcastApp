# Audio Stream Feeder (ASF)

## Purpose

The Audio Stream Feeder (ASF) service is responsible for streaming generated podcast audio to connected clients (e.g., the Aethercast web frontend) in real-time using WebSockets. It allows clients to start receiving audio data as it's being processed or as soon as it's fully available, without needing to download the entire file first.

Key Responsibilities:

1.  **WebSocket Server:** Provides a WebSocket endpoint (`/api/v1/podcasts/stream`) for clients to connect to.
2.  **Stream Management:**
    *   Allows clients to `join_stream` using a unique `stream_id` (provided by VFA via CPOA and API Gateway).
    *   Maintains a mapping from `stream_id` to the actual audio file path on the server. This map is populated via an internal HTTP endpoint.
3.  **Audio Chunk Streaming:**
    *   When a client joins a stream, ASF retrieves the associated audio file.
    *   It reads the audio file in chunks of a configurable size (`ASF_CHUNK_SIZE`).
    *   Each chunk is sent as a binary WebSocket message (`audio_chunk` event) to the client(s) in that stream's room.
    *   A small configurable delay (`ASF_STREAM_SLEEP_INTERVAL`) is introduced between chunks to manage flow.
4.  **Stream Lifecycle & Control:**
    *   Sends `audio_control` messages to clients to indicate the `start_of_stream` and `end_of_stream`.
    *   Handles client connections, disconnections, and potential errors during streaming, emitting `stream_error` messages if issues occur.

## Configuration

ASF is configured via environment variables, typically managed in a `.env` file within the `aethercast/asf/` directory. Create one by copying the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file. Key variables include:

-   `ASF_SECRET_KEY`: Secret key for Flask session management and signing. **Important: Change this in production!** If not set, a temporary UUID will be generated at startup (check logs for the value if needed for external session verification, though not typical for ASF's primary role).
    -   *Example:* `your_very_secret_key_here_for_asf`
-   `ASF_CORS_ALLOWED_ORIGINS`: Specifies which origins are allowed for Cross-Origin Resource Sharing with SocketIO. Use `*` for all origins (less secure, suitable for development) or a comma-separated list of specific origins for production.
    -   *Default:* `*`
-   `ASF_CHUNK_SIZE`: The size of audio data (in bytes) to read from the file and send in each WebSocket message.
    -   *Default:* `4096` (4KB)
-   `ASF_STREAM_SLEEP_INTERVAL`: The small delay (in seconds) between sending audio chunks. This helps in pacing the stream.
    -   *Default:* `0.01` (10 milliseconds)
-   `ASF_HOST`: The host address for the ASF Flask-SocketIO server to bind to.
    -   *Default:* `0.0.0.0` (listens on all available network interfaces)
-   `ASF_PORT`: The port number for the ASF server.
    -   *Default:* `5006`
-   `ASF_DEBUG_MODE`: Enables or disables Flask debug mode (and SocketIO debug features).
    -   *Default:* `True`

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `Flask-SocketIO`, `python-dotenv`, and `eventlet` (as the recommended WebSocket server for Flask-SocketIO in production, though the dev server is used if `eventlet` isn't explicitly run).

## Running the Service

1.  Ensure environment variables are correctly set (e.g., in a `.env` file).
2.  The shared audio directory (used by VFA to save files, and ASF to read them) must be accessible to ASF at the path configured in VFA (e.g., `/srv/aethercast/generated_audio/`).
3.  Run the Flask-SocketIO server:
    ```bash
    python aethercast/asf/main.py
    ```
    This will start the service, typically on `http://0.0.0.0:5006` (WebSocket will be `ws://0.0.0.0:5006`).

For a more production-like setup using `eventlet`:
```bash
# Ensure eventlet is installed (it's in requirements.txt)
# The python script itself is set up to use socketio.run which can use eventlet if available.
# Alternatively, a gunicorn setup with eventlet worker would be:
# gunicorn --worker-class eventlet -w 1 aethercast.asf.main:app --bind 0.0.0.0:5006
```

## WebSocket API

Clients connect to the ASF service via WebSockets.

-   **Namespace:** `/api/v1/podcasts/stream`
    -   The client should connect to `your_asf_host:your_asf_port/api/v1/podcasts/stream`.
    -   Example (JavaScript): `const socket = io('ws://localhost:5006/api/v1/podcasts/stream');` (if `asf_websocket_url` from API GW already includes the namespace) or construct from base URL.

### Client-to-Server Events:

-   **`join_stream`**
    -   **Description:** Sent by the client after connecting, to subscribe to a specific audio stream.
    -   **Payload Example (JSON):**
        ```json
        {
            "stream_id": "strm_abcdef12345"
        }
        ```

### Server-to-Client Events:

-   **`connection_ack`**
    -   **Description:** Sent by the server upon successful WebSocket connection to the namespace.
    -   **Payload Example (JSON):**
        ```json
        {
            "message": "Connected to ASF. Please send join_stream with your stream_id."
        }
        ```
-   **`stream_status`**
    -   **Description:** Confirms the client has successfully joined the stream room.
    -   **Payload Example (JSON):**
        ```json
        {
            "status": "joined",
            "stream_id": "strm_abcdef12345",
            "message": "Successfully joined stream strm_abcdef12345. Preparing to stream audio."
        }
        ```
-   **`audio_control`**
    -   **Description:** Signals the start or end of the audio stream.
    -   **Payload Example (JSON for start_of_stream):**
        ```json
        {
            "event": "start_of_stream",
            "stream_id": "strm_abcdef12345",
            "timestamp": 1678886400.123
        }
        ```
    -   **Payload Example (JSON for end_of_stream):**
        ```json
        {
            "event": "end_of_stream",
            "stream_id": "strm_abcdef12345",
            "timestamp": 1678886430.456
        }
        ```
-   **`audio_chunk`**
    -   **Description:** Transmits a binary chunk of audio data.
    -   **Payload:** Raw binary data (`ArrayBuffer` on the JavaScript client).
-   **`stream_error`**
    -   **Description:** Sent if an error occurs related to the stream (e.g., file not found, error during streaming).
    -   **Payload Example (JSON):**
        ```json
        {
            "message": "Audio file unavailable for this stream."
        }
        ```
-   **`error`** (Generic SocketIO error from ASF)
    -   **Description:** Sent if there's an issue with the request not specific to streaming (e.g., `join_stream` without `stream_id`).
    -   **Payload Example (JSON):**
        ```json
        {
            "message": "stream_id is required for join_stream."
        }
        ```

## Internal HTTP API

ASF also exposes an internal HTTP endpoint for other services to notify it about newly available audio files.

### Notify New Audio

-   **HTTP Method:** `POST`
-   **URL Path:** `/asf/internal/notify_new_audio`
-   **Description:** Used by CPOA (after VFA successfully generates an audio file) to inform ASF about the `stream_id` and the `filepath` where the audio file is stored. ASF uses this information to serve the correct file when a client joins that stream.
-   **Request Payload Example (JSON):**
    ```json
    {
        "stream_id": "strm_abcdef12345",
        "filepath": "/srv/aethercast/generated_audio/aethercast_audio_strm_abcdef12345_uuid.mp3"
    }
    ```
-   **Success Response (200 OK - JSON):**
    ```json
    {
        "message": "Notification received successfully",
        "stream_id": "strm_abcdef12345"
    }
    ```
-   **Error Response (400 Bad Request - JSON):**
    If `stream_id` or `filepath` are missing.
    ```json
    {
        "error": "Missing required parameters: stream_id"
    }
    ```
