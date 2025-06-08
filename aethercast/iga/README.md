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

## Limitations of Current Placeholder

The current IGA service is a basic placeholder and has several limitations:

-   **No Actual AI Image Generation:** It does not use any AI models to generate images based on the prompt. Instead, it fetches random images from Unsplash.
-   **Low Relevance:** Unsplash images are selected based on very broad keywords (derived from the first few words of the prompt, a random theme, and static terms like "podcast", "abstract"). The relevance to the actual prompt content is often low or coincidental.
-   **Inconsistency:** Requesting an image for the same prompt multiple times will likely result in different images due to the `source.unsplash.com/random/` endpoint and the random theme selection.
-   **No Simulation of Real-World Conditions:** It does not simulate the typical latency involved in AI image generation, nor does it replicate the variety of error modes that a real image generation service might encounter (e.g., specific API errors, content moderation flags, rate limits).
-   **Limited Customization:** There's no control over image style, aspect ratio (fixed at 400x225), or other parameters that a real IGA would offer.

## Requirements for a Real IGA Implementation

A production-ready IGA would require the following:

1.  **Integration with an Image Generation Backend:**
    *   **Option 1: Cloud AI Service API:** Integrate with a managed AI image generation service like OpenAI's DALL-E API, Stability AI's API (Stable Diffusion), Google Imagen, or similar. This typically involves making HTTP requests to their endpoints.
    *   **Option 2: Self-Hosted Model Interface:** If using a self-hosted open-source model (e.g., a specific Stable Diffusion checkpoint), the IGA would need to interface with the model serving framework (e.g., via an internal API, a message queue, or direct Python integration if feasible).
2.  **API Key Management:**
    *   Securely store and manage API keys for the chosen external image generation service. This should involve using environment variables or a secrets management system, not hardcoding keys.
3.  **Prompt Engineering:**
    *   The IGA might need to preprocess or enhance the input `cover_art_prompt` received from CPOA to make it more effective for the specific AI model being used (e.g., adding style cues, negative prompts, ensuring length constraints).
4.  **Image Processing, Storage, and Delivery:**
    *   **Decision Point:** Determine if the IGA will store the generated images or simply return the URL provided by the external service.
    *   **If Storing:**
        *   Choose a storage solution (e.g., AWS S3, Google Cloud Storage, local file system if appropriate for the deployment).
        *   Define image formats, sizes, and compression levels.
        *   Implement a robust naming convention and path structure for stored images (e.g., based on snippet ID or a hash of the prompt).
        *   The IGA would then return a URL to the image stored in this location.
    *   **If Not Storing:** The IGA directly returns the temporary or permanent URL provided by the image generation service. This might be simpler but offers less control and potential issues with URL expiry.
5.  **Robust Error Handling:**
    *   Handle various errors from the image generation backend:
        *   API authentication errors (invalid key).
        *   Rate limiting errors.
        *   Content moderation flags (if the prompt or generated image violates policies).
        *   Timeouts if the generation takes too long.
        *   Service unavailability or other HTTP errors.
    *   Return appropriate error responses to CPOA, possibly with specific error codes.
6.  **Cost Management and Monitoring:**
    *   Real AI image generation incurs costs per image or per API call.
    *   Implement mechanisms for monitoring usage and costs.
    -   Consider adding caching strategies (e.g., if the exact same prompt is requested again, return the previously generated image if stored) to reduce redundant calls and costs.
7.  **API Contract Adherence:**
    *   The service must continue to adhere to the established `POST /generate_image` API contract.
    *   The response should include `image_url` (pointing to the generated and possibly stored image), `prompt_used` (the actual prompt sent to the model after any engineering), and `model_version` (identifier of the AI model and version used).
8.  **Asynchronous Operation (Consideration):**
    *   Image generation can be slow. For a better user experience in the overall system, the IGA might need to support an asynchronous operation mode (e.g., CPOA submits a request, IGA returns an immediate acknowledgment with a task ID, and CPOA polls or receives a webhook when the image is ready). However, the current CPOA implementation calls IGA synchronously.

## Requirements for an Advanced Mock IGA

If a real IGA implementation is deferred, an "advanced mock" could provide more utility than the current placeholder:

1.  **Deterministic Output:**
    *   Generate a consistent placeholder image URL for a given prompt. This could be achieved by:
        *   Using a deterministic placeholder service that accepts seeds or specific identifiers (e.g., `https://picsum.photos/seed/{seed}/400/225` where seed is derived from a hash of the prompt).
        *   Using Unsplash but with more specific, hashed keywords rather than random ones, e.g., `https://source.unsplash.com/400x225/?{hashed_prompt_keywords}`.
    *   This aids in UI testing by ensuring the same visual appears for the same content.
2.  **Improved Keyword-to-Theme Mapping:**
    *   Instead of just taking the first few words, implement a simple keyword extraction logic from the prompt.
    *   Map common prompt keywords or categories (e.g., "technology", "nature", "business") to more specific Unsplash search terms or collections to improve the relevance of placeholder images.
3.  **Simulate Latency and Errors:**
    *   Introduce optional query parameters or headers in the request to `/generate_image` that allow CPOA (or a tester) to simulate different conditions:
        *   `?simulate_delay=1500ms`: Makes the IGA wait for the specified duration before responding.
        *   `?simulate_error=503`: Makes the IGA return a specific HTTP error code.
        *   `?simulate_content_moderation=true`: Returns an error similar to what a real service might send if a prompt is flagged.
4.  **Expanded Local Image Set (Alternative to Unsplash):**
    *   Serve images from a predefined local directory within the IGA service.
    *   Images could be categorized, and the IGA could try to select one based on (hashed) prompt keywords. This offers more control over the placeholder content and avoids external dependencies during tests.
    *   The `/generate_image` response would then return a URL pointing to an endpoint on the IGA itself (e.g., `/images/{image_id}.jpg`) which would serve the static file.
5.  **More Detailed Logging:**
    *   Log the mapping decisions (e.g., "Prompt 'AI in healthcare' mapped to keywords 'ai,medical' for Unsplash query").

These enhancements would make the mock IGA a more useful tool for development and testing while a full AI-powered solution is being developed.
