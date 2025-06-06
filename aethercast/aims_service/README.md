# AIMS (AI Model Service) - Google Vertex AI Edition

## Purpose

The AIMS (AI Model Service) acts as a centralized gateway for interacting with Large Language Models (LLMs). This version of AIMS utilizes **Google Cloud Vertex AI** (specifically models like Gemini) to provide generative AI capabilities to other services within the Aethercast system (e.g., PSWA, SCA). It abstracts the direct LLM calls, offering a single point of configuration and a consistent API endpoint.

## API Endpoints

### Generate Text

-   **HTTP Method:** `POST`
-   **URL Path:** `/v1/generate`
-   **Description:** Receives a prompt and other parameters, calls the configured Google Cloud Vertex AI LLM (e.g., a Gemini model), and returns the generated text along with metadata.
-   **Request Payload (JSON):**
    *   `prompt` (string, required): The textual prompt for the LLM.
    *   `model_id_override` (string, optional): Specify a Google LLM model ID (e.g., "gemini-1.5-pro-preview-0409") to use instead of the service's default. Also accepts `model`.
    *   `max_tokens` (integer, optional): Maximum tokens to generate. Defaults to a model-specific value or a service default (e.g., 2048 for Gemini Pro).
    *   `temperature` (float, optional): Sampling temperature. Defaults to a service/model default (e.g., 0.7).
    *   `response_format` (object, optional): Specify desired output format. For JSON output from compatible models, use `{"type": "json_object"}`.
    *   *(For more details on the API contract, refer to `aethercast/aims/llm_api_placeholder.md` which defines the intended stable interface AIMS provides.)*
-   **Success Response (JSON):**
    *   `request_id` (string): Unique ID for the request.
    *   `model_id` (string): The actual Google LLM model ID used for generation (as reported by Vertex AI or the configured model).
    *   `choices` (array of objects):
        *   `text` (string): The LLM-generated text.
        *   `finish_reason` (string): Reason generation stopped (e.g., "STOP", "MAX_TOKENS", "SAFETY").
    *   `usage` (object):
        *   `prompt_tokens` (integer): Tokens in the input prompt.
        *   `completion_tokens` (integer): Tokens in the generated output.
        *   `total_tokens` (integer): Total tokens processed.
    *   *(Refer to `aethercast/aims/llm_api_placeholder.md` for the detailed response structure.)*
-   **Error Responses (JSON):**
    *   Structured JSON errors are returned for issues like configuration problems, invalid requests, or errors from the Vertex AI service. Example: `{"request_id": "...", "error": {"type": "google_vertex_ai_error", "message": "Details..."}}`.
    *   Specific error types exist for `configuration_error`, `invalid_request_error`, `generation_blocked_safety`, and various `google_vertex_ai_*` errors.

## Configuration

Configuration is managed via environment variables, typically set in an `.env` file created from `.env.example` located in the `aethercast/aims_service/` directory.

Key environment variables:

-   `AIMS_HOST`: Host address for the Flask server to bind to.
    -   *Default:* `0.0.0.0`
-   `AIMS_PORT`: Port on which the AIMS service will listen.
    -   *Default:* `8000`
-   `FLASK_DEBUG`: Enables or disables Flask's debug mode.
    -   *Default:* `True` (for development)
-   `GOOGLE_APPLICATION_CREDENTIALS`: **Required.** Path to your Google Cloud service account key JSON file. This file grants the service permission to access Vertex AI.
    -   *Example when running locally:* `GOOGLE_APPLICATION_CREDENTIALS=./your-gcp-service-account-key.json` (place the key file in the `aims_service` directory).
    -   *Example when running in Docker (as configured in `docker-compose.yml`):* `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json` (the local key file is mounted to this path in the container).
-   `GCP_PROJECT_ID`: **Required.** Your Google Cloud Project ID where Vertex AI is enabled.
    -   *Example:* `GCP_PROJECT_ID=my-gcp-project-123`
-   `GCP_LOCATION`: **Required.** The Google Cloud location/region for Vertex AI operations (e.g., where your models are or where you want to run jobs).
    -   *Example:* `GCP_LOCATION=us-central1`
-   `AIMS_GOOGLE_LLM_MODEL_ID`: The default Google LLM model ID to be used if not specified in the request.
    -   *Default:* `gemini-1.0-pro`
    -   *Examples:* `gemini-1.5-pro-preview-0409`, `text-bison@001` (though Gemini is preferred for chat-like completions)

## Dependencies

Service dependencies are listed in `requirements.txt`:
-   `Flask`: Web framework.
-   `python-dotenv`: For loading environment variables from `.env` files.
-   `google-cloud-aiplatform`: The Google Cloud Vertex AI SDK.

Install dependencies using:
```bash
pip install -r requirements.txt
```

## Running Standalone

To run the AIMS service directly for development or testing:

1.  Ensure all required environment variables are set (e.g., in an `.env` file in this directory). Specifically, `GOOGLE_APPLICATION_CREDENTIALS`, `GCP_PROJECT_ID`, and `GCP_LOCATION` must be correctly configured.
2.  Execute the main script:
    ```bash
    python aethercast/aims_service/main.py
    ```
    The service will typically start on `http://0.0.0.0:8000` (or as configured by `AIMS_HOST`/`AIMS_PORT`).

## Docker

The AIMS service is designed to be run as a Docker container and is included in the project's `docker-compose.yml` file.

-   **Building the Image:** If changes are made, you might need to rebuild the service's image: `docker-compose build aims_service`.
-   **Credentials in Docker:** The `docker-compose.yml` is configured to mount a local Google Cloud credentials file into the container. You must:
    1.  Place your downloaded GCP service account key JSON file into the `./aethercast/aims_service/` directory (e.g., name it `gcp-credentials.json`).
    2.  In your `aethercast/aims_service/.env` file, set `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json`. This path corresponds to where the file is mounted inside the container.
-   **Running with Docker Compose:**
    ```bash
    docker-compose up -d aims_service
    ```
    Or, to run all services:
    ```bash
    docker-compose up -d
    ```

The service will then be accessible to other Dockerized services (like PSWA and SCA) via its service name and internal port (e.g., `http://aims_service:8000`).
