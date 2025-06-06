import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys
import json

# Adjust path
current_dir = os.path.dirname(os.path.abspath(__file__))
vfa_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(vfa_dir)
project_root_dir = os.path.dirname(aethercast_dir)

sys.path.insert(0, project_root_dir)
sys.path.insert(0, aethercast_dir)

from aethercast.vfa import main as vfa_main
# No longer directly import or mock Google Cloud TTS SDK components here
# import google.cloud.texttospeech
# import google.api_core.exceptions as google_exceptions
import requests # For mocking AIMS_TTS calls

class TestForgeVoiceLogic(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        # Updated mock_vfa_config for AIMS_TTS
        self.mock_vfa_config = {
            "AIMS_TTS_SERVICE_URL": "http://mock-aims-tts.test/v1/synthesize",
            "AIMS_TTS_REQUEST_TIMEOUT_SECONDS": 30,
            "VFA_SHARED_AUDIO_DIR": "/tmp/vfa_test_audio", # For test mode dummy files
            "VFA_MIN_SCRIPT_LENGTH": 10,
            # Removed Google TTS specific defaults like VFA_TTS_VOICE_NAME etc.
            # VFA now passes params to AIMS_TTS, which has its own defaults.
            "VFA_TEST_MODE_ENABLED": False # Default to False for logic tests unless overridden
        }
        self.config_patcher = patch.dict(vfa_main.vfa_config, self.mock_vfa_config, clear=True)
        self.mock_config = self.config_patcher.start()

        # os.makedirs might still be used by test mode for dummy files
        self.makedirs_patcher = patch('os.makedirs')
        self.mock_makedirs = self.makedirs_patcher.start()

        # VFA_IMPORTS_SUCCESSFUL is no longer relevant as Google SDK not used directly
        # self.imports_patcher = patch.object(vfa_main, 'VFA_IMPORTS_SUCCESSFUL', True)
        # self.mock_imports_successful = self.imports_patcher.start()
        
        # google_audio_encoding_map is no longer relevant
        # self.encoding_map_patcher.stop()

    def tearDown(self):
        self.config_patcher.stop()
        self.makedirs_patcher.stop()
        # self.imports_patcher.stop()

    @patch('requests.post') # Mock the call to AIMS_TTS
    def test_forge_voice_success_via_aims_tts(self, mock_requests_post):
        # Ensure test mode is off for this test
        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            mock_aims_tts_response = MagicMock()
            mock_aims_tts_response.status_code = 200
            mock_aims_tts_response.json.return_value = {
                "request_id": "aims_tts_req_123",
                "voice_id": "aims-voice-en-US-Wavenet-D",
                "audio_url": "/shared_audio/aims_tts/generated_audio_123.mp3",
                "audio_duration_seconds": 10.5,
                "audio_format": "mp3"
            }
            mock_requests_post.return_value = mock_aims_tts_response

            structured_script = {
                "script_id": "s1", "topic": "Test Topic via AIMS_TTS", "title": "AIMS_TTS Podcast Title",
                "segments": [{"segment_title": "INTRO", "content": "Intro for AIMS_TTS."}]
            }
            result = vfa_main.forge_voice(structured_script)

            self.assertEqual(result["status"], "success")
            self.assertIn("audio successfully synthesized via aims_tts", result["message"].lower())
            self.assertEqual(result["audio_filepath"], "/shared_audio/aims_tts/generated_audio_123.mp3")
            self.assertEqual(result["audio_format"], "mp3")
            self.assertEqual(result["engine_used"], "aims_tts_via_aims-voice-en-US-Wavenet-D")

            mock_requests_post.assert_called_once()
            call_args = mock_requests_post.call_args.kwargs
            self.assertEqual(call_args['url'], self.mock_vfa_config["AIMS_TTS_SERVICE_URL"])
            self.assertIn("Intro for AIMS_TTS.", call_args['json']['text'])
            # Check tts_settings_used reflects data from AIMS_TTS
            self.assertIsNotNone(result.get("tts_settings_used"))
            self.assertEqual(result["tts_settings_used"]["voice_name"], "aims-voice-en-US-Wavenet-D")
            self.assertEqual(result["tts_settings_used"]["audio_encoding"], "mp3")


    @patch('requests.post')
    def test_forge_voice_with_custom_voice_params_via_aims_tts(self, mock_requests_post):
        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            custom_voice_params_to_send = {
                "voice_name": "custom-aims-voice", # Mapped to voice_id for AIMS_TTS
                "audio_encoding": "ogg_opus",     # Mapped to audio_format for AIMS_TTS
                "speaking_rate": 1.2,
                "pitch": -2.0
            }

            mock_aims_tts_response = MagicMock()
            mock_aims_tts_response.status_code = 200
            mock_aims_tts_response.json.return_value = {
                "request_id": "aims_tts_req_custom",
                "voice_id": "custom-aims-voice-reported", # AIMS_TTS might confirm or change voice
                "audio_url": "/shared_audio/aims_tts/custom_audio.ogg",
                "audio_duration_seconds": 8.0,
                "audio_format": "ogg_opus" # AIMS_TTS confirms format
            }
            mock_requests_post.return_value = mock_aims_tts_response

            structured_script = {"segments": [{"content": "Testing custom voice parameters via AIMS_TTS."}]}
            result = vfa_main.forge_voice(structured_script, voice_params_input=custom_voice_params_to_send)

            self.assertEqual(result["status"], "success")

            sent_payload_to_aims = mock_requests_post.call_args.kwargs['json']
            self.assertEqual(sent_payload_to_aims.get("voice_id"), "custom-aims-voice")
            self.assertEqual(sent_payload_to_aims.get("audio_format"), "ogg_opus")
            self.assertEqual(sent_payload_to_aims.get("speech_rate"), 1.2)
            self.assertEqual(sent_payload_to_aims.get("pitch"), -2.0)

            used_settings = result["tts_settings_used"]
            self.assertEqual(used_settings["voice_name"], "custom-aims-voice-reported")
            self.assertEqual(used_settings["audio_encoding"], "ogg_opus")
            self.assertEqual(used_settings["speaking_rate"], 1.2)
            self.assertEqual(used_settings["pitch"], -2.0)
            self.assertEqual(result["audio_format"], "ogg_opus")


    def test_forge_voice_pswa_error_script_no_aims_call(self): # Behavior should be same
        error_script = {"full_raw_script": "[ERROR] Insufficient content"}
        result = vfa_main.forge_voice(error_script)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("error message from PSWA", result["message"])

    def test_forge_voice_script_too_short_no_aims_call(self): # Behavior should be same
        script_no_text = {"segments": [{"content": "short"}]} # Assuming min_length is > 5
        with patch.dict(vfa_main.vfa_config, {"VFA_MIN_SCRIPT_LENGTH": 10}):
            result = vfa_main.forge_voice(script_no_text)
            self.assertEqual(result["status"], "skipped")
            self.assertIn("too short", result["message"])

    @patch('requests.post')
    def test_forge_voice_aims_tts_http_error(self, mock_requests_post):
        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            mock_aims_response = MagicMock()
            mock_aims_response.status_code = 500
            mock_aims_response.reason = "AIMS TTS Internal Server Error"
            mock_aims_response.text = '{"error": {"type": "aims_tts_server_error", "message": "AIMS TTS exploded"}}'
            mock_requests_post.side_effect = requests.exceptions.HTTPError(response=mock_aims_response)

            valid_script = {"segments": [{"content": "A valid script long enough for TTS attempt."}]}
            result = vfa_main.forge_voice(valid_script)

            self.assertIn("error_code", result)
            self.assertEqual(result["error_code"], "VFA_AIMS_TTS_HTTP_ERROR")
            self.assertIn("AIMS_TTS request failed (HTTP 500)", result["message"])
            self.assertIn("AIMS TTS exploded", result["details"])

    @patch('requests.post')
    def test_forge_voice_aims_tts_timeout(self, mock_requests_post):
        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            mock_requests_post.side_effect = requests.exceptions.Timeout("AIMS_TTS timed out")
            valid_script = {"segments": [{"content": "A valid script."}]}
            result = vfa_main.forge_voice(valid_script)
            self.assertEqual(result["error_code"], "VFA_AIMS_TTS_TIMEOUT")
            self.assertIn("AIMS_TTS request timed out.", result["message"])

    # Test mode tests need adjustment to reflect simulated AIMS_TTS interaction if scenario is 'default'
    def test_forge_voice_test_mode_default_scenario(self):
        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": True, "VFA_SHARED_AUDIO_DIR": "/test/dummy_audio"}):
            # No X-Test-Scenario header
            response = vfa_main.forge_voice({"topic": "Test Default", "segments": [{"content": "Sufficiently long script for test."}]})
            self.assertEqual(response["status"], "success")
            self.assertIn("(VFA TEST MODE - dummy file, AIMS_TTS call bypassed).", response["message"])
            self.assertTrue(response["audio_filepath"].startswith("/test/dummy_audio/aethercast_audio_vfa_testmode_"))
            self.assertEqual(response["engine_used"], "test_mode_bypassed_aims_tts")

    def test_forge_voice_test_mode_aims_tts_error_scenario(self):
        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": True}):
            # Simulate request object for header access
            with patch.object(vfa_main, 'request', MagicMock(headers={'X-Test-Scenario': 'vfa_error_aims_tts'})):
                response = vfa_main.forge_voice({"topic": "Test AIMS TTS Error", "segments": [{"content": "Script for AIMS TTS error test."}]})
                self.assertEqual(response["error_code"], "VFA_TEST_MODE_AIMS_TTS_ERROR")
                self.assertIn(vfa_main.VFA_TEST_SCENARIO_AIMS_TTS_ERROR_MSG, response["message"])
                self.assertEqual(response["engine_used"], "test_mode_aims_tts_error")


class TestForgeVoiceEndpoint(unittest.TestCase):
    def setUp(self):
        vfa_main.app.config['TESTING'] = True
        self.client = vfa_main.app.test_client()
        # Updated mock config for AIMS_TTS integration for endpoint tests
        self.mock_vfa_config_for_endpoint = {
            "AIMS_TTS_SERVICE_URL": "http://mock-aims-tts.test/v1/synthesize",
            "AIMS_TTS_REQUEST_TIMEOUT_SECONDS": 10,
            "VFA_SHARED_AUDIO_DIR": "/tmp/vfa_test_audio_endpoint",
            "VFA_MIN_SCRIPT_LENGTH": 5,
            "VFA_TEST_MODE_ENABLED": True # Most endpoint tests will use test mode
        }
        self.config_patcher = patch.dict(vfa_main.vfa_config, self.mock_vfa_config_for_endpoint, clear=True)
        self.mock_config = self.config_patcher.start()

        self.makedirs_patcher = patch('os.makedirs')
        self.mock_makedirs = self.makedirs_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.makedirs_patcher.stop() # Stop os.makedirs patch

    @patch('aethercast.vfa.main.forge_voice')
    def test_handle_forge_voice_success(self, mock_forge_voice_func):
        mock_forge_voice_func.return_value = {
            "status": "success", "message": "Audio created",
            "audio_filepath": "/path/audio.mp3", "stream_id": "s1",
            "tts_settings_used": {"voice_name": "default"} # ensure this key exists
        }
        payload = {
            "script": {"script_id": "s1", "topic": "Test", "title": "Test", "full_raw_script": "Test script", "segments": []},
            "voice_params": {"voice_name": "custom-voice"} # Test sending voice_params
        }
        response = self.client.post('/forge_voice', json=payload)
        
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        self.assertEqual(json_data["status"], "success")
        self.assertEqual(json_data["audio_filepath"], "/path/audio.mp3")
        # Check that voice_params are passed through
        mock_forge_voice_func.assert_called_once_with(payload["script"], voice_params_input=payload["voice_params"])

    def test_handle_forge_voice_missing_script(self):
        response = self.client.post('/forge_voice', json={})
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data.get("error_code"), "VFA_VALIDATION_ERROR")
        self.assertEqual(json_data.get("message"), "Invalid input")
        self.assertEqual(json_data.get("details"), "Missing 'script' parameter")

    def test_handle_forge_voice_script_not_dict(self):
        response = self.client.post('/forge_voice', json={"script": "this is a string, not a dict"})
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data.get("error_code"), "VFA_VALIDATION_ERROR")
        self.assertEqual(json_data.get("message"), "Invalid input")
        self.assertEqual(json_data.get("details"), "'script' parameter must be a valid JSON object (dictionary).")

    def test_handle_forge_voice_no_json_payload(self):
        response = self.client.post('/forge_voice', data="not a json payload", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data.get("error_code"), "VFA_PAYLOAD_ERROR")
        self.assertEqual(json_data.get("message"), "Invalid payload")
        self.assertEqual(json_data.get("details"), "No JSON payload received")

    def test_handle_forge_voice_voice_params_not_dict(self):
        payload = {
            "script": {"script_id": "s_vp_err", "topic": "VP Error", "full_raw_script": "Test script"},
            "voice_params": "this is a string, not a dict"
        }
        response = self.client.post('/forge_voice', json=payload)
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data.get("error_code"), "VFA_VALIDATION_ERROR")
        self.assertEqual(json_data.get("message"), "Invalid input")
        self.assertEqual(json_data.get("details"), "'voice_params' parameter must be a valid JSON object if provided.")

    @patch('aethercast.vfa.main.forge_voice')
    def test_handle_forge_voice_skipped(self, mock_forge_voice_func):
        mock_forge_voice_func.return_value = {"status": "skipped", "message": "Script too short"}
        response = self.client.post('/forge_voice', json={"script": {"full_raw_script": "short"}}) # Pass a dict
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        self.assertEqual(json_data["status"], "skipped")

    @patch('aethercast.vfa.main.forge_voice')
    def test_handle_forge_voice_error(self, mock_forge_voice_func):
        mock_forge_voice_func.return_value = {"status": "error", "message": "TTS failed"}
        response = self.client.post('/forge_voice', json={"script": {"full_raw_script": "test"}}) # Pass a dict
        self.assertEqual(response.status_code, 500)
        json_data = response.get_json()
        self.assertEqual(json_data["status"], "error")

    # --- New Tests for Scenario-Based Test Mode in Endpoint ---

    @patch('os.path.exists') # Mock os.path.exists as forge_voice (test mode) might not create file
    @patch('builtins.open', new_callable=mock_open) # Mock open to check if file write is attempted
    def test_forge_voice_endpoint_test_mode_default_scenario(self, mock_file_open, mock_os_path_exists):
        """Test VFA endpoint in test mode with default success scenario."""
        mock_os_path_exists.return_value = True # Assume file "created" by test mode exists for this check

        payload = {"script": {"topic": "Test Default", "full_raw_script":"Sufficiently long script for test."}}
        # No X-Test-Scenario header, should use default success
        response = self.client.post('/forge_voice', json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("(TEST MODE - dummy file)", data["message"])
        self.assertIsNotNone(data["audio_filepath"])
        self.assertTrue(data["audio_filepath"].startswith(self.mock_vfa_config_for_endpoint["VFA_SHARED_AUDIO_DIR"]))
        self.assertEqual(data["engine_used"], "test_mode_tts_success")
        mock_file_open.assert_called_once() # Check that dummy file write was attempted

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_endpoint_test_mode_vfa_error_tts_scenario(self, mock_file_open, mock_os_path_exists):
        """Test VFA endpoint in test mode for 'vfa_error_tts' scenario."""
        headers = {'X-Test-Scenario': 'vfa_error_tts'}
        payload = {"script": {"topic": "Test TTS Error", "full_raw_script":"Script for TTS error test."}}
        response = self.client.post('/forge_voice', json=payload, headers=headers)

        self.assertEqual(response.status_code, 500) # Should be 500 as it's an error status
        data = response.get_json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["message"], vfa_main.VFA_TEST_SCENARIO_TTS_ERROR_MSG)
        self.assertIsNone(data["audio_filepath"])
        self.assertEqual(data["engine_used"], "test_mode_tts_api_error")
        mock_file_open.assert_not_called() # No file should be created or attempted

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_endpoint_test_mode_vfa_error_file_save_scenario(self, mock_file_open, mock_os_path_exists):
        """Test VFA endpoint in test mode for 'vfa_error_file_save' scenario."""
        headers = {'X-Test-Scenario': 'vfa_error_file_save'}
        payload = {"script": {"topic": "Test File Save Error", "full_raw_script":"Script for file save error test."}}
        response = self.client.post('/forge_voice', json=payload, headers=headers)

        self.assertEqual(response.status_code, 500) # Should be 500
        data = response.get_json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["message"], vfa_main.VFA_TEST_SCENARIO_FILE_SAVE_ERROR_MSG)
        self.assertIsNotNone(data["audio_filepath"]) # Filepath might be determined
        self.assertEqual(data["engine_used"], "test_mode_tts_file_error")
        # In this specific scenario, os.makedirs might be called, but open() for writing the file itself shouldn't.
        # The current VFA test mode logic for 'vfa_error_file_save' doesn't attempt to write the file.
        mock_file_open.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
