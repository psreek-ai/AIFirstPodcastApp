import unittest
import sys
import os

# Ensure the 'aethercast' directory (which is one level up from 'wcha')
# is in the Python path for absolute imports.
# This is primarily for running tests directly from this file or if the test runner
# doesn't automatically add the project root.
# `python -m unittest discover` from the root should handle this automatically.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
wcha_dir = os.path.dirname(current_script_dir) # aethercast/wcha/
aethercast_dir = os.path.dirname(wcha_dir) # aethercast/
project_root_dir = os.path.dirname(aethercast_dir) # repo root

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.wcha.main import harvest_content, SIMULATED_WEB_CONTENT

class TestHarvestContent(unittest.TestCase):

    def test_known_topic(self):
        """Test harvesting content for a known topic."""
        topic = "ai in healthcare"
        expected_content = SIMULATED_WEB_CONTENT.get(topic)
        self.assertIsNotNone(expected_content, "Test setup error: Known topic not found in SIMULATED_WEB_CONTENT.")
        
        result = harvest_content(topic)
        self.assertEqual(result, expected_content)

    def test_unknown_topic(self):
        """Test harvesting content for an unknown topic."""
        topic = "underwater basket weaving techniques"
        expected_message = f"No pre-defined content found for topic: {topic}"
        result = harvest_content(topic)
        self.assertEqual(result, expected_message)

    def test_case_insensitivity_and_spacing(self):
        """Test that topic matching is case-insensitive and handles extra spacing."""
        topic_variations = [
            "Ai In Healthcare",
            " ai in healthcare ",
            "AI IN HEALTHCARE"
        ]
        # Assuming "ai in healthcare" is a key in SIMULATED_WEB_CONTENT
        # and the function normalizes to this key.
        expected_content = SIMULATED_WEB_CONTENT.get("ai in healthcare")
        self.assertIsNotNone(expected_content, "Test setup error: Base topic 'ai in healthcare' not found for sensitivity test.")

        for topic_variation in topic_variations:
            with self.subTest(topic_variation=topic_variation):
                result = harvest_content(topic_variation)
                self.assertEqual(result, expected_content)
    
    def test_empty_topic(self):
        """Test harvesting content with an empty topic string."""
        topic = ""
        # The current implementation of harvest_content normalizes "" to ""
        # and then checks if "" is in SIMULATED_WEB_CONTENT.
        # If "" is not a key, it returns "No pre-defined content found for topic: ".
        # Let's assume "" is not a valid key.
        expected_message = "No pre-defined content found for topic: "
        if "" in SIMULATED_WEB_CONTENT: # If for some reason "" becomes a valid topic
             expected_message = SIMULATED_WEB_CONTENT[""]

        result = harvest_content(topic)
        self.assertEqual(result, expected_message)

    def test_none_topic(self):
        """Test harvesting content with topic as None."""
        # The current implementation of harvest_content has:
        # normalized_topic = topic.lower().strip() if topic else ""
        # So, None becomes "", and then it behaves like an empty topic string.
        expected_message = "No pre-defined content found for topic: "
        if "" in SIMULATED_WEB_CONTENT:
             expected_message = SIMULATED_WEB_CONTENT[""]
        result = harvest_content(None)
        self.assertEqual(result, expected_message)

if __name__ == '__main__':
    unittest.main()
