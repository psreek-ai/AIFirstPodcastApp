# CPOA State Management Database Schema

## 1. Introduction

The Central Podcast Orchestrator Agent (CPOA) manages complex, multi-step workflows to generate podcasts and other content. To provide robust tracking, observability, debugging capabilities, and to lay the groundwork for future enhancements like workflow resumption, a dedicated state management schema is required.

These tables are designed for **PostgreSQL** and will track the overall state of each workflow initiated within CPOA and the state of individual tasks (agent calls) within those workflows. The same PostgreSQL database instance also hosts the shared `idempotency_keys` table, which is used by downstream services (TDA, SCA, PSWA, IGA, VFA) to ensure their operations are idempotent. CPOA plays a key role in propagating the necessary idempotency headers to these services.

## 2. Table Definitions

### 2.1. `workflow_instances` Table

This table stores information about each high-level workflow instance triggered in CPOA. A workflow could be the generation of a full podcast, the creation of landing page snippets, or a search operation.

**SQL DDL:**

```sql
CREATE TABLE workflow_instances (
    workflow_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    trigger_event_type VARCHAR(255) NOT NULL,
    trigger_event_details_json JSONB,
    overall_status VARCHAR(50) NOT NULL,
    start_timestamp TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    end_timestamp TIMESTAMPTZ,
    last_updated_timestamp TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    context_data_json JSONB,
    error_message TEXT
);
```

**Column Descriptions:**

