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
        self.maxDiff = None
        self.mock_vfa_config = {
            "GOOGLE_APPLICATION_CREDENTIALS": "fake_creds.json",
            "VFA_SHARED_AUDIO_DIR": "/tmp/vfa_test_audio",
            "VFA_TTS_VOICE_NAME": "en-US-Standard-A", # Default voice
            "VFA_TTS_LANG_CODE": "en-US",             # Default lang
            "VFA_TTS_AUDIO_ENCODING_STR": "MP3",
            "VFA_MIN_SCRIPT_LENGTH": 10,
            "VFA_TTS_DEFAULT_SPEAKING_RATE": 1.0,     # Added default
            "VFA_TTS_DEFAULT_PITCH": 0.0              # Added default
        }
        # Use clear=True with patch.dict if vfa_config might already exist from module import
        self.config_patcher = patch.dict(vfa_main.vfa_config, self.mock_vfa_config, clear=True)
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
        self.assertEqual(call_args['voice'].language_code, self.mock_vfa_config["VFA_TTS_LANG_CODE"])
        self.assertEqual(call_args['voice'].name, self.mock_vfa_config["VFA_TTS_VOICE_NAME"])
        self.assertEqual(call_args['audio_config'].speaking_rate, self.mock_vfa_config["VFA_TTS_DEFAULT_SPEAKING_RATE"])
        self.assertEqual(call_args['audio_config'].pitch, self.mock_vfa_config["VFA_TTS_DEFAULT_PITCH"])
        
        expected_encoding_enum = vfa_main.google_audio_encoding_map["MP3"]
        self.assertEqual(call_args['audio_config'].audio_encoding, expected_encoding_enum)

        mock_file_open.assert_called_once_with(result["audio_filepath"], "wb")
        mock_file_open().write.assert_called_once_with(b"mock audio")
        self.assertIsNotNone(result.get("tts_settings_used"))
        self.assertEqual(result["tts_settings_used"]["voice_name"], self.mock_vfa_config["VFA_TTS_VOICE_NAME"])

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_with_custom_voice_params(self, mock_file_open, mock_tts_client_constructor):
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"custom audio")

        structured_script = {
            "script_id": "s_custom", "topic": "Custom Voice", "title": "Custom Voice Test",
            "segments": [{"segment_title": "INTRO", "content": "Testing custom voice parameters."}]
        }
        custom_voice_params = {
            "voice_name": "en-GB-Wavenet-F",
            "language_code": "en-GB",
            "speaking_rate": 1.2,
            "pitch": -2.5
        }
        result = vfa_main.forge_voice(structured_script, voice_params_input=custom_voice_params)

        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(result["tts_settings_used"])
        self.assertEqual(result["tts_settings_used"]["voice_name"], "en-GB-Wavenet-F")
        self.assertEqual(result["tts_settings_used"]["language_code"], "en-GB")
        self.assertEqual(result["tts_settings_used"]["speaking_rate"], 1.2)
        self.assertEqual(result["tts_settings_used"]["pitch"], -2.5) # Clamped to -2.0 by logic in forge_voice if out of range

        mock_tts_client_instance.synthesize_speech.assert_called_once()
        call_args = mock_tts_client_instance.synthesize_speech.call_args[1]['request']
        self.assertEqual(call_args['voice'].name, "en-GB-Wavenet-F")
        self.assertEqual(call_args['voice'].language_code, "en-GB")
        self.assertEqual(call_args['audio_config'].speaking_rate, 1.2)
        self.assertEqual(call_args['audio_config'].pitch, -2.5) # Check if clamping logic is tested separately if needed

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_with_partial_voice_params(self, mock_file_open, mock_tts_client_constructor):
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"partial audio")
        structured_script = {"segments": [{"content": "Test partial params"}]}
        partial_voice_params = {"speaking_rate": 0.9}

        result = vfa_main.forge_voice(structured_script, voice_params_input=partial_voice_params)

        self.assertEqual(result["status"], "success")
        used_settings = result["tts_settings_used"]
        self.assertEqual(used_settings["speaking_rate"], 0.9)
        self.assertEqual(used_settings["voice_name"], self.mock_vfa_config["VFA_TTS_VOICE_NAME"]) # Default
        self.assertEqual(used_settings["pitch"], self.mock_vfa_config["VFA_TTS_DEFAULT_PITCH"])   # Default

        call_args = mock_tts_client_instance.synthesize_speech.call_args[1]['request']
        self.assertEqual(call_args['audio_config'].speaking_rate, 0.9)
        self.assertEqual(call_args['voice'].name, self.mock_vfa_config["VFA_TTS_VOICE_NAME"])


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

    @patch('aethercast.vfa.main.VFA_IMPORTS_SUCCESSFUL', False)
    @patch('aethercast.vfa.main.VFA_MISSING_IMPORT_ERROR', "Simulated google.cloud.texttospeech import error")
    def test_forge_voice_google_sdk_import_failure(self):
        # This test relies on VFA_IMPORTS_SUCCESSFUL being False at the module level
        # when forge_voice is called. Patching it globally for the duration of this test.

        # Script input needs to be long enough to pass the min_length check,
        # otherwise it will return "skipped" before hitting the import check.
        # Constructing text_to_synthesize based on how forge_voice does it:
        title = "SDK Import Fail Title"
        intro_content = "Content to make it long enough past min_script_length which is 10 for this test class."
        # Ensure combined length is > vfa_config['VFA_MIN_SCRIPT_LENGTH'] (default 10 from setUp)
        text_to_synthesize_example = f"{title}. {intro_content}"
        # Manually check length based on setUp's config
        self.assertTrue(len(text_to_synthesize_example) > self.mock_vfa_config['VFA_MIN_SCRIPT_LENGTH'])


        valid_script_long_enough = {
            "script_id": "s_sdk_fail", "topic": "SDK Import Fail", "title": title,
            "full_raw_script": "This is a sufficiently long script to ensure we pass initial length checks and hit the SDK import error path.",
            "segments": [{"segment_title": "INTRO", "content": intro_content }]
        }
        # Ensure the min_script_length in mocked config is less than len(text_to_synthesize)
        # Default min_script_length is 10 in setUp, this script is longer.

        result = vfa_main.forge_voice(script_input=valid_script_long_enough)

        self.assertIn("error_code", result)
        self.assertEqual(result["error_code"], "VFA_IMPORT_ERROR_GOOGLE_SDK")
        self.assertIn("Google Cloud Text-to-Speech library not available.", result["message"])
        self.assertIn("Simulated google.cloud.texttospeech import error", result["details"])
        self.assertEqual(result["engine_used"], "google_cloud_tts_unavailable")
        self.assertIsNotNone(result["tts_settings_used"]) # Should still contain attempted settings

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient') # Mock the client
    @patch('os.makedirs') # Target os.makedirs directly
    def test_forge_voice_os_makedirs_io_error(self, mock_os_makedirs, mock_tts_client_constructor):
        # Ensure imports are successful for this path
        # self.mock_imports_successful is True from setUp

        mock_os_makedirs.side_effect = IOError("Simulated os.makedirs failure")

        # Mock TTS client because it's initialized before makedirs is called
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"mock audio")

        valid_script_long_enough = {
            "script_id": "s_mkdir_fail", "topic": "Mkdir Fail", "title": "Mkdir Fail Title",
            "full_raw_script": "This script is long enough to pass length checks.",
            "segments": [{"segment_title": "INTRO", "content": "Content to ensure it's long enough."}]
        }
        # This test assumes VFA_TEST_MODE_ENABLED is False, which is not the default in setUp.
        # We need to override it for this test.
        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            with patch.object(vfa_main.logger, 'error') as mock_logger_error:
                result = vfa_main.forge_voice(script_input=valid_script_long_enough)

                self.assertIn("error_code", result)
                self.assertEqual(result["error_code"], "VFA_FILE_SYSTEM_ERROR_MKDIR")
                self.assertIn("VFA failed to create output directory.", result["message"])
                self.assertEqual(result["details"], "Simulated os.makedirs failure")
                self.assertEqual(result["engine_used"], "google_cloud_tts")
                self.assertIsNotNone(result["tts_settings_used"])

                # Verify logging if specific logging for this was added
                # Example: self.assertTrue(any("Failed to create directory" in call_arg[0][0] for call_arg in mock_logger_error.call_args_list))

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('os.makedirs', return_value=None) # Mock makedirs to succeed
    @patch('builtins.open', new_callable=mock_open) # Mock open
    def test_forge_voice_file_open_io_error(self, mock_file_open_builtin, mock_os_makedirs, mock_tts_client_constructor):
        # Ensure imports are successful
        # self.mock_imports_successful is True from setUp

        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"mock audio data")

        # Configure mock_open to raise IOError only for 'wb' mode
        mock_file_open_builtin.side_effect = lambda file, mode='r', *args, **kwargs: \
            mock_open(file, mode, *args, **kwargs).return_value if mode != 'wb' \
            else (_ for _ in ()).throw(IOError("Simulated file open for write failure"))


        valid_script_long_enough = {
            "script_id": "s_fopen_fail", "topic": "Fopen Fail", "title": "Fopen Fail Title",
            "full_raw_script": "This script is also long enough.",
            "segments": [{"segment_title": "INTRO", "content": "Sufficient content here."}]
        }

        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            with patch.object(vfa_main.logger, 'error') as mock_logger_error:
                result = vfa_main.forge_voice(script_input=valid_script_long_enough)

                self.assertIn("error_code", result)
                self.assertEqual(result["error_code"], "VFA_FILE_SYSTEM_ERROR_WRITE_AUDIO")
                self.assertIn("VFA failed to write synthesized audio to file.", result["message"])
                self.assertEqual(result["details"], "Simulated file open for write failure")
                self.assertEqual(result["engine_used"], "google_cloud_tts")
                self.assertIsNotNone(result["tts_settings_used"])

                # Verify logging if specific logging for this was added
                # Example: self.assertTrue(any("Failed to open file for writing" in call_arg[0][0] for call_arg in mock_logger_error.call_args_list))

    # --- Tests for Voice Parameter Clamping ---
    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('os.makedirs', return_value=None)
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_speaking_rate_clamped_low(self, mock_file_open_builtin, mock_os_makedirs, mock_tts_client_constructor):
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"mock audio")

        script_input = {"script_id": "s_clamp1", "topic": "Clamping", "title": "Rate Low", "segments": [{"content": "Long enough content for test."}]}
        voice_params = {"speaking_rate": 0.1} # Below min 0.25

        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            vfa_main.forge_voice(script_input, voice_params_input=voice_params)

        mock_tts_client_instance.synthesize_speech.assert_called_once()
        called_audio_config = mock_tts_client_instance.synthesize_speech.call_args[1]['request']['audio_config']
        self.assertEqual(called_audio_config.speaking_rate, 0.25) # Clamped to min

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('os.makedirs', return_value=None)
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_speaking_rate_clamped_high(self, mock_file_open_builtin, mock_os_makedirs, mock_tts_client_constructor):
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"mock audio")

        script_input = {"script_id": "s_clamp2", "topic": "Clamping", "title": "Rate High", "segments": [{"content": "Long enough content for test."}]}
        voice_params = {"speaking_rate": 10.0} # Above max 4.0

        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            vfa_main.forge_voice(script_input, voice_params_input=voice_params)

        mock_tts_client_instance.synthesize_speech.assert_called_once()
        called_audio_config = mock_tts_client_instance.synthesize_speech.call_args[1]['request']['audio_config']
        self.assertEqual(called_audio_config.speaking_rate, 4.0) # Clamped to max

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('os.makedirs', return_value=None)
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_pitch_clamped_low(self, mock_file_open_builtin, mock_os_makedirs, mock_tts_client_constructor):
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"mock audio")

        script_input = {"script_id": "s_clamp3", "topic": "Clamping", "title": "Pitch Low", "segments": [{"content": "Long enough content for test."}]}
        voice_params = {"pitch": -30.0} # Below min -20.0

        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            vfa_main.forge_voice(script_input, voice_params_input=voice_params)

        mock_tts_client_instance.synthesize_speech.assert_called_once()
        called_audio_config = mock_tts_client_instance.synthesize_speech.call_args[1]['request']['audio_config']
        self.assertEqual(called_audio_config.pitch, -20.0) # Clamped to min

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('os.makedirs', return_value=None)
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_pitch_clamped_high(self, mock_file_open_builtin, mock_os_makedirs, mock_tts_client_constructor):
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"mock audio")

        script_input = {"script_id": "s_clamp4", "topic": "Clamping", "title": "Pitch High", "segments": [{"content": "Long enough content for test."}]}
        voice_params = {"pitch": 30.0} # Above max 20.0

        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            vfa_main.forge_voice(script_input, voice_params_input=voice_params)

        mock_tts_client_instance.synthesize_speech.assert_called_once()
        called_audio_config = mock_tts_client_instance.synthesize_speech.call_args[1]['request']['audio_config']
        self.assertEqual(called_audio_config.pitch, 20.0) # Clamped to max

    @patch('aethercast.vfa.main.texttospeech.TextToSpeechClient')
    @patch('os.makedirs', return_value=None)
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_valid_params_not_clamped(self, mock_file_open_builtin, mock_os_makedirs, mock_tts_client_constructor):
        mock_tts_client_instance = mock_tts_client_constructor.return_value
        mock_tts_client_instance.synthesize_speech.return_value = MagicMock(audio_content=b"mock audio")

        script_input = {"script_id": "s_clamp5", "topic": "Clamping", "title": "Valid Params", "segments": [{"content": "Long enough content for test."}]}
        voice_params = {"speaking_rate": 1.5, "pitch": 5.0} # Valid values

        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": False}):
            vfa_main.forge_voice(script_input, voice_params_input=voice_params)

        mock_tts_client_instance.synthesize_speech.assert_called_once()
        called_audio_config = mock_tts_client_instance.synthesize_speech.call_args[1]['request']['audio_config']
        self.assertEqual(called_audio_config.speaking_rate, 1.5) # Unchanged
        self.assertEqual(called_audio_config.pitch, 5.0) # Unchanged


