# Aethercast Frontend (FEND)

This directory contains a very basic HTML, CSS, and JavaScript frontend to interact with the Aethercast backend services (CPOA, and indirectly, ASF).

## Features:

1.  **Snippet Display:**
    *   Fetches a list of podcast snippets from the CPOA (`GET /api/v1/snippets`) on page load.
    *   Displays these snippets, typically showing title and summary.
2.  **Podcast Generation Request:**
    *   Each snippet has a "Listen" or "Generate Podcast" button.
    *   Clicking this button sends a request to CPOA (`POST /api/v1/podcasts/generate`) using the snippet's associated data (e.g., `topic` derived from snippet, or `snippet_id`).
3.  **Task Status Polling:**
    *   After requesting podcast generation, the UI polls CPOA's task status endpoint (`GET /api/v1/tasks/<task_id>`) to check the progress.
4.  **WebSocket Connection to ASF:**
    *   Once CPOA signals that the podcast is ready for streaming (by providing `audio_stream_url_for_client` and `stream_id` via the task status endpoint), the frontend:
        *   Establishes a WebSocket connection to the Audio Stream Feeder (ASF) using the provided URL.
        *   Sends a `join_stream` message to ASF with the `stream_id`.
5.  **Simulated Audio Streaming:**
    *   Listens for messages from ASF over the WebSocket.
    *   Displays received messages (simulated audio chunks and control messages) in the UI or console.
    *   No actual audio playback is implemented in this version.
6.  **Status Updates:** Provides feedback to the user about ongoing processes (e.g., "Fetching snippets...", "Generating podcast...", "Connecting to stream...", "Streaming chunk...").

## Files:

*   `index.html`: The main HTML structure.
*   `style.css`: Basic CSS for styling.
*   `app.js`: JavaScript for API communication, WebSocket handling, and UI updates.

## How to Run:

1.  Ensure all backend services are running:
    *   CPOA (Central Podcast Orchestrator Agent)
    *   TDA (Topic Discovery Agent) - if CPOA is configured to use it for snippet generation.
    *   SCA (Snippet Craft Agent) - if CPOA is configured to use it.
    *   WCHA (Web Content Harvester Agent) - if CPOA is configured to use it.
    *   PSWA (Podcast Script Weaver Agent) - if CPOA is configured to use it.
    *   VFA (Voice Forge Agent) - if CPOA is configured to use it.
    *   ASF (Audio Stream Feeder)
2.  Open `index.html` in a web browser that supports modern JavaScript and WebSockets.

## CPOA Endpoints Used:

*   `GET /api/v1/snippets` (CPOA should be configured to generate one on demand for this to work as designed in CPOA's current version)
*   `POST /api/v1/podcasts/generate`
*   `GET /api/v1/tasks/<task_id>`

## ASF WebSocket Interaction:

*   Connects to the WebSocket URL provided by CPOA (which VFA constructs, e.g., `ws://localhost:5005/api/v1/podcasts/stream`).
*   Sends `join_stream` event with `{'stream_id': '...'}`.
*   Receives `stream_status`, `audio_control`, and `text_chunk` events. (Note: ASF `main.py` was built to handle a namespaced connection, so the client-side JS will need to reflect that, e.g. `io('/api/v1/podcasts/stream')`)

This is a simplified frontend for demonstration and testing of the backend pipeline.
