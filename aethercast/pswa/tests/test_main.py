import unittest
from unittest import mock
from unittest.mock import patch, Mock # Specific imports for clarity
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

# Import openai for referencing openai.error
# The actual openai module will be mocked where its methods are called.
try:
    import openai
except ImportError:
    # Create a placeholder if openai is not installed, to allow tests to define/catch openai.error.APIError
    class OpenAIErrorPlaceholder(Exception): pass
    class DummyOpenAI:
        error = type('error', (object,), {'APIError': OpenAIErrorPlaceholder, 'OpenAIError': OpenAIErrorPlaceholder})() # Add OpenAIError too
    openai = DummyOpenAI()


from aethercast.pswa.main import weave_script

class TestWeaveScript(unittest.TestCase):

    def setUp(self):
        """Set up a fake API key for tests that expect one."""
        self.patcher = patch.dict(os.environ, {"OPENAI_API_KEY": "fake_test_key"})
        self.patcher.start()

    def tearDown(self):
        """Clean up the patched environment variables."""
        self.patcher.stop()

    @patch('aethercast.pswa.main.openai.ChatCompletion.create')
    def test_successful_script_generation(self, mock_create_method):
        """Test successful script generation with mocked LLM call."""
        mock_response = Mock()
        # Ensure choices is a list of Mocks, and each Mock has a message attribute
        mock_choice = Mock()
        mock_choice.message = {'content': "This is a mocked LLM script."}
        mock_response.choices = [mock_choice]
        mock_create_method.return_value = mock_response
        
        input_content = "Some input content"
        input_topic = "A great topic"
        result = weave_script(input_content, input_topic)
        
        mock_create_method.assert_called_once()
        call_args = mock_create_method.call_args
        self.assertEqual(call_args.kwargs['model'], "gpt-3.5-turbo")
        
        # Check messages structure and content
        messages = call_args.kwargs['messages']
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['role'], "system")
        self.assertEqual(messages[1]['role'], "user")
        self.assertIn(input_content, messages[1]['content'])
        self.assertIn(input_topic, messages[1]['content'])
        
        self.assertEqual(result, "This is a mocked LLM script.")

    @patch.dict(os.environ, {}, clear=True) # Clears all env vars, including one from setUp
    def test_missing_openai_api_key(self):
        """Test behavior when OPENAI_API_KEY is not set."""
        # Stop the class-level patcher if it's running, or ensure this test runs isolated for env vars
        # self.patcher.stop() # This would require self.patcher.start() in finally or a more complex setup
        # The clear=True in patch.dict should handle this by overriding.
        
        result = weave_script("content", "topic")
        self.assertEqual(result, "Error: OPENAI_API_KEY environment variable is not set or empty.")

    @patch('aethercast.pswa.main.openai.ChatCompletion.create')
    def test_openai_api_error_handling(self, mock_create_method):
        """Test handling of OpenAI API errors."""
        # Ensure openai.error.APIError exists for the test, even if openai failed to import fully
        # This is usually handled by the try-except import block in pswa.main
        # For tests, ensure we reference something valid.
        if not hasattr(openai.error, 'APIError'): # If placeholder was used
             openai.error.APIError = type('APIError', (openai.error.OpenAIError,), {})

        mock_create_method.side_effect = openai.error.APIError(
            "Simulated OpenAI API Error", http_status=500, request_id="test_req_id"
        )
        
        result = weave_script("content", "topic")
        self.assertTrue(result.startswith("OpenAI API Error: APIError -"), f"Unexpected result: {result}")
        self.assertIn("Simulated OpenAI API Error", result)

    @patch('aethercast.pswa.main.openai.ChatCompletion.create')
    def test_unexpected_error_during_llm_call(self, mock_create_method):
        """Test handling of unexpected errors like ConnectionRefusedError."""
        mock_create_method.side_effect = ConnectionRefusedError("Simulated connection refused")
        
        result = weave_script("content", "topic")
        self.assertTrue(result.startswith("An unexpected error occurred during LLM call: ConnectionRefusedError -"), f"Unexpected result: {result}")
        self.assertIn("Simulated connection refused", result)

    @patch('aethercast.pswa.main.PSWA_IMPORTS_SUCCESSFUL', False)
    @patch('aethercast.pswa.main.PSWA_MISSING_IMPORT_ERROR', "Test: openai library is missing")
    def test_missing_openai_library(self, mock_imports_flag, mock_import_error_msg): # Mocks passed by decorator order
        """Test behavior when the OpenAI library is indicated as not imported."""
        # No need to mock ChatCompletion.create as the function should exit early.
        result = weave_script("content", "topic")
        self.assertEqual(result, "OpenAI library not available. Test: openai library is missing")

    @patch('aethercast.pswa.main.openai.ChatCompletion.create')
    def test_llm_returns_insufficient_content_error(self, mock_create_method):
        """Test PSWA returns the LLM's own error message for insufficient content."""
        mock_response = Mock()
        mock_choice = Mock()
        error_message_from_llm = "[ERROR] Insufficient content provided to generate a full podcast script for the topic: test topic"
        mock_choice.message = {'content': error_message_from_llm}
        mock_response.choices = [mock_choice]
        mock_create_method.return_value = mock_response
        
        result = weave_script("very little", "test topic")
        self.assertEqual(result, error_message_from_llm)


if __name__ == '__main__':
    unittest.main()
