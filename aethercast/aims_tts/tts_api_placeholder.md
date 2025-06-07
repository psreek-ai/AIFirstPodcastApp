# AIMS (TTS) - API Placeholder

**Disclaimer:** This document describes a conceptual API for a Text-to-Speech (TTS) service. The concrete implementation in `aethercast/aims_tts_service/` provides a subset of this functionality, primarily focused on file-based TTS generation using Google Cloud TTS. Refer to its specific README (`aethercast/aims_tts_service/README.md`) for current capabilities.

This document defines the API contract for the placeholder Text-to-Speech (TTS) service.

## Endpoint: `/v1/synthesize`

### Method
POST

### Request Body
```json
{
  "text": "string", // The text to synthesize into speech
  "voice_id": "string", // Identifier for the desired voice (e.g., "AetherVoice-Nova", "en-US-Wavenet-D")
  "language_code": "string", // Optional: Language code (e.g., "en-US")
  "output_format": "string", // Desired audio format (e.g., "mp3", "aac", "pcm", "LINEAR16", "OGG_OPUS")
  "speech_rate": "float", // Optional: Speed of speech (e.g., 1.0 for normal, 0.25 for slowest, 4.0 for fastest)
  "pitch": "float", // Optional: Pitch adjustment (e.g., 0.0 for normal, -20.0 for lowest, 20.0 for highest)
  "response_type": "string" // "url" or "stream".
                           // Note: The current `aims_tts_service` implementation primarily supports a file generation workflow.
                           // When 'url' is implied or requested, it returns a file path within a shared volume, not a direct HTTP URL.
                           // True 'stream' response is not implemented by the `aims_tts_service`.
}
```

### Success Response (Status Code: 200 OK)

#### For `response_type: "url"`
```json
{
  "request_id": "string", // Unique ID for this synthesis request
  "voice_id": "string", // Voice used
  "audio_url": "string", // URL or path to the generated audio file. (Note: The actual `aims_tts_service` returns a file path here, e.g., "/shared_audio/aims_tts/audio.mp3").
  "audio_duration_seconds": "float", // Duration of the audio in seconds
  "audio_format": "string" // Format of the audio (e.g., "mp3")
}
```
**Illustrative Placeholder Response (for `response_type: "url"`):**
```json
{
  "request_id": "aims-tts-placeholder-req-example",
  "voice_id": "PlaceholderVoice-Standard",
  "audio_url": "/path/to/audio/on/shared/volume/sample-audio.mp3", // Example file path
  "audio_duration_seconds": 3.0,
  "audio_format": "mp3"
}
```

#### For `response_type: "stream"`
The response would ideally be a direct audio stream (e.g., `audio/mpeg`). The current `aims_tts_service` does not implement this.
A placeholder might simulate this by returning a JSON response indicating the intent.

**Illustrative Placeholder Response (simulating `response_type: "stream"`):**
```json
{
  "request_id": "aims-tts-placeholder-req-stream-example",
  "voice_id": "PlaceholderVoice-Standard",
  "message": "This is a placeholder response. If 'stream' type were fully supported, audio data would be streamed here.",
  "audio_format": "mp3" // Intended stream format
}
```

### Error Response (Status Code: 4xx/5xx)
```json
{
  "error": {
    "type": "string", // e.g., "invalid_request_error", "tts_failure"
    "message": "string"
  }
}
```
