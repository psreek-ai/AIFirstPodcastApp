# Image Generation Agent (IGA) - Vertex AI Imagen Edition

## Purpose

The Image Generation Agent (IGA) is responsible for generating images based on text prompts using **Google Cloud Vertex AI's Imagen models**. It is called by the Central Podcast Orchestrator Agent (CPOA) when a `cover_art_prompt` is available from a generated snippet, to associate an image with that snippet. The generated image is saved to a shared volume, and its path is returned.

## API Contract

### Generate Image

-   **Endpoint:** `POST /generate_image`
-   **Description:** Accepts a text prompt, generates an image using Vertex AI Imagen, saves it to a shared directory, and returns the filepath.
-   **Request Body (JSON):**
    ```json
    {
        "prompt": "A detailed description of the desired image"
    }
    ```
    -   `prompt` (string, required): The text prompt for image generation. Must be a non-empty string.

-   **Success Response (200 OK) (JSON):**
    ```json
    {
        "image_url": "/shared_audio/iga_images/iga_req_xxxx_yyyy.png", // Path within the shared volume
        "prompt_used": "The prompt that was processed",
        "model_version": "vertex-ai-imagegeneration@006" // Example, reflects actual model used
    }
    ```
    -   `image_url` (string): The filepath of the generated image within the container's shared volume. This path is intended for inter-service access.
    -   `prompt_used` (string): The prompt string that was received and processed.
    -   `model_version` (string): An identifier for the Vertex AI model used.

-   **Error Responses (JSON):**
    -   **400 Bad Request**:
        -   If the `prompt` field is missing or empty:
            ```json
            {
                "error_code": "IGA_BAD_REQUEST_PROMPT_MISSING",
                "message": "Prompt is required for image generation.",
                "details": "Missing or empty 'prompt' in request body."
            }
            ```
        -   If Vertex AI blocks the prompt due to safety filters:
            ```json
            {
                "error_code": "IGA_VERTEXAI_PROMPT_BLOCKED_SAFETY",
                "message": "Prompt blocked by safety filters.",
                "details": "<Vertex AI error details>"
            }
            ```
        -   For other invalid arguments to Vertex AI:
            ```json
            {
                "error_code": "IGA_VERTEXAI_INVALID_ARGUMENT",
                "message": "Invalid argument provided to Vertex AI.",
                "details": "<Vertex AI error details>"
            }
            ```
    -   **403 Forbidden**:
        -   If Vertex AI denies permission (e.g., API not enabled, billing issues, credentials):
            ```json
            {
                "error_code": "IGA_VERTEXAI_PERMISSION_DENIED",
                "message": "Permission denied for Vertex AI operation.",
                "details": "<Vertex AI error details>"
            }
            ```
    -   **429 Too Many Requests**:
        -   If Vertex AI rate limits or quotas are exceeded:
            ```json
            {
                "error_code": "IGA_VERTEXAI_RESOURCE_EXHAUSTED",
                "message": "Vertex AI resource exhausted (e.g., quota exceeded).",
                "details": "<Vertex AI error details>"
            }
            ```
    -   **500 Internal Server Error**:
        -   For general Vertex AI API errors:
            ```json
            {
                "error_code": "IGA_VERTEXAI_API_ERROR",
                "message": "An error occurred with the Vertex AI service.",
                "details": "<Vertex AI error details>"
            }
            ```
        -   If the image object from Vertex AI is missing image bytes:
            ```json
            {
                "error_code": "IGA_VERTEXAI_EMPTY_IMAGE_BYTES",
                "message": "Image generation produced an empty image.",
                "details": "The image data from Vertex AI was empty or inaccessible."
            }
            ```
        -   If IGA fails to save the generated image file:
            ```json
            {
                "error_code": "IGA_FILE_SAVE_ERROR",
                "message": "Could not save generated image.",
                "details": "<OS I/O error details>"
            }
            ```
        -   For any other unexpected internal errors:
            ```json
            {
                "error_code": "IGA_INTERNAL_SERVER_ERROR",
                "message": "IGA encountered an unexpected error.",
                "details": "<specific error string from exception>"
            }
            ```
    -   **503 Service Unavailable**:
        -   If Vertex AI service is unavailable:
            ```json
            {
                "error_code": "IGA_VERTEXAI_API_ERROR", /* Might be a generic GoogleAPIError mapped to 503 by IGA */
                "message": "An error occurred with the Vertex AI service.",
                "details": "Service Unavailable"
            }
            ```

## Configuration

IGA is configured via environment variables. If an `.env` file is present in the `aethercast/iga/` directory when `main.py` is run, it will be loaded.

Key environment variables:

