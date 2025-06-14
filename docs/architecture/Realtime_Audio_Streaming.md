# Realtime_Audio_Streaming.md

**Version:** 1.0
**Date:** May 19, 2025
**Status:** Draft

## 1. Introduction and Purpose

This document specifies the design and mechanisms for real-time audio streaming within the Aethercast application. Aethercast's core premise is the on-demand, AI-driven generation of podcast content, which necessitates a robust and low-latency streaming solution to deliver audio to the user as it's being synthesized.

The purpose is to detail:
* The goals and requirements for the audio streaming subsystem.
* The components involved in the generation and delivery of the audio stream.
* The chosen streaming protocols, data formats, and client-side playback mechanisms.
* Strategies for buffering, latency management, error handling, and scalability.

This document focuses on the flow of audio data from the point of generation by the `VoiceForgeAgent` (VFA) to playback on the `Frontend UI (FEND)`.

## 2. Goals and Requirements for Real-time Audio Streaming

* **Low Latency (Time-to-First-Byte):** Minimize the delay between the user initiating podcast playback and the audio starting to play. Target: < 2-3 seconds from VFA starting audio generation to FEND playing the first chunk.
* **Smooth, Uninterrupted Playback:** Ensure continuous audio playback without noticeable gaps, stutters, or excessive buffering, assuming reasonable network conditions.
* **Real-time Generation Coupling:** The streaming solution must seamlessly integrate with the AI-driven audio generation process, where audio is produced in chunks by the VFA.
* **Scalability:** The streaming infrastructure must handle numerous concurrent users, each with an active audio stream.
* **Resilience:** Gracefully handle transient network issues and potential interruptions in the audio generation pipeline.
* **Cross-Platform Compatibility:** The chosen client-side technology should work across modern web browsers and potentially mobile platforms.
* **Resource Efficiency:** Minimize bandwidth consumption (through efficient codecs) and client-side processing overhead.

## 3. Core Components Involved

The real-time audio streaming process involves several key components from the `System_Architecture.md`:

1.  **`VoiceForgeAgent` (VFA):** Its primary `forge_voice_task` (an asynchronous, idempotent Celery task) is responsible for receiving the podcast script from CPOA. This task then calls the AIMS_TTS service to synthesize audio and obtain a GCS URI for the generated audio file.
2.  **`AI Model Serving Infrastructure (AIMS_TTS)`:** Hosts the TTS models. It's called by VFA's Celery task, performs TTS, and saves the audio to GCS, returning the GCS URI to VFA.
3.  **`Central Podcast Orchestrator Agent` (CPOA):** Initiates the `forge_voice_task` in VFA (passing `X-Idempotency-Key` and `X-Workflow-ID`). Once VFA's task successfully completes and CPOA retrieves the audio GCS URI (by polling VFA's task status endpoint), CPOA notifies ASF, providing it with a `stream_id` and the audio GCS URI. CPOA also provides the `stream_id` and ASF's WebSocket URL to the client via the API Gateway.
4.  **`Audio Stream Feeder` (ASF):**
    * Receives notification from CPOA about a new audio stream, including its `stream_id` and GCS URI.
    * Manages WebSocket connections from clients. When a client requests a `stream_id`, ASF fetches the audio from the GCS URI (using a signed URL obtained from the API Gateway) and streams it in chunks.
5.  **`Frontend UI` (FEND):** (As before) Establishes WebSocket connection to ASF, receives, buffers, and plays audio.
6.  **`API Gateway` (APIGW):** (As before) Involved in initial WebSocket handshake and provides an internal endpoint for ASF to get signed GCS URLs.

## 4. Streaming Initiation Process

This process outlines how a FEND client initiates and establishes an audio stream.

1.  **User Action & Podcast Generation Request:** User requests a podcast. API Gateway forwards to CPOA, which orchestrates WCHA, PSWA, and eventually VFA by dispatching its `forge_voice_task` (with `X-Idempotency-Key`). CPOA receives a Celery `task_id` for the VFA operation.
2.  **Stream Identifier Generation:** CPOA generates a unique `stream_id` that will be associated with the VFA's audio generation task and the resulting audio.
3.  **CPOA Initial Response to FEND (via APIGW):** CPOA might provide an initial response indicating podcast generation is in progress. This response includes the `stream_id` and the `websocket_url` for ASF. Crucially, the audio is not yet ready for streaming at this point.
    * **Data Example:** `{"status": "GENERATING", "cpoa_workflow_id": "cpoa_workflow_uuid", "stream_id": "unique_stream_id_xyz", "websocket_url": "wss://aethercast.example.com/api/v1/podcasts/stream"}`.