| Column                       | Type          | Constraints                                       | Purpose                                                                                                |
|------------------------------|---------------|---------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| `workflow_id`                | UUID          | Primary Key, DEFAULT `gen_random_uuid()`          | Unique identifier for the workflow instance. This ID is passed by CPOA as the `X-Workflow-ID` header to downstream idempotent services, linking their idempotency records to this CPOA workflow. |
| `user_id`                    | UUID          | Foreign Key to `users.user_id`, ON DELETE SET NULL, Nullable | Identifier of the user who initiated the workflow, if applicable. Set to NULL if the user is deleted. |
| `trigger_event_type`         | VARCHAR(255)  | NOT NULL                                          | Type of event that triggered the workflow (e.g., "api_podcast_generation", "api_landing_page_snippets", "api_search"). |
| `trigger_event_details_json` | JSONB         | Nullable                                          | Stores the initial parameters or payload that triggered the workflow (e.g., API request body, including client-provided `X-Idempotency-Key` if applicable for the top-level CPOA operation, though CPOA itself isn't idempotent in the same way as backend task agents).        |
| `overall_status`             | VARCHAR(50)   | NOT NULL                                          | Current overall status of the workflow.                                                                |
| `start_timestamp`            | TIMESTAMPTZ   | NOT NULL, DEFAULT `current_timestamp`             | Timestamp when the workflow instance was created/started.                                              |
| `end_timestamp`              | TIMESTAMPTZ   | Nullable                                          | Timestamp when the workflow instance concluded (completed or failed).                                  |
| `last_updated_timestamp`     | TIMESTAMPTZ   | NOT NULL, DEFAULT `current_timestamp`             | Timestamp of the last update to this workflow instance record.                                         |
| `context_data_json`          | JSONB         | Nullable                                          | Stores evolving data relevant to the workflow, shared across tasks (e.g., generated GCS URIs, intermediate results). |
| `error_message`              | TEXT          | Nullable                                          | Stores a top-level error message if the entire workflow failed catastrophically.                       |

### 2.2. `task_instances` Table

This table stores information about each individual task executed as part of a workflow. A task typically represents a call to a specific agent (e.g., TDA, SCA, PSWA).

**SQL DDL:**

```sql
CREATE TABLE task_instances (
    task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES workflow_instances(workflow_id) ON DELETE CASCADE,
    external_celery_task_id VARCHAR(255) NULL, -- Store Celery task ID from downstream async services
    agent_name VARCHAR(255) NOT NULL,
    task_order INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL,
    input_params_json JSONB,
    output_result_summary_json JSONB,
    error_details_json JSONB,
    start_timestamp TIMESTAMPTZ,
    end_timestamp TIMESTAMPTZ,
    last_updated_timestamp TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    retry_count INTEGER NOT NULL DEFAULT 0
);
```

**Column Descriptions:**

| Column                         | Type         | Constraints                                                          | Purpose                                                                                                    |
|--------------------------------|--------------|----------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| `task_id`                      | UUID         | Primary Key, DEFAULT `gen_random_uuid()`                             | Unique identifier for this CPOA task instance record (CPOA's internal ID for this step).                                                                   |
| `workflow_id`                  | UUID         | NOT NULL, Foreign Key to `workflow_instances.workflow_id`, ON DELETE CASCADE | Identifier of the parent workflow this task belongs to.                                                    |
| `external_celery_task_id`      | VARCHAR(255) | Nullable                                                             | Stores the Celery task ID returned by downstream asynchronous services (TDA, SCA, PSWA, IGA, VFA). Used by CPOA for polling the actual agent task. |
| `agent_name`                   | VARCHAR(255) | NOT NULL                                                             | Name of the agent or component responsible for this task (e.g., "TDA", "SCA", "PSWA", "IGA", "ASF_NOTIFY"). |
| `task_order`                   | INTEGER      | NOT NULL                                                             | Sequence number of this task within the workflow.                                                          |
| `status`                       | VARCHAR(50)  | NOT NULL                                                             | Current status of this specific CPOA-tracked task (e.g., "dispatched", "polling", "agent_completed", "agent_failed"). |
| `input_params_json`            | JSONB        | Nullable                                                             | Parameters or payload sent to the agent for this task.                                                     |
| `output_result_summary_json`   | JSONB        | Nullable                                                             | A summary of the agent's output or a reference to it (e.g., generated snippet ID, GCS URI of audio).       |
| `error_details_json`           | JSONB        | Nullable                                                             | If the task failed, stores detailed error information from the agent or CPOA.                              |
| `start_timestamp`              | TIMESTAMPTZ  | Nullable                                                             | Timestamp when the task was initiated/dispatched.                                                          |
| `end_timestamp`                | TIMESTAMPTZ  | Nullable                                                             | Timestamp when the task concluded (completed or failed).                                                   |
| `last_updated_timestamp`       | TIMESTAMPTZ  | NOT NULL, DEFAULT `current_timestamp`                                | Timestamp of the last update to this task instance record.                                                 |
| `retry_count`                  | INTEGER      | NOT NULL, DEFAULT 0                                                  | Number of times this task has been retried.                                                                |

## 3. Relationships

-   **`workflow_instances` to `task_instances`**: One-to-Many.
    -   A single workflow instance can have multiple task instances associated with it.
    -   The `task_instances.workflow_id` column links back to `workflow_instances.workflow_id`.
    -   The `ON DELETE CASCADE` constraint on `task_instances.workflow_id` ensures that if a workflow instance is deleted, all its associated task instances are also automatically deleted.

## 4. Indexes

The following indexes are defined to optimize common query patterns:

**For `workflow_instances`:**

-   `CREATE INDEX idx_workflow_user_id ON workflow_instances (user_id) WHERE user_id IS NOT NULL;`
    -   Purpose: Efficiently query workflows initiated by a specific user.
-   `CREATE INDEX idx_workflow_status ON workflow_instances (overall_status);`
    -   Purpose: Quickly find workflows based on their overall status.
-   `CREATE INDEX idx_workflow_start_time ON workflow_instances (start_timestamp DESC);`
    -   Purpose: Efficiently retrieve workflows ordered by their start time, useful for listing recent workflows.
-   `CREATE INDEX idx_workflow_trigger_event_type ON workflow_instances (trigger_event_type);`
    -   Purpose: Efficiently query workflows by the type of event that triggered them.

**For `task_instances`:**

-   `CREATE INDEX idx_task_workflow_id ON task_instances (workflow_id);`
    -   Purpose: Quickly retrieve all tasks associated with a specific workflow.
-   `CREATE INDEX idx_task_agent_name ON task_instances (agent_name);`
    -   Purpose: Find tasks executed by a particular agent.
-   `CREATE INDEX idx_task_status ON task_instances (status);`
    -   Purpose: Quickly find tasks based on their status.
-   `CREATE INDEX idx_task_order ON task_instances (workflow_id, task_order);`
    -   Purpose: Efficiently retrieve tasks in their execution order within a workflow.

## 5. JSONB Field Usage Guidelines

The `JSONB` fields provide flexibility in storing structured data:

-   **`workflow_instances.trigger_event_details_json`**: Intended to store the initial request payload that started the workflow. For example, for a podcast generation, this could be `{"topic": "AI in Healthcare", "voice_params": {...}}`.
-   **`workflow_instances.context_data_json`**: A general-purpose field to store data that evolves during the workflow and might be needed by subsequent tasks or for final reporting. Examples:
    -   GCS URIs of generated artifacts (audio, images).
    -   IDs of created database records (e.g., main podcast ID if separate from workflow ID).
    -   Aggregated results from multiple snippet generations.
-   **`task_instances.input_params_json`**: Stores the specific parameters sent to an agent for a particular task. For an SCA call, this might be `{"topic_id": "...", "content_brief": "..."}`.
-   **`task_instances.output_result_summary_json`**: Stores a concise summary of the task's successful output. For an IGA call, this could be `{"image_url": "gs://bucket/image.png"}`. For VFA, `{"audio_gcs_uri": "gs://...", "duration": 120.5}`. Avoid storing very large blobs of text or data here; use it for key results or references.
-   **`task_instances.error_details_json`**: If an agent call fails, this can store the structured error response received from the agent, or any detailed error information CPOA captures for that task failure.

## 6. Status Lifecycles (Examples)

These are illustrative examples and can be expanded.

**`workflow_instances.overall_status`:**

-   `pending`: Workflow has been created but not yet started processing.
-   `in_progress`: Workflow is actively being processed; tasks are being dispatched.
-   `completed`: All tasks in the workflow completed successfully.
-   `failed`: A critical task failed, and the workflow could not be completed.
-   `completed_with_errors`: The workflow reached an end state, but some non-critical tasks may have failed or produced warnings (e.g., image generation for a snippet failed, but the snippet text was generated).

**`task_instances.status`:**

-   `pending`: Task is defined but not yet dispatched to an agent.
-   `dispatched`: Task has been sent to an agent, awaiting acknowledgment or start of processing.
-   `in_progress`: Agent has acknowledged the task and is actively processing it (if agent provides such feedback).
-   `completed`: Agent completed the task successfully.
-   `failed`: Agent reported failure, or CPOA encountered an error calling the agent (e.g., timeout after retries).
-   `skipped`: Task was intentionally skipped due to upstream failures or conditional logic.

## 7. CPOA Integration Overview

The CPOA module utilizes these tables to manage and track its operations. Key CPOA orchestration functions such as `orchestrate_podcast_generation`, `orchestrate_landing_page_snippets`, `orchestrate_search_results_generation`, and `orchestrate_topic_exploration` are responsible for:

1.  **Workflow Initialization:** Upon invocation, a new record is created in the `workflow_instances` table by calling the internal helper `_create_workflow_instance`. This helper captures the `trigger_event_type` (e.g., "podcast_generation"), initial `trigger_event_details_json` (like the API request payload), and the `user_id` if provided by the API Gateway.
2.  **Task Initialization:** Before calling an external agent (like TDA, PSWA, SCA, IGA, VFA) or performing a significant internal step, a corresponding record is created in the `task_instances` table using `_create_task_instance`. This logs the `agent_name`, `task_order` within the workflow, and `input_params_json`.
3.  **Task Updates:** After the agent call or step is completed (or fails), the relevant `task_instances` record is updated using `_update_task_instance_status`. This captures the final `status` (e.g., "completed", "failed"), a summary of the `output_result_summary_json`, any `error_details_json`, and timestamps. Retry attempts are also tracked.
4.  **Workflow Finalization:** Once all tasks in a workflow are processed, or if a critical error halts the workflow, the parent `workflow_instances` record is updated using `_update_workflow_instance_status`. This sets the `overall_status`, `end_timestamp`, any top-level `error_message`, and can update `context_data_json` with final results or references (like the main GCS URI of a generated podcast).

### Helper Functions in CPOA:

To interact with these tables, CPOA uses a set of internal helper functions:
-   `_create_workflow_instance(...)`: Creates a new workflow entry.
-   `_update_workflow_instance_status(...)`: Updates an existing workflow's status and other relevant fields.
    -   `_create_task_instance(...)`: Creates a new task entry, capturing input parameters and the `external_celery_task_id` if an async task is dispatched.
    -   `_update_task_instance_status(...)`: Updates an existing task's status (based on polling results from the agent's status endpoint) and outcome details.

These helpers encapsulate the SQL logic for database interactions, ensuring consistency and proper error handling (including transaction management) when CPOA updates its state in the PostgreSQL database. They are designed to primarily use PostgreSQL connections obtained via CPOA's internal DB connection management.
```

---

*For information on the overarching Aethercast project architecture, advanced setup including database migrations for shared resources like idempotency tables, and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