-   `IGA_HOST`: Host address for the Flask server. Defaults to `0.0.0.0`.
-   `IGA_PORT`: Port for the IGA service. Defaults to `5007`.
-   `IGA_DEBUG_MODE`: Enables or disables Flask's debug mode (e.g., "True" or "False"). Defaults to `True`.
-   `GOOGLE_APPLICATION_CREDENTIALS`: (Optional but Recommended for explicit auth) Path to your Google Cloud service account key JSON file. If not set, Vertex AI SDK attempts to use Application Default Credentials (ADC).
-   `IGA_VERTEXAI_PROJECT_ID`: **Required.** Your Google Cloud Project ID where Vertex AI is enabled. Can fallback to `GCP_PROJECT_ID` if that env var is set.
-   `IGA_VERTEXAI_LOCATION`: **Required.** The Google Cloud location/region for Vertex AI operations (e.g., `us-central1`). Can fallback to `GCP_LOCATION` if that env var is set.
-   `IGA_VERTEXAI_IMAGE_MODEL_ID`: The Vertex AI Imagen model ID to use.
    -   *Default:* `imagegeneration@006`
-   `IGA_GENERATED_IMAGE_DIR`: **Required.** Directory path *inside the container* where generated images will be saved. This path must be on a shared volume accessible by other services (like CPOA or API Gateway if they need direct access, though typically only the path is needed by CPOA).
    -   *Default:* `/shared_audio/iga_images` (aligns with typical shared volume structures like `aethercast_audio_data:/shared_audio`)
-   `IGA_DEFAULT_ASPECT_RATIO`: Default aspect ratio for generated images.
    -   *Default:* `1:1`
-   `IGA_ADD_WATERMARK`: Whether to add a Google watermark to generated images (boolean).
    -   *Default:* `True`

## Dependencies

Service dependencies are listed in `requirements.txt`:
-   `Flask>=2.0`
-   `python-dotenv>=0.15`
-   `google-cloud-aiplatform>=1.0.0`

Install dependencies using:
```bash
pip install -r requirements.txt
```

## Running Standalone

To run the IGA service directly for development or testing:

1.  Ensure all required environment variables are set (e.g., in an `.env` file in this directory).
    *   `IGA_VERTEXAI_PROJECT_ID` and `IGA_VERTEXAI_LOCATION` are critical.
    *   Ensure Google Cloud authentication is configured (either via `GOOGLE_APPLICATION_CREDENTIALS` pointing to a key file, or by having Application Default Credentials set up in your environment, e.g., by running `gcloud auth application-default login`).
    *   The `IGA_GENERATED_IMAGE_DIR` must be a writable path on your local system if running outside Docker with that path. For consistency with Docker, you might map a local path to `/shared_audio/iga_images`.
2.  Create the directory specified by `IGA_GENERATED_IMAGE_DIR` if it doesn't exist (e.g., `mkdir -p ./shared_data_mount/iga_images` and set `IGA_GENERATED_IMAGE_DIR=./shared_data_mount/iga_images`).
3.  Execute the main script:
    ```bash
    python aethercast/iga/main.py
    ```
    The service will typically start on `http://0.0.0.0:5007` (or as configured).

## Docker

The IGA service is designed to be run as a Docker container and is included in the project's `docker-compose.yml` file.

-   **Building the Image:** If changes are made, you might need to rebuild the service's image: `docker-compose build iga_service`.
-   **Credentials in Docker:**
    *   The recommended way for services running in Google Cloud (like Cloud Run, GKE) is to use service account identity.
    *   For local Docker development, you can mount your GCP service account key JSON file into the container. Ensure your `.env` file for IGA sets `GOOGLE_APPLICATION_CREDENTIALS` to the path where this key will be mounted inside the container (e.g., `/app/gcp-credentials.json`). The `docker-compose.yml` should handle this mounting.
-   **Shared Volume for Images:** The `docker-compose.yml` file should define a named volume (e.g., `aethercast_audio_data` or a new `aethercast_image_data`) and mount it to a common path like `/shared_data` or `/shared_audio` inside the `iga_service` container. The `IGA_GENERATED_IMAGE_DIR` (e.g., `/shared_audio/iga_images`) will then reside within this shared volume, allowing other services to potentially access these files if needed (though typically CPOA only needs the path).
-   **Running with Docker Compose:**
    ```bash
    docker-compose up -d iga_service
    ```
    Or, to run all services:
    ```bash
    docker-compose up -d
    ```

The service will then be accessible to other Dockerized services (like CPOA) via its service name and internal port (e.g., `http://iga_service:5007`).

[end of aethercast/iga/README.md]
