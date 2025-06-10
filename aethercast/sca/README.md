# Snippet Craft Agent (SCA)

## Purpose

The Snippet Craft Agent (SCA) is a specialized microservice within the Aethercast system. Its primary function is to generate short, engaging podcast snippets based on topic information provided by the Central Podcast Orchestrator Agent (CPOA). These snippets typically include a title and a brief text content. SCA achieves this by calling the **AIMS (AI Model Service)** for LLM-based text generation.

Key Responsibilities:

1.  **Input Processing:** Receives topic information (e.g., a `topic_id`, a title suggestion as `content_brief`, and the full `topic_info` object) from the CPOA.
2.  **Prompt Engineering:** Formulates a detailed prompt for the AIMS service based on the input `content_brief` and other details from `topic_info` (like summary, keywords, source inspiration).
3.  **AIMS Service Interaction:**
    *   Constructs a request payload for the AIMS `/v1/generate` endpoint, including the engineered prompt, desired model (e.g., `SCA_LLM_MODEL_ID`), temperature, and max tokens.
    *   Calls the configured `AIMS_SERVICE_URL`.
    *   Handles HTTP errors and error responses from the AIMS service.
4.  **Snippet Structuring:**
    *   Receives a JSON response from AIMS which contains the LLM's generated text (within `choices[0].text`), the model used, and usage statistics.
    *   Parses the `text` from AIMS (which is expected to have the title on the first line, followed by content) to extract the snippet title and content.
    *   Assembles the generated title, text, and other relevant metadata (like `topic_id`, a new `snippet_id`, timestamps, `llm_model_used` as reported by AIMS) into a structured `SnippetDataObject`.
    *   Generates a basic `cover_art_prompt` based on the snippet title.
5.  **Output:** Returns the `SnippetDataObject` to the CPOA.

## Configuration

SCA is configured via environment variables, typically managed in a `.env` file within the `aethercast/sca/` directory. To create one, copy the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file. The following variables are used:

-   `AIMS_SERVICE_URL`: **Required if `USE_REAL_LLM_SERVICE=true`.** The URL for the AIMS (AI Model Service) endpoint for text generation.
    -   *Example:* `http://aims_service:8000/v1/generate`
-   `AIMS_REQUEST_TIMEOUT_SECONDS`: Timeout in seconds for requests to the AIMS service.
    -   *Default:* `60`
-   `SCA_LLM_MODEL_ID`: The LLM model ID to *request* from AIMS for snippet generation.
    -   *Default:* `gpt-3.5-turbo`
-   `SCA_LLM_MAX_TOKENS_SNIPPET`: Maximum tokens for the generated snippet (passed as a request to AIMS).
    -   *Default:* `150`
-   `SCA_LLM_TEMPERATURE_SNIPPET`: LLM sampling temperature (passed as a request to AIMS).
    -   *Default:* `0.7`
-   `USE_REAL_LLM_SERVICE`: Set to `true` to use a real LLM (via AIMS); `false` for simulated placeholder responses (bypasses AIMS).
    -   *Default:* `false`
    -   *Note on placeholder mode (when `false`):* The placeholder generates snippet titles and content directly based on the `content_brief` and `keywords` from the `topic_info` in the request. This provides a more consistent and cleaner simulated response compared to older parsing-based placeholder behavior.

**Flask Application Parameters:**
-   `SCA_HOST` / `FLASK_RUN_HOST`: Host for the Flask development server.
    -   *Default in `main.py` if run directly:* `0.0.0.0`
-   `SCA_PORT` / `FLASK_RUN_PORT`: Port for the Flask development server.
    -   *Default in `main.py` if run directly:* `5002`
-   `FLASK_DEBUG`: To run Flask in debug mode (standard Flask variable).
    -   *Default in `main.py` if run directly:* `True`

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `requests` (for AIMS calls), and `python-dotenv`.

## Running the Service (Standalone)

While SCA is typically called by CPOA, its Flask application can be run as a standalone service:

1.  Ensure environment variables are set. If `USE_REAL_LLM_SERVICE=true`, ensure `AIMS_SERVICE_URL` and `SCA_LLM_MODEL_ID` are correctly configured.
2.  Run the Flask development server:
    ```bash
    python aethercast/sca/main.py
    ```
    This will start the service, typically on `http://0.0.0.0:5002`.

