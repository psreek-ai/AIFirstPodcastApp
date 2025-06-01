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
    *   **Status Display:** Instead of a single 'Generating...' message, the UI now displays a sequence of simulated progress updates in a dedicated area (`#generation-progress-display`). These messages (e.g., 'Discovering content...', 'Crafting script...', 'Synthesizing audio...') provide a perception of progress while waiting for the actual API response. The final status from the API call then replaces these simulated messages, shown in `#status-messages`.

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
        *   **Buffering Indicator:** Displays buffering status messages (e.g., 'Buffering audio...', 'Buffer ready...') in `#streaming-status` based on the audio queue and buffer state.
        *   **Error Handling:** Listens for WebSocket `error`, `connect_error`, `disconnect`, ASF `stream_error`, `MediaSource` errors, `SourceBuffer` errors, and `audio` element errors. On critical errors, it displays user-friendly messages in `#streaming-status`, attempts to clean up MSE resources, and may show a 'Retry Stream' button.
        *   **Retry Mechanism:** A 'Retry Stream' button (`#retry-stream-btn`) is shown if a WebSocket connection fails or a critical MSE error occurs, allowing the user to attempt reconnecting and restarting the stream.
        *   **Status Updates:** A dedicated `<div id="streaming-status">` displays messages related to the streaming process (e.g., "Connecting...", "Buffering...", "Stream ended.").
        *   `cleanupMSE()` function ensures resources are reset when a stream ends or errors out.

4.  **Topic Exploration:**
    *   **Triggering Exploration:** Users can explore topics in two ways:
        *   Clicking an "Explore Related" button (`.explore-related-btn`) dynamically added to each snippet card. This uses the snippet's associated topic ID (`data-topic-id`).
        *   Entering keywords into a dedicated input field (`#explore-keywords-input`) and clicking an "Explore Keywords" button (`#explore-keywords-btn`).
    *   **API Call:** Both methods trigger `triggerTopicExploration(payload)`, which makes a `POST` request to the API Gateway's `/api/v1/topics/explore` endpoint. The payload contains either `current_topic_id` or `keywords`, and a `depth` mode (currently "deeper").
    *   **Displaying Results:** New snippets returned by the API are rendered in the `#explored-topics-container` using a reusable `renderSnippetCard` function. Each new explored snippet card also includes "Generate Podcast" and "Explore Related" buttons, allowing for further interaction.
    *   **Status Updates:** Loading states and errors during the exploration process are displayed in `#explored-topics-status`.

5.  **Advanced Error Diagnostics:**
    *   **Triggering Diagnostics:** After a podcast generation attempt (whether successful or failed), if a `podcast_id` is available from the API response, a "View Diagnostics" button (`.view-diagnostics-btn`) is dynamically added to the status display area.
    *   **Modal Display:** Clicking this button opens a modal window (`#diagnostics-modal`) overlaying the page.
    *   **Fetching Details:** The modal fetches detailed information for the specific podcast task by making a `GET` request to the API Gateway's `/api/v1/podcasts/<podcast_id>` endpoint.
    *   **Information Displayed:** The modal presents:
        *   Key information: Podcast ID, Topic, Overall Status, and Final Error Message (if any).
        *   A formatted, scrollable view of CPOA's `orchestration_log`. Each log entry shows its timestamp, stage, message, and any `data_preview` or `structured_data` associated with it (formatted as JSON). This allows for detailed inspection of the generation process.
    *   **Interactivity:** The modal can be closed by clicking an "X" button (`#diagnostics-modal-close-btn`) or by clicking outside the main modal content area.

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
    -   `<div id="generation-progress-display">`: A div to show the sequence of simulated progress messages during podcast generation.
    -   `<audio id="audio-player-mse" controls></audio>`: A dedicated audio element for playback via MediaSource Extensions.
    -   `<div id="streaming-status"></div>`: A div to show real-time status messages related to audio streaming.
    -   `<button id="retry-stream-btn">`: Button (initially hidden) that allows users to retry audio streaming if it fails.
    -   `#explore-keywords-input`: Text input for users to enter keywords for topic exploration.
    -   `#explore-keywords-btn`: Button to trigger exploration based on keywords.
    -   `#explored-topics-container`: Div where snippets resulting from topic exploration are displayed.
    -   `#explored-topics-status`: Div for displaying status messages related to the topic exploration process.
    -   `.explore-related-btn`: Class for "Explore Related" buttons dynamically added to snippet cards.
    -   `#diagnostics-modal`: The main container for the diagnostics modal (initially hidden).
    -   `#diagnostics-modal-close-btn`: The close button ('&times;') for the modal.
    -   `#diag-podcast-id`, `#diag-topic`, `#diag-overall-status`, `#diag-final-error`: Spans within the modal to display basic info of the diagnosed podcast.
    -   `#diag-orchestration-log-container`: A div within the modal to display the formatted orchestration log entries.
    -   `.view-diagnostics-btn`: Class for "View Diagnostics" buttons dynamically added after a podcast generation attempt.
    *   `#preferences-section`: Container for the user preferences UI.
    *   `#pref-news-category`: Input field for the "Preferred News Category" preference.
    *   `#save-prefs-btn`: Button to save the preferences.
    *   `#prefs-status`: Paragraph element to display status messages related to saving/loading preferences.

## User Preferences

The frontend supports basic user preferences that are saved on the backend per user session.

1.  **Client/Session ID:**
    *   On first load, `app.js` generates a unique `currentUiClientId`.
    *   This ID is sent to the API Gateway (`POST /api/v1/session/init`) to initialize or retrieve an existing session.
2.  **Loading Preferences:**
    *   After session initialization, any existing preferences for the `currentUiClientId` are fetched from the server and stored locally in `currentUserPreferences`.
    *   The `populatePreferencesForm()` function then updates the UI fields (e.g., `#pref-news-category`) with these loaded values.
3.  **Displaying & Modifying Preferences:**
    *   A "My Preferences" section in `index.html` allows users to view and modify their settings.
    *   Currently, a "Preferred News Category" can be set. This is intended to (eventually) influence topic suggestions from TDA, though the deep integration is pending.
4.  **Saving Preferences:**
    *   Clicking the "Save Preferences" button (`#save-prefs-btn`) triggers a `POST` request to `/api/v1/session/preferences`.
    *   The payload includes the `currentUiClientId` and the current set of preferences from the UI form.
    *   Success or error messages are displayed in `#prefs-status`. The local `currentUserPreferences` variable is updated on successful save.
5.  **Using Preferences in Podcast Generation:**
    *   When a podcast generation is triggered (`POST /api/v1/podcasts`), the frontend automatically includes the `currentUiClientId`.
    *   The API Gateway uses this `client_id` to fetch the user's saved preferences from the `user_sessions` table and passes them to CPOA.
    *   CPOA can then use these preferences (e.g., a preferred VFA voice name if no explicit voice parameters are given in the podcast request).

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
