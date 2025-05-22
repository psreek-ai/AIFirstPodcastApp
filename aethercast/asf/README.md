# Audio Stream Feeder (ASF)

The Audio Stream Feeder (ASF) is responsible for managing real-time audio streaming to connected clients (e.g., the frontend application) via WebSockets.

## Key Responsibilities:

1.  **WebSocket Server:** Implements a WebSocket server that clients can connect to for receiving audio streams.
2.  **Stream Association:**
    *   Handles connections to specific stream endpoints, typically identified by a `stream_id` in the WebSocket URL (e.g., `wss://aethercast.example.com/api/v1/podcasts/stream/{stream_id}`).
    *   (Future) Associates incoming WebSocket connections with audio data prepared by the Voice Forge Agent (VFA). VFA would notify ASF about available audio for a given `stream_id`.
3.  **Audio Chunk Streaming:**
    *   (Future) Retrieves the actual audio data (e.g., from a file system, S3, or a queue populated by VFA).
    *   (Future) Breaks the audio data into small chunks suitable for streaming.
    *   (Future) Sends these audio chunks (as binary messages) over the WebSocket connection to the client.
    *   **For current simulation:** When a client connects, ASF will send a few predefined text messages (e.g., "Audio chunk 1", "Audio chunk 2", "End of stream") to simulate the streaming process.
4.  **Stream Lifecycle Management:**
    *   Handles client connections and disconnections.
    *   Sends control messages (e.g., "start_of_stream", "end_of_stream", error messages).
    *   (Future) Manages buffering and flow control.

## Integration:

*   **Receives Data From (Conceptually):** Voice Forge Agent (VFA). VFA would make the full audio content available to ASF, associated with a `stream_id`. (For now, this is a loose coupling; ASF doesn't actively receive data from VFA yet).
*   **Clients Connect To:** ASF's WebSocket endpoint (e.g., `/api/v1/podcasts/stream/{stream_id}`).
*   **Output:** Streams audio data (simulated as text messages for now) and control messages to connected WebSocket clients.

This directory contains the source code for the ASF service.
It will be a Python-based service using Flask-SocketIO to handle WebSocket connections.
The primary goal for this stage is to set up the WebSocket endpoint and simulate basic streaming behavior.
