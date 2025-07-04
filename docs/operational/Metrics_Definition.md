# Metrics Definition for Aethercast

## 1. Introduction

The purpose of this document is to define key metrics for the Aethercast system. These metrics are essential for monitoring the health and performance of various microservices, tracking error rates, and understanding system behavior. Initially, these metrics will be logged as structured JSON within the application logs. This allows for easy parsing, aggregation, and visualization using log management and analytics tools.

## 2. General Metric Logging Strategy

Metrics are logged as structured JSON objects embedded within the standard application logs. Each metric log entry should ideally contain:

*   `metric_name` (string): The name of the metric (e.g., "endpoint_latency_ms").
*   `value` (numeric): The value of the metric.
*   `tags` (object, optional but recommended): A collection of key-value pairs (dimensions) providing context for the metric (e.g., `{"endpoint": "/api/v1/podcasts", "method": "POST"}`).
*   Standard log fields like `timestamp`, `level`, `service_name`, `logger_name` will also be present due to the JSON logging setup.

This approach allows for easy filtering and aggregation in a log analytics platform. For example, one can count occurrences of a specific metric name to get a total count, or average the `value` field for latency metrics.

## 3. Service-Specific Metrics

### 3.1. API Gateway (`api_gateway`)

*Note: Code-level implementation of these specific metrics was previously attempted but blocked due to tool limitations with file modifications. The definitions below remain as the target for future implementation.*

*   **`endpoint_request_count`** (Counter)
    *   **Description:** Tracks the number of requests processed by each endpoint.
    *   **Value:** `1` (logged for each request).
    *   **Tags:**
        *   `endpoint` (string): The request path (e.g., "/api/v1/snippets", "/api/v1/podcasts").
        *   `method` (string): The HTTP method (e.g., "GET", "POST").
        *   `status_code_class` (string): The class of the HTTP status code returned (e.g., "2xx", "4xx", "5xx").
*   **`endpoint_latency_ms`** (Histogram/Summary)
    *   **Description:** Measures the duration taken to process an HTTP request from the moment it's received until a response is sent.
    *   **Value:** Duration in milliseconds.
    *   **Tags:**
        *   `endpoint` (string): The request path.
        *   `method` (string): The HTTP method.
        *   `status_code` (integer): The actual HTTP status code returned (e.g., 200, 401, 503).
*   **`auth_failure_count`** (Counter)
    *   **Description:** Tracks the number of failed authentication attempts.
    *   **Value:** `1` (logged for each authentication failure).
    *   **Tags:**
        *   `reason` (string): The reason for authentication failure (e.g., "token_expired", "token_invalid", "user_not_found", "missing_token", "invalid_credentials").

### 3.2. CPOA (Central Podcast Orchestrator Agent)
*Note: Code-level implementation of these specific metrics was previously attempted but blocked due to tool limitations with file modifications. The definitions below remain as the target for future implementation.*

