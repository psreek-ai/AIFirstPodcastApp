# Aethercast: AI-Powered Podcast Generation System

Aethercast is a multi-service application designed to automate the creation of podcasts, from topic discovery and script generation to voice synthesis and audio streaming.

## Overview

The system consists of several microservices that work together:

-   **API Gateway (API_GW):** The main entry point for clients (e.g., frontend UI). Routes requests, serves the frontend, and orchestrates calls to CPOA.
-   **Central Podcast Orchestrator (CPOA):** Manages the podcast generation lifecycle, coordinating other agents. (Note: Currently runs as part of the API Gateway's process).
-   **Topic Discovery Agent (TDA):** Identifies and suggests potential podcast topics from various sources.
-   **Snippet Craft Agent (SCA):** Generates short, engaging text snippets based on topics or content briefs.
-   **Podcast Script Weaver Agent (PSWA):** Generates a full podcast script from harvested content and a topic, using an LLM.
-   **Voice Forge Agent (VFA):** Synthesizes audio from the script using a Text-to-Speech (TTS) service.
-   **Audio Stream Feeder (ASF):** Streams the generated audio to clients in real-time via WebSockets.

### Backend/Supporting Services

-   **AIMS Service (`aims_service`):** Provides access to general-purpose AI models (e.g., Large Language Models) used by other agents like SCA and PSWA.
-   **AIMS TTS Service (`aims_tts_service`):** Handles Text-to-Speech synthesis, converting scripts from VFA into audio. It's used by the VFA.
-   **Image Generation Agent (IGA):** Dynamically generates cover art or accompanying images for podcasts using Google Cloud Vertex AI Imagen, based on prompts, storing them in GCS.

## Implemented Features

-   Automated topic discovery and suggestion (via TDA).
-   AI-driven script generation (via PSWA with AIMS).
-   Customizable voice synthesis (via VFA with AIMS_TTS using Google Cloud TTS, output to GCS).
-   Real-time audio streaming (via ASF, sourcing from GCS signed URLs).
-   Dynamic snippet generation for content previews (via SCA with AIMS, and IGA for images).
-   Topic exploration and "go deeper" functionalities.
-   **Header search functionality for discovering podcasts.**
-   **Email subscription option for users to receive updates.**
-   User authentication (registration and login with JWT).
-   User session management and basic preferences persistence.
-   Storage of media files (audio, images) in Google Cloud Storage.
-   Serving of GCS media files via short-lived signed URLs through the API Gateway.
-   Internal API in API Gateway for services to request signed URLs.
-   CPOA workflow and task state management in PostgreSQL database.
-   Centralized logging format including `workflow_id` and `task_id`.
-   Comprehensive error handling and retry mechanisms in CPOA service calls.
-   Advanced error diagnostics UI for tracing podcast generation.

## Features (Conceptual / Future Enhancements)

-   Enhanced caching mechanisms for scripts and snippets (beyond current direct DB storage).
-   More sophisticated user preference models and personalization.
-   User feedback mechanisms for content quality.
-   Adaptive/interactive podcast elements based on real-time user feedback.
-   Scalable deployment on Kubernetes or serverless platforms.
-   More advanced AI model selection and fine-tuning capabilities within AIMS.
-   Support for multiple languages.

## Project Structure

The project is organized into services within the `aethercast/` directory:

```
aethercast/
├── api_gateway/    # API Gateway service
├── asf/            # Audio Stream Feeder service
├── common/         # Common utilities/modules (if any)
├── cpoa/           # Central Podcast Orchestrator (logic module)
├── fend/           # Frontend static files (HTML, CSS, JS)
├── pswa/           # Podcast Script Weaver Agent service
├── sca/            # Snippet Craft Agent service
├── tda/            # Topic Discovery Agent service
└── vfa/            # Voice Forge Agent service
tests/
├── integration/    # Integration tests
└── unit/           # (Conceptual, if unit tests were service-specific, e.g. aethercast/pswa/tests)
common.env          # Common environment variables for Docker Compose
docker-compose.yml  # Docker Compose configuration
README.md           # This file
```

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
            *   Ensure variables like `DATABASE_FILE`, `PSWA_DATABASE_PATH`, `TDA_DATABASE_PATH` are set to `${DATABASE_FILE_PATH_CONTAINER}`.
            *   Ensure `VFA_SHARED_AUDIO_DIR` is set to `${SHARED_AUDIO_DIR_CONTAINER}`.
            *   Update inter-service URLs to use Docker Compose service names (e.g., `TDA_SERVICE_URL=http://tda:5000/discover_topics` in `api_gateway/.env`). The `.env.example` files should now reflect these Docker-friendly URLs.
            *   For services requiring external API keys (like PSWA for OpenAI, VFA for Google TTS, TDA for NewsAPI):
                *   If you want to run these services with their real external dependencies, populate the API key variables in the respective `.env` files.
                *   For integration testing or running without external API access, ensure the "test mode" flags are enabled:
                    *   In `aethercast/pswa/.env`: `PSWA_TEST_MODE_ENABLED=True` (bypasses LLM)
                    *   In `aethercast/vfa/.env`: `VFA_TEST_MODE_ENABLED=True` (bypasses TTS)
                    *   In `aethercast/tda/.env`: `USE_REAL_NEWS_API=False` (uses simulated news)
                    The `common.env` file sets these test modes to `True` by default, but service-specific `.env` files can override them if needed.
            *   **For Google Cloud Platform (GCP) configuration details (required for AIMS, AIMS_TTS, IGA, and GCS operations), please refer to the new '## GCP Prerequisites and Setup for Local Development' section.**
            *   You will need to update `common.env` with your `GCP_PROJECT_ID`, `GCP_LOCATION`, and `GCS_BUCKET_NAME` as described in that section.
            *   For services using GCP (AIMS, AIMS_TTS, IGA, API Gateway), ensure `GOOGLE_APPLICATION_CREDENTIALS` in their respective `.env` files is set as detailed in the GCP setup section.

2.  **Build and Run Services:**
    Open a terminal at the project root (where `docker-compose.yml` is located) and run:
    ```bash
    docker-compose up --build
    ```
    -   `--build`: Forces Docker to rebuild the images if any Dockerfiles or application code has changed.
    -   Use `-d` to run in detached mode (in the background).

3.  **Accessing Services:**
    *   **API Gateway / Frontend:** `http://localhost:5001`
    *   TDA: `http://localhost:5000`
    *   SCA: `http://localhost:5002`
    *   PSWA: `http://localhost:5004`
    *   VFA: `http://localhost:5005`
    *   ASF: `ws://localhost:5006` (for WebSocket connections)
    *   AIMS Service: `http://localhost:8008` (maps to container port 8000)
    *   AIMS TTS Service: `http://localhost:9009` (maps to container port 9000)
    *   IGA: `http://localhost:5007` (Image Generation Agent, now uses Vertex AI)

    Note: Backend services like AIMS, AIMS TTS, and IGA are typically not accessed directly by the user via a browser. Their ports are exposed primarily for inter-service communication within the Docker network or for debugging purposes. The main interaction point for users is the API Gateway. IGA now performs real image generation using Vertex AI.

4.  **Shared Volumes:**
    *   `postgres_data`: A named volume used by the PostgreSQL service to persist database data. This is the primary database for the application.
    *   `aethercast_db_data`: (Legacy for SQLite, may be phased out) A named volume that previously stored the shared SQLite database.
    *   `aethercast_audio_data`: (Legacy for local file sharing, less critical now) A named volume that was used for storing generated audio/image files locally. With GCS integration, final media assets are stored in the cloud. This volume might still be used for temporary files by some services or if local file handling is still partially active.

5.  **Stopping Services:**
    Press `Ctrl+C` in the terminal where `docker-compose up` is running. If in detached mode, use:
    ```bash
    docker-compose down
    ```
    To remove volumes (and thus delete the shared database and audio files), use:
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

For more detailed information on each service (configuration, API, running standalone), please refer to the `README.md` file within its respective directory in `aethercast/`.
