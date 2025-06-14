# Data_Flows.md

**Version:** 1.0
**Date:** May 19, 2025
**Status:** Draft

## 1. Introduction and Purpose

This document details the primary data flows within the Aethercast system. It aims to provide a clear understanding of how data is ingested, processed, transformed, stored, and transmitted between various components, including the user interface, API gateway, Central Podcast Orchestrator Agent (CPOA), specialized AI agents, AI model serving infrastructure, and data stores.

Understanding these data flows is crucial for system design, development, debugging, and identifying potential bottlenecks or security considerations.

## 2. General Data Handling Principles

* **Data Formats:**
    * **Inter-Service Communication (Backend):**
        *   **Asynchronous Operations (CPOA <-> TDA, SCA, PSWA, IGA, VFA):** CPOA dispatches Celery tasks. Arguments and results are JSON. CPOA polls HTTP GET endpoints (`/v1/tasks/<task_id>`) on these services to retrieve JSON results.
        *   **Synchronous Operations (e.g., PSWA/SCA/VFA -> AIMS/AIMS_TTS, CPOA -> ASF notify):** JSON over HTTP/HTTPS.
        *   **Idempotency Headers:** `X-Idempotency-Key` and `X-Workflow-ID` are passed as HTTP headers by the client to the API Gateway, then relayed by CPOA as parameters to the Celery tasks of idempotent backend services (TDA, SCA, PSWA, IGA, VFA). These services use the `X-Idempotency-Key` in conjunction with a shared `idempotency_keys` table in the PostgreSQL database to manage the state of operations and ensure exactly-once processing.
    * **Frontend-Backend Communication:** JSON over HTTPS.
    * **AI Model Interactions:** (As before).
* **Security in Transit:** (As before - HTTPS for external, TLS for internal HTTP, secure Celery broker).
* **Large Data Objects:** (As before - claim check pattern with GCS URIs is used, e.g., by VFA for audio).
* **Data Validation:** (As before).

## 3. Detailed Data Flow Scenarios

### 3.1. Data Flow 1: Landing Page Snippet Generation and Display

* **Trigger:** User navigates to the Aethercast landing page/application home screen.
* **Goal:** To dynamically generate and display a list of fresh, engaging podcast snippets to the user.
* **Actors/Components Involved:** User, Frontend (FEND), API Gateway (APIGW), Central Podcast Orchestrator Agent (CPOA), `TopicDiscoveryAgent` (TDA), `SnippetCraftAgent` (SCA), AI Model Serving Infrastructure (AIMS - LLM), Data Stores (DS). (Optional: `ImageGenerationAgent` (IGA) if cover art is included).

