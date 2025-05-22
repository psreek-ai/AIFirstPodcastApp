# AIMS (LLM) - API Placeholder

This document defines the API contract for the placeholder LLM service.

## Endpoint: `/v1/generate`

### Method
POST

### Request Body
```json
{
  "model_id": "string", // Identifier for the specific LLM to use (e.g., "AetherLLM-Snippet-v1", "AetherLLM-PodcastScript-v1")
  "prompt": "string", // The input prompt for the LLM
  "max_tokens": "integer", // Maximum number of tokens to generate
  "temperature": "float", // Sampling temperature
  "context": { // Optional: for providing broader context if needed
    "topic_keywords": ["string"],
    "previous_text": "string"
  },
  "response_format": "string" // "text" or "json" (if structured output is needed)
}
```

### Success Response (Status Code: 200 OK)

#### For `response_format: "text"`
```json
{
  "request_id": "string", // Unique ID for this generation request
  "model_id": "string", // Model used
  "choices": [
    {
      "text": "string", // The generated text
      "finish_reason": "string" // e.g., "length", "stop_sequence"
    }
  ],
  "usage": {
    "prompt_tokens": "integer",
    "completion_tokens": "integer",
    "total_tokens": "integer"
  }
}
```
**Hardcoded Placeholder Response (for text generation):**
```json
{
  "request_id": "aims-llm-placeholder-req-123",
  "model_id": "AetherLLM-Placeholder-v0.1",
  "choices": [
    {
      "text": "This is a placeholder response from the AIMS LLM service. Based on your prompt, here's a generic title: 'Interesting Developments' and some generic content: 'Several interesting developments have occurred recently, leading to much discussion and speculation within the community. Further analysis is required to fully understand the implications.'",
      "finish_reason": "length"
    }
  ],
  "usage": {
    "prompt_tokens": 10, // Placeholder
    "completion_tokens": 50, // Placeholder
    "total_tokens": 60 // Placeholder
  }
}
```

#### For `response_format: "json"`
(Schema would depend on the specific structured output required by the agent)
```json
{
  "request_id": "string",
  "model_id": "string",
  "structured_output": {
    // Agent-specific JSON structure
  },
  "usage": { ... }
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
