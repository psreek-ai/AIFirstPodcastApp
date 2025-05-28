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
        
        mock_vfa.return_value = {
            "status": "success", "message": "Mock audio generated successfully.", 
            "audio_url": "http://mock.url/default_audio.mp3", "script_char_count": len(pswa_script_content),
            "engine_used": "mock_tts_engine_cpoa_default_test"
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
            return {"status": "skipped", "message": "VFA skipped due to PSWA error string input", 
                    "audio_url": None, "script_char_count": len(script_input_to_vfa),
                    "engine_used": "mock_tts_engine_cpoa_test_skipped_pswa_error"}
        
        return {"status": "success", "message": "Mock audio generated for VFA (side_effect path)", 
                "audio_url": "http://mock.url/audio_side_effect_success.mp3", 
                "script_char_count": len(script_input_to_vfa),
                "engine_used": "mock_tts_engine_cpoa_test_side_effect_other"}


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"ai in healthcare": "http://example.com/ai_health"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_mapped_topic_live_fetch_success(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored): # mock_topic_map_ignored is the mock object for TOPIC_TO_URL_MAP
        """Test mapped topic with successful live fetch."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice) # Uses realistic default PSWA script
        live_content = "live content for AI in healthcare from a reliable source"
        mock_harvest_from_url.return_value = live_content
        
        test_topic = "ai in healthcare"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_called_once_with("http://example.com/ai_health")
        mock_harvest_content.assert_not_called()
        mock_weave_script.assert_called_once_with(content=live_content, topic=test_topic)
        self.assertEqual(result.get("status"), "completed")
        self.assertIn("Successfully harvested content from URL", result["orchestration_log"][-4]["message"]) 

    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"ai in healthcare": "http://example.com/ai_health_fail"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_mapped_topic_live_fetch_fails_fallback_succeeds(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test mapped topic, live fetch fails, fallback to mock succeeds."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice)
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


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"climate change": "http://example.com/climate_fail"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_mapped_topic_live_fetch_fails_fallback_fails(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test mapped topic, live fetch fails, fallback to mock also returns 'not found'."""
        # PSWA will receive "No pre-defined content..." and should produce its generic script.
        # VFA might skip if PSWA's generic script is too short.
        pswa_generic_script_for_no_content = """[TITLE] Climate Change Update
[INTRO] Today we discuss climate change, but current specific details are limited.
[OUTRO] We'll revisit this topic with more information soon."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice, pswa_script_content=pswa_generic_script_for_no_content)
        
        mock_harvest_from_url.return_value = "Error fetching URL..."
        fallback_not_found_message = "No pre-defined content found for topic: climate change"
        mock_harvest_content.return_value = fallback_not_found_message
        
        test_topic = "climate change"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_called_once_with("http://example.com/climate_fail")
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content=fallback_not_found_message, topic=test_topic)
        
        vfa_call_arg_script = mock_forge_voice.call_args[0][0]
        # Check if VFA got the generic script from PSWA
        self.assertEqual(vfa_call_arg_script, pswa_generic_script_for_no_content)
        
        # If VFA's MIN_SCRIPT_LENGTH_FOR_AUDIO is small enough, this might pass.
        # Otherwise, it might result in 'skipped'.
        # Let's assume the generic script is long enough for VFA to "succeed" in this mock setup.
        # If it were to skip, the mock_forge_voice.return_value would need to be set to a skipped dict.
        # Or, _configure_downstream_mocks would need more complex logic for VFA based on script length.
        # For this test, we are verifying the flow up to VFA getting the right (generic) script.
        # The CPOA logic for completed_with_warnings handles VFA skips.

        if result.get("final_audio_details", {}).get("status") == "skipped":
             self.assertEqual(result.get("status"), "completed_with_warnings")
        else: # Assuming the generic script is long enough for VFA mock
            self.assertEqual(result.get("status"), "completed")
            
        self.assertIn("Falling back to mock harvest", result["orchestration_log"][-5]["message"])
        self.assertIn("Final content is a 'not found' message", result["orchestration_log"][-4]["message"])


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) 
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_unmapped_topic_uses_mock_success(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test unmapped topic, uses mock data successfully."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice) # Uses realistic default PSWA script
        mock_content_str = "mock content for quantum computing from internal data"
        mock_harvest_content.return_value = mock_content_str
        
        test_topic = "quantum computing"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_not_called()
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content=mock_content_str, topic=test_topic)
        self.assertEqual(result.get("status"), "completed")
        self.assertIn("Using mock harvest for topic", result["orchestration_log"][-4]["message"])


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
        self.assertIn("Final content is a 'not found' message", result["orchestration_log"][-4]["message"])

    # --- New test for PSWA returning error string ---
    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) # Unmapped topic for simplicity
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_pswa_returns_error_string_propagates_to_vfa(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test PSWA returns an error string, which is then passed to VFA, and VFA skips."""
        mock_harvest_content.return_value = "Some valid content for PSWA to initially process."
        
        pswa_error_output = "OpenAI API Error: Rate limit exceeded." # This is a string error from PSWA
        mock_weave_script.return_value = pswa_error_output
        
        # Configure VFA to use the side effect that checks for error strings
        mock_forge_voice.side_effect = self._vfa_side_effect_for_pswa_error_string
        
        test_topic = "topic_for_pswa_error_string"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_content.assert_called_once_with(topic=test_topic) # WCHA part
        mock_weave_script.assert_called_once_with(content="Some valid content for PSWA to initially process.", topic=test_topic) # PSWA called
        mock_forge_voice.assert_called_once_with(script=pswa_error_output) # VFA called with PSWA's error string
        
        self.assertEqual(result.get("status"), "completed_with_warnings") # CPOA status
        self.assertIsNotNone(result.get("final_audio_details"))
        self.assertEqual(result.get("final_audio_details", {}).get("status"), "skipped") # VFA status
        self.assertIn("VFA skipped due to PSWA error string input", result.get("final_audio_details", {}).get("message", ""))
        
        # Check log for PSWA error indication from CPOA's perspective
        pswa_error_logged_by_cpoa = any("PSWA (LLM) indicated an error or failed to generate a script." in entry["message"] for entry in result["orchestration_log"])
        self.assertTrue(pswa_error_logged_by_cpoa, "CPOA did not log the error string returned by PSWA.")
        # Check log for VFA info about non-successful forging due to skipped status
        vfa_info_logged = any("VFA Info: Voice forging was not fully successful" in entry["message"] and "skipped" in entry.get("data", {}).get("status", "") for entry in result["orchestration_log"])
        self.assertTrue(vfa_info_logged, "VFA info log for skipped status was not found or incorrect.")


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"mapped_topic_wcha_exception": "http://example.com/mapped_topic_wcha_exception"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_wcha_harvest_from_url_raises_exception(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test WCHA harvest_from_url raises an unexpected exception. CPOA should catch it and fail."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice) # PSWA/VFA won't be called
        
        wcha_error_message = "WCHA harvest_from_url_exception"
        mock_harvest_from_url.side_effect = Exception(wcha_error_message)
        
        test_topic = "mapped_topic_wcha_exception"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_called_once_with("http://example.com/mapped_topic_wcha_exception")
        mock_harvest_content.assert_not_called() 
        mock_weave_script.assert_not_called()
        mock_forge_voice.assert_not_called()
        
        self.assertEqual(result.get("status"), "failed")
        self.assertIn(f"WCHA failed critically: {wcha_error_message}", result.get("error_message", ""))
        self.assertTrue(any("WCHA: Critical error during content harvesting" in entry["message"] for entry in result["orchestration_log"]))


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) 
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_wcha_harvest_content_raises_exception(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test WCHA harvest_content (mock path) raises an unexpected exception. CPOA should catch and fail."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice) # PSWA/VFA won't be called
        
        wcha_error_message = "WCHA harvest_content_exception"
        mock_harvest_content.side_effect = Exception(wcha_error_message)

        test_topic = "unmapped_topic_wcha_exception"
        result = orchestrate_podcast_generation(test_topic)

        mock_harvest_from_url.assert_not_called()
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_not_called()
        mock_forge_voice.assert_not_called()

        self.assertEqual(result.get("status"), "failed")
        self.assertIn(f"WCHA failed critically: {wcha_error_message}", result.get("error_message", ""))
        self.assertTrue(any("WCHA: Critical error during content harvesting" in entry["message"] for entry in result["orchestration_log"]))


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) 
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url') 
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_failure_in_pswa(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test workflow failure when PSWA raises an exception (not returns error string)."""
        mock_forge_voice.return_value = {"status": "success"} # Should not be called as PSWA fails
        
        mock_harvest_content.return_value = "mock_content_from_wcha_for_pswa_fail_test"
        
        pswa_exception_message = "PSWA simulated critical EXCEPTION"
        mock_weave_script.side_effect = Exception(pswa_exception_message) # PSWA raises an actual Python exception

        test_topic = "a_topic_that_causes_pswa_exception"
        result = orchestrate_podcast_generation(test_topic)

        mock_harvest_content.assert_called_once_with(topic=test_topic) # WCHA part
        mock_weave_script.assert_called_once_with(content="mock_content_from_wcha_for_pswa_fail_test", topic=test_topic) # PSWA called
        mock_forge_voice.assert_not_called() # VFA should not be called if PSWA raises an exception

        self.assertEqual(result.get("status"), "failed")
        # CPOA's error message for PSWA *exception* is "PSWA failed: {str(e)}"
        self.assertEqual(result.get("error_message"), f"PSWA failed: {pswa_exception_message}")
        
        # Check that the log indicates a *critical* error in PSWA (from CPOA's perspective)
        pswa_critical_error_logged = any("PSWA: Critical error during weave_script (LLM call)" in entry["message"] for entry in result["orchestration_log"])
        self.assertTrue(pswa_critical_error_logged, "PSWA critical exception was not logged correctly by CPOA.")
        # Ensure it's not mistaken for PSWA *returning* an error string in the log
        pswa_returned_error_logged = any("PSWA (LLM) indicated an error or failed to generate a script." in entry["message"] for entry in result["orchestration_log"])
        self.assertFalse(pswa_returned_error_logged, "PSWA critical exception was misidentified as a returned error string in CPOA logs.")


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) 
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url') 
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_failure_in_vfa(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test workflow failure when VFA raises an exception."""
        mock_harvest_content.return_value = "mock_content_from_wcha"
        # Use a more realistic script for PSWA output for this VFA failure test
        realistic_pswa_output_for_vfa_test = """[TITLE] Test VFA Failure Podcast
[INTRO] This script is perfectly fine, but VFA will encounter an issue.
[SEGMENT_1_TITLE] Normal Segment
[SEGMENT_1_CONTENT] This content is structured and ready for voicing.
[OUTRO] The end, or so PSWA thinks."""
        # Configure PSWA to return this realistic script, VFA mock is separate
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice, pswa_script_content=realistic_pswa_output_for_vfa_test)
        # Now, make VFA fail by raising an exception
        vfa_error_message = "VFA simulated critical EXCEPTION"
        mock_forge_voice.side_effect = Exception(vfa_error_message)

        test_topic = "a_topic_that_fails_at_vfa"
        result = orchestrate_podcast_generation(test_topic)

        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content="mock_content_from_wcha", topic=test_topic)
        mock_forge_voice.assert_called_once_with(script=realistic_pswa_output_for_vfa_test)
        
        self.assertEqual(result.get("status"), "failed")
        self.assertEqual(result.get("error_message"), f"VFA failed: {vfa_error_message}")

        vfa_error_logged = any("VFA: Error during forge_voice" in entry["message"] for entry in result["orchestration_log"])
        self.assertTrue(vfa_error_logged, "VFA error was not logged.")

    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) 
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url') 
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_vfa_skipped_status_propagation(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map_ignored):
        """Test that VFA 'skipped' status propagates correctly."""
        mock_harvest_content.return_value = "mock_content_short_enough_for_pswa"
        
        short_script_output = "[TITLE] Short\n[INTRO] Too short for VFA.\n[OUTRO] End."
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice, pswa_script_content=short_script_output)
        
        mock_vfa_skipped_output = {
            "status": "skipped", "message": "Script too short, VFA skipped.", "audio_url": None,
            "script_char_count": len(short_script_output), "engine_used": "mock_tts_engine_v1_skipped"
        }
        mock_forge_voice.return_value = mock_vfa_skipped_output 
        
        test_topic = "short_script_topic_for_vfa_skip"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content="mock_content_short_enough_for_pswa", topic=test_topic)
        mock_forge_voice.assert_called_once_with(script=short_script_output)

        self.assertEqual(result.get("status"), "completed_with_warnings")
        self.assertEqual(result.get("final_audio_details"), mock_vfa_skipped_output)
        
        vfa_info_logged = any("VFA Info: Voice forging was not fully successful" in entry["message"] for entry in result["orchestration_log"])
        self.assertTrue(vfa_info_logged, "VFA info log for skipped status was not found.")
        
        completion_log_message = result["orchestration_log"][-1]["message"]
        self.assertIn(f"Orchestration finished with status: 'completed_with_warnings' for topic: '{test_topic}'", completion_log_message)

if __name__ == '__main__':
    unittest.main()
