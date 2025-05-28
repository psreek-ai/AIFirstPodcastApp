import unittest
import sys
import os

# Ensure the 'aethercast' directory (which is one level up from 'pswa')
# is in the Python path for absolute imports.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
pswa_dir = os.path.dirname(current_script_dir) # aethercast/pswa/
aethercast_dir = os.path.dirname(pswa_dir) # aethercast/
project_root_dir = os.path.dirname(aethercast_dir) # repo root

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.pswa.main import weave_script

class TestWeaveScript(unittest.TestCase):

    def test_basic_script_weaving(self):
        """Test basic script weaving with sample content and topic."""
        topic = "The Wonders of AI"
        content = "AI can learn from data. It is used in many fields. Ethical considerations are important."
        
        result = weave_script(content=content, topic=topic)
        
        self.assertIn(f"Exploring: {topic.title()}", result)
        self.assertIn(f"Key Insights on {topic.title()}", result)
        self.assertIn(content, result)
        self.assertIn("[INTRO]", result)
        self.assertIn("[OUTRO]", result)

    def test_empty_content(self):
        """Test script weaving with empty content."""
        topic = "The Void"
        content = ""
        expected_placeholder_content = "We found some interesting information, but it seems the details are currently unavailable. We'll explore this more in a future episode."
        
        result = weave_script(content=content, topic=topic)
        
        self.assertIn(f"Exploring: {topic.title()}", result)
        self.assertIn(expected_placeholder_content, result)
        self.assertNotIn("Based on our information gathering, here's what stands out regarding 'The Void':\n\n", result, "Empty content string should not be directly in output")


    def test_empty_topic(self):
        """Test script weaving with an empty topic."""
        topic = ""
        content = "Some interesting data points."
        # As per pswa.main.py: topic = "an interesting subject" if not topic
        expected_topic_in_script = "an interesting subject"
        
        result = weave_script(content=content, topic=topic)
        
        self.assertIn(f"Exploring: {expected_topic_in_script.title()}", result)
        self.assertIn(f"Key Insights on {expected_topic_in_script.title()}", result)
        self.assertIn(content, result)

    def test_none_topic_and_content(self):
        """Test script weaving with None for both topic and content."""
        expected_topic_in_script = "an interesting subject"
        expected_placeholder_content = "We found some interesting information, but it seems the details are currently unavailable. We'll explore this more in a future episode."

        result = weave_script(content=None, topic=None)

        self.assertIn(f"Exploring: {expected_topic_in_script.title()}", result)
        self.assertIn(expected_placeholder_content, result)


if __name__ == '__main__':
    unittest.main()
