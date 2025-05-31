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
    if 'openai' not in sys.modules:
        openai = MagicMock()
        openai.error = DummyOpenAIError()
    else:
        import openai
        if not hasattr(openai, 'error'):
            openai.error = DummyOpenAIError()
        elif not hasattr(openai.error, 'OpenAIError'):
            openai.error.OpenAIError = OpenAIErrorPlaceholder
else:
    import openai

# For mocking datetime
from datetime import datetime, timedelta


class TestCalculateContentHash(unittest.TestCase):
    def test_hash_consistency(self):
        hash1 = pswa_main._calculate_content_hash("Topic A", "Content for topic A, first 1000 chars.")
        hash2 = pswa_main._calculate_content_hash("Topic A", "Content for topic A, first 1000 chars.")
        self.assertEqual(hash1, hash2)

    def test_hash_case_insensitivity(self):
        hash1 = pswa_main._calculate_content_hash("Topic B", "Some Content.")
        hash2 = pswa_main._calculate_content_hash("topic b", "some content.")
        self.assertEqual(hash1, hash2)

    def test_hash_content_truncation(self):
        base_content = "c" * 1000
        extended_content = base_content + "extra content that should not affect hash"
        hash1 = pswa_main._calculate_content_hash("Topic C", base_content)
        hash2 = pswa_main._calculate_content_hash("Topic C", extended_content)
        self.assertEqual(hash1, hash2)

        # Ensure that if the first 1000 chars change, the hash changes
        different_base_content = "d" * 1000
        hash3 = pswa_main._calculate_content_hash("Topic C", different_base_content)
        self.assertNotEqual(hash1, hash3)

    def test_hash_topic_sensitivity(self):
        hash1 = pswa_main._calculate_content_hash("Topic D1", "Common Content")
        hash2 = pswa_main._calculate_content_hash("Topic D2", "Common Content")
        self.assertNotEqual(hash1, hash2)


