import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Adjust path to import AIMS_TTS main module components
current_dir = os.path.dirname(os.path.abspath(__file__))
aims_tts_service_dir = os.path.dirname(current_dir) # Should be /aethercast/aims_tts_service
aethercast_dir = os.path.dirname(aims_tts_service_dir) # Should be /aethercast
project_root_dir = os.path.dirname(aethercast_dir) # Should be / (project root)

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.aims_tts_service.main import invoke_tts_google_task, celery_app as aims_tts_celery_app
from aethercast.aims_tts_service.main import aims_tts_config # For config access if needed in tests

# Import specific exceptions if they are caught and handled in the task
from google.api_core import exceptions as google_exceptions
import psycopg2


class TestInvokeTtsGoogleTask(unittest.TestCase):

    def setUp(self):
        # Configure Celery for testing (task_always_eager=True runs tasks synchronously)
        aims_tts_celery_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True # Propagates exceptions raised by tasks
        )
        # Minimal config for the task to run without hitting NoneErrors for missing keys
        self.test_config_overrides = {
            "GCS_BUCKET_NAME": "test-bucket",
            "AIMS_TTS_GCS_AUDIO_PREFIX": "test_audio/",
            "IDEMPOTENCY_STATUS_PROCESSING": "processing",
            "IDEMPOTENCY_STATUS_COMPLETED": "completed",
            "IDEMPOTENCY_STATUS_FAILED": "failed",
            "IDEMPOTENCY_LOCK_TIMEOUT_SECONDS": 60,
        }
        self.config_patcher = patch.dict(aims_tts_config, self.test_config_overrides, clear=False)
        self.mocked_aims_tts_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    @patch('aethercast.aims_tts_service.main.GLOBAL_TTS_CLIENT')
    @patch('aethercast.aims_tts_service.main.GLOBAL_STORAGE_CLIENT_TTS')
    @patch('aethercast.aims_tts_service.main.get_db_connection_tts') # Mock DB interactions for idempotency
    def test_invoke_tts_google_task_success(self, mock_get_db_conn, mock_gcs_client, mock_tts_client):
        """
        Test successful invocation of invoke_tts_google_task using mocked global clients.
        """
        # --- Mock DB for Idempotency ---
        mock_db_conn_instance = MagicMock()
        mock_db_cursor_instance = MagicMock()
        mock_get_db_conn.return_value = mock_db_conn_instance
        mock_db_conn_instance.cursor.return_value.__enter__.return_value = mock_db_cursor_instance
        mock_db_cursor_instance.fetchone.return_value = None # Simulate new idempotency key

        # --- Mock Google TTS Client ---
        mock_tts_response = MagicMock()
        mock_tts_response.audio_content = b"dummy audio content"
        mock_tts_client.synthesize_speech.return_value = mock_tts_response

        # --- Mock Google Storage Client ---
        mock_blob = MagicMock()
        mock_gcs_client.bucket.return_value.blob.return_value = mock_blob

        # --- Task Arguments ---
        request_id = "test_req_001"
        text_to_synthesize = "Hello, this is a test."
        voice_id = "en-US-TestVoice"
        language_code = "en-US"
        speech_rate = 1.0
        pitch = 0.0
        output_format_str = "MP3"
        selected_audio_encoding_details = {"enum": 2, "mimetype": "audio/mpeg"} # Example for MP3
        file_extension = "mp3"
        idempotency_key = request_id # As per task logic

        # --- Execute Task ---
        # Celery task is run directly because of task_always_eager=True
        result = invoke_tts_google_task(
            request_id, text_to_synthesize, voice_id, language_code,
            speech_rate, pitch, output_format_str,
            selected_audio_encoding_details, file_extension,
            idempotency_key=idempotency_key, workflow_id="test_wf_001"
        )

        # --- Assertions ---
        mock_tts_client.synthesize_speech.assert_called_once()
        mock_gcs_client.bucket.assert_called_once_with("test-bucket")
        mock_blob.upload_from_string.assert_called_once_with(
            b"dummy audio content", content_type="audio/mpeg"
        )

        self.assertIn("audio_url", result)
        self.assertTrue(result["audio_url"].startswith("gs://test-bucket/test_audio/test_req_001_"))
        self.assertEqual(result["request_id"], request_id)
        self.assertEqual(result["voice_id"], voice_id)
        self.assertEqual(result["audio_format"], "mp3")

        # Idempotency DB call assertions
        mock_get_db_conn.assert_called_once() # Should be called for idempotency
        # Check that acquire lock and update record were called
        self.assertTrue(mock_db_cursor_instance.execute.call_count >= 2) # At least for acquire and update

    @patch('aethercast.aims_tts_service.main.GLOBAL_TTS_CLIENT', None) # Simulate TTS client failed to init
    @patch('aethercast.aims_tts_service.main.get_db_connection_tts')
    def test_invoke_tts_task_global_tts_client_unavailable(self, mock_get_db_conn, mock_tts_client_is_none):
        mock_db_conn_instance = MagicMock()
        mock_db_cursor_instance = MagicMock()
        mock_get_db_conn.return_value = mock_db_conn_instance
        mock_db_conn_instance.cursor.return_value.__enter__.return_value = mock_db_cursor_instance
        mock_db_cursor_instance.fetchone.return_value = None

        with self.assertRaises(ConnectionError) as context:
            invoke_tts_google_task(
                "req_id_no_tts_client", "text", "voice", "lang", 1.0, 0.0, "MP3", {}, "mp3",
                idempotency_key="req_id_no_tts_client"
            )
        self.assertIn("Global TTS Client failed to initialize", str(context.exception))

    @patch('aethercast.aims_tts_service.main.GLOBAL_TTS_CLIENT') # Mock available TTS client
    @patch('aethercast.aims_tts_service.main.GLOBAL_STORAGE_CLIENT_TTS', None) # Simulate Storage client failed to init
    @patch('aethercast.aims_tts_service.main.get_db_connection_tts')
    def test_invoke_tts_task_global_storage_client_unavailable(self, mock_get_db_conn, mock_gcs_client_is_none, mock_tts_client_available):
        mock_db_conn_instance = MagicMock()
        mock_db_cursor_instance = MagicMock()
        mock_get_db_conn.return_value = mock_db_conn_instance
        mock_db_conn_instance.cursor.return_value.__enter__.return_value = mock_db_cursor_instance
        mock_db_cursor_instance.fetchone.return_value = None

        mock_tts_response = MagicMock()
        mock_tts_response.audio_content = b"dummy audio content"
        mock_tts_client_available.synthesize_speech.return_value = mock_tts_response

        with self.assertRaises(ConnectionError) as context:
            invoke_tts_google_task(
                "req_id_no_gcs_client", "text", "voice", "lang", 1.0, 0.0, "MP3", {}, "mp3",
                idempotency_key="req_id_no_gcs_client"
            )
        self.assertIn("Global Storage Client failed to initialize", str(context.exception))


