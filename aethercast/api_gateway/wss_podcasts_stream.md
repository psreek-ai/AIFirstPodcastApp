# Endpoint: WSS /api/v1/podcasts/stream

## Purpose
This endpoint allows the frontend to establish a WebSocket connection for real-time streaming of generated podcast audio.

## Connection Handshake
- Method: GET (Upgrade to WebSocket)
- Query Parameters:
  - `podcast_id` (string): The ID of the podcast to stream.

## Messages
- Server-to-Client:
  - Audio Chunks: Binary audio data (e.g., MP3 or AAC chunks).
  - Stream Metadata: JSON objects indicating start/end of stream, errors, etc.
    ```json
    {
      "type": "metadata", // "start", "end", "error"
      "message": "string" // Optional message, e.g., error details
    }
    ```
- Client-to-Server:
  - (Potentially) Playback control messages like pause/resume, though this might be managed client-side initially.

## Expected Behavior
- Once connected, the server will stream audio chunks as they become available from the VoiceForgeAgent.
- The stream will close once the entire podcast has been streamed.

## Error Responses
- During Handshake:
  - Status Code: 400 Bad Request - If `podcast_id` is missing or invalid.
  - Status Code: 404 Not Found - If the podcast with the given ID doesn't exist or isn't ready for streaming.
  - Status Code: 500 Internal Server Error - If there's an issue establishing the WebSocket connection.
- During Streaming:
  - Metadata messages will indicate errors (e.g., if audio generation fails mid-stream).
