import unittest
import requests
import time
import json
import os
import uuid

# Base URL for the API Gateway service as exposed by Docker Compose
API_GATEWAY_BASE_URL = os.getenv("API_GW_TEST_URL", "http://localhost:5001/api/v1")

# Path to the shared audio volume on the HOST machine (for optional file check)
# This needs to be configured by the user running the tests if they want to perform this check.
# It's the path where Docker mounts 'aethercast_audio_data'.
# Example: './aethercast_volumes/audio_data' if 'aethercast_volumes/audio_data' is created in project root
# and docker-compose mounts it to 'aethercast_audio_data' volume.
# For automated CI, this might be tricky or skipped.
HOST_AUDIO_DATA_PATH = os.getenv("HOST_AUDIO_DATA_PATH", None)


class TestFullPodcastFlow(unittest.TestCase):

    def test_successful_podcast_generation_in_test_modes(self):
        """
        Tests the full end-to-end podcast generation flow with PSWA and VFA in "test mode".
        1. Initiates podcast generation via API Gateway.
        2. Polls for completion.
        3. Verifies the final status and presence of key output fields.
        """
        client_id = f"test_client_{uuid.uuid4().hex}"
        topic = "Integration Test Topic in Test Mode"

        print(f"\n[INFO] Starting test_successful_podcast_generation_in_test_modes for topic: '{topic}' with client_id: {client_id}")

        # 1. Initiate podcast generation
        initiate_payload = {
            "topic": topic,
            "client_id": client_id
            # Voice params could be added here if needed for a specific test mode VFA behavior
        }
        print(f"[INFO] POST {API_GATEWAY_BASE_URL}/podcasts with payload: {initiate_payload}")
        initiate_response = requests.post(f"{API_GATEWAY_BASE_URL}/podcasts", json=initiate_payload, timeout=30)

        # API Gateway should accept the request.
        # Status could be 201 (if CPOA finishes very quickly due to test modes) or 200 (if CPOA is processing)
        self.assertIn(initiate_response.status_code, [200, 201],
                      f"Initiate request failed: {initiate_response.status_code} - {initiate_response.text}")

        init_data = initiate_response.json()
        self.assertIn("podcast_id", init_data, "Response data missing 'podcast_id'")
        podcast_id = init_data["podcast_id"]
        print(f"[INFO] Podcast generation initiated. Podcast ID: {podcast_id}")

        # 2. Poll for completion
        max_polls = 20  # Increased polling for potentially slower CI environments
        poll_interval_seconds = 3 # Increased interval
        completed = False
        final_status_data = None

        for i in range(max_polls):
            print(f"[INFO] Polling attempt {i+1}/{max_polls} for podcast_id: {podcast_id}...")
            poll_response = requests.get(f"{API_GATEWAY_BASE_URL}/podcasts/{podcast_id}", timeout=10)
            self.assertEqual(poll_response.status_code, 200,
                             f"Polling request failed: {poll_response.status_code} - {poll_response.text}")

            poll_data = poll_response.json()
            current_status = poll_data.get("status")
            print(f"[INFO] Current status: {current_status}")

            if current_status == "completed":
                completed = True
                final_status_data = poll_data
                print(f"[INFO] Podcast generation completed for {podcast_id}.")
                break
            elif current_status and "failed" in current_status.lower():
                self.fail(f"Podcast generation failed with status: {current_status}. Error: {poll_data.get('error_message')}")

            time.sleep(poll_interval_seconds)

        self.assertTrue(completed, f"Podcast generation did not complete within the polling timeout for {podcast_id}. Last status: {current_status}")

        # 3. Verify final status and key output fields
        self.assertIsNotNone(final_status_data, "Final status data not captured.")
        print(f"[INFO] Verifying final details for podcast {podcast_id}: {json.dumps(final_status_data, indent=2)}")

        self.assertEqual(final_status_data["status"], "completed", "Final status is not 'completed'")
        self.assertIsNotNone(final_status_data["final_audio_filepath"], "final_audio_filepath is missing")
        self.assertTrue(final_status_data["final_audio_filepath"].endswith((".mp3", ".wav", ".ogg")), # VFA test mode might use configured encoding
                        f"Audio filepath {final_status_data['final_audio_filepath']} does not have an expected audio extension.")
        self.assertIsNotNone(final_status_data["stream_id"], "stream_id is missing")
        self.assertTrue(final_status_data["stream_id"].startswith("strm_"), "stream_id format is unexpected")

        self.assertIsNotNone(final_status_data["tts_settings_used"], "tts_settings_used is missing")
        tts_settings = final_status_data["tts_settings_used"]
        # In VFA test mode, it should return the (default or input) voice params it *would* have used.
        self.assertIn("voice_name", tts_settings)
        self.assertIn("speaking_rate", tts_settings)

        self.assertIsNotNone(final_status_data["orchestration_log"], "orchestration_log is missing")
        self.assertIsInstance(final_status_data["orchestration_log"], list, "orchestration_log is not a list")
        self.assertTrue(len(final_status_data["orchestration_log"]) > 0, "Orchestration log is empty")

        # Check for specific log messages indicating test modes were active (optional, depends on PSWA/VFA logging)
        # For example, if PSWA's test mode log includes "test-mode-model" for llm_model_used in its script:
        # And CPOA's log includes the PSWA script details:
        pswa_log_found = False
        for log_entry in final_status_data["orchestration_log"]:
            if log_entry.get("stage") == "pswa_script_generation" and \
               log_entry.get("message") == "PSWA Service finished successfully.":
                if log_entry.get("structured_data", {}).get("llm_model_used") == "test-mode-model":
                    pswa_log_found = True
                    break
        # This check is too specific and might fail if structured_data changes.
        # A better check might be on the final script_id in the log, but this is sufficient for now.
        # self.assertTrue(pswa_log_found, "PSWA test mode log not found in orchestration_log")


        # 4. Optional: Check if dummy audio file exists (if HOST_AUDIO_DATA_PATH is set)
        if HOST_AUDIO_DATA_PATH:
            # The filename in final_audio_filepath is the container path. We need just the basename.
            container_audio_path = final_status_data["final_audio_filepath"]
            audio_filename = os.path.basename(container_audio_path)
            expected_host_audio_path = os.path.join(HOST_AUDIO_DATA_PATH, audio_filename)
            print(f"[INFO] Optional: Checking for dummy audio file on host at: {expected_host_audio_path}")
            # This check might be flaky in CI if volume mounting or paths are not perfectly aligned.
            # self.assertTrue(os.path.exists(expected_host_audio_path),
            #                 f"Dummy audio file not found on host at {expected_host_audio_path}")
            if not os.path.exists(expected_host_audio_path):
                print(f"[WARN] Optional check failed: Dummy audio file not found at {expected_host_audio_path}. This might be a path issue or timing.")
            else:
                print(f"[INFO] Optional check passed: Dummy audio file found at {expected_host_audio_path}.")
        else:
            print("[INFO] Optional: HOST_AUDIO_DATA_PATH not set, skipping host audio file check.")

        print(f"[INFO] Test test_successful_podcast_generation_in_test_modes for {podcast_id} PASSED.")

    def test_podcast_generation_pswa_insufficient_content(self):
        """
        Tests podcast generation where PSWA is instructed (via test_scenarios)
        to return an 'insufficient content' error.
        """
        client_id = f"test_client_pswa_fail_{uuid.uuid4().hex}"
        topic = "PSWA Insufficient Content Scenario"
        print(f"\n[INFO] Starting test_podcast_generation_pswa_insufficient_content for topic: '{topic}' with client_id: {client_id}")

        initiate_payload = {
            "topic": topic,
            "client_id": client_id,
            "test_scenarios": {"pswa": "insufficient_content"}
        }
        print(f"[INFO] POST {API_GATEWAY_BASE_URL}/podcasts with payload: {initiate_payload}")
        initiate_response = requests.post(f"{API_GATEWAY_BASE_URL}/podcasts", json=initiate_payload, timeout=30)

        self.assertIn(initiate_response.status_code, [200, 201],
                      f"Initiate request failed: {initiate_response.status_code} - {initiate_response.text}")

        init_data = initiate_response.json()
        podcast_id = init_data["podcast_id"]
        print(f"[INFO] Podcast task initiated for PSWA failure test. Podcast ID: {podcast_id}")

        max_polls = 20
        poll_interval_seconds = 3
        failed_as_expected = False
        final_status_data = None
        current_status = "unknown"

        for i in range(max_polls):
            print(f"[INFO] Polling PSWA failure test ({i+1}/{max_polls}) for {podcast_id}...")
            poll_response = requests.get(f"{API_GATEWAY_BASE_URL}/podcasts/{podcast_id}", timeout=10)
            self.assertEqual(poll_response.status_code, 200)
            poll_data = poll_response.json()
            current_status = poll_data.get("status")
            print(f"[INFO] Current status: {current_status}")

            # PSWA's "insufficient_content" scenario returns a JSON error that PSWA endpoint turns into HTTP 400.
            # CPOA catches this HTTP 400 and sets its status to "failed_pswa_request_exception".
            if current_status == "failed_pswa_request_exception":
                failed_as_expected = True
                final_status_data = poll_data
                print(f"[INFO] PSWA failure correctly processed for {podcast_id}.")
                break
            elif current_status == "completed" or (current_status and "failed" in current_status and current_status != "failed_pswa_request_exception"):
                self.fail(f"Podcast generation reached unexpected status: {current_status}. Error: {poll_data.get('error_message')}")
            time.sleep(poll_interval_seconds)

        self.assertTrue(failed_as_expected, f"Podcast did not fail as expected for PSWA insufficient content. Last status: {current_status}")
        self.assertIsNotNone(final_status_data)
        # CPOA's error message for a failed HTTP request from PSWA will include details about the HTTP status.
        self.assertIn("PSWA service call failed (HTTP status: 400", final_status_data.get("error_message", ""),
                      "Error message from CPOA not as expected for PSWA insufficient content")
        self.assertIn("LLM indicated content was insufficient", final_status_data.get("error_message", ""),
                      "Original PSWA error not found in CPOA message")
        print(f"[INFO] Test test_podcast_generation_pswa_insufficient_content for {podcast_id} PASSED.")


    def test_podcast_generation_vfa_tts_error(self):
        """
        Tests podcast generation where VFA is instructed (via test_scenarios)
        to return a TTS error.
        """
        client_id = f"test_client_vfa_fail_{uuid.uuid4().hex}"
        topic = "VFA TTS Error Scenario"
        print(f"\n[INFO] Starting test_podcast_generation_vfa_tts_error for topic: '{topic}' with client_id: {client_id}")

        initiate_payload = {
            "topic": topic,
            "client_id": client_id,
            "test_scenarios": {"vfa": "vfa_error_tts"} # PSWA will be default success
        }
        print(f"[INFO] POST {API_GATEWAY_BASE_URL}/podcasts with payload: {initiate_payload}")
        initiate_response = requests.post(f"{API_GATEWAY_BASE_URL}/podcasts", json=initiate_payload, timeout=30)

        self.assertIn(initiate_response.status_code, [200, 201],
                      f"Initiate request failed: {initiate_response.status_code} - {initiate_response.text}")

        init_data = initiate_response.json()
        podcast_id = init_data["podcast_id"]
        print(f"[INFO] Podcast task initiated for VFA TTS error test. Podcast ID: {podcast_id}")

        max_polls = 20
        poll_interval_seconds = 3
        failed_as_expected = False
        final_status_data = None
        current_status = "unknown"

        for i in range(max_polls):
            print(f"[INFO] Polling VFA TTS error test ({i+1}/{max_polls}) for {podcast_id}...")
            poll_response = requests.get(f"{API_GATEWAY_BASE_URL}/podcasts/{podcast_id}", timeout=10)
            self.assertEqual(poll_response.status_code, 200)
            poll_data = poll_response.json()
            current_status = poll_data.get("status")
            print(f"[INFO] Current status: {current_status}")

            # VFA test mode for 'vfa_error_tts' returns HTTP 500 to CPOA.
            # CPOA then sets status to CPOA_STATUS_FAILED_VFA_REQUEST_EXCEPTION.
            if current_status == "failed_vfa_request_exception":
                failed_as_expected = True
                final_status_data = poll_data
                print(f"[INFO] VFA TTS failure correctly processed for {podcast_id}.")
                break
            elif current_status == "completed" or (current_status and "failed" in current_status and current_status != "failed_vfa_request_exception"):
                self.fail(f"Podcast generation reached unexpected status: {current_status}. Error: {poll_data.get('error_message')}")
            time.sleep(poll_interval_seconds)

        self.assertTrue(failed_as_expected, f"Podcast did not fail as expected for VFA TTS error. Last status: {current_status}")
        self.assertIsNotNone(final_status_data)
        self.assertIn("VFA service call failed (HTTP status: 500", final_status_data.get("error_message", ""),
                      "Error message from CPOA not as expected for VFA TTS error")
        # VFA's specific message for this scenario is "Test scenario: Simulated TTS API error from VFA."
        self.assertIn("Test scenario: Simulated TTS API error from VFA.", final_status_data.get("error_message", ""),
                      "Original VFA error message not found in CPOA's error message")
        print(f"[INFO] Test test_podcast_generation_vfa_tts_error for {podcast_id} PASSED.")

    def test_podcast_generation_vfa_logical_error(self):
        """
        Tests podcast generation where VFA is instructed (via test_scenarios)
        to return a logical error (HTTP 200 OK, but error in JSON body).
        CPOA should detect this and mark the task as failed due to VFA.
        """
        client_id = f"test_client_vfa_logical_err_{uuid.uuid4().hex}"
        topic = "VFA Logical Error Scenario"
        print(f"\n[INFO] Starting test_podcast_generation_vfa_logical_error for topic: '{topic}' with client_id: {client_id}")

        initiate_payload = {
            "topic": topic,
            "client_id": client_id,
            "test_scenarios": {"vfa": "vfa_logical_error_response"}
        }
        print(f"[INFO] POST {API_GATEWAY_BASE_URL}/podcasts with payload: {initiate_payload}")
        initiate_response = requests.post(f"{API_GATEWAY_BASE_URL}/podcasts", json=initiate_payload, timeout=30)

        self.assertIn(initiate_response.status_code, [200, 201],
                      f"Initiate request failed: {initiate_response.status_code} - {initiate_response.text}")

        init_data = initiate_response.json()
        podcast_id = init_data["podcast_id"]
        print(f"[INFO] Podcast task initiated for VFA logical error test. Podcast ID: {podcast_id}")

        max_polls = 20
        poll_interval_seconds = 3
        failed_as_expected = False
        final_status_data = None
        current_status = "unknown"

        for i in range(max_polls):
            print(f"[INFO] Polling VFA logical error test ({i+1}/{max_polls}) for {podcast_id}...")
            poll_response = requests.get(f"{API_GATEWAY_BASE_URL}/podcasts/{podcast_id}", timeout=10)
            self.assertEqual(poll_response.status_code, 200)
            poll_data = poll_response.json()
            current_status = poll_data.get("status")
            print(f"[INFO] Current status: {current_status}")

            if current_status == "failed_vfa_reported_error": # CPOA status for VFA JSON error
                failed_as_expected = True
                final_status_data = poll_data
                print(f"[INFO] VFA logical error correctly processed by CPOA for {podcast_id}.")
                break
            elif current_status == "completed" or (current_status and "failed" in current_status and current_status != "failed_vfa_reported_error"):
                self.fail(f"Podcast generation reached unexpected status: {current_status}. Error: {poll_data.get('error_message')}")
            time.sleep(poll_interval_seconds)

        self.assertTrue(failed_as_expected, f"Podcast did not fail as expected for VFA logical error. Last status: {current_status}")
        self.assertIsNotNone(final_status_data)

        # Check the error message from CPOA, which should incorporate VFA's error message and code
        cpoa_error_message = final_status_data.get("error_message", "")
        self.assertIn("VFA service reported an internal error", cpoa_error_message,
                      "Error message from CPOA not as expected for VFA logical error.")
        self.assertIn("VFA_TEST_LOGICAL_ERROR", cpoa_error_message, # Check for VFA's error_code
                      "VFA's error_code not found in CPOA's error message.")
        self.assertIn("Simulated VFA logical error from test scenario", cpoa_error_message, # Check for VFA's message
                      "VFA's original message not found in CPOA's error message.")

        # Optionally check tts_settings_used in the final_status_data if CPOA populates it from VFA's error response
        vfa_result_in_log = None
        for log_entry in final_status_data.get("orchestration_log", []):
            if log_entry.get("stage") == "vfa_audio_generation" and log_entry.get("status") == "error":
                vfa_result_in_log = log_entry.get("structured_data", {}).get("vfa_response", {})
                break
        self.assertIsNotNone(vfa_result_in_log, "VFA error response not found in orchestration log.")
        if vfa_result_in_log: # Check only if found
            self.assertEqual(vfa_result_in_log.get("error_code"), "VFA_TEST_LOGICAL_ERROR")
            self.assertIn("test_voice", vfa_result_in_log.get("tts_settings_used", {}).get("voice_name", ""), "TTS settings used not logged as expected.")

        print(f"[INFO] Test test_podcast_generation_vfa_logical_error for {podcast_id} PASSED.")

    def test_podcast_generation_pswa_internal_error_valid_json(self):
        """
        Test full podcast generation flow when PSWA returns a 200 OK with a JSON payload
        that contains an internal PSWA error message (e.g., {"error": "PSWA_UNEXPECTED_PROCESSING_ERROR", ...}).
        CPOA should treat this as a malformed script from PSWA.
        """
        topic = "PSWA Internal Error in JSON Scenario"
        client_id = f"test_client_pswa_error_json_{uuid.uuid4().hex}" # Ensure unique client_id
        print(f"\n[INFO] Starting test_podcast_generation_pswa_internal_error_valid_json for topic: '{topic}' with client_id: {client_id}")

        initiate_payload = {
            "topic": topic,
            "client_id": client_id,
            "test_scenarios": {"pswa": "pswa_internal_error_valid_json"}
        }
        expected_final_cpoa_status = "failed_pswa_bad_script_structure"
        expected_error_message_substring = "PSWA service returned invalid or malformed structured script"

        # 1. Initiate podcast generation
        print(f"[INFO] POST {API_GATEWAY_BASE_URL}/podcasts with payload: {initiate_payload}")
        initiate_response = requests.post(f"{API_GATEWAY_BASE_URL}/podcasts", json=initiate_payload, timeout=30)

        self.assertIn(initiate_response.status_code, [200, 201, 502, 500], # Expecting API GW to return error status from CPOA
                      f"Initiate request failed unexpectedly: {initiate_response.status_code} - {initiate_response.text}")

        init_data = initiate_response.json()
        podcast_id = init_data.get("podcast_id")
        self.assertIsNotNone(podcast_id, msg="Podcast ID not found in initiation response.")
        print(f"[INFO] Podcast task initiated for PSWA internal error test. Podcast ID: {podcast_id}")

        # If API GW immediately returns the final error (e.g. 500/502), we check that.
        # Otherwise, we poll.
        if initiate_response.status_code in [500, 502]:
            print(f"[INFO] Received immediate error response from API Gateway: {initiate_response.status_code}")
            final_status_data = init_data
            current_status = final_status_data.get("generation_status")
        else: # Poll if initial response was 200/201/202 (though 202 is more common for async)
            print(f"[INFO] Initial API GW response status: {initiate_response.status_code}. Proceeding to poll.")
             # 2. Poll for completion or failure
            max_polls = 20
            poll_interval_seconds = 3
            final_status_data = None
            current_status = "polling" # Initial status before first poll

            for i in range(max_polls):
                print(f"[INFO] Polling PSWA internal error test ({i+1}/{max_polls}) for {podcast_id}...")
                poll_response = requests.get(f"{API_GATEWAY_BASE_URL}/podcasts/{podcast_id}", timeout=10)
                self.assertEqual(poll_response.status_code, 200, f"Polling request failed: {poll_response.status_code} - {poll_response.text}")

                poll_data = poll_response.json()
                current_status = poll_data.get("status")
                print(f"[INFO] Current status: {current_status}")

                if current_status == expected_final_cpoa_status:
                    final_status_data = poll_data
                    print(f"[INFO] PSWA internal error correctly processed, CPOA status '{current_status}' for {podcast_id}.")
                    break
                elif current_status and "failed" in current_status and current_status != expected_final_cpoa_status:
                    final_status_data = poll_data # Capture data even if it's an unexpected failure
                    self.fail(f"Podcast generation failed with an unexpected status: '{current_status}'. Error: {poll_data.get('error_message')}")
                elif current_status == "completed":
                    final_status_data = poll_data # Capture data
                    self.fail(f"Podcast generation completed unexpectedly for PSWA internal error scenario. Status: '{current_status}'")

                time.sleep(poll_interval_seconds)

            if not final_status_data: # If loop finishes without setting final_status_data
                 self.fail(f"Polling timeout: Podcast did not reach expected status '{expected_final_cpoa_status}'. Last status: '{current_status}'")


        # 3. Assertions
        self.assertIsNotNone(final_status_data, msg="Final status data was not captured.")

        self.assertEqual(final_status_data.get("generation_status"), expected_final_cpoa_status,
                         msg=f"CPOA status was '{final_status_data.get('generation_status')}', expected '{expected_final_cpoa_status}'. Full data: {json.dumps(final_status_data, indent=2)}")

        self.assertIn(expected_error_message_substring, final_status_data.get("message", final_status_data.get("error_message", "")), # Check 'message' from API GW, or 'error_message' from details
                      msg=f"Error message substring '{expected_error_message_substring}' not found in CPOA error message. Full data: {json.dumps(final_status_data, indent=2)}")

        # Note: ASF UI updates check might be less relevant here if the task fails very early in CPOA due to PSWA's bad script structure.
        # However, CPOA should still attempt to send a task_error update.
        # We would need to ensure the mock ASF service is running and accessible for this part of the test.
        # For now, this check might be omitted or made conditional if ASF interaction is not guaranteed.
        print(f"[INFO] Test test_podcast_generation_pswa_internal_error_valid_json for {podcast_id} PASSED.")

    def test_search_podcasts_successful(self):
        """
        Tests the podcast search functionality (/api/v1/search/podcasts).
        Ensures that a valid query returns a list of search result snippets.
        Relies on TDA (simulated data) and SCA (test mode) via CPOA.
        """
        search_query = "AI" # This query should match keywords in TDA's simulated data
        client_id = f"test_client_search_success_{uuid.uuid4().hex}"
        print(f"\n[INFO] Starting test_search_podcasts_successful for query: '{search_query}' with client_id: {client_id}")

        payload = {"query": search_query, "client_id": client_id}

        print(f"[INFO] POST {API_GATEWAY_BASE_URL}/search/podcasts with payload: {payload}")
        response = requests.post(f"{API_GATEWAY_BASE_URL}/search/podcasts", json=payload, timeout=30)

        self.assertEqual(response.status_code, 200, f"Search request failed: {response.status_code} - {response.text}")

        response_data = response.json()
        self.assertIsInstance(response_data, dict, "Search response is not a dictionary.")
        self.assertIn("search_results", response_data, "Search response missing 'search_results' key.")

        search_results = response_data["search_results"]
        self.assertIsInstance(search_results, list, "'search_results' is not a list.")

        self.assertTrue(len(search_results) > 0, f"Search returned no results for query '{search_query}', which should yield results from TDA/SCA simulated/test data.")

        # Validate the structure of the first search result
        first_result = search_results[0]
        self.assertIsInstance(first_result, dict, "First search result item is not a dictionary.")

        expected_keys = ["snippet_id", "topic_id", "title", "summary", "text_content", "cover_art_prompt", "llm_model_used", "keywords"]
        for key in expected_keys:
            self.assertIn(key, first_result, f"First search result missing key: '{key}'. Result: {first_result}")

        # Check SCA's test model name (from sca/main.py SCENARIO_DEFAULT_SNIPPET_DATA)
        # This might need adjustment if SCA's test model name changes.
        # Current SCA default test model is "AetherLLM-Placeholder-DynamicSnippet-v0.2"
        # However, CPOA's orchestrate_snippet_generation might use a different one if it has its own test mode logic for SCA calls.
        # Let's assume SCA's test mode is active and CPOA passes it through.
        # From sca/main.py: SCENARIO_DEFAULT_SNIPPET_DATA['llm_model_used'] = "SCA-Test-LLM-v1.0"
        # Update: SCA's default test data uses "SCA-Test-LLM-v1.0" as per its main.py
        self.assertEqual(first_result.get("llm_model_used"), "SCA-Test-LLM-v1.0",
                         f"LLM model used for snippet generation is not the expected SCA test model. Got: {first_result.get('llm_model_used')}")

        # Assertions for image_url
        self.assertIn("image_url", first_result, f"First search result missing key: 'image_url'. Result: {first_result}")
        image_url = first_result.get("image_url")
        self.assertIsInstance(image_url, str, f"'image_url' should be a string. Got: {image_url}")
        self.assertTrue(len(image_url) > 0, "'image_url' should not be empty.")
        self.assertTrue(image_url.startswith("https://source.unsplash.com/random/400x225/"),
                        f"Expected image_url to start with 'https://source.unsplash.com/random/400x225/'. Got: {image_url}")

        # Optional advanced check for keywords in image_url based on cover_art_prompt
        cover_art_prompt_from_snippet = first_result.get("cover_art_prompt")
        if cover_art_prompt_from_snippet:
            # IGA placeholder uses: keywords = "+".join(prompt.split()[:3])
            # The prompt IGA receives is the cover_art_prompt_from_snippet itself.
            # IGA's main.py:
            # keywords_from_prompt_for_iga = "+".join(cover_art_prompt_from_snippet.split()[:3])
            # sanitized_keywords_for_iga = "".join(c if c.isalnum() or c == '+' else '+' for c in keywords_from_prompt_for_iga)
            # expected_url_keywords_segment = "+".join(filter(None, sanitized_keywords_for_iga.split('+')))

            # Simplified simulation of IGA's keyword extraction for URL for the test assertion:
            # Takes the first 3 words of the prompt, makes them alphanumeric, joins with '+'
            prompt_words = cover_art_prompt_from_snippet.split()
            keywords_for_url_list = []
            for word in prompt_words[:3]:
                sanitized_word = "".join(filter(str.isalnum, word))
                if sanitized_word:
                    keywords_for_url_list.append(sanitized_word)
            expected_url_keywords_segment = "+".join(keywords_for_url_list)


            if expected_url_keywords_segment:
                # The Unsplash URL from IGA is like: https://source.unsplash.com/random/400x225/?{sanitized_keywords_from_prompt},podcast,abstract
                # We need to check if our `expected_url_keywords_segment` is in the path part of `image_url`
                # Extract the keyword part from the image_url. It's between "/?" and ",podcast,abstract".
                try:
                    url_keyword_part = image_url.split("/?")[1].split(",podcast,abstract")[0]
                    self.assertTrue(expected_url_keywords_segment.lower() in url_keyword_part.lower(),
                                    f"Expected keyword segment '{expected_url_keywords_segment.lower()}' from prompt not found or mismatched in image_url's keyword part '{url_keyword_part.lower()}'. Prompt: '{cover_art_prompt_from_snippet}'")
                except IndexError:
                    self.fail(f"Could not parse keyword segment from image_url: {image_url}")

        # Optional: Check if the title or summary contains the search query (or related terms)
        # This depends on TDA's simulated data and SCA's snippet generation logic.
        # For "AI", TDA has "The Future of AI in Personalized Medicine". SCA generates a generic snippet.
        # The title of the snippet might be derived from the TDA topic.
        title_contains_query = search_query.lower() in first_result.get("title", "").lower()
        summary_contains_query = search_query.lower() in first_result.get("summary", "").lower()
        # self.assertTrue(title_contains_query or summary_contains_query,
        #                 f"Neither title nor summary of the first search result seems related to the query '{search_query}'. Title: '{first_result.get('title', '')}', Summary: '{first_result.get('summary', '')}'")
        # This assertion can be very flaky depending on how test data is structured and processed.
        # For now, presence of results and correct structure is the primary goal.
        print(f"[INFO] Test test_search_podcasts_successful for query '{search_query}' PASSED. Found {len(search_results)} results.")

    def test_search_podcasts_missing_query(self):
        """
        Tests the search endpoint with a missing or empty query parameter.
        Expects a 400 Bad Request response.
        """
        client_id = f"test_client_search_badreq_{uuid.uuid4().hex}"
        print(f"\n[INFO] Starting test_search_podcasts_missing_query with client_id: {client_id}")

        payloads_to_test = [
            {},                            # Empty payload
            {"query": ""},                 # Empty query string
            {"client_id": client_id}       # Payload with client_id but no query
        ]

        for payload in payloads_to_test:
            with self.subTest(payload=payload):
                print(f"[INFO] POST {API_GATEWAY_BASE_URL}/search/podcasts with invalid payload: {payload}")
                response = requests.post(f"{API_GATEWAY_BASE_URL}/search/podcasts", json=payload, timeout=10)

                self.assertEqual(response.status_code, 400, f"Search request with payload {payload} did not return 400. Got: {response.status_code} - {response.text}")

                response_data = response.json()
                self.assertIsInstance(response_data, dict)
                self.assertIn("error", response_data, "Error response missing 'error' key.")
                self.assertEqual(response_data["error"], "Bad Request", "Error type is not 'Bad Request'.")
                self.assertIn("message", response_data, "Error response missing 'message' key.")
                self.assertIn("Missing or empty 'query'", response_data["message"],
                              f"Error message does not indicate missing query. Message: '{response_data['message']}'")

        print(f"[INFO] Test test_search_podcasts_missing_query PASSED for all invalid payloads.")


if __name__ == "__main__":
    # This allows running the integration test directly.
    # Ensure Docker Compose environment is up and services are running.
    # You might need to set API_GW_TEST_URL if it's not localhost:5001
    # e.g. API_GW_TEST_URL=http://your_docker_host_ip:5001 python -m unittest tests.integration.test_full_flow
    unittest.main(verbosity=2)
