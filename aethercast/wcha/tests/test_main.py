import unittest
from unittest.mock import patch, MagicMock, call
import os
import sys
import logging
import requests # Existing import
import socket # For socket.gaierror
import ipaddress # Though not directly used in tests, good for context if is_url_safe uses it.

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


class TestIsUrlSafe(unittest.TestCase):
    @patch('socket.gethostbyname')
    def test_safe_url_http_public_ip(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "8.8.8.8"
        is_safe, reason = wcha_main.is_url_safe("http://example.com")
        self.assertTrue(is_safe)
        self.assertEqual(reason, "URL is safe.")
        mock_gethostbyname.assert_called_once_with("example.com")

    @patch('socket.gethostbyname')
    def test_safe_url_https_public_ip(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "8.8.4.4"
        is_safe, reason = wcha_main.is_url_safe("https://sub.example.org/path?query=true")
        self.assertTrue(is_safe)
        self.assertEqual(reason, "URL is safe.")
        mock_gethostbyname.assert_called_once_with("sub.example.org")

    def test_unsafe_url_ftp_scheme(self):
        is_safe, reason = wcha_main.is_url_safe("ftp://example.com")
        self.assertFalse(is_safe)
        self.assertEqual(reason, "Invalid URL scheme: 'ftp'. Only 'http' or 'https' allowed.")

    def test_unsafe_url_file_scheme(self):
        is_safe, reason = wcha_main.is_url_safe("file:///etc/passwd")
        self.assertFalse(is_safe)
        self.assertEqual(reason, "Invalid URL scheme: 'file'. Only 'http' or 'https' allowed.")

    def test_unsafe_url_no_hostname(self):
        is_safe, reason = wcha_main.is_url_safe("http://")
        self.assertFalse(is_safe)
        self.assertEqual(reason, "URL has no hostname.")

    @patch('socket.gethostbyname')
    def test_unsafe_url_private_ip(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "192.168.1.1"
        is_safe, reason = wcha_main.is_url_safe("http://local.network/resource")
        self.assertFalse(is_safe)
        self.assertEqual(reason, "Resolved IP address '192.168.1.1' for hostname 'local.network' is not a public IP (is private).")
        mock_gethostbyname.assert_called_once_with("local.network")

    @patch('socket.gethostbyname')
    def test_unsafe_url_loopback_ip(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "127.0.0.1"
        is_safe, reason = wcha_main.is_url_safe("http://localhost/path")
        self.assertFalse(is_safe)
        self.assertEqual(reason, "Resolved IP address '127.0.0.1' for hostname 'localhost' is not a public IP (is loopback, is private).")

    @patch('socket.gethostbyname')
    def test_unsafe_url_link_local_ip(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "169.254.1.1"
        is_safe, reason = wcha_main.is_url_safe("http://linklocal.example/")
        self.assertFalse(is_safe)
        self.assertEqual(reason, "Resolved IP address '169.254.1.1' for hostname 'linklocal.example' is not a public IP (is private, is link-local).")

    @patch('socket.gethostbyname')
    def test_unresolvable_hostname(self, mock_gethostbyname):
        mock_gethostbyname.side_effect = socket.gaierror("Test gaierror")
        is_safe, reason = wcha_main.is_url_safe("http://domain.that.does.not.exist.hopefully/")
        self.assertFalse(is_safe)
        self.assertEqual(reason, "Could not resolve hostname: 'domain.that.does.not.exist.hopefully'.")

    @patch('socket.gethostbyname')
    def test_url_with_ip_address_public(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "1.1.1.1"
        is_safe, reason = wcha_main.is_url_safe("http://1.1.1.1/some/path")
        self.assertTrue(is_safe)
        self.assertEqual(reason, "URL is safe.")
        mock_gethostbyname.assert_called_once_with("1.1.1.1")

    @patch('socket.gethostbyname')
    def test_url_with_ip_address_private(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "10.0.0.1"
        is_safe, reason = wcha_main.is_url_safe("http://10.0.0.1/confidential")
        self.assertFalse(is_safe)
        self.assertEqual(reason, "Resolved IP address '10.0.0.1' for hostname '10.0.0.1' is not a public IP (is private).")

    def test_url_invalid_structure_value_error(self):
        # This tests the case where urlparse might create a hostname that socket.gethostbyname rejects (e.g., with null bytes)
        # which then gets caught by the generic Exception handler in is_url_safe.
        with patch('socket.gethostbyname', side_effect=TypeError("gethostbyname() argument 1 must be encoded string without null bytes, not str")):
            is_safe, reason = wcha_main.is_url_safe("http://exa\x00mple.com")
            self.assertFalse(is_safe)
            self.assertEqual(reason, "Unexpected error during URL validation: gethostbyname() argument 1 must be encoded string without null bytes, not str")

    @patch('socket.gethostbyname')
    def test_url_with_ipv6_loopback(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "::1"
        is_safe, reason = wcha_main.is_url_safe("http://[::1]/test")
        self.assertFalse(is_safe)
        self.assertEqual(reason, "Resolved IP address '::1' for hostname '::1' is not a public IP (is loopback, is private).")
        mock_gethostbyname.assert_called_once_with("::1") # urlparse provides '::1' as hostname

    @patch('socket.gethostbyname')
    def test_url_with_ipv6_public(self, mock_gethostbyname):
        mock_gethostbyname.return_value = "2001:4860:4860::8888"
        is_safe, reason = wcha_main.is_url_safe("http://[2001:4860:4860::8888]/ipv6test")
        self.assertTrue(is_safe)
        self.assertEqual(reason, "URL is safe.")
        mock_gethostbyname.assert_called_once_with("2001:4860:4860::8888") # urlparse provides hostname without brackets


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
        # is_url_safe is not called if IMPORTS_SUCCESSFUL is False at the start of get_content_for_topic
        result = wcha_main.get_content_for_topic("any topic")
        self.assertEqual(result["status"], "failure")
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
             "WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION": 50
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
        mock_get_content_for_topic.assert_called_once_with('test success topic')

    def test_harvest_endpoint_missing_parameters(self):
        response = self.client.post('/harvest', json={})
        self.assertEqual(response.status_code, 400)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "WCHA_INVALID_PAYLOAD")

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_use_search_failure_from_logic(self, mock_get_content_for_topic):
        mock_failure_data = {"status": "failure", "message": wcha_main.ERROR_WCHA_NO_SEARCH_RESULTS}
        mock_get_content_for_topic.return_value = mock_failure_data
        response = self.client.post('/harvest', json={"topic": "test failure topic", "use_search": True})
        self.assertEqual(response.status_code, 404)

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_internal_error_in_logic(self, mock_get_content_for_topic):
        mock_get_content_for_topic.side_effect = Exception("Core logic unexpected error")
        response = self.client.post('/harvest', json={"topic": "test internal error", "use_search": True})
        self.assertEqual(response.status_code, 500)

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_with_max_results_override(self, mock_get_content_for_topic):
        mock_get_content_for_topic.return_value = {"status": "success"}
        self.client.post('/harvest', json={"topic": "test max results", "use_search": True, "max_results": 3})
        self.assertEqual(mock_get_content_for_topic.call_count, 1)
        expected_call_args = call("test max results", max_results_override=3)
        self.assertEqual(mock_get_content_for_topic.call_args, expected_call_args)

    @patch('aethercast.wcha.main.harvest_from_url')
    def test_harvest_endpoint_direct_url_success(self, mock_harvest_from_url):
        mock_harvest_from_url.return_value = {"content": "Direct content"}
        response = self.client.post('/harvest', json={"url": "http://example.com/direct"})
        self.assertEqual(response.status_code, 200)
        mock_harvest_from_url.assert_called_once_with("http://example.com/direct")

    @patch('aethercast.wcha.main.harvest_from_url')
    def test_harvest_endpoint_direct_url_failure(self, mock_harvest_from_url):
        mock_harvest_from_url.return_value = {"content": None, "error_type": wcha_main.WCHA_ERROR_TYPE_FETCH}
        response = self.client.post('/harvest', json={"url": "http://example.com/failed_direct"})
        self.assertEqual(response.status_code, 502)

if __name__ == '__main__':
    unittest.main()