class TestForgeVoiceEndpoint(unittest.TestCase):
    def setUp(self):
        vfa_main.app.config['TESTING'] = True
        self.client = vfa_main.app.test_client()
        # Add VFA_TEST_MODE_ENABLED to the mock_config for this test class
        self.mock_vfa_config_for_endpoint = {
            "GOOGLE_APPLICATION_CREDENTIALS": "fake_creds.json", # Still needed for non-test mode paths if any
            "VFA_SHARED_AUDIO_DIR": "/tmp/vfa_test_audio_endpoint", # Use a specific dir for endpoint tests
            "VFA_TTS_VOICE_NAME": "en-US-Standard-A",
            "VFA_TTS_LANG_CODE": "en-US",
            "VFA_TTS_AUDIO_ENCODING_STR": "MP3",
            "VFA_MIN_SCRIPT_LENGTH": 5, # Lower for some test scenarios if needed
            "VFA_TTS_DEFAULT_SPEAKING_RATE": 1.0,
            "VFA_TTS_DEFAULT_PITCH": 0.0,
            "VFA_TEST_MODE_ENABLED": True # Crucial: Enable Test Mode
        }
        self.config_patcher = patch.dict(vfa_main.vfa_config, self.mock_vfa_config_for_endpoint, clear=True)
        self.mock_config = self.config_patcher.start()

        self.makedirs_patcher = patch('os.makedirs') # Patch os.makedirs for endpoint tests too
        self.mock_makedirs = self.makedirs_patcher.start()

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
