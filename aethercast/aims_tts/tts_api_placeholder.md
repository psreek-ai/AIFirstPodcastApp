# AIMS (TTS) - API Placeholder

**Disclaimer:** This document describes a conceptual API for a Text-to-Speech (TTS) service. The concrete implementation in `aethercast/aims_tts_service/` provides a subset of this functionality, primarily focused on file-based TTS generation using Google Cloud TTS. Refer to its specific README (`aethercast/aims_tts_service/README.md`) for current capabilities.

This document defines the API contract for the placeholder Text-to-Speech (TTS) service.

## Endpoint: `/v1/synthesize` (Asynchronous)

This endpoint initiates an asynchronous Text-to-Speech (TTS) task.

### Method
POST

### Request Body
```json
{
  "text": "string", // The text to synthesize
  "voice_id": "string", // Optional: e.g., "en-US-Wavenet-D"
  "language_code": "string", // Optional: e.g., "en-US"
  "audio_format": "string", // Optional: e.g., "MP3", "OGG_OPUS" (determines GCS object extension)
  "speech_rate": "float", // Optional: e.g., 1.0
  "pitch": "float" // Optional: e.g., 0.0
}
```
**Note:** The actual `aims_tts_service` implementation will pass these parameters to a Celery task. An `X-Idempotency-Key` header would be expected by the concrete service for this endpoint.

### Success Response (Status Code: 202 Accepted)
```json
{
  "task_id": "string", // Unique ID for the asynchronous TTS task
  "status_url": "/v1/tasks/string", // URL to poll for task status and result
  "message": "TTS synthesis task accepted."
  // "idempotency_key_processed": "string" // Would be included if X-Idempotency-Key was processed
}
```

## Endpoint: `/v1/tasks/<task_id>` (For Polling)

### Method
GET

### Description
Poll this endpoint to get the status and result of a TTS synthesis task.

### Success Response (Status Code: 200 OK - Task Completed)
```json
{
  "task_id": "string",
  "status": "SUCCESS", // Other statuses: PENDING, STARTED, FAILURE, RETRY
  "result": { // Present if status is SUCCESS
    "request_id": "string", // ID of the original synthesis request this task fulfilled
    "voice_id": "string", // Voice used for synthesis
    "audio_url": "gs://bucket-name/path/to/audio.mp3", // GCS URI of the generated audio
    "audio_duration_seconds": "float",
    "audio_format": "string" // e.g., "mp3"
  }
}
```

### Error Response (Status Code: 200 OK - Task Failed but Celery task completed)
If the Celery task itself completes but reports a failure in processing (e.g., TTS provider error).
```json
{
  "task_id": "string",
  "status": "SUCCESS", // Celery task itself completed by returning an error structure
  "result": {
    "error_code": "AIMS_TTS_SYNTHESIS_FAILED",
    "message": "Detailed error from TTS provider or internal processing."
  }
}
```

### Error Response (Status Code: 500 Internal Server Error - Celery Task Failed)
If the Celery task execution itself raised an unhandled exception.
```json
{
  "task_id": "string",
  "status": "FAILURE",
  "result": { // Contains error information from Celery
    "error": {"type": "task_failed", "message": "Details of the exception..."}
  }
}
```

### Other Responses
-   **202 Accepted:** If task is `PENDING` or `STARTED`. `result` field may be null or contain progress metadata.
-   **409 Conflict (Conceptual for Idempotency):** If the concrete `aims_tts_service` were to implement idempotency checks at the endpoint level before dispatching or if a task with the same key is already processing (though the placeholder doesn't detail this, the concrete service does).
    ```json
    {
        "task_id": "string", // ID of the conflicting/existing task
        "status": "PROCESSING_CONFLICT", // Custom status indicating conflict
        "message": "A task with the provided idempotency key is already processing.",
        "idempotency_key": "client_provided_idempotency_key"
    }
    ```

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