* **Sequence Diagram:**

    ```mermaid
    sequenceDiagram
        participant User
        participant FEND as Frontend UI
        participant APIGW as API Gateway
        participant CPOA as Central Orchestrator
        participant TDA_Celery as TDA Celery Task
        participant SCA_Celery as SCA Celery Task
        participant IGA_Celery as IGA Celery Task
        participant AIMS_LLM as AI Models (LLM)
        participant VertexAI_IMG as Vertex AI Imagen
        participant PG_DB as PostgreSQL DB (Topics, Idempotency)
        participant DUIA as DynamicUIAgent

        User->>FEND: Requests Landing Page
        FEND->>APIGW: GET /api/v1/snippets (Headers: X-Idempotency-Key, X-Workflow-ID - optional for GET)
        note right of FEND: Request may include pagination, personalization tokens
        APIGW->>CPOA: Forward request (incl. headers, user_id if auth)
        CPOA-->>PG_DB: Create CPOA WorkflowInstance
        CPOA->>CPOA: Determine topic needs

        %% Topic Discovery Sub-Flow
        CPOA->>TDA_Celery: dispatch_task(Discover Topics, IdempotencyKey_TDA, CPOA_WorkflowID)
        TDA_Celery-->>CPOA: tda_task_id (Celery AsyncResult)
        note right of CPOA: TDA Task internally handles Idempotency DB check/store for IdempotencyKey_TDA
        note right of CPOA: Poll TDA Task: GET /v1/tasks/{tda_task_id}
        TDA_Celery-->>PG_DB: Store Topics in topics_snippets
        TDA_Celery-->>CPOA: Result: TopicObjects (JSON Array)
        note right of CPOA: TDA Task internally updates Idempotency DB (Completed)

        %% Snippet Generation Sub-Flow (parallel for multiple snippets)
        loop For Each Topic
            CPOA->>SCA_Celery: dispatch_task(Craft Snippet, TopicObject, IdemKey_SCA, CPOA_WorkflowID)
            SCA_Celery-->>CPOA: sca_task_id
            note right of CPOA: SCA Task internally handles Idempotency DB check/store
            note right of CPOA: Poll SCA Task: GET /v1/tasks/{sca_task_id}
            SCA_Celery->>AIMS_LLM: Call AIMS for text generation
            AIMS_LLM-->>SCA_Celery: Generated text (title, snippet, image_prompt)
            SCA_Celery-->>CPOA: Result: SnippetDataObject (with image_prompt)
            note right of CPOA: SCA Task internally updates Idempotency DB (Completed)

            %% Image Generation Sub-Flow
            CPOA->>IGA_Celery: dispatch_task(Generate Image, image_prompt, IdemKey_IGA, CPOA_WorkflowID)
            IGA_Celery-->>CPOA: iga_task_id
            note right of CPOA: IGA Task internally handles Idempotency DB check/store
            note right of CPOA: Poll IGA Task: GET /v1/tasks/{iga_task_id}
            IGA_Celery->>VertexAI_IMG: Call Vertex AI Imagen
            VertexAI_IMG-->>IGA_Celery: Image GCS URI
            IGA_Celery-->>CPOA: Result: Image GCS URI
            note right of CPOA: IGA Task internally updates Idempotency DB (Completed)
            CPOA->>CPOA: Augment SnippetDataObject with image_url (GCS URI)
        end

        CPOA->>CPOA: Aggregate augmented SnippetDataObjects
        CPOA->>DUIA: Generate UI Definition (with SnippetDataObjects)
        DUIA-->>CPOA: UI Definition JSON
        CPOA-->>APIGW: UI Definition JSON (including CPOA workflow_id)
        APIGW-->>FEND: Response with UI Definition JSON (APIGW converts GCS URIs to Signed URLs)
        FEND->>User: Displays Landing Page
    ```

* **Data Exchanged & Formats:**
    1.  **FEND -> APIGW:** HTTP GET `/api/v1/snippets`. Headers: Optional `X-Idempotency-Key`, `X-Workflow-ID`, Auth token.
    2.  **APIGW -> CPOA:** Internal call. Passes headers, `user_id`. CPOA generates its own `workflow_id`.
    3.  **CPOA -> TDA_Celery (Task Dispatch):**
        * Celery task message via broker.
        * `kwargs` (JSON): `{ "query": "...", "limit": N, "idempotency_key": "key_from_client_or_cpoa_generated", "workflow_id": "cpoa_workflow_id" }`
    4.  **TDA_Celery -> CPOA (Result via Polling):**
        * CPOA polls `GET /v1/tasks/<tda_task_id>` on TDA service.
        * Response (JSON): `{ "status": "SUCCESS", "result": {"discovered_topics": [...] } }`
    5.  **CPOA -> SCA_Celery (Task Dispatch):**
        * Celery task message.
        * `kwargs` (JSON): `{ "topic_id": "...", ..., "idempotency_key": "unique_key_for_this_sca_task", "workflow_id": "cpoa_workflow_id" }`
    6.  **SCA_Celery -> AIMS_LLM:** Synchronous HTTP POST to AIMS. Payload: prompt, model params.
    7.  **AIMS_LLM -> SCA_Celery:** HTTP Response. JSON with generated text.
    8.  **SCA_Celery -> CPOA (Result via Polling):**
        * CPOA polls `GET /v1/tasks/<sca_task_id>` on SCA service.
        * Response (JSON): `{ "status": "SUCCESS", "result": {"snippet_id": ..., "title": ..., "text_content": ..., "cover_art_prompt": ...} }`
    9.  **CPOA -> IGA_Celery (Task Dispatch):**
        * Celery task message.
        * `kwargs` (JSON): `{ "prompt": "cover_art_prompt_from_sca", "idempotency_key": "unique_key_for_this_iga_task", "workflow_id": "cpoa_workflow_id" }`
    10. **IGA_Celery -> VertexAI_IMG:** API call to Google Vertex AI.
    11. **VertexAI_IMG -> IGA_Celery:** Image data/reference. IGA uploads to GCS.
    12. **IGA_Celery -> CPOA (Result via Polling):**
        * CPOA polls `GET /v1/tasks/<iga_task_id>` on IGA service.
        * Response (JSON): `{ "status": "SUCCESS", "result": {"image_url": "gs://bucket/image.png"} }`
    13. **CPOA -> APIGW:** Internal data (list of fully populated SnippetDataObjects, CPOA workflow_id).
        ```json
        {
            "workflow_id": "uuid-for-this-snippet-generation-workflow",
            "snippets": [
                {"snippet_id": "uuid", ... }
            ],
            "source": "generation"
        }
        ```
    14. **APIGW -> FEND:** HTTP Response. Payload: Same as CPOA to APIGW.

