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
