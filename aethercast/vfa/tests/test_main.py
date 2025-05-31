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
# Import TextToSpeechClient and other google types for mocking if needed
try:
    from google.cloud import texttospeech
    from google.api_core import exceptions as google_exceptions
    GOOGLE_SDK_AVAILABLE = True
except ImportError:
    GOOGLE_SDK_AVAILABLE = False
    # Create placeholders if google cloud sdk is not available
    class MockTextToSpeechClient:
        def synthesize_speech(self, *args, **kwargs):
            response = MagicMock()
            response.audio_content = b"mock audio data"
            return response
    
    class MockAudioEncoding:
        MP3 = 2
        LINEAR16 = 3
        OGG_OPUS = 4

        @staticmethod
        def Name(value): # Add the Name method to the mock
            names = {2: "MP3", 3: "LINEAR16", 4: "OGG_OPUS"}
            return names.get(value, "UNKNOWN")


    texttospeech = MagicMock()
    texttospeech.TextToSpeechClient = MockTextToSpeechClient
    texttospeech.AudioEncoding = MockAudioEncoding # Use the mock with Name method
    texttospeech.SynthesisInput = MagicMock
    texttospeech.VoiceSelectionParams = MagicMock
    texttospeech.AudioConfig = MagicMock
    google_exceptions = MagicMock()
    google_exceptions.GoogleAPIError = type('GoogleAPIError', (Exception,), {})


class TestForgeVoiceLogic(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None # Show full diff on assertion failure
        self.mock_vfa_config = {
            "GOOGLE_APPLICATION_CREDENTIALS": "fake_creds.json",
            "VFA_SHARED_AUDIO_DIR": "/tmp/vfa_test_audio",
            "VFA_TTS_VOICE_NAME": "en-TEST-Voice",
            "VFA_TTS_LANG_CODE": "en-TEST",
            "VFA_TTS_AUDIO_ENCODING_STR": "MP3",
            "VFA_MIN_SCRIPT_LENGTH": 10,
        }
        self.config_patcher = patch.dict(vfa_main.vfa_config, self.mock_vfa_config)
        self.mock_config = self.config_patcher.start()

        self.makedirs_patcher = patch('os.makedirs')
        self.mock_makedirs = self.makedirs_patcher.start()

        self.imports_patcher = patch.object(vfa_main, 'VFA_IMPORTS_SUCCESSFUL', True)
        self.mock_imports_successful = self.imports_patcher.start()
        
        # Explicitly mock the map that load_vfa_configuration would create
        # This is important if the real google.cloud.texttospeech is not available during tests
        self.encoding_map_content = {
            "MP3": vfa_main.texttospeech.AudioEncoding.MP3, # Use the (potentially mocked) texttospeech
            "LINEAR16": vfa_main.texttospeech.AudioEncoding.LINEAR16,
            "OGG_OPUS": vfa_main.texttospeech.AudioEncoding.OGG_OPUS
        }
        self.encoding_map_patcher = patch.dict(vfa_main.google_audio_encoding_map, self.encoding_map_content, clear=True)
        self.encoding_map_patcher.start()


    def tearDown(self):
        self.config_patcher.stop()
        self.makedirs_patcher.stop()
        self.imports_patcher.stop()
        self.encoding_map_patcher.stop()


    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_success_structured_script(self, mock_file_open, mock_tts_client_constructor):
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"mock audio")

        structured_script = {
            "script_id": "s1", "topic": "Test Topic", "title": "Main Podcast Title",
            "full_raw_script": "[TITLE]Main Podcast Title\n[INTRO]Intro content for our test.\n[SEGMENT_1_TITLE]First Segment\n[SEGMENT_1_CONTENT]Content of the first segment.",
            "segments": [
                {"segment_title": "INTRO", "content": "Intro content for our test."},
                {"segment_title": "First Segment", "content": "Content of the first segment."}
            ]
        }
        result = vfa_main.forge_voice(structured_script)

        self.assertEqual(result["status"], "success")
        self.assertIn("audio successfully synthesized", result["message"].lower())
        self.assertIsNotNone(result["audio_filepath"])
        self.assertTrue(result["audio_filepath"].startswith(self.mock_vfa_config["VFA_SHARED_AUDIO_DIR"]))
        self.assertTrue(result["audio_filepath"].endswith(".mp3"))
        self.assertEqual(result["audio_format"], "mp3")
        
        expected_text_for_tts = "Main Podcast Title.\n\nIntro content for our test.\n\nFirst Segment.\n\nContent of the first segment."
        
        mock_tts_client_instance.synthesize_speech.assert_called_once()
        call_args = mock_tts_client_instance.synthesize_speech.call_args[1]['request']
        self.assertEqual(call_args['input'].text, expected_text_for_tts)
        self.assertEqual(call_args['voice'].language_code, "en-TEST")
        self.assertEqual(call_args['voice'].name, "en-TEST-Voice")
        
        # Depending on whether texttospeech is real or mocked, the enum value might differ
        # For safety, compare against the value fetched from the (mocked) map
        expected_encoding_enum = vfa_main.google_audio_encoding_map["MP3"]
        self.assertEqual(call_args['audio_config'].audio_encoding, expected_encoding_enum)

        mock_file_open.assert_called_once_with(result["audio_filepath"], "wb")
        mock_file_open().write.assert_called_once_with(b"mock audio")

    def test_forge_voice_pswa_error_script(self):
        error_script = {
            "script_id": "s_err", "topic": "Error Topic", "title": "Error Title",
            "full_raw_script": "[ERROR] Insufficient content provided for topic: Error Topic",
            "segments": [{"segment_title": "ERROR", "content": "[ERROR] Insufficient content..."}]
        }
        result = vfa_main.forge_voice(error_script)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("error message from PSWA", result["message"])

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_empty_segments_uses_raw_script(self, mock_file, mock_tts_constructor):
        script_no_segments = {
            "script_id": "s_raw", "topic": "Raw Topic", "title": "Raw Title",
            "full_raw_script": "This is the full raw script content only, long enough.",
            "segments": []
        }
        mock_tts_instance = mock_tts_constructor.return_value
        mock_tts_instance.synthesize_speech.return_value = MagicMock(audio_content=b"raw audio")

        result = vfa_main.forge_voice(script_no_segments)
        self.assertEqual(result["status"], "success")

        synthesized_text = mock_tts_instance.synthesize_speech.call_args[1]['request']['input'].text
        self.assertEqual(synthesized_text, "This is the full raw script content only, long enough.")


    def test_forge_voice_no_usable_text(self):
        script_no_text = {
            "script_id": "s_notxt", "topic": "No Text Topic", "title": "No Text Title",
            "full_raw_script": "", "segments": [] # Empty raw script and segments
        }
        result = vfa_main.forge_voice(script_no_text)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("too short", result["message"])

    def test_forge_voice_missing_google_credentials(self):
        with patch.dict(vfa_main.vfa_config, {"GOOGLE_APPLICATION_CREDENTIALS": ""}):
            result = vfa_main.forge_voice({"script_id": "s_nocred", "topic": "No Creds", "title": "No Creds", "full_raw_script": "test script long enough", "segments": []})
            self.assertEqual(result["status"], "error")
            self.assertIn("GOOGLE_APPLICATION_CREDENTIALS environment variable not set", result["message"])

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    def test_forge_voice_tts_api_error(self, mock_tts_client_constructor):
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        # Use the potentially mocked google_exceptions.GoogleAPIError
        mock_tts_client_instance.synthesize_speech.side_effect = vfa_main.google_exceptions.GoogleAPIError("TTS API failed")

        valid_script = {"script_id": "s_apierr", "topic": "API Error", "title": "API Error", "full_raw_script": "A valid script long enough for TTS attempt.", "segments": []}
        result = vfa_main.forge_voice(valid_script)
        self.assertEqual(result["status"], "error")
        self.assertIn("Google TTS API Error", result["message"])