---

### 3.2. Data Flow 2: On-Demand Full Podcast Generation and Streaming

* **Trigger:** User clicks "Listen" on a podcast snippet or initiates a request for a podcast on a specific topic via API Gateway.
* **Goal:** Generate and make available a full podcast episode. The generation involves multiple asynchronous, idempotent Celery tasks.
* **Actors/Components Involved:** User, Frontend (FEND), API Gateway (APIGW), CPOA, `WebContentHarvesterAgent` (WCHA - as library), `PodcastScriptWeaverAgent` (PSWA - Celery Task), `VoiceForgeAgent` (VFA - Celery Task), `ImageGenerationAgent` (IGA - Celery Task, optional), AI Model Serving Infrastructure (AIMS - LLM, AIMS_TTS), Vertex AI Imagen, PostgreSQL DB (for CPOA state & Idempotency), Google Cloud Storage (GCS), Audio Stream Feeder (ASF).

* **Sequence Diagram:**
    ```mermaid
    sequenceDiagram
        participant User
        participant FEND as Frontend UI
        participant APIGW as API Gateway
        participant CPOA as Central Podcast Orchestrator
        participant WCHA as WebContentHarvester (Library)
        participant PSWA_Celery as PSWA Celery Task
        participant VFA_Celery as VFA Celery Task
        participant IGA_Celery as IGA Celery Task (Optional)
        participant AIMS_LLM as AI Model Serving (LLM)
        participant AIMS_TTS as AI Model Serving (TTS)
        participant VertexAI_IMG as Vertex AI Imagen
        participant PG_DB as PostgreSQL DB (CPOA State, Idempotency)
        participant ASF as AudioStreamFeeder

        User->>FEND: Request Podcast (Topic X)
        FEND->>APIGW: POST /api/v1/podcasts (Topic X, X-Idempotency-Key, X-Workflow-ID)
        APIGW->>CPOA: Generate podcast (Topic X, IdemKey, WorkflowID, UserContext)
        CPOA-->>PG_DB: Create WorkflowInstance (master CPOA workflow_id created here)

        CPOA->>WCHA: Harvest Content (sync library call)
        WCHA-->>CPOA: Harvested Content

        CPOA->>PSWA_Celery: dispatch_task(Weave Script, Content, Topic, Persona, IdemKey_PSWA, CPOA_WorkflowID)
        PSWA_Celery-->>CPOA: pswa_celery_task_id
        note right of CPOA: PSWA Task internally handles Idempotency DB check/store
        note right of CPOA: Poll PSWA Task: GET /tasks/{pswa_celery_task_id}
        PSWA_Celery->>AIMS_LLM: Generate Script
        AIMS_LLM-->>PSWA_Celery: Script
        note right of PSWA_Celery: PSWA Task internally updates Idempotency DB (Completed)
        PSWA_Celery-->>CPOA: Final Script (result)

        CPOA->>VFA_Celery: dispatch_task(Forge Voice, Script, VoiceParams, IdemKey_VFA, CPOA_WorkflowID)
        VFA_Celery-->>CPOA: vfa_celery_task_id
        note right of CPOA: VFA Task internally handles Idempotency DB check/store
        note right of CPOA: Poll VFA Task: GET /tasks/{vfa_celery_task_id}
        VFA_Celery->>AIMS_TTS: Synthesize Audio (now an async call itself, VFA polls AIMS_TTS)
        AIMS_TTS-->>VFA_Celery: Audio GCS URI
        note right of VFA_Celery: VFA Task internally updates Idempotency DB (Completed)
        VFA_Celery-->>CPOA: Audio GCS URI (result)

        %% Optional Image Generation
        CPOA->>IGA_Celery: dispatch_task(Generate Image, Prompt, IdemKey_IGA, CPOA_WorkflowID)
        IGA_Celery-->>CPOA: iga_celery_task_id
        note right of CPOA: IGA Task internally handles Idempotency DB check/store
        Note right of CPOA: Poll IGA Task
        IGA_Celery->>VertexAI_IMG: Generate Image
        VertexAI_IMG-->>IGA_Celery: Image GCS URI
        note right of IGA_Celery: IGA Task internally updates Idempotency DB (Completed)
        IGA_Celery-->>CPOA: Image GCS URI (result)

        CPOA->>ASF: HTTP POST /asf/internal/notify_new_audio (GCS URI, StreamID)
        CPOA-->>PG_DB: Update WorkflowInstance (Completed, GCS URIs)

        APIGW-->>FEND: task_id (CPOA workflow_id or initial podcast_id for client to track)
        note left of FEND: Client polls API GW task status endpoint for CPOA workflow.
        note left of FEND: When audio ready, FEND connects to ASF WebSocket using StreamID.
    ```

