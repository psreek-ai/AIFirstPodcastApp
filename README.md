# Aethercast: AI-Powered Podcast Generation System

Aethercast is a multi-service application designed to automate the creation of podcasts, from topic discovery and script generation to voice synthesis and audio streaming.

## Overview

The system consists of several microservices that work together:

-   **API Gateway (API_GW):** The main entry point for clients (e.g., frontend UI). Routes requests, serves the frontend, and orchestrates calls to CPOA.
-   **Central Podcast Orchestrator (CPOA):** Manages the podcast generation lifecycle, coordinating other agents. Its state and task management are persisted in a PostgreSQL database. (Note: CPOA logic currently runs as part of the API Gateway's process).
-   **Topic Discovery Agent (TDA):** Identifies and suggests potential podcast topics from various sources. Core Celery task operations are idempotent.
-   **Snippet Craft Agent (SCA):** Generates short, engaging text snippets based on topics or content briefs, leveraging AIMS. Core Celery task operations are idempotent.
-   **Podcast Script Weaver Agent (PSWA):** Generates a full podcast script from harvested content and a topic, using AIMS. Core Celery task operations are idempotent. It also features script caching in its configured database (SQLite or PostgreSQL, separate from the idempotency store).
-   **Voice Forge Agent (VFA):** Synthesizes audio from the script by calling the AIMS_TTS service. (Idempotency for VFA tasks is a potential future enhancement).
-   **Audio Stream Feeder (ASF):** Streams the generated audio to clients in real-time via WebSockets.

### Backend/Supporting Services

-   **AIMS Service (`aims_service`):** (AI Model Service) Provides a unified interface to general-purpose AI models (e.g., Large Language Models like GPT). Used by SCA and PSWA for content generation.
-   **AIMS TTS Service (`aims_tts_service`):** (AI Model Text-to-Speech Service) Handles Text-to-Speech synthesis using providers like Google Cloud TTS. It's called by the VFA to convert script segments into audio, which are then stored in GCS.
-   **Image Generation Agent (IGA):** Dynamically generates cover art or accompanying images for podcasts using Google Cloud Vertex AI Imagen models, based on input prompts. Stores generated images in GCS. Core Celery task operations are idempotent.

## Implemented Features

-   Automated topic discovery and suggestion (TDA).
-   AI-driven script generation (PSWA with AIMS).
-   AI-driven snippet generation (SCA with AIMS).
-   Dynamic image generation for podcast visuals (IGA with Vertex AI).
-   Customizable voice synthesis (VFA with AIMS_TTS using Google Cloud TTS, output to GCS).
-   Real-time audio streaming (ASF, sourcing from GCS signed URLs).
-   **Idempotent Task Processing:** Key asynchronous operations in TDA, SCA, PSWA, and IGA are designed to be idempotent. Clients making requests to initiate these operations (via the API Gateway and CPOA) should include an `X-Idempotency-Key` header (typically a UUID). The services use this key, in conjunction with a shared `idempotency_keys` table in the PostgreSQL database, to ensure that identical requests (same key, same task type) are processed only once, preventing duplicate resource creation or processing.
    -   If a request with a new key is received, the task proceeds and its outcome is stored.
    -   If a request with a previously seen key is received:
        -   If the original task is still processing, a conflict status is typically returned (e.g., HTTP 409 from the task status endpoint after initial 202 acceptance).
        -   If the original task completed successfully, the stored result is returned without re-processing.
        -   If the original task failed, it may be retried (depending on service logic).
-   Topic exploration and "go deeper" functionalities.
-   Header search functionality for discovering podcasts.
-   Email subscription option for users to receive updates.
-   User authentication (registration and login with JWT).
-   User session management.
-   Storage of media files (audio, images) in Google Cloud Storage.
-   Serving of GCS media files via short-lived signed URLs through the API Gateway.
-   Internal API in API Gateway for services to request signed URLs.
-   CPOA workflow and task state management in PostgreSQL database.
-   Centralized logging format including `workflow_id` and `task_id`.
-   Comprehensive error handling and retry mechanisms in CPOA service calls and within idempotent Celery tasks.
-   Advanced error diagnostics UI for tracing podcast generation.
-   Script caching for PSWA to reduce redundant LLM calls (can be configured for SQLite or PostgreSQL).

## Features (Conceptual / Future Enhancements)

-   Idempotency for VFA (Voice Forge Agent) tasks.
-   More advanced caching strategies (e.g., distributed caching, semantic caching for LLM results).
-   More sophisticated user preference models and personalization.
-   User feedback mechanisms for content quality.
-   Adaptive/interactive podcast elements based on real-time user feedback.
-   Scalable deployment on Kubernetes or serverless platforms.
-   More advanced AI model selection and fine-tuning capabilities within AIMS.
-   Support for multiple languages.

## Project Structure

The project is organized into services within the `aethercast/` directory. Each service typically contains its own `main.py`, `Dockerfile`, `requirements.txt`, and an `.env.example` file.

```
aethercast/
├── api_gateway/        # API Gateway: Main entry point, serves frontend, orchestrates CPOA.
├── aims_service/       # AIMS: AI Model Service (for LLMs).
├── aims_tts_service/   # AIMS_TTS: AI Model Text-to-Speech Service.
├── asf/                # ASF: Audio Stream Feeder service.
├── cpoa/               # CPOA: Central Podcast Orchestrator (logic module, runs in API_GW process).
├── data_stores/        # Database related files, including:
│   └── migrations/     # SQL migration scripts (e.g., for idempotency_keys table).
├── fend/               # Frontend static files (HTML, CSS, JS).
├── iga/                # IGA: Image Generation Agent.
├── pswa/               # PSWA: Podcast Script Weaver Agent service.
├── sca/                # SCA: Snippet Craft Agent service.
├── tda/                # TDA: Topic Discovery Agent service.
└── vfa/                # VFA: Voice Forge Agent service.
docs/                   # Project documentation.
tests/
├── integration/        # Integration tests for the full flow.
common.env              # Common environment variables for Docker Compose.
docker-compose.yml      # Docker Compose configuration.
README.md               # This file.
```
Unit tests are typically located within each service's `tests/` subdirectory (e.g., `aethercast/pswa/tests/`).

## GCP Prerequisites and Setup for Local Development

This section details the Google Cloud Platform (GCP) resources and configurations required for local development and testing of Aethercast services that interact with GCP, specifically AIMS, AIMS_TTS, IGA, and the API Gateway (for GCS operations).

**A. GCP Project:**

1.  **Create or Select a Project:**
    *   If you don't have one, create a new GCP project through the [Google Cloud Console](https://console.cloud.google.com/projectcreate).
    *   Alternatively, you can use an existing GCP project.
2.  **Identify your Project ID (`GCP_PROJECT_ID`):**
    *   Your Project ID is a unique string that identifies your project. You can find it on the project dashboard in the Google Cloud Console.
    *   Ensure this is the ID, not the project name or number.
3.  **Update `common.env`:**
    *   Open the `common.env` file at the root of the project.
    *   Set the `GCP_PROJECT_ID` variable to your actual GCP Project ID.
        ```env
        GCP_PROJECT_ID="your-gcp-project-id"
        ```

**B. Enable APIs:**

For Aethercast services to function correctly, you need to enable the following APIs in your GCP project. You can enable them from the [API Library](https://console.cloud.google.com/apis/library) in the Google Cloud Console:

*   Vertex AI API (`aiplatform.googleapis.com`)
*   Cloud Storage API (`storage.googleapis.com`)
*   Cloud SQL Admin API (`sqladmin.googleapis.com`) (Primarily for CPOA database if using Cloud SQL in future, good to enable)
*   Artifact Registry API (`artifactregistry.googleapis.com`) (If you plan to store Docker images in GCP)
*   Cloud Build API (`cloudbuild.googleapis.com`) (If you plan to use Cloud Build for CI/CD)
*   Secret Manager API (`secretmanager.googleapis.com`) (If you plan to manage secrets in GCP)

**C. GCS Bucket:**

A Google Cloud Storage (GCS) bucket is required for storing generated media files (audio, images).

1.  **Create a GCS Bucket:**
    *   Navigate to the [Cloud Storage browser](https://console.cloud.google.com/storage/browser) in the GCP console.
    *   Create a new bucket. The name must be globally unique.
    *   Choose a suitable region for your bucket (e.g., `us-central1`).
2.  **Get GCS Bucket Name (`GCS_BUCKET_NAME`):**
    *   This is the name you assigned to your bucket in the previous step.
3.  **Update `common.env`:**
    *   Open the `common.env` file.
    *   Set the `GCS_BUCKET_NAME` variable to your bucket's name.
        ```env
        GCS_BUCKET_NAME="your-globally-unique-bucket-name"
        ```
    *   Also, ensure the `GCP_LOCATION` variable in `common.env` is set. This typically corresponds to the region where you created your GCS bucket and plan to run Vertex AI jobs (e.g., `us-central1`).
        ```env
        GCP_LOCATION="us-central1"
        ```

**D. Service Account for Local Docker Development:**

This service account (SA) and its key are primarily used for local Dockerized development and testing. They allow the services running in Docker containers on your local machine to authenticate to GCP services.

1.  **Purpose:** Allows local Docker containers to impersonate a service account and access GCP resources as if they were running in GCP.
2.  **Create a Service Account:**
    *   In the GCP Console, navigate to "IAM & Admin" > "Service Accounts".
    *   Click "Create Service Account".
    *   Give it a name (e.g., `aethercast-local-dev-sa`) and an optional description.
3.  **Download JSON Key:**
    *   After creating the service account, select it from the list.
    *   Go to the "Keys" tab.
    *   Click "Add Key" > "Create new key".
    *   Choose "JSON" as the key type and click "Create".
    *   A JSON file will be downloaded. **Rename this file to `gcp-credentials.json`**.
4.  **Grant IAM Roles:**
    Grant the following minimum required IAM roles to this service account. You can do this from the "IAM" page in the GCP Console by adding the service account as a new principal:
    *   `Vertex AI User`: Required by AIMS, AIMS_TTS, and IGA services to interact with Vertex AI models.
    *   `Storage Object Admin`: Required by AIMS_TTS and IGA to write objects to GCS, and potentially by the API Gateway if it needs to manage objects directly (though its primary GCS interaction is generating signed URLs).
        *   *Alternative (more granular)*: You can use `Storage Object Creator` (to allow creating/uploading files) and `Storage Object Viewer` (to allow reading files) instead of `Storage Object Admin` if you prefer stricter permissions.
    *   `Service Account Token Creator`: This role must be granted *on the service account itself*. It is needed if this service account is used to generate signed URLs directly, which is a common pattern in local Docker setups where the application code (e.g., in API Gateway) running with these credentials creates signed URLs for client-side GCS access.
5.  **Place `gcp-credentials.json`:**
    Copy the downloaded and renamed `gcp-credentials.json` file into the following service directories:
    *   `aethercast/aims_service/gcp-credentials.json`
    *   `aethercast/aims_tts_service/gcp-credentials.json`
    *   `aethercast/iga/gcp-credentials.json`
    *   `aethercast/api_gateway/gcp-credentials.json`
6.  **Configure `.env` Files:**
    The `GOOGLE_APPLICATION_CREDENTIALS` environment variable in each service's specific `.env` file tells the Google Cloud client libraries where to find the credentials key. This variable must be set to point to the path where the key will be mounted inside the Docker containers. As per the `docker-compose.yml` configuration, this path is typically `/app/gcp-credentials.json`.
    *   Ensure that in `aethercast/aims_service/.env`, `aethercast/aims_tts_service/.env`, `aethercast/iga/.env`, and `aethercast/api_gateway/.env`, the following line is present and correctly set:
        ```env
        GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json
        ```

## Running with Docker Compose

This project uses Docker Compose to manage and run the suite of microservices in a containerized environment. This is the recommended way to run the application for development and testing.

**Prerequisites:**
-   Docker installed and running.
-   Docker Compose installed.

**Setup:**

1.  **Environment Files:**
    *   A `common.env` file exists at the project root. It defines shared container-internal paths for resources like the database and audio files, and default test mode flags.
    *   Each service directory (e.g., `aethercast/api_gateway/`, `aethercast/tda/`, etc.) contains an `.env.example` file. For Docker Compose to work correctly with local overrides (like API keys), you should:
        *   Copy each `aethercast/service_name/.env.example` to `aethercast/service_name/.env`.
        *   **Edit these new `.env` files:**
            *   **Database Configuration:** Most services (TDA, SCA, PSWA, IGA, API Gateway/CPOA) now rely on a **shared PostgreSQL database** for core functionalities like CPOA state management and idempotency tracking. Ensure the `POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, and `POSTGRES_PORT` variables are correctly set. These are typically defined in `common.env` and sourced by individual service `.env` files (e.g., `POSTGRES_HOST=${POSTGRES_HOST}`). The `postgres_db` service in `docker-compose.yml` provides this database.
            *   **Idempotency Configuration:** Services implementing idempotency (TDA, SCA, PSWA, IGA) have specific environment variables in their `.env.example` files (e.g., `TDA_IDEMPOTENCY_STATUS_PROCESSING`, `TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS`). Review these and ensure they are set as needed (defaults are provided). These configurations control the behavior of the idempotency mechanism for each service.
            *   **Inter-service URLs:** Ensure these are set to use Docker Compose service names (e.g., `TDA_SERVICE_URL=http://tda:5000/discover_topics` in `api_gateway/.env`). The `.env.example` files generally reflect these Docker-friendly URLs.
            *   **External API Keys & Test Modes:**
                *   For services requiring external API keys (e.g., TDA for NewsAPI), populate these in the respective `.env` files if you intend to use the live services.
                *   For development or testing without external calls, ensure "test mode" or placeholder flags are enabled (e.g., `USE_REAL_NEWS_API=False` in `tda/.env`, `PSWA_TEST_MODE_ENABLED=True` in `pswa/.env`). These are often defaulted in `common.env` but can be overridden.
            *   **GCP Configuration:** For services interacting with GCP (AIMS, AIMS_TTS, IGA, API Gateway for GCS), follow the '## GCP Prerequisites and Setup for Local Development' section. This includes setting `GCP_PROJECT_ID`, `GCP_LOCATION`, `GCS_BUCKET_NAME` in `common.env`, and ensuring `GOOGLE_APPLICATION_CREDENTIALS` is correctly configured in service-specific `.env` files.

2.  **Database Initialization (PostgreSQL):**
    *   The PostgreSQL service defined in `docker-compose.yml` (`postgres_db`) will initialize itself.
    *   The `api_gateway` service (which includes CPOA logic) and other services like TDA, PSWA, SCA, IGA will attempt to connect to this database.
    *   **Idempotency Table Migration:** A SQL migration script (`aethercast/data_stores/migrations/001_create_idempotency_keys_table.sql`) creates the necessary `idempotency_keys` table used by TDA, SCA, PSWA, and IGA. **This script must be applied manually** to the PostgreSQL database after the `postgres_db` container is up and running. You can use a PostgreSQL client tool (e.g., `psql` via `docker exec`, or a GUI tool like DBeaver or pgAdmin) connected to the PostgreSQL container.
        *   Example using `psql` via `docker exec`:
            ```bash
            docker exec -i $(docker-compose ps -q postgres_db) psql -U your_db_user -d aethercast_db < aethercast/data_stores/migrations/001_create_idempotency_keys_table.sql
            ```
            (Replace `your_db_user` and `aethercast_db` with the actual values from your `.env` files if they differ from the defaults in `common.env` used by the `postgres_db` service).
    *   **Other Tables:** Services like CPOA (via API Gateway) and TDA also manage their own tables (e.g., `cpoa_tasks`, `topics_snippets`). These are typically created or checked for existence by the services themselves on startup (see `init_cpoa_db()` in API Gateway, `init_tda_db()` in TDA).

3.  **Build and Run Services:**
    Open a terminal at the project root (where `docker-compose.yml` is located) and run:
    ```bash
    docker-compose up --build
    ```
    -   `--build`: Forces Docker to rebuild the images if any Dockerfiles or application code has changed.
    -   Use `-d` to run in detached mode (in the background).

4.  **Accessing Services:**
    *   **API Gateway / Frontend:** `http://localhost:5001`
    *   TDA: `http://localhost:5000`
    *   SCA: `http://localhost:5002`
    *   PSWA: `http://localhost:5004`
    *   VFA: `http://localhost:5005`
    *   ASF: `ws://localhost:5006` (for WebSocket connections)
    *   AIMS Service: `http://localhost:8008` (maps to container port 8000)
    *   AIMS TTS Service: `http://localhost:9009` (maps to container port 9000)
    *   IGA: `http://localhost:5007`

    Note: Backend services like AIMS, AIMS TTS, and IGA are typically not accessed directly by the user via a browser. Their ports are exposed primarily for inter-service communication within the Docker network or for debugging purposes.

5.  **Shared Volumes:**
    *   `postgres_data`: A named volume used by the PostgreSQL service to persist database data. This is the primary database for the application, storing CPOA task states, idempotency records for TDA/SCA/PSWA/IGA, and potentially cached scripts (e.g., by PSWA if configured for PostgreSQL).
    *   `aethercast_db_data`: (Legacy for SQLite) This volume was used for SQLite databases. Its role is diminished as core functionalities use PostgreSQL.
    *   `aethercast_audio_data`: (Legacy for local file sharing) With GCS as the primary media store, this volume's importance is reduced.

6.  **Stopping Services:**
    Press `Ctrl+C` in the terminal where `docker-compose up` is running. If in detached mode, use:
    ```bash
    docker-compose down
    ```
    To remove volumes (and thus delete the PostgreSQL database data), use:
    ```bash
    docker-compose down -v
    ```

**Running Integration Tests:**
Once the Docker Compose environment is up and running (with services in their "test modes"):
1. Ensure you have Python and `requests` installed on your host machine (or in a virtual environment).
2. Navigate to the project root directory.
3. Run the integration tests:
   ```bash
   python -m unittest tests/integration/test_full_flow.py
   ```
   (You might need to set `PYTHONPATH=.` or `export PYTHONPATH=$(pwd)` for the tests to find the `aethercast` modules if you add more complex test runners or helper modules locally).
   The `API_GATEWAY_BASE_URL` in the test script defaults to `http://localhost:5001/api/v1`.

## Individual Service READMEs

For more detailed information on each service (specific configurations, API details, and potentially how to run them standalone if supported), please refer to the `README.md` file within its respective directory in `aethercast/`. Note that these individual READMEs may not reflect all cross-cutting concerns like the centralized idempotency setup to the same level of detail as this main README.
