# AIMS (LLM) - API Placeholder

This document defines the API contract for the placeholder LLM service.

## Endpoint: `/v1/generate`

### Method
POST

### Request Body
```json
{
  "model_id_override": "string", // Optional: Identifier for a specific LLM to use (e.g., "gemini-1.5-pro-latest"). Also accepts "model" as an alias. If not provided, a service default is used.
  "prompt": "string", // The input prompt for the LLM
  "max_tokens": "integer", // Optional: Maximum number of tokens to generate
  "temperature": "float", // Optional: Sampling temperature
  "response_format": { // Optional: Specifies the desired output format.
    "type": "string" // Use "text" for plain text (default if field is omitted), or "json_object" for structured JSON output (if supported by the model).
  }
}
```

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*

### Success Response (Status Code: 200 OK)

#### For `response_format: {"type": "text"}` (or when `response_format` is omitted)
```json
{
  "request_id": "string", // Unique ID for this generation request
  "model_id": "string", // Identifier of the model actually used for generation (e.g., "gemini-1.0-pro").
  "choices": [
    {
      "text": "string", // The generated text
      "finish_reason": "string" // e.g., "MAX_TOKENS", "STOP", "SAFETY"
    }
  ],
  "usage": {
    "prompt_tokens": "integer",
    "completion_tokens": "integer",
    "total_tokens": "integer"
  }
}
```

#### For `response_format: {"type": "json_object"}`
(The structure of `choices[0].text` would be a JSON string, or the response might directly embed a JSON object depending on final implementation alignment with specific LLM provider capabilities. The schema below assumes the `text` field contains the JSON string.)
```json
{
  "request_id": "string",
  "model_id": "string", // Identifier of the model actually used for generation.
  "choices": [
    {
      "text": "string", // A string representation of the JSON object. Some LLMs might place the object directly here.
      "finish_reason": "string"
    }
  ],
  "usage": {
    "prompt_tokens": "integer",
    "completion_tokens": "integer",
    "total_tokens": "integer"
  }
}
```

### Error Response (Status Code: 4xx/5xx)
```json
{
  "error": {
    "type": "string", // e.g., "invalid_request_error", "api_error"
    "message": "string"
  }
}
```
