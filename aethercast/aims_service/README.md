# AIMS (AI Model Service) - Google Vertex AI Edition

## Purpose

The AIMS (AI Model Service) acts as a centralized gateway for interacting with Large Language Models (LLMs). This version of AIMS utilizes **Google Cloud Vertex AI** (specifically models like Gemini) to provide generative AI capabilities to other services within the Aethercast system (e.g., PSWA, SCA). It abstracts the direct LLM calls, offering a single point of configuration and a consistent API endpoint.

## API Endpoints

### Generate Text

-   **HTTP Method:** `POST`
-   **URL Path:** `/v1/generate`
-   **Description:** Receives a prompt and other parameters, calls the configured Google Cloud Vertex AI LLM (e.g., a Gemini model), and returns the generated text along with metadata.
-   **Request Payload (JSON):**
    *   `prompt` (string, required): The textual prompt for the LLM. Must be a non-empty string.
    *   `model_id_override` (string, optional): Specify a Google LLM model ID (e.g., "gemini-1.5-pro-preview-0409") to use instead of the service's default. Also accepts `model`. If provided, must be a string.
    *   `max_tokens` (integer, optional): Maximum tokens to generate. Must be a positive integer if provided. Defaults to a model-specific value or a service default (e.g., 2048 for Gemini Pro). Invalid values result in a 400 error.
    *   `temperature` (float, optional): Sampling temperature. Must be a float between 0.0 and 2.0 (inclusive) if provided. Defaults to a service/model default (e.g., 0.7). Invalid values result in a 400 error.
    *   `response_format` (object, optional): Specify desired output format. Must be an object if provided. For JSON output from compatible models, use `{"type": "json_object"}`. The `type` field, if present, must be a string. Invalid structures result in a 400 error.
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
    *   Structured JSON errors are returned for issues like configuration problems, invalid requests, or errors from the Vertex AI service.
    *   **400 Bad Request:** Returned for issues like missing `prompt`, invalid types for parameters (e.g., `max_tokens` not an integer, `temperature` not a float), or values out of allowed range (e.g., `max_tokens` not positive, `temperature` outside 0.0-2.0). The error response will typically look like: `{"request_id": "...", "error": {"type": "invalid_request_error", "message": "Validation failed: <specific_reason>"}}`.
    *   **403 Forbidden:** May be returned by Vertex AI for permission issues.
    *   **429 Too Many Requests:** May be returned by Vertex AI if rate limits are exceeded.
    *   **500 Internal Server Error:** For unexpected errors within AIMS or non-specific errors from Vertex AI.
    *   **503 Service Unavailable:** If Vertex AI is unavailable or if AIMS is not configured correctly.
    *   Specific error types also exist for `configuration_error`, `generation_blocked_safety`, and various `google_vertex_ai_*` errors.

## Configuration

Configuration is managed via environment variables, typically set in an `.env` file created from `.env.example` located in the `aethercast/aims_service/` directory.

Key environment variables:

This service requires Google Cloud Platform credentials and configuration to interact with Vertex AI. Please refer to the main project README's section on **'## GCP Prerequisites and Setup for Local Development'** for comprehensive instructions. This includes setting up your GCP project, enabling necessary APIs (especially Vertex AI API), and configuring a service account. You will need to download the service account's JSON key, name it `gcp-credentials.json`, and place it in the `aethercast/aims_service/` directory. Ensure `GCP_PROJECT_ID` and `GCP_LOCATION` are set in your `common.env` file (or in this service's `.env` file if you need to override). The `GOOGLE_APPLICATION_CREDENTIALS` variable in the `.env` file for this service must be set to `/app/gcp-credentials.json`, which is the path where the key will be mounted inside the Docker container.

-   `AIMS_HOST`: Host address for the Flask server to bind to.
    -   *Default:* `0.0.0.0`
-   `AIMS_PORT`: Port on which the AIMS service will listen.
    -   *Default:* `8000`
-   `FLASK_DEBUG`: Enables or disables Flask's debug mode.
    -   *Default:* `True` (for development)
-   `AIMS_GOOGLE_LLM_MODEL_ID`: The default Google LLM model ID to be used if not specified in the request.
    -   *Default:* `gemini-1.0-pro`
    -   *Examples:* `gemini-1.5-pro-preview-0409`, `text-bison@001` (though Gemini is preferred for chat-like completions)
    Details for `GOOGLE_APPLICATION_CREDENTIALS`, `GCP_PROJECT_ID`, and `GCP_LOCATION` are covered in the paragraph above and the main project README.

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
-   **Credentials in Docker:** The `docker-compose.yml` file is configured to mount your GCP service account key (`gcp-credentials.json` located in `aethercast/aims_service/`) into the container at `/app/gcp-credentials.json`. Ensure your `aethercast/aims_service/.env` file has `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json` set. For detailed steps on creating and placing the `gcp-credentials.json` file, see the main project README's section '## GCP Prerequisites and Setup for Local Development'.
-   **Running with Docker Compose:**
    ```bash
    docker-compose up -d aims_service
    ```
    Or, to run all services:
    ```bash
    docker-compose up -d
    ```

The service will then be accessible to other Dockerized services (like PSWA and SCA) via its service name and internal port (e.g., `http://aims_service:8000`).

## Monitoring and Logging

This service outputs logs in a structured JSON format. Key operational metrics, such as request latency, counts, and Vertex AI call performance, are also logged as part of these structured logs.

For details on the general logging format, specific metrics defined for this service, and how to view logs (e.g., using `docker-compose logs aims_service`), please refer to the main [Logging Guide](../../../docs/operational/Logging_Guide.md) and [Metrics Definition](../../../docs/operational/Metrics_Definition.md) in the project's `docs/operational/` directory.

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
