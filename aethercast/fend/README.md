# Aethercast Frontend (FEND)

## Purpose

The Aethercast Frontend provides a web-based user interface for interacting with the Aethercast system. It allows users to:
-   View suggested podcast snippets.
-   Initiate the generation of full podcast episodes based on chosen topics or snippets.
-   Receive status updates on podcast generation.
-   Play back generated podcast audio, either through direct file links or real-time streaming via WebSockets and MediaSource Extensions (MSE).

It is a single-page application (SPA) built with HTML, CSS, and vanilla JavaScript.

## Key Functionalities (`app.js`)

The core client-side logic resides in `aethercast/fend/app.js`.

1.  **Snippet Handling:**
    *   **Fetch Snippets:** On page load and via a "Refresh Snippets" button, it makes a `GET` request to the API Gateway's `/api/v1/snippets` endpoint.
    *   **Display Snippets:** Dynamically renders received snippets (title, summary) in the `#snippet-list-container`.
    *   **Trigger Podcast from Snippet:** Each snippet card has a "Generate Podcast from this Snippet" button. Clicking this button takes the snippet's title (or a relevant topic identifier) and uses it to initiate a full podcast generation task.

2.  **Podcast Generation (from Topic Input or Snippet):**
    *   **User Input:** Allows users to enter a topic directly into an input field (`#topic-input`).
    *   **API Call:** When generation is triggered (either from direct input or a snippet button), it makes a `POST` request to the API Gateway's `/api/v1/podcasts` endpoint with the topic: `{"topic": "your_topic_string"}`.
    *   **Status Display:** Updates the UI (`#status-messages` or a dedicated status area) with messages like "Generating podcast for '{topic}'... Please wait."

3.  **Podcast Playback & Streaming:**
    *   **Response Handling:** When the `/api/v1/podcasts` POST request completes, the frontend inspects the response from the API Gateway.
    *   **Direct Playback (Fallback):** If the API response includes a direct `audio_url` (and no WebSocket streaming information), it sets the `src` of the main `<audio id="audio-player">` element to this URL for playback.
    *   **WebSocket/MSE Streaming (Primary):**
        *   If the API response includes an `asf_websocket_url` (base URL for ASF) and a `stream_id` (from `final_audio_details`), the frontend initiates a WebSocket connection to the AudioStreamFeeder (ASF).
        *   A new `<audio id="audio-player-mse">` element is used for MSE-based playback.
        *   **Connection:** Connects to the ASF namespace (e.g., `ws://asf_host:port/api/v1/podcasts/stream`).
        *   **Joining Stream:** Sends a `join_stream` event with the `stream_id` to ASF.
        *   **MediaSource Extensions (MSE):**
            *   A `MediaSource` object is created and attached to `audio-player-mse`.
            *   On the `sourceopen` event, a `SourceBuffer` is added (typically for `'audio/mpeg'` if MP3s are streamed).
        *   **Receiving Audio:** Listens for `audio_chunk` events from ASF. Received binary `ArrayBuffer` data is queued.
        *   **Appending Chunks:** An `audioQueue` and an `appendNextChunk()` mechanism manage appending audio data from the queue to the `sourceBuffer`, respecting `sourceBuffer.updating` status.
        *   **Stream Control:** Listens for `audio_control` messages from ASF (`start_of_stream`, `end_of_stream`). When `end_of_stream` is received and all chunks are appended, `mediaSource.endOfStream()` is called.
        *   **Error Handling:** Listens for WebSocket `error`, `connect_error`, `disconnect`, and ASF `stream_error` events to update UI and clean up.
        *   **Status Updates:** A dedicated `<div id="streaming-status">` displays messages related to the streaming process (e.g., "Connecting...", "Buffering...", "Stream ended.").
        *   `cleanupMSE()` function ensures resources are reset when a stream ends or errors out.

## HTML Structure (`index.html`)

The `app.js` script relies on specific element IDs being present in `index.html`:

-   `#topic-input`: Text input for users to enter a podcast topic.
-   `#generate-btn`: Button to trigger podcast generation from the text input.
-   `#status-messages`: General area for displaying status messages from various operations.
-   `#podcast-display`: Container for the main audio player and generation details (for direct playback or when streaming finishes).
-   `#podcast-topic-title`: To display the topic of the currently playing/generated podcast.
-   `#audio-player`: The standard HTML5 audio element, primarily used for direct `audio_url` playback.
-   `#generation-details-log`: A `<pre>` tag to show raw JSON details from API responses.
-   `#podcast-snippets-section`: Main container for displaying snippets.
-   `#snippet-list-container`: Where individual snippet cards are dynamically added.
-   `#snippet-status-message`: For messages related to loading snippets (e.g., "Loading...", "No snippets...").
-   `#refresh-snippets-btn`: Button to manually refresh the list of snippets.
-   **New/Assumed for Streaming:**
    -   `<audio id="audio-player-mse" controls></audio>`: A dedicated audio element for playback via MediaSource Extensions.
    -   `<div id="streaming-status"></div>`: A div to show real-time status messages related to audio streaming (e.g., "Connecting to stream...", "Buffering...", "Stream ended.").

## Dependencies

-   **Socket.IO Client Library:** The frontend relies on the Socket.IO client JavaScript library being available. This is typically served automatically by Flask-SocketIO (ASF) at `/socket.io/socket.io.js` when a client connects from a page served by a Flask app integrated with Flask-SocketIO. If ASF is on a different domain or the library is not served automatically, it needs to be included manually in `index.html`:
    ```html
    <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
    ```
    (Using a CDN or a self-hosted version). The current ASF setup implies it serves the client library.
-   No other external JavaScript frameworks are used; it's plain vanilla JavaScript.

## How to Run

The frontend is a set of static files (HTML, CSS, JS) and is served by the API Gateway.
1.  Ensure the API Gateway service is running.
2.  Navigate your browser to the root URL of the API Gateway (e.g., `http://localhost:5001/`). The `index.html` page should load.
The `app.js` script will then automatically attempt to fetch snippets and set up event listeners.
