**Note:** This document describes a **conceptual Key-Value store (e.g., Redis)** for caching and session management. The Aethercast system currently implements:
- User session state and preferences in the **PostgreSQL** `user_sessions` table (managed by API Gateway).
- Podcast generation status tracking via CPOA's `workflow_instances` and `task_instances` tables in **PostgreSQL**.
- Caching for topics and snippets relies on the `topics_snippets` table in **PostgreSQL** and PSWA's script cache (`generated_scripts` table, which can be PostgreSQL or SQLite).
- Redis is primarily used as the **Celery message broker and results backend**.

Refer to the main `aethercast/data_stores/README.md`, `docs/architecture/CPOA_State_Management.md`, and relevant service READMEs for details on the current PostgreSQL-based implementations. This document outlines potential future or auxiliary uses for a dedicated Key-Value store.
---
# Key-Value Store (e.g., Redis) Definitions

## Purpose
Conceptually provides fast access to frequently used data, user session information, and caches. In the current Aethercast system, Redis serves primarily as the Celery broker/backend, while PostgreSQL handles most persistent state and some caching.

---

## 1. User Session State

### Key Format
`session:<session_id>`

### Value Structure
A JSON string or serialized object containing session-specific data:
```json
{
  "user_id": "string", // If users can log in
  "preferences": {
    "preferred_topics": ["string"],
    "playback_speed_default": "float"
  },
  "last_activity_timestamp": "timestamp",
  "active_podcast_stream_id": "string" // ID of the podcast currently being streamed, if any
}
```

### TTL (Time-To-Live)
Typically set to a standard session timeout (e.g., 30 minutes, extended on activity).

### Use Case
- Storing user preferences.
- Tracking active sessions for metrics.
- Managing state for WebSocket connections (e.g., which podcast a user is listening to).

---

## 2. Topic Cache

### Key Format
`cache:topic:<topic_id>` or `cache:topics:all` (for a list of popular/all topics)

### Value Structure
- For `cache:topic:<topic_id>`: JSON string of a `TopicObject` (see NoSQL definitions).
- For `cache:topics:all`: JSON string of an array of `TopicObject`s.

### TTL (Time-To-Live)
- Can vary. E.g., 1 hour for individual topics, longer for the "all topics" list if it doesn't change often.

### Use Case
- Reducing load on the NoSQL database for frequently accessed topics.
- Speeding up responses for API endpoints that list or fetch topic details.

---

## 3. Snippet Cache

### Key Format
`cache:snippet:<snippet_id>` or `cache:snippets:topic:<topic_id>:page:<page_number>`

### Value Structure
- For `cache:snippet:<snippet_id>`: JSON string of a `SnippetDataObject`.
- For `cache:snippets:topic:<topic_id>:page:<page_number>`: JSON string of an array of `SnippetDataObject`s for a given topic and pagination.

### TTL (Time-To-Live)
- E.g., 15-30 minutes. Snippets might be updated or new ones added frequently.

### Use Case
- Caching results for the `GET /api/v1/snippets` endpoint.

---

## 4. Podcast Generation Status Cache

### Key Format
`cache:podcast_status:<podcast_id>`

### Value Structure
A JSON string indicating the current status of a podcast generation task:
```json
{
  "podcast_id": "string",
  "status": "string", // e.g., "queued", "script_generating", "audio_rendering", "ready_to_stream", "error"
  "estimated_completion_time": "timestamp", // Optional
  "error_message": "string" // Optional, if status is "error"
}
```

### TTL (Time-To-Live)
- Short TTL, e.g., 1-5 minutes, or until the status changes to "ready_to_stream" or "error".

### Use Case
- Providing quick status updates for the `status_url` returned by asynchronous task submissions.
- Reducing load on primary databases for status polling if such a cache were implemented.
---

*For information on the overarching Aethercast project architecture, current data storage solutions (primarily PostgreSQL and GCS), and how services interact, please refer to the main [README.md](../../../README.md) at the root of the Aethercast project.*
