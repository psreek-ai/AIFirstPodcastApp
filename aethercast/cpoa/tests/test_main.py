import unittest
from unittest import mock
import sys
import os
import json

# Ensure the 'aethercast' directory (which is one level up from 'cpoa')
# is in the Python path for absolute imports.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
cpoa_dir = os.path.dirname(current_script_dir) # aethercast/cpoa/
aethercast_dir = os.path.dirname(cpoa_dir) # aethercast/
project_root_dir = os.path.dirname(aethercast_dir) # repo root

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.cpoa.main import orchestrate_podcast_generation, TOPIC_TO_URL_MAP as CPOA_TOPIC_MAP # Import the map for reference if needed, but we will mock it

class TestOrchestrationFlow(unittest.TestCase):

    # Common mock setup for VFA and PSWA
    def _configure_downstream_mocks(self, mock_pswa, mock_vfa, pswa_script_content=None):
        if pswa_script_content is None:
            # More realistic LLM-like script for default success
            pswa_script_content = """[TITLE] Default Mock Podcast Title
[INTRO] Welcome to this default mock podcast. We explore fascinating default topics.
[SEGMENT_1_TITLE] Default Segment Alpha
[SEGMENT_1_CONTENT] This is the detailed content for segment Alpha. It's quite engaging.
[OUTRO] Thanks for listening to this default mock. Tune in next time!"""
        
        mock_pswa.return_value = pswa_script_content
        
        # Updated VFA success mock to include new fields
        mock_vfa.return_value = {
            "status": "success", 
            "message": "Mock audio successfully synthesized for test.",
            "audio_filepath": f"/tmp/aethercast_audio/mock_cpoa_test_audio_{len(pswa_script_content)}.mp3", # Example dynamic path
            "audio_format": "mp3",
            "script_char_count": len(pswa_script_content),
            "engine_used": "mock_google_cloud_tts" 
        }

    # Helper for VFA side effect in specific tests
    def _vfa_side_effect_for_pswa_error_string(self, script_input_to_vfa: str):
        pswa_error_indicators = [
            "OpenAI library not available", "Error: OPENAI_API_KEY", 
            "OpenAI API Error:", "An unexpected error occurred",
            "[ERROR] Insufficient content" 
        ]
        is_error_string = any(script_input_to_vfa.startswith(prefix) for prefix in pswa_error_indicators)
        
        if is_error_string:
            # Updated VFA "skipped" mock
            return {
                "status": "skipped", 
                "message": "VFA skipped due to PSWA error string (mocked).",
                "audio_filepath": None,
                "audio_format": None, 
                "script_char_count": len(script_input_to_vfa),
                "engine_used": "google_cloud_tts" # VFA now specifies engine even if skipped
            }
        
        # Updated VFA success mock for the side_effect path
        return {
            "status": "success", 
            "message": "Mock audio generated for VFA (side_effect success path).", 
            "audio_filepath": f"/tmp/aethercast_audio/mock_cpoa_side_effect_audio_{len(script_input_to_vfa)}.mp3",
            "audio_format": "mp3",
            "script_char_count": len(script_input_to_vfa),
            "engine_used": "mock_google_cloud_tts_side_effect"
        }


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"ai in healthcare": "http://example.com/ai_health"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_mapped_topic_live_fetch_success(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test mapped topic with successful live fetch."""
        # PSWA and VFA mocks are configured by _configure_downstream_mocks
        # Get the expected VFA output from the helper for assertion
        expected_pswa_script = """[TITLE] Default Mock Podcast Title
[INTRO] Welcome to this default mock podcast. We explore fascinating default topics.
[SEGMENT_1_TITLE] Default Segment Alpha
[SEGMENT_1_CONTENT] This is the detailed content for segment Alpha. It's quite engaging.
[OUTRO] Thanks for listening to this default mock. Tune in next time!"""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice, pswa_script_content=expected_pswa_script)
        expected_vfa_dict = {
            "status": "success", "message": "Mock audio successfully synthesized for test.",
            "audio_filepath": f"/tmp/aethercast_audio/mock_cpoa_test_audio_{len(expected_pswa_script)}.mp3",
            "audio_format": "mp3", "script_char_count": len(expected_pswa_script),
            "engine_used": "mock_google_cloud_tts"
        }
        mock_forge_voice.return_value = expected_vfa_dict # Ensure it's exactly this for the test

        live_content = "live content for AI in healthcare from a reliable source"
        mock_harvest_from_url.return_value = live_content
        
        test_topic = "ai in healthcare"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_called_once_with("http://example.com/ai_health")
        mock_harvest_content.assert_not_called()
        mock_weave_script.assert_called_once_with(content=live_content, topic=test_topic)
        self.assertEqual(result.get("status"), "completed")
        self.assertIn("Successfully harvested content from URL", result["orchestration_log"][-4]["message"]) 
        self.assertEqual(result.get("final_audio_details"), expected_vfa_dict)


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"ai in healthcare": "http://example.com/ai_health_fail"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_mapped_topic_live_fetch_fails_fallback_succeeds(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test mapped topic, live fetch fails, fallback to mock succeeds."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice) # Uses default realistic mocks
        expected_vfa_dict_from_helper = mock_forge_voice.return_value # Capture what the helper set up

        mock_harvest_from_url.return_value = "Error fetching URL..." 
        fallback_content = "mock fallback for AI in healthcare from internal data"
        mock_harvest_content.return_value = fallback_content
        
        test_topic = "ai in healthcare"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_called_once_with("http://example.com/ai_health_fail")
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content=fallback_content, topic=test_topic)
        self.assertEqual(result.get("status"), "completed")
        self.assertIn("Falling back to mock harvest", result["orchestration_log"][-5]["message"])
        self.assertEqual(result.get("final_audio_details"), expected_vfa_dict_from_helper)


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"climate change": "http://example.com/climate_fail"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_mapped_topic_live_fetch_fails_fallback_fails(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test mapped topic, live fetch fails, fallback to mock also returns 'not found'."""
        pswa_generic_script_for_no_content = """[TITLE] Climate Change Update
[INTRO] Today we discuss climate change, but current specific details are limited.
[OUTRO] We'll revisit this topic with more information soon."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice, pswa_script_content=pswa_generic_script_for_no_content)
        # Capture the VFA response configured by the helper for this specific PSWA script
        expected_vfa_dict_for_generic_script = mock_forge_voice.return_value.copy() 
        expected_vfa_dict_for_generic_script['script_char_count'] = len(pswa_generic_script_for_no_content)
        # Update the path to reflect the new script length if the helper makes it dynamic
        expected_vfa_dict_for_generic_script['audio_filepath'] = f"/tmp/aethercast_audio/mock_cpoa_test_audio_{len(pswa_generic_script_for_no_content)}.mp3"


        mock_harvest_from_url.return_value = "Error fetching URL..."
        fallback_not_found_message = "No pre-defined content found for topic: climate change"
        mock_harvest_content.return_value = fallback_not_found_message
        
        test_topic = "climate change"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_called_once_with("http://example.com/climate_fail")
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content=fallback_not_found_message, topic=test_topic)
        
        vfa_call_arg_script = mock_forge_voice.call_args[0][0]
        self.assertEqual(vfa_call_arg_script, pswa_generic_script_for_no_content)
        
        # If VFA's MIN_SCRIPT_LENGTH_FOR_AUDIO is small enough, this might pass.
        # The VFA mock in _configure_downstream_mocks assumes success by default.
        # We need to check if the CPOA status reflects a VFA skip IF the generic script is too short.
        # For now, assume the generic script is long enough for the default VFA mock.
        if result.get("final_audio_details", {}).get("status") == "skipped":
             self.assertEqual(result.get("status"), "completed_with_warnings")
        else: 
            self.assertEqual(result.get("status"), "completed")
            self.assertEqual(result.get("final_audio_details"), expected_vfa_dict_for_generic_script) # Check full structure
            
        self.assertIn("Falling back to mock harvest", result["orchestration_log"][-5]["message"])
        self.assertIn("Final content is a 'not found' message", result["orchestration_log"][-4]["message"])


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) 
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_unmapped_topic_uses_mock_success(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test unmapped topic, uses mock data successfully."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice)
        expected_vfa_dict_from_helper = mock_forge_voice.return_value

        mock_content_str = "mock content for quantum computing from internal data"
        mock_harvest_content.return_value = mock_content_str
        
        test_topic = "quantum computing"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_not_called()
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content=mock_content_str, topic=test_topic)
        self.assertEqual(result.get("status"), "completed")
        self.assertIn("Using mock harvest for topic", result["orchestration_log"][-4]["message"])
        self.assertEqual(result.get("final_audio_details"), expected_vfa_dict_from_helper)


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) 
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_unmapped_topic_uses_mock_not_found(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test unmapped topic, mock data also returns 'not found'."""
        pswa_generic_script_for_no_content = """[TITLE] Underwater Weaving Wonders
[INTRO] Today we try to explore underwater basket weaving.
[OUTRO] Our exploration was short today."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice, pswa_script_content=pswa_generic_script_for_no_content)
        expected_vfa_dict_for_generic_script = mock_forge_voice.return_value.copy()
        expected_vfa_dict_for_generic_script['script_char_count'] = len(pswa_generic_script_for_no_content)
        expected_vfa_dict_for_generic_script['audio_filepath'] = f"/tmp/aethercast_audio/mock_cpoa_test_audio_{len(pswa_generic_script_for_no_content)}.mp3"
        
        not_found_message = "No pre-defined content found for topic: underwater basket weaving"
        mock_harvest_content.return_value = not_found_message
        
        test_topic = "underwater basket weaving"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_not_called()
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content=not_found_message, topic=test_topic)
        
        vfa_call_arg_script = mock_forge_voice.call_args[0][0]
        self.assertEqual(vfa_call_arg_script, pswa_generic_script_for_no_content)

        if result.get("final_audio_details", {}).get("status") == "skipped":
             self.assertEqual(result.get("status"), "completed_with_warnings")
        else:
            self.assertEqual(result.get("status"), "completed")
            self.assertEqual(result.get("final_audio_details"), expected_vfa_dict_for_generic_script)
        self.assertIn("Final content is a 'not found' message", result["orchestration_log"][-4]["message"])

    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) 
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url') 
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_vfa_skipped_status_propagation(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test that VFA 'skipped' status propagates correctly."""
        mock_harvest_content.return_value = "mock_content_short_enough_for_pswa"
        
        short_script_output = "[TITLE] Short\n[INTRO] Too short for VFA.\n[OUTRO] End."
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice, pswa_script_content=short_script_output) # Base config
        
        # Explicitly set the VFA mock to return the detailed "skipped" structure
        expected_vfa_skipped_dict = {
            "status": "skipped", 
            "message": "Script too short, VFA skipped (mocked).", 
            "audio_filepath": None,
            "audio_format": None,
            "script_char_count": len(short_script_output), 
            "engine_used": "google_cloud_tts" # VFA indicates engine even if skipped
        }
        mock_forge_voice.return_value = expected_vfa_skipped_dict
        
        test_topic = "short_script_topic_for_vfa_skip"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content="mock_content_short_enough_for_pswa", topic=test_topic)
        mock_forge_voice.assert_called_once_with(script=short_script_output)

        self.assertEqual(result.get("status"), "completed_with_warnings")
        self.assertEqual(result.get("final_audio_details"), expected_vfa_skipped_dict) # Check full structure
        
        vfa_info_logged = any("VFA Info: Voice forging was not fully successful" in entry["message"] for entry in result["orchestration_log"])
        self.assertTrue(vfa_info_logged, "VFA info log for skipped status was not found.")
        
        completion_log_message = result["orchestration_log"][-1]["message"]
        self.assertIn(f"Orchestration finished with status: 'completed_with_warnings' for topic: '{test_topic}'", completion_log_message)

if __name__ == '__main__':
    unittest.main()
