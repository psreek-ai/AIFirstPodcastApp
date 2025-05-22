# Endpoint: GET /api/v1/snippets

## Purpose
This endpoint allows the frontend to fetch a list of podcast snippets.

## Request Validation
- Method: GET
- Query Parameters:
  - `topic` (optional, string): Filter snippets by topic.
  - `limit` (optional, integer, default: 10): Maximum number of snippets to return.
  - `offset` (optional, integer, default: 0): Number of snippets to skip for pagination.

## Expected Response
- Status Code: 200 OK
- Body: JSON array of SnippetDataObjects
  ```json
  [
    {
      "snippet_id": "string",
      "topic": "string",
      "title": "string",
      "summary": "string",
      "audio_url": "string" // URL to a short audio preview
    }
  ]
  ```

## Error Responses
- Status Code: 400 Bad Request - If query parameters are invalid.
- Status Code: 500 Internal Server Error - If there's an issue fetching snippets.
