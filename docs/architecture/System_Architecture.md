# System_Architecture.md

**Version:** 1.0
**Date:** May 19, 2025

## 1. Overview

This document outlines the high-level system architecture for **Aethercast** (or your chosen app name), an AI-driven podcast application. The architecture is designed to support the dynamic, real-time generation of all content, from landing page snippets to full podcast audio streams, as described in `docs/vision/03_Podcast_App_Manifesto.md`.

The system is built around a central **Podcast Orchestrator Agent** that coordinates various specialized AI agents and services to deliver a unique, generative audio experience to the user.

## 2. System Architecture Diagram

```mermaid
graph TD
    %% Node Definitions
    User["User"]
    FEND["Frontend UI / Client App"]
    APIGW["API Gateway (runs CPOA logic)"]
    CPOA["Central Podcast Orchestrator (logic within APIGW)"]

    subgraph Asynchronous Agents (Celery-based)
        TDA_Service["TDA Service (Flask App)"]
        TDA_Worker["TDA Worker (Celery)"]
        SCA_Service["SCA Service (Flask App)"]
        SCA_Worker["SCA Worker (Celery)"]
        PSWA_Service["PSWA Service (Flask App)"]
        PSWA_Worker["PSWA Worker (Celery)"]
        IGA_Service["IGA Service (Flask App)"]
        IGA_Worker["IGA Worker (Celery)"]
        VFA_Service["VFA Service (Flask App)"]
        VFA_Worker["VFA Worker (Celery)"]
    end

    WCHA["WebContentHarvesterAgent (Library)"]
    DUIA["DynamicUIAgent (DUIA - Conceptual/Module)"]

    subgraph AI Model Services
        AIMS_SVC["AIMS Service (LLM Proxy)"]
        AIMS_TTS_SVC["AIMS_TTS Service (TTS Proxy)"]
        VertexAI_IMG["Vertex AI Imagen (Google Cloud)"]
    end

    subgraph Supporting Infrastructure
        PostgresDB["PostgreSQL DB </br>(CPOA State, Idempotency, Topics, Scripts, Users etc.)"]
        Redis["Redis </br>(Celery Broker, Caching)"]
        GCS["Google Cloud Storage </br>(Audio, Images)"]
        Internet["External Web / News APIs"]
        ASF["Audio Stream Feeder"]
    end

    %% Connections
    User --> FEND
    FEND -- "HTTPS Requests / WebSocket (to ASF)" --> APIGW
    APIGW -- "includes" --> CPOA

    CPOA -- "Dispatch Celery Task </br> (X-Idem-Key, X-Workflow-ID)" --> TDA_Service
    TDA_Service -- "Celery Task" --> Redis
    Redis --> TDA_Worker
    TDA_Worker -- "HTTP Call" --> Internet
    TDA_Worker -- "DB Read/Write" --> PostgresDB
    TDA_Service -- "Task Status Poll by CPOA" --> CPOA

    CPOA -- "Dispatch Celery Task </br> (X-Idem-Key, X-Workflow-ID)" --> SCA_Service
    SCA_Service -- "Celery Task" --> Redis
    Redis --> SCA_Worker
    SCA_Worker -- "HTTP Call" --> AIMS_SVC
    SCA_Worker -- "DB Read/Write (Idempotency)" --> PostgresDB
    SCA_Service -- "Task Status Poll by CPOA" --> CPOA

    CPOA -- "Call as Library" --> WCHA
    WCHA -- "HTTP Call" --> Internet

    CPOA -- "Dispatch Celery Task </br> (X-Idem-Key, X-Workflow-ID)" --> PSWA_Service
    PSWA_Service -- "Celery Task" --> Redis
    Redis --> PSWA_Worker
    PSWA_Worker -- "HTTP Call" --> AIMS_SVC
    PSWA_Worker -- "DB Read/Write (Idempotency, Script Cache)" --> PostgresDB
    PSWA_Service -- "Task Status Poll by CPOA" --> CPOA

    CPOA -- "Dispatch Celery Task </br> (X-Idem-Key, X-Workflow-ID)" --> VFA_Service
    VFA_Service -- "Celery Task" --> Redis
    Redis --> VFA_Worker
    VFA_Worker -- "HTTP Call" --> AIMS_TTS_SVC
    VFA_Worker -- "DB Read/Write (Idempotency)" --> PostgresDB
    VFA_Service -- "Task Status Poll by CPOA" --> CPOA

    CPOA -- "Dispatch Celery Task </br> (X-Idem-Key, X-Workflow-ID)" --> IGA_Service
    IGA_Service -- "Celery Task" --> Redis
    Redis --> IGA_Worker
    IGA_Worker -- "API Call" --> VertexAI_IMG
    IGA_Worker -- "GCS Write" --> GCS
    IGA_Worker -- "DB Read/Write (Idempotency)" --> PostgresDB
    IGA_Service -- "Task Status Poll by CPOA" --> CPOA

    AIMS_TTS_SVC -- "GCS Write" --> GCS

    CPOA -- "Content/Context for UI" --> DUIA
    DUIA -- "UI Definition JSON" --> APIGW

    CPOA -- "Notify New Audio (GCS URI)" --> ASF
    ASF -- "Get Signed URL" --> APIGW
    APIGW -- "GCS Read (for Signed URL)" --> GCS
    FEND -- "WebSocket Audio Stream" --> ASF
    ASF -- "Stream from GCS via Signed URL" --> FEND


    CPOA -- "DB Read/Write (Workflow State)" --> PostgresDB
    APIGW -- "DB Read/Write (Users, Sessions)" --> PostgresDB


    %% Styling
    style User fill:#f9f,stroke:#333,stroke-width:2px
    style FEND fill:#bbf,stroke:#333,stroke-width:2px
    style APIGW fill:#ccf,stroke:#333,stroke-width:2px
    style CPOA fill:#f00,stroke:#333,stroke-width:3px,color:#fff
    style TDA_Service fill:#ff9,stroke:#333,stroke-width:2px
    style SCA_Service fill:#ff9,stroke:#333,stroke-width:2px
    style PSWA_Service fill:#ff9,stroke:#333,stroke-width:2px
    style VFA_Service fill:#ff9,stroke:#333,stroke-width:2px
    style IGA_Service fill:#ff9,stroke:#333,stroke-width:2px
    style TDA_Worker fill:#fde,stroke:#333,stroke-width:2px
    style SCA_Worker fill:#fde,stroke:#333,stroke-width:2px
    style PSWA_Worker fill:#fde,stroke:#333,stroke-width:2px
    style VFA_Worker fill:#fde,stroke:#333,stroke-width:2px
    style IGA_Worker fill:#fde,stroke:#333,stroke-width:2px
    style WCHA fill:#fdb,stroke:#333,stroke-width:2px
    style DUIA fill:#f9c,stroke:#333,stroke-width:2px
    style AIMS_SVC fill:#9cf,stroke:#333,stroke-width:2px
    style AIMS_TTS_SVC fill:#9cf,stroke:#333,stroke-width:2px
    style VertexAI_IMG fill:#9cf,stroke:#333,stroke-width:2px
    style PostgresDB fill:#9c9,stroke:#333,stroke-width:2px
    style Redis fill:#f69,stroke:#333,stroke-width:2px
    style GCS fill:#9c9,stroke:#333,stroke-width:2px
    style ASF fill:#f99,stroke:#333,stroke-width:2px
    style Internet fill:#ccc,stroke:#333,stroke-width:2px
```

