# Podcast Script Weaver Agent (PSWA)

## Purpose

The Podcast Script Weaver Agent (PSWA) is a specialized microservice within the Aethercast system. Its primary role is to take raw textual content (harvested by WCHA) and a topic, and generate a well-structured podcast script. It achieves this by calling the **AIMS (AI Model Service)**, which handles the direct interaction with a Large Language Model (LLM). PSWA then parses the AIMS service's response into a structured JSON format suitable for downstream processing (e.g., by the Voice Forge Agent).

Key Responsibilities:

1.  **Input Processing:** Receives textual `content` and a `topic` string from the Central Podcast Orchestrator Agent (CPOA).
2.  **Prompt Engineering:**
    *   Uses configured system messages and user prompt templates (which include placeholders for `topic` and `content`) to create a detailed prompt. This prompt is then sent to the AIMS service.
    *   The prompt guides the LLM (via AIMS) to generate a script with specific structural cues or in a direct JSON format.
3.  **AIMS Service Interaction:**
    *   Constructs a request payload for the AIMS `/v1/generate` endpoint, including the engineered prompt, desired model (e.g., `PSWA_LLM_MODEL`), temperature, max tokens, and whether JSON output (`response_format: "json_object"`) is requested (based on `PSWA_LLM_JSON_MODE`).
    *   Calls the configured `AIMS_SERVICE_URL`.
    *   Handles HTTP errors and error responses from the AIMS service.
