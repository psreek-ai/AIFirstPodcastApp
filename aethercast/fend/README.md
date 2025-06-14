# Aethercast Frontend (FEND)

## Purpose

The Aethercast Frontend provides a web-based user interface for interacting with the Aethercast system.

## Features

The frontend allows users to:
-   View suggested podcast snippets.
-   **Search:**
    -   Search for content using the main search bar in the site header (activated by pressing 'Enter').
    -   Search within "Latest Episodes" using a dedicated search input and button.
    -   Both search methods utilize the `/api/v1/search/podcasts` endpoint (which requires user authentication) and display results in the main snippet area.
-   Explore popular podcast categories.
-   Initiate the generation of full podcast episodes based on chosen topics or snippets.
-   Receive status updates on podcast generation, including real-time progress via WebSockets.
-   Play back generated podcast audio, either through direct file links or real-time streaming via WebSockets and MediaSource Extensions (MSE).
-   Manage basic user preferences (e.g., preferred news category).
-   **Subscription:** A 'Subscribe' button in the site header opens a modal. Users can enter their email address to subscribe for updates. This functionality interacts with the public `/api/v1/subscribe` endpoint.

It is a single-page application (SPA) built with HTML, CSS, and vanilla JavaScript.

## Key Functionalities (`app.js`)

The core client-side logic resides in `aethercast/fend/app.js`.

1.  **Snippet Handling:**
    *   **Fetch Snippets:** Makes a `GET` request to `/api/v1/snippets`.
        *   *Idempotency Note:* While optional for GET, `app.js` could send an `X-Idempotency-Key` if this action might trigger significant backend generation via CPOA.
    *   **Display Snippets:** (As before).
    *   **Trigger Podcast from Snippet:** (As before, this initiates a podcast generation flow).

2.  **Popular Categories:** (As before - likely no idempotency key needed for this read operation).

3.  **Search Functionality:**
    *   **API Call:** Makes a `POST` request to `/api/v1/search/podcasts`.
        *   `app.js` **should** generate a unique `X-Idempotency-Key` (e.g., UUID) and include it in the headers for this request to ensure the search operation (which can trigger CPOA workflows) is processed idempotently by backend services. An `X-Workflow-ID` can also be included.
    *   **Display Results:** (As before).

4.  **Podcast Generation (from Topic Input or Snippet):**
    *   **API Call:** When generation is triggered, `app.js` makes a `POST` request to `/api/v1/podcasts`.
        *   It **must** generate a unique `X-Idempotency-Key` (e.g., UUID) and include it in the headers. An `X-Workflow-ID` (which could be the same as the idempotency key or a separate tracking ID) can also be sent.
        *   The request payload includes topic, `client_id`, etc.
    *   **Initial Response Handling:** The API Gateway returns a 202 Accepted response with a CPOA `workflow_id` (often referred to as `podcast_id` in this context for client tracking) and a `status_url` (e.g., `/api/v1/podcasts/<workflow_id>`).
    *   **Status Polling & Display:**
        *   `app.js` polls the received `status_url` to get updates on the CPOA workflow.
        *   Progress updates (if available from CPOA state and relayed via API GW or WebSocket) are shown in `#generation-progress-display`.
        *   Final status messages (success or failure) are displayed in `#status-messages`.

5.  **Podcast Playback & Streaming:**
    *   **Obtaining Audio Info:** When polling the CPOA workflow status (e.g., `/api/v1/podcasts/<workflow_id>`), a successful completion will include an `audio_url` (which is a relative path like `/api/v1/podcasts/<workflow_id>/audio.mp3` for API Gateway to stream from GCS) and potentially an `asf_websocket_url` and `stream_id` if real-time streaming via ASF is configured and ready.
    *   **Streaming via ASF:** If `asf_websocket_url` and `stream_id` are provided by the backend (after CPOA confirms VFA success and notifies ASF), `app.js` connects to ASF via WebSockets.
        *   (As before) Uses MSE, handles chunks, displays status.
    *   **Direct GCS Stream/Download (via API Gateway):** The `audio_url` (e.g., `/api/v1/podcasts/<podcast_id>/audio.mp3`) on the API Gateway will stream the final audio file from GCS (using signed URLs internally). This can be used by a standard HTML5 `<audio>` tag. The frontend decides whether to use this or ASF based on the response from the main podcast status polling.

6.  **UI Updates (via WebSockets):** (Largely as before) CPOA sends detailed progress updates via ASF, which `app.js` listens to for the active `client_id`.

7.  **Topic Exploration:**
    *   Calls `POST /api/v1/topics/explore`.
    *   `app.js` **should** generate and send an `X-Idempotency-Key` for this request, as it can trigger new content generation workflows in CPOA.
    *   Renders results as before.

8.  **Advanced Error Diagnostics:** (Description largely accurate)
    *   "View Diagnostics" button appears after podcast generation attempts.
    *   Opens a modal (`#diagnostics-modal`) to display detailed task information fetched from `/api/v1/podcasts/<podcast_id>`, including the CPOA orchestration log.

9.  **User Preferences:** (Description largely accurate)
    *   Manages a `currentUiClientId` for session tracking.
    *   Initializes session with `POST /api/v1/session/init`.
    *   Fetches and populates preferences (e.g., `#pref-news-category`).
    *   Saves preferences with `POST /api/v1/session/preferences`.
    *   Includes `client_id` in podcast generation requests, allowing CPOA to use saved preferences.
10. **Subscription Modal:**
    *   Handles the display and closing of the `#subscribe-modal`.
    *   Captures email input from `#subscribe-email-input`.
    *   Performs client-side validation of the email format.
    *   Submits the email to the `/api/v1/subscribe` backend endpoint.
    *   Displays success or error messages from the subscription attempt in `#subscribe-modal-status`.

## HTML Structure (`index.html`)

(No significant changes expected in this section due to backend idempotency logic, but ensure it's generally accurate.)

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
