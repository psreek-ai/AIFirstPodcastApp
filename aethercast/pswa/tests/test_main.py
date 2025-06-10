import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json

# Explicitly add user site-packages to sys.path
# This is a workaround for potential PYTHONPATH issues in the execution environment.
user_site_packages = '/home/swebot/.local/lib/python3.10/site-packages'
if user_site_packages not in sys.path:
    sys.path.insert(0, user_site_packages)

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
        self.maxDiff = None
        self.mock_pswa_config_defaults = {
            "AIMS_SERVICE_URL": "http://mockaims.test/v1/generate", # New
            "AIMS_REQUEST_TIMEOUT_SECONDS": 10, # New
            "PSWA_LLM_MODEL": "gpt-3.5-turbo-1106", # Request to AIMS
            "PSWA_LLM_TEMPERATURE": 0.5, # Request to AIMS
            "PSWA_LLM_MAX_TOKENS": 1000, # Request to AIMS
            "PSWA_LLM_JSON_MODE": True, # Request to AIMS
            "PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE": "System prompt for JSON",
            "PSWA_DEFAULT_PROMPT_USER_TEMPLATE": "User prompt for JSON: {topic} - {content}",
            "PSWA_DATABASE_PATH": ":memory:",
            "PSWA_SCRIPT_CACHE_ENABLED": True,
            "PSWA_SCRIPT_CACHE_MAX_AGE_HOURS": 720
        }
        self.current_test_config = self.mock_pswa_config_defaults.copy()

        # Patch pswa_main.pswa_config directly. This is simpler if pswa_config is a global dict.
        self.current_test_config = self.mock_pswa_config_defaults.copy()
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.current_test_config, clear=True)
        self.mock_config = self.config_patcher.start()

        # PSWA_IMPORTS_SUCCESSFUL is for OpenAI, not relevant here as we mock requests to AIMS
        # If there was a check for `requests` library, we'd patch that.

    def tearDown(self):
        self.config_patcher.stop()

    @patch('requests.post') # Mock the call to AIMS
    def test_weave_script_success_json_mode_via_aims(self, mock_requests_post):
        aims_llm_output_json_str = json.dumps({
            "title": "AI in Education (AIMS JSON)",
            "intro": "Welcome to an AIMS JSON discussion on AI in education.",
            "segments": [{"segment_title": "Personalized Learning (AIMS JSON)", "content": "AI offers AIMS tailored learning."}],
            "outro": "AIMS JSON AI will reshape learning."
        })
        
        mock_aims_response = MagicMock()
        mock_aims_response.status_code = 200
        mock_aims_response.json.return_value = {
            "request_id": "aims_req_123",
            "model_id": "aims-gpt-3.5-turbo-0125", # Model reported by AIMS
            "choices": [{"text": aims_llm_output_json_str, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 50, "total_tokens": 60}
        }
        mock_requests_post.return_value = mock_aims_response

        result = pswa_main.weave_script("Some harvested content", "AI in Education via AIMS JSON")

        mock_requests_post.assert_called_once_with(
            pswa_main.pswa_config['AIMS_SERVICE_URL'],
            json=unittest.mock.ANY, # Check payload in more detail below
            timeout=pswa_main.pswa_config['AIMS_REQUEST_TIMEOUT_SECONDS']
        )
        # Check specific parts of the payload sent to AIMS
        sent_payload = mock_requests_post.call_args.kwargs['json']
        self.assertIn("User prompt for JSON: AI in Education via AIMS JSON - Some harvested content", sent_payload['prompt'])
        self.assertEqual(sent_payload['model_id_override'], self.current_test_config['PSWA_LLM_MODEL'])
        self.assertEqual(sent_payload['response_format'], {"type": "json_object"})

        self.assertNotIn("error_code", result)
        self.assertEqual(result["topic"], "AI in Education via AIMS JSON")
        self.assertEqual(result["title"], "AI in Education (AIMS JSON)")
        self.assertEqual(result["llm_model_used"], "aims-gpt-3.5-turbo-0125") # From AIMS response
        self.assertEqual(result["full_raw_script"], aims_llm_output_json_str)
        self.assertEqual(result.get("source"), "generation_via_aims")
        self.assertEqual(len(result["segments"]), 3) # Intro, 1 segment, Outro
        self.assertEqual(result["segments"][0]["content"], "Welcome to an AIMS JSON discussion on AI in education.")

    @patch('requests.post')
    def test_weave_script_success_fallback_parsing_via_aims(self, mock_requests_post):
        with patch.dict(pswa_main.pswa_config, {"PSWA_LLM_JSON_MODE": False}):
            aims_llm_text_output = """[TITLE]AI Education (AIMS Fallback)
[INTRO]AIMS Tag-based intro.
[OUTRO]AIMS Tag-based outro."""

            mock_aims_response = MagicMock()
            mock_aims_response.status_code = 200
            mock_aims_response.json.return_value = {
                "request_id": "aims_req_fallback", "model_id": "aims-fallback-model",
                "choices": [{"text": aims_llm_text_output, "finish_reason": "stop"}], "usage": {}
            }
            mock_requests_post.return_value = mock_aims_response

            result = pswa_main.weave_script("Content", "AI Education AIMS Fallback")

            sent_payload = mock_requests_post.call_args.kwargs['json']
            self.assertNotIn("response_format", sent_payload) # JSON mode was off

            self.assertNotIn("error_code", result)
            self.assertEqual(result["title"], "AI Education (AIMS Fallback)")
            self.assertEqual(result.get("source"), "generation_via_aims")
            self.assertEqual(len(result["segments"]), 2) # Intro, Outro
            self.assertEqual(result["segments"][0]["content"], "AIMS Tag-based intro.")

    @patch('requests.post')
    def test_weave_script_aims_returns_error_json(self, mock_requests_post):
        mock_aims_error_response = MagicMock()
        mock_aims_error_response.status_code = 400 # e.g., AIMS had an invalid request
        aims_error_payload = {"error": {"type": "invalid_request", "message": "AIMS: Prompt too long"}}
        mock_aims_error_response.json.return_value = aims_error_payload
        mock_aims_error_response.text = json.dumps(aims_error_payload) # For HTTPError text

        # Simulate requests.post raising an HTTPError for the 400 status
        mock_requests_post.side_effect = requests.exceptions.HTTPError(response=mock_aims_error_response)

        result = pswa_main.weave_script("Content", "AIMS Error Test")

        self.assertIn("error_code", result)
        self.assertEqual(result["error_code"], "PSWA_AIMS_HTTP_ERROR")
        self.assertIn("AIMS service returned HTTP 400", result["message"])
        self.assertEqual(result["details"], aims_error_payload) # AIMS JSON error is in details

    @patch('requests.post')
    def test_weave_script_aims_request_timeout(self, mock_requests_post):
        mock_requests_post.side_effect = requests.exceptions.Timeout("AIMS request timed out")

        result = pswa_main.weave_script("Content", "AIMS Timeout Test")
        self.assertIn("error_code", result)
        self.assertEqual(result["error_code"], "PSWA_AIMS_TIMEOUT")
        self.assertIn("AIMS request timed out", result["details"])

    def test_weave_script_aims_url_not_configured(self):
        with patch.dict(pswa_main.pswa_config, {"AIMS_SERVICE_URL": ""}):
            # Need to reload config in pswa_main or ensure weave_script re-reads it.
            # For this test, we assume load_pswa_configuration would be called or its effect matters.
            # The check for AIMS_SERVICE_URL is at the end of load_pswa_configuration.
            # To properly test this, we might need to trigger re-evaluation or test the loader.
            # However, if weave_script directly accesses pswa_config, this change should be seen.
            # The current structure of weave_script gets it from pswa_config at call time.
            # The critical check is in load_pswa_configuration. If it raises ValueError,
            # the app wouldn't start. If it doesn't, then weave_script might try to use an empty URL.
            # Let's assume the initial load_pswa_configuration would have failed.
            # This test is more about the ValueError from load_pswa_configuration.
            with self.assertRaises(ValueError) as context:
                pswa_main.load_pswa_configuration() # Trigger the check
            self.assertIn("AIMS_SERVICE_URL is not set", str(context.exception))
            # If the test reaches weave_script with empty URL, it would be a requests.exceptions.MissingSchema
            # For now, the load_pswa_configuration should prevent this.

    @patch('requests.post') # To ensure it's NOT called
    @patch('aethercast.pswa.main._save_script_to_cache')
    @patch('aethercast.pswa.main._get_cached_script')
    def test_weave_script_cache_hit_no_aims_call(self, mock_get_cached_script, mock_save_script_to_cache, mock_requests_post):
        self.current_test_config['PSWA_SCRIPT_CACHE_ENABLED'] = True
        with patch.dict(pswa_main.pswa_config, self.current_test_config, clear=True):
            topic = "Cache Hit Topic AIMS"
            content = "Content for AIMS cache hit."
            mock_cached_data = {
                "script_id": "cached_script_aims", "topic": topic, "title": "Cached AIMS Title",
                "full_raw_script": "Cached script text for AIMS",
                "segments": [{"segment_title": "INTRO", "content": "Cached intro AIMS"}],
                "llm_model_used": "aims-cached-model", "source": "cache"
            }
            mock_get_cached_script.return_value = mock_cached_data

            result = pswa_main.weave_script(content, topic)

            mock_get_cached_script.assert_called_once()
            mock_requests_post.assert_not_called() # AIMS should not be called
            mock_save_script_to_cache.assert_not_called()
            self.assertEqual(result, mock_cached_data)
            self.assertEqual(result["source"], "cache")

# Test class for the parsing logic (remains largely the same as it processes text)
# No changes needed here as it parses the text provided by AIMS.
class TestParseLlmScriptOutput(unittest.TestCase):
    # Test the parser directly. This class primarily tests the TAG-BASED parser.
    # JSON parsing is simpler (json.loads) and its failure modes are tested within weave_script tests.
    def setUp(self):
        self.mock_pswa_config = {
            "PSWA_LLM_MODEL": "parser-test-model", # Used as default by parser
            # PSWA_LLM_JSON_MODE is not directly used by parse_llm_script_output,
            # its effect is on what kind of string is passed to the parser.
        }
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.mock_pswa_config, clear=True)
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

