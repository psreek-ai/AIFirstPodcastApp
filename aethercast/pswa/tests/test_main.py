import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json

# Adjust path to import PSWA main module
current_dir = os.path.dirname(os.path.abspath(__file__))
pswa_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(pswa_dir)
project_root_dir = os.path.dirname(aethercast_dir)

sys.path.insert(0, project_root_dir)
sys.path.insert(0, aethercast_dir)

from aethercast.pswa import main as pswa_main
# Import openai for mocking openai.error.OpenAIError if not already imported in main for placeholders
if not hasattr(pswa_main, 'openai') or not hasattr(pswa_main.openai, 'error'):
    # Define placeholder if main module's import failed and didn't set up a dummy
    class OpenAIErrorPlaceholder(Exception): pass
    class DummyOpenAIError: OpenAIError = OpenAIErrorPlaceholder
    if 'openai' not in sys.modules: # if openai module itself is not in sys.modules
        openai = MagicMock() # general MagicMock for openai
        openai.error = DummyOpenAIError()
    else: # if openai is imported but error attribute is missing
        import openai
        if not hasattr(openai, 'error'):
            openai.error = DummyOpenAIError()
        elif not hasattr(openai.error, 'OpenAIError'):
            openai.error.OpenAIError = OpenAIErrorPlaceholder
else:
    import openai


class TestWeaveScriptLogic(unittest.TestCase):

    def setUp(self):
        # Mock configurations - these will be active for each test
        self.mock_pswa_config = {
            "OPENAI_API_KEY": "fake_api_key",
            "PSWA_LLM_MODEL": "gpt-test-model",
            "PSWA_LLM_TEMPERATURE": 0.5,
            "PSWA_LLM_MAX_TOKENS": 1000,
            "PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE": "System message for tests.",
            "PSWA_DEFAULT_PROMPT_USER_TEMPLATE": "Topic: {topic}\nContent: {content}\n\n[TITLE]Default Test Title\n[INTRO]Default Intro\n[SEGMENT_1_TITLE]Seg1 Title\n[SEGMENT_1_CONTENT]Seg1 Content\n[OUTRO]Default Outro"
        }
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.mock_pswa_config)
        self.mock_config = self.config_patcher.start()

        self.imports_patcher = patch.object(pswa_main, 'PSWA_IMPORTS_SUCCESSFUL', True)
        self.mock_imports = self.imports_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.imports_patcher.stop()

    @patch('openai.ChatCompletion.create')
    def test_weave_script_success(self, mock_openai_create):
        mock_llm_response_content = """[TITLE]AI in Education
[INTRO]Welcome to a discussion on how AI is reshaping education.
[SEGMENT_1_TITLE]Personalized Learning
[SEGMENT_1_CONTENT]AI algorithms analyze student performance to offer tailored learning paths. This helps address individual needs effectively.
[OUTRO]AI holds immense potential to revolutionize teaching and learning. Join us next time!"""
        
        mock_openai_create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=mock_llm_response_content))],
            model="gpt-test-model-from-api"
        )

        result = pswa_main.weave_script("Some harvested content", "AI in Education")

        self.assertNotIn("error", result)
        self.assertEqual(result["topic"], "AI in Education")
        self.assertEqual(result["title"], "AI in Education")
        self.assertEqual(result["llm_model_used"], "gpt-test-model-from-api")
        self.assertEqual(result["full_raw_script"], mock_llm_response_content)
        
        self.assertEqual(len(result["segments"]), 3)
        self.assertEqual(result["segments"][0]["segment_title"], "INTRO")
        self.assertEqual(result["segments"][0]["content"], "Welcome to a discussion on how AI is reshaping education.")
        self.assertEqual(result["segments"][1]["segment_title"], "Personalized Learning")
        self.assertEqual(result["segments"][1]["content"], "AI algorithms analyze student performance to offer tailored learning paths. This helps address individual needs effectively.")
        self.assertEqual(result["segments"][2]["segment_title"], "OUTRO")
        self.assertEqual(result["segments"][2]["content"], "AI holds immense potential to revolutionize teaching and learning. Join us next time!")


    @patch('openai.ChatCompletion.create')
    def test_weave_script_openai_api_error(self, mock_openai_create):
        mock_openai_create.side_effect = openai.error.APIError("Test API Error", code=500) # Use the imported/mocked openai.error

        result = pswa_main.weave_script("Content", "Topic")
        self.assertIn("error", result)
        self.assertEqual(result["error"], "PSWA_OPENAI_API_ERROR")
        self.assertIn("Test API Error", result["details"])

    def test_weave_script_missing_api_key(self):
        with patch.dict(pswa_main.pswa_config, {"OPENAI_API_KEY": ""}):
            result = pswa_main.weave_script("Content", "Topic")
            self.assertIn("error", result)
            self.assertEqual(result["error"], "PSWA_CONFIG_ERROR_API_KEY")

    @patch('openai.ChatCompletion.create')
    def test_weave_script_insufficient_content_from_llm(self, mock_openai_create):
        error_message_from_llm = "[ERROR] Insufficient content provided to generate a full podcast script for the topic: Sparse Topic"
        mock_openai_create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=error_message_from_llm))],
            model="gpt-test-model"
        )
        result = pswa_main.weave_script("Too little content", "Sparse Topic")
        
        self.assertNotIn("error", result, "weave_script should parse the LLM's error message, not raise its own error key for insufficient content.")
        self.assertEqual(result["segments"][0]["segment_title"], "ERROR")
        self.assertEqual(result["segments"][0]["content"], error_message_from_llm)
        self.assertEqual(result["full_raw_script"], error_message_from_llm)
        self.assertTrue(result["title"].startswith("Error: Insufficient Content"))


    @patch('openai.ChatCompletion.create')
    def test_script_parsing_variations(self, mock_openai_create):
        # Test case 1: Only Title and Intro
        script_1 = "[TITLE]Minimalist Podcast\n[INTRO]Just an intro here."
        mock_openai_create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=script_1))])
        result_1 = pswa_main.weave_script("content", "topic1")
        self.assertEqual(result_1["title"], "Minimalist Podcast")
        self.assertEqual(len(result_1["segments"]), 1)
        self.assertEqual(result_1["segments"][0]["segment_title"], "INTRO")
        self.assertEqual(result_1["segments"][0]["content"], "Just an intro here.")

        # Test case 2: Missing Title tag, but other tags present
        script_2 = "[INTRO]An intro without a title tag first.\n[OUTRO]And an outro."
        mock_openai_create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=script_2))])
        result_2 = pswa_main.weave_script("content", "topic2")
        self.assertTrue(result_2["title"].startswith("Podcast on topic2"))
        self.assertEqual(len(result_2["segments"]), 2)
        self.assertEqual(result_2["segments"][0]["segment_title"], "INTRO")
        self.assertEqual(result_2["segments"][1]["segment_title"], "OUTRO")

        # Test case 3: Segment title without content tag immediately after
        script_3 = "[TITLE]Segment Anomaly\n[INTRO]Intro here.\n[SEGMENT_1_TITLE]Title for Segment 1\n[SEGMENT_2_TITLE]Another Title, No Content for Seg1"
        mock_openai_create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=script_3))])
        result_3 = pswa_main.weave_script("content", "topic3")
        # The parser should handle the content of [SEGMENT_1_TITLE] as the title for that segment.
        # Since there's no [SEGMENT_1_CONTENT], its content will be empty.
        self.assertEqual(result_3["segments"][1]["segment_title"], "Title for Segment 1")
        self.assertEqual(result_3["segments"][1]["content"], "")
        self.assertEqual(result_3["segments"][2]["segment_title"], "SEGMENT_2_TITLE")
        self.assertEqual(result_3["segments"][2]["content"], "Another Title, No Content for Seg1")


