import unittest
from unittest.mock import patch, MagicMock, call
import os
import sys
import logging

# Adjust path to import WCHA main module
current_dir = os.path.dirname(os.path.abspath(__file__))
wcha_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(wcha_dir)
project_root_dir = os.path.dirname(aethercast_dir)

if wcha_dir not in sys.path:
    sys.path.insert(0, wcha_dir)
if aethercast_dir not in sys.path:
    sys.path.insert(0, aethercast_dir)
if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.wcha import main as wcha_main

# Configure basic logging to avoid NoHandlerFoundError if wcha.main uses logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TestGetContentForTopic(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None # Show full diff on assertion failures
        self.mock_wcha_config_defaults = {
            "WCHA_MIN_CONTENT_LENGTH": 100,
            "WCHA_MAX_CONTENT_LENGTH": 1000, # Content longer than this will be summarized
            "WCHA_SUMMARIZER_CHUNK_SIZE": 500,
            "WCHA_MAX_URLS_TO_CHECK_DEFAULT": 5,
            "WCHA_MAX_TEXT_LENGTH_FOR_RELEVANCE_CHECK": 5000, # Chars
            "WCHA_RELEVANCE_THRESHOLD": 0.3, # Example value
            "DDG_MAX_RESULTS": 7, # Used by search_duckduckgo if not overridden
        }
        # Use a copy for modification in tests if needed, though patch.dict usually handles this well
        self.current_test_config = self.mock_wcha_config_defaults.copy()

        self.config_patcher = patch.dict(wcha_main.wcha_config, self.current_test_config, clear=True)
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    @patch('aethercast.wcha.main.is_content_relevant')
    @patch('aethercast.wcha.main.summarize_text_bart')
    @patch('aethercast.wcha.main.harvest_from_url')
    @patch('aethercast.wcha.main.search_duckduckgo')
    def test_get_content_for_topic_success_sufficient_content_no_summarization(
        self, mock_search_ddg, mock_harvest, mock_summarize, mock_is_relevant
    ):
        mock_search_ddg.return_value = ["http://example.com/page1", "http://example.com/page2"]
        # First URL returns enough content, second one won't be needed if content is sufficient
        # Min content length is 100. This content is 30 * 5 = 150.
        mock_harvest.side_effect = [
            {"text_content": "This is the first page content, long enough. " * 5, "status": "success", "url": "http://example.com/page1"},
            {"text_content": "Second page content, also good.", "status": "success", "url": "http://example.com/page2"}
        ]
        mock_is_relevant.return_value = True # All harvested content is relevant
        mock_summarize.return_value = "This should not be called." # Summarizer should not be called

        result = wcha_main.get_content_for_topic("test topic")

        self.assertEqual(result["status"], "SUCCESS")
        expected_content = "This is the first page content, long enough. " * 5
        self.assertEqual(result["original_content"], expected_content)
        self.assertEqual(result["processed_content"], expected_content) # No summarization
        self.assertIn("http://example.com/page1", result["source_urls"])
        
        # Check that harvesting stops once enough content is gathered.
        # Current logic in get_content_for_topic aims to fill up to WCHA_MIN_CONTENT_LENGTH,
        # and continues up to WCHA_MAX_URLS_TO_CHECK_DEFAULT if not enough content yet.
        # If first URL provides >= WCHA_MIN_CONTENT_LENGTH, it should stop.
        self.assertEqual(mock_harvest.call_count, 1)
        mock_summarize.assert_not_called()

    @patch('aethercast.wcha.main.is_content_relevant')
    @patch('aethercast.wcha.main.summarize_text_bart')
    @patch('aethercast.wcha.main.harvest_from_url')
    @patch('aethercast.wcha.main.search_duckduckgo')
    def test_get_content_for_topic_success_needs_summarization(
        self, mock_search_ddg, mock_harvest, mock_summarize, mock_is_relevant
    ):
        mock_search_ddg.return_value = ["http://example.com/longpage"]
        long_content = "Long content. " * 200 # Exceeds WCHA_MAX_CONTENT_LENGTH (1000)
        self.assertTrue(len(long_content) > self.current_test_config["WCHA_MAX_CONTENT_LENGTH"])
        mock_harvest.return_value = {"text_content": long_content, "status": "success", "url": "http://example.com/longpage"}
        mock_is_relevant.return_value = True
        mock_summarize.return_value = "Summarized long content."

        result = wcha_main.get_content_for_topic("long topic")

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["original_content"], long_content)
        self.assertEqual(result["processed_content"], "Summarized long content.")
        mock_summarize.assert_called_once()

    @patch('aethercast.wcha.main.harvest_from_url')
    @patch('aethercast.wcha.main.search_duckduckgo')
    def test_get_content_for_topic_failure_no_urls_found(
        self, mock_search_ddg, mock_harvest
    ):
        mock_search_ddg.return_value = [] # No URLs found
        result = wcha_main.get_content_for_topic("obscure topic")
        self.assertEqual(result["status"], "FAILURE_NO_URLS_FOUND")
        mock_harvest.assert_not_called()

    @patch('aethercast.wcha.main.is_content_relevant')
    @patch('aethercast.wcha.main.harvest_from_url')
    @patch('aethercast.wcha.main.search_duckduckgo')
    def test_get_content_for_topic_failure_harvesting_fails_for_all_urls(
        self, mock_search_ddg, mock_harvest, mock_is_relevant
    ):
        mock_search_ddg.return_value = ["http://example.com/page1", "http://example.com/page2"]
        mock_harvest.return_value = None # All harvesting attempts fail
        # is_content_relevant won't be called if harvest returns None
        result = wcha_main.get_content_for_topic("harvest fail topic")
        self.assertEqual(result["status"], "FAILURE_HARVESTING_ALL_URLS")
        # It should attempt to harvest for WCHA_MAX_URLS_TO_CHECK_DEFAULT if all fail
        self.assertEqual(mock_harvest.call_count, self.current_test_config["WCHA_MAX_URLS_TO_CHECK_DEFAULT"])


    @patch('aethercast.wcha.main.is_content_relevant')
    @patch('aethercast.wcha.main.harvest_from_url')
    @patch('aethercast.wcha.main.search_duckduckgo')
    def test_get_content_for_topic_failure_content_too_short_after_harvesting(
        self, mock_search_ddg, mock_harvest, mock_is_relevant
    ):
        mock_search_ddg.return_value = ["http://example.com/shortcontent"]
        short_content = "This is too short." # Less than WCHA_MIN_CONTENT_LENGTH (100)
        self.assertTrue(len(short_content) < self.current_test_config["WCHA_MIN_CONTENT_LENGTH"])
        # Simulate multiple URLs returning short content, all combined still too short
        mock_harvest.return_value = {"text_content": short_content, "status": "success", "url": "http://example.com/shortcontent"}
        mock_is_relevant.return_value = True # Content is relevant but too short

        result = wcha_main.get_content_for_topic("short content topic")
        self.assertEqual(result["status"], "FAILURE_CONTENT_TOO_SHORT")
        # It should have tried multiple URLs up to the default max
        self.assertEqual(mock_harvest.call_count, self.current_test_config["WCHA_MAX_URLS_TO_CHECK_DEFAULT"])


    @patch('aethercast.wcha.main.is_content_relevant')
    @patch('aethercast.wcha.main.harvest_from_url')
    @patch('aethercast.wcha.main.search_duckduckgo')
    def test_get_content_for_topic_failure_no_relevant_content(
        self, mock_search_ddg, mock_harvest, mock_is_relevant
    ):
        mock_search_ddg.return_value = ["http://example.com/page1", "http://example.com/page2"]
        # Harvest returns content, but it's deemed not relevant
        mock_harvest.side_effect = [
            {"text_content": "Content from page 1, long enough for min_length check.", "status": "success", "url": "http://example.com/page1"},
            {"text_content": "Content from page 2, also long enough for min_length check.", "status": "success", "url": "http://example.com/page2"}
        ] * 3 # Provide enough side effects for multiple calls
        mock_is_relevant.return_value = False # All content is irrelevant

        result = wcha_main.get_content_for_topic("irrelevant topic")
        self.assertEqual(result["status"], "FAILURE_NO_RELEVANT_CONTENT")
        self.assertTrue(mock_is_relevant.call_count <= self.current_test_config["WCHA_MAX_URLS_TO_CHECK_DEFAULT"])


    @patch('aethercast.wcha.main.is_content_relevant')
    @patch('aethercast.wcha.main.summarize_text_bart')
    @patch('aethercast.wcha.main.harvest_from_url')
    @patch('aethercast.wcha.main.search_duckduckgo')
    def test_get_content_for_topic_stops_after_max_urls_or_sufficient_content(
        self, mock_search_ddg, mock_harvest, mock_summarize, mock_is_relevant
    ):
        mock_search_ddg.return_value = [f"http://example.com/page{i}" for i in range(1, 11)] # 10 URLs
        max_urls_to_check_param = 3
        
        # Scenario 1: Stops because max_urls_to_check is hit, content still short
        mock_harvest.return_value = {"text_content": "Short piece " * 2, "status": "success", "url": "mock_url"} # 20 chars
        mock_is_relevant.return_value = True
        
        result1 = wcha_main.get_content_for_topic("max_urls_test", max_urls_to_check=max_urls_to_check_param)
        self.assertEqual(mock_harvest.call_count, max_urls_to_check_param)
        self.assertEqual(result1["status"], "FAILURE_CONTENT_TOO_SHORT")

        # Reset mocks for Scenario 2
        mock_harvest.reset_mock()
        mock_is_relevant.reset_mock() # is_relevant also needs reset
        mock_summarize.reset_mock() # Summarize might be called if content gets long

        # Scenario 2: Stops because sufficient content is gathered before hitting max_urls_to_check
        # min_content_length = 100. Each harvest returns 60 chars. 2 harvests needed.
        mock_harvest.side_effect = [
            {"text_content": "Sufficient content part 1. " * 3, "status": "success", "url": "http://example.com/page1"}, # 60 chars
            {"text_content": "Sufficient content part 2. " * 3, "status": "success", "url": "http://example.com/page2"}, # 60 chars, total 120
            {"text_content": "This should not be called.", "status": "success", "url": "http://example.com/page3"}
        ]
        mock_is_relevant.return_value = True
        mock_summarize.return_value = "summarized for length check" # Only if it became > WCHA_MAX_CONTENT_LENGTH

        result2 = wcha_main.get_content_for_topic("sufficient_content_test", max_urls_to_check=max_urls_to_check_param)
        self.assertEqual(mock_harvest.call_count, 2)
        self.assertEqual(result2["status"], "SUCCESS")
        self.assertTrue(len(result2["original_content"]) >= self.current_test_config["WCHA_MIN_CONTENT_LENGTH"])


class TestWCHAFlaskEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wcha_main.app.config['TESTING'] = True
        cls.client = wcha_main.app.test_client()

    def setUp(self):
        self.mock_wcha_config_for_endpoint = {
             "WCHA_MAX_URLS_TO_CHECK_DEFAULT": 5,
        }
        self.config_patcher = patch.dict(wcha_main.wcha_config, self.mock_wcha_config_for_endpoint, clear=True)
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    def test_health_endpoint(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        expected_response = {"status": "healthy", "service": "WCHA"}
        self.assertEqual(response.get_json(), expected_response)

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_success(self, mock_get_content_for_topic):
        mock_success_data = {
            "status": "SUCCESS",
            "original_content": "Original full content.",
            "processed_content": "Processed (maybe summarized) content.",
            "source_urls": ["http://example.com/source1"]
        }
        mock_get_content_for_topic.return_value = mock_success_data
        
        response = self.client.post('/harvest', json={"topic": "test success topic"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), mock_success_data)
        mock_get_content_for_topic.assert_called_once_with(
            topic_name="test success topic",
            max_urls_to_check=wcha_main.wcha_config.get("WCHA_MAX_URLS_TO_CHECK_DEFAULT", 5),
            config=unittest.mock.ANY
        )

    def test_harvest_endpoint_missing_topic(self):
        response = self.client.post('/harvest', json={})
        self.assertEqual(response.status_code, 400)
        expected_error = {"error": "BAD_REQUEST", "message": "'topic' is a required field."}
        self.assertEqual(response.get_json(), expected_error)

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_get_content_failure(self, mock_get_content_for_topic):
        mock_failure_data = {
            "status": "FAILURE_NO_URLS_FOUND",
            "message": "Could not find any URLs for the topic.",
            "original_content": "",
            "processed_content": "",
            "source_urls": []
        }
        mock_get_content_for_topic.return_value = mock_failure_data
        
        response = self.client.post('/harvest', json={"topic": "test failure topic"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), mock_failure_data)

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_internal_error(self, mock_get_content_for_topic):
        mock_get_content_for_topic.side_effect = Exception("Core logic unexpected error")
        
        response = self.client.post('/harvest', json={"topic": "test internal error topic"})
        self.assertEqual(response.status_code, 500)
        expected_error = {"error": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred."}
        json_response = response.get_json()
        self.assertEqual(json_response["error"], expected_error["error"])
        self.assertEqual(json_response["message"], expected_error["message"])

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_with_max_urls(self, mock_get_content_for_topic):
        mock_success_data = {"status": "SUCCESS", "processed_content": "Content from 3 URLs"}
        mock_get_content_for_topic.return_value = mock_success_data
        
        response = self.client.post('/harvest', json={"topic": "test max urls", "max_urls": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), mock_success_data)
        mock_get_content_for_topic.assert_called_once_with(
            topic_name="test max urls",
            max_urls_to_check=3,
            config=unittest.mock.ANY
        )


if __name__ == '__main__':
    unittest.main()
