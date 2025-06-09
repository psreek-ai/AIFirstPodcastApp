# Data_Flows.md

**Version:** 1.0
**Date:** May 19, 2025
**Status:** Draft

## 1. Introduction and Purpose

This document details the primary data flows within the Aethercast system. It aims to provide a clear understanding of how data is ingested, processed, transformed, stored, and transmitted between various components, including the user interface, API gateway, Central Podcast Orchestrator Agent (CPOA), specialized AI agents, AI model serving infrastructure, and data stores.

Understanding these data flows is crucial for system design, development, debugging, and identifying potential bottlenecks or security considerations.

## 2. General Data Handling Principles

* **Data Formats:**
    * **Inter-Service Communication (Backend):** JSON is the primary format for synchronous API calls (RESTful) and message payloads in asynchronous messaging (e.g., RabbitMQ, Kafka). Protocol Buffers (Protobuf) may be used for gRPC-based communication where performance and schema enforcement are critical between internal microservices.
    * **Frontend-Backend Communication:** JSON is used for API requests and responses. Audio data is streamed in standard audio formats (e.g., MP3 segments, Opus).
    * **AI Model Interactions:** Input prompts to LLMs are typically text or structured JSON. Outputs from LLMs are text or JSON. TTS inputs are text/SSML, outputs are raw audio data.
* **Security in Transit:**
    * All external communication (User to Frontend, Frontend to API Gateway) will use HTTPS.
    * All internal backend communication between services (API Gateway to CPOA, CPOA to Agents, Agents to AIMS) should use TLS/mTLS encryption, even within a trusted network.
    * Message queues should be configured with transport layer security.
* **Large Data Objects:**
    * For very large data payloads (e.g., extensive harvested web content from `WebContentHarvesterAgent`, full podcast scripts before TTS if exceptionally long, or full audio files if not streamed chunk-by-chunk), a "claim check" pattern might be used:
        1.  The producing agent stores the large object in a dedicated object store (e.g., AWS S3, Google Cloud Storage).
        2.  A reference (e.g., URI to the object) is passed in messages or API calls.
        3.  The consuming agent retrieves the object directly from the object store using the reference.
    * Audio streaming will inherently handle large audio data by breaking it into manageable chunks.