## 3. Major Components & Technologies

Below are descriptions of the major components depicted in the architecture diagram and the likely technologies involved.

### 3.1. User Facing

* **User:** The end-user interacting with the podcast application.
* **Frontend UI / Client App (FEND):**
    * **Description:** The web application or mobile application interface that users interact with. Responsible for displaying podcast snippets, playback controls, and handling user input. It communicates with the backend via the API Gateway. For real-time audio, it will establish a connection for streaming.
    * **Key Responsibilities:**
        * Rendering dynamically generated UI elements (snippets, player).
        * Capturing user interactions (clicks, searches).
        * Initiating requests for podcast generation.
        * Handling real-time audio streaming and playback.
        * Displaying loading states and feedback during generation.
    * **Potential Technologies:**
        * Web: React, Vue, Svelte, Angular with HTML5, CSS3, JavaScript/TypeScript.
        * Mobile: Swift/Objective-C (iOS), Kotlin/Java (Android), React Native, Flutter.
        * Real-time communication for UI updates from CPOA: WebSockets, Server-Sent Events (SSE).
        * Audio Streaming: HTML5 Audio API, WebRTC, HLS/DASH.

### 3.2. Backend Services

* **API Gateway (APIGW):**
    * **Description:** Single entry point for all client requests to the backend. Handles request routing, authentication, authorization, rate limiting, and potentially request/response transformations.
    * **Key Responsibilities:**
        * Expose public endpoints for frontend interaction.
        * Route requests to the Central Podcast Orchestrator Agent.
        * Manage security and access control.
    * **Potential Technologies:** AWS API Gateway, Azure API Management, Google Cloud API Gateway, Kong, Tyk.