4.  **VFA Task Completion & ASF Notification:**
    * CPOA polls VFA's `/v1/tasks/<vfa_celery_task_id>` endpoint.
    * Once VFA's `forge_voice_task` completes successfully, its result (containing the GCS URI of the audio, e.g., `gs://bucket/audio.mp3`) is retrieved by CPOA.
    * CPOA then makes an HTTP POST request to ASF's internal `/asf/internal/notify_new_audio` endpoint, providing the `stream_id` and the audio `filepath` (the GCS URI).
5.  **FEND Establishes Stream Connection:**
    * Upon receiving the initial response from CPOA (with `stream_id` and `websocket_url`), FEND initiates a WebSocket connection to ASF.
    * FEND sends the `stream_id` to ASF. ASF will now have (or soon receive) the GCS URI for this `stream_id` from CPOA's notification. Once ASF has the GCS URI, it can begin fetching and streaming the audio.

## 5. Audio Generation and Chunking (AIMS_TTS, VFA Task, ASF)

1.  **Script Segmentation (VFA Task):** VFA's Celery task prepares the script text.
2.  **TTS Synthesis (VFA Task calling AIMS_TTS):** VFA's task calls AIMS_TTS (which is itself async and returns a task_id that VFA polls). AIMS_TTS synthesizes audio and saves it to GCS, returning the GCS URI to VFA.
3.  **Audio Fetching by ASF:** Once ASF is notified by CPOA and a client connects for a `stream_id`, ASF:
    *   Obtains a short-lived signed HTTP URL for the GCS URI (by calling an internal API Gateway endpoint).
    *   Fetches the audio from this signed URL via a streaming HTTP GET request.
4.  **Encoding & Chunking by ASF:**
    *   The audio from GCS is already in a final encoded format (e.g., MP3, OGG Opus, as determined by AIMS_TTS).
    *   ASF reads this audio data in chunks from the HTTP stream.
    *   These chunks are then sent as binary WebSocket messages.
    *   **Chunk Size:** A balance between latency and overhead. (As before).

## 6. Streaming Protocol: WebSockets

* **Rationale:**
    * **Bidirectional & Full-Duplex:** While primarily server-to-client for audio data, WebSockets allow for client-to-server messages (e.g., acknowledgments, pause/resume commands in future, feedback).
    * **Low Latency:** Lower overhead compared to repeated HTTP requests, suitable for real-time data.
    * **Persistent Connection:** Avoids the overhead of establishing new connections for each chunk.
    * **Wide Browser Support:** Well-supported in modern browsers.
* **Connection:** Secure WebSockets (WSS) must be used for encrypted communication.
* **Message Format:**
    * Audio data chunks will be sent as **binary messages** over the WebSocket.
    * Control messages (e.g., stream start, end, error, metadata) can be sent as JSON text messages.
        * Example Control Message (Server to Client): `{"type": "stream_metadata", "codec": "opus", "sampleRate": 48000, "channels": 1}`
        * Example Control Message (Server to Client): `{"type": "stream_end"}`
        * Example Control Message (Server to Client): `{"type": "error", "message": "TTS service temporarily unavailable"}`

## 7. Audio Data Format of Chunks

* **Codec:** **Opus**.
    * Target Bitrate: Variable, depending on desired quality vs. bandwidth. For voice, 24-40 kbps often provides excellent quality.
    * Sample Rate: Typically 16 kHz, 24 kHz, or 48 kHz for Opus. VFA/ASF will signal this to FEND.
    * Channels: Mono (1 channel) for podcast voice.
* **Container/Framing (within WebSocket binary messages):**
    * Each WebSocket binary message should contain one or more complete Opus packets.
    * If sending raw Opus packets, the client needs an Opus decoder.
    * Alternatively, chunks could be wrapped in a very lightweight container format if needed (e.g., Ogg Opus segments, though this adds slight overhead). For simplicity and low latency, sending raw Opus packets directly is often preferred if the client can handle it.

## 8. Frontend Audio Playback (FEND)

