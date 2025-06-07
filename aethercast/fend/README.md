# Aethercast Frontend (FEND)

## Purpose

The Aethercast Frontend provides a web-based user interface for interacting with the Aethercast system. It allows users to:
-   View suggested podcast snippets and search for content.
-   Explore popular podcast categories.
-   Initiate the generation of full podcast episodes based on chosen topics or snippets.
-   Receive status updates on podcast generation, including real-time progress via WebSockets.
-   Play back generated podcast audio, either through direct file links or real-time streaming via WebSockets and MediaSource Extensions (MSE).
-   Manage basic user preferences.

It is a single-page application (SPA) built with HTML, CSS, and vanilla JavaScript.

## Key Functionalities (`app.js`)

The core client-side logic resides in `aethercast/fend/app.js`.

1.  **Snippet Handling:**
    *   **Fetch Snippets:** On page load (and potentially via a refresh mechanism), it makes a `GET` request to the API Gateway's `/api/v1/snippets` endpoint.
    *   **Display Snippets:** Dynamically renders received snippets (title, summary, cover image) as cards in the `#snippet-list-container`.
    *   **Trigger Podcast from Snippet:** Each snippet card has a "Listen Now" button that uses the snippet's title to initiate a full podcast generation task.

2.  **Popular Categories:**
    *   **Fetch Categories:** `app.js` fetches a list of popular categories from the API Gateway's `/api/v1/categories` endpoint (e.g., on page load or when a specific section becomes visible).
    *   **Display Categories:** It dynamically renders these categories, typically as clickable links or buttons, into the `.category-list-container` within the `#popular-categories-section` of `index.html`. This allows users to browse or filter content by category.

3.  **Search Functionality:**
    *   **User Input:** `app.js` captures search queries from the input field `#episodes-search-input` (located within the "Latest Episodes" section). The `#header-search-input` in the main site header is also available for potential future wiring.
    *   **API Call:** When a search is triggered, it makes a `POST` request to the API Gateway's `/api/v1/search/podcasts` endpoint with the search query (and `client_id` if available).
    *   **Display Results:** The search results, which are formatted as snippets, are rendered into the main `#snippet-list-container`, replacing any existing snippets. Status messages related to the search (e.g., "Searching...", "No results found...") are displayed in `#snippet-status-message`.

4.  **Podcast Generation (from Topic Input or Snippet):**
    *   **User Input:** Allows users to enter a topic directly into an input field (`#topic-input` - though this specific ID might be deprecated if search/snippet interaction is primary).
    *   **API Call:** When generation is triggered, it makes a `POST` request to `/api/v1/podcasts` with the topic and `client_id`.
    *   **Status Display:** The UI displays progress updates in `#generation-progress-display` (relayed via WebSocket from CPOA through ASF) and final status messages in `#status-messages`.

5.  **Podcast Playback & Streaming:**
    *   **Response Handling:** Handles responses from `/api/v1/podcasts`.
    *   **Direct Playback:** If a direct `audio_url` is provided, uses the standard `<audio id="audio-player">`.
    *   **WebSocket/MSE Streaming:** If `asf_websocket_url` and `stream_id` are provided, it connects to ASF via WebSockets using the `/api/v1/podcasts/stream` namespace.
        *   Uses `<audio id="audio-player-mse">` with `MediaSource Extensions`.
        *   Manages `audio_chunk` events, appends them to a `SourceBuffer`, and handles stream control signals (`start_of_stream`, `end_of_stream`).
        *   Displays buffering and streaming status in `#streaming-status`.
        *   Includes a retry mechanism (`#retry-stream-btn`) for failed streams.

6.  **UI Updates (via WebSockets):**
    *   After session initialization, connects to ASF's UI updates namespace (e.g., `/ui_updates`).
    *   Subscribes to updates using the `client_id` by sending a `subscribe_to_ui_updates` event.
    *   Listens for dynamic events (e.g., `generation_status`, `task_error`) relayed by ASF from CPOA, and updates the `#generation-progress-display` or other relevant UI elements accordingly.

7.  **Topic Exploration:** (Description largely accurate, assuming this functionality is maintained)
    *   Triggered by "Explore Related" buttons on snippet cards or keyword input (`#explore-keywords-input`).
    *   Calls `POST /api/v1/topics/explore`.
    *   Renders new explored snippets in `#explored-topics-container`. Status in `#explored-topics-status`.

8.  **Advanced Error Diagnostics:** (Description largely accurate)
    *   "View Diagnostics" button appears after podcast generation attempts.
    *   Opens a modal (`#diagnostics-modal`) to display detailed task information fetched from `/api/v1/podcasts/<podcast_id>`, including the CPOA orchestration log.

9.  **User Preferences:** (Description largely accurate)
    *   Manages a `currentUiClientId` for session tracking.
    *   Initializes session with `POST /api/v1/session/init`.
    *   Fetches and populates preferences (e.g., `#pref-news-category`).
    *   Saves preferences with `POST /api/v1/session/preferences`.
    *   Includes `client_id` in podcast generation requests, allowing CPOA to use saved preferences.

## HTML Structure (`index.html`)

The `index.html` file provides the foundational layout for the single-page application. Key structural elements include:

-   **`<header class="site-header">`**:
    *   Contains branding elements (e.g., site title "Aethercast").
    *   Includes primary navigation links.
    *   Features a site-wide search input (`#header-search-input`).

-   **`<main>`**:
    *   Wraps the primary content sections of the page.
    *   **`<section id="latest-episodes-section">`**:
        *   Displays the main list of podcast snippets or search results in `#snippet-list-container`.
        *   Includes its own search bar (`#episodes-search-input`, `#episodes-search-btn`).
        *   Shows status messages related to snippet loading or search results in `#snippet-status-message`.
    *   **`<section id="popular-categories-section">`**:
        *   Dedicated to displaying popular podcast categories.
        *   Uses a `div` with class `.category-list-container` where category links/buttons are dynamically rendered by `app.js`.
    *   **`<section id="podcast-output-area">`**:
        *   Serves as the main area for feedback on podcast generation and for playback.
        *   Displays general status messages in `#status-messages`.
        *   Shows real-time generation progress updates in `#generation-progress-display`.
        *   Houses the audio players: `#audio-player` (for direct file playback) and `#audio-player-mse` (for streaming via MediaSource Extensions).
        *   Shows streaming-specific status in `#streaming-status` and a retry button (`#retry-stream-btn`).
        *   Displays detailed generation logs or metadata in `#generation-details-log` and the podcast topic in `#podcast-topic-title`.
    *   **(Other Sections for Future Features):** The HTML might include other sections like topic exploration inputs (`#explore-keywords-input`, `#explore-keywords-btn`) and results display (`#explored-topics-container`, `#explored-topics-status`), and user preferences management (`#preferences-section`, `#pref-news-category`, `#save-prefs-btn`, `#prefs-status`).

-   **`<div id="diagnostics-modal">`**: A modal window (initially hidden) for displaying detailed podcast generation diagnostics, including the CPOA orchestration log (`#diag-orchestration-log-container`).

This structure allows `app.js` to target specific containers for rendering dynamic content and updating status messages.

## Dependencies

-   **Socket.IO Client Library:** Relies on the Socket.IO client library being available (typically served by ASF or included manually).
-   No other external JavaScript frameworks are used.

## How to Run

The frontend is a set of static files served by the API Gateway.
1.  Ensure the API Gateway service is running.
2.  Navigate your browser to the root URL of the API Gateway (e.g., `http://localhost:5001/`).
The `app.js` script will then initialize, fetch initial content, and set up event listeners.
```