* **Central Podcast Orchestrator Agent (CPOA):**
    * **Description:** Runs as part of the API Gateway process. Orchestrates podcast generation by dispatching asynchronous Celery tasks to specialized agents (TDA, SCA, PSWA, IGA, VFA). Manages overall workflow state in PostgreSQL. Propagates `X-Idempotency-Key` and its own `workflow_id` (as `X-Workflow-ID`) to downstream agents.
    * **Key Responsibilities:** Workflow management, task delegation (Celery), polling for task results, state persistence (PostgreSQL), error handling.
    * **Potential Technologies:** Python, Flask (as part of API GW).

* **Specialized AI Agents (TDA, SCA, PSWA, IGA, VFA):**
  Each of these agents is now a microservice consisting of a Flask app (for task status polling and potentially direct interaction if ever needed) and Celery workers for asynchronous task processing. They implement idempotency for their core Celery tasks using `X-Idempotency-Key` and a shared PostgreSQL table (`idempotency_keys`).

    * **`TopicDiscoveryAgent` (TDA):**
        * **Description:** Asynchronously discovers topics via its `discover_topics_task` (Celery). Uses NewsAPI (via `fetch_news_from_newsapi_task` sub-task) or simulated data. Stores topics in PostgreSQL.
        * **Interaction:** Receives Celery task from CPOA (with idempotency keys). Uses PostgreSQL for idempotency and topic storage. Calls external News APIs.
        * **Potential Technologies:** Python, Flask, Celery, NewsAPI client, PostgreSQL.

    * **`SnippetCraftAgent` (SCA):**
        * **Description:** Asynchronously crafts snippets via its `sca_craft_snippet_task` (Celery). Calls AIMS for LLM.
        * **Interaction:** Receives Celery task from CPOA. Uses AIMS. Uses PostgreSQL for idempotency.
        * **Potential Technologies:** Python, Flask, Celery, Requests, PostgreSQL.

    * **`WebContentHarvesterAgent` (WCHA):**
        * **Description:** (As before) Functions as a Python library called directly by CPOA for synchronous web content fetching and extraction. Does not have its own Celery task or direct idempotency handling (idempotency of its use would be covered by the calling CPOA workflow if CPOA's operation involving WCHA was idempotent).
        * **Interaction:** Called as a library by CPOA. Accesses External Web.
        * **Potential Technologies:** Python, `duckduckgo_search`, `trafilatura`.

    * **`PodcastScriptWeaverAgent` (PSWA):**
        * **Description:** Asynchronously weaves scripts via `weave_script_task` (Celery). Calls AIMS. Supports script caching (SQLite or PostgreSQL).
        * **Interaction:** Receives Celery task from CPOA. Uses AIMS. Uses PostgreSQL for idempotency and optionally for script caching.
        * **Potential Technologies:** Python, Flask, Celery, Requests, PostgreSQL, SQLite.

    * **`VoiceForgeAgent` (VFA):**
        * **Description:** Asynchronously forges voice via `forge_voice_task` (Celery). Calls AIMS_TTS service (which handles TTS and GCS upload).
        * **Interaction:** Receives Celery task from CPOA. Calls AIMS_TTS. Uses PostgreSQL for idempotency.
        * **Potential Technologies:** Python, Flask, Celery, Requests, PostgreSQL.

    * **`ImageGenerationAgent` (IGA):**
        * **Description:** Asynchronously generates images via `generate_image_vertex_ai_task` (Celery). Calls Google Vertex AI Imagen and uploads to GCS.
        * **Interaction:** Receives Celery task from CPOA. Calls Vertex AI. Uploads to GCS. Uses PostgreSQL for idempotency.
        * **Potential Technologies:** Python, Flask, Celery, `google-cloud-aiplatform`, `google-cloud-storage`, PostgreSQL.

    * **`DynamicUIAgent` (DUIA) (Conceptual):** (As before)

### 3.3. Supporting Infrastructure

* **AI Model Services:**
    * **`AIMS Service (AIMS_SVC)`:** Synchronous HTTP proxy for general LLMs (e.g., GPT via Vertex AI).
    * **`AIMS_TTS Service (AIMS_TTS_SVC)`:** Synchronous HTTP proxy for TTS services (e.g., Google TTS via Vertex AI), handles audio generation and upload to GCS, returns GCS URI.
    * **`Vertex AI Imagen`:** Google Cloud service for image generation, called by IGA.
* **Databases & Messaging:**
    * **`PostgreSQL DB`:** Central relational database storing:
        * CPOA workflow and task instance state (`workflow_instances`, `task_instances`).
        * Shared `idempotency_keys` table for TDA, SCA, PSWA, IGA, VFA.
        * TDA's discovered topics (`topics_snippets` table).
        * PSWA's script cache (if configured for PostgreSQL).
        * User accounts, sessions, subscriptions (managed by API Gateway).
    * **`Redis`:** Acts as the Celery message broker for task queuing and potentially for results backend or general caching.
* **Storage:**
    * **`Google Cloud Storage (GCS)`:** Primary storage for generated media files (audio from AIMS_TTS via VFA, images from IGA).
* **`Audio Stream Feeder (ASF)`:** WebSocket server for streaming audio (fetched from GCS via signed URLs) to clients.
* **`External Web / News APIs (Internet)`:** (As before)

## 4. Key Considerations

* **Scalability:** All components, especially API Gateway (running CPOA), AI agents (Flask app + Celery workers), AI Model Services, and databases must be designed for horizontal scalability. Celery workers allow for distributed task processing.
* **Latency:** (As before) Minimized by asynchronous processing where appropriate. Polling for Celery task results introduces some latency but decouples services.
* **Cost:** (As before)
* **Modularity & Maintainability:** (As before) Microservice architecture with Celery tasks enhances this.
* **Resilience & Fault Tolerance:** Celery's retry mechanisms, coupled with the implemented idempotency in backend agents, significantly improve resilience. CPOA manages overall workflow recovery.
* **Security:** (As before) Secure Celery broker communication, database credentials.

This System Architecture document provides a foundational understanding. More detailed designs for each component and their interactions will be elaborated in subsequent architecture documents (e.g., `Agent_Orchestration.md`, `Data_Flows.md`).

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
