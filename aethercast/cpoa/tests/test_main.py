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
        # WCHA now returns a dictionary
        mock_get_content.return_value = {
            "status": "success",
            "content": "Detailed content about a fascinating topic.",
            "source_urls": ["http://example.com/wcha_source1"],
            "message": "WCHA successfully fetched content."
        }

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
        result = cpoa_main.orchestrate_podcast_generation("Test Topic", "task_podcast_001", "dummy.db", client_id=client_id_test, user_preferences=None)

        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_COMPLETED) # Use constant
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
            client_id=client_id_test_vp,
            user_preferences=None # Explicitly None for this test
        )
        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_COMPLETED) # Use constant
        self.assertIn("tts_settings_used", result['final_audio_details'])
        mock_send_ui_update.assert_called() # Check that it was called, specific calls can be added if needed
        self.assertEqual(result['final_audio_details']['tts_settings_used']['voice_name'], "en-GB-Standard-A")
        self.assertEqual(result['final_audio_details']['tts_settings_used']['speaking_rate'], 0.9)


    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_wcha_failure_returns_error_string(self, mock_get_content, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = f"{cpoa_main.ERROR_WCHA_NO_SEARCH_RESULTS}: Obscure Topic" # Use constant from wcha via cpoa import if available, or define in cpoa
        # WCHA returns a dict, simulate failure
        mock_get_content.return_value = {
            "status": "failure",
            "content": None,
            "source_urls": [],
            "message": "WCHA: No search results found for topic: Obscure Topic"
        }
        client_id_wcha_fail = "client_wcha_fail"
        result = cpoa_main.orchestrate_podcast_generation("Obscure Topic", "task_wcha_fail_001", "dummy.db", client_id=client_id_wcha_fail, user_preferences=None)

        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_FAILED_WCHA_CONTENT_HARVEST)
        self.assertEqual(result['error_message'], "WCHA: No search results found for topic: Obscure Topic")
        # Check UI update for error
        mock_send_ui_update.assert_any_call(client_id_wcha_fail, cpoa_main.UI_EVENT_TASK_ERROR, {"message": result['error_message'], "stage": cpoa_main.ORCHESTRATION_STAGE_WCHA})

        last_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_call_args[1], "task_wcha_fail_001")
        self.assertEqual(last_call_args[2], cpoa_main.CPOA_STATUS_FAILED_WCHA_CONTENT_HARVEST)
        self.assertEqual(last_call_args[3], "WCHA: No search results found for topic: Obscure Topic")


    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_pswa_http_error(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Some content",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }

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

        result = cpoa_main.orchestrate_podcast_generation("Test Topic PSWA Fail", "task_pswa_fail_001", "dummy.db", client_id=client_id_pswa_fail, user_preferences=None)

        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_FAILED_PSWA_REQUEST_EXCEPTION) # Use constant
        self.assertIn("PSWA service call failed", result['error_message'])
        mock_send_ui_update.assert_any_call(client_id_pswa_fail, cpoa_main.UI_EVENT_TASK_ERROR, {"message": result['error_message'], "stage": cpoa_main.ORCHESTRATION_STAGE_PSWA})
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
        mock_get_content.return_value = {
            "status": "success", "content": "Some content",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }

        mock_pswa_malformed_response = MagicMock(status_code=200)
        # Missing 'segments' key, which is essential for VFA processing later
        mock_pswa_malformed_response.json.return_value = {
            "script_id": "pswa_script_malformed", "title": "Malformed Title"
            # "segments" key is missing
        }
        # This mock_requests_retry will only be for the PSWA call in this test.
        mock_requests_retry.return_value = mock_pswa_malformed_response

        result = cpoa_main.orchestrate_podcast_generation("Test Topic PSWA Malformed Segments", "task_pswa_malformed_seg_001", "dummy.db", user_preferences=None)

        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_FAILED_PSWA_BAD_SCRIPT_STRUCTURE) # Use constant
        self.assertIn("PSWA service returned invalid or malformed structured script", result['error_message'])

        last_db_call_args = mock_update_db.call_args_list[-1][0] # Check the final status update to DB
        self.assertEqual(last_db_call_args[2], cpoa_main.CPOA_STATUS_FAILED_PSWA_BAD_SCRIPT_STRUCTURE)

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_pswa_returns_malformed_script_missing_id(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Some content",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }

        mock_pswa_malformed_response_missing_id = MagicMock(status_code=200)
        # Missing 'script_id'
        mock_pswa_malformed_response_missing_id.json.return_value = {
            "title": "Malformed Title No ID", "segments": [{"segment_title": "INTRO", "content": "Intro"}]
        }
        mock_requests_retry.return_value = mock_pswa_malformed_response_missing_id

        result = cpoa_main.orchestrate_podcast_generation("Test PSWA Malformed ID", "task_pswa_malformed_id_001", "dummy.db", user_preferences=None)

        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_FAILED_PSWA_BAD_SCRIPT_STRUCTURE) # Use constant
        self.assertIn("PSWA service returned invalid or malformed structured script", result['error_message'])

        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], cpoa_main.CPOA_STATUS_FAILED_PSWA_BAD_SCRIPT_STRUCTURE)

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_vfa_failure_http_error(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Some content for VFA test",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }

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
        result = cpoa_main.orchestrate_podcast_generation("Test Topic VFA Fail", "task_vfa_fail_001", "dummy.db", client_id=client_id_vfa_fail, user_preferences=None)

        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_FAILED_VFA_REQUEST_EXCEPTION) # Use constant
        self.assertIn("VFA service call failed", result['error_message'])
        mock_send_ui_update.assert_any_call(client_id_vfa_fail, cpoa_main.UI_EVENT_TASK_ERROR, {"message": result['error_message'], "stage": cpoa_main.ORCHESTRATION_STAGE_VFA})
        self.assertIn("500", result['error_message'])

        # Check that PSWA was called, then VFA was attempted
        self.assertGreaterEqual(mock_requests_retry.call_count, 2) # At least PSWA and VFA attempt(s)

        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], cpoa_main.CPOA_STATUS_FAILED_VFA_REQUEST_EXCEPTION)


    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_asf_notification_failure(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Content for ASF test",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }

        mock_pswa_response = MagicMock(status_code=200)
        # Ensure PSWA returns a script_id, as VFA call depends on it.
        mock_pswa_response.json.return_value = {"script_id": "s_asf_test", "title": "ASF Test", "segments": [{"content": "Script for ASF test"}]}


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
        result = cpoa_main.orchestrate_podcast_generation("Test Topic ASF Fail", "task_asf_fail_001", "dummy.db", client_id=client_id_asf_fail, user_preferences=None)

        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_COMPLETED_WITH_ASF_NOTIFICATION_FAILURE) # Use constant
        self.assertIn("ASF notification failed", result['error_message'])
        mock_send_ui_update.assert_any_call(client_id_asf_fail, cpoa_main.UI_EVENT_TASK_ERROR, {"message": result['error_message'], "final_status": cpoa_main.CPOA_STATUS_COMPLETED_WITH_ASF_NOTIFICATION_FAILURE})
        self.assertIn("ConnectionError", result['error_message'])
        self.assertIsNotNone(result['final_audio_details'].get('audio_filepath')) # Audio generation was successful

        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], cpoa_main.CPOA_STATUS_COMPLETED_WITH_ASF_NOTIFICATION_FAILURE)
        self.assertIn("ASF notification failed", last_db_call_args[3]) # error_msg in DB


    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    def test_wcha_module_import_failure(self, mock_update_db, mock_send_ui_update):
        # Temporarily set WCHA_IMPORT_SUCCESSFUL to False for this test
        with patch.object(cpoa_main, 'WCHA_IMPORT_SUCCESSFUL', False):
            with patch.object(cpoa_main, 'WCHA_MISSING_IMPORT_ERROR', "Simulated WCHA import error"):
                client_id_wcha_import_fail = "client_wcha_import_fail"
                result = cpoa_main.orchestrate_podcast_generation("Test Topic WCHA Import Fail", "task_wcha_import_fail", "dummy.db", client_id=client_id_wcha_import_fail, user_preferences=None)

                self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_FAILED_WCHA_MODULE_ERROR) # Use constant
                self.assertIn("Simulated WCHA import error", result['error_message'])
                mock_send_ui_update.assert_called_with(client_id_wcha_import_fail, cpoa_main.UI_EVENT_TASK_ERROR, {"message": "Simulated WCHA import error", "stage": cpoa_main.ORCHESTRATION_STAGE_INITIALIZATION_FAILURE})

                last_db_call_args = mock_update_db.call_args_list[-1][0]
                self.assertEqual(last_db_call_args[2], cpoa_main.CPOA_STATUS_FAILED_WCHA_MODULE_ERROR)
                self.assertIn("Simulated WCHA import error", last_db_call_args[3])

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry') # Mock underlying requests for _send_ui_update
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_orchestration_when_send_ui_update_fails(self, mock_get_content, mock_requests_retry_services, mock_update_db, mock_direct_send_ui_update_call):
        # This test is to ensure that if _send_ui_update itself has an issue (e.g., ASF down), CPOA doesn't crash.
        mock_get_content.return_value = {
            "status": "success", "content": "Content for UI update failure test",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }

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
            result = cpoa_main.orchestrate_podcast_generation("UI Send Fail Topic", "task_ui_send_fail", "dummy.db", client_id=client_id_ui_send_fail, user_preferences=None)

            self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_COMPLETED) # Main orchestration should still complete

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
        mock_get_content.return_value = {
            "status": "success", "content": "Content for no client ID test",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }
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

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_orchestration_uses_voice_preference_from_user_prefs(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Content for voice preference test.",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }
        mock_pswa_script = {"script_id": "s_voice_pref", "title": "Voice Pref Test", "segments": [{"content":"test"}]}
        mock_pswa_response = MagicMock(status_code=200, json=lambda: mock_pswa_script)

        # VFA should be called with preferred voice name
        preferred_voice = "en-US-PreferredVoice"
        user_prefs = {cpoa_main.PREF_KEY_VFA_VOICE_NAME: preferred_voice}

        # Mock VFA to check params and return success
        mock_vfa_response_data = {"status": "success", "audio_filepath": "/audio_pref.mp3", "stream_id": "st_pref", "tts_settings_used": {"voice_name": preferred_voice}}
        mock_vfa_response = MagicMock(status_code=200, json=lambda: mock_vfa_response_data)

        mock_asf_response = MagicMock(status_code=200, json=lambda: {"message": "ASF notified"})

        def service_calls_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL:
                return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                self.assertIn("json", kwargs)
                self.assertIn("voice_params", kwargs["json"])
                self.assertEqual(kwargs["json"]["voice_params"]["voice_name"], preferred_voice)
                # Other params should be default if not in user_prefs or voice_params_input
                return mock_vfa_response
            if url == cpoa_main.ASF_NOTIFICATION_URL:
                return mock_asf_response
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = service_calls_side_effect

        result = cpoa_main.orchestrate_podcast_generation(
            "Voice Pref Topic", "task_voice_pref", "dummy.db",
            voice_params_input=None, # Crucial: no direct voice params
            user_preferences=user_prefs
        )
        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_COMPLETED)
        self.assertEqual(result['final_audio_details']['tts_settings_used']['voice_name'], preferred_voice)

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_orchestration_direct_voice_params_override_user_prefs(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Content for voice param override test.",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }
        mock_pswa_script = {"script_id": "s_override", "title": "Override Test", "segments": [{"content":"test"}]}
        mock_pswa_response = MagicMock(status_code=200, json=lambda: mock_pswa_script)

        direct_voice_params = {"voice_name": "en-GB-OverrideDirect"}
        user_prefs_ignored = {cpoa_main.PREF_KEY_VFA_VOICE_NAME: "en-US-IgnoredPreference"}

        mock_vfa_response_data = {"status": "success", "audio_filepath": "/audio_override.mp3", "stream_id": "st_override", "tts_settings_used": direct_voice_params}
        mock_vfa_response = MagicMock(status_code=200, json=lambda: mock_vfa_response_data)
        mock_asf_response = MagicMock(status_code=200, json=lambda: {"message": "ASF notified"})

        def service_calls_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL: return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                self.assertIn("json", kwargs)
                self.assertIn("voice_params", kwargs["json"])
                self.assertEqual(kwargs["json"]["voice_params"]["voice_name"], "en-GB-OverrideDirect") # Direct param takes precedence
                return mock_vfa_response
            if url == cpoa_main.ASF_NOTIFICATION_URL: return mock_asf_response
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = service_calls_side_effect

        result = cpoa_main.orchestrate_podcast_generation(
            "Override Test Topic", "task_override", "dummy.db",
            voice_params_input=direct_voice_params,
            user_preferences=user_prefs_ignored
        )
        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_COMPLETED)
        self.assertEqual(result['final_audio_details']['tts_settings_used']['voice_name'], "en-GB-OverrideDirect")

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_orchestration_passes_test_scenario_headers(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Content for test scenario header test.",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }
        mock_pswa_script = {"script_id": "s_scenario", "title": "Scenario Test", "segments": [{"content":"test"}]}
        mock_pswa_response = MagicMock(status_code=200, json=lambda: mock_pswa_script)

        mock_vfa_response_data = {"status": "success", "audio_filepath": "/audio_scenario.mp3", "stream_id": "st_scenario", "tts_settings_used": {}}
        mock_vfa_response = MagicMock(status_code=200, json=lambda: mock_vfa_response_data)

        mock_asf_response = MagicMock(status_code=200, json=lambda: {"message": "ASF notified"})

        test_scenarios_payload = {"pswa": "insufficient_content", "vfa": "vfa_error_tts"}

        def service_calls_side_effect(method, url, **kwargs):
            headers = kwargs.get("headers", {})
            if url == cpoa_main.PSWA_SERVICE_URL:
                self.assertEqual(headers.get('X-Test-Scenario'), "insufficient_content")
                return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                self.assertEqual(headers.get('X-Test-Scenario'), "vfa_error_tts")
                return mock_vfa_response
            if url == cpoa_main.ASF_NOTIFICATION_URL:
                return mock_asf_response
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = service_calls_side_effect

        result = cpoa_main.orchestrate_podcast_generation(
            "Scenario Header Test", "task_scenario_header", "dummy.db",
            test_scenarios=test_scenarios_payload
        )
        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_COMPLETED)
        # Verify requests_with_retry was called for PSWA and VFA
        self.assertTrue(any(call[0][1] == cpoa_main.PSWA_SERVICE_URL for call in mock_requests_retry.call_args_list))
        self.assertTrue(any(call[0][1] == cpoa_main.VFA_SERVICE_URL for call in mock_requests_retry.call_args_list))

    @patch.object(cpoa_main, '_send_ui_update') # Mock to avoid external calls
    @patch.object(cpoa_main, '_update_task_status_in_db') # Mock DB updates
    @patch.object(cpoa_main, 'requests_with_retry') # Mock all service calls
    @patch.object(cpoa_main, 'get_content_for_topic') # Mock WCHA
    def test_orchestrate_podcast_pswa_cache_hit(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Some content for caching test.",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }

        # Simulate PSWA service returning a cached script
        mock_cached_pswa_script = {
            "script_id": "pswa_script_cached_123",
            "topic": "Cached Topic",
            "title": "Previously Generated Title (from cache)",
            "full_raw_script": "Cached full script text",
            "segments": [{"segment_title": "INTRO", "content": "Cached intro."}],
            "llm_model_used": "gpt-3.5-turbo-cached-version", # Could be the original model
            "source": "cache" # This indicates it's from cache
        }
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = mock_cached_pswa_script

        # VFA and ASF still need to be mocked for successful completion
        mock_vfa_response_data = {
            "status": "success", "audio_filepath": "/shared/audio/cached_podcast.mp3",
            "stream_id": "stream_cached_abc",
            "tts_settings_used": {"voice_name": "en-TEST-Voice", "audio_encoding": "MP3"}
        }
        mock_vfa_response = MagicMock(status_code=200)
        mock_vfa_response.json.return_value = mock_vfa_response_data

        mock_asf_response = MagicMock(status_code=200)
        mock_asf_response.json.return_value = {"message": "ASF notified for cached script"}

        def selective_requests_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL:
                # This is the key: PSWA service itself would have logic to check its cache
                # and return a response with "source": "cache". CPOA just consumes this.
                return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                return mock_vfa_response
            if url == cpoa_main.ASF_NOTIFICATION_URL:
                return mock_asf_response
            return MagicMock(status_code=404) # Default for unexpected calls
        mock_requests_retry.side_effect = selective_requests_side_effect

        result = cpoa_main.orchestrate_podcast_generation(
            topic="Cached Topic",
            task_id="task_cache_hit_001",
            db_path="dummy_cache_test.db"
        )

        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_COMPLETED)

        # Verify that the PSWA call was made (CPOA doesn't know it was a cache hit on PSWA's side,
        # it just gets a valid script response quickly).
        pswa_call_made = any(
            call_args[0][1] == cpoa_main.PSWA_SERVICE_URL for call_args in mock_requests_retry.call_args_list
        )
        self.assertTrue(pswa_call_made, "PSWA service was not called.")

        # Check the orchestration log for PSWA stage details
        pswa_log_entry = None
        for entry in result.get("orchestration_log", []):
            if entry.get("stage") == cpoa_main.ORCHESTRATION_STAGE_PSWA and\
               entry.get("message") == "PSWA Service finished successfully.": # Check against actual log message
                pswa_log_entry = entry
                break

        self.assertIsNotNone(pswa_log_entry, f"PSWA success log entry not found in {result.get('orchestration_log')}")
        # The structured_data in the log should reflect what PSWA returned (the cached script)
        # CPOA logs a summary of the PSWA response
        logged_pswa_response_summary = pswa_log_entry["structured_data"]["response_summary"]
        self.assertEqual(logged_pswa_response_summary["script_id"], "pswa_script_cached_123")
        self.assertEqual(logged_pswa_response_summary["title"], "Previously Generated Title (from cache)")
        self.assertEqual(logged_pswa_response_summary["source"], "cache") # Ensure 'source' from PSWA is logged by CPOA

        # Ensure VFA was called with the cached script details
        vfa_call_found = False
        for call_args_tuple in mock_requests_retry.call_args_list:
            if call_args_tuple[0][1] == cpoa_main.VFA_SERVICE_URL:
                vfa_payload_sent = call_args_tuple[1].get('json', {})
                self.assertEqual(vfa_payload_sent.get('script', {}).get('script_id'), "pswa_script_cached_123")
                vfa_call_found = True
                break
        self.assertTrue(vfa_call_found, "VFA was not called or not called with expected script ID from cached PSWA response.")

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_orchestrate_podcast_vfa_returns_status_skipped_in_json(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Content for VFA skipped test.",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }
        mock_pswa_script = {"script_id": "s_vfa_skip", "title": "VFA Skip Test", "segments": [{"content":"test"}]}
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = mock_pswa_script

        # Simulate VFA service returning HTTP 200 OK, but with "status": "skipped" in JSON body
        mock_vfa_skipped_response_data = {
            "status": "skipped",
            "message": "VFA skipped: Script too short (simulated).",
            "audio_filepath": None,
            "stream_id": "strm_vfa_skipped_test", # VFA might still generate a stream_id
            "script_char_count": 5,
            "engine_used": "google_cloud_tts",
            "tts_settings_used": {"voice_name": "default", "audio_encoding": "MP3"}
        }
        mock_vfa_response = MagicMock(status_code=200) # HTTP OK
        mock_vfa_response.json.return_value = mock_vfa_skipped_response_data

        # ASF call should still happen if VFA skips but provides stream_id (current CPOA logic might not if filepath is None)
        # For this test, let's assume ASF notification is skipped if audio_filepath is None from VFA.
        # If ASF were to be called, it would need mocking here.

        def selective_requests_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL:
                return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                return mock_vfa_response
            # No ASF_NOTIFICATION_URL mock needed if we expect it to be skipped
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = selective_requests_side_effect

        result = cpoa_main.orchestrate_podcast_generation(
            topic="VFA Skipped Scenario",
            task_id="task_vfa_skipped_json_001",
            db_path="dummy_vfa_skip.db"
        )

        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_COMPLETED_WITH_VFA_SKIPPED)
        self.assertIn("VFA skipped: Script too short (simulated).", result['error_message'])
        self.assertIsNone(result['final_audio_details'].get('audio_filepath'))
        self.assertEqual(result['final_audio_details'].get('status'), "skipped")

    @patch.object(cpoa_main, '_send_ui_update')
    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_orchestrate_podcast_vfa_returns_status_error_in_json(self, mock_get_content, mock_requests_retry, mock_update_db, mock_send_ui_update):
        mock_get_content.return_value = {
            "status": "success", "content": "Content for VFA error in JSON test.",
            "source_urls": ["http://example.com/source"], "message": "WCHA success"
        }
        mock_pswa_script = {"script_id": "s_vfa_err_json", "title": "VFA Error JSON Test", "segments": [{"content":"test"}]}
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = mock_pswa_script

        # Simulate VFA service returning HTTP 200 OK, but with "status": "error" in JSON body
        mock_vfa_error_response_data = {
            "status": "error", # Logical error reported by VFA
            "message": "VFA internal processing error (simulated in JSON).", # This should become error_message
            "error_code": "VFA_INTERNAL_ERROR_SIMULATED", # If VFA uses standardized errors
            "details": "Detailed VFA error info.",
            "audio_filepath": None,
            "stream_id": "strm_vfa_error_json_test",
            "engine_used": "google_cloud_tts",
            "tts_settings_used": {"voice_name": "default", "audio_encoding": "MP3"}
        }
        mock_vfa_response = MagicMock(status_code=200) # HTTP OK
        mock_vfa_response.json.return_value = mock_vfa_error_response_data

        def selective_requests_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL:
                return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                return mock_vfa_response
            return MagicMock(status_code=404)
        mock_requests_retry.side_effect = selective_requests_side_effect

        result = cpoa_main.orchestrate_podcast_generation(
            topic="VFA Error in JSON Scenario",
            task_id="task_vfa_error_json_001",
            db_path="dummy_vfa_error.db"
        )

        # This path in CPOA currently leads to CPOA_STATUS_FAILED_VFA_REPORTED_ERROR
        self.assertEqual(result['status'], cpoa_main.CPOA_STATUS_FAILED_VFA_REPORTED_ERROR)
        self.assertIn("VFA internal processing error (simulated in JSON).", result['error_message'])
        # final_audio_details should reflect the error status from VFA's JSON body
        self.assertEqual(result['final_audio_details'].get('status'), "error")
        self.assertEqual(result['final_audio_details'].get('message'), "VFA internal processing error (simulated in JSON).")


    def test_get_popular_categories_returns_correct_structure(self):
        # No mocks needed as it's a hardcoded list return
        result = cpoa_main.get_popular_categories()
        self.assertIsInstance(result, dict)
        self.assertIn("categories", result)
        self.assertIsInstance(result["categories"], list)

        expected_defaults = [
            "Business", "Technology", "Lifestyle", "Entertainment",
            "Health", "Science", "Education", "Arts"
        ]
        # Check if the returned list matches the expected hardcoded list
        self.assertListEqual(sorted(result["categories"]), sorted(expected_defaults))

        for category in result["categories"]:
            self.assertIsInstance(category, str)


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

    @patch('aethercast.cpoa.main.requests_with_retry')
    @patch('aethercast.cpoa.main._save_snippet_to_db') # Mock the DB save
    def test_snippet_gen_success_with_iga_success(self, mock_save_db, mock_requests_retry):
        mock_sca_response_data = {
            "snippet_id": "sca_snip_1", "topic_id": "topic1", "title": "SCA Title",
            "summary": "SCA Summary", "cover_art_prompt": "A prompt for IGA"
        }
        mock_iga_response_data = {"image_url": "http://example.com/image.jpg", "prompt_used": "A prompt for IGA", "model_version": "iga-placeholder-v0.1"}

        def sca_iga_side_effect(method, url, **kwargs):
            if url == cpoa_main.SCA_SERVICE_URL:
                resp = MagicMock(status_code=200)
                resp.json.return_value = mock_sca_response_data
                return resp
            elif url.startswith(cpoa_main.IGA_SERVICE_URL): # Check startswith for /generate_image
                resp = MagicMock(status_code=200)
                resp.json.return_value = mock_iga_response_data
                return resp
            raise ValueError(f"Unexpected URL for requests_with_retry: {url}")
        mock_requests_retry.side_effect = sca_iga_side_effect
        mock_save_db.return_value = None # Simulate successful save

        topic_info_input = {"topic_id": "topic1", "title_suggestion": "Original Title"}
        result = cpoa_main.orchestrate_snippet_generation(topic_info_input)

        self.assertNotIn("error", result)
        self.assertEqual(result["snippet_id"], "sca_snip_1")
        self.assertEqual(result["image_url"], "http://example.com/image.jpg")
        mock_requests_retry.assert_any_call("post", cpoa_main.SCA_SERVICE_URL, json=unittest.mock.ANY, timeout=60, max_retries=unittest.mock.ANY, backoff_factor=unittest.mock.ANY)
        # Corrected IGA_SERVICE_URL usage for assertion
        iga_expected_url = f"{self.mock_env_vars['IGA_SERVICE_URL'].rstrip('/')}/generate_image"
        mock_requests_retry.assert_any_call("post", iga_expected_url, json={"prompt": "A prompt for IGA"}, timeout=20, max_retries=unittest.mock.ANY, backoff_factor=unittest.mock.ANY)
        mock_save_db.assert_called_once()

    @patch('aethercast.cpoa.main.requests_with_retry')
    @patch('aethercast.cpoa.main._save_snippet_to_db')
    def test_snippet_gen_success_iga_http_error(self, mock_save_db, mock_requests_retry):
        mock_sca_response_data = {"snippet_id": "sca_snip_2", "cover_art_prompt": "prompt2"}
        mock_iga_http_error_response = MagicMock(status_code=500)
        mock_iga_http_error_response.json.return_value = {"error_code": "IGA_SERVER_DOWN", "message":"IGA server down."}

        def sca_iga_side_effect_iga_fail(method, url, **kwargs):
            if url == cpoa_main.SCA_SERVICE_URL:
                resp = MagicMock(status_code=200)
                resp.json.return_value = mock_sca_response_data
                return resp
            elif url.startswith(cpoa_main.IGA_SERVICE_URL):
                raise requests.exceptions.HTTPError(response=mock_iga_http_error_response)
            raise ValueError(f"Unexpected URL for requests_with_retry: {url}")
        mock_requests_retry.side_effect = sca_iga_side_effect_iga_fail

        result = cpoa_main.orchestrate_snippet_generation({"topic_id": "t2", "title_suggestion": "Title2"})
        self.assertNotIn("error", result)
        self.assertEqual(result["snippet_id"], "sca_snip_2")
        self.assertIsNone(result.get("image_url"))
        mock_save_db.assert_called_once()

    @patch('aethercast.cpoa.main.requests_with_retry')
    @patch('aethercast.cpoa.main._save_snippet_to_db')
    def test_snippet_gen_success_iga_returns_error_json(self, mock_save_db, mock_requests_retry):
        mock_sca_response_data = {"snippet_id": "sca_snip_3", "cover_art_prompt": "prompt3"}
        mock_iga_logical_error_response_data = {"error_code": "IGA_PROMPT_REJECTED", "message":"Prompt was rejected by IGA policy."}

        def sca_iga_side_effect_iga_logical_fail(method, url, **kwargs):
            if url == cpoa_main.SCA_SERVICE_URL:
                resp = MagicMock(status_code=200)
                resp.json.return_value = mock_sca_response_data
                return resp
            elif url.startswith(cpoa_main.IGA_SERVICE_URL):
                resp = MagicMock(status_code=200)
                resp.json.return_value = mock_iga_logical_error_response_data
                return resp
            raise ValueError(f"Unexpected URL: {url}")
        mock_requests_retry.side_effect = sca_iga_side_effect_iga_logical_fail

        result = cpoa_main.orchestrate_snippet_generation({"topic_id": "t3", "title_suggestion": "Title3"})
        self.assertNotIn("error", result)
        self.assertEqual(result["snippet_id"], "sca_snip_3")
        self.assertIsNone(result.get("image_url"))
        mock_save_db.assert_called_once()

    @patch('aethercast.cpoa.main.requests_with_retry')
    @patch('aethercast.cpoa.main._save_snippet_to_db')
    def test_snippet_gen_db_save_fails(self, mock_save_db, mock_requests_retry):
        mock_sca_response_data = {"snippet_id": "sca_snip_4", "cover_art_prompt": None}

        def sca_side_effect(method, url, **kwargs):
            if url == cpoa_main.SCA_SERVICE_URL:
                resp = MagicMock(status_code=200)
                resp.json.return_value = mock_sca_response_data
                return resp
            raise ValueError(f"Unexpected URL: {url}")
        mock_requests_retry.side_effect = sca_side_effect
        mock_save_db.side_effect = sqlite3.Error("Simulated DB error on snippet save")

        with patch.object(cpoa_main.logger, 'error') as mock_logger_error:
            result = cpoa_main.orchestrate_snippet_generation({"topic_id": "t4", "title_suggestion": "Title4"})
            self.assertNotIn("error", result)
            self.assertEqual(result["snippet_id"], "sca_snip_4")
            mock_save_db.assert_called_once()
            self.assertTrue(any("Database error saving snippet sca_snip_4" in call_arg[0][0] for call_arg in mock_logger_error.call_args_list))


