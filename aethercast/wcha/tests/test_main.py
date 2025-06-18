import unittest
from unittest.mock import patch, MagicMock, call
import os
import sys
import logging
import requests # Existing import
import socket # To reference socket.gaierror, socket.AF_INET, etc.
from urllib.parse import urlparse # May not be needed in test, but good for context

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

# Assuming is_url_safe is in aethercast.wcha.main
from aethercast.wcha.main import is_url_safe
from aethercast.wcha import main as wcha_main # For other tests

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


class TestIsUrlSafe(unittest.TestCase):
    @patch('socket.getaddrinfo')
    def test_valid_url_public_ipv4(self, mock_getaddrinfo):
        # AF_INET for IPv4
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 0))]
        safe, reason = is_url_safe("http://example.com")
        self.assertTrue(safe)
        self.assertEqual(reason, "URL is safe.")
        mock_getaddrinfo.assert_called_once_with("example.com", None)

    @patch('socket.getaddrinfo')
    def test_valid_url_public_ipv6(self, mock_getaddrinfo):
        # AF_INET6 for IPv6
        mock_getaddrinfo.return_value = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:4860:4860::8888', 0, 0, 0))]
        safe, reason = is_url_safe("http://example.com")
        self.assertTrue(safe)
        self.assertEqual(reason, "URL is safe.")

    @patch('socket.getaddrinfo')
    def test_url_private_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.1', 0))]
        safe, reason = is_url_safe("http://private.local")
        self.assertFalse(safe)
        self.assertIn("is not a public IP (is private)", reason)

    @patch('socket.getaddrinfo')
    def test_url_loopback_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0))]
        safe, reason = is_url_safe("http://localhost")
        self.assertFalse(safe)
        self.assertIn("is not a public IP (is loopback)", reason)

    @patch('socket.getaddrinfo')
    def test_url_loopback_ipv6(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 0, 0, 0))]
        safe, reason = is_url_safe("http://localhost6")
        self.assertFalse(safe)
        self.assertIn("is not a public IP (is loopback)", reason)

    @patch('socket.getaddrinfo')
    def test_url_multiple_ips_one_private(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 0)) # Private IP
        ]
        safe, reason = is_url_safe("http://mixed.example.com")
        self.assertFalse(safe)
        self.assertIn("10.0.0.1", reason)
        self.assertIn("is not a public IP (is private)", reason)

    @patch('socket.getaddrinfo')
    def test_url_multiple_public_ips(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:4860:4860::8888', 0, 0, 0))
        ]
        safe, reason = is_url_safe("http://multi.example.com")
        self.assertTrue(safe)
        self.assertEqual(reason, "URL is safe.")

    @patch('socket.getaddrinfo')
    def test_url_non_resolvable_hostname(self, mock_getaddrinfo):
        mock_getaddrinfo.side_effect = socket.gaierror("DNS resolution failed")
        safe, reason = is_url_safe("http://nonexistentdomain12345.com")
        self.assertFalse(safe)
        self.assertIn("Could not resolve hostname", reason)

    # No mock needed for getaddrinfo as it won't be called for scheme checks
    def test_url_invalid_scheme_ftp(self):
        safe, reason = is_url_safe("ftp://example.com")
        self.assertFalse(safe)
        self.assertIn("Invalid URL scheme: 'ftp'", reason)

    def test_url_invalid_scheme_file(self):
        safe, reason = is_url_safe("file:///etc/passwd")
        self.assertFalse(safe)
        self.assertIn("Invalid URL scheme: 'file'", reason)

    def test_url_no_hostname(self):
        # urlparse behavior for "http:///path" results in hostname being None
        safe, reason = is_url_safe("http:///path")
        self.assertFalse(safe)
        self.assertIn("URL has no hostname.", reason)

    @patch('socket.getaddrinfo')
    def test_url_link_local_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.1.1', 0))]
        safe, reason = is_url_safe("http://linklocal.corp")
        self.assertFalse(safe)
        self.assertIn("is not a public IP (is link-local)", reason)

    @patch('socket.getaddrinfo')
    def test_url_unspecified_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('0.0.0.0', 0))]
        safe, reason = is_url_safe("http://any.host") # Hostname doesn't matter if IP is 0.0.0.0
        self.assertFalse(safe)
        # The exact wording depends on how ipaddress formats multiple flags.
        # We need to ensure "is unspecified" is part of it.
        self.assertTrue("is unspecified" in reason and "is not a public IP" in reason)


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
            "WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION": 50
        }
        # Patch wcha_main.wcha_config directly
        self.config_patcher = patch.dict(wcha_main.wcha_config, self.mock_wcha_config_defaults, clear=True)
        self.mock_config = self.config_patcher.start()

        mock_ddgs_instance.text.reset_mock(return_value=True, side_effect=True)
        mock_requests_get.reset_mock(return_value=True, side_effect=True)
        mock_trafilatura_extract.reset_mock(return_value=True, side_effect=True)

    def tearDown(self):
        self.config_patcher.stop()

    def test_get_content_for_topic_success(self):
        mock_ddgs_instance.text.return_value = [{'href': 'http://example.com/page1'}, {'href': 'http://example.com/page2'}]
        mock_response1 = MagicMock(status_code=200, headers={'Content-Type': 'text/html'}, content=b"Page 1 HTML content")
        mock_response2 = MagicMock(status_code=200, headers={'Content-Type': 'text/html'}, content=b"Page 2 HTML content")
        mock_requests_get.side_effect = [mock_response1, mock_response2]

        page1_content = "Extracted content from page 1, which is definitely long enough."
        page2_content = "Extracted content from page 2, also made sure it is long enough."
        mock_trafilatura_extract.side_effect = [page1_content, page2_content]

        # Mock is_url_safe to always return True for this specific test
        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            result = wcha_main.get_content_for_topic("test topic")

        self.assertEqual(result["status"], "success")
        self.assertIn(page1_content, result["content"])
        self.assertIn(page2_content, result["content"])
        self.assertEqual(len(result["source_urls"]), 2)
        self.assertIn("http://example.com/page1", result["source_urls"])
        self.assertIn("http://example.com/page2", result["source_urls"])
        self.assertIn("Successfully consolidated content from 2 out of 2 URLs", result["message"])
        mock_ddgs_instance.text.assert_called_once_with(keywords="test topic", region='wt-wt', safesearch='moderate', max_results=3)
        self.assertEqual(mock_requests_get.call_count, 2)
        self.assertEqual(mock_trafilatura_extract.call_count, 2)

    def test_get_content_for_topic_no_search_results(self):
        mock_ddgs_instance.text.return_value = []
        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            result = wcha_main.get_content_for_topic("obscure topic")
        self.assertEqual(result["status"], "failure")
        self.assertIsNone(result["content"])
        self.assertEqual(len(result["source_urls"]), 0)
        self.assertIn(wcha_main.ERROR_WCHA_NO_SEARCH_RESULTS, result["message"])

    def test_get_content_for_topic_search_exception(self):
        mock_ddgs_instance.text.side_effect = Exception("DDG API Error")
        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            result = wcha_main.get_content_for_topic("search error topic")
        self.assertEqual(result["status"], "failure")
        self.assertIn(wcha_main.ERROR_WCHA_SEARCH_FAILED, result["message"])

    def test_get_content_for_topic_harvest_all_urls_fail(self):
        mock_ddgs_instance.text.return_value = [{'href': 'http://example.com/page1'}]
        mock_requests_get.side_effect = requests.exceptions.Timeout("Simulated timeout")
        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            result = wcha_main.get_content_for_topic("harvest fail topic")
        self.assertEqual(result["status"], "failure")
        self.assertIn(wcha_main.ERROR_WCHA_HARVEST_ALL_FAILED, result["message"])
        self.assertIn("Timeout after 10 seconds", result["message"])

    def test_get_content_for_topic_content_too_short_from_all_sources(self):
        mock_ddgs_instance.text.return_value = [{'href': 'http://example.com/short1'}]
        mock_response = MagicMock(status_code=200, headers={'Content-Type': 'text/html'}, content=b"Short HTML")
        mock_requests_get.return_value = mock_response
        mock_trafilatura_extract.return_value = "Too short."
        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            result = wcha_main.get_content_for_topic("short content topic")
        self.assertEqual(result["status"], "failure")
        self.assertIn(wcha_main.ERROR_WCHA_HARVEST_ALL_FAILED, result["message"])
        self.assertIn("Skipped (too short)", result["message"])

    def test_get_content_for_topic_partial_success_one_url_good_one_fails(self):
        mock_ddgs_instance.text.return_value = [{'href': 'http://good.com/page1'}, {'href': 'http://bad.com/page2'}]
        mock_good_response = MagicMock(status_code=200, headers={'Content-Type': 'text/html'}, content=b"Good HTML")
        mock_requests_get.side_effect = [mock_good_response, requests.exceptions.Timeout("Simulated timeout on second URL")]
        mock_trafilatura_extract.return_value = "Good content that is definitely long enough for aggregation."
        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            result = wcha_main.get_content_for_topic("partial success topic")
        self.assertEqual(result["status"], "success")
        self.assertIn("Failures: URL: http://bad.com/page2", result["message"])

    def test_get_content_for_topic_max_results_override(self):
        mock_ddgs_instance.text.return_value = []
        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            wcha_main.get_content_for_topic("test max results override", max_results_override=1)
        mock_ddgs_instance.text.assert_called_once_with(keywords="test max results override", region='wt-wt', safesearch='moderate', max_results=1)

    @patch('aethercast.wcha.main.IMPORTS_SUCCESSFUL', False)
    @patch('aethercast.wcha.main.MISSING_IMPORT_ERROR', "Simulated missing library")
    def test_get_content_for_topic_imports_not_successful(self):
        result = wcha_main.get_content_for_topic("any topic")
        self.assertEqual(result["status"], "failure_dependency") # Adjusted to match new status
        self.assertIn(wcha_main.ERROR_WCHA_LIB_MISSING, result["message"])


class TestWCHAFlaskEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if wcha_main.app:
            wcha_main.app.config['TESTING'] = True
            cls.client = wcha_main.app.test_client()
        else:
            cls.client = None

    def setUp(self):
        if not self.client:
            self.skipTest("Flask app not initialized in wcha_main. Skipping endpoint tests.")
        self.mock_wcha_config_for_endpoint = {
             "WCHA_SEARCH_MAX_RESULTS": 3,
             "WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION": 50,
             # Add other necessary configs, e.g., for Celery if endpoint uses it
             "USE_REAL_NEWS_API": False # Ensure predictable path for some tests
        }
        self.config_patcher = patch.dict(wcha_main.wcha_config, self.mock_wcha_config_for_endpoint, clear=True)
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_use_search_success(self, mock_get_content_for_topic):
        mock_success_data = {"status": "success", "content": "Content.", "source_urls": [], "message": ""}
        mock_get_content_for_topic.return_value = mock_success_data
        response = self.client.post('/harvest', json={"topic": "test success topic", "use_search": True})
        self.assertEqual(response.status_code, 200)
        mock_get_content_for_topic.assert_called_once_with('test success topic', max_results_override=None) # Adjusted expectation

    def test_harvest_endpoint_missing_parameters(self):
        response = self.client.post('/harvest', json={})
        self.assertEqual(response.status_code, 400)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "WCHA_MISSING_PARAMETERS") # Adjusted error code

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_use_search_failure_from_logic(self, mock_get_content_for_topic):
        mock_failure_data = {"status": "failure", "message": wcha_main.ERROR_WCHA_NO_SEARCH_RESULTS, "content": None, "source_urls": []}
        mock_get_content_for_topic.return_value = mock_failure_data
        response = self.client.post('/harvest', json={"topic": "test failure topic", "use_search": True})
        self.assertEqual(response.status_code, 404)

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_internal_error_in_logic(self, mock_get_content_for_topic):
        mock_get_content_for_topic.side_effect = Exception("Core logic unexpected error")
        response = self.client.post('/harvest', json={"topic": "test internal error", "use_search": True})
        self.assertEqual(response.status_code, 500)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "WCHA_INTERNAL_SERVER_ERROR")


    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_with_max_results_override(self, mock_get_content_for_topic):
        mock_get_content_for_topic.return_value = {"status": "success"}
        self.client.post('/harvest', json={"topic": "test max results", "use_search": True, "max_results": "3"}) # Pass as string like from JSON
        self.assertEqual(mock_get_content_for_topic.call_count, 1)
        # get_content_for_topic's max_results_override is passed from harvest_params_for_search
        # The endpoint logic converts "max_results" from payload to int for this.
        expected_call_args = call("test max results", max_results_override=3)
        self.assertEqual(mock_get_content_for_topic.call_args, expected_call_args)


    @patch('aethercast.wcha.main.harvest_url_content_task.delay')
    def test_harvest_endpoint_direct_url_async_dispatch(self, mock_delay):
        mock_task_instance = MagicMock()
        mock_task_instance.id = "test_celery_task_id_123"
        mock_delay.return_value = mock_task_instance

        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")): # Ensure is_url_safe passes
            response = self.client.post('/harvest', json={"url": "http://example.com/direct_async"})

        self.assertEqual(response.status_code, 202) # Should be 202 Accepted
        json_response = response.get_json()
        self.assertEqual(json_response["task_id"], "test_celery_task_id_123")
        self.assertIn("/v1/tasks/test_celery_task_id_123", json_response["status_url"])
        mock_delay.assert_called_once()
        # Check args passed to delay, request_id will be auto-generated
        args, kwargs = mock_delay.call_args
        self.assertIn('url_to_harvest', kwargs)
        self.assertEqual(kwargs['url_to_harvest'], "http://example.com/direct_async")
        self.assertIn('request_id', kwargs) # Check if request_id is passed


if __name__ == '__main__':
    unittest.main()
