# Podcast Script Weaver Agent (PSWA)

## Purpose

The Podcast Script Weaver Agent (PSWA) is a specialized microservice within the Aethercast system. Its primary role is to take raw textual content (harvested by WCHA) and a topic, and generate a well-structured podcast script. It leverages a Large Language Model (LLM) for this generation and then parses the LLM's output into a structured JSON format suitable for downstream processing (e.g., by the Voice Forge Agent).

Key Responsibilities:

1.  **Input Processing:** Receives textual `content` and a `topic` string from the Central Podcast Orchestrator Agent (CPOA).
2.  **Prompt Engineering:**
    *   Uses configured system messages and user prompt templates (which include placeholders for `topic` and `content`) to create a detailed prompt for the LLM.
    *   The prompt guides the LLM to generate a script with specific structural cues (e.g., `[TITLE]`, `[INTRO]`, `[SEGMENT_X_TITLE]`, `[SEGMENT_X_CONTENT]`, `[OUTRO]`) and to indicate errors like insufficient content.
3.  **LLM Interaction:**
    *   Calls an OpenAI-compatible LLM service using the configured API key, model, temperature, and max tokens.
    *   When `PSWA_LLM_JSON_MODE` is enabled and a compatible model is used, PSWA instructs the LLM to return its output directly as a JSON string.
    *   Handles API errors from the LLM service.
4.  **Script Parsing & Structuring:**
    *   If JSON output is requested and successfully received, it's parsed directly.
    *   If JSON output fails or is disabled, PSWA falls back to parsing a text-based response from the LLM using predefined tags (e.g., `[TITLE]`, `[INTRO]`).
    *   Identifies and extracts sections based on the chosen parsing method.
    *   Constructs a structured JSON object representing the podcast script, including a `script_id`, `topic`, `title`, the `full_raw_script` from the LLM, a list of `segments` (each with `segment_title` and `content`), and the `llm_model_used`.
5.  **Output:** Returns the structured script JSON object to the CPOA.

## Configuration

PSWA is configured via environment variables, typically managed in a `.env` file within the `aethercast/pswa/` directory. Create one by copying the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file. The following variables are used:

-   `OPENAI_API_KEY`: Your API key for the OpenAI service (or compatible LLM provider). This is **required** for the service to function.
    -   *Example:* `your_openai_api_key_here`
-   `PSWA_LLM_MODEL`: The LLM model ID to use for script generation.
    -   *Default:* `gpt-3.5-turbo`
-   `PSWA_LLM_TEMPERATURE`: Temperature setting for the LLM response (controls creativity vs. determinism).
    -   *Default:* `0.7`
-   `PSWA_LLM_MAX_TOKENS`: Maximum number of tokens to generate in the LLM response.
    -   *Default:* `1500`
-   `PSWA_LLM_JSON_MODE`: Set to `true` to explicitly request JSON output from compatible LLMs. If `false`, or if the model doesn't support the JSON mode flag, PSWA will rely on tag-based parsing from the LLM's text response. The system and user prompts should be adjusted accordingly based on this mode.
    -   *Default:* `true` (as per current `.env.example`)
-   `PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE`: The system message to set the context for the LLM. The default example is designed for JSON output mode.
    -   *Default:* `"You are a podcast scriptwriter tasked with creating well-structured podcast scripts."` (Note: The actual default in `.env.example` is more JSON specific and should be referenced by users).
-   `PSWA_DEFAULT_PROMPT_USER_TEMPLATE`: The template for the user message sent to the LLM. It uses `{topic}` and `{content}` placeholders. The default template guides the LLM to produce specific tags like `[TITLE]`, `[INTRO]`, etc. The default example is designed for JSON output mode.
    -   *Default:* (A multi-line template string, see `.env.example` for the full content, which is JSON specific)
-   `PSWA_HOST`: Host for the Flask development server.
    -   *Default:* `0.0.0.0`
-   `PSWA_PORT`: Port for the Flask development server.
    -   *Default:* `5004`
-   `PSWA_DEBUG`: Enables/disables Flask debug mode.
    -   *Default:* `True`

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `openai`, and `python-dotenv`.

## Running the Service

1.  Ensure environment variables are set, especially `OPENAI_API_KEY`.
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
-   **Description:** Receives content and a topic, then generates a structured podcast script using an LLM.
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
        "full_raw_script": "[TITLE]AI: Shaping Tomorrow\n[INTRO]Welcome to our podcast on AI...\n[SEGMENT_1_TITLE]The Current State\n[SEGMENT_1_CONTENT]Currently, AI is impacting various sectors...\n[OUTRO]Join us next time as we delve deeper...",
        "segments": [
            {"segment_title": "INTRO", "content": "Welcome to our podcast on AI..."},
            {"segment_title": "The Current State", "content": "Currently, AI is impacting various sectors..."},
            {"segment_title": "OUTRO", "content": "Join us next time as we delve deeper..."}
        ],
        "llm_model_used": "gpt-3.5-turbo"
    }
    ```
-   **Error Response Examples (JSON):**
    -   **400 Bad Request (Missing Parameters):**
        ```json
        {
            "error": "Missing required parameters: content, topic"
        }
        ```
    -   **400 Bad Request (Insufficient Content Indicated by LLM):**
        If the LLM (in JSON mode) returns the specific error JSON.
        ```json
        {
            "error": "PSWA_INSUFFICIENT_CONTENT_FROM_LLM",
            "message": "LLM indicated content was insufficient for topic: The Future of Artificial Intelligence",
            "llm_response": {
                "error": "Insufficient content",
                "message": "The provided content was not sufficient for topic: The Future of Artificial Intelligence"
            }
        }
        ```
        If not in JSON mode, a tag-based error like `[ERROR] Insufficient content...` would be parsed and result in a structured error segment.
    -   **500 Internal Server Error (LLM API Error):**
        ```json
        {
            "error": "PSWA_OPENAI_API_ERROR",
            "message": "OpenAI API Error: APIError - Test API Error"
        }
        ```
    -   **500 Internal Server Error (Script Parsing Failure):**
        If JSON mode is enabled, this error occurs if the LLM output is not valid JSON *and* the subsequent fallback to tag-based parsing also fails to find essential script structure. If JSON mode is disabled, it means tag-based parsing failed.
        ```json
        {
            "error": "PSWA_SCRIPT_PARSING_FAILURE",
            "message": "Failed to parse LLM output as JSON and also failed tag-based fallback.",
            "raw_output_preview": "..." // Preview of the raw LLM output
        }
        ```
    -   **500 Internal Server Error (Configuration/Import Issues):**
        ```json
        {
            "error": "PSWA_CONFIG_ERROR_API_KEY", // or PSWA_IMPORT_ERROR
            "message": "Error: OPENAI_API_KEY is not configured."
        }
        ```
