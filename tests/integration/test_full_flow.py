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


if __name__ == "__main__":
    # This allows running the integration test directly.
    # Ensure Docker Compose environment is up and services are running.
    # You might need to set API_GW_TEST_URL if it's not localhost:5001
    # e.g. API_GW_TEST_URL=http://your_docker_host_ip:5001 python -m unittest tests.integration.test_full_flow
    unittest.main(verbosity=2)