class TestGetTopicDetailsFromDb(unittest.TestCase):
    @patch('sqlite3.connect')
    def test_get_topic_success(self, mock_sqlite_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Simulate a row being found
        mock_row = {"id": "topic123", "title": "Test Topic", "keywords": '["kw1", "kw2"]', "type": "topic"}
        mock_cursor.fetchone.return_value = mock_row

        result = cpoa_main._get_topic_details_from_db("dummy.db", "topic123")

        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Test Topic")
        self.assertEqual(result["keywords"], ["kw1", "kw2"]) # Check JSON deserialization
        mock_cursor.execute.assert_called_once_with("SELECT * FROM topics_snippets WHERE id = ? AND type = 'topic'", ("topic123",))

    @patch('sqlite3.connect')
    def test_get_topic_not_found(self, mock_sqlite_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None # Simulate no row found

        result = cpoa_main._get_topic_details_from_db("dummy.db", "topic_not_exist")
        self.assertIsNone(result)

    @patch('sqlite3.connect')
    def test_get_topic_db_error(self, mock_sqlite_connect):
        mock_sqlite_connect.side_effect = sqlite3.Error("DB connection failed")
        with patch.object(cpoa_main.logger, 'error') as mock_logger_error:
            result = cpoa_main._get_topic_details_from_db("dummy.db", "topic_any")
            self.assertIsNone(result)
            mock_logger_error.assert_called_once()
            self.assertIn("Database error fetching topic topic_any", mock_logger_error.call_args[0][0])


class TestOrchestrateTopicExploration(unittest.TestCase):
    def setUp(self):
        self.mock_env_vars = {
            "TDA_SERVICE_URL": "http://mocktda.test/discover_topics",
            "CPOA_DATABASE_PATH": "test_explore_cpoa.db", # Use a distinct DB for these tests if needed
            "SCA_SERVICE_URL": "http://mocksca.test/craft_snippet", # orchestrate_snippet_generation uses this
            "CPOA_SERVICE_RETRY_COUNT": "1",
            "CPOA_SERVICE_RETRY_BACKOFF_FACTOR": "0.01"
        }
        # Patch module-level constants in cpoa_main
        self.tda_url_patch = patch.object(cpoa_main, 'TDA_SERVICE_URL', self.mock_env_vars['TDA_SERVICE_URL'])
        self.db_path_patch = patch.object(cpoa_main, 'CPOA_DATABASE_PATH', self.mock_env_vars['CPOA_DATABASE_PATH'])
        self.sca_url_patch = patch.object(cpoa_main, 'SCA_SERVICE_URL', self.mock_env_vars['SCA_SERVICE_URL'])
        self.retry_patch = patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_COUNT', int(self.mock_env_vars['CPOA_SERVICE_RETRY_COUNT']))
        self.backoff_patch = patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_BACKOFF_FACTOR', float(self.mock_env_vars['CPOA_SERVICE_RETRY_BACKOFF_FACTOR']))

        self.tda_url_patch.start()
        self.db_path_patch.start()
        self.sca_url_patch.start()
        self.retry_patch.start()
        self.backoff_patch.start()

    def tearDown(self):
        self.tda_url_patch.stop()
        self.db_path_patch.stop()
        self.sca_url_patch.stop()
        self.retry_patch.stop()
        self.backoff_patch.stop()

    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'orchestrate_snippet_generation')
    @patch.object(cpoa_main, '_get_topic_details_from_db', return_value=None) # Assume topic ID not found or not used
    def test_exploration_with_keywords_success(self, mock_get_details, mock_orch_snippet, mock_requests_retry):
        mock_tda_response = MagicMock()
        mock_tda_response.json.return_value = {"topics": [{"id": "tda1", "title": "Explored Topic 1"}]}
        mock_requests_retry.return_value = mock_tda_response

        mock_orch_snippet.return_value = {"snippet_id": "snip1", "title": "Snippet for Explored Topic 1"}

        result = cpoa_main.orchestrate_topic_exploration(keywords=["new keyword"], user_preferences=None, test_scenarios=None)

        mock_requests_retry.assert_called_once()
        self.assertEqual(mock_requests_retry.call_args[0][1], cpoa_main.TDA_SERVICE_URL)
        self.assertEqual(mock_requests_retry.call_args[1]['json']['query'], "new keyword")

        mock_orch_snippet.assert_called_once()
        self.assertEqual(mock_orch_snippet.call_args[0][0]['title_suggestion'], "Explored Topic 1")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["snippet_id"], "snip1")

    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'orchestrate_snippet_generation')
    @patch.object(cpoa_main, '_get_topic_details_from_db')
    def test_exploration_with_topic_id_success(self, mock_get_details, mock_orch_snippet, mock_requests_retry):
        mock_original_topic = {"id": "orig_topic", "title": "Original Topic Title", "keywords": ["orig", "key"]}
        mock_get_details.return_value = mock_original_topic

        mock_tda_response = MagicMock()
        mock_tda_response.json.return_value = {"topics": [{"id": "tda2", "title": "Deeper Dive Topic"}]}
        mock_requests_retry.return_value = mock_tda_response

        mock_orch_snippet.return_value = {"snippet_id": "snip2", "title": "Snippet for Deeper Dive"}

        result = cpoa_main.orchestrate_topic_exploration(current_topic_id="orig_topic", user_preferences=None, test_scenarios=None)

        mock_get_details.assert_called_once_with(cpoa_main.CPOA_DATABASE_PATH, "orig_topic")
        mock_requests_retry.assert_called_once()
        self.assertEqual(mock_requests_retry.call_args[1]['json']['query'], "orig key") # From keywords

        mock_orch_snippet.assert_called_once()
        self.assertEqual(mock_orch_snippet.call_args[0][0]['title_suggestion'], "Deeper Dive Topic")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["snippet_id"], "snip2")

    def test_exploration_no_identifier_raises_error(self):
        with self.assertRaises(ValueError) as context:
            cpoa_main.orchestrate_topic_exploration(user_preferences=None, test_scenarios=None)
        self.assertIn("Cannot explore topic without a valid current_topic_id or keywords", str(context.exception))

    @patch.object(cpoa_main, '_get_topic_details_from_db', return_value=None)
    def test_exploration_topic_id_not_found(self, mock_get_details):
        # If only topic_id is provided and it's not found, and no keywords.
        result = cpoa_main.orchestrate_topic_exploration(current_topic_id="unknown_topic", user_preferences=None, test_scenarios=None)
        mock_get_details.assert_called_once_with(cpoa_main.CPOA_DATABASE_PATH, "unknown_topic")
        self.assertEqual(result, []) # Returns empty list as per current logic

    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, '_get_topic_details_from_db', return_value=None) # Ensure keywords are used
    def test_exploration_tda_fails(self, mock_get_details, mock_requests_retry):
        mock_requests_retry.side_effect = requests.exceptions.RequestException("TDA down")
        with patch.object(cpoa_main.logger, 'error') as mock_logger_error:
            result = cpoa_main.orchestrate_topic_exploration(keywords=["test"], user_preferences=None, test_scenarios=None)
            self.assertEqual(result, [])
            self.assertTrue(any("TDA service call failed during exploration" in call_arg[0][0] for call_arg in mock_logger_error.call_args_list))

    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, '_get_topic_details_from_db', return_value=None)
    def test_exploration_tda_returns_no_topics(self, mock_get_details, mock_requests_retry):
        mock_tda_response = MagicMock()
        mock_tda_response.json.return_value = {"topics": []} # TDA found nothing
        mock_requests_retry.return_value = mock_tda_response

        result = cpoa_main.orchestrate_topic_exploration(keywords=["obscure"], user_preferences=None, test_scenarios=None)
        self.assertEqual(result, [])

    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'orchestrate_snippet_generation')
    @patch.object(cpoa_main, '_get_topic_details_from_db', return_value=None)
    def test_exploration_snippet_generation_fails_for_some(self, mock_get_details, mock_orch_snippet, mock_requests_retry):
        mock_tda_response = MagicMock()
        mock_tda_response.json.return_value = {
            "topics": [
                {"id": "tda_ok", "title": "Good Topic"},
                {"id": "tda_fail", "title": "Bad Topic for SCA"}
            ]
        }
        mock_requests_retry.return_value = mock_tda_response

        def snippet_side_effect(topic_info):
            if topic_info["title_suggestion"] == "Good Topic":
                return {"snippet_id": "snip_good", "title": "Good Snippet"}
            else:
                return {"error": "SCA failed", "details": "SCA could not process Bad Topic"}
        mock_orch_snippet.side_effect = snippet_side_effect

        result = cpoa_main.orchestrate_topic_exploration(keywords=["mixed results"], user_preferences=None, test_scenarios=None)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["snippet_id"], "snip_good")
        self.assertEqual(mock_orch_snippet.call_count, 2)