* **Data Validation:**
    * All components (API Gateway, CPOA, each specialized agent) must validate incoming data for schema, type, and business rules before processing.

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
        participant TDA as TopicDiscoveryAgent
        participant SCA as SnippetCraftAgent
        participant AIMS_LLM as AI Models (LLM)
        participant DS as Data Stores
        %% Optional: participant IGA as ImageGenerationAgent
        %% Optional: participant AIMS_IMG as AI Models (Image)

        User->>FEND: Requests Landing Page
        FEND->>APIGW: GET /api/v1/snippets (or similar)
        note right of FEND: Request may include pagination, personalization tokens (future)
        APIGW->>CPOA: Forward GET /api/v1/snippets request
        CPOA->>CPOA: Determine topic needs (fresh vs. cached)

        %% Topic Discovery Sub-Flow (may happen periodically or on-demand)
        CPOA->>TDA: Request Topics (e.g., {count: 10, categories: ["tech", "science"]})
        note right of CPOA: Communication via Message Queue (Async) or API (Sync)
        TDA->>ExternalWeb: Query news APIs, trend services
        ExternalWeb-->>TDA: Raw trend/news data (JSON/XML/HTML)
        TDA->>TDA: Process & Rank Topics
        TDA-->>CPOA: TopicObjects (JSON Array, e.g., [{topic_id, title_suggestion, summary, keywords}])
        CPOA->>DS: Store/Cache TopicObjects (if new)

        %% Snippet Generation Sub-Flow (parallel for multiple snippets)
        CPOA->>SCA: Generate Snippet (Task with TopicObject)
        note right of CPOA: For each topic, via Message Queue (Async) or API (Sync)
        SCA->>AIMS_LLM: Prompt for snippet text & title (JSON/Text)
        AIMS_LLM-->>SCA: Generated text (JSON containing title, snippet_text)
        %% Optional Image Prompt Generation
        %% SCA->>AIMS_LLM: Prompt for image description
        %% AIMS_LLM-->>SCA: Image description text
        SCA-->>CPOA: SnippetDataObject (JSON, e.g., {snippet_id, topic_id, title, text_content, cover_art_prompt})

        %% Optional Image Generation Sub-Flow
        %% loop For each SnippetDataObject with cover_art_prompt
        %%    CPOA->>IGA: Generate Image (Task with cover_art_prompt)
        %%    IGA->>AIMS_IMG: Prompt for image generation
        %%    AIMS_IMG-->>IGA: Image data/URL
        %%    IGA-->>CPOA: Image URL
        %%    CPOA->>CPOA: Associate Image URL with SnippetDataObject
        %% end

        CPOA->>CPOA: Aggregate SnippetDataObjects
        CPOA-->>APIGW: List of SnippetDataObjects (JSON Array)
        APIGW-->>FEND: Response with SnippetDataObjects (JSON)
        FEND->>FEND: Process data, render snippets
        FEND->>User: Displays Landing Page
    ```

* **Data Exchanged & Formats:**
    1.  **FEND -> APIGW:** HTTP GET request. Path: `/api/v1/snippets`. Headers: Auth token (if applicable).
    2.  **APIGW -> CPOA:** Internal request (e.g., HTTP/gRPC call or message queue event) mirroring the frontend request. If the user is authenticated or an `Authorization` token is provided opportunistically, the `user_id` is extracted by the API Gateway and passed to CPOA.
    3.  **CPOA -> TDA (Task):**
        * Channel: Message Queue (e.g., `topic_discovery_tasks`) or direct API call.
        * Payload (JSON): `{ "task_id": "uuid", "requested_count": 10, "filter_criteria": {"categories": ["tech"], "min_recency_hours": 24}, "reply_to_queue": "cpoa_topic_results" }`
    4.  **TDA -> External Web:** HTTP GET requests to news APIs, web pages.
    5.  **External Web -> TDA:** Raw data (HTML, JSON, XML).
    6.  **TDA -> CPOA (Result):**
        * Channel: Message Queue (e.g., `cpoa_topic_results`) or API response.
        * Payload (JSON Array of `TopicObjects`): `[ { "topic_id": "uuid", "title_suggestion": "...", "summary": "...", "keywords": ["...", "..."], "potential_sources": [{"url": "...", "title": "..."}], "relevance_score": 0.85 } ]`
    7.  **CPOA -> DS (Topic Storage):** `TopicObjects` stored (e.g., in a NoSQL document store or relational DB).
    8.  **CPOA -> SCA (Task):**
        * Channel: Message Queue (e.g., `snippet_craft_tasks`) or direct API call.
        * Payload (JSON): `{ "task_id": "uuid", "topic_object": { ...from_TDA... }, "output_parameters": {"max_length_chars": 300, "style_persona_id": "engaging_teaser"}, "reply_to_queue": "cpoa_snippet_results" }`
    9.  **SCA -> AIMS (LLM):**
        * Channel: Secure API call (HTTPS/gRPC).
        * Payload (JSON/Text): Prompt engineered from `topic_object` and `output_parameters`. Example: `{"prompt": "Create a compelling 2-sentence teaser and a catchy title for the topic: 'AI in Renewable Energy Management'. Focus on recent breakthroughs. Style: Enthusiastic but informative.", "max_tokens": 100}`
    10. **AIMS (LLM) -> SCA:**
        * Payload (JSON): `{"title": "AI Supercharges Green Energy!", "snippet_text": "Discover how artificial intelligence is revolutionizing renewable energy, optimizing grids and accelerating the transition to a sustainable future. Recent innovations are game-changers!", "finish_reason": "stop"}`
    11. **SCA -> CPOA (Result):**
        * Channel: Message Queue (e.g., `cpoa_snippet_results`) or API response.
        * Payload (JSON `SnippetDataObject`): `{"snippet_id": "uuid", "topic_id": "uuid", "title": "AI Supercharges Green Energy!", "text_content": "...", "cover_art_prompt": "Futuristic wind turbines and solar panels with glowing AI data streams", "generation_timestamp": "iso_datetime"}`
    12. **CPOA -> APIGW:** HTTP Response. Payload: JSON dictionary containing `workflow_id` and a list of `SnippetDataObjects`.
        ```json
        {
            "workflow_id": "uuid-for-this-snippet-generation-workflow",
            "snippets": [
                {"snippet_id": "uuid", ... }
            ],
            "source": "generation"
        }
        ```
    13. **APIGW -> FEND:** HTTP Response. Payload: Same as CPOA to APIGW.

---

### 3.2. Data Flow 2: On-Demand Full Podcast Generation and Streaming

* **Trigger:** User clicks "Listen" on a podcast snippet or directly requests a podcast for a given topic.
* **Goal:** To dynamically generate a full podcast episode based on the topic, sourcing live web content, and stream the audio to the user in real-time.
* **Actors/Components Involved:** User, Frontend (FEND), API Gateway (APIGW), CPOA, `WebContentHarvesterAgent` (WCHA), `PodcastScriptWeaverAgent` (PSWA), `VoiceForgeAgent` (VFA), AI Model Serving Infrastructure (AIMS - LLM, AIMS_TTS), Data Stores (DS), External Web, Audio Streaming Service (conceptual, could be part of VFA or CPOA's responsibility to deliver to FEND).

* **Sequence Diagram:**

    ```mermaid
    sequenceDiagram
        participant User
        participant FEND as Frontend UI
        participant APIGW as API Gateway
        participant CPOA as Central Orchestrator
        participant WCHA as WebContentHarvester
        participant PSWA as PodcastScriptWeaver
        participant VFA as VoiceForgeAgent
        participant AIMS_LLM as AI Models (LLM)
        participant AIMS_TTS as AI Models (TTS)
        participant ExtWeb as External Web
        participant DS as Data Stores
        participant StreamFeeder as Audio Stream Feeder (Conceptual)

        User->>FEND: Clicks "Listen" (Topic ID: "xyz")
        FEND->>APIGW: POST /api/v1/podcasts/generate (Body: {topicId: "xyz"})
        note right of FEND: Request may include preferred persona, length hints
        APIGW->>CPOA: Forward POST request with topicId

        %% Web Content Harvesting
        CPOA->>WCHA: Task: Harvest Content (Topic ID: "xyz", params: {depth: 3, recency_days: 7})
        note right of CPOA: Async via Message Queue
        WCHA->>ExtWeb: HTTP GET requests (search engines, specific sites)
        ExtWeb-->>WCHA: HTML/JSON/XML content
        WCHA->>WCHA: Process & Extract Text
        WCHA-->>CPOA: Result: HarvestedContentBundle (JSON, potentially with S3 links for large content)
        CPOA->>DS: (Optional) Cache HarvestedContentBundle

        %% Script Weaving
        CPOA->>PSWA: Task: Weave Script (HarvestedContentBundle, params: {persona_id: "expert", target_minutes: 10})
        note right of CPOA: Async via Message Queue
        PSWA->>AIMS_LLM: Prompt for script generation (Text/JSON, including harvested content)
        AIMS_LLM-->>PSWA: Generated Script (Text/JSON)
        PSWA-->>CPOA: Result: PodcastScript (JSON)
        CPOA->>DS: (Optional) Store PodcastScript metadata

        %% Voice Forging & Streaming
        CPOA->>VFA: Task: Forge Voice (PodcastScript, params: {voice_id: "aura", format: "opus_segments"})
        note right of CPOA: Async via Message Queue, or direct call to initiate streaming
        FEND->>StreamFeeder: GET /api/v1/podcasts/stream/{stream_id} (WebSocket or HTTP Streaming)
        note left of FEND: Frontend establishes connection for audio stream
        CPOA->>StreamFeeder: Provide Stream ID to FEND (via APIGW initial response or separate message)

        VFA->>AIMS_TTS: Text Segments for TTS
        AIMS_TTS-->>VFA: Audio Chunks (raw audio data)
        VFA->>StreamFeeder: Push Audio Chunks to {stream_id}
        StreamFeeder-->>FEND: Stream Audio Chunks
        FEND->>User: Plays Podcast Audio

        %% Status updates (simplified)
        CPOA-->>APIGW: Initial Response (e.g., {status: "generating", stream_id: "abc"})
        APIGW-->>FEND: Initial Response
    ```

* **Data Exchanged & Formats:**
    1.  **FEND -> APIGW:** HTTP POST `/api/v1/podcasts/generate`. Body (JSON): `{"topic_id": "xyz", "user_preferences": {"persona_id": "friendly_explainer"}}`
    2.  **APIGW -> CPOA:** Internal request forwarding the above, including the authenticated `user_id`. The `podcast_id` generated by API Gateway is passed as `original_task_id` to CPOA.
    3.  **CPOA (Initial Response Path) -> APIGW -> FEND:** HTTP Response (JSON):
        ```json
        {
            "podcast_id": "original_task_id_from_apigw",
            "workflow_id": "uuid-for-this-podcast-generation-workflow",
            "status": "GENERATING",
            "stream_id": "unique_stream_id_for_client_to_connect_to",
            "estimated_wait_time_seconds": 15
        }
        ```
        (The `stream_id` is crucial for the client to connect for audio. `workflow_id` allows tracking).
    4.  **CPOA -> WCHA (Task):**
        * Channel: Message Queue (e.g., `content_harvest_tasks`).
        * Payload (JSON): `{"task_id": "uuid", "topic_id": "xyz", "constraints": {"max_sources": 5, "recency_days": 7, "depth_level": 2}, "reply_to_queue": "cpoa_harvest_results"}`
    5.  **WCHA -> External Web:** HTTP GET requests.
    6.  **External Web -> WCHA:** HTML, JSON, XML.
    7.  **WCHA -> CPOA (Result):**
        * Channel: Message Queue (e.g., `cpoa_harvest_results`).
        * Payload (JSON `HarvestedContentBundle`): `{"task_id": "uuid", "status": "COMPLETED", "content": { "topic_id": "xyz", "sources": [{"url": "...", "cleaned_text": "...", "title": "..."}] }}`. (If very large, `cleaned_text` might be an S3 URI).
    8.  **CPOA -> PSWA (Task):**
        * Channel: Message Queue (e.g., `script_weave_tasks`).
        * Payload (JSON): `{"task_id": "uuid", "harvested_content_ref": "uri_or_inline_content", "topic_details": {...}, "script_parameters": {"persona_id": "friendly_explainer", "target_duration_minutes": 8, "style": "conversational"}, "reply_to_queue": "cpoa_script_results"}`
    9.  **PSWA -> AIMS (LLM):**
        * Channel: Secure API call.
        * Payload (JSON/Text): Extensive prompt containing processed web content, instructions for structure, persona, style.
    10. **AIMS (LLM) -> PSWA:**
        * Payload (JSON/Text): The generated podcast script.
    11. **PSWA -> CPOA (Result):**
        * Channel: Message Queue (e.g., `cpoa_script_results`).
        * Payload (JSON `PodcastScript`): `{"task_id": "uuid", "status": "COMPLETED", "script": {"title": "...", "full_text": "...", "segments": [...]}}`
    12. **CPOA -> VFA (Task):**
        * Channel: Message Queue or direct API call to initiate streaming.
        * Payload (JSON): `{"task_id": "uuid", "script_object": { ...from_PSWA... }, "voice_parameters": {"voice_id": "persona_voice_xyz", "audio_format": "opus", "chunk_duration_ms": 5000}, "stream_id": "unique_stream_id_for_client"}`
    13. **FEND -> Audio Stream Feeder (Conceptual - could be VFA, CPOA, or dedicated service):**
        * Channel: WebSocket connection or HTTP long-polling/streaming request to ` /api/v1/podcasts/stream/{stream_id}`.
    14. **VFA -> AIMS (TTS):**
        * Channel: Secure API call.
        * Payload (Text/SSML): Segments of the script.
    15. **AIMS (TTS) -> VFA:**
        * Payload: Raw audio data chunks (e.g., PCM, Opus frames).
    16. **VFA -> Audio Stream Feeder:** Pushes audio chunks.
    17. **Audio Stream Feeder -> FEND:**
        * Channel: WebSocket messages or HTTP stream.
        * Payload: Audio data chunks (e.g., Opus packets, MP3 segments).

---

## 4. Data Storage Overview (Data At Rest)

Referencing `Data Stores (DS)` from `System_Architecture.md`:

* **User Session State:**
    * **Data:** Active user context (e.g., current topic interest, partial interactions for resumability).
    * **Storage:** Key-value store (e.g., Redis) for fast access, keyed by session ID.
    * **Format:** JSON.
* **Generated Content Metadata:**
    * **Data:** Information about `TopicObjects`, `SnippetDataObjects`, `PodcastScripts` (e.g., IDs, titles, text, source references, generation parameters, timestamps, (optional) S3 URIs to full content if not stored directly).
    * **Storage:** NoSQL Document Store (e.g., MongoDB, Firestore) or Relational Database (e.g., PostgreSQL). Chosen for querying and relational needs if any.
    * **Format:** JSON documents or structured relational tables.
* **Agent Task & Workflow State (Managed by CPOA):**
    * **Data:** Status of ongoing and completed orchestration workflows (`workflow_instances` table: `workflow_id`, `user_id`, `trigger_event_type`, `overall_status`, etc.) and individual agent tasks (`task_instances` table: `task_id`, `workflow_id`, `agent_name`, `status`, `input_params_json`, `output_result_summary_json`, etc.).
    * **Storage:** PostgreSQL database, as defined in `docs/architecture/CPOA_State_Management.md`.
    * **Format:** Structured data in relational tables with JSONB fields for flexible details.
* **Topic Cache:**
    * **Data:** Recently discovered or frequently accessed `TopicObjects`.
    * **Storage:** Cache (e.g., Redis, Memcached) or a table in the main `Data Stores` with TTL.
    * **Format:** JSON.
* **Script Cache (Optional):**
    * **Data:** Full `PodcastScripts` if caching is implemented to serve identical requests quickly.
    * **Storage:** Cache or object store, linked from metadata.
    * **Format:** Text/JSON.
* **Large Binary Objects (e.g., full harvested web content, intermediate audio files if not purely streamed):**
    * **Storage:** Object Storage (e.g., AWS S3, Google Cloud Storage). Referenced by URI in metadata stores.

## 5. Error State Data Flows (Brief Overview)

* When an agent fails a task, it reports an error object/message back to the CPOA (via its reply queue or API response).
* Error Object (JSON): `{"task_id": "uuid", "status": "FAILED", "error_code": "AGENT_TIMEOUT_ERROR", "error_message": "Agent X timed out", "error_details": {...}}`
* CPOA logs this error and initiates error handling logic (retry, fallback, inform user).
* Error messages to the user (via FEND) are user-friendly, not raw technical errors, e.g., `{"user_message": "Sorry, we couldn't generate this podcast right now. Please try another."}`.

This document provides a detailed view of the data flows. It should be updated as the system evolves and specific implementation choices for communication channels and data formats are finalized.
