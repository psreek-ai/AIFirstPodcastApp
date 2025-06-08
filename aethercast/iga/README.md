# Image Generation Agent (IGA) - Placeholder

## Purpose

The Image Generation Agent (IGA) is a placeholder service within the Aethercast system. In a production environment, this service would be responsible for generating actual images based on text prompts using sophisticated AI models (e.g., DALL-E, Stable Diffusion, Midjourney).

For development and testing purposes, this placeholder service simulates the API contract of an IGA and returns predefined or dynamically constructed placeholder image URLs. It does **not** perform any actual AI image generation.

It is called by the Central Podcast Orchestrator Agent (CPOA) when a `cover_art_prompt` is available from a generated snippet, to associate an image URL with that snippet.

## API Contract

### Generate Image

-   **Endpoint:** `POST /generate_image`
-   **Description:** Accepts a text prompt and returns a URL to a placeholder image.
-   **Request Body (JSON):**
    ```json
    {
        "prompt": "A detailed description of the desired image"
    }
    ```
    -   `prompt` (string, required): The text prompt based on which an image would ideally be generated.

-   **Success Response (200 OK) (JSON):**
    ```json
    {
        "image_url": "https://source.unsplash.com/random/400x225/?keyword1,keyword2,theme,podcast,abstract",
        "prompt_used": "The prompt that was processed by the placeholder",
        "model_version": "iga-placeholder-v0.1"
    }
    ```
    -   `image_url` (string): A URL to a placeholder image from Unsplash. The URL is dynamically constructed using keywords from the prompt (first few words), a randomly selected theme (e.g., "tech", "news", "audio"), and default terms like "podcast" and "abstract" to provide some visual variety.
    -   `prompt_used` (string): The prompt string that was received and processed by the placeholder.
    -   `model_version` (string): An identifier for the placeholder model.

-   **Error Responses:**
    -   **`400 Bad Request`**: If the `prompt` field is missing or invalid.
        ```json
        {
            "error_code": "IGA_BAD_REQUEST_PROMPT_MISSING",
            "message": "Prompt is required for image generation.",
            "details": "Missing or empty 'prompt' in request body."
        }
        ```
    -   **`500 Internal Server Error`**: For any unexpected internal errors within the placeholder service.
        ```json
        {
            "error_code": "IGA_INTERNAL_SERVER_ERROR",
            "message": "IGA placeholder encountered an unexpected error.",
            "details": "<specific error string from exception>"
        }
        ```

## Configuration

IGA is configured via environment variables. If an `.env` file is present in the `aethercast/iga/` directory when `main.py` is run, it will be loaded.

Key environment variables:
-   `IGA_HOST`: Host address for the Flask server. Defaults to `0.0.0.0`.
-   `IGA_PORT`: Port for the IGA service. Defaults to `5007`.
-   `IGA_DEBUG_MODE`: Enables or disables Flask's debug mode (e.g., "True" or "False"). Defaults to `True` (for development).

## Running (as part of Docker Compose)

The IGA service will be included in the main `docker-compose.yml` file and will be started along with other Aethercast services. It can also be run standalone using `python aethercast/iga/main.py` after setting up the environment variables.
