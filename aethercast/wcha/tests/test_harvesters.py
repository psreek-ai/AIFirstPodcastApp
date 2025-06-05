import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import logging

# Adjust path to import WCHA modules
current_dir = os.path.dirname(os.path.abspath(__file__))
wcha_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(wcha_dir)
project_root_dir = os.path.dirname(aethercast_dir)

if wcha_dir not in sys.path:
    sys.path.insert(0, wcha_dir)
if aethercast_dir not in sys.path: # For importing wcha.main to access wcha_config
    sys.path.insert(0, aethercast_dir)
if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.wcha import harvesters as wcha_harvesters
from aethercast.wcha import main as wcha_main # To access and mock wcha_config
import requests # For requests.exceptions.RequestException

# Configure basic logging to avoid NoHandlerFoundError if wcha.harvesters uses logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Mock trafilatura before it's imported by wcha_harvesters, if necessary,
# or ensure it's patchable where used.
# For simplicity, we'll patch it directly in tests where wcha_harvesters.trafilatura is called.

class TestHarvestFromUrl(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.mock_wcha_config_defaults = {
            "WCHA_USER_AGENT": "Test User Agent",
            "WCHA_MIN_ARTICLE_LENGTH_FETCH": 50,  # Shorter for easier testing
            "WCHA_MAX_ARTICLE_LENGTH_FETCH": 500, # Smaller max for easier testing
            "WCHA_TRAFILATURA_DEFAULT_CONFIG": {"timeout": 2}, # Example trafilatura config
            # Add other config values that harvest_from_url might use
        }
        # Patch the wcha_config used by the harvesters module.
        # This assumes wcha_harvesters.py imports and uses wcha_config from wcha_main.py
        # or has its own wcha_config. If it's from wcha_main, we patch that.
        self.config_patcher = patch.dict(wcha_main.wcha_config, self.mock_wcha_config_defaults, clear=True)
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    @patch('aethercast.wcha.harvesters.trafilatura.extract')
    @patch('aethercast.wcha.harvesters.trafilatura.fetch_url')
    def test_harvest_from_url_trafilatura_fetch_returns_none(self, mock_fetch_url, mock_extract_text):
        mock_fetch_url.return_value = None # Simulate trafilatura failing to download

        with patch.object(wcha_harvesters.logging, 'warning') as mock_log_warning:
            result = wcha_harvesters.harvest_from_url("http://example.com/failfetch")
            self.assertIsNone(result)
            mock_extract_text.assert_not_called()
            mock_log_warning.assert_any_call("Trafilatura failed to fetch URL: %s", "http://example.com/failfetch")

    @patch('aethercast.wcha.harvesters.trafilatura.extract')
    @patch('aethercast.wcha.harvesters.trafilatura.fetch_url')
    def test_harvest_from_url_trafilatura_extract_returns_none(self, mock_fetch_url, mock_extract_text):
        mock_fetch_url.return_value = "<html><body>Some content</body></html>" # Download success
        mock_extract_text.return_value = None # Simulate extraction failure

        with patch.object(wcha_harvesters.logging, 'warning') as mock_log_warning:
            result = wcha_harvesters.harvest_from_url("http://example.com/failextract")
            self.assertIsNone(result)
            mock_fetch_url.assert_called_once_with("http://example.com/failextract", config=wcha_main.wcha_config["WCHA_TRAFILATURA_DEFAULT_CONFIG"])
            mock_extract_text.assert_called_once() # Extract is called
            mock_log_warning.assert_any_call("Trafilatura failed to extract main content from URL: %s", "http://example.com/failextract")

    @patch('aethercast.wcha.harvesters.trafilatura.extract')
    @patch('aethercast.wcha.harvesters.trafilatura.fetch_url')
    def test_harvest_from_url_trafilatura_success_content_too_short(self, mock_fetch_url, mock_extract_text):
        mock_fetch_url.return_value = "<html><body>Content</body></html>"
        short_text = "Too short." # Length 10
        self.assertTrue(len(short_text) < self.mock_wcha_config_defaults["WCHA_MIN_ARTICLE_LENGTH_FETCH"])
        mock_extract_text.return_value = short_text

        with patch.object(wcha_harvesters.logging, 'info') as mock_log_info:
            result = wcha_harvesters.harvest_from_url("http://example.com/short")
            self.assertIsNone(result)
            mock_log_info.assert_any_call(
                "Content from %s is too short after extraction (%d chars), skipping.",
                "http://example.com/short", len(short_text)
            )

    @patch('aethercast.wcha.harvesters.trafilatura.extract')
    @patch('aethercast.wcha.harvesters.trafilatura.fetch_url')
    def test_harvest_from_url_trafilatura_success_content_too_long(self, mock_fetch_url, mock_extract_text):
        mock_fetch_url.return_value = "<html><body>Long content...</body></html>"
        long_text = "This is very long text. " * 50 # 25 * 50 = 1250 chars
        max_len = self.mock_wcha_config_defaults["WCHA_MAX_ARTICLE_LENGTH_FETCH"] # 500
        self.assertTrue(len(long_text) > max_len)
        mock_extract_text.return_value = long_text

        with patch.object(wcha_harvesters.logging, 'info') as mock_log_info:
            result = wcha_harvesters.harvest_from_url("http://example.com/long")
            self.assertIsNotNone(result)
            self.assertEqual(result["text_content"], long_text[:max_len]) # Truncated
            self.assertEqual(result["status"], "success_truncated")
            mock_log_info.assert_any_call(
                "Content from %s was truncated from %d to %d characters.",
                "http://example.com/long", len(long_text), max_len
            )

    @patch('aethercast.wcha.harvesters.trafilatura.extract')
    @patch('aethercast.wcha.harvesters.trafilatura.fetch_url')
    def test_harvest_from_url_trafilatura_extract_exception(self, mock_fetch_url, mock_extract_text):
        mock_fetch_url.return_value = "<html><body>Content</body></html>"
        mock_extract_text.side_effect = Exception("Simulated trafilatura extract error")

        with patch.object(wcha_harvesters.logging, 'error') as mock_log_error:
            result = wcha_harvesters.harvest_from_url("http://example.com/extracterror")
            self.assertIsNone(result)
            mock_log_error.assert_any_call(
                "Trafilatura.extract raised an exception for URL %s: %s",
                "http://example.com/extracterror", unittest.mock.ANY, # Check for any exception string
                exc_info=True
            )

    @patch('aethercast.wcha.harvesters.requests.get') # Patch requests.get used when use_trafilatura=False
    @patch('aethercast.wcha.harvesters.BeautifulSoup') # Mock BeautifulSoup as well for this path
    def test_harvest_from_url_requests_get_exception_when_trafilatura_is_false(self, mock_bs, mock_requests_get):
        mock_requests_get.side_effect = requests.exceptions.RequestException("Simulated requests.get error")

        with patch.object(wcha_harvesters.logging, 'error') as mock_log_error:
            result = wcha_harvesters.harvest_from_url("http://example.com/requests_error", use_trafilatura=False)
            self.assertIsNone(result)
            mock_bs.assert_not_called() # BeautifulSoup should not be called if requests.get fails
            mock_log_error.assert_any_call(
                "Request failed for URL %s (use_trafilatura=False): %s",
                "http://example.com/requests_error", unittest.mock.ANY,
                exc_info=True
            )


if __name__ == '__main__':
    unittest.main()