class TestParseLlmScriptOutput(unittest.TestCase):
    # Test the parser directly
    def setUp(self):
        # The parser uses pswa_config for default model, so mock it.
        self.mock_pswa_config = {"PSWA_LLM_MODEL": "parser-test-model"}
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.mock_pswa_config)
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    def test_parse_perfect_script(self):
        raw_script = """[TITLE]Perfect Podcast Title
[INTRO]This is the introduction. It has multiple lines.
Welcome!
[SEGMENT_1_TITLE]First Segment Title
[SEGMENT_1_CONTENT]Content for the first segment.
More content for segment 1.
[SEGMENT_2_TITLE]Second Segment
[SEGMENT_2_CONTENT]Content for the second segment.
[OUTRO]This is the outro.
Thanks for listening!"""
        parsed = pswa_main.parse_llm_script_output(raw_script, "Perfect Topic")
        self.assertEqual(parsed["title"], "Perfect Podcast Title")
        self.assertEqual(len(parsed["segments"]), 4) # Intro, Seg1, Seg2, Outro
        self.assertEqual(parsed["segments"][0]["segment_title"], "INTRO")
        self.assertEqual(parsed["segments"][0]["content"], "This is the introduction. It has multiple lines.\nWelcome!")
        self.assertEqual(parsed["segments"][1]["segment_title"], "First Segment Title")
        self.assertEqual(parsed["segments"][1]["content"], "Content for the first segment.\nMore content for segment 1.")
        self.assertEqual(parsed["segments"][2]["segment_title"], "Second Segment")
        self.assertEqual(parsed["segments"][2]["content"], "Content for the second segment.")
        self.assertEqual(parsed["segments"][3]["segment_title"], "OUTRO")
        self.assertEqual(parsed["segments"][3]["content"], "This is the outro.\nThanks for listening!")

    def test_parse_minimal_script(self):
        raw_script = "[TITLE]Minimal\n[INTRO]Just intro.\n[OUTRO]Just outro."
        parsed = pswa_main.parse_llm_script_output(raw_script, "Minimal Topic")
        self.assertEqual(parsed["title"], "Minimal")
        self.assertEqual(len(parsed["segments"]), 2)
        self.assertEqual(parsed["segments"][0]["segment_title"], "INTRO")
        self.assertEqual(parsed["segments"][0]["content"], "Just intro.")
        self.assertEqual(parsed["segments"][1]["segment_title"], "OUTRO")
        self.assertEqual(parsed["segments"][1]["content"], "Just outro.")

    def test_parse_missing_optional_tags(self):
        raw_script = "[TITLE]No Segments\n[INTRO]Only intro and outro here.\n[OUTRO]Bye."
        parsed = pswa_main.parse_llm_script_output(raw_script, "No Segments Topic")
        self.assertEqual(parsed["title"], "No Segments")
        self.assertEqual(len(parsed["segments"]), 2) # Intro, Outro
        self.assertEqual(parsed["segments"][0]["content"], "Only intro and outro here.")

    def test_parse_extra_whitespace_and_newlines(self):
        raw_script = """  [TITLE]   Spaced Out Title

[INTRO]

  Intro with spaces.

[OUTRO]  Outro also spaced.
"""
        parsed = pswa_main.parse_llm_script_output(raw_script, "Whitespace Topic")
        self.assertEqual(parsed["title"], "Spaced Out Title")
        self.assertEqual(parsed["segments"][0]["segment_title"], "INTRO")
        self.assertEqual(parsed["segments"][0]["content"], "Intro with spaces.")
        self.assertEqual(parsed["segments"][1]["segment_title"], "OUTRO")
        self.assertEqual(parsed["segments"][1]["content"], "Outro also spaced.")
        
    def test_parse_segment_title_no_content(self):
        raw_script = "[TITLE]Seg Title No Content\n[INTRO]Intro.\n[SEGMENT_1_TITLE]Title Only\n[OUTRO]End."
        parsed = pswa_main.parse_llm_script_output(raw_script, "Seg Title No Content")
        self.assertEqual(parsed["segments"][1]["segment_title"], "Title Only") # Content of the [SEGMENT_1_TITLE] tag
        self.assertEqual(parsed["segments"][1]["content"], "") # No matching [SEGMENT_1_CONTENT]

    def test_parse_segment_content_no_title(self):
        # This case is tricky because [SEGMENT_1_CONTENT] without a preceding _TITLE might be ignored or attached to INTRO.
        # Current parser might treat it as a generic segment if it doesn't match _CONTENT for a _TITLE.
        # The current parser logic might put this into a segment with "SEGMENT_1_CONTENT" as title.
        raw_script = "[TITLE]Seg Content No Title\n[INTRO]Intro.\n[SEGMENT_1_CONTENT]Content without specific title.\n[OUTRO]End."
        parsed = pswa_main.parse_llm_script_output(raw_script, "Seg Content No Title")
        found_unmatched_content = False
        for seg in parsed["segments"]:
            if seg["segment_title"] == "SEGMENT_1_CONTENT" and seg["content"] == "Content without specific title.":
                found_unmatched_content = True
                break
        self.assertTrue(found_unmatched_content, "Content for SEGMENT_1_CONTENT without title not found as expected.")


    def test_parse_llm_error_message(self):
        raw_script = "[ERROR] Insufficient content provided to generate a full podcast script for the topic: Error Topic"
        parsed = pswa_main.parse_llm_script_output(raw_script, "Error Topic")
        self.assertTrue(parsed["title"].startswith("Error: Insufficient Content"))
        self.assertEqual(len(parsed["segments"]), 1)
        self.assertEqual(parsed["segments"][0]["segment_title"], "ERROR")
        self.assertEqual(parsed["segments"][0]["content"], raw_script)

    def test_parse_empty_string(self):
        raw_script = ""
        parsed = pswa_main.parse_llm_script_output(raw_script, "Empty Topic")
        self.assertTrue(parsed["title"].startswith("Podcast on Empty Topic")) # Default title
        self.assertEqual(len(parsed["segments"]), 0) # No segments

    def test_parse_no_valid_tags(self):
        raw_script = "This is just a plain sentence without any of our special tags."
        parsed = pswa_main.parse_llm_script_output(raw_script, "No Tags Topic")
        self.assertTrue(parsed["title"].startswith("Podcast on No Tags Topic")) # Default title
        # The current parser might create a segment with the raw text if no tags are found,
        # depending on how it handles untagged content.
        # Let's check if segments list is empty or contains the raw text.
        # Based on current logic, it should be empty because no tags are processed.
        self.assertEqual(len(parsed["segments"]), 0, f"Segments found: {parsed['segments']}")