# Renaming this class to be more specific about testing the tag-based parser.
class TestTagBasedParseLlmScriptOutput(unittest.TestCase):
    def setUp(self):
        self.mock_pswa_config = {"PSWA_LLM_MODEL": "parser-test-model"}
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.mock_pswa_config, clear=True) # Use clear=True
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
        # Config for endpoint tests, especially PSWA_TEST_MODE_ENABLED
        self.mock_pswa_config_endpoint = {
            "AIMS_SERVICE_URL": "http://mockaims.test/v1/generate", # Still needed even if test mode bypasses
            "AIMS_REQUEST_TIMEOUT_SECONDS": 5,
            "PSWA_LLM_MODEL": "gpt-endpoint-model", # For requests to AIMS if not in test mode
            "PSWA_LLM_JSON_MODE": True,
            "PSWA_TEST_MODE_ENABLED": True # Critical for these tests
        }
        self.config_patcher = patch.dict(pswa_main.pswa_config, self.mock_pswa_config_endpoint, clear=True)
        self.mock_config = self.config_patcher.start()

        # PSWA_IMPORTS_SUCCESSFUL is for OpenAI, not directly relevant now AIMS is used.
        # If there was a check for `requests` library for AIMS calls, that would be relevant.
        # For now, assuming `requests` is available.

    def tearDown(self):
        self.config_patcher.stop()

    @patch('aethercast.pswa.main.weave_script') # Still mock weave_script for endpoint unit tests
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
        # Simulate weave_script returning an error that came from AIMS
        mock_weave_script_func.return_value = {"error_code": "PSWA_AIMS_REQUEST_ERROR", "message": "AIMS down", "details": "Connection refused"}

        response = self.client.post('/weave_script', json={'content': 'content', 'topic': 'topic'})
        # Assuming PSWA_AIMS_REQUEST_ERROR maps to a 502 or 503 type error
        self.assertIn(response.status_code, [500, 502, 503, 504])
        json_data = response.get_json()
        self.assertEqual(json_data["error_code"], "PSWA_AIMS_REQUEST_ERROR")
        self.assertEqual(json_data["message"], "AIMS down")

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
        self.assertIn("Failed to parse essential script structure", json_data["details"]) # Changed message to details to match other errors


    # --- New Tests for Scenario-Based Test Mode ---
    def test_weave_script_test_mode_default_scenario(self):
        """Test test mode with no scenario header, should return default script."""
        # No X-Test-Scenario header, or an unrecognised one.
        response = self.client.post('/weave_script', json={'content': 'Some content', 'topic': 'Test Default Scenario'})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data['source'], 'test_mode_scenario_default')
        self.assertEqual(data['topic'], 'Test Default Scenario')
        self.assertTrue(data['title'].startswith("Test Mode: Test Default Scenario")) # Title is dynamic
        self.assertTrue(data['intro'].startswith("This is the intro for the test mode topic: Test Default Scenario"))
        self.assertEqual(len(data['segments']), pswa_main.SCENARIO_DEFAULT_SCRIPT_CONTENT['segments'].__len__())
        # Check full_raw_script reflects the dynamic title and intro
        raw_script_content = json.loads(data['full_raw_script'])
        self.assertTrue(raw_script_content['title'].startswith("Test Mode: Test Default Scenario"))

    def test_weave_script_test_mode_insufficient_content_scenario(self):
        """Test test mode with 'insufficient_content' scenario header."""
        headers = {'X-Test-Scenario': 'insufficient_content'}
        response = self.client.post('/weave_script', json={'content': 'Tiny content', 'topic': 'Test Insufficient'}, headers=headers)
        self.assertEqual(response.status_code, 200) # PSWA itself doesn't error, it returns the error structure from LLM
        data = response.get_json()

        self.assertEqual(data['source'], 'test_mode_scenario_insufficient_content')
        self.assertEqual(data['topic'], 'Test Insufficient')
        self.assertEqual(data[pswa_main.KEY_ERROR], "Insufficient content")
        self.assertIn("Test Insufficient", data[pswa_main.KEY_MESSAGE])
        # The endpoint might interpret this as a 400, check endpoint logic if this fails.
        # Current endpoint logic for insufficient content:
        # if result_data.get(KEY_SEGMENTS) and result_data[KEY_SEGMENTS][0][KEY_SEGMENT_TITLE] == SEGMENT_TITLE_ERROR ... returns 400
        # This is not directly hit here as the returned structure for this scenario is {"error": ..., "message": ...}
        # The endpoint test for insufficient content (`test_handle_weave_script_insufficient_content`) covers the 400.
        # This unit test for weave_script just checks the direct output of weave_script.

    def test_weave_script_test_mode_empty_segments_scenario(self):
        """Test test mode with 'empty_segments' scenario header."""
        headers = {'X-Test-Scenario': 'empty_segments'}
        response = self.client.post('/weave_script', json={'content': 'Content for empty seg', 'topic': 'Test Empty Segments'}, headers=headers)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data['source'], 'test_mode_scenario_empty_segments')
        self.assertEqual(data['topic'], 'Test Empty Segments')
        self.assertEqual(data[pswa_main.KEY_TITLE], pswa_main.SCENARIO_EMPTY_SEGMENTS_SCRIPT_CONTENT[pswa_main.KEY_TITLE])
        self.assertEqual(data[pswa_main.KEY_INTRO], pswa_main.SCENARIO_EMPTY_SEGMENTS_SCRIPT_CONTENT[pswa_main.KEY_INTRO])
        self.assertEqual(len(data[pswa_main.KEY_SEGMENTS]), 0) # Key check: segments list is empty
        self.assertEqual(data[pswa_main.KEY_OUTRO], pswa_main.SCENARIO_EMPTY_SEGMENTS_SCRIPT_CONTENT[pswa_main.KEY_OUTRO])


if __name__ == '__main__':
    unittest.main(verbosity=2)
