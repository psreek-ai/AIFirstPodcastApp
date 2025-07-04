# Image Generation Agent (IGA) - Vertex AI Imagen Edition

## Purpose

The Image Generation Agent (IGA) is responsible for generating images based on text prompts using **Google Cloud Vertex AI's Imagen models**. It is typically called by the Central Podcast Orchestrator Agent (CPOA) when a `cover_art_prompt` is available (e.g., from a generated snippet by SCA) to create an accompanying visual.

IGA operates asynchronously using a Celery task queue. When a request to generate an image is received, a task is dispatched, allowing the client to poll for completion. The generated image is uploaded to Google Cloud Storage (GCS), and the GCS URI is returned upon successful task completion.

Key operations in IGA are **idempotent**: if the same image generation request (identified by an `X-Idempotency-Key`) is submitted multiple times, it will be processed only once, preventing duplicate image generation and GCS uploads. Idempotency state is managed using a shared PostgreSQL database.

## API Contract

IGA now operates asynchronously using Celery for image generation tasks.

### 1. Initiate Image Generation

-   **Endpoint:** `POST /generate_image`
-   **Description:** Accepts a text prompt and other optional parameters, then dispatches a Celery task to generate an image using Vertex AI Imagen. The generated image is saved to Google Cloud Storage (GCS). This endpoint returns a task ID for polling.
-   **Headers:**
    -   `X-Idempotency-Key` (string, **Required**): A unique key (e.g., UUID) provided by the client to ensure idempotent processing of the image generation request. If the same key is used for a new request while a previous one with that key is still being processed or has completed, the service will handle it according to idempotency rules (e.g., return existing result or conflict).
    -   `X-Workflow-ID` (string, Optional): An identifier to correlate this task with a larger workflow, often provided by CPOA.
-   **Request Body (JSON):**
    ```json
    {
        "prompt": "A detailed description of the desired image",
        "aspect_ratio": "1:1", // Optional, e.g., "1:1", "16:9", "9:16"
        "add_watermark": true, // Optional, boolean
        "model_id_override": "imagegeneration@006" // Optional
    }
    ```
    -   `prompt` (string, required): The text prompt for image generation.
    -   `aspect_ratio` (string, optional): Defaults to `IGA_DEFAULT_ASPECT_RATIO`.
    -   `add_watermark` (boolean, optional): Defaults to `IGA_ADD_WATERMARK`.
    -   `model_id_override` (string, optional): To use a specific Vertex AI Imagen model. Defaults to `IGA_VERTEXAI_IMAGE_MODEL_ID`.

-   **Success Response (202 Accepted - JSON):**
    ```json
    {
        "message": "Image generation task accepted.",
        "task_id": "celery_task_uuid_string",
        "status_url": "/v1/tasks/celery_task_uuid_string",
        "idempotency_key_processed": "client_provided_idempotency_key"
    }
    ```
-   **Error Responses (JSON):**
    -   **400 Bad Request**: If `X-Idempotency-Key` header is missing (`IGA_MISSING_IDEMPOTENCY_KEY`), or if the `prompt` is missing/invalid (`IGA_BAD_REQUEST_PROMPT_MISSING`), or other payload validation errors.
    -   **503 Service Unavailable**: If GCS bucket is not configured (`IGA_CONFIG_ERROR_GCS_BUCKET`).

### 2. Get Task Status / Result

-   **Endpoint:** `GET /v1/tasks/<task_id>`
-   **Description:** Poll this endpoint to check the status of the image generation task and retrieve the result once completed.
-   **Success Response (200 OK - JSON, if task completed successfully):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "SUCCESS", // Or PENDING, STARTED, RETRY, FAILURE
        "result": { // Present if status is SUCCESS
            "image_url": "gs://your-bucket-name/images/iga/iga_req_xxxx_yyyy.png",
            "prompt_used": "The prompt that was processed",
            "model_version": "vertex-ai-imagegeneration@006"
        }
    }
    ```
-   **Conflict Response (200 OK - JSON, if task execution determined an idempotency conflict):**
    If the task execution determined a conflict (e.g., another task with the same idempotency key is currently processing and not timed out), the task itself might complete successfully by returning this conflict information.
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "SUCCESS", // Celery task successfully determined and reported the conflict.
        "result": {
            "status": "PROCESSING_CONFLICT",
            "message": "Task with this idempotency key is already processing or recently completed with a conflict.",
            "idempotency_key": "client_provided_idempotency_key"
        }
    }
    ```
