import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import uuid

# Adjust path to import AIMS_TTS main module components
current_dir = os.path.dirname(os.path.abspath(__file__))
aims_tts_service_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(aims_tts_service_dir)
project_root_dir = os.path.dirname(aethercast_dir)

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.aims_tts_service.main import invoke_tts_google_task, celery_app as aims_tts_celery_app

# Import specific exceptions if they are caught and handled in the task
from google.api_core import exceptions as google_exceptions
import psycopg2


class TestInvokeTtsGoogleTask(unittest.TestCase):

    @patch('aethercast.common.db.get_db_connection')
    @patch('aethercast.aims_tts_service.main.texttospeech.TextToSpeechClient')
    @patch('aethercast.aims_tts_service.main.storage.Client')
    def test_invoke_tts_google_task_success(self, mock_storage_client, mock_tts_client, mock_get_db_conn):
        """
        Test successful invocation of invoke_tts_google_task using mocked global clients.
        """
        mock_db_conn_instance = MagicMock()
        mock_db_cursor_instance = MagicMock()
        mock_get_db_conn.return_value = mock_db_conn_instance
        mock_db_conn_instance.cursor.return_value.__enter__.return_value = mock_db_cursor_instance
        mock_db_cursor_instance.fetchone.side_effect = [None, (1,)]

        # --- Mock Google TTS Client ---
        mock_tts_response = MagicMock()
        mock_tts_response.audio_content = b"dummy audio content"
        mock_tts_client.return_value.synthesize_speech.return_value = mock_tts_response

        # --- Mock Google Storage Client ---
        mock_blob = MagicMock()
        mock_storage_client.return_value.bucket.return_value.blob.return_value = mock_blob

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

        # --- Execute Task ---
        with patch('aethercast.aims_tts_service.main.GCS_BUCKET_NAME', "test-bucket"), \
             patch('aethercast.aims_tts_service.main.AIMS_TTS_GCS_AUDIO_PREFIX', "test_audio/"):
            result = invoke_tts_google_task(
                request_id, text_to_synthesize, voice_id, language_code,
                speech_rate, pitch, output_format_str,
                selected_audio_encoding_details, file_extension
            )

        # --- Assertions ---
        mock_tts_client.return_value.synthesize_speech.assert_called_once()
        mock_storage_client.return_value.bucket.assert_called_once_with("test-bucket")
        mock_blob.upload_from_string.assert_called_once_with(
            b"dummy audio content", content_type="audio/mpeg"
        )

        self.assertIn("audio_url", result)
        self.assertTrue(result["audio_url"].startswith("gs://test-bucket/test_audio/test_req_001_"))
        self.assertEqual(result["request_id"], request_id)
        self.assertEqual(result["voice_id"], voice_id)
        self.assertEqual(result["audio_format"], "mp3")

    @patch('aethercast.common.db.get_db_connection')
    @patch('aethercast.aims_tts_service.main.texttospeech.TextToSpeechClient', side_effect=Exception("TTS Client Error"))
    def test_invoke_tts_task_global_tts_client_unavailable(self, mock_tts_client, mock_get_db_conn):
        mock_db_conn_instance = MagicMock()
        mock_db_cursor_instance = MagicMock()
        mock_get_db_conn.return_value = mock_db_conn_instance
        mock_db_conn_instance.cursor.return_value.__enter__.return_value = mock_db_cursor_instance
        mock_db_cursor_instance.fetchone.side_effect = [None, (1,)]
        with self.assertRaises(Exception) as context:
            invoke_tts_google_task(
                "req_id_no_tts_client", "text", "voice", "lang", 1.0, 0.0, "MP3", {}, "mp3"
            )
        self.assertIn("TTS Client Error", str(context.exception))

    @patch('aethercast.common.db.get_db_connection')
    @patch('aethercast.aims_tts_service.main.texttospeech.TextToSpeechClient')
    @patch('aethercast.aims_tts_service.main.storage.Client', side_effect=Exception("Storage Client Error"))
    def test_invoke_tts_task_global_storage_client_unavailable(self, mock_storage_client, mock_tts_client, mock_get_db_conn):
        mock_db_conn_instance = MagicMock()
        mock_db_cursor_instance = MagicMock()
        mock_get_db_conn.return_value = mock_db_conn_instance
        mock_db_conn_instance.cursor.return_value.__enter__.return_value = mock_db_cursor_instance
        mock_db_cursor_instance.fetchone.side_effect = [None, (1,)]
        mock_tts_response = MagicMock()
        mock_tts_response.audio_content = b"dummy audio content"
        mock_tts_client.return_value.synthesize_speech.return_value = mock_tts_response

        with self.assertRaises(Exception) as context:
            invoke_tts_google_task(
                "req_id_no_gcs_client", "text", "voice", "lang", 1.0, 0.0, "MP3", {"enum": 2, "mimetype": "audio/mpeg"}, "mp3"
            )
        self.assertIn("Storage Client Error", str(context.exception))