class TestWeaveScriptEndpoint(unittest.TestCase):

    def setUp(self):
        pswa_main.app.config['TESTING'] = True
        self.client = pswa_main.app.test_client()
        self.mock_pswa_config = {
            "OPENAI_API_KEY": "fake_api_key_for_endpoint",
            "PSWA_LLM_MODEL": "gpt-endpoint-model",
            # Ensure all keys used by weave_script are present if they are accessed via pswa_config.get
            "PSWA_LLM_TEMPERATURE": 0.7,
            "PSWA_LLM_MAX_TOKENS": 1500,
            "PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE": "System msg",
            "PSWA_DEFAULT_PROMPT_USER_TEMPLATE": "User: {topic} - {content}"
        }
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.mock_pswa_config)
        self.mock_config = self.config_patcher.start()

        self.imports_patcher = patch.object(pswa_main, 'PSWA_IMPORTS_SUCCESSFUL', True)
        self.mock_imports = self.imports_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.imports_patcher.stop()

    @patch('aethercast.pswa.main.weave_script')
    def test_handle_weave_script_success(self, mock_weave_script_func):
        mock_structured_script = {
            "script_id": "pswa_script_test123", "topic": "Test Topic",
            "title": "Great Test Podcast", "full_raw_script": "[TITLE]Great Test Podcast...",
            "segments": [{"segment_title": "INTRO", "content": "Intro content"}],
            "llm_model_used": "gpt-endpoint-model"
        }
        mock_weave_script_func.return_value = mock_structured_script
        
        response = self.client.post('/weave_script', json={'content': 'Some content', 'topic': 'Test Topic'})
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        
        self.assertEqual(json_data["script_id"], "pswa_script_test123")
        self.assertEqual(json_data["title"], "Great Test Podcast")

    def test_handle_weave_script_missing_params(self):
        response = self.client.post('/weave_script', json={'content': 'Some content'})
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertIn("Missing required parameters", json_data['error'])

    @patch('aethercast.pswa.main.weave_script')
    def test_handle_weave_script_insufficient_content(self, mock_weave_script_func):
        error_message_from_llm = "[ERROR] Insufficient content provided for topic: Bad Topic"
        # Simulate what parse_llm_script_output would return for this
        mock_weave_script_func.return_value = {
            "script_id": "pswa_script_err", "topic": "Bad Topic",
            "title": "Error: Insufficient Content for Bad Topic",
            "full_raw_script": error_message_from_llm,
            "segments": [{"segment_title": "ERROR", "content": error_message_from_llm}],
            "llm_model_used": "gpt-endpoint-model"
        }
        
        response = self.client.post('/weave_script', json={'content': 'short', 'topic': 'Bad Topic'})
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertTrue(json_data['error'].startswith("[ERROR] Insufficient content"))

    @patch('aethercast.pswa.main.weave_script')
    def test_handle_weave_script_llm_api_error(self, mock_weave_script_func):
        mock_weave_script_func.return_value = {"error": "PSWA_OPENAI_API_ERROR", "details": "OpenAI down"}
        
        response = self.client.post('/weave_script', json={'content': 'content', 'topic': 'topic'})
        self.assertEqual(response.status_code, 500)
        json_data = response.get_json()
        self.assertEqual(json_data["error"], "PSWA_OPENAI_API_ERROR")

    @patch('aethercast.pswa.main.weave_script')
    def test_handle_script_parsing_failure_in_endpoint(self, mock_weave_script_func):
        # Simulate a case where LLM output was fine, but parsing failed to get essential fields
        mock_structured_script_bad_parse = {
            "script_id": "pswa_script_badparse", "topic": "Test Topic Bad Parse",
            "title": None, # Simulate title not being parsed
            "full_raw_script": "Some raw output without clear tags for title or intro",
            "segments": [], # Simulate no segments parsed
            "llm_model_used": "gpt-endpoint-model"
        }
        mock_weave_script_func.return_value = mock_structured_script_bad_parse

        response = self.client.post('/weave_script', json={'content': 'content', 'topic': 'Test Topic Bad Parse'})
        self.assertEqual(response.status_code, 500)
        json_data = response.get_json()
        self.assertEqual(json_data["error"], "PSWA_SCRIPT_PARSING_FAILURE")
        self.assertIn("Failed to parse essential script structure", json_data["message"])


if __name__ == '__main__':
    unittest.main(verbosity=2)
