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

from aethercast.cpoa.main import orchestrate_podcast_generation

class TestOrchestrationFlow(unittest.TestCase):

    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_successful_orchestration(self, mock_harvest_content, mock_weave_script, mock_forge_voice):
        """Test a successful podcast generation workflow."""
        
        # Configure mock return values
        mock_harvest_content.return_value = "mock_content_from_wcha"
        mock_weave_script.return_value = "mock_script_from_pswa"
        mock_vfa_output = {
            "status": "success",
            "message": "Mock audio generated successfully.",
            "audio_url": "http://placeholder.aethercast.io/audio/mock_cpoa_test.mp3",
            "script_char_count": len("mock_script_from_pswa"),
            "engine_used": "mock_tts_engine_v1_for_cpoa_test"
        }
        mock_forge_voice.return_value = mock_vfa_output
        
        test_topic = "a_successful_topic"
        result = orchestrate_podcast_generation(test_topic)
        
        # Assert that agent functions were called correctly
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        mock_weave_script.assert_called_once_with(content="mock_content_from_wcha", topic=test_topic)
        mock_forge_voice.assert_called_once_with(script="mock_script_from_pswa")
        
        # Assert the structure and content of the returned dictionary
        self.assertEqual(result.get("topic"), test_topic)
        self.assertEqual(result.get("status"), "completed")
        self.assertEqual(result.get("final_audio_details"), mock_vfa_output)
        
        # Check orchestration log basics
        self.assertIsInstance(result.get("orchestration_log"), list)
        self.assertTrue(len(result.get("orchestration_log")) > 3) # Expect several log entries
        
        # Check that the last log message indicates completion
        last_log_message = result["orchestration_log"][-1]["message"]
        self.assertIn(f"Orchestration completed for topic: '{test_topic}'", last_log_message)


    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_failure_in_wcha(self, mock_harvest_content, mock_weave_script, mock_forge_voice):
        """Test workflow failure when WCHA raises an exception."""
        
        # Configure WCHA mock to raise an exception
        wcha_error_message = "WCHA simulated critical failure"
        mock_harvest_content.side_effect = Exception(wcha_error_message)
        
        test_topic = "a_topic_that_fails_at_wcha"
        result = orchestrate_podcast_generation(test_topic)
        
        # Assert WCHA was called
        mock_harvest_content.assert_called_once_with(topic=test_topic)
        
        # Assert PSWA and VFA were NOT called
        mock_weave_script.assert_not_called()
        mock_forge_voice.assert_not_called()
        
        # Assert the overall status and error message
        self.assertEqual(result.get("topic"), test_topic)
        self.assertEqual(result.get("status"), "failed")
        self.assertIsNone(result.get("final_audio_details"))
        self.assertEqual(result.get("error_message"), f"WCHA failed: {wcha_error_message}")
        
        # Check orchestration log for error
        self.assertIsInstance(result.get("orchestration_log"), list)
        wcha_error_logged = False
        for log_entry in result["orchestration_log"]:
            if "WCHA: Error during harvest_content" in log_entry["message"]:
                wcha_error_logged = True
                # Check if the data field in the log contains the error type
                log_data = log_entry.get("data")
                if isinstance(log_data, str): # if data is stringified JSON
                    try: log_data = json.loads(log_data)
                    except: pass # keep as string if not json
                if isinstance(log_data, dict):
                    self.assertEqual(log_data.get("error_type"), "Exception")
                break
        self.assertTrue(wcha_error_logged, "WCHA error was not logged in orchestration_log.")

    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_failure_in_pswa(self, mock_harvest_content, mock_weave_script, mock_forge_voice):
        """Test workflow failure when PSWA raises an exception."""
        mock_harvest_content.return_value = "mock_content_from_wcha"
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

    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_orchestration_failure_in_vfa(self, mock_harvest_content, mock_weave_script, mock_forge_voice):
        """Test workflow failure when VFA raises an exception."""
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

    @mock.patch('aethercast.vfa.main.forge_voice')
    @mock.patch('aethercast.pswa.main.weave_script')
    @mock.patch('aethercast.wcha.main.harvest_content')
    def test_vfa_skipped_status_propagation(self, mock_harvest_content, mock_weave_script, mock_forge_voice):
        """Test that VFA 'skipped' status propagates correctly."""
        mock_harvest_content.return_value = "mock_content_short"
        mock_weave_script.return_value = "mock_script_short" # Assume this script would cause VFA to skip
        
        mock_vfa_skipped_output = {
            "status": "skipped",
            "message": "Script too short, mock audio generation skipped.",
            "audio_url": None,
            "script_char_count": len("mock_script_short"),
            "engine_used": "mock_tts_engine_v1"
        }
        mock_forge_voice.return_value = mock_vfa_skipped_output
        
        test_topic = "short_script_topic"
        result = orchestrate_podcast_generation(test_topic)
        
        self.assertEqual(result.get("status"), "completed_with_warnings")
        self.assertEqual(result.get("final_audio_details"), mock_vfa_skipped_output)
        
        vfa_warning_logged = any("VFA Warning: Voice forging may have failed or been skipped" in entry["message"] for entry in result["orchestration_log"])
        self.assertTrue(vfa_warning_logged, "VFA warning for skipped status was not logged.")
        
        completion_log_message = result["orchestration_log"][-1]["message"]
        self.assertIn(f"Orchestration completed_with_warnings for topic: '{test_topic}'", completion_log_message)

if __name__ == '__main__':
    unittest.main()