if __name__ == '__main__':
    unittest.main()


class TestAimsTtsCeleryLogging(unittest.TestCase):

    @patch('aethercast.common.db.get_db_connection')
    @patch('aethercast.aims_tts_service.main.texttospeech.TextToSpeechClient')
    @patch('aethercast.aims_tts_service.main.storage.Client')
    def test_invoke_tts_google_task_json_logging(self, mock_storage_client, mock_tts_client, mock_get_db_conn):
        mock_db_conn_instance = MagicMock()
        mock_db_cursor_instance = MagicMock()
        mock_get_db_conn.return_value = mock_db_conn_instance
        mock_db_conn_instance.cursor.return_value.__enter__.return_value = mock_db_cursor_instance
        mock_db_cursor_instance.fetchone.side_effect = [None, (1,)]

        mock_tts_response = MagicMock()
        mock_tts_response.audio_content = b"dummy audio for logging test"
        mock_tts_client.return_value.synthesize_speech.return_value = mock_tts_response
        mock_blob = MagicMock()
        mock_storage_client.return_value.bucket.return_value.blob.return_value = mock_blob

        task_request_id = f"aims_tts_log_req_{uuid.uuid4().hex[:6]}"
        task_voice_id = "en-US-LogVoice"
        task_language_code = "en-US"

        with patch('aethercast.aims_tts_service.main.logger') as mock_logger, \
             patch('aethercast.aims_tts_service.main.GCS_BUCKET_NAME', "test-bucket-log"), \
             patch('aethercast.aims_tts_service.main.AIMS_TTS_GCS_AUDIO_PREFIX', "test_audio_log/"):
            invoke_tts_google_task(
                request_id=task_request_id,
                text_to_synthesize="Test TTS logging.",
                voice_id=task_voice_id,
                language_code=task_language_code,
                speech_rate=1.1, pitch=0.1, output_format_str="MP3",
                selected_audio_encoding_details={"enum": 2, "mimetype": "audio/mpeg"},
                file_extension="mp3"
            )

            self.assertTrue(mock_logger.info.called)
            found_log_call = None
            for call_args_tuple in mock_logger.info.call_args_list:
                message_arg = call_args_tuple[0][0]
                if "Starting TTS synthesis" in message_arg:
                    found_log_call = call_args_tuple
                    break
            self.assertIsNotNone(found_log_call, "Expected starting log message not found.")

            if found_log_call:
                log_kwargs = found_log_call[1]
                self.assertIn('extra', log_kwargs)
                log_extra_dict = log_kwargs['extra']
                self.assertEqual(log_extra_dict.get('request_id'), task_request_id)
                self.assertEqual(log_extra_dict.get('voice_id_used'), task_voice_id)
                self.assertEqual(log_extra_dict.get('language_code_used'), task_language_code)
