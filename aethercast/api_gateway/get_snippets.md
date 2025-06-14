# Endpoint: GET /api/v1/snippets

## Purpose
This endpoint allows the frontend to fetch a list of podcast snippets, typically for display on a landing page or as general content suggestions. The generation of these snippets, including topic discovery, text generation, and cover art association, is orchestrated by the Central Podcast Orchestrator Agent (CPOA).

## Request Validation
- Method: GET
- Query Parameters:
  - `limit` (optional, integer, default: 6, max: 20): Maximum number of snippets to return.
- Optional Headers:
  - `X-Idempotency-Key` (string): While this is a GET request, providing an idempotency key can be useful if the client wants to ensure that any backend CPOA workflow triggered to refresh/generate snippets (e.g., if none are cached or available) is processed idempotently by the downstream services (TDA, SCA, IGA). CPOA may use this key if it initiates a new generation workflow.
  - `X-Workflow-ID` (string): An optional identifier to correlate this request with a larger client-side or end-to-end workflow. CPOA may log this ID or pass it to downstream services.

## Expected Response
- Status Code: 200 OK
- Body: JSON array of SnippetDataObjects. The structure of each object is determined by CPOA.
  ```json
  [
    {
      "snippet_id": "snippet_abcdef123",
      "topic_id": "topic_xyz789", // ID of the original topic if applicable
      "title": "The Future of AI in Snippets",
      "summary": "A brief look at how AI is changing snippet generation for podcasts and other media...",
      "text_content": "A brief look at how AI is changing snippet generation for podcasts and other media. This might include more details than the summary.", // Often similar to summary for placeholder snippets
      "image_url": "https://source.unsplash.com/random/400x225/?abstract,ai", // Example placeholder from IGA
      "cover_art_prompt": "Abstract concept of AI and creativity, podcast theme",
      "llm_model_used": "aims-sca-model-v1", // Example model ID from SCA via AIMS
      "generation_timestamp": "2024-03-15T12:00:00Z"
      // "audio_url" for snippets is typically a placeholder and not functional for playback.
      // Other fields as defined by CPOA's SnippetDataObject might be present.
    }
  ]
  ```
- **Notes on fields:**
    - `image_url`: May be a placeholder (e.g., from Unsplash if IGA is in placeholder mode) or a URL to a genuinely generated image if a real IGA is integrated.
    - `audio_url`: For snippets, this field is often a non-functional placeholder or absent, as snippets are primarily text and image previews.

## Error Responses
- Status Code: 400 Bad Request - If query parameters are invalid (e.g., `limit` out of range).
  ```json
  {
    "error_code": "API_GW_VALIDATION_ERROR",
    "message": "Invalid query parameters.",
    "details": { "limit": "Must be between 1 and 20." }
  }
  ```
- Status Code: 503 Service Unavailable - If CPOA or a critical downstream service (TDA, SCA, IGA) is unavailable.
  ```json
  {
    "error_code": "API_GW_CPOA_SNIPPET_SERVICE_UNAVAILABLE",
    "message": "Snippet generation service is currently unavailable.",
    "details": "Reason from CPOA or underlying service."
  }
  ```
- Status Code: 500 Internal Server Error - For other unexpected issues within the API Gateway or CPOA during snippet orchestration.
  ```json
  {
    "error_code": "API_GW_SNIPPETS_UNEXPECTED_ERROR",
    "message": "An unexpected error occurred while fetching snippets.",
    "details": "Specific error information."
  }
  ```

---

*For information on the overarching Aethercast project architecture, CPOA orchestration details, how idempotency keys are utilized by backend services, and shared database schemas (including `idempotency_keys` table), please refer to the main [README.md](../../../README.md) at the root of the Aethercast project and relevant documents in the `docs/architecture/` directory.*
