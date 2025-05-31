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
        self.sca_url_patch = patch.object(cpoa_main, 'SCA_SERVICE_URL', self.mock_env_vars['SCA_SERVICE_URL'])
        self.db_path_patch = patch.object(cpoa_main, 'CPOA_DATABASE_PATH', self.mock_env_vars['CPOA_DATABASE_PATH'])
        self.retry_count_patch = patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_COUNT', int(self.mock_env_vars['CPOA_SERVICE_RETRY_COUNT']))
        self.backoff_patch = patch.object(cpoa_main, 'CPOA_SERVICE_RETRY_BACKOFF_FACTOR', float(self.mock_env_vars['CPOA_SERVICE_RETRY_BACKOFF_FACTOR']))

        self.pswa_url_patch.start()
        self.vfa_url_patch.start()
        self.asf_url_patch.start()
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
        self.sca_url_patch.stop()
        self.db_path_patch.stop()
        self.retry_count_patch.stop()
        self.backoff_patch.stop()
        self.wcha_import_patch.stop()


    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_successful_run(self, mock_get_content, mock_requests_retry, mock_update_db):
        mock_get_content.return_value = "Detailed content about a fascinating topic."

        # PSWA now returns a structured script
        mock_pswa_structured_script = {
            "script_id": "pswa_script_123",
            "topic": "Test Topic", # Should match input topic to PSWA
            "title": "A Brilliant Podcast Title",
            "full_raw_script": "[TITLE]A Brilliant Podcast Title\n[INTRO]Intro here.",
            "segments": [{"segment_title": "INTRO", "content": "Intro here."}],
            "llm_model_used": "gpt-test"
        }
        mock_pswa_response = MagicMock(status_code=200)
        mock_pswa_response.json.return_value = mock_pswa_structured_script
        
        mock_vfa_response = MagicMock(status_code=200)
        mock_vfa_response.json.return_value = {
            "status": "success",
            "audio_filepath": "/shared/audio/podcast_123.mp3",
            "stream_id": "stream_abc"
        }
        
        mock_asf_response = MagicMock(status_code=200)
        mock_asf_response.json.return_value = {"message": "ASF notified"}

        def requests_side_effect(method, url, **kwargs):
            if url == cpoa_main.PSWA_SERVICE_URL:
                return mock_pswa_response
            if url == cpoa_main.VFA_SERVICE_URL:
                # Check that VFA is called with the structured script from PSWA
                self.assertIn("json", kwargs)
                self.assertIsInstance(kwargs["json"]["script"], dict)
                self.assertEqual(kwargs["json"]["script"]["script_id"], "pswa_script_123")
                return mock_vfa_response
            if url == cpoa_main.ASF_NOTIFICATION_URL:
                return mock_asf_response
            # Fallback for unexpected calls
            error_response = MagicMock(status_code=500)
            error_response.json.return_value = {"error": f"Unexpected URL in test: {url}"}
            # Ensure the mock for raise_for_status is also set up if requests_with_retry uses it internally on the returned obj
            error_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=error_response)
            return error_response
        mock_requests_retry.side_effect = requests_side_effect

        result = cpoa_main.orchestrate_podcast_generation("Test Topic", "task_podcast_001", "dummy.db")

        self.assertEqual(result['status'], "completed")
        self.assertIsNotNone(result['final_audio_details'].get('audio_filepath'))
        self.assertIsNone(result['error_message']) # Should be None for fully successful
        self.assertTrue(result['asf_notification_status'].startswith("ASF notified successfully"))
        
        # Verify DB updates: initial, wcha, pswa, vfa, asf_notification (if success), final "completed"
        # Example: check the final status update
        final_db_call = mock_update_db.call_args_list[-1][0] # Get args of the last call
        self.assertEqual(final_db_call[1], "task_podcast_001") # task_id
        self.assertEqual(final_db_call[2], "completed")       # status
        self.assertIsNone(final_db_call[3])                   # error_message


    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_wcha_failure_returns_error_string(self, mock_get_content, mock_update_db):
        mock_get_content.return_value = "WCHA: No search results found for topic: Obscure Topic"

        result = cpoa_main.orchestrate_podcast_generation("Obscure Topic", "task_wcha_fail_001", "dummy.db")

        self.assertEqual(result['status'], "failed_wcha_content_harvest")
        self.assertIn("WCHA: No search results", result['error_message'])
        
        last_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_call_args[1], "task_wcha_fail_001")
        self.assertEqual(last_call_args[2], "failed_wcha_content_harvest")
        self.assertIn("WCHA: No search results", last_call_args[3])


    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_pswa_http_error(self, mock_get_content, mock_requests_retry, mock_update_db):
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


        result = cpoa_main.orchestrate_podcast_generation("Test Topic PSWA Fail", "task_pswa_fail_001", "dummy.db")

        self.assertEqual(result['status'], "failed_pswa_request_exception")
        self.assertIn("PSWA service call failed after retries", result['error_message'])
        self.assertIn("503", result['error_message'])
        
        # Ensure PSWA call was attempted
        pswa_attempted = any(call[0][1] == cpoa_main.PSWA_SERVICE_URL for call in mock_requests_retry.call_args_list)
        self.assertTrue(pswa_attempted, "requests_with_retry was not called for PSWA URL")

        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], "failed_pswa_request_exception")

    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_pswa_returns_malformed_script(self, mock_get_content, mock_requests_retry, mock_update_db):
        mock_get_content.return_value = "Some content"

        mock_pswa_malformed_response = MagicMock(status_code=200)
        # Missing 'segments' key, which is essential for VFA processing later
        mock_pswa_malformed_response.json.return_value = {
            "script_id": "pswa_script_malformed", "title": "Malformed Title"
            # "segments" key is missing
        }
        # This mock_requests_retry will only be for the PSWA call in this test.
        mock_requests_retry.return_value = mock_pswa_malformed_response

        result = cpoa_main.orchestrate_podcast_generation("Test Topic PSWA Malformed", "task_pswa_malformed_001", "dummy.db")

        self.assertEqual(result['status'], "failed_pswa_bad_script_structure")
        self.assertIn("PSWA service returned invalid or malformed structured script", result['error_message'])

        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], "failed_pswa_bad_script_structure")


    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_vfa_failure_http_error(self, mock_get_content, mock_requests_retry, mock_update_db):
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

        result = cpoa_main.orchestrate_podcast_generation("Test Topic VFA Fail", "task_vfa_fail_001", "dummy.db")

        self.assertEqual(result['status'], "failed_vfa_request_exception")
        self.assertIn("VFA service call failed after retries", result['error_message'])
        self.assertIn("500", result['error_message'])

        # Check that PSWA was called, then VFA was attempted
        self.assertGreaterEqual(mock_requests_retry.call_count, 2) # At least PSWA and VFA attempt(s)
        
        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], "failed_vfa_request_exception")


    @patch.object(cpoa_main, '_update_task_status_in_db')
    @patch.object(cpoa_main, 'requests_with_retry')
    @patch.object(cpoa_main, 'get_content_for_topic')
    def test_asf_notification_failure(self, mock_get_content, mock_requests_retry, mock_update_db):
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

        result = cpoa_main.orchestrate_podcast_generation("Test Topic ASF Fail", "task_asf_fail_001", "dummy.db")

        self.assertEqual(result['status'], "completed_with_asf_notification_failure")
        self.assertIn("ASF notification failed after retries", result['error_message'])
        self.assertIn("ConnectionError", result['error_message'])
        self.assertIsNotNone(result['final_audio_details'].get('audio_filepath')) # Audio generation was successful

        last_db_call_args = mock_update_db.call_args_list[-1][0]
        self.assertEqual(last_db_call_args[2], "completed_with_asf_notification_failure")
        self.assertIn("ASF notification failed", last_db_call_args[3]) # error_msg in DB


    @patch.object(cpoa_main, '_update_task_status_in_db')
    def test_wcha_module_import_failure(self, mock_update_db):
        # Temporarily set WCHA_IMPORT_SUCCESSFUL to False for this test
        with patch.object(cpoa_main, 'WCHA_IMPORT_SUCCESSFUL', False):
            with patch.object(cpoa_main, 'WCHA_MISSING_IMPORT_ERROR', "Simulated WCHA import error"):
                result = cpoa_main.orchestrate_podcast_generation("Test Topic WCHA Import Fail", "task_wcha_import_fail", "dummy.db")

                self.assertEqual(result['status'], "failed_wcha_module_error")
                self.assertIn("Simulated WCHA import error", result['error_message'])

                last_db_call_args = mock_update_db.call_args_list[-1][0]
                self.assertEqual(last_db_call_args[2], "failed_wcha_module_error")
                self.assertIn("Simulated WCHA import error", last_db_call_args[3])


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