1.  **WebSocket Client:** FEND establishes and maintains the WebSocket connection to the ASF.
2.  **Receiving Chunks:** FEND listens for binary messages containing audio chunks and text messages for control signals.
3.  **Decoding:** If Opus packets are received, FEND will need an Opus decoder (e.g., `libopus.js` compiled to WebAssembly, or browser-native support if available and reliable for streaming input).
4.  **Buffering & Playback with Media Source Extensions (MSE):**
    * MSE is the standard browser API for handling adaptive streaming and custom media sources.
    * **Workflow:**
        1.  Create a `MediaSource` object.
        2.  Create an `Audio` element in HTML and set its `src` to a URL created from the `MediaSource` object (`URL.createObjectURL(mediaSource)`).
        3.  When the `MediaSource` `sourceopen` event fires, create a `SourceBuffer` with the appropriate MIME type (e.g., `audio/ogg; codecs=opus` or `audio/webm; codecs=opus` if chunks are wrapped in WebM, or a specific type if just raw Opus is being fed and MSE supports it directly via a specific MIME type).
        4.  As Opus audio chunks (potentially wrapped in Ogg/WebM if MSE requires it for Opus) are received via WebSocket and decoded (if necessary, to PCM, then re-encoded if MSE needs a different container for Opus, or directly fed if MSE supports raw Opus packet streams), append them to the `SourceBuffer` using `sourceBuffer.appendBuffer(audioData)`.
        5.  The browser's media engine handles playback from the `SourceBuffer`.
5.  **Alternative (Simpler, Less Robust): Web Audio API**
    * For very low-latency needs and if MSE seems too complex initially, decoded PCM audio chunks could potentially be queued and played using the Web Audio API (`AudioBufferSourceNode`).
    * However, MSE is generally better for managing buffering, seeking (future), and handling different codecs/containers for media playback.
    * **Recommendation:** Start with **Media Source Extensions (MSE)** for robustness and future flexibility, even if it has a steeper learning curve.

## 9. Buffering Strategy

* **Client-Side (FEND):**
    * **Goal:** Maintain enough audio data in the `SourceBuffer` to ensure smooth playback despite network jitter or slight variations in chunk arrival times.
    * **Initial Buffer:** FEND should wait until a certain amount of audio (e.g., 2-5 seconds) is buffered before starting playback to prevent immediate underrun.
    * **Target Buffer Length:** Aim to keep a rolling buffer of, for example, 5-15 seconds of audio ahead of the current playback time.
    * **Buffer Management:** FEND needs to monitor the `SourceBuffer`'s buffered time ranges and the `HTMLMediaElement`'s `currentTime`. If the buffer runs low (underrun), FEND should pause playback and display a buffering indicator until enough data is re-buffered. If the buffer grows too large (overrun), older data can be removed from the `SourceBuffer` using `sourceBuffer.remove()` to manage memory, though this is less common an issue than underrun.
* **Server-Side (VFA/ASF):**
    * VFA might generate a few chunks slightly ahead of what's immediately needed by ASF to ensure a steady supply.
    * ASF might hold a small buffer of chunks received from VFA before forwarding them, to smooth out any minor timing variations from VFA's generation process. This buffer should be minimal to avoid increasing overall latency.

## 10. Stream Lifecycle Management

* **Connection Establishment:** As per Section 4.
* **Stream Active:**
    * ASF continuously pushes audio chunks (binary messages) to FEND.
    * ASF may send periodic metadata or keep-alive messages (text messages) if needed.
* **Pause/Resume (User-initiated on FEND):**
    * **Pause:** FEND pauses the HTML `Audio` element. It *may* signal the ASF/CPOA (e.g., via a WebSocket text message: `{"type": "pause_request"}`).
        * If ASF/CPOA receives a pause, VFA *could* temporarily halt TTS generation to save resources. This adds complexity.
        * Simpler: VFA continues generating, ASF continues sending, FEND just stops appending to `SourceBuffer` or stops playback. When resuming, FEND starts appending/playing again. This is easier but less resource-efficient on the backend if pauses are long.
    * **Resume:** FEND resumes the HTML `Audio` element. If backend generation was paused, FEND signals ASF/CPOA (`{"type": "resume_request"}`) to restart generation/sending.
* **Stream Termination:**
    * **Normal End:** ASF finishes streaming all audio chunks from the GCS file. It sends a final control message to FEND (e.g., `{"type": "stream_end"}`).
    * **User Closes Player/Navigates Away:** FEND closes the WebSocket connection. ASF detects the closure. Since VFA's task is likely already complete (audio is on GCS), no specific action needs to be taken on VFA unless it was an exceptionally long audio file still being "virtually" processed by VFA post-generation for some reason (unlikely in current model). CPOA would be aware of the main podcast generation workflow's state.
    * **Server-Initiated Termination (Error):** If ASF encounters an unrecoverable error (e.g., cannot get signed URL, GCS file deleted), it sends an error message to FEND and closes the WebSocket.