*(Metrics are logged via the API Gateway's logger as CPOA functions are executed within the API Gateway's process context. The `workflow_id` and `task_id` tags will be automatically included by the CPOA's `WorkflowLoggerAdapter`.)*

*   **`cpoa_workflow_invoked_count`** (Counter)
    *   **Description:** Tracks the number of times a CPOA orchestration workflow is invoked.
    *   **Value:** `1` (logged when a workflow starts).
    *   **Tags:**
        *   `trigger_event_type` (string): The type of event that triggered the workflow (e.g., "podcast_generation", "landing_page_snippets", "search_results_generation", "topic_exploration").
*   **`cpoa_workflow_duration_ms`** (Histogram/Summary)
    *   **Description:** Measures the total time taken for a CPOA workflow to complete or fail.
    *   **Value:** Duration in milliseconds.
    *   **Tags:**
        *   `trigger_event_type` (string): The type of workflow.
        *   `overall_status` (string): The final status of the workflow (e.g., "completed", "failed", "completed_with_errors").
*   **`cpoa_workflow_status_count`** (Counter)
    *   **Description:** Tracks the count of workflows ending in a specific status.
    *   **Value:** `1` (logged when a workflow reaches a terminal status).
    *   **Tags:**
        *   `trigger_event_type` (string): The type of workflow.
        *   `overall_status` (string): The final status of the workflow.
*   **`cpoa_task_instance_duration_ms`** (Histogram/Summary)
    *   **Description:** Measures the time taken for an individual task (agent call) within a CPOA workflow to complete or fail.
    *   **Value:** Duration in milliseconds.
    *   **Tags:**
        *   `agent_name` (string): The name of the agent or task type (e.g., "TDA", "SCA", "PSWA", "VFA").
        *   `status` (string): The final status of the task instance (e.g., "completed", "failed", "skipped").
*   **`cpoa_task_instance_status_count`** (Counter)
    *   **Description:** Tracks the count of task instances ending in a specific status.
    *   **Value:** `1` (logged when a task instance is updated to a terminal status).
    *   **Tags:**
        *   `agent_name` (string): The name of the agent or task type.
        *   `status` (string): The final status of the task instance.

### 3.3. AIMS Service (`aims_service` - LLM via Vertex AI)

*   **`aims_request_count`** (Counter)
    *   **Description:** Tracks the number of requests made to the AIMS `/v1/generate` endpoint.
    *   **Value:** `1` (logged for each request processed).
    *   **Tags:**
        *   `model_id_requested` (string): The LLM model ID requested by the client.
        *   `status` (string): The outcome of the request (e.g., "success", "validation_error_prompt", "vertexai_invalid_argument", "vertexai_safety_blocked", "config_error").
*   **`aims_request_latency_ms`** (Histogram/Summary)
    *   **Description:** Measures the total duration for a call to the `/v1/generate` endpoint.
    *   **Value:** Duration in milliseconds.
    *   **Tags:**
        *   `model_id_requested` (string): The LLM model ID requested.
*   **`aims_vertexai_call_latency_ms`** (Histogram/Summary)
    *   **Description:** Measures the specific duration of the call to the underlying Vertex AI `model.generate_content` method.
    *   **Value:** Duration in milliseconds.
    *   **Tags:**
        *   `model_id_used` (string): The actual Vertex AI model ID used for the call.
*   **`aims_vertexai_error_count`** (Counter)
    *   **Description:** Tracks errors specifically from Vertex AI during LLM calls.
    *   **Value:** `1` (logged for each distinct Vertex AI error).
    *   **Tags:**
        *   `model_id_used` (string): The Vertex AI model ID that was used.
        *   `error_type` (string): A classification of the Vertex AI error (e.g., "invalid_argument", "permission_denied", "rate_limit", "safety_blocked", "service_unavailable", "google_api_error").
*   **`aims_token_usage_input_tokens`** (Counter/Sum or Histogram)
    *   **Description:** Tracks the number of input tokens processed by the LLM, as reported by Vertex AI.
    *   **Value:** Number of input tokens.
    *   **Tags:**
        *   `model_id_used` (string): The Vertex AI model ID used.
*   **`aims_token_usage_output_tokens`** (Counter/Sum or Histogram)
    *   **Description:** Tracks the number of output tokens generated by the LLM, as reported by Vertex AI.
    *   **Value:** Number of output tokens.
    *   **Tags:**
        *   `model_id_used` (string): The Vertex AI model ID used.

### 3.4. AIMS TTS Service (`aims_tts_service` - Text-to-Speech via Google Cloud TTS)

*   **`aims_tts_request_count`** (Counter)
    *   **Description:** Tracks requests to the `/v1/synthesize` endpoint.
    *   **Value:** `1`.
    *   **Tags:** `voice_id_requested`, `audio_format_requested`, `status` (e.g., "success", "validation_error_text", "gcp_tts_error", "gcs_upload_error", "config_error").
*   **`aims_tts_request_latency_ms`** (Histogram/Summary)
    *   **Description:** Overall latency for a `/v1/synthesize` request.
    *   **Value:** Duration in milliseconds.
    *   **Tags:** `voice_id_requested`, `audio_format_requested`.
*   **`aims_tts_gcp_tts_call_latency_ms`** (Histogram/Summary)
    *   **Description:** Latency of the `client.synthesize_speech(...)` call.
    *   **Value:** Duration in milliseconds.
    *   **Tags:** `voice_id_used`.
*   **`aims_tts_gcs_upload_latency_ms`** (Histogram/Summary)
    *   **Description:** Latency of the GCS `blob.upload_from_string(...)` call.
    *   **Value:** Duration in milliseconds.
*   **`aims_tts_synthesized_chars_count`** (Histogram/Summary)
    *   **Description:** Number of characters synthesized.
    *   **Value:** Character count.
    *   **Tags:** `voice_id_used`.
*   **`aims_tts_gcp_error_count`** (Counter)
    *   **Description:** Counts errors specifically from the Google Cloud TTS API call.
    *   **Value:** `1`.
    *   **Tags:** `error_type` (e.g., "gcp_api_error_tts").
*   **`aims_tts_gcs_upload_failure_count`** (Counter)
    *   **Description:** Counts failures during GCS upload.
    *   **Value:** `1`.
    *   **Tags:** `error_detail` (brief error string).

### 3.5. TDA (Topic Discovery Agent)

*   **`tda_discover_topics_request_count`**: (Counter)
    *   **Description:** Tracks requests to the `/discover_topics` endpoint.
    *   **Value:** `1`.
    *   **Tags:** `status` (e.g., "success", "success_no_topics", "validation_error_query", "simulated_error", "internal_server_error").
*   **`tda_discover_topics_latency_ms`**: (Histogram/Summary)
    *   **Description:** Overall latency for a `/discover_topics` request.
    *   **Value:** Duration in milliseconds.
*   **`tda_newsapi_call_latency_ms`**: (Histogram/Summary)
    *   **Description:** Latency of the call to `call_real_news_api` (if `USE_REAL_NEWS_API` is true).
    *   **Value:** Duration in milliseconds.
*   **`tda_newsapi_error_count`**: (Counter)
    *   **Description:** Tracks errors during NewsAPI calls.
    *   **Value:** `1`.
    *   *Note: Implementation relies on errors logged within `call_real_news_api` as distinguishing API errors from 'no results' is indirect at the endpoint level without modifying `call_real_news_api`'s return signature. An error log within `call_real_news_api` can be used as a proxy for this count.*
*   **`tda_topics_discovered_count`**: (Histogram/Summary)
    *   **Description:** Number of topics returned by the `/discover_topics` endpoint.
    *   **Value:** Number of topics.

### 3.6. SCA (Snippet Craft Agent)

*   **`sca_craft_snippet_request_count`**: (Counter)
    *   **Description:** Tracks requests to the `/craft_snippet` endpoint.
    *   **Value:** `1`.
    *   **Tags:** `status` (e.g., "success", "placeholder_used", "validation_error_payload", "aims_error", "simulated_sca_error", "internal_server_error").
*   **`sca_craft_snippet_latency_ms`**: (Histogram/Summary)
    *   **Description:** Overall latency for a `/craft_snippet` request.
    *   **Value:** Duration in milliseconds.
*   **`sca_aims_call_latency_ms`**: (Histogram/Summary)
    *   **Description:** Latency of the call to `call_real_llm_service` (if `USE_REAL_LLM_SERVICE` is true).
    *   **Value:** Duration in milliseconds.
*   **`sca_aims_call_failure_count`**: (Counter)
    *   **Description:** Counts failures during calls to the AIMS service.
    *   **Value:** `1`.
    *   **Tags:** `error_code` (the error code returned by AIMS or SCA's internal error code for AIMS call).

### 3.7. PSWA (Podcast Script Weaver Agent)

*   **`pswa_weave_script_request_count`**: (Counter)
    *   **Description:** Tracks requests to the `/weave_script` endpoint.
    *   **Value:** `1`.
    *   **Tags:** `status` (e.g., "success_generation", "success_cache_hit", "test_mode_scenario_default", "validation_error_payload", "aims_timeout_error", "aims_http_error_502").
*   **`pswa_weave_script_latency_ms`**: (Histogram/Summary)
    *   **Description:** Overall latency for a `/weave_script` request.
    *   **Value:** Duration in milliseconds.
*   **`pswa_aims_call_latency_ms`**: (Histogram/Summary)
    *   **Description:** Latency of the call to AIMS service (if not test mode and not a cache hit).
    *   **Value:** Duration in milliseconds.
*   **`pswa_aims_call_failure_count`**: (Counter)
    *   **Description:** Counts failures during calls to the AIMS service.
    *   **Value:** `1`.
    *   **Tags:** `error_type` (e.g., "timeout", "http_error_500", "parse_error").
*   **`pswa_cache_hit_count`**: (Counter)
    *   **Description:** Number of times a script was successfully retrieved from cache.
    *   **Value:** `1`.
    *   **Tags:** `topic_hash_prefix` (first 8 characters of the topic hash).
*   **`pswa_cache_miss_count`**: (Counter)
    *   **Description:** Number of times a script was not found in cache (and an AIMS call was attempted).
    *   **Value:** `1`.
    *   **Tags:** `topic_hash_prefix`.

### 3.8. VFA (Voice Forge Agent)

*   **`vfa_forge_voice_request_count`**: (Counter)
    *   **Description:** Tracks requests to the `/forge_voice` endpoint.
    *   **Value:** `1`.
    *   **Tags:** `status` (e.g., "success", "skipped_script_too_short", "skipped_pswa_error", "test_mode_success", "aims_tts_timeout", "aims_tts_http_error_502").
*   **`vfa_forge_voice_latency_ms`**: (Histogram/Summary)
    *   **Description:** Overall latency for a `/forge_voice` request.
    *   **Value:** Duration in milliseconds.
*   **`vfa_aims_tts_call_latency_ms`**: (Histogram/Summary)
    *   **Description:** Latency of the call to AIMS_TTS service (if not test mode and TTS is attempted).
    *   **Value:** Duration in milliseconds.
    *   **Tags:** `voice_id_used`.
*   **`vfa_aims_tts_call_failure_count`**: (Counter)
    *   **Description:** Counts failures during calls to the AIMS_TTS service.
    *   **Value:** `1`.
    *   **Tags:** `error_type` (e.g., "timeout", "http_error_502", "parse_error").
*   **`vfa_script_char_count_for_synthesis`**: (Histogram/Summary)
    *   **Description:** Number of characters in the text actually sent for synthesis.
    *   **Value:** Character count.
    *   **Tags:** `voice_id_used`.

### 3.9. IGA (Image Generation Agent)

*   **`iga_generate_image_request_count`**: (Counter)
    *   **Description:** Tracks requests to the `/generate_image` endpoint.
    *   **Value:** `1`.
    *   **Tags:** `status` (e.g., "success", "validation_error_prompt_missing", "vertexai_error_invalid_argument", "vertexai_error_safety_blocked", "gcs_upload_error").
*   **`iga_generate_image_latency_ms`**: (Histogram/Summary)
    *   **Description:** Overall latency for a `/generate_image` request.
    *   **Value:** Duration in milliseconds.
*   **`iga_vertexai_call_latency_ms`**: (Histogram/Summary)
    *   **Description:** Latency of the Vertex AI Imagen call (`model.generate_images(...)`).
    *   **Value:** Duration in milliseconds.
*   **`iga_vertexai_error_count`**: (Counter)
    *   **Description:** Counts errors from Vertex AI Imagen.
    *   **Value:** `1`.
    *   **Tags:** `error_type` (e.g., "invalid_argument", "permission_denied", "resource_exhausted", "safety_blocked").
*   **`iga_gcs_upload_latency_ms`**: (Histogram/Summary)
    *   **Description:** Latency of the GCS `blob.upload_from_string(...)` call.
    *   **Value:** Duration in milliseconds.
*   **`iga_gcs_upload_failure_count`**: (Counter)
    *   **Description:** Counts failures during GCS upload.
    *   **Value:** `1`.
    *   **Tags:** `error_detail` (brief error string, if available).

### 3.10. ASF (Audio Stream Feeder)

*   **`asf_websocket_connection_count`**: (Counter)
    *   **Description:** Tracks WebSocket connections and disconnections.
    *   **Value:** `1`.
    *   **Tags:** `namespace` (e.g., "/api/v1/podcasts/stream", "/ui_updates"), `status` ("connected", "disconnected").
*   **`asf_audio_stream_started_count`**: (Counter)
    *   **Description:** Number of audio streams successfully initiated.
    *   **Value:** `1`.
    *   **Tags:** `stream_id_prefix` (first 8 characters of stream_id).
*   **`asf_audio_stream_completed_count`**: (Counter)
    *   **Description:** Number of audio streams successfully completed (EOS emitted).
    *   **Value:** `1`.
    *   **Tags:** `stream_id_prefix`.
*   **`asf_audio_stream_error_count`**: (Counter)
    *   **Description:** Number of errors encountered during audio streaming.
    *   **Value:** `1`.
    *   **Tags:** `stream_id_prefix`, `reason` (e.g., "gcs_fetch_failed", "client_disconnected_early", "file_not_found_locally_fallback", "stream_id_not_found_in_map").
*   **`asf_signed_url_fetch_latency_ms`**: (Histogram/Summary)
    *   **Description:** Latency for fetching signed GCS URLs from the API Gateway.
    *   **Value:** Duration in milliseconds.
*   **`asf_signed_url_fetch_failure_count`**: (Counter)
    *   **Description:** Counts failures when fetching signed GCS URLs.
    *   **Value:** `1`.
    *   **Tags:** `reason` (e.g., "timeout", "http_error_500", "no_url_in_response").
*   **`asf_ui_update_relayed_count`**: (Counter)
    *   **Description:** Number of UI update messages successfully relayed via Socket.IO.
    *   **Value:** `1`.
    *   **Tags:** `event_name`.
*   **`asf_ui_update_relay_failed_count`**: (Counter)
    *   **Description:** Number of UI update messages that failed to be relayed.
    *   **Value:** `1`.
    *   **Tags:** `event_name`.

### 3.11. WCHA (Web Content Harvester Agent)

*   **`wcha_harvest_request_count`** (Counter)
    *   **Description:** Tracks requests to the `/harvest` endpoint.
    *   **Value:** `1`.
    *   **Tags:** `status` (e.g., "success_sync_ddgs", "success_async_newsapi_dispatch", "success_async_url_dispatch", "validation_error_payload", "no_results_ddgs", "all_urls_failed_harvest").
*   **`wcha_harvest_latency_ms`** (Histogram/Summary)
    *   **Description:** Overall latency for a `/harvest` request. For async dispatches, this measures the time to dispatch the Celery task. For synchronous DDGS, it's the full processing time.
    *   **Value:** Duration in milliseconds.
    *   **Tags:** `type` (e.g., "sync_ddgs", "async_newsapi_dispatch", "async_url_dispatch").
*   **`wcha_celery_task_duration_ms`** (Histogram/Summary)
    *   **Description:** Duration of WCHA Celery tasks (`fetch_news_articles_task`, `harvest_url_content_task`).
    *   **Value:** Duration in milliseconds.
    *   **Tags:** `task_name` (e.g., "fetch_news_articles_task", "harvest_url_content_task"), `status` ("success", "failure").
*   **`wcha_ddgs_search_latency_ms`** (Histogram/Summary)
    *   **Description:** Latency for DuckDuckGo searches performed synchronously by the `/harvest` endpoint.
    *   **Value:** Duration in milliseconds.
*   **`wcha_newsapi_call_latency_ms`** (Histogram/Summary)
    *   **Description:** Latency for NewsAPI calls made by `fetch_news_articles_task`.
    *   **Value:** Duration in milliseconds.
*   **`wcha_url_fetch_latency_ms`** (Histogram/Summary)
    *   **Description:** Latency for fetching content from a single URL (within DDGS sync path or `harvest_url_content_task`).
    *   **Value:** Duration in milliseconds.
*   **`wcha_content_extraction_latency_ms`** (Histogram/Summary)
    *   **Description:** Latency for `trafilatura` content extraction from a single page.
    *   **Value:** Duration in milliseconds.
*   **`wcha_urls_found_count`** (Histogram/Summary)
    *   **Description:** Number of URLs found by a search operation (DDGS or NewsAPI).
    *   **Value:** Count of URLs.
    *   **Tags:** `source` ("ddgs", "newsapi").
*   **`wcha_urls_successfully_harvested_count`** (Histogram/Summary)
    *   **Description:** Number of URLs from which content was successfully extracted in a single operation.
    *   **Value:** Count of successfully harvested URLs.
    *   **Tags:** `source_operation` ("ddgs_sync_harvest", "newsapi_task", "direct_url_task").
*   **`wcha_harvest_error_count`** (Counter)
    *   **Description:** Tracks errors during content harvesting from individual URLs or from search APIs.
    *   **Value:** `1`.
    *   **Tags:** `source_type` ("ddgs_search", "newsapi_call", "url_fetch", "content_extraction"), `error_class` (e.g., "requests.Timeout", "trafilatura.ExtractionError", "NewsAPIException").
