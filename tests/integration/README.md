# Integration Testing

## Purpose

Integration tests for the Aethercast system aim to verify the end-to-end flows between different microservices. These tests typically interact with the API Gateway, which then orchestrates calls to backend services like CPOA, TDA, PSWA, VFA, SCA, and ASF.

## Running Integration Tests

1.  **Environment Setup:**
    *   Ensure the entire Aethercast application is running, usually via Docker Compose:
        ```bash
        docker-compose up -d --build
        ```
    *   Make sure all services have initialized correctly and are accessible. Key services involved in full flow tests include API Gateway, CPOA, PSWA, VFA, and ASF. TDA and SCA are involved if testing snippet generation or exploration flows.

2.  **Test Configuration:**
    *   The primary configuration for integration tests is the `API_GW_TEST_URL` environment variable, which should point to the base URL of the API Gateway.
        -   *Default:* `http://localhost:5001/api/v1` (as set in `test_full_flow.py`).
    *   `HOST_AUDIO_DATA_PATH` (optional): If you want tests to verify the existence of dummy audio files created by VFA in test mode, set this to the *host machine's path* that maps to the `aethercast_audio_data` Docker volume (e.g., `./aethercast_volumes/audio_data` if you created it in the project root). This check can be flaky in CI environments.

3.  **Executing Tests:**
    *   Run the tests using Python's `unittest` module from the project root:
        ```bash
        python -m unittest discover tests/integration -v
        ```
        Or, to run a specific file:
        ```bash
        python -m unittest tests.integration.test_full_flow -v
        ```

## Key Test Scenarios (`test_full_flow.py`)

-   **`test_successful_podcast_generation_in_test_modes`**:
    -   This test verifies the default end-to-end success path when PSWA and VFA are in their respective "test modes" (as configured by their environment variables like `PSWA_TEST_MODE_ENABLED=true`).
    -   It initiates a podcast generation via `POST /api/v1/podcasts`, polls the podcast status endpoint (`GET /api/v1/podcasts/<podcast_id>`) until completion, and then checks for a successful status and the presence of expected output fields (like `final_audio_filepath`, `stream_id`, `tts_settings_used`, and a non-empty `orchestration_log`).

-   **Scenario-Based Testing (Enhanced Test Modes):**
    -   To test specific error conditions or alternative behaviors from PSWA and VFA without changing their core code, the `POST /api/v1/podcasts` endpoint now accepts an optional `test_scenarios` field in its JSON payload.
    -   This field is a dictionary where keys are service aliases ("pswa", "vfa") and values are scenario names recognized by those services.
        ```json
        {
            "topic": "My Test Topic",
            "client_id": "some_client_id",
            "test_scenarios": {
                "pswa": "insufficient_content",
                "vfa": "vfa_error_tts"
            }
        }
        ```
    -   The API Gateway passes `test_scenarios` to CPOA. CPOA then adds an `X-Test-Scenario` HTTP header to its requests to PSWA and VFA, based on the provided scenario values.
    -   PSWA and VFA, when in their respective test modes (`PSWA_TEST_MODE_ENABLED=true`, `VFA_TEST_MODE_ENABLED=true`), will check for this header and modify their behavior accordingly.

    -   **`test_podcast_generation_pswa_insufficient_content`**:
        -   Uses `test_scenarios: {"pswa": "insufficient_content"}`.
        -   PSWA (in test mode) receives `X-Test-Scenario: insufficient_content`.
        -   PSWA returns a predefined JSON error structure indicating insufficient content (with an HTTP 200 to CPOA, but its endpoint `/weave_script` then returns HTTP 400 to CPOA based on the error content).
        -   The test verifies that the final CPOA status for the podcast task is `failed_pswa_request_exception` and that the error message reflects the insufficient content issue.

    -   **`test_podcast_generation_vfa_tts_error`**:
        -   Uses `test_scenarios: {"vfa": "vfa_error_tts"}`.
        -   VFA (in test mode) receives `X-Test-Scenario: vfa_error_tts`.
        -   VFA simulates a TTS API error and returns an error JSON (with an HTTP 500 to CPOA).
        -   The test verifies that the final CPOA status is `failed_vfa_request_exception` and that the error message reflects the simulated VFA TTS failure.

**Troubleshooting Integration Tests:**

-   Ensure all Docker services are running and healthy (`docker-compose ps`).
-   Check logs for each service (`docker-compose logs <service_name>`).
-   Verify environment variables are correctly set for each service, especially URLs and API keys (even if mocked/dummy for test modes).
-   Confirm that `API_GW_TEST_URL` is correctly pointing to where the API Gateway is accessible from the machine running the tests.
-   If testing file existence with `HOST_AUDIO_DATA_PATH`, double-check the volume mount path on your host.
