# Snippet Craft Agent (SCA)

## Purpose

The Snippet Craft Agent (SCA) is a specialized microservice within the Aethercast system. Its primary function is to generate short, engaging podcast snippets based on topic information provided by the Central Podcast Orchestrator Agent (CPOA). These snippets typically include a title and a brief text content.

Key Responsibilities:

1.  **Input Processing:** Receives topic information (e.g., a `topic_id`, a title suggestion, summary, keywords) from the CPOA.
2.  **LLM Interaction:**
    *   Formulates a prompt for a Large Language Model (LLM) service (e.g., OpenAI).
    *   Calls the configured LLM service to generate a snippet title and content.
    *   Handles responses and errors from the LLM service.
3.  **Snippet Structuring:** Assembles the generated title, text, and other relevant metadata (like `topic_id`, a new `snippet_id`, timestamps) into a structured `SnippetDataObject`.
4.  **Output:** Returns the `SnippetDataObject` to the CPOA.

## Configuration

SCA is configured via environment variables, typically managed in a `.env` file within the `aethercast/sca/` directory. To create one, copy the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file. The following variables are used:

-   `SCA_LLM_PROVIDER`: Specifies the LLM provider.
    -   *Default:* `openai`
    -   *Note:* Currently, only `openai` is fully implemented for real LLM calls.
-   `SCA_LLM_API_KEY`: API key for the chosen LLM service. Required if `USE_REAL_LLM_SERVICE=true`.
    -   *Example:* `your_llm_api_key_here`
-   `SCA_LLM_BASE_URL`: Base URL for the LLM service API. Required if `USE_REAL_LLM_SERVICE=true`.
    -   *Example (OpenAI):* `https://api.openai.com/v1`
-   `SCA_LLM_MODEL_ID`: Specific model ID for snippet generation. Required if `USE_REAL_LLM_SERVICE=true`.
    -   *Example (OpenAI):* `gpt-3.5-turbo`
-   `SCA_LLM_MAX_TOKENS_SNIPPET`: Maximum tokens for the generated snippet.
    -   *Default:* `150`
-   `SCA_LLM_TEMPERATURE_SNIPPET`: LLM sampling temperature.
    -   *Default:* `0.7`
-   `SCA_LLM_REQUEST_TIMEOUT_SECONDS`: Timeout for LLM requests.
    -   *Default:* `30`
-   `USE_REAL_LLM_SERVICE`: Set to `true` to use a real LLM; `false` for simulated responses.
    -   *Default:* `false`
-   `# AIMS_LLM_PLACEHOLDER_URL`: (Commented out) URL for a placeholder LLM if the simulation logic were to make a live call (currently internal simulation).
    -   *Example:* `http://localhost:8000/v1/generate`

**Flask Application Parameters:**
The following are standard Flask environment variables used by `main.py` if you run it directly (though typically SCA is a service called by CPOA):
-   `FLASK_APP=aethercast/sca/main.py` (standard way to specify app for flask command)
-   `FLASK_RUN_HOST`: Host for the Flask development server.
    -   *Default in `main.py` if run directly:* `0.0.0.0`
-   `FLASK_RUN_PORT`: Port for the Flask development server.
    -   *Default in `main.py` if run directly:* `5002`
-   `FLASK_DEBUG`: To run Flask in debug mode.
    -   *Default in `main.py` if run directly:* `True` (enables debug)

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `requests`, and `python-dotenv`.

## Running the Service (Standalone)

While SCA is typically called by CPOA, its Flask application can be run as a standalone service for development or testing:

1.  Ensure environment variables are set (e.g., in a `.env` file or system environment).
2.  Run the Flask development server:
    ```bash
    python aethercast/sca/main.py
    ```
    This will start the service, typically on `http://0.0.0.0:5002` by default.

Alternatively, using the `flask` command:
```bash
export FLASK_APP=aethercast/sca/main.py
export FLASK_DEBUG=1 # Optional
flask run --host=0.0.0.0 --port=5002
```

## API Endpoints

### Craft Snippet

-   **HTTP Method:** `POST`
-   **URL Path:** `/craft_snippet`
-   **Description:** Receives topic information and generates a podcast snippet.
-   **Request Payload Example (JSON):**
    ```json
    {
        "topic_id": "topic_12345",
        "content_brief": "The Future of Renewable Energy",
        "topic_info": {
            "title_suggestion": "The Future of Renewable Energy",
            "summary": "Exploring advancements in solar, wind, and geothermal power.",
            "keywords": ["solar", "wind", "geothermal", "sustainability"]
        }
    }
    ```
-   **Success Response (200 OK) Example (JSON):**
    ```json
    {
        "snippet_id": "snippet_abcdef123456",
        "topic_id": "topic_12345",
        "title": "Renewable Revolution: Powering Tomorrow",
        "summary": "Recent breakthroughs in solar panel efficiency and wind turbine design are making renewable energy more affordable and accessible than ever before, paving the way for a cleaner future.",
        "text_content": "Recent breakthroughs in solar panel efficiency and wind turbine design are making renewable energy more affordable and accessible than ever before, paving the way for a cleaner future.",
        "audio_url": "https://aethercast.com/placeholder_audio/snippet_abcdef123456.mp3",
        "cover_art_prompt": "Podcast snippet cover art for: Renewable Revolution: Powering Tomorrow",
        "generation_timestamp": "2024-03-15T12:30:00Z",
        "llm_prompt_used": "Generate a short, engaging podcast snippet...",
        "llm_model_used": "gpt-3.5-turbo",
        "original_topic_details_from_tda": {
            "title_suggestion": "The Future of Renewable Energy",
            "summary": "Exploring advancements in solar, wind, and geothermal power.",
            "keywords": ["solar", "wind", "geothermal", "sustainability"]
        }
    }
    ```
-   **Error Response Examples (JSON):**
    -   **400 Bad Request (Invalid Payload):**
        ```json
        {
            "error": "'topic_id' and 'content_brief' are required."
        }
        ```
    -   **500 Internal Server Error (LLM Call Fails after retries):**
        ```json
        {
            "error": "LLM_HTTP_ERROR",
            "details": "HTTP Error 500: Internal Server Error. LLM Service Msg: {...}",
            "status_code": 500
        }
        ```
    -   **500 Internal Server Error (Simulated for testing):**
        ```json
        {
            "error": "Simulated SCA Error",
            "details": "This is a controlled error triggered for testing purposes in SnippetCraftAgent."
        }
        ```
    -   **503 Service Unavailable (If LLM provider not supported):**
        ```json
        {
            "error": "UNSUPPORTED_LLM_PROVIDER",
            "details": "Provider 'some_other_provider' not supported."
        }
        ```
