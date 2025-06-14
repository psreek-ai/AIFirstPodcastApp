# Data Stores in Aethercast

This document provides an overview of the data storage mechanisms implemented in the Aethercast system, with a primary focus on the central PostgreSQL database.

## Primary Data Store: PostgreSQL

The Aethercast application has transitioned to primarily using a **PostgreSQL database** as its central, persistent data store. This database is run as a Docker container (`postgres_db`) within the Docker Compose environment, and its data is persisted using a Docker volume (`postgres_data`).

This PostgreSQL instance is accessed by multiple services and stores critical data for:

*   **CPOA Workflow and Task State:**
    *   `workflow_instances`: Tracks high-level orchestration flows managed by the Central Podcast Orchestrator Agent (CPOA).
    *   `task_instances`: Tracks individual agent calls and steps within each CPOA workflow.
    *   *Schema Details:* `docs/architecture/CPOA_State_Management.md`
*   **Shared Idempotency Records:**
    *   `idempotency_keys`: A shared table used by asynchronous backend services (TDA, SCA, PSWA, IGA, VFA) to ensure their Celery tasks are processed idempotently based on client-provided `X-Idempotency-Key` and CPOA-provided `X-Workflow-ID`.
    *   *Schema Details:* `aethercast/data_stores/migrations/001_create_idempotency_keys_table.sql`
*   **Topic and Snippet Data (TDA & CPOA):**
    *   `topics_snippets`: Stores discovered topics (by TDA) and generated snippet metadata (text from SCA, image GCS URIs from IGA, managed by CPOA).
    *   *Schema Details:* Defined in `aethercast/tda/main.py` (variable `DB_SCHEMA_TDA_TABLES`).
*   **User Accounts, Sessions, and Subscriptions (API Gateway):**
    *   `users`: Stores user registration data.
    *   `user_sessions`: Manages user session identifiers and preferences.
    *   `subscribers`: Stores email addresses for subscriptions.
    *   *Schema Details:* Defined within `aethercast/api_gateway/main.py`.
*   **Podcast Script Cache (PSWA - Optional):**
    *   `generated_scripts`: If PSWA is configured with `DATABASE_TYPE=postgres`, it uses this table in the PostgreSQL database to cache generated podcast scripts.
    *   *Schema Details:* Defined in `aethercast/pswa/main.py`.

### Rationale for PostgreSQL
PostgreSQL was chosen as the primary central database due to:
-   Its robustness and reliability for relational data.
-   Support for advanced data types like JSONB, which is useful for storing flexible metadata (e.g., in CPOA state tables, idempotency records).
-   Strong transactional capabilities, essential for maintaining consistency in workflow state and idempotency tracking.
-   Better concurrency handling compared to SQLite for a multi-service architecture.
-   Scalability options for future growth.

### SQLite Usage (Service-Specific Caching)
-   **PSWA Script Cache:** The Podcast Script Weaver Agent (PSWA) can still be configured to use SQLite for its script cache (`DATABASE_TYPE=sqlite` in PSWA's environment, using `SHARED_DATABASE_PATH`). This is a service-specific choice for local caching if a lighter option is preferred for that particular dataset and PostgreSQL is not desired for PSWA's cache.
-   **Legacy/Other:** Other services do not use SQLite for shared state anymore. Any remaining SQLite usage would be for purely local, non-shared purposes within a service, if any.

## Other Data Storage

*   **Google Cloud Storage (GCS):** Used as the primary object store for large binary files, specifically:
    *   Generated audio files (e.g., MP3, OGG Opus) from the AIMS_TTS service (via VFA).
    *   Generated images (e.g., PNG, JPEG) from the Image Generation Agent (IGA).
    *   These files are referenced by their GCS URIs (e.g., `gs://bucket-name/path/to/file`) in the PostgreSQL metadata tables.
*   **Redis:**
    *   Acts as the **Celery message broker** for all asynchronous tasks (TDA, SCA, PSWA, IGA, VFA).
    *   Can also be used as the **Celery result backend**.
    *   Potentially used for other caching purposes (e.g., user session data if not stored in PostgreSQL by API Gateway, general-purpose caching by services).

## Migrations
Database schema migrations, especially for shared tables like `idempotency_keys`, are managed via SQL scripts located in `aethercast/data_stores/migrations/`. These need to be applied manually to the PostgreSQL database after it's initialized (see main project README for setup instructions). Service-specific tables like `topics_snippets` (TDA) or `generated_scripts` (PSWA) might be created by the services themselves on startup if they don't exist (e.g., via `init_tda_db()`).

## Conceptual Future Data Stores (Legacy Notes)
The markdown files within this directory (e.g., `nosql_agent_task_state.md`, `key_value_store_definitions.md`) describe conceptual data models that were considered earlier in the project. With the standardization on PostgreSQL for core structured data and GCS for media, these primarily serve as historical context or for very specialized future needs. Always refer to the current service implementations and the main PostgreSQL schema for the source of truth.

---

*For information on the overarching Aethercast project architecture, detailed setup instructions (including database migrations), and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