## API Endpoints

### Craft Snippet

-   **HTTP Method:** `POST`
-   **URL Path:** `/craft_snippet`
-   **Description:** Receives topic information and generates a podcast snippet by calling the AIMS service.
-   **Request Payload Example (JSON):**
    ```json
    {
        "topic_id": "topic_12345",
        "content_brief": "The Future of Renewable Energy", // Used as the main subject for the prompt
        "topic_info": { // Additional context for richer prompts
            "title_suggestion": "The Future of Renewable Energy",
            "summary": "Exploring advancements in solar, wind, and geothermal power.",
            "keywords": ["solar", "wind", "geothermal", "sustainability"],
            "potential_sources": [{"title": "Recent study on solar panel efficiency"}]
        }
        // Optional: "error_trigger": "sca_error" // For testing SCA's internal error handling
    }
    ```
-   **Success Response (200 OK) Example (JSON - SnippetDataObject):**
    ```json
    {
        "snippet_id": "snippet_abcdef123456",
        "topic_id": "topic_12345",
        "title": "Renewable Revolution: Powering Tomorrow", // Generated by LLM via AIMS
        "summary": "Recent breakthroughs in solar panel efficiency...", // Generated by LLM via AIMS
        "text_content": "Recent breakthroughs in solar panel efficiency...", // Same as summary for now
        "audio_url": "https://aethercast.com/placeholder_audio/snippet_abcdef123456.mp3",
        "cover_art_prompt": "Podcast snippet cover art for: Renewable Revolution: Powering Tomorrow",
        "generation_timestamp": "2024-03-15T12:30:00Z",
        "llm_prompt_used": "Generate a short, engaging podcast snippet title and content...", // The prompt SCA sent to AIMS
        "llm_model_used": "aims-model-gpt-3.5-turbo", // Model reported by AIMS
        "original_topic_details_from_tda": { /* ... topic_info from request ... */ }
    }
    ```
-   **Error Response Examples (JSON):**
    -   **400 Bad Request (Invalid Payload or Missing Fields):**
        ```json
        {
            "error_code": "SCA_INVALID_PAYLOAD", // or SCA_MISSING_FIELDS
            "message": "Invalid or missing JSON payload." // or specific missing fields message
        }
        ```
    -   **50X Errors (AIMS Service Error):**
        If the AIMS service call fails (e.g., timeout, AIMS returns an HTTP error, AIMS response is unparsable):
        ```json
        {
            "error_code": "SCA_AIMS_HTTP_ERROR", // or SCA_AIMS_TIMEOUT, SCA_AIMS_BAD_RESPONSE_STRUCTURE etc.
            "message": "AIMS request failed with HTTP error.", // or specific error from AIMS interaction
            "details": "AIMS HTTP Error 500: Internal Server Error. AIMS Service Msg: {...}" // Details from AIMS if available
        }
        ```
    -   **500 Internal Server Error (SCA Internal Simulated Error):**
        If `error_trigger: "sca_error"` is passed in the request for testing.
        ```json
        {
            "error_code": "SCA_SIMULATED_ERROR",
            "message": "A simulated error occurred in SCA."
        }
        ```
    -   **500 Internal Server Error (SCA Configuration Error):**
        If `USE_REAL_LLM_SERVICE` is true but `AIMS_SERVICE_URL` or `SCA_LLM_MODEL_ID` is not set (this would typically prevent startup, but as an error response example).
        ```json
        {
            "error_code": "SCA_AIMS_CONFIG_MISSING",
            "message": "AIMS_SERVICE_URL not configured."
        }
        ```

## Monitoring and Logging

This service outputs logs in a structured JSON format. Key operational metrics, such as request latency, counts, and AIMS (LLM) call performance, are also logged as part of these structured logs.

For details on the general logging format, specific metrics defined for this service, and how to view logs (e.g., using `docker-compose logs sca`), please refer to the main [Logging Guide](../../../docs/operational/Logging_Guide.md) and [Metrics Definition](../../../docs/operational/Metrics_Definition.md) in the project's `docs/operational/` directory.

[end of aethercast/sca/README.md]
