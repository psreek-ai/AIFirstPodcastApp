import unittest
from unittest import mock
from unittest.mock import patch, mock_open, MagicMock
import sys
import os
import uuid

# Ensure the 'aethercast' directory (which is one level up from 'vfa')
# is in the Python path for absolute imports.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
vfa_dir = os.path.dirname(current_script_dir) # aethercast/vfa/
aethercast_dir = os.path.dirname(vfa_dir) # aethercast/
project_root_dir = os.path.dirname(aethercast_dir) # repo root

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

# Import target function and constants
from aethercast.vfa.main import (
    forge_voice, 
    MIN_SCRIPT_LENGTH_FOR_AUDIO, 
    PSWA_ERROR_PREFIXES, 
    TEMP_AUDIO_DIR,
    DEFAULT_TTS_VOICE_NAME,
    DEFAULT_TTS_LANG_CODE,
    DEFAULT_AUDIO_ENCODING_TYPE # This will be the one from vfa.main, possibly placeholder
)

# Attempt to import Google Cloud libraries for type checking and error simulation
# These will be mocked during tests anyway.
try:
    from google.cloud import texttospeech
    from google.api_core import exceptions as google_exceptions
    # If the main module used a placeholder for texttospeech.AudioEncoding,
    # we might want to use the real one here if available for constructing expected objects.
    # However, DEFAULT_AUDIO_ENCODING_TYPE from vfa.main should be what's used in the code.
except ImportError:
    # Create placeholders if not available, so tests can still reference these types
    class PlaceholderGoogleTTS:
        SynthesisInput = MagicMock
        VoiceSelectionParams = MagicMock
        AudioConfig = MagicMock
        AudioEncoding = type('AudioEncoding', (object,), {'MP3': 2, 'LINEAR16': 3, 'OGG_OPUS': 4})() # Match vfa.main placeholder
    
    class PlaceholderGoogleExceptions:
        GoogleAPIError = type('GoogleAPIError', (Exception,), {})
        ServiceUnavailable = type('ServiceUnavailable', (GoogleAPIError,), {})

    texttospeech = PlaceholderGoogleTTS()
    google_exceptions = PlaceholderGoogleExceptions()


TEST_UUID = uuid.UUID('12345678-1234-5678-1234-567812345678')

