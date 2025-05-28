import unittest
import sys
import os

# Ensure the 'aethercast' directory (which is one level up from 'vfa')
# is in the Python path for absolute imports.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
vfa_dir = os.path.dirname(current_script_dir) # aethercast/vfa/
aethercast_dir = os.path.dirname(vfa_dir) # aethercast/
project_root_dir = os.path.dirname(aethercast_dir) # repo root

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.vfa.main import forge_voice, MIN_SCRIPT_LENGTH_FOR_AUDIO

class TestForgeVoice(unittest.TestCase):

    def test_successful_forging(self):
        """Test successful voice forging with an adequate script."""
        # Create a script longer than MIN_SCRIPT_LENGTH_FOR_AUDIO
        sample_script = "This is a sufficiently long script for testing voice forging. " * int(MIN_SCRIPT_LENGTH_FOR_AUDIO / 10 + 1)
        
        result = forge_voice(script=sample_script)
        
        self.assertEqual(result.get("status"), "success")
        self.assertIn("Mock audio generated successfully", result.get("message", ""))
        self.assertIsInstance(result.get("audio_url"), str)
        self.assertTrue(result.get("audio_url", "").startswith("http://placeholder.aethercast.io/audio/mock_episode_"))
        self.assertTrue(result.get("audio_url", "").endswith(".mp3"))
        self.assertEqual(result.get("script_char_count"), len(sample_script))
        self.assertEqual(result.get("engine_used"), "mock_tts_engine_v1")

    def test_short_script(self):
        """Test voice forging with a script shorter than the minimum requirement."""
        short_script = "Too short." 
        self.assertLess(len(short_script), MIN_SCRIPT_LENGTH_FOR_AUDIO, "Test setup: script should be shorter than min length.")
        
        result = forge_voice(script=short_script)
        
        self.assertEqual(result.get("status"), "skipped")
        self.assertIn("Script too short", result.get("message", ""))
        self.assertIsNone(result.get("audio_url"))
        self.assertEqual(result.get("script_char_count"), len(short_script))

    def test_empty_script(self):
        """Test voice forging with an empty script."""
        empty_script = ""
        
        result = forge_voice(script=empty_script)
        
        self.assertEqual(result.get("status"), "skipped")
        self.assertIn("Script too short", result.get("message", ""))
        self.assertIsNone(result.get("audio_url"))
        self.assertEqual(result.get("script_char_count"), 0)

    def test_script_at_minimum_length(self):
        """Test voice forging with a script exactly at the minimum length."""
        # Create a script that is exactly MIN_SCRIPT_LENGTH_FOR_AUDIO characters long
        min_length_script = "a" * MIN_SCRIPT_LENGTH_FOR_AUDIO
        self.assertEqual(len(min_length_script), MIN_SCRIPT_LENGTH_FOR_AUDIO, "Test setup: script should be exactly min length.")

        result = forge_voice(script=min_length_script)

        self.assertEqual(result.get("status"), "success")
        self.assertIsInstance(result.get("audio_url"), str)
        self.assertTrue(result.get("audio_url", "").startswith("http://placeholder.aethercast.io/audio/mock_episode_"))

    def test_script_just_below_minimum_length(self):
        """Test voice forging with a script just one character below minimum length."""
        if MIN_SCRIPT_LENGTH_FOR_AUDIO > 0:
            just_below_min_script = "a" * (MIN_SCRIPT_LENGTH_FOR_AUDIO - 1)
            
            result = forge_voice(script=just_below_min_script)
            
            self.assertEqual(result.get("status"), "skipped")
            self.assertIsNone(result.get("audio_url"))
        else: # If MIN_SCRIPT_LENGTH_FOR_AUDIO is 0, this test is not applicable
            self.skipTest("MIN_SCRIPT_LENGTH_FOR_AUDIO is 0, so 'just_below_minimum' test is not applicable.")


if __name__ == '__main__':
    unittest.main()