class TestForgeVoiceEndpoint(unittest.TestCase):
    def setUp(self):
        vfa_main.app.config['TESTING'] = True
        self.client = vfa_main.app.test_client()
        self.config_patcher = patch.dict(vfa_main.vfa_config, {
             "GOOGLE_APPLICATION_CREDENTIALS": "fake_creds.json"
        })
        self.mock_config = self.config_patcher.start()
        # Ensure imports are considered successful for endpoint tests that call forge_voice
        self.imports_patcher = patch.object(vfa_main, 'VFA_IMPORTS_SUCCESSFUL', True)
        self.mock_imports = self.imports_patcher.start()


    def tearDown(self):
        self.config_patcher.stop()
        self.imports_patcher.stop()

    @patch('aethercast.vfa.main.forge_voice')
    def test_handle_forge_voice_success(self, mock_forge_voice_func):
        mock_forge_voice_func.return_value = {
            "status": "success", "message": "Audio created",
            "audio_filepath": "/path/audio.mp3", "stream_id": "s1"
        }
        payload = {"script": {"script_id": "s1", "topic": "Test", "title": "Test", "full_raw_script": "Test script", "segments": []}}
        response = self.client.post('/forge_voice', json=payload)
        
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        self.assertEqual(json_data["status"], "success")
        self.assertEqual(json_data["audio_filepath"], "/path/audio.mp3")
        mock_forge_voice_func.assert_called_once_with(payload["script"])

    def test_handle_forge_voice_missing_script(self):
        response = self.client.post('/forge_voice', json={})
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertIn("Missing 'script' parameter", json_data["message"])

    def test_handle_forge_voice_script_not_dict(self):
        response = self.client.post('/forge_voice', json={"script": "this is a string, not a dict"})
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertIn("'script' parameter must be a valid JSON object", json_data["message"])

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
