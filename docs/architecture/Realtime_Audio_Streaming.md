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

1.  **`VoiceForgeAgent` (VFA):** Responsible for receiving the podcast script from the CPOA and using Text-to-Speech (TTS) models to synthesize audio data in manageable chunks.
2.  **`AI Model Serving Infrastructure (AIMS_TTS)`:** Hosts the TTS models used by the VFA.
3.  **`Central Podcast Orchestrator Agent` (CPOA):** Initiates the audio generation task for VFA and provides the FEND with the necessary information (e.g., a `stream_id`) to connect to the audio stream.
4.  **`Audio Stream Feeder` (ASF - Conceptual Service):**
    * This is a logical component responsible for receiving audio chunks from the VFA and pushing them to the connected FEND client.
    * It manages active stream connections (e.g., WebSocket connections).
    * It could be implemented as a dedicated microservice, part of the VFA's responsibilities, or integrated within a broader API Gateway/backend infrastructure designed for real-time communication. For clarity, we'll treat it as a distinct logical service.
5.  **`Frontend UI` (FEND):** The client application (web or mobile) that establishes a connection to the ASF, receives audio chunks, buffers them, and plays them back using appropriate audio APIs.
6.  **`API Gateway` (APIGW):** May be involved in the initial WebSocket handshake upgrade request or routing to the ASF.

## 4. Streaming Initiation Process

This process outlines how a FEND client initiates and establishes an audio stream. (Referenced from `Data_Flows.md` - Flow 2).

1.  **User Action:** User clicks "Listen" on a snippet or requests a podcast.
2.  **FEND Request:** FEND sends a request to APIGW (e.g., `POST /api/v1/podcasts/generate`) to start podcast generation.
3.  **CPOA Orchestration:** CPOA receives the request and initiates the podcast generation workflow, eventually tasking the VFA.
4.  **Stream Identifier Generation:** CPOA (or VFA upon task initiation) generates a unique `stream_id`.
5.  **CPOA Initial Response to FEND:** CPOA, via APIGW, sends an initial response to FEND.
    * **Data:** `{"status": "GENERATING", "stream_id": "unique_stream_id_xyz", "estimated_wait_time_seconds": 10, "websocket_url": "wss://aethercast.example.com/api/v1/podcasts/stream"}`.
    * The `websocket_url` is the endpoint for the ASF.
6.  **FEND Establishes Stream Connection:**
    * Upon receiving the `stream_id` and `websocket_url`, FEND initiates a WebSocket connection to the ASF.
    * During the WebSocket handshake or as the first message after connection, FEND sends the `stream_id` to ASF for identification and association with the ongoing VFA generation task.
    * Example WebSocket connection request: `wss://aethercast.example.com/api/v1/podcasts/stream?streamId=unique_stream_id_xyz` (passing `stream_id` as a query parameter is common).

## 5. Audio Generation and Chunking (VFA & AIMS_TTS)

1.  **Script Segmentation:** VFA receives the full podcast script from CPOA. It may break the script into smaller segments (e.g., sentences, paragraphs) suitable for individual TTS synthesis calls. This helps in achieving lower first-chunk latency and allows for pipelining.
2.  **TTS Synthesis:** For each script segment, VFA calls AIMS_TTS.
3.  **Audio Output Format from TTS:** AIMS_TTS returns raw audio data (e.g., PCM).
4.  **Encoding & Chunking by VFA/ASF:**
    * The raw audio (PCM) is encoded into a streaming-friendly and efficient codec. **Opus** is highly recommended for voice due to its quality at various bitrates and low latency characteristics. Alternatives include AAC or MP3, but Opus is generally superior for this use case.
    * The encoded audio is then packetized/chunked into manageable sizes by VFA or the ASF before being sent over the WebSocket.
    * **Chunk Size:** A balance between latency and overhead. Smaller chunks mean lower latency for each piece but more network/processing overhead. Typical chunk durations might be 200ms to 1 second of audio.
    * Each chunk should ideally be self-contained or carry enough information for the client to decode it. For Opus, this typically means sending individual Opus packets.

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
    * **Normal End:** VFA finishes synthesizing the entire script. It signals "end of script" to ASF. ASF sends the remaining audio chunks, followed by a final control message to FEND (e.g., `{"type": "stream_end"}`). FEND stops trying to buffer more data once the `SourceBuffer` update ends and the `ended` event fires on the audio element.
    * **User Closes Player/Navigates Away:** FEND closes the WebSocket connection. ASF detects the closure and signals VFA (via CPOA) to stop the generation task for that `stream_id` to free up resources.
    * **Server-Initiated Termination (Error):** If VFA or ASF encounters an unrecoverable error, ASF sends an error message to FEND (e.g., `{"type": "error", "message": "..."}`) and closes the WebSocket.