* **Data Exchanged & Formats:**
    1.  **FEND -> APIGW:** HTTP POST `/api/v1/podcasts`. Headers: `X-Idempotency-Key`, `X-Workflow-ID` (optional), Auth Token. Body (JSON): `{"topic_id": "xyz", "user_preferences": {...}}`
    2.  **APIGW -> CPOA:** Internal call. Passes headers, `user_id`, topic data. CPOA generates its own master `workflow_id`.
    3.  **CPOA (Initial Response Path) -> APIGW -> FEND:** HTTP 202 Accepted. Body (JSON): `{"podcast_id": "cpoa_workflow_id", "workflow_id": "cpoa_workflow_id", "status": "GENERATING", ...}`. (Client uses `podcast_id` or `workflow_id` to poll main task status via API GW).
    4.  **CPOA -> WCHA (Library Call):** Direct function call with topic string. Returns harvested text.
    5.  **CPOA -> PSWA_Celery (Task Dispatch):** Celery task. `kwargs` (JSON): `{..., "idempotency_key": "client_idem_key_for_pswa", "workflow_id": "cpoa_workflow_id"}`.
    6.  **PSWA_Celery -> CPOA (Result via Polling):** CPOA polls `GET /tasks/<pswa_celery_task_id>` on PSWA. Response (JSON): `{ "status": "SUCCESS", "result": {PodcastScript object} }`.
    7.  **CPOA -> VFA_Celery (Task Dispatch):** Celery task. `kwargs` (JSON): `{..., "script_object": {...}, "idempotency_key": "client_idem_key_for_vfa", "workflow_id": "cpoa_workflow_id"}`.
    8.  **VFA_Celery -> AIMS_TTS:** VFA's Celery task makes async HTTP calls to AIMS_TTS and polls it.
    9.  **AIMS_TTS -> VFA_Celery:** AIMS_TTS task result (JSON with GCS URI).
    10. **VFA_Celery -> CPOA (Result via Polling):** CPOA polls `GET /tasks/<vfa_celery_task_id>` on VFA. Response (JSON): `{ "status": "SUCCESS", "result": {"audio_filepath": "gs://...", ...} }`.
    11. **CPOA -> IGA_Celery (Task Dispatch, Optional):** Similar async pattern with idempotency keys.
    12. **IGA_Celery -> CPOA (Result via Polling, Optional):** Similar async pattern.
    13. **CPOA -> ASF (HTTP Call):** `POST /asf/internal/notify_new_audio`. Body (JSON): `{"stream_id": "...", "filepath": "gcs_uri_from_vfa"}`.
    14. **Client (FEND) -> API_GW -> CPOA (Polling for overall status):** Client polls `GET /api/v1/podcasts/<cpoa_workflow_id>` (or similar endpoint that tracks CPOA workflow). API GW gets updates from CPOA's PostgreSQL `workflow_instances` table.
    15. **Client (FEND) -> ASF (WebSocket):** Connects to ASF for audio streaming once GCS URI and stream ID are available and ASF is notified.

