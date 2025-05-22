# Voice Forge Agent (VFA)

The Voice Forge Agent (VFA) is responsible for orchestrating the synthesis of a full podcast audio from a given script using a Text-to-Speech (TTS) service. It also manages the information needed for audio streaming.

## Key Responsibilities:

1.  **Input Processing:** Receives a `PodcastScript` object (from PSWA via CPOA) and voice preferences.
2.  **Script Segmentation & Preparation:**
    *   Processes the script segments from the `PodcastScript`.
    *   (Future) May apply segment-specific TTS instructions (e.g., different voices, prosody).
    *   For the current simulation, it will concatenate the text from all segments to send to the AIMS_TTS placeholder.
3.  **AIMS_TTS Interaction:**
    *   For each significant chunk of text (or the whole script for this simulation), VFA formulates a request for the AIMS_TTS service.
    *   Calls the AIMS_TTS placeholder service with the text and voice parameters.
    *   Receives metadata from AIMS_TTS (e.g., a URL to a sample audio file and its duration, as per the placeholder's hardcoded response).
4.  **Audio Assembly & Storage (Conceptual):**
    *   (Future) In a real system, VFA would receive actual audio data (or URLs to longer audio segments) from AIMS_TTS. It would then be responsible for assembling these segments into a complete podcast audio file and storing it in a suitable location (e.g., S3 bucket).
    *   **For current simulation:** It will use the placeholder audio URL from AIMS_TTS and primarily focus on setting up streaming.
5.  **Stream Management:**
    *   Generates a unique `stream_id`.
    *   Provides the `stream_id` and the WebSocket URL of the `AudioStreamFeeder` (ASF) back to the CPOA. This allows the frontend to connect to the correct stream for the generated podcast.
6.  **Output:** Returns metadata to CPOA, including the `stream_id`, the ASF's WebSocket URL, estimated duration, and voice used. This information is ultimately for the client/frontend.

## Integration:

*   **Called by:** Central Podcast Orchestrator Agent (CPOA) via an API endpoint (e.g., `POST /forge_audio`).
*   **Calls:** AIMS_TTS service (placeholder for now) to synthesize audio from text.
*   **Interacts with (Conceptually):** `AudioStreamFeeder` (ASF) by providing it with the generated audio content (or references to it) associated with a `stream_id`. For now, this interaction is loose, as ASF will simulate streaming predefined messages.
*   **Output:** Metadata for CPOA, including `stream_id`, `audio_stream_url` (ASF's WebSocket endpoint), `estimated_duration_seconds`, and `voice_used`.

This directory contains the source code and any specific configuration for the VFA service.
It will be a Python-based service (e.g., using Flask) that interacts with the placeholder AIMS_TTS service and prepares information for the conceptual ASF.