class TestWeaveScriptLogic(unittest.TestCase):

    def setUp(self):
        self.maxDiff = None # Show full diff on assertion failure
        # Mock configurations - these will be active for each test
        self.mock_pswa_config_defaults = {
            "OPENAI_API_KEY": "fake_api_key",
            "PSWA_LLM_MODEL": "gpt-3.5-turbo-1106",
            "PSWA_LLM_TEMPERATURE": 0.5,
            "PSWA_LLM_MAX_TOKENS": 1000,
            "PSWA_LLM_JSON_MODE": True,
            "PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE": "System prompt for JSON",
            "PSWA_DEFAULT_PROMPT_USER_TEMPLATE": "User prompt for JSON: {topic} - {content}",
            "PSWA_DATABASE_PATH": ":memory:", # Use in-memory DB for caching tests by default
            "PSWA_SCRIPT_CACHE_ENABLED": True,
            "PSWA_SCRIPT_CACHE_MAX_AGE_HOURS": 720 # 30 days
        }

        # Use a copy for modification in tests to avoid altering the class default dict
        self.current_test_config = self.mock_pswa_config_defaults.copy()

        # Patch pswa_main.pswa_config directly. This is simpler if pswa_config is a global dict.
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.current_test_config)
        self.mock_config = self.config_patcher.start()

        self.imports_patcher = patch.object(pswa_main, 'PSWA_IMPORTS_SUCCESSFUL', True)
        self.mock_imports = self.imports_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.imports_patcher.stop()

    @patch('openai.ChatCompletion.create')
    def test_weave_script_success_json_mode(self, mock_openai_create):
        # Simulate LLM returning a valid JSON string
        llm_output_json_str = json.dumps({
            "title": "AI in Education (JSON)",
            "intro": "Welcome to a JSON discussion on AI in education.",
            "segments": [
                {"segment_title": "Personalized Learning (JSON)", "content": "AI offers tailored JSON learning paths."},
                {"segment_title": "Future Trends (JSON)", "content": "JSON-based AI tutors are emerging."}
            ],
            "outro": "JSON AI will reshape learning. Thanks!"
        })
        
        mock_openai_create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=llm_output_json_str))],
            model="gpt-3.5-turbo-1106-from-api"
        )

        result = pswa_main.weave_script("Some harvested content", "AI in Education via JSON")

        mock_openai_create.assert_called_once()
        call_kwargs = mock_openai_create.call_args.kwargs
        self.assertEqual(call_kwargs.get("response_format"), {"type": "json_object"})


        self.assertNotIn("error", result)
        self.assertEqual(result["topic"], "AI in Education via JSON")
        self.assertEqual(result["title"], "AI in Education (JSON)")
        self.assertEqual(result["llm_model_used"], "gpt-3.5-turbo-1106-from-api")
        self.assertEqual(result["full_raw_script"], llm_output_json_str)
        self.assertEqual(result.get("source"), "generation") # Should indicate it was generated

        self.assertEqual(len(result["segments"]), 4)
        self.assertEqual(result["segments"][0]["segment_title"], "INTRO")
        self.assertEqual(result["segments"][0]["content"], "Welcome to a JSON discussion on AI in education.")
        self.assertEqual(result["segments"][1]["segment_title"], "Personalized Learning (JSON)")
        self.assertEqual(result["segments"][1]["content"], "AI offers tailored JSON learning paths.")
        self.assertEqual(result["segments"][3]["segment_title"], "OUTRO")


    @patch('openai.ChatCompletion.create')
    def test_weave_script_success_fallback_parsing(self, mock_openai_create):
        # Test fallback to tag-based parsing if JSON mode is off or LLM fails to produce JSON
        with patch.dict(pswa_main.pswa_config, {"PSWA_LLM_JSON_MODE": False}):
            mock_llm_response_content = """[TITLE]AI in Education (Tag Fallback)
[INTRO]Tag-based intro.
[SEGMENT_1_TITLE]Segment One (Tag)
[SEGMENT_1_CONTENT]Content for segment one via tags.
[OUTRO]Tag-based outro."""

            mock_openai_create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content=mock_llm_response_content))],
                model="gpt-test-model-fallback"
            )

            result = pswa_main.weave_script("Some content", "AI Education Fallback")

            mock_openai_create.assert_called_once()
            call_kwargs = mock_openai_create.call_args.kwargs
            self.assertNotIn("response_format", call_kwargs)

            self.assertNotIn("error", result)
            self.assertEqual(result["title"], "AI in Education (Tag Fallback)")
            self.assertEqual(result.get("source"), "generation")
            self.assertEqual(len(result["segments"]), 3)
            self.assertEqual(result["segments"][0]["content"], "Tag-based intro.")
            self.assertEqual(result["segments"][1]["segment_title"], "Segment One (Tag)")
            self.assertEqual(result["segments"][2]["segment_title"], "OUTRO")

    @patch('openai.ChatCompletion.create')
    def test_weave_script_invalid_json_fallback_to_tags(self, mock_openai_create):
        # LLM returns non-JSON string even when JSON mode might have been requested
        invalid_json_but_valid_tags = "[TITLE]Title from Tags\n[INTRO]Intro from Tags\nThis is not JSON."
        mock_openai_create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=invalid_json_but_valid_tags))],
            model="gpt-test-model-invalid-json"
        )

        # Keep PSWA_LLM_JSON_MODE True to simulate LLM ignoring the format request
        with patch.object(pswa_main.logger, 'warning') as mock_logger_warning:
            result = pswa_main.weave_script("Content", "Invalid JSON Test")

            self.assertNotIn("error", result)
            self.assertEqual(result["title"], "Title from Tags")
            self.assertEqual(result["segments"][0]["content"], "Intro from Tags")
            self.assertEqual(result.get("source"), "generation") # Fallback is still a form of generation
            self.assertTrue(any("LLM output was not valid JSON" in call_args[0][0] for call_args in mock_logger_warning.call_args_list))

    @patch('openai.ChatCompletion.create')
    def test_weave_script_critical_failure_unparsable_output_json_mode(self, mock_openai_create):
        unparsable_gibberish = "This is complete gibberish, not JSON, and not tags."
        mock_openai_create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=unparsable_gibberish))],
            model="gpt-test-model-gibberish"
        )

        self.assertTrue(pswa_main.pswa_config["PSWA_LLM_JSON_MODE"]) # Ensure JSON mode is on for this test variant

        with patch.object(pswa_main.logger, 'error') as mock_logger_error:
            result = pswa_main.weave_script("Content", "Unparsable Test")

            self.assertIn("error", result)
            self.assertEqual(result["error"], "PSWA_SCRIPT_PARSING_FAILURE")
            self.assertEqual(result.get("source"), "error")
            self.assertIn("Failed to parse LLM output as JSON and also failed tag-based fallback", result["details"])
            self.assertTrue(any("Failed to parse LLM output as JSON" in call_args[0][0] for call_args in mock_logger_error.call_args_list))

    @patch('openai.ChatCompletion.create')
    def test_weave_script_json_insufficient_content(self, mock_openai_create):
        llm_error_json_str = json.dumps({
            "error": "Insufficient content",
            "message": "The provided content was not sufficient for topic: Too Brief"
        })
        mock_openai_create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=llm_error_json_str))],
            model="gpt-test-model"
        )
        result = pswa_main.weave_script("Too little", "Too Brief")
        self.assertEqual(result["segments"][0]["segment_title"], "ERROR")
        self.assertIn("not sufficient for topic: Too Brief", result["segments"][0]["content"])
        self.assertTrue(result["title"].startswith("Error: Insufficient Content"))
        self.assertEqual(result.get("source"), "generation") # LLM generated an error message, counts as generation

    @patch('openai.ChatCompletion.create')
    def test_weave_script_success_no_cache_involvement(self, mock_openai_create):
        # This test is similar to test_weave_script_success_json_mode but explicitly for when cache is not hit
        # and to ensure 'source: generation' is added.
        llm_output_json_str = json.dumps({
            "title": "AI in Education",
            "intro": "Welcome to a discussion on how AI is reshaping education.",
            "segments": [{"segment_title": "Personalized Learning", "content": "AI algorithms analyze."}],
            "outro": "Join us next time!"
        })
        mock_openai_create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=llm_output_json_str))],
            model="gpt-test-model-from-api"
        )
        # Ensure caching is enabled for this test to check save path, but mock _get_cached_script to return None
        pswa_main.pswa_config['PSWA_SCRIPT_CACHE_ENABLED'] = True
        with patch.object(pswa_main, '_get_cached_script', return_value=None) as mock_get_cache, \
             patch.object(pswa_main, '_save_script_to_cache') as mock_save_cache:

            result = pswa_main.weave_script("Some harvested content", "AI in Education")

            self.assertNotIn("error", result)
            self.assertEqual(result["topic"], "AI in Education")
            self.assertEqual(result["title"], "AI in Education")
            self.assertEqual(result["llm_model_used"], "gpt-test-model-from-api")
            self.assertEqual(result["full_raw_script"], llm_output_json_str)
            self.assertEqual(result.get("source"), "generation")
            mock_get_cache.assert_called_once()
            mock_save_cache.assert_called_once()
        
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
    def test_weave_script_insufficient_content_from_llm_tag_mode(self, mock_openai_create):
        # Test the tag-based insufficient content error when JSON mode is off
        with patch.dict(pswa_main.pswa_config, {"PSWA_LLM_JSON_MODE": False}):
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
    def test_script_parsing_variations_tag_mode(self, mock_openai_create):
        # Test tag parsing variations specifically when JSON mode is off
        with patch.dict(pswa_main.pswa_config, {"PSWA_LLM_JSON_MODE": False}):
            # Test case 1: Only Title and Intro
            script_1 = "[TITLE]Minimalist Podcast (Tag)\n[INTRO]Just an intro here (Tag)."
            mock_openai_create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=script_1))])
            result_1 = pswa_main.weave_script("content", "topic1_tag")
            self.assertEqual(result_1["title"], "Minimalist Podcast (Tag)")
            self.assertEqual(len(result_1["segments"]), 1)
            self.assertEqual(result_1["segments"][0]["segment_title"], "INTRO")
            self.assertEqual(result_1["segments"][0]["content"], "Just an intro here (Tag).")

            # Test case 2: Missing Title tag, but other tags present
            script_2 = "[INTRO]An intro without a title tag first (Tag).\n[OUTRO]And an outro (Tag)."
            mock_openai_create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=script_2))])
            result_2 = pswa_main.weave_script("content", "topic2_tag")
            self.assertTrue(result_2["title"].startswith("Podcast on topic2_tag"))
            self.assertEqual(len(result_2["segments"]), 2)
            self.assertEqual(result_2["segments"][0]["segment_title"], "INTRO")
            self.assertEqual(result_2["segments"][1]["segment_title"], "OUTRO")