---

## 4. Data Storage Overview (Data At Rest)

The primary database for structured data like CPOA state, idempotency records, and topic/snippet metadata is **PostgreSQL**. Google Cloud Storage (GCS) is used for media files.

* **User Session State:**
    * **Storage:** Key-value store (e.g., Redis, or potentially PostgreSQL `user_sessions` table managed by API Gateway).
* **CPOA Workflow & Task State:**
    * **Data:** `workflow_instances` and `task_instances` as defined in `docs/architecture/CPOA_State_Management.md`.
    * **Storage:** PostgreSQL database.
* **Idempotency Keys Table (`idempotency_keys`):**
    * **Data:** `idempotency_key`, `task_name`, `workflow_id`, `status`, `result_payload`, `error_payload`, timestamps.
    * **Storage:** Shared PostgreSQL database. Used by TDA, SCA, PSWA, IGA, VFA.
* **Discovered Topics & Snippets Metadata (`topics_snippets` table):**
    * **Data:** `TopicObjects` from TDA, `SnippetDataObjects` (text by SCA, image GCS URI by IGA).
    * **Storage:** PostgreSQL database (managed by TDA for initial topic creation, CPOA updates with snippet/image details).
* **Podcast Scripts Cache (`generated_scripts` table - PSWA):**
    * **Data:** Full `PodcastScripts` generated by PSWA.
    * **Storage:** Configurable by PSWA (SQLite file, or preferably the shared PostgreSQL database).
* **Media Files (Audio, Images):**
    * **Data:** MP3/Opus audio files, PNG/JPEG images.
    * **Storage:** Google Cloud Storage (GCS). Referenced by GCS URIs (e.g., `gs://bucket-name/...`) in PostgreSQL metadata tables.

## 5. Error State Data Flows (Brief Overview)

* When an agent's Celery task (TDA, SCA, PSWA, IGA, VFA) fails:
    * The agent's `on_failure` handler (if defined, like in `ScaCeleryTask`, `TdaCeleryTask`, etc.) updates the corresponding record in the `idempotency_keys` table to "failed", storing error details in `error_payload`.
* CPOA, while polling the agent's `/v1/tasks/<celery_task_id>` endpoint, will receive a `FAILURE` status from Celery (or a success status with an error payload if the task handles its own errors gracefully before Celery marks it as failed).
* CPOA updates its own `task_instances` record for that agent call to reflect the failure, storing detailed error information.
* CPOA's workflow logic then decides on retries. If retrying, CPOA will re-dispatch the Celery task using the **same `X-Idempotency-Key`** as the original attempt.
* If an operation cannot be completed after retries or due to non-recoverable errors, the overall CPOA `workflow_instance` is marked as "failed" or "completed_with_errors".
* User-friendly error messages are relayed to the client via the API Gateway.

This document provides a detailed view of the data flows. It should be updated as the system evolves and specific implementation choices for communication channels and data formats are finalized.

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