* **Reconnection Logic (FEND):** (As before - simpler initial approach is likely stream failure).

## 11. Latency Considerations & Optimization

* **VFA Task Completion Time:** The time taken for VFA's Celery task (including AIMS_TTS interaction and GCS upload) is now a prerequisite before ASF can even start streaming. This is a shift from a model where VFA might stream chunks *as* they are generated internally.
* **AIMS_TTS Inference Time:** (As before) Key latency factor for VFA's task.
* **ASF Fetch & Stream Latency:** Once ASF is notified and has the GCS URI:
    * Latency to get signed URL from API Gateway.
    * Latency for ASF to start fetching from GCS.
    * Network Latency between FEND-ASF.
* **Chunk Size:** (As before) Still relevant for ASF's streaming to FEND.
* **Pipelining:** CPOA pipelines agent calls. VFA's internal polling of AIMS_TTS is a form of pipelining. ASF fetches from GCS and streams to FEND.
* **Early Metadata:** (As before) ASF should send this once it starts processing a stream.

## 12. Error Handling and Resilience

* **VFA Celery Task Failure:**
    * If VFA's `forge_voice_task` fails (e.g., AIMS_TTS error, GCS upload error), its `on_failure` handler updates the idempotency record in PostgreSQL.
    * CPOA, when polling VFA's task status endpoint, will see the failure. CPOA updates its own `task_instance` and `workflow_instance` in PostgreSQL.
    * CPOA's workflow logic determines if retries (by re-dispatching VFA task with same idempotency key) are appropriate or if the overall podcast generation fails.
    * If ASF was already notified or a client connected, CPOA might need to signal ASF to terminate the (now invalid) stream_id.
* **ASF Failure:** (As before)
* **Network Disconnection (WebSocket):** (As before - ASF detects client disconnect).
* **FEND Playback Errors:** (As before)
* **Buffer Underrun on FEND:** (As before)

## 13. Scalability of Streaming Infrastructure

* **ASF (`Audio Stream Feeder`):** (As before - needs to be highly scalable for WebSocket connections).
* **VFA Celery Workers:** Scaled independently based on the number of concurrent TTS generation tasks.
* **AIMS_TTS:** (As before) Scaled based on TTS inference load.
* **Bandwidth:** (As before) For ASF outgoing streams.
* **PostgreSQL Database:** The database handling idempotency records and CPOA state must be scalable.

## 14. Security Considerations

* **Secure WebSockets (WSS):** All WebSocket connections must use WSS (TLS encryption).
* **Authentication/Authorization:**
    * The initial request to `POST /api/v1/podcasts/generate` should be authenticated.
    * The `stream_id` acts as a temporary, single-use capability token for accessing a specific stream via WebSocket. It should be unguessable and expire or be invalidated after the stream ends or on error.
    * The WebSocket endpoint (`/api/v1/podcasts/stream`) should validate the `stream_id` against active generation tasks managed by CPOA/VFA before allowing the connection to proceed.
* **Rate Limiting:** Apply rate limiting on the API Gateway for stream initiation requests.
* **Data Validation:** ASF should validate any control messages received from FEND over WebSocket.

## 15. Metrics and Monitoring

* **ASF Metrics:**
    * Number of active WebSocket connections.
    * Data throughput (bytes sent/received per stream, total).
    * Message send/receive rates.
    * Connection errors, disconnections.
    * Latency between receiving a chunk from VFA and sending it to FEND.
* **VFA Metrics:**
    * TTS generation latency per chunk/segment.
    * Audio encoding time per chunk.
    * Number of active generation tasks.
    * Error rates from AIMS_TTS.
* **FEND Metrics (Client-Side Monitoring):**
    * Buffer levels (`SourceBuffer.buffered`, `HTMLAudioElement.buffered`).
    * Playback stalls (underruns).
    * Time to first audio byte.
    * Decoding errors.
    * WebSocket connection status and errors.
* **Logging:** Detailed logs from ASF, VFA, and FEND (client-side) correlated with `stream_id` and `user_id` for debugging.

This detailed specification should provide a strong foundation for implementing the real-time audio streaming functionality in Aethercast.

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