* **Reconnection Logic (FEND):**
    * If the WebSocket connection drops unexpectedly, FEND should attempt to reconnect (with exponential backoff).
    * Upon reconnection, FEND would need to inform ASF of the `stream_id` and potentially the last received chunk/timestamp to attempt a seamless resume. This requires ASF and VFA to support resuming generation or re-transmitting missed chunks, which adds significant complexity.
    * Simpler initial approach: If connection drops, the stream is considered failed, and the user might need to restart. Robust resume is a v2 feature.

## 11. Latency Considerations & Optimization

* **TTS Inference Time:** The primary source of latency.
    * Use highly optimized TTS models and serving infrastructure (AIMS_TTS).
    * VFA should segment scripts into small enough pieces for quick TTS processing per segment.
* **Network Latency:** Between all components (FEND-ASF, ASF-VFA, VFA-AIMS_TTS). Minimized by geographically distributed services and efficient protocols.
* **Chunk Size:** Smaller audio chunks reduce perceived latency for each piece but increase overhead. Optimize based on testing.
* **Processing Overhead:** Minimize processing in VFA, ASF, and FEND for encoding, decoding, and message handling.
* **Pipelining:** VFA should pipeline script segmentation, TTS calls, and chunk encoding/sending to ASF. ASF pipelines receiving from VFA and sending to FEND. FEND pipelines receiving, decoding, and buffering.
* **Early Metadata:** Send codec/stream metadata to FEND as soon as possible so it can initialize `MediaSource` and `SourceBuffer` while the first audio chunks are being generated.

## 12. Error Handling and Resilience

* **VFA/AIMS_TTS Failure:**
    * If TTS fails for a segment, VFA reports to CPOA/ASF.
    * ASF can send an error message to FEND.
    * Options:
        * Terminate stream.
        * Attempt to skip the problematic segment and continue (if script structure allows).
        * (Fallback) VFA uses a simpler/backup TTS model for that segment.
* **ASF Failure:** If ASF crashes, WebSocket connections drop. CPOA should detect ASF unhealthiness.
* **Network Disconnection (WebSocket):**
    * FEND: Detects `onclose` or `onerror` WebSocket events. Attempts reconnection as per Section 10.
    * ASF: Detects client disconnection. Notifies CPOA/VFA to halt generation for that `stream_id`.
* **FEND Playback Errors:**
    * MSE errors (e.g., `MediaError` on audio element, errors appending to `SourceBuffer`): Log and potentially display a user-friendly error.
    * Decoding errors: If audio chunks are corrupted.
* **Buffer Underrun on FEND:** Pause playback, show buffering indicator, resume when sufficient buffer is available. ASF should ideally send data at a rate that prevents this under normal network conditions.

## 13. Scalability of Streaming Infrastructure

* **ASF (`Audio Stream Feeder`):** This component must be highly scalable.
    * Likely implemented using technologies optimized for many concurrent connections (e.g., Node.js, Go, or Java/Kotlin with non-blocking I/O like Netty/Vert.x).
    * Deployed as multiple instances behind a load balancer that supports WebSocket sticky sessions (if ASF instances maintain stream-specific state not shared externally) or can route based on `stream_id`.
    * Stateless ASF instances are preferable, with stream state managed externally (e.g., Redis) or by VFA.
* **VFA:** Scaled independently based on the number of concurrent TTS generation tasks.
* **AIMS_TTS:** Scaled based on TTS inference load.
* **Bandwidth:** Ensure sufficient network bandwidth for outgoing audio streams from ASF.

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