4.  **Script Parsing & Structuring:**
    *   Receives a JSON response from AIMS which contains the LLM's generated text (within `choices[0].text`), the model used, and usage statistics.
    *   If JSON output was requested from AIMS and successfully received (as a string within `choices[0].text`), it's parsed directly.
    *   If JSON output fails or was not requested, PSWA falls back to parsing the text-based response from AIMS using predefined tags (e.g., `[TITLE]`, `[INTRO]`).
    *   Identifies and extracts sections based on the chosen parsing method.
    *   Constructs a structured JSON object representing the podcast script, including a `script_id`, `topic`, `title`, the `full_raw_script` (from AIMS's `text` field), a list of `segments` (each with `segment_title` and `content`), and the `llm_model_used` (as reported by AIMS).
5.  **Output:** Returns the structured script JSON object to the CPOA. This object includes a `source` field indicating if the script was from `"generation_via_aims"` or `"cache"`.
6.  **Script Caching (Optional):**
    *   If enabled via configuration, PSWA calculates a hash based on the input `topic` and `content`.
    *   It checks a shared database table (`generated_scripts`) for a recent, matching script.
    *   If a fresh cached script is found, it's returned, bypassing the AIMS call.
    *   Newly generated scripts (received from AIMS) are saved to this cache if caching is enabled.

## Configuration

PSWA is configured via environment variables, typically managed in a `.env` file within the `aethercast/pswa/` directory. Create one by copying the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file. The following variables are used:

-   `AIMS_SERVICE_URL`: **Required.** The URL for the AIMS (AI Model Service) endpoint for text generation.
    -   *Example:* `http://aims_service:8000/v1/generate`
-   `AIMS_REQUEST_TIMEOUT_SECONDS`: Timeout in seconds for requests to the AIMS service.
    -   *Default:* `180`
-   `PSWA_LLM_MODEL`: The LLM model ID to *request* from AIMS. AIMS will ultimately decide which model it uses if this is not available or overridden.
    -   *Default:* `gpt-3.5-turbo-0125`
-   `PSWA_LLM_TEMPERATURE`: Temperature setting for the LLM response (passed to AIMS).
    -   *Default:* `0.7`
-   `PSWA_LLM_MAX_TOKENS`: Maximum number of tokens to generate in the LLM response (passed to AIMS).
    -   *Default:* `1500`
-   `PSWA_LLM_JSON_MODE`: Set to `true` to request JSON output from AIMS (which then requests it from the underlying LLM, if supported). If `false`, or if the model doesn't support the JSON mode flag, PSWA will rely on tag-based parsing from the text AIMS returns. The system and user prompts should be adjusted accordingly based on this mode.
    -   *Default:* `true`
-   `PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE`: The system message to set the context for the LLM (sent as part of the prompt to AIMS). The default example is designed for JSON output mode.
    -   *Default:* (A multi-line JSON-specific system prompt, see `.env.example`)
-   `PSWA_DEFAULT_PROMPT_USER_TEMPLATE`: The template for the user message sent to the LLM (via AIMS). It uses `{topic}` and `{content}` placeholders. The default template guides the LLM to produce specific tags or a JSON structure.
    -   *Default:* (A multi-line template string, see `.env.example` for the full content)
-   `PSWA_HOST`: Host for the Flask development server.
    -   *Default:* `0.0.0.0`
-   `PSWA_PORT`: Port for the Flask development server.
    -   *Default:* `5004`
-   `PSWA_DEBUG_MODE` / `FLASK_DEBUG`: Enables/disables Flask debug mode.
    -   *Default:* `True`
-   `SHARED_DATABASE_PATH`: Path to the shared SQLite database (e.g., `/app/database/aethercast_podcasts.db` in Docker). **Required** if script caching (`PSWA_SCRIPT_CACHE_ENABLED`) is enabled.
    -   *Default (in code):* `/app/database/aethercast_podcasts.db`
-   `PSWA_SCRIPT_CACHE_ENABLED`: Set to `true` to enable script caching, `false` to disable.
    -   *Default (in code):* `true`
-   `PSWA_SCRIPT_CACHE_MAX_AGE_HOURS`: Maximum age (in hours) for a cached script to be considered fresh and usable.
    -   *Default (in code):* `720` (30 days)
-   `PSWA_TEST_MODE_ENABLED`: Set to `true` to enable a simplified test mode that bypasses AIMS calls and returns predefined script data. Useful for integration testing of downstream services without actual LLM costs or variability.
    -   *Default (in code):* `false`

## Testing

When `PSWA_TEST_MODE_ENABLED` is set to `true`, the `/weave_script` endpoint behaves differently:
- It **does not** call the AIMS service.
- It returns predefined, structured script data based on an optional `X-Test-Scenario` HTTP header provided in the request (same scenarios as before: `default`, `insufficient_content`, `empty_segments`).
- The `source` field in the returned JSON will indicate the test scenario used (e.g., `"source": "test_mode_scenario_default"`).
- The `llm_model_used` will be `"test-mode-model"`.

This test mode is intended for integration tests to verify how CPOA and other downstream services handle different types of script outputs or error conditions from PSWA.

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `requests` (for AIMS calls), and `python-dotenv`. The `openai` library is no longer a direct dependency of PSWA.

## Running the Service

1.  Ensure environment variables are set, especially `AIMS_SERVICE_URL`.
2.  Run the Flask development server:
    ```bash
    python aethercast/pswa/main.py
    ```
    This will start the service, typically on `http://0.0.0.0:5004`.

Alternatively, using the `flask` command:
```bash
export FLASK_APP=aethercast/pswa/main.py
export FLASK_DEBUG=1 # Optional
flask run --host=0.0.0.0 --port=5004
```

## API Endpoints

### Weave Script

-   **HTTP Method:** `POST`
-   **URL Path:** `/weave_script`
-   **Description:** Receives content and a topic, then generates a structured podcast script by calling the AIMS service.
-   **Request Payload Example (JSON):**
    ```json
    {
        "content": "Detailed textual content harvested from WCHA...",
        "topic": "The Future of Artificial Intelligence"
    }
    ```
-   **Success Response (200 OK) Example (JSON - Structured Script):**
    ```json
    {
        "script_id": "pswa_script_abcdef123456",
        "topic": "The Future of Artificial Intelligence",
        "title": "AI: Shaping Tomorrow",
        "full_raw_script": "...", // Text content from AIMS (which was from the LLM)
        "segments": [
            {"segment_title": "INTRO", "content": "Welcome to our podcast on AI..."},
            {"segment_title": "The Current State", "content": "Currently, AI is impacting various sectors..."},
            {"segment_title": "OUTRO", "content": "Join us next time as we delve deeper..."}
        ],
        "llm_model_used": "aims-model-gpt-3.5-turbo", // Model reported by AIMS
        "source": "generation_via_aims" // or "cache"
    }
    ```
-   **Error Response Examples (JSON):**
    -   **400 Bad Request (Invalid Input):**
        - Payload not JSON or empty:
          ```json
          {
              "error_code": "PSWA_MALFORMED_JSON", # or PSWA_INVALID_PAYLOAD
              "message": "Malformed JSON payload.", # or "Invalid or empty JSON payload."
              "details": "..."
          }
          ```
        - Missing or invalid `content` or `topic`:
          ```json
          {
              "error_code": "PSWA_INVALID_CONTENT", # or PSWA_INVALID_TOPIC, PSWA_CONTENT_TOO_LONG
              "message": "Validation failed: 'content' must be a non-empty string.",
              "details": "'content' must be a non-empty string."
          }
          ```
    -   **400 Bad Request (Insufficient Content Indicated by LLM via AIMS):**
        ```json
        {
            "error_code": "PSWA_INSUFFICIENT_CONTENT",
            "message": "Content provided was insufficient for script generation (reported by LLM).",
            "details": "The LLM (via AIMS) indicated content was insufficient for topic: ..."
        }
        ```
    -   **50X Errors (AIMS Service Error or Parsing Failure):**
        - If AIMS call fails (e.g., timeout, AIMS returns HTTP error, AIMS response unparsable):
        ```json
        {
            "error_code": "PSWA_AIMS_REQUEST_ERROR", // or PSWA_AIMS_TIMEOUT, PSWA_AIMS_HTTP_ERROR, PSWA_AIMS_BAD_RESPONSE
            "message": "Failed to communicate with AIMS service.", // or specific error
            "details": "..." // Further details from the error
        }
        ```
        - If PSWA fails to parse AIMS's successful response (e.g., AIMS `text` field doesn't match expected script structure):
        ```json
        {
            "error_code": "PSWA_SCRIPT_PARSING_FAILURE",
            "message": "Failed to parse essential script structure from AIMS output.",
            "details": "The AIMS output did not conform to the expected script structure.",
            "raw_output_preview": "..."
        }
        ```
    -   **500/503 Internal Server Error (Configuration Issues):**
        ```json
        {
            "error_code": "PSWA_CONFIG_ERROR_AIMS_URL",
            "message": "AIMS Service URL is not configured for PSWA."
        }
        ```

[end of aethercast/pswa/README.md]
