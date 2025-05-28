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

    # Common mock setup for VFA and PSWA as their behavior isn't the primary focus of these WCHA interaction tests
    def _configure_downstream_mocks(self, mock_pswa, mock_vfa):
        mock_pswa.return_value = "mock_script_from_pswa"
        mock_vfa.return_value = {
            "status": "success", "message": "Mock audio generated.", 
            "audio_url": "http://mock.url/audio.mp3", "script_char_count": len("mock_script_from_pswa"),
            "engine_used": "mock_tts_engine_cpoa_test"
        }

    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"ai in healthcare": "http://example.com/ai_health"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_mapped_topic_live_fetch_success(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test mapped topic with successful live fetch."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice)
        live_content = "live content for AI in healthcare"
        mock_harvest_from_url.return_value = live_content
        
        test_topic = "ai in healthcare"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_called_once_with("http://example.com/ai_health")
        mock_harvest_content.assert_not_called()
        mock_weave_script.assert_called_once_with(content=live_content, topic=test_topic)
        self.assertEqual(result.get("status"), "completed")
        self.assertIn("Successfully harvested content from URL", result["orchestration_log"][-4]["message"]) # Check log

    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"ai in healthcare": "http://example.com/ai_health_fail"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_mapped_topic_live_fetch_fails_fallback_succeeds(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test mapped topic, live fetch fails, fallback to mock succeeds."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice)
        mock_harvest_from_url.return_value = "Error fetching URL..." # Simulate WCHA returning an error string
        fallback_content = "mock fallback for AI in healthcare"
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
    def test_orchestration_mapped_topic_live_fetch_fails_fallback_fails(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test mapped topic, live fetch fails, fallback to mock also returns 'not found'."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice)
        mock_harvest_from_url.return_value = "Error fetching URL..."
        fallback_not_found_message = "No pre-defined content found for topic: climate change"
        mock_harvest_content.return_value = fallback_not_found_message
        
        test_topic = "climate change" # This topic will be used for fallback
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_called_once_with("http://example.com/climate_fail")
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content=fallback_not_found_message, topic=test_topic)
        # The overall status is still "completed" because PSWA handles "no content" by creating a generic script.
        # VFA might skip if the script is too short, leading to "completed_with_warnings".
        # Let's check if VFA was called with the "no content" script.
        # If VFA skipped, the status would be "completed_with_warnings"
        if mock_forge_voice.return_value.get("status") == "skipped":
            self.assertEqual(result.get("status"), "completed_with_warnings")
        else:
            self.assertEqual(result.get("status"), "completed") # Default if VFA somehow succeeds with generic script
        self.assertIn("Falling back to mock harvest", result["orchestration_log"][-5]["message"])
        self.assertIn("Final content is a 'not found' message", result["orchestration_log"][-4]["message"])


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) # Empty map for this test
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_unmapped_topic_uses_mock_success(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test unmapped topic, uses mock data successfully."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice)
        mock_content_str = "mock content for quantum computing"
        mock_harvest_content.return_value = mock_content_str
        
        test_topic = "quantum computing"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_not_called()
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content=mock_content_str, topic=test_topic)
        self.assertEqual(result.get("status"), "completed")
        self.assertIn("Using mock harvest for topic", result["orchestration_log"][-4]["message"])


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) # Empty map
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_unmapped_topic_uses_mock_not_found(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test unmapped topic, mock data also returns 'not found'."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice)
        not_found_message = "No pre-defined content found for topic: underwater basket weaving"
        mock_harvest_content.return_value = not_found_message
        
        test_topic = "underwater basket weaving"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_not_called()
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content=not_found_message, topic=test_topic)
        if mock_forge_voice.return_value.get("status") == "skipped":
            self.assertEqual(result.get("status"), "completed_with_warnings")
        else:
            self.assertEqual(result.get("status"), "completed")
        self.assertIn("Final content is a 'not found' message", result["orchestration_log"][-4]["message"])


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {"mapped_topic_wcha_exception": "http://example.com/mapped_topic_wcha_exception"})
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_wcha_harvest_from_url_raises_exception(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test WCHA harvest_from_url raises an unexpected exception. CPOA should catch it and fail."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice)
        wcha_error_message = "WCHA harvest_from_url_exception"
        mock_harvest_from_url.side_effect = Exception(wcha_error_message)
        
        test_topic = "mapped_topic_wcha_exception"
        result = orchestrate_podcast_generation(test_topic)
        
        mock_harvest_from_url.assert_called_once_with("http://example.com/mapped_topic_wcha_exception")
        mock_harvest_content.assert_not_called() # Fallback is skipped due to exception
        mock_weave_script.assert_not_called()
        mock_forge_voice.assert_not_called()
        
        self.assertEqual(result.get("status"), "failed")
        self.assertIn(f"WCHA failed critically: {wcha_error_message}", result.get("error_message", ""))
        self.assertTrue(any("WCHA: Critical error during content harvesting" in entry["message"] for entry in result["orchestration_log"]))


    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) # Unmapped topic
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_wcha_harvest_content_raises_exception(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test WCHA harvest_content (mock path) raises an unexpected exception. CPOA should catch and fail."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice)
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


    # Keep existing tests for PSWA and VFA failures as they are still relevant
    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) # Assuming unmapped for simplicity
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url') # Added harvest_from_url
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_failure_in_pswa(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test workflow failure when PSWA raises an exception."""
        self._configure_downstream_mocks(mock_weave_script, mock_forge_voice) # mock_forge_voice part won't be used
        mock_harvest_content.return_value = "mock_content_from_wcha" # PSWA needs this
        
        pswa_error_message = "PSWA simulated critical failure"
        mock_weave_script.side_effect = Exception(pswa_error_message)

        test_topic = "a_topic_that_fails_at_pswa"
        result = orchestrate_podcast_generation(test_topic)

        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content="mock_content_from_wcha", topic=test_topic)
        mock_forge_voice.assert_not_called()

        self.assertEqual(result.get("status"), "failed")
        self.assertEqual(result.get("error_message"), f"PSWA failed: {pswa_error_message}")
        
        pswa_error_logged = any("PSWA: Error during weave_script" in entry["message"] for entry in result["orchestration_log"])
        self.assertTrue(pswa_error_logged, "PSWA error was not logged.")

    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) # Assuming unmapped
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url') # Added harvest_from_url
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_failure_in_vfa(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test workflow failure when VFA raises an exception."""
        # No need for _configure_downstream_mocks for VFA as it's the one failing
        mock_harvest_content.return_value = "mock_content_from_wcha"
        mock_weave_script.return_value = "mock_script_from_pswa"
        
        vfa_error_message = "VFA simulated critical failure"
        mock_forge_voice.side_effect = Exception(vfa_error_message)

        test_topic = "a_topic_that_fails_at_vfa"
        result = orchestrate_podcast_generation(test_topic)

        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content="mock_content_from_wcha", topic=test_topic)
        mock_forge_voice.assert_called_once_with(script="mock_script_from_pswa")
        
        self.assertEqual(result.get("status"), "failed")
        self.assertEqual(result.get("error_message"), f"VFA failed: {vfa_error_message}")

        vfa_error_logged = any("VFA: Error during forge_voice" in entry["message"] for entry in result["orchestration_log"])
        self.assertTrue(vfa_error_logged, "VFA error was not logged.")

    @mock.patch('aethercast.cpoa.main.TOPIC_TO_URL_MAP', {}) # Assuming unmapped
    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_from_url') # Added harvest_from_url
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_vfa_skipped_status_propagation(self, mock_harvest_content, mock_harvest_from_url, mock_weave_script, mock_forge_voice, mock_topic_map):
        """Test that VFA 'skipped' status propagates correctly."""
        mock_harvest_content.return_value = "mock_content_short"
        mock_weave_script.return_value = "mock_script_short" 
        
        mock_vfa_skipped_output = {
            "status": "skipped", "message": "Script too short", "audio_url": None,
            "script_char_count": len("mock_script_short"), "engine_used": "mock_tts_engine_v1"
        }
        mock_forge_voice.return_value = mock_vfa_skipped_output
        
        test_topic = "short_script_topic"
        result = orchestrate_podcast_generation(test_topic)
        
        self.assertEqual(result.get("status"), "completed_with_warnings")
        self.assertEqual(result.get("final_audio_details"), mock_vfa_skipped_output)
        
        vfa_warning_logged = any("VFA Info: Voice forging was not fully successful" in entry["message"] for entry in result["orchestration_log"])
        self.assertTrue(vfa_warning_logged, "VFA warning for skipped status was not logged correctly.")
        
        completion_log_message = result["orchestration_log"][-1]["message"]
        self.assertIn(f"Orchestration finished with status: 'completed_with_warnings' for topic: '{test_topic}'", completion_log_message)

if __name__ == '__main__':
    unittest.main()
