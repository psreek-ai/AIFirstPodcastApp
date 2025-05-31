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

4.  **Shared Volumes:**
    *   `aethercast_db_data`: A named volume that stores the shared SQLite database (`aethercast_podcasts.db`). This ensures data persistence across container restarts and allows all services to access the same DB instance.
    *   `aethercast_audio_data`: A named volume that stores generated audio files. VFA writes to this volume, and ASF reads from it.

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
