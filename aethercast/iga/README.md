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
        "image_url": "https://source.unsplash.com/random/400x225/?abstract,podcast",
        "prompt_used": "The prompt that was processed by the placeholder",
        "model_version": "iga-placeholder-v0.1"
    }
    ```
    -   `image_url` (string): A URL to a placeholder image. This placeholder might return a random image fitting a general theme.
    -   `prompt_used` (string): The prompt string that was received and processed by the placeholder.
    -   `model_version` (string): An identifier for the placeholder model.

-   **Error Responses:**
    -   **`400 Bad Request`**: If the `prompt` field is missing or invalid.
        ```json
        {
            "error": "BAD_REQUEST",
            "message": "Missing 'prompt' in request body."
        }
        ```
    -   **`500 Internal Server Error`**: For any unexpected internal errors within the placeholder service.
        ```json
        {
            "error": "INTERNAL_SERVER_ERROR",
            "message": "IGA placeholder encountered an unexpected error."
        }
        ```

## Configuration

The IGA placeholder service might have minimal configuration, primarily for setting its host and port. See `aethercast/iga/.env.example` (once created).

## Running (as part of Docker Compose)

The IGA service will be included in the main `docker-compose.yml` file and will be started along with other Aethercast services.