class TestForgeVoice(unittest.TestCase):

    def setUp(self):
        """Set up common patches for tests."""
        self.credentials_patcher = patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "fake_google_creds.json"})
        self.imports_patcher = patch('aethercast.vfa.main.VFA_IMPORTS_SUCCESSFUL', True)
        
        self.credentials_patcher.start()
        self.imports_patcher.start()

    def tearDown(self):
        """Clean up patches."""
        self.imports_patcher.stop()
        self.credentials_patcher.stop()

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('builtins.open', new_callable=mock_open)
    @patch('aethercast.vfa.main.uuid.uuid4')
    @patch('aethercast.vfa.main.os.makedirs')
    def test_successful_tts_generation_and_save(self, mock_makedirs, mock_uuid4, mock_file_open, mock_tts_client_constructor):
        """Test successful TTS generation and saving of the audio file."""
        mock_uuid4.return_value = TEST_UUID
        
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_response = MagicMock() # Use MagicMock for attribute assignment
        mock_tts_response.audio_content = b"fake_audio_bytes"
        mock_tts_client_instance.synthesize_speech.return_value = mock_tts_response
        
        script = "This is a valid script for TTS processing, long enough to pass checks."
        result = forge_voice(script)

        mock_tts_client_constructor.assert_called_once()
        mock_tts_client_instance.synthesize_speech.assert_called_once()
        
        # Verify call arguments for synthesize_speech
        call_args = mock_tts_client_instance.synthesize_speech.call_args
        request_arg = call_args.kwargs['request']
        self.assertIsInstance(request_arg['input'], texttospeech.SynthesisInput) # Check type if real lib available
        self.assertEqual(request_arg['input'].text, script)
        self.assertEqual(request_arg['voice'].language_code, DEFAULT_TTS_LANG_CODE)
        self.assertEqual(request_arg['voice'].name, DEFAULT_TTS_VOICE_NAME)
        self.assertEqual(request_arg['audio_config'].audio_encoding, DEFAULT_AUDIO_ENCODING_TYPE)

        mock_makedirs.assert_called_once_with(TEMP_AUDIO_DIR, exist_ok=True)
        
        # Determine expected extension based on DEFAULT_AUDIO_ENCODING_TYPE from vfa.main
        expected_extension = ".mp3" # Default if MP3
        if DEFAULT_AUDIO_ENCODING_TYPE == texttospeech.AudioEncoding.LINEAR16: # Use the actual value from the imported constant
            expected_extension = ".wav"
        elif DEFAULT_AUDIO_ENCODING_TYPE == texttospeech.AudioEncoding.OGG_OPUS:
            expected_extension = ".ogg"
            
        expected_filepath = os.path.join(TEMP_AUDIO_DIR, f"aethercast_audio_{TEST_UUID.hex}{expected_extension}")
        
        mock_file_open.assert_called_once_with(expected_filepath, "wb")
        mock_file_open().write.assert_called_once_with(b"fake_audio_bytes")
        
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("audio_filepath"), expected_filepath)
        self.assertEqual(result.get("audio_format"), texttospeech.AudioEncoding.Name(DEFAULT_AUDIO_ENCODING_TYPE).lower())
        self.assertEqual(result.get("engine_used"), "google_cloud_tts")

    @patch.dict(os.environ, {}, clear=True) # Override setUp patch
    def test_missing_google_credentials(self):
        """Test VFA behavior when GOOGLE_APPLICATION_CREDENTIALS are not set."""
        result = forge_voice("A valid script.")
        self.assertEqual(result.get("status"), "error")
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS environment variable not set", result.get("message", ""))
        self.assertEqual(result.get("engine_used"), "google_cloud_tts_no_credentials")

    @patch('aethercast.vfa.main.VFA_IMPORTS_SUCCESSFUL', False)
    @patch('aethercast.vfa.main.VFA_MISSING_IMPORT_ERROR', "Test: TTS SDK is missing")
    def test_vfa_imports_failed(self, mock_imports_flag_ignored, mock_error_msg_ignored): # Mocks are applied by decorators
        """Test VFA behavior when Google Cloud TTS library import fails."""
        result = forge_voice("A valid script.")
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Google Cloud Text-to-Speech library not available. Test: TTS SDK is missing", result.get("message", ""))
        self.assertEqual(result.get("engine_used"), "google_cloud_tts_unavailable")

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    def test_google_api_error_on_synthesize(self, mock_tts_client_constructor):
        """Test handling of GoogleAPIError during speech synthesis."""
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.side_effect = google_exceptions.ServiceUnavailable("TTS service currently down")
        
        result = forge_voice("A valid script for TTS.")
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Google TTS API Error: ServiceUnavailable", result.get("message", ""))
        self.assertIn("TTS service currently down", result.get("message", ""))

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    def test_unexpected_error_on_synthesize(self, mock_tts_client_constructor):
        """Test handling of unexpected errors during speech synthesis."""
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.side_effect = RuntimeError("Unexpected Boom!")
        
        result = forge_voice("A valid script for TTS.")
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Unexpected error during TTS synthesis: RuntimeError", result.get("message", ""))
        self.assertIn("Unexpected Boom!", result.get("message", ""))

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient') # Still need to mock as it's called if checks pass
    @patch('builtins.open', new_callable=mock_open)
    def test_script_too_short_skips_tts(self, mock_file_open, mock_tts_client_constructor):
        """Test that TTS is skipped for scripts shorter than MIN_SCRIPT_LENGTH_FOR_AUDIO."""
        short_script = "Too short."
        self.assertTrue(len(short_script) < MIN_SCRIPT_LENGTH_FOR_AUDIO)
        
        result = forge_voice(short_script)
        
        self.assertEqual(result.get("status"), "skipped")
        self.assertIn("Script too short", result.get("message", ""))
        mock_tts_client_constructor.assert_not_called()
        mock_file_open.assert_not_called()

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('builtins.open', new_callable=mock_open)
    def test_script_is_pswa_error_skips_tts(self, mock_file_open, mock_tts_client_constructor):
        """Test that TTS is skipped if the script is a known PSWA error string."""
        # Use one of the prefixes defined in vfa.main's PSWA_ERROR_PREFIXES
        pswa_error_script = PSWA_ERROR_PREFIXES[0] + " - Details of the API key error."
        
        result = forge_voice(pswa_error_script)
        
        self.assertEqual(result.get("status"), "skipped")
        self.assertIn("Script appears to be an error message from PSWA", result.get("message", ""))
        mock_tts_client_constructor.assert_not_called()
        mock_file_open.assert_not_called()

if __name__ == '__main__':
    unittest.main()
