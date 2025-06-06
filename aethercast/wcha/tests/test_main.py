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

# Mock DDGS and requests before they are used by wcha_main
# This is to prevent real network calls during test loading or execution.
mock_ddgs_instance = MagicMock()
mock_ddgs_instance.text.return_value = [] # Default to no results

mock_ddgs_context_manager = MagicMock()
mock_ddgs_context_manager.__enter__.return_value = mock_ddgs_instance
mock_ddgs_constructor = MagicMock(return_value=mock_ddgs_context_manager)

# Mock requests.get
mock_requests_get = MagicMock()

# Mock trafilatura.extract
mock_trafilatura_extract = MagicMock()

@patch('aethercast.wcha.main.DDGS', mock_ddgs_constructor)
@patch('aethercast.wcha.main.requests.get', mock_requests_get)
@patch('aethercast.wcha.main.trafilatura.extract', mock_trafilatura_extract)
class TestGetContentForTopic(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.mock_wcha_config_defaults = {
            "WCHA_SEARCH_MAX_RESULTS": 3,
            "WCHA_REQUEST_TIMEOUT": 10,
            "WCHA_USER_AGENT": "TestAgent/1.0",
            "WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION": 50 # Lower for easier testing
        }
        self.config_patcher = patch.dict(wcha_main.wcha_config, self.mock_wcha_config_defaults, clear=True)
        self.mock_config = self.config_patcher.start()

        # Reset mocks before each test
        mock_ddgs_instance.text.reset_mock(return_value=True, side_effect=True)
        mock_requests_get.reset_mock(return_value=True, side_effect=True)
        mock_trafilatura_extract.reset_mock(return_value=True, side_effect=True)


    def tearDown(self):
        self.config_patcher.stop()

    def test_get_content_for_topic_success(self):
        mock_ddgs_instance.text.return_value = [{'href': 'http://example.com/page1'}, {'href': 'http://example.com/page2'}]

        mock_response1 = MagicMock()
        mock_response1.status_code = 200
        mock_response1.headers = {'Content-Type': 'text/html'}
        mock_response1.content = b"Page 1 HTML content"

        mock_response2 = MagicMock()
        mock_response2.status_code = 200
        mock_response2.headers = {'Content-Type': 'text/html'}
        mock_response2.content = b"Page 2 HTML content"

        mock_requests_get.side_effect = [mock_response1, mock_response2]

        mock_trafilatura_extract.side_effect = ["Extracted content from page 1, which is long enough.", "Extracted content from page 2, also long enough."]

        result = wcha_main.get_content_for_topic("test topic")

        self.assertEqual(result["status"], "success")
        self.assertIn("Extracted content from page 1", result["content"])
        self.assertIn("Extracted content from page 2", result["content"])
        self.assertEqual(len(result["source_urls"]), 2)
        self.assertIn("http://example.com/page1", result["source_urls"])
        self.assertIn("http://example.com/page2", result["source_urls"])
        self.assertIn("Successfully consolidated content", result["message"])
        mock_ddgs_instance.text.assert_called_once_with(keywords="test topic", region='wt-wt', safesearch='moderate', max_results=3)
        self.assertEqual(mock_requests_get.call_count, 2)
        self.assertEqual(mock_trafilatura_extract.call_count, 2)

    def test_get_content_for_topic_no_search_results(self):
        mock_ddgs_instance.text.return_value = [] # No search results
        
        result = wcha_main.get_content_for_topic("obscure topic")

        self.assertEqual(result["status"], "failure")
        self.assertIsNone(result["content"])
        self.assertEqual(len(result["source_urls"]), 0)
        self.assertIn(wcha_main.ERROR_WCHA_NO_SEARCH_RESULTS, result["message"])
        mock_requests_get.assert_not_called()
        mock_trafilatura_extract.assert_not_called()

    def test_get_content_for_topic_search_exception(self):
        mock_ddgs_instance.text.side_effect = Exception("DDG API Error")

        result = wcha_main.get_content_for_topic("search error topic")

        self.assertEqual(result["status"], "failure")
        self.assertIn(wcha_main.ERROR_WCHA_SEARCH_FAILED, result["message"])
        self.assertIn("DDG API Error", result["message"])

    def test_get_content_for_topic_harvest_all_urls_fail(self):
        mock_ddgs_instance.text.return_value = [{'href': 'http://example.com/page1'}]
        mock_requests_get.side_effect = requests.exceptions.Timeout("Simulated timeout")

        result = wcha_main.get_content_for_topic("harvest fail topic")

        self.assertEqual(result["status"], "failure")
        self.assertIsNone(result["content"])
        self.assertEqual(len(result["source_urls"]), 0)
        self.assertIn(wcha_main.ERROR_WCHA_HARVEST_ALL_FAILED, result["message"])
        self.assertIn("Simulated timeout", result["message"]) # Check if error detail is propagated
        mock_requests_get.assert_called_once() # Should try the one URL
        mock_trafilatura_extract.assert_not_called() # Should not reach extraction if fetch fails

    def test_get_content_for_topic_content_too_short_from_all_sources(self):
        mock_ddgs_instance.text.return_value = [{'href': 'http://example.com/short1'}]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'text/html'}
        mock_response.content = b"Short HTML"
        mock_requests_get.return_value = mock_response

        # WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION is 50 by default in these tests
        mock_trafilatura_extract.return_value = "Too short." # Length 10
        
        result = wcha_main.get_content_for_topic("short content topic")

        self.assertEqual(result["status"], "failure") # Fails if ALL are too short
        self.assertIsNone(result["content"])
        self.assertEqual(len(result["source_urls"]), 0)
        self.assertIn(wcha_main.ERROR_WCHA_HARVEST_ALL_FAILED, result["message"])
        self.assertIn("Skipped (too short)", result["message"])

    def test_get_content_for_topic_partial_success_one_url_good_one_fails(self):
        mock_ddgs_instance.text.return_value = [{'href': 'http://good.com/page1'}, {'href': 'http://bad.com/page2'}]

        mock_good_response = MagicMock()
        mock_good_response.status_code = 200
        mock_good_response.headers = {'Content-Type': 'text/html'}
        mock_good_response.content = b"Good HTML"

        # Simulate requests.get: first call is good, second call raises Timeout
        mock_requests_get.side_effect = [mock_good_response, requests.exceptions.Timeout("Simulated timeout on second URL")]
        
        mock_trafilatura_extract.return_value = "Good content that is definitely long enough for aggregation."

        result = wcha_main.get_content_for_topic("partial success topic")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["content"], "Source: http://good.com/page1\nGood content that is definitely long enough for aggregation.")
        self.assertEqual(result["source_urls"], ["http://good.com/page1"])
        self.assertIn("Successfully consolidated content", result["message"])
        self.assertIn("Failures: URL: http://bad.com/page2, Status: Failed", result["message"]) # Check for failure part in message
        self.assertEqual(mock_requests_get.call_count, 2) # Both URLs attempted
        mock_trafilatura_extract.assert_called_once_with(b"Good HTML", url='http://good.com/page1', output_format='txt', include_comments=False, include_tables=False, favor_precision=True)


    def test_get_content_for_topic_max_results_override(self):
        # Test that max_results_override is passed to DDGS
        mock_ddgs_instance.text.return_value = [] # No need for results, just checking the call
        wcha_main.get_content_for_topic("test max results override", max_results_override=1)
        mock_ddgs_instance.text.assert_called_once_with(keywords="test max results override", region='wt-wt', safesearch='moderate', max_results=1)

    @patch('aethercast.wcha.main.IMPORTS_SUCCESSFUL', False)
    @patch('aethercast.wcha.main.MISSING_IMPORT_ERROR', "Simulated missing library")
    def test_get_content_for_topic_imports_not_successful(self):
        result = wcha_main.get_content_for_topic("any topic")
        self.assertEqual(result["status"], "failure")
        self.assertIsNone(result["content"])
        self.assertEqual(result["source_urls"], [])
        self.assertIn(wcha_main.ERROR_WCHA_LIB_MISSING, result["message"])
        self.assertIn("Simulated missing library", result["message"])


class TestWCHAFlaskEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure Flask app is configured for testing if it exists
        if wcha_main.app:
            wcha_main.app.config['TESTING'] = True
            cls.client = wcha_main.app.test_client()
        else:
            cls.client = None # No Flask app defined

    def setUp(self):
        if not self.client:
            self.skipTest("Flask app not initialized in wcha_main. Skipping endpoint tests.")

        self.mock_wcha_config_for_endpoint = {
             "WCHA_SEARCH_MAX_RESULTS": 3, # Example, can be overridden by request
             "WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION": 50
        }
        self.config_patcher = patch.dict(wcha_main.wcha_config, self.mock_wcha_config_for_endpoint, clear=True)
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    # No /health endpoint defined in the provided wcha_main.py
    # def test_health_endpoint(self):
    #     response = self.client.get('/health')
    #     self.assertEqual(response.status_code, 200)
    #     expected_response = {"status": "healthy", "service": "WCHA"}
    #     self.assertEqual(response.get_json(), expected_response)

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_use_search_success(self, mock_get_content_for_topic):
        mock_success_data = {
            "status": "success",
            "content": "Successfully harvested content for topic.",
            "source_urls": ["http://example.com/source1"],
            "message": "Successfully consolidated content from 1 out of 1 URLs."
        }
        mock_get_content_for_topic.return_value = mock_success_data
        
        response = self.client.post('/harvest', json={"topic": "test success topic", "use_search": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), mock_success_data)
        mock_get_content_for_topic.assert_called_once_with(
            topic="test success topic",
            max_results_override=None # Default as not provided in request
        )

    def test_harvest_endpoint_missing_parameters(self):
        response = self.client.post('/harvest', json={}) # No topic, no url
        self.assertEqual(response.status_code, 400)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "WCHA_MISSING_PARAMETERS")
        self.assertIn("'topic' (with use_search=true) or 'url' must be provided", json_response.get("details"))

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_use_search_failure_from_logic(self, mock_get_content_for_topic):
        mock_failure_data = {
            "status": "failure",
            "content": None,
            "source_urls": [],
            "message": "WCHA: No search results found for topic: test failure topic"
        }
        mock_get_content_for_topic.return_value = mock_failure_data
        
        response = self.client.post('/harvest', json={"topic": "test failure topic", "use_search": True})
        # The status code depends on how errors from get_content_for_topic are mapped in the endpoint
        # Current mapping: ERROR_WCHA_NO_SEARCH_RESULTS -> 404
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), mock_failure_data)

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_internal_error_in_logic(self, mock_get_content_for_topic):
        mock_get_content_for_topic.side_effect = Exception("Core logic unexpected error")
        
        response = self.client.post('/harvest', json={"topic": "test internal error", "use_search": True})
        self.assertEqual(response.status_code, 500)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "WCHA_INTERNAL_SERVER_ERROR")
        self.assertIn("Core logic unexpected error", json_response.get("details"))

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_with_max_results_override(self, mock_get_content_for_topic):
        mock_success_data = {"status": "success", "content": "Content from 3 URLs"}
        mock_get_content_for_topic.return_value = mock_success_data
        
        response = self.client.post('/harvest', json={"topic": "test max results", "use_search": True, "max_results": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), mock_success_data)
        mock_get_content_for_topic.assert_called_once_with(
            topic="test max results",
            max_results_override=3
        )

    @patch('aethercast.wcha.main.harvest_from_url')
    def test_harvest_endpoint_direct_url_success(self, mock_harvest_from_url):
        mock_harvest_data = {
            "url": "http://example.com/direct",
            "content": "Directly harvested content, long enough for testing.",
            "error_type": None,
            "error_message": None
        }
        mock_harvest_from_url.return_value = mock_harvest_data

        response = self.client.post('/harvest', json={"url": "http://example.com/direct"})
        self.assertEqual(response.status_code, 200)
        expected_response_structure = {
            "status": "success",
            "content": "Directly harvested content, long enough for testing.",
            "source_urls": ["http://example.com/direct"],
            "message": "Successfully harvested content from URL: http://example.com/direct"
        }
        self.assertEqual(response.get_json(), expected_response_structure)
        mock_harvest_from_url.assert_called_once_with("http://example.com/direct", timeout=unittest.mock.ANY, min_length=unittest.mock.ANY)


    @patch('aethercast.wcha.main.harvest_from_url')
    def test_harvest_endpoint_direct_url_failure(self, mock_harvest_from_url):
        mock_harvest_data = {
            "url": "http://example.com/failed_direct",
            "content": None,
            "error_type": wcha_main.WCHA_ERROR_TYPE_FETCH,
            "error_message": "Simulated fetch error"
        }
        mock_harvest_from_url.return_value = mock_harvest_data

        response = self.client.post('/harvest', json={"url": "http://example.com/failed_direct"})
        self.assertEqual(response.status_code, 502) # Mapped from WCHA_ERROR_TYPE_FETCH
        expected_response_structure = {
            "status": "failure",
            "content": None,
            "source_urls": [],
            "message": "Failed to harvest content from URL: http://example.com/failed_direct. Reason: Simulated fetch error"
        }
        self.assertEqual(response.get_json(), expected_response_structure)


if __name__ == '__main__':
    unittest.main()
