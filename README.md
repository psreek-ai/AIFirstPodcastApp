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
-   **Image Generation Agent (IGA):** Dynamically generates cover art or accompanying images for podcasts using Google Cloud Vertex AI Imagen, based on prompts.

## Features (Conceptual)

-   Automated topic discovery and suggestion.
-   AI-driven script generation.
-   Customizable voice synthesis.
-   Real-time audio streaming.
-   Snippet generation for content previews.
-   Topic exploration and "go deeper" functionalities.
-   Caching mechanisms for scripts and snippets.
-   Advanced error diagnostics UI.

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
            *   **Google Cloud Platform (GCP) Setup for AIMS, AIMS_TTS, IGA, and API Gateway (GCS):**
                *   AIMS, AIMS_TTS, and IGA utilize Google Cloud Vertex AI.
                *   AIMS_TTS, IGA, and the API Gateway interact with Google Cloud Storage (GCS).
                *   In `common.env`, you **must** set your actual `GCP_PROJECT_ID`, `GCP_LOCATION`, and `GCS_BUCKET_NAME`.
                *   **Create a GCS Bucket:** You need to create a Google Cloud Storage bucket. The name you choose must be globally unique. Update `GCS_BUCKET_NAME` in `common.env` with this name.
                *   **Service Account Key:**
                    *   Create a GCP service account.
                    *   Grant this service account the following roles (or more fine-grained permissions):
                        *   `Vertex AI User` (for AIMS, AIMS_TTS, IGA).
                        *   `Storage Object Admin` (for AIMS_TTS and IGA to write objects, and API Gateway to manage them if needed). Or, more granularly: `Storage Object Creator` and `Storage Object Viewer`.
                        *   If the service account itself will be generating signed URLs (common for local/Docker setup where ADC isn't a K8s SA), it needs `Service Account Token Creator` on itself or on a relevant Google-managed service account if using impersonation (advanced). Simpler for local dev is to ensure the SA key used has rights to sign blobs.
                    *   Download the JSON key file for this service account.
                    *   For **each** of the services (`aims_service`, `aims_tts_service`, `iga`, and `api_gateway` if it needs to directly manipulate GCS beyond signed URLs, though currently it only signs), place this GCP service account key JSON file (e.g., named `gcp-credentials.json`) inside their respective directories (e.g., `aethercast/aims_service/gcp-credentials.json`).
                    *   Ensure the `.env` file for each of these services correctly sets `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json` (this is the path where the key file will be mounted inside their containers as per `docker-compose.yml`).

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