if __name__ == '__main__':
    unittest.main()


class TestAimsTtsCeleryLogging(unittest.TestCase):
    def setUp(self):
        aims_tts_celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)

        # Minimal config for the task to run without hitting NoneErrors for missing keys
        self.test_config_overrides = {
            "GCS_BUCKET_NAME": "test-bucket-log",
            "AIMS_TTS_GCS_AUDIO_PREFIX": "test_audio_log/",
            "IDEMPOTENCY_STATUS_PROCESSING": "processing_log",
            "IDEMPOTENCY_STATUS_COMPLETED": "completed_log",
            "IDEMPOTENCY_STATUS_FAILED": "failed_log",
            "IDEMPOTENCY_LOCK_TIMEOUT_SECONDS": 30,
        }
        self.config_patcher = patch.dict(aims_tts_config, self.test_config_overrides, clear=False)
        self.mocked_aims_tts_config = self.config_patcher.start()

        # Mock DB interactions for idempotency
        self.mock_db_conn_patcher = patch('aethercast.aims_tts_service.main.get_db_connection_tts')
        self.mock_get_db_conn = self.mock_db_conn_patcher.start()
        self.mock_db_conn_instance = MagicMock(name="MockAimsTtsDbConnectionLoggingTest")
        self.mock_db_cursor_instance = MagicMock(name="MockAimsTtsDbCursorLoggingTest")
        self.mock_get_db_conn.return_value = self.mock_db_conn_instance
        self.mock_db_conn_instance.cursor.return_value.__enter__.return_value = self.mock_db_cursor_instance
        self.mock_db_cursor_instance.fetchone.return_value = None # Simulate new idempotency key

        # Patch the logger used by AIMS_TTS tasks
        self.logger_patcher = patch('aethercast.aims_tts_service.main.logger')
        self.mock_logger = self.logger_patcher.start()

        self.addCleanup(self.config_patcher.stop)
        self.addCleanup(self.mock_db_conn_patcher.stop)
        self.addCleanup(self.logger_patcher.stop)

    @patch('aethercast.aims_tts_service.main.GLOBAL_TTS_CLIENT')
    @patch('aethercast.aims_tts_service.main.GLOBAL_STORAGE_CLIENT_TTS')
    def test_invoke_tts_google_task_json_logging(self, mock_gcs_client, mock_tts_client):
        # Mock TTS and GCS client behaviors for a successful run
        mock_tts_response = MagicMock()
        mock_tts_response.audio_content = b"dummy audio for logging test"
        mock_tts_client.synthesize_speech.return_value = mock_tts_response
        mock_blob = MagicMock()
        mock_gcs_client.bucket.return_value.blob.return_value = mock_blob

        # Task arguments
        task_request_id = f"aims_tts_log_req_{uuid.uuid4().hex[:6]}"
        task_voice_id = "en-US-LogVoice"
        task_language_code = "en-US"

        # Execute the task
        invoke_tts_google_task(
            request_id=task_request_id,
            text_to_synthesize="Test TTS logging.",
            voice_id=task_voice_id,
            language_code=task_language_code,
            speech_rate=1.1, pitch=0.1, output_format_str="MP3",
            selected_audio_encoding_details={"enum": 2, "mimetype": "audio/mpeg"}, # MP3 details
            file_extension="mp3",
            idempotency_key=task_request_id, # Task uses request_id as idempotency key
            workflow_id="wf_aims_tts_log_test" # Example workflow_id
        )

        self.assertTrue(self.mock_logger.info.called)

        found_log_call = None
        celery_task_id_from_call = None
        for call_args_tuple in self.mock_logger.info.call_args_list:
            message_arg = call_args_tuple[0][0]
            if "Starting TTS synthesis" in message_arg:
                found_log_call = call_args_tuple
                if found_log_call[1].get('extra', {}).get('task_id'):
                     celery_task_id_from_call = found_log_call[1]['extra']['task_id']
                break

        self.assertIsNotNone(found_log_call, "Expected starting log message not found.")

        if found_log_call:
            log_kwargs = found_log_call[1]
            self.assertIn('extra', log_kwargs)
            log_extra_dict = log_kwargs['extra']

            self.assertEqual(log_extra_dict.get('orig_req_id'), task_request_id)
            self.assertEqual(log_extra_dict.get('idempotency_key'), task_request_id)
            self.assertEqual(log_extra_dict.get('workflow_id'), "wf_aims_tts_log_test")
            self.assertEqual(log_extra_dict.get('voice_id_used'), task_voice_id)
            self.assertEqual(log_extra_dict.get('language_code_used'), task_language_code)
            self.assertIn('task_id', log_extra_dict)
            if celery_task_id_from_call:
                 self.assertEqual(log_extra_dict.get('task_id'), celery_task_id_from_call)

        # Verify idempotency DB calls were made (simplified check, similar to AIMS test)
        self.mock_db_cursor_instance.execute.assert_any_call(
            "SELECT status, result_payload, locked_at, error_payload FROM idempotency_keys WHERE key = %s AND task_name = %s",
            (task_request_id, "aims_invoke_tts_google_task")
        )
        self.mock_db_cursor_instance.execute.assert_any_call(
            unittest.mock.ANY, # SQL for INSERT
            (task_request_id, "aims_invoke_tts_google_task", "wf_aims_tts_log_test", unittest.mock.ANY, "processing_log", None, None, unittest.mock.ANY)
        )
        self.mock_db_cursor_instance.execute.assert_any_call(
            unittest.mock.ANY, # SQL for UPDATE to completed
            ('completed_log', unittest.mock.ANY, None, task_request_id, "aims_invoke_tts_google_task")
        )