-   **Error Response (200 OK - JSON, if task failed):**
    If the task execution resulted in a failure, the status endpoint successfully retrieves this failure state.
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "FAILURE",
        "result": {
            "error": {
                "type": "task_failed", // Or specific exception type
                "message": "<Details of the error that occurred in the task>"
            }
        }
    }
    ```
-   **Response (202 Accepted - JSON, if task is still pending/processing):**
    ```json
    {
        "task_id": "celery_task_uuid_string",
        "status": "PENDING", // Or STARTED, RETRY
        "result": null
    }
    ```

## Configuration

IGA is configured via environment variables. An `.env.example` file is provided in `aethercast/iga/`; copy it to `.env` and customize.

Key environment variables:

-   `IGA_HOST`, `IGA_PORT`, `IGA_DEBUG_MODE`: Standard Flask server settings.
-   **GCP Configuration:** (Crucial for Vertex AI and GCS)
    -   Refer to the main project README's section **'## GCP Prerequisites and Setup for Local Development'**.
    -   `GOOGLE_APPLICATION_CREDENTIALS`: Path to GCP service account key JSON (e.g., `/app/gcp-credentials.json` in Docker).
    -   `IGA_VERTEXAI_PROJECT_ID` (or `GCP_PROJECT_ID` from `common.env`): Your GCP Project ID.
    -   `IGA_VERTEXAI_LOCATION` (or `GCP_LOCATION` from `common.env`): GCP region for Vertex AI.
    -   `GCS_BUCKET_NAME` (typically from `common.env`): GCS bucket for image storage.
-   **Service-specific Variables:**
    -   `IGA_VERTEXAI_IMAGE_MODEL_ID`: Default Vertex AI Imagen model.
    -   `IGA_GCS_IMAGE_PREFIX`: **Required.** GCS path prefix for IGA images (e.g., `images/iga/`).
    -   `IGA_DEFAULT_ASPECT_RATIO`, `IGA_ADD_WATERMARK`: Default image generation parameters.
-   **Celery Configuration:**
    -   `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`: URLs for your Celery message broker and result backend (e.g., Redis).
-   **PostgreSQL Database for Idempotency:** IGA uses a shared PostgreSQL database to store idempotency records. These variables are typically defined in `common.env` and sourced by IGA's `.env` file:
    -   `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.
-   **Idempotency Behavior Configuration:**
    -   `IGA_IDEMPOTENCY_STATUS_PROCESSING`, `IGA_IDEMPOTENCY_STATUS_COMPLETED`, `IGA_IDEMPOTENCY_STATUS_FAILED`: Define the status strings used in the idempotency table.
    -   `IGA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`: Duration after which a "processing" lock is considered stale.

## Dependencies

Service dependencies are listed in `requirements.txt`:
-   `Flask>=2.0`
-   `python-dotenv>=0.15`
-   `google-cloud-aiplatform>=1.0.0`
-   `google-cloud-storage>=1.30.0`
-   `celery>=5.0`
-   `redis>=3.5` (if using Redis for Celery)
-   `psycopg2-binary` (for PostgreSQL idempotency database interaction)

Install dependencies using:
```bash
pip install -r requirements.txt
```

## Running Standalone

To run the IGA service directly for development or testing (requires Flask, Celery worker, Redis, and PostgreSQL to be running and configured):

1.  **Set Environment Variables:** Ensure all required variables (GCP, Celery, PostgreSQL, IGA-specific) are set in an `.env` file or your environment.
2.  **Start PostgreSQL & Redis:** Make sure instances are accessible.
3.  **Apply Database Migrations:** The shared `idempotency_keys` table must exist. Apply the migration from `aethercast/data_stores/migrations/` to your PostgreSQL database if not already done.
4.  **Run Flask App:**
    ```bash
    python aethercast/iga/main.py
    ```
5.  **Run Celery Worker:** In a separate terminal, from the project root:
    ```bash
    celery -A aethercast.iga.main.celery_app worker -l info
    ```
    Images will be generated by the Celery worker and uploaded to GCS.

## Docker

The IGA service is designed to be run as a Docker container and is included in the project's `docker-compose.yml` file. This setup includes the Flask application server and a Celery worker.

-   **Building the Image:** `docker-compose build iga_service`
-   **Configuration in Docker:**
    -   Ensure all necessary environment variables (GCP, Celery, PostgreSQL for idempotency, IGA-specific) are correctly set in `aethercast/iga/.env` and (for shared variables) `common.env`.
    -   **GCP Credentials:** For local Docker development using a service account key, ensure `gcp-credentials.json` is in `aethercast/iga/` and `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json` is set in `.env`. Refer to the main project README for GCP setup.
    -   **Database:** The service connects to the PostgreSQL database defined in `docker-compose.yml` (service name `postgres_db`). Ensure the `idempotency_keys` table migration has been applied as described in the main project README.
-   **Running with Docker Compose:**
    ```bash
    docker-compose up -d iga_service
    ```
    This will start both the `iga_service` (Flask app) and its associated `iga_worker`. To run all Aethercast services:
    ```bash
    docker-compose up -d
    ```
The service's API (for dispatching tasks) will be accessible to other Dockerized services (like CPOA) via its service name and internal port (e.g., `http://iga_service:5007`).

## Monitoring and Logging

This service outputs logs in a structured JSON format. Key operational metrics, such as request latency, task processing times, Vertex AI Imagen call performance, and GCS upload times, are logged.

For details on the general logging format, specific metrics, and how to view logs (e.g., `docker-compose logs iga_service` and `docker-compose logs iga_worker`), please refer to the main [Logging Guide](../../../docs/operational/Logging_Guide.md) and [Metrics Definition](../../../docs/operational/Metrics_Definition.md) in the project's `docs/operational/` directory.

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*

[end of aethercast/iga/README.md]