class TestParseLlmScriptOutput(unittest.TestCase):
    # Test the parser directly. This class primarily tests the TAG-BASED parser.
    # JSON parsing is simpler (json.loads) and its failure modes are tested within weave_script tests.
    def setUp(self):
        # The parser uses pswa_config for default model, so mock it.
        # For these tag-based tests, PSWA_LLM_JSON_MODE should be False or not strictly relevant
        # as we are testing the direct tag parser.
        self.mock_pswa_config = {
            "PSWA_LLM_MODEL": "parser-test-model",
            "PSWA_LLM_JSON_MODE": False
        }
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.mock_pswa_config, clear=True)
        self.mock_config = self.config_patcher.start()

        # Mock imports_patcher if pswa_main.parse_llm_script_output relies on it (it shouldn't directly)
        # For safety, keeping it if other utility functions called by parser might use it.
        self.imports_patcher = patch.object(pswa_main, 'PSWA_IMPORTS_SUCCESSFUL', True)
        self.mock_imports = self.imports_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.imports_patcher.stop()

    # The test_weave_script_success and other weave_script tests from the original TestParseLlmScriptOutput
    # seem redundant if TestWeaveScriptLogic is comprehensive.
    # I'll keep the direct parser tests for the tag-based parser.
    # Removing the redundant weave_script tests from this class.

    # @patch('openai.ChatCompletion.create')
    # def test_weave_script_success(self, mock_openai_create):
    # ... (removed) ...

    # @patch('openai.ChatCompletion.create')
    # def test_weave_script_openai_api_error(self, mock_openai_create):
    # ... (removed) ...

    # def test_weave_script_missing_api_key(self):
    # ... (removed) ...

    # @patch('openai.ChatCompletion.create')
    # def test_weave_script_insufficient_content_from_llm(self, mock_openai_create):
    # ... (removed) ...

    # @patch('openai.ChatCompletion.create')
    # def test_script_parsing_variations(self, mock_openai_create):
    # ... (removed) ...