class TestOrchestrateSearchResultsGeneration(unittest.TestCase):
    def setUp(self):
        self.mock_env_vars = {
            "TDA_SERVICE_URL": "http://mocktda.test/discover_topics",
            "SCA_SERVICE_URL": "http://mocksca.test/craft_snippet", # For orchestrate_snippet_generation
            "IGA_SERVICE_URL": "http://mockiga.test/generate_image", # For orchestrate_snippet_generation
            "CPOA_DATABASE_PATH": ":memory:", # In-memory for any DB ops within snippet gen
            "CPOA_SERVICE_RETRY_COUNT": "1",
            "CPOA_SERVICE_RETRY_BACKOFF_FACTOR": "0.01"
        }
        # Patch.dict on os.environ is tricky if modules load vars at import time.
        # Instead, we patch the module-level constants directly in cpoa_main
        # This assumes cpoa_main.py defines these as module-level variables that are
        # then used by the functions. If functions call os.getenv directly each time,
        # then patch.dict(os.environ, ...) is the way.
        # Given current cpoa_main structure, it seems it loads some at module level.

        self.patchers = []

        # Check if constants exist before patching, to avoid AttributeError if not defined in cpoa_main
        if hasattr(cpoa_main, 'TDA_SERVICE_URL'):
            self.patchers.append(patch.object(cpoa_main, 'TDA_SERVICE_URL', self.mock_env_vars['TDA_SERVICE_URL']))
        if hasattr(cpoa_main, 'SCA_SERVICE_URL'):
            self.patchers.append(patch.object(cpoa_main, 'SCA_SERVICE_URL', self.mock_env_vars['SCA_SERVICE_URL']))
        if hasattr(cpoa_main, 'IGA_SERVICE_URL'):
             self.patchers.append(patch.object(cpoa_main, 'IGA_SERVICE_URL', self.mock_env_vars['IGA_SERVICE_URL']))
        if hasattr(cpoa_main, 'CPOA_DATABASE_PATH'):
            self.patchers.append(patch.object(cpoa_main, 'CPOA_DATABASE_PATH', self.mock_env_vars['CPOA_DATABASE_PATH']))

        # These are likely used directly by os.getenv in functions, so patch os.environ
        self.env_patcher = patch.dict(os.environ, self.mock_env_vars)
        self.env_patcher.start()
        self.patchers.append(self.env_patcher) # Add to list for teardown

        # For retry counts, cpoa_main.py loads them into module constants CPOA_SERVICE_RETRY_COUNT etc.
        # So, we patch those module constants after os.environ is patched, assuming they are re-evaluated or loaded in a function.
        # If they are set ONCE at module import from os.getenv, then we must patch os.environ *before* cpoa_main is first imported in the test file.
        # This is complex. For now, let's assume functions like requests_with_retry re-evaluate or are passed these.
        # The provided cpoa_main.py structure has these as module-level globals.
        # So, the patch.object approach is better for these module globals.
        if hasattr(cpoa_main, 'CPOA_SERVICE_RETRY_COUNT'):
             self.patchers.append(patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_COUNT', int(self.mock_env_vars['CPOA_SERVICE_RETRY_COUNT'])))
        if hasattr(cpoa_main, 'CPOA_SERVICE_RETRY_BACKOFF_FACTOR'):
             self.patchers.append(patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_BACKOFF_FACTOR', float(self.mock_env_vars['CPOA_SERVICE_RETRY_BACKOFF_FACTOR'])))

        for p in self.patchers:
            if hasattr(p, 'is_local'): # distinguish patch.dict from patch.object
                 continue # already started if it's env_patcher
            p.start()


    def tearDown(self):
        for p in reversed(self.patchers): # Stop in reverse order of start
            p.stop()

    @patch('aethercast.cpoa.main.requests_with_retry')
    @patch('aethercast.cpoa.main.orchestrate_snippet_generation') # Mock the internal call
    def test_search_success_tda_and_sca_success(self, mock_orch_snippet_gen, mock_requests_retry_tda):
        # Mock TDA response
        mock_tda_response_data = {
            "discovered_topics": [
                {"topic_id": "tda_topic_1", "title_suggestion": "Topic 1 from TDA", "summary": "Sum1", "keywords": ["k1"]},
                {"topic_id": "tda_topic_2", "title_suggestion": "Topic 2 from TDA", "summary": "Sum2", "keywords": ["k2"]}
            ]
        }
        mock_tda_http_response = MagicMock(status_code=200)
        mock_tda_http_response.json.return_value = mock_tda_response_data
        mock_requests_retry_tda.return_value = mock_tda_http_response

        # Mock orchestrate_snippet_generation response
        def mock_snippet_gen_side_effect(topic_info, user_preferences=None, test_scenarios=None, client_id=None): # Added default args
            return {"snippet_id": f"snip_for_{topic_info['topic_id']}", "title": topic_info["title_suggestion"], "summary": "Generated snippet"}
        mock_orch_snippet_gen.side_effect = mock_snippet_gen_side_effect

        result = cpoa_main.orchestrate_search_results_generation(query="test query")

        mock_requests_retry_tda.assert_called_once_with(
            "post", cpoa_main.TDA_SERVICE_URL,
            max_retries=int(self.mock_env_vars['CPOA_SERVICE_RETRY_COUNT']),
            backoff_factor=float(self.mock_env_vars['CPOA_SERVICE_RETRY_BACKOFF_FACTOR']),
            json={"query": "test query", "limit": 7}, timeout=30
        )
        self.assertEqual(mock_orch_snippet_gen.call_count, 2)
        self.assertIn("search_results", result)
        self.assertEqual(len(result["search_results"]), 2)
        self.assertEqual(result["search_results"][0]["title"], "Topic 1 from TDA")
        self.assertEqual(result["search_results"][1]["snippet_id"], "snip_for_tda_topic_2")

    @patch('aethercast.cpoa.main.requests_with_retry')
    def test_search_tda_returns_no_topics(self, mock_requests_retry_tda):
        mock_tda_response_data = {"discovered_topics": []} # TDA finds nothing
        mock_tda_http_response = MagicMock(status_code=200)
        mock_tda_http_response.json.return_value = mock_tda_response_data
        mock_requests_retry_tda.return_value = mock_tda_http_response

        result = cpoa_main.orchestrate_search_results_generation(query="obscure query")
        self.assertIn("search_results", result)
        self.assertEqual(len(result["search_results"]), 0)
        self.assertNotIn("error", result) # Should not be an error for no topics

    @patch('aethercast.cpoa.main.requests_with_retry')
    def test_search_tda_call_fails_http_error(self, mock_requests_retry_tda):
        mock_tda_http_response = MagicMock(status_code=500)
        mock_tda_http_response.json.return_value = {"error": "TDA Down"}
        mock_requests_retry_tda.side_effect = requests.exceptions.HTTPError(response=mock_tda_http_response)

        result = cpoa_main.orchestrate_search_results_generation(query="query during tda fail")
        self.assertIn("error", result)
        self.assertEqual(result["error"], "TDA_REQUEST_FAILED")
        self.assertIn("search_results", result)
        self.assertEqual(len(result["search_results"]), 0)

    @patch('aethercast.cpoa.main.requests_with_retry')
    def test_search_tda_returns_malformed_json(self, mock_requests_retry_tda):
        mock_tda_http_response = MagicMock(status_code=200)
        mock_tda_http_response.json.side_effect = json.JSONDecodeError("bad json", "doc", 0)
        mock_requests_retry_tda.return_value = mock_tda_http_response

        result = cpoa_main.orchestrate_search_results_generation(query="query for bad json tda")
        self.assertIn("error", result)
        self.assertEqual(result["error"], "TDA_RESPONSE_INVALID_JSON")
        self.assertEqual(len(result.get("search_results", [])), 0)


    @patch('aethercast.cpoa.main.requests_with_retry')
    @patch('aethercast.cpoa.main.orchestrate_snippet_generation')
    def test_search_some_snippet_generations_fail(self, mock_orch_snippet_gen, mock_requests_retry_tda):
        mock_tda_response_data = {
            "discovered_topics": [
                {"topic_id": "t1", "title_suggestion": "Topic 1"},
                {"topic_id": "t2", "title_suggestion": "Topic 2 (will fail snippet gen)"},
                {"topic_id": "t3", "title_suggestion": "Topic 3"}
            ]
        }
        mock_tda_http_response = MagicMock(status_code=200)
        mock_tda_http_response.json.return_value = mock_tda_response_data
        mock_requests_retry_tda.return_value = mock_tda_http_response

        def mock_snippet_gen_side_effect(topic_info, user_preferences=None, test_scenarios=None, client_id=None): # Added default args
            if topic_info['topic_id'] == "t2":
                return {"error": "SCA_SIMULATED_ERROR", "details": "SCA failed for t2"}
            return {"snippet_id": f"snip_for_{topic_info['topic_id']}", "title": topic_info["title_suggestion"]}
        mock_orch_snippet_gen.side_effect = mock_snippet_gen_side_effect

        result = cpoa_main.orchestrate_search_results_generation(query="test some fail")
        self.assertNotIn("error", result)
        self.assertEqual(len(result["search_results"]), 2)
        self.assertTrue(any(s["snippet_id"] == "snip_for_t1" for s in result["search_results"]))
        self.assertTrue(any(s["snippet_id"] == "snip_for_t3" for s in result["search_results"]))
        self.assertEqual(mock_orch_snippet_gen.call_count, 3)

    @patch('aethercast.cpoa.main.TDA_SERVICE_URL', None)
    def test_search_tda_service_url_not_configured(self):
         # This test requires TDA_SERVICE_URL to be None *when the function is called*.
         # If TDA_SERVICE_URL is checked at module load, this won't work as expected without re-importing or deeper patching.
         # The setUp now patches module-level vars, so this should be fine.
         # However, the function might have already captured the TDA_SERVICE_URL from its module scope at import time.
         # Let's try by directly setting the module's global if the patch doesn't reflect immediately.
         original_tda_url = getattr(cpoa_main, 'TDA_SERVICE_URL', 'marker_not_exists')
         setattr(cpoa_main, 'TDA_SERVICE_URL', None)

         result = cpoa_main.orchestrate_search_results_generation(query="any")

         if original_tda_url != 'marker_not_exists': # Restore original value
             setattr(cpoa_main, 'TDA_SERVICE_URL', original_tda_url)

         self.assertEqual(result.get("error"), "CPOA_CONFIG_ERROR")
         self.assertIn("TDA_SERVICE_URL not set", result.get("details"))
         self.assertEqual(len(result.get("search_results", [])), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
