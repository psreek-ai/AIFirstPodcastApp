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

from aethercast.wcha import main as wcha_logic # Changed import and alias
from aethercast.wcha import main as wcha_main # To access and mock wcha_config
import requests # For requests.exceptions.RequestException

# Configure basic logging to avoid NoHandlerFoundError if wcha_logic uses logging
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

    @patch('aethercast.wcha.main.requests.get') # Patch requests.get directly
    @patch('aethercast.wcha.main.trafilatura.extract') # Still need to mock extract to prevent it from running
    def test_harvest_from_url_fetch_fails_with_http_error(self, mock_extract_text, mock_requests_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_requests_get.side_effect = requests.exceptions.HTTPError(response=mock_response)

        with patch.object(wcha_logic.logger, 'error') as mock_log_error: # Patched logger instance
            result = wcha_logic.harvest_from_url("http://example.com/notfound")
            self.assertIsNotNone(result)
            self.assertIsNone(result["content"])
            self.assertEqual(result["error_type"], wcha_logic.WCHA_ERROR_TYPE_FETCH)
            self.assertIn("HTTP Status 404", result["error_message"])
            mock_extract_text.assert_not_called()
            # Corrected log assertion format
            mock_log_error.assert_any_call(
                f"[WCHA_LOGIC_WEB] HTTP Status 404 while fetching 'http://example.com/notfound'. Response: Not Found",
                exc_info=True
            )

    @patch('aethercast.wcha.main.trafilatura.extract') # Patched to main
    @patch('aethercast.wcha.main.requests.get') # Patching requests.get
    def test_harvest_from_url_trafilatura_extract_returns_none(self, mock_requests_get, mock_extract_text):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = "<html><body>Some content</body></html>"
        mock_response.headers = {'Content-Type': 'text/html'}
        mock_requests_get.return_value = mock_response
        mock_extract_text.return_value = None # Simulate extraction failure

        with patch.object(wcha_logic.logger, 'warning') as mock_log_warning: # Patched logger instance
            result = wcha_logic.harvest_from_url("http://example.com/failextract") # Use alias
            self.assertIsNotNone(result)
            self.assertIsNone(result["content"])
            self.assertEqual(result["error_type"], wcha_logic.WCHA_ERROR_TYPE_NO_CONTENT)
            mock_requests_get.assert_called_once_with("http://example.com/failextract", headers=unittest.mock.ANY, timeout=unittest.mock.ANY, allow_redirects=False)
            mock_extract_text.assert_called_once()
            # Corrected log assertion format
            mock_log_warning.assert_any_call(f"[WCHA_LOGIC_WEB] Trafilatura extracted no content from URL: http://example.com/failextract.")

    @patch('aethercast.wcha.main.trafilatura.extract') # Patched to main
    @patch('aethercast.wcha.main.requests.get') # Patching requests.get
    def test_harvest_from_url_trafilatura_success_content_too_short(self, mock_requests_get, mock_extract_text):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = "<html><body>Content</body></html>"
        mock_response.headers = {'Content-Type': 'text/html'}
        mock_requests_get.return_value = mock_response
        short_text = "Too short." # Length 10
        min_len_config = self.mock_wcha_config_defaults["WCHA_MIN_ARTICLE_LENGTH_FETCH"]
        self.assertTrue(len(short_text) < min_len_config)
        mock_extract_text.return_value = short_text

        with patch.object(wcha_logic.logger, 'warning') as mock_log_warning: # Patched logger instance
            result = wcha_logic.harvest_from_url("http://example.com/short", min_length=min_len_config) # Use alias
            self.assertIsNotNone(result) # harvest_from_url returns the content
            self.assertEqual(result["content"], short_text)
            # Corrected log assertion format
            mock_log_warning.assert_any_call(
                f"[WCHA_LOGIC_WEB] Content from http://example.com/short is shorter ({len(short_text)} chars) than min_length ({min_len_config} chars)."
            )

    @patch('aethercast.wcha.main.trafilatura.extract') # Patched to main
    @patch('aethercast.wcha.main.requests.get') # Patching requests.get
    def test_harvest_from_url_trafilatura_success_content_too_long(self, mock_requests_get, mock_extract_text):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = "<html><body>Long content...</body></html>"
        mock_response.headers = {'Content-Type': 'text/html'}
        mock_requests_get.return_value = mock_response
        long_text = "This is very long text. " * 50
        mock_extract_text.return_value = long_text
        # max_len = self.mock_wcha_config_defaults["WCHA_MAX_ARTICLE_LENGTH_FETCH"] # Not directly used by harvest_from_url for truncation

        result = wcha_logic.harvest_from_url("http://example.com/long") # Use alias
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], long_text) # No truncation in harvest_from_url
        # self.assertEqual(result["status"], "success_truncated") # No status like this from harvest_from_url
        # No specific log for truncation in harvest_from_url

    @patch('aethercast.wcha.main.trafilatura.extract') # Patched to main
    @patch('aethercast.wcha.main.requests.get') # Patching requests.get
    def test_harvest_from_url_trafilatura_extract_exception(self, mock_requests_get, mock_extract_text):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = "<html><body>Content</body></html>"
        mock_response.headers = {'Content-Type': 'text/html'}
        mock_requests_get.return_value = mock_response
        mock_extract_text.side_effect = Exception("Simulated trafilatura extract error")

        with patch.object(wcha_logic.logger, 'error') as mock_log_error: # Patched logger instance
            result = wcha_logic.harvest_from_url("http://example.com/extracterror") # Use alias
            self.assertIsNotNone(result)
            self.assertIsNone(result["content"])
            self.assertEqual(result["error_type"], wcha_logic.WCHA_ERROR_TYPE_EXTRACTION)
            # Corrected log assertion format
            expected_log_message = f"[WCHA_LOGIC_WEB] Trafilatura processing or other unexpected error for 'http://example.com/extracterror': Exception - Simulated trafilatura extract error"
            mock_log_error.assert_any_call(expected_log_message, exc_info=True)

    @patch('aethercast.wcha.main.requests.get')
    @patch('aethercast.wcha.main.BeautifulSoup')
    @patch('aethercast.wcha.main.trafilatura.extract') # Mock to prevent actual execution
    def test_harvest_from_url_trafilatura_import_fails(self, mock_trafilatura_extract, mock_bs, mock_requests_get):
        # Simulate that trafilatura was not successfully imported
        with patch('aethercast.wcha.main._IMPORTS_SUCCESSFUL_TRAFILATURA', False):
            with patch.object(wcha_logic.logger, 'error') as mock_log_error: # Patched logger instance
                result = wcha_logic.harvest_from_url("http://example.com/anyurl")

                self.assertIsNotNone(result)
                self.assertIsNone(result["content"])
                self.assertEqual(result["error_type"], wcha_logic.WCHA_ERROR_TYPE_LIB_MISSING)
                self.assertIn("trafilatura", result["error_message"])

                # Corrected log assertion format
                expected_log_message = f"[WCHA_LOGIC_WEB] Required library missing: trafilatura ({wcha_logic._MISSING_IMPORT_ERROR_TRAFILATURA})"
                mock_log_error.assert_any_call(expected_log_message)
                mock_requests_get.assert_not_called() # Should not attempt requests if primary lib is missing
                mock_bs.assert_not_called()
                mock_trafilatura_extract.assert_not_called()


if __name__ == '__main__':
    unittest.main()
