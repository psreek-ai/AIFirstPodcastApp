import unittest
from unittest.mock import patch, MagicMock, call
import os
import sys
import json

# Adjust path to import CPOA main module
# Assuming this test file is in aethercast/cpoa/tests/
current_dir = os.path.dirname(os.path.abspath(__file__))
cpoa_dir = os.path.dirname(current_dir) # This should be aethercast/cpoa/
aethercast_dir = os.path.dirname(cpoa_dir) # This should be aethercast/
project_root_dir = os.path.dirname(aethercast_dir) # This should be the project root

if cpoa_dir not in sys.path:
    sys.path.insert(0, cpoa_dir)
if aethercast_dir not in sys.path: # If cpoa.main imports other aethercast modules
    sys.path.insert(0, aethercast_dir)
if project_root_dir not in sys.path:
     sys.path.insert(0, project_root_dir)


from aethercast.cpoa import main as cpoa_main
# Import requests specifically for requests.exceptions.RequestException etc.
import requests


class TestUpdateTaskStatusInDb(unittest.TestCase):
    @patch('sqlite3.connect')
    def test_update_success(self, mock_sqlite_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Call the function to be tested from the imported cpoa_main
        cpoa_main._update_task_status_in_db("dummy.db", "task_123", "completed", "All good")

        mock_sqlite_connect.assert_called_once_with("dummy.db")
        mock_conn.cursor.assert_called_once()
        self.assertEqual(mock_cursor.execute.call_count, 1)
        
        args, _ = mock_cursor.execute.call_args
        self.assertIn("UPDATE podcasts SET cpoa_status = ?, cpoa_error_message = ?, last_updated_timestamp = ?", args[0])
        self.assertEqual(args[1][0], "completed")
        self.assertEqual(args[1][1], "All good")
        self.assertEqual(args[1][3], "task_123")
        
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('sqlite3.connect')
    def test_update_db_error(self, mock_sqlite_connect):
        mock_conn = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        # Simulate a database error during cursor execution
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = sqlite3.Error("Test DB error on execute")


        with patch.object(cpoa_main.logger, 'error') as mock_logger_error:
            cpoa_main._update_task_status_in_db("dummy.db", "task_error", "failed_db_error", "DB issue")

            self.assertTrue(mock_logger_error.called)
            call_args_list = mock_logger_error.call_args_list
            found_error_log = False
            for call_arg in call_args_list:
                if "Database error for task task_error" in call_arg[0][0] and "Test DB error on execute" in call_arg[0][0]:
                    found_error_log = True
                    break
            self.assertTrue(found_error_log, "Expected database execution error log message not found.")

        # Ensure commit was not called if execute failed
        mock_conn.commit.assert_not_called()
        mock_conn.close.assert_called_once() # Connection should still be closed


class TestOrchestratePodcastGeneration(unittest.TestCase):

    def setUp(self):
        self.mock_env_vars = {
            "PSWA_SERVICE_URL": "http://mockpswa.test/weave_script",
            "VFA_SERVICE_URL": "http://mockvfa.test/forge_voice",
            "ASF_NOTIFICATION_URL": "http://mockasf.test/notify",
            "ASF_WEBSOCKET_BASE_URL": "ws://mockasf.test/stream",
            "SCA_SERVICE_URL": "http://mocksca.test/craft_snippet",
            "CPOA_DATABASE_PATH": "test_cpoa_orchestration.db",
            "CPOA_ASF_SEND_UI_UPDATE_URL": "http://mockasf.test/internal/send_ui_update", # Added
            "CPOA_SERVICE_RETRY_COUNT": "1",
            "CPOA_SERVICE_RETRY_BACKOFF_FACTOR": "0.01"
        }

        # IMPORTANT: We need to patch os.getenv BEFORE cpoa_main is loaded if its constants
        # are defined at the module level. However, cpoa_main is already imported.
        # So, we will patch the global variables within cpoa_main directly after they've been loaded,
        # or use patch.dict(os.environ, self.mock_env_vars) if cpoa_main re-reads from os.environ.
        # For simplicity here, assuming cpoa_main's global config vars can be patched if necessary,
        # or that its functions use os.getenv dynamically (which they do for retry counts).

        # Patching the globally loaded config values in cpoa_main
        self.pswa_url_patch = patch.object(cpoa_main, 'PSWA_SERVICE_URL', self.mock_env_vars['PSWA_SERVICE_URL'])
        self.vfa_url_patch = patch.object(cpoa_main, 'VFA_SERVICE_URL', self.mock_env_vars['VFA_SERVICE_URL'])
        self.asf_url_patch = patch.object(cpoa_main, 'ASF_NOTIFICATION_URL', self.mock_env_vars['ASF_NOTIFICATION_URL'])
        self.asf_ui_url_patch = patch.object(cpoa_main, 'CPOA_ASF_SEND_UI_UPDATE_URL', self.mock_env_vars['CPOA_ASF_SEND_UI_UPDATE_URL']) # Added
        self.sca_url_patch = patch.object(cpoa_main, 'SCA_SERVICE_URL', self.mock_env_vars['SCA_SERVICE_URL'])
        self.db_path_patch = patch.object(cpoa_main, 'CPOA_DATABASE_PATH', self.mock_env_vars['CPOA_DATABASE_PATH'])
        self.retry_count_patch = patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_COUNT', int(self.mock_env_vars['CPOA_SERVICE_RETRY_COUNT']))
        self.backoff_patch = patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_BACKOFF_FACTOR', float(self.mock_env_vars['CPOA_SERVICE_RETRY_BACKOFF_FACTOR']))

        self.pswa_url_patch.start()
        self.vfa_url_patch.start()
        self.asf_url_patch.start()
        self.asf_ui_url_patch.start() # Added
        self.sca_url_patch.start()
        self.db_path_patch.start()
        self.retry_count_patch.start()
        self.backoff_patch.start()
        
        # Ensure WCHA is considered imported successfully for most tests
        self.wcha_import_patch = patch.object(cpoa_main, 'WCHA_IMPORT_SUCCESSFUL', True)
        self.mock_wcha_import = self.wcha_import_patch.start()


    def tearDown(self):
        self.pswa_url_patch.stop()
        self.vfa_url_patch.stop()
        self.asf_url_patch.stop()
        self.asf_ui_url_patch.stop() # Added
        self.sca_url_patch.stop()
        self.db_path_patch.stop()
        self.retry_count_patch.stop()
        self.backoff_patch.stop()
        self.wcha_import_patch.stop()


    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_successful_run(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = "Detailed content about a fascinating topic."

        mock_pswa_structured_script = {
            "script_id": "pswa_script_123", "topic": "Test Topic", "title": "A Brilliant Podcast Title",
            "full_raw_script": "Full script text",
            "segments": [{"segment_title": "INTRO", "content": "Intro here."}]
        }
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = mock_pswa_structured_script
        
        mock_vfa_response_data = {
            "status": "success", "audio_filepath": "/shared/audio/podcast_123.mp3",
            "stream_id": "stream_abc",
            "tts_settings_used": {"voice_name": "en-TEST-Voice", "speaking_rate": 1.0, "pitch": 0.0, "audio_encoding": "MP3"}
        }
        mock_vfa_response = MagicMock(status_code=200)
        mock_vfa_response.json.return_value = mock_vfa_response_data

        mock_asf_response = MagicMock(status_code=200)
        mock_asf_response.json.return_value = {"message": "ASF notified"}

        def requests_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL: return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                self.assertIn("json", kwargs)
                self.assertEqual(kwargs["json"]["script"]["script_id"], "pswa_script_123")
                self.assertNotIn("voice_params", kwargs["json"]) # No voice_params in this call
                return mock_vfa_response
            if url == cpoa_main.ASF_NOTIFICATION_URL: return mock_asf_response
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = requests_side_effect
        client_id_test = "client_test_success"
        result = cpoa_main.orchestrate_podcast_generation("Test Topic", "task_podcast_001", "dummy.db", client_id=client_id_test)

        self.assertEqual(result['status'], "completed")
        self.assertEqual(result['final_audio_details']['tts_settings_used']['voice_name'], "en-TEST-Voice")
        self.assertTrue(result['asf_notification_status'].startswith("ASF notified successfully"))

        # Check UI update calls
        expected_ui_calls = [
            call(client_id_test, "generation_status", {"message": "Fetching and processing web content...", "stage": "wcha_content_retrieval"}),
            call(client_id_test, "generation_status", {"message": "Crafting podcast script with AI...", "stage": "pswa_script_generation"}),
            call(client_id_test, "generation_status", {"message": "Synthesizing audio...", "stage": "vfa_audio_generation"}),
            call(client_id_test, "generation_status", {"message": "Preparing audio stream...", "stage": "asf_notification"}),
            call(client_id_test, "generation_status", {"message": "Podcast generation complete!", "final_status": "completed", "is_terminal": True})
        ]
        mock_send_ui_update.assert_has_calls(expected_ui_calls, any_order=False) # Order matters for progress

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_successful_run_with_voice_params(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = "Detailed content."
        mock_pswa_structured_script = {
            "script_id": "s456", "topic": "Custom Voice Topic", "title": "Title With Custom Voice",
            "segments": [{"segment_title": "INTRO", "content": "Intro with custom voice."}]
        }
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = mock_pswa_structured_script

        custom_voice_params = {"voice_name": "en-GB-Standard-A", "speaking_rate": 0.9, "pitch": -2.0}
        
        # VFA response should reflect that it used these custom params
        mock_vfa_response_data_custom = {
            "status": "success", "audio_filepath": "/custom/audio.mp3", "stream_id": "stream_custom",
            "tts_settings_used": custom_voice_params
        }
        mock_vfa_response_with_custom_voice = MagicMock(status_code=200)
        mock_vfa_response_with_custom_voice.json.return_value = mock_vfa_response_data_custom

        mock_asf_response = MagicMock(status_code=200)
        mock_asf_response.json.return_value = {"message": "ASF notified for custom voice"}

        def requests_side_effect_custom_voice(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL: return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                self.assertIn("json", kwargs)
                self.assertIn("voice_params", kwargs["json"])
                self.assertEqual(kwargs["json"]["voice_params"]["voice_name"], "en-GB-Standard-A")
                self.assertEqual(kwargs["json"]["voice_params"]["speaking_rate"], 0.9)
                return mock_vfa_response_with_custom_voice
            if url == cpoa_main.ASF_NOTIFICATION_URL: return mock_asf_response
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = requests_side_effect_custom_voice

        client_id_test_vp = "client_vp_test"
        result = cpoa_main.orchestrate_podcast_generation(
            "Custom Voice Topic", "task_custom_voice_001", "dummy.db",
            voice_params_input=custom_voice_params,
            client_id=client_id_test_vp
        )
        self.assertEqual(result['status'], "completed")
        self.assertIn("tts_settings_used", result['final_audio_details'])
        mock_send_ui_update.assert_called() # Check that it was called, specific calls can be added if needed
        self.assertEqual(result['final_audio_details']['tts_settings_used']['voice_name'], "en-GB-Standard-A")
        self.assertEqual(result['final_audio_details']['tts_settings_used']['speaking_rate'], 0.9)


    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_wcha_failure_returns_error_string(self, mock_get_content, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = "WCHA: No search results found for topic: Obscure Topic"
        client_id_wcha_fail = "client_wcha_fail"
        result = cpoa_main.orchestrate_podcast_generation("Obscure Topic", "task_wcha_fail_001", "dummy.db", client_id=client_id_wcha_fail)

        self.assertEqual(result['status'], "failed_wcha_content_harvest")
        self.assertIn("WCHA: No search results", result['error_message'])
        # Check UI update for error
        mock_send_ui_update.assert_any_call(client_id_wcha_fail, "task_error", {"message": result['error_message'], "stage": "wcha_content_retrieval"})
        
        last_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_call_args[1], "task_wcha_fail_001")
        self.assertEqual(last_call_args[2], "failed_wcha_content_harvest")
        self.assertIn("WCHA: No search results", last_call_args[3])


    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_pswa_http_error(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = "Some content"
        
        mock_pswa_error_response = MagicMock()
        mock_pswa_error_response.status_code = 503 # Simulate a server error from PSWA
        mock_pswa_error_response.json.return_value = {"error": "PSWA service overloaded"}
        mock_pswa_error_response.text = '{"error": "PSWA service overloaded"}'
        
        # Configure requests_with_retry for PSWA to raise HTTPError
        # Need to ensure it's raised only for PSWA call if other calls exist in the same test scope for mock_requests_retry
        def selective_pswa_fail_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL:
                raise requests.exceptions.HTTPError("PSWA service error", response=mock_pswa_error_response)
            # Provide generic success for other potential calls if any, or specific mocks if needed
            generic_success = MagicMock(status_code=200)
            generic_success.json.return_value = {"status": "generic_ok"}
            return generic_success
        mock_requests_retry.side_effect = selective_pswa_fail_side_effect
        client_id_pswa_fail = "client_pswa_fail"

        result = cpoa_main.orchestrate_podcast_generation("Test Topic PSWA Fail", "task_pswa_fail_001", "dummy.db", client_id=client_id_pswa_fail)

        self.assertEqual(result['status'], "failed_pswa_request_exception")
        self.assertIn("PSWA service call failed", result['error_message']) # Removed "after retries" as it's part of the generic message now
        mock_send_ui_update.assert_any_call(client_id_pswa_fail, "task_error", {"message": result['error_message'], "stage": "pswa_script_generation"})
        self.assertIn("503", result['error_message'])
        
        # Ensure PSWA call was attempted
        pswa_attempted = any(call[0][1] == cpoa_main.PSWA_SERVICE_URL for call in mock_requests_retry.call_args_list)
        self.assertTrue(pswa_attempted, "requests_with_retry was not called for PSWA URL")

        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], "failed_pswa_request_exception")

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_pswa_returns_malformed_script(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = "Some content"

        mock_pswa_malformed_response = MagicMock(status_code=200)
        # Missing 'segments' key, which is essential for VFA processing later
        mock_pswa_malformed_response.json.return_value = {
            "script_id": "pswa_script_malformed", "title": "Malformed Title"
            # "segments" key is missing
        }
        # This mock_requests_retry will only be for the PSWA call in this test.
        mock_requests_retry.return_value = mock_pswa_malformed_response

        result = cpoa_main.orchestrate_podcast_generation("Test Topic PSWA Malformed Segments", "task_pswa_malformed_seg_001", "dummy.db")

        self.assertEqual(result['status'], "failed_pswa_bad_script_structure")
        self.assertIn("PSWA service returned invalid or malformed structured script", result['error_message'])

        last_db_call_args = mock_update_db.call_args_list[-1][0] # Check the final status update to DB
        self.assertEqual(last_db_call_args[2], "failed_pswa_bad_script_structure")

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_pswa_returns_malformed_script_missing_id(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = "Some content"

        mock_pswa_malformed_response_missing_id = MagicMock(status_code=200)
        # Missing 'script_id'
        mock_pswa_malformed_response_missing_id.json.return_value = {
            "title": "Malformed Title No ID", "segments": [{"segment_title": "INTRO", "content": "Intro"}]
        }
        mock_requests_retry.return_value = mock_pswa_malformed_response_missing_id

        result = cpoa_main.orchestrate_podcast_generation("Test PSWA Malformed ID", "task_pswa_malformed_id_001", "dummy.db")

        self.assertEqual(result['status'], "failed_pswa_bad_script_structure")
        self.assertIn("PSWA service returned invalid or malformed structured script", result['error_message'])

        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], "failed_pswa_bad_script_structure")

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_vfa_failure_http_error(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = "Some content for VFA test"

        # PSWA returns valid structured script
        mock_pswa_structured_script = {
            "script_id": "pswa_script_vfa_test", "topic": "VFA Test", "title": "VFA Test Title",
            "full_raw_script": "Raw script", "segments": [{"segment_title": "INTRO", "content": "Intro"}]
        }
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = mock_pswa_structured_script


        mock_vfa_error_response = MagicMock(status_code=500)
        mock_vfa_error_response.json.return_value = {"message": "VFA internal server error"}
        mock_vfa_error_response.text = '{"message": "VFA internal server error"}'

        def requests_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL:
                return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                # Check that VFA is called with the structured script
                self.assertEqual(kwargs["json"]["script"]["script_id"], "pswa_script_vfa_test")
                raise requests.exceptions.HTTPError("VFA service error", response=mock_vfa_error_response)
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = requests_side_effect
        client_id_vfa_fail = "client_vfa_fail"
        result = cpoa_main.orchestrate_podcast_generation("Test Topic VFA Fail", "task_vfa_fail_001", "dummy.db", client_id=client_id_vfa_fail)

        self.assertEqual(result['status'], "failed_vfa_request_exception")
        self.assertIn("VFA service call failed", result['error_message']) # Removed "after retries"
        mock_send_ui_update.assert_any_call(client_id_vfa_fail, "task_error", {"message": result['error_message'], "stage": "vfa_audio_generation"})
        self.assertIn("500", result['error_message'])

        # Check that PSWA was called, then VFA was attempted
        self.assertGreaterEqual(mock_requests_retry.call_count, 2) # At least PSWA and VFA attempt(s)
        
        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], "failed_vfa_request_exception")


    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_asf_notification_failure(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = "Content for ASF test"
        
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = {"script_text": "Script for ASF test"}

        mock_vfa_response = MagicMock(status_code=200)
        mock_vfa_response.json.return_value = {
            "status": "success",
            "audio_filepath": "/shared/audio/asf_test.mp3",
            "stream_id": "stream_asf_test"
        }
        
        # Mock ASF call to raise RequestException (e.g., connection error)
        mock_asf_error_response = MagicMock(status_code=500) # for HTTPError case if not ConnectionError
        mock_asf_error_response.json.return_value = {"error": "ASF connection failed"}
        mock_asf_error_response.text = '{"error": "ASF connection failed"}'

        def requests_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL: return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL: return mock_vfa_response
            if url == cpoa_main.ASF_NOTIFICATION_URL:
                raise requests.exceptions.ConnectionError("ASF connection error")
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = requests_side_effect
        client_id_asf_fail = "client_asf_fail"
        result = cpoa_main.orchestrate_podcast_generation("Test Topic ASF Fail", "task_asf_fail_001", "dummy.db", client_id=client_id_asf_fail)

        self.assertEqual(result['status'], "completed_with_asf_notification_failure")
        self.assertIn("ASF notification failed", result['error_message']) # Removed "after retries"
        mock_send_ui_update.assert_any_call(client_id_asf_fail, "task_error", {"message": result['error_message'], "final_status": "completed_with_asf_notification_failure"})
        self.assertIn("ConnectionError", result['error_message'])
        self.assertIsNotNone(result['final_audio_details'].get('audio_filepath')) # Audio generation was successful

        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], "completed_with_asf_notification_failure")
        self.assertIn("ASF notification failed", last_db_call_args[3]) # error_msg in DB


    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    def test_wcha_module_import_failure(self, mock_update_db, mock_send_ui_update):
        # Temporarily set WCHA_IMPORT_SUCCESSFUL to False for this test
        with patch.object(cpoa_main, 'WCHA_IMPORT_SUCCESSFUL', False):
            with patch.object(cpoa_main, 'WCHA_MISSING_IMPORT_ERROR', "Simulated WCHA import error"):
                client_id_wcha_import_fail = "client_wcha_import_fail"
                result = cpoa_main.orchestrate_podcast_generation("Test Topic WCHA Import Fail", "task_wcha_import_fail", "dummy.db", client_id=client_id_wcha_import_fail)

                self.assertEqual(result['status'], "failed_wcha_module_error")
                self.assertIn("Simulated WCHA import error", result['error_message'])
                mock_send_ui_update.assert_called_with(client_id_wcha_import_fail, "task_error", {"message": "Simulated WCHA import error", "stage": "initialization_failure"})

                last_db_call_args = mock_update_db.call_args_list[-1][0]
                self.assertEqual(last_db_call_args[2], "failed_wcha_module_error")
                self.assertIn("Simulated WCHA import error", last_db_call_args[3])

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry') # Mock underlying requests for _send_ui_update
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_orchestration_when_send_ui_update_fails(self, mock_get_content, mock_requests_retry_services, mock_update_db, mock_direct_send_ui_update_call):
        # This test is to ensure that if _send_ui_update itself has an issue (e.g., ASF down), CPOA doesn't crash.
        mock_get_content.return_value = "Content for UI update failure test"

        # Mock successful PSWA, VFA, ASF calls
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = {"script_id": "s_ui_fail", "title": "UI Fail Title", "segments": [{"content":"test"}]}
        mock_vfa_response = MagicMock(status_code=200)
        mock_vfa_response.json.return_value = {"status": "success", "audio_filepath": "/audio.mp3", "stream_id": "st_ui_fail"}
        mock_asf_response = MagicMock(status_code=200)
        mock_asf_response.json.return_value = {"message": "ASF notified"}

        def service_calls_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL: return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL: return mock_vfa_response
            if url == cpoa_main.ASF_NOTIFICATION_URL: return mock_asf_response
            # For CPOA_ASF_SEND_UI_UPDATE_URL, this mock won't be hit if we mock _send_ui_update directly.
            # If testing requests_with_retry inside _send_ui_update, then this needs to handle that URL.
            return MagicMock(status_code=404)
        mock_requests_retry_services.side_effect = service_calls_side_effect

        # Mock _send_ui_update to simulate failure
        mock_direct_send_ui_update_call.side_effect = Exception("Simulated UI send exception")

        client_id_ui_send_fail = "client_ui_send_fail"
        with patch.object(cpoa_main.logger, 'error') as mock_logger_error:
            result = cpoa_main.orchestrate_podcast_generation("UI Send Fail Topic", "task_ui_send_fail", "dummy.db", client_id=client_id_ui_send_fail)

            self.assertEqual(result['status'], "completed") # Main orchestration should still complete

            # Check that logger.error was called due to _send_ui_update failure
            # The _send_ui_update function has its own logger.error call.
            # We're mocking _send_ui_update itself, so its internal logging won't run unless we make it.
            # Instead, let's verify our mock_direct_send_ui_update_call was actually called.
            self.assertTrue(mock_direct_send_ui_update_call.called)
            # To check the log, we'd need to let the original _send_ui_update run and mock requests.post within it.
            # For simplicity, confirming the mock was called is enough to know the logic path was taken.

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_orchestration_with_no_client_id(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = "Content for no client ID test"
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = {"script_id": "s_no_client", "title": "No Client ID Title", "segments": [{"content":"test"}]}
        mock_vfa_response = MagicMock(status_code=200)
        mock_vfa_response.json.return_value = {"status": "success", "audio_filepath": "/audio_no_client.mp3", "stream_id": "st_no_client"}
        mock_asf_response = MagicMock(status_code=200)
        mock_asf_response.json.return_value = {"message": "ASF notified"}

        def service_calls_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL: return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL: return mock_vfa_response
            if url == cpoa_main.ASF_NOTIFICATION_URL: return mock_asf_response
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = service_calls_side_effect

        result = cpoa_main.orchestrate_podcast_generation("No Client ID Topic", "task_no_client_id", "dummy.db", client_id=None)

        self.assertEqual(result['status'], "completed")
        # Assert that _send_ui_update was effectively not called with a client_id,
        # meaning no actual attempt to send an update should have occurred.
        # The _send_ui_update function logs and returns if client_id is None.
        # So, we check it wasn't called in a way that would make requests.
        for call_args in mock_send_ui_update.call_args_list:
            self.assertIsNone(call_args[0][0]) # client_id argument should be None


class TestOrchestrateSnippetGeneration(unittest.TestCase):
    def setUp(self):
        self.mock_env_vars = {
            "SCA_SERVICE_URL": "http://mocksca.test/craft_snippet",
            "CPOA_SERVICE_RETRY_COUNT": "1",
            "CPOA_SERVICE_RETRY_BACKOFF_FACTOR": "0.01"
        }
        # Patching global config values in cpoa_main for SCA tests
        self.sca_url_patch = patch.object(cpoa_main, 'SCA_SERVICE_URL', self.mock_env_vars['SCA_SERVICE_URL'])
        self.retry_count_patch = patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_COUNT', int(self.mock_env_vars['CPOA_SERVICE_RETRY_COUNT']))
        self.backoff_patch = patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_BACKOFF_FACTOR', float(self.mock_env_vars['CPOA_SERVICE_RETRY_BACKOFF_FACTOR']))
        self.sca_url_patch.start()
        self.retry_count_patch.start()
        self.backoff_patch.start()

    def tearDown(self):
        self.sca_url_patch.stop()
        self.retry_count_patch.stop()
        self.backoff_patch.stop()

    @patch.object(cpoa_main, 'requests_with_retry')
    def test_snippet_generation_successful(self, mock_requests_retry):
        mock_sca_response = MagicMock(status_code=200)
        mock_sca_response.json.return_value = {"snippet_id": "snip_123", "snippet_text": "A great snippet."}
        mock_requests_retry.return_value = mock_sca_response

        topic_info = {"topic_id": "topic_abc", "title_suggestion": "A Great Topic"}
        result = cpoa_main.orchestrate_snippet_generation(topic_info)

        self.assertNotIn("error", result)
        self.assertEqual(result["snippet_id"], "snip_123")
        mock_requests_retry.assert_called_once()


    @patch.object(cpoa_main, 'requests_with_retry')
    def test_snippet_generation_sca_http_error(self, mock_requests_retry):
        mock_response = MagicMock(status_code=500)
        mock_response.json.return_value = {"error": "SCA internal error"}
        mock_response.text = '{"error": "SCA internal error"}'
        mock_requests_retry.side_effect = requests.exceptions.HTTPError(
            "SCA service error", response=mock_response
        )

        topic_info = {"topic_id": "topic_xyz", "title_suggestion": "Another Topic"}
        result = cpoa_main.orchestrate_snippet_generation(topic_info)

        self.assertIn("error", result)
        self.assertEqual(result["error"], "SCA_CALL_FAILED_AFTER_RETRIES")
        self.assertIn("SCA service call failed", result["details"])
        self.assertEqual(mock_requests_retry.call_count, 1) # Retry count is 1


if __name__ == '__main__':
    unittest.main(verbosity=2)