# Renaming this class to be more specific about testing the tag-based parser.
class TestTagBasedParseLlmScriptOutput(unittest.TestCase):
    def setUp(self):
        # For these tag-based tests, PSWA_LLM_JSON_MODE is not relevant for the parser itself.
        self.mock_pswa_config = {"PSWA_LLM_MODEL": "parser-test-model"}
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.mock_pswa_config)
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
    def test_parse_perfect_script(self): # Testing TAG-BASED parser
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
        # Note: parse_llm_script_output is the tag-based parser.
        parsed = pswa_main.parse_llm_script_output(raw_script, "Perfect Topic")
        self.assertEqual(parsed["title"], "Perfect Podcast Title")
        self.assertEqual(len(parsed["segments"]), 4)
        self.assertEqual(parsed["segments"][0]["segment_title"], "INTRO")
        self.assertEqual(parsed["segments"][0]["content"], "This is the introduction. It has multiple lines.\nWelcome!")
        self.assertEqual(parsed["segments"][1]["segment_title"], "First Segment Title")
        self.assertEqual(parsed["segments"][1]["content"], "Content for the first segment.\nMore content for segment 1.")
        self.assertEqual(parsed["segments"][2]["segment_title"], "Second Segment")
        self.assertEqual(parsed["segments"][2]["content"], "Content for the second segment.")
        self.assertEqual(parsed["segments"][3]["segment_title"], "OUTRO")
        self.assertEqual(parsed["segments"][3]["content"], "This is the outro.\nThanks for listening!")

    def test_parse_minimal_script(self): # Testing TAG-BASED parser
        raw_script = "[TITLE]Minimal\n[INTRO]Just intro.\n[OUTRO]Just outro."
        parsed = pswa_main.parse_llm_script_output(raw_script, "Minimal Topic")
        self.assertEqual(parsed["title"], "Minimal")
        self.assertEqual(len(parsed["segments"]), 2)
        self.assertEqual(parsed["segments"][0]["segment_title"], "INTRO")
        self.assertEqual(parsed["segments"][0]["content"], "Just intro.")
        self.assertEqual(parsed["segments"][1]["segment_title"], "OUTRO")
        self.assertEqual(parsed["segments"][1]["content"], "Just outro.")

    def test_parse_missing_optional_tags(self): # Testing TAG-BASED parser
        raw_script = "[TITLE]No Segments\n[INTRO]Only intro and outro here.\n[OUTRO]Bye."
        parsed = pswa_main.parse_llm_script_output(raw_script, "No Segments Topic")
        self.assertEqual(parsed["title"], "No Segments")
        self.assertEqual(len(parsed["segments"]), 2)
        self.assertEqual(parsed["segments"][0]["content"], "Only intro and outro here.")

    def test_parse_extra_whitespace_and_newlines(self): # Testing TAG-BASED parser
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
            "PSWA_LLM_TEMPERATURE": 0.7,
            "PSWA_LLM_MAX_TOKENS": 1500,
            "PSWA_LLM_JSON_MODE": True, # For endpoint tests, assume JSON mode is generally active
            "PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE": "System msg for endpoint",
            "PSWA_DEFAULT_PROMPT_USER_TEMPLATE": "User: {topic} - {content} (endpoint)"
        }
        # Use clear=True to ensure only these values are in pswa_config for this test class
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.mock_pswa_config, clear=True)
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
