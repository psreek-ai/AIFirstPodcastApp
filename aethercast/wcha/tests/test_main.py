import unittest
import sys
import os

# Ensure the 'aethercast' directory (which is one level up from 'wcha')
# is in the Python path for absolute imports.
# This is primarily for running tests directly from this file or if the test runner
# doesn't automatically add the project root.
# `python -m unittest discover` from the root should handle this automatically.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
wcha_dir = os.path.dirname(current_script_dir) # aethercast/wcha/
aethercast_dir = os.path.dirname(wcha_dir) # aethercast/
project_root_dir = os.path.dirname(aethercast_dir) # repo root

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

# Updated imports
from unittest import mock # Used as decorator and for Mock object
import requests # For requests.exceptions

from aethercast.wcha.main import harvest_content, SIMULATED_WEB_CONTENT, harvest_from_url

class TestHarvestContent(unittest.TestCase):

    def test_known_topic(self):
        """Test harvesting content for a known topic."""
        topic = "ai in healthcare"
        expected_content = SIMULATED_WEB_CONTENT.get(topic)
        self.assertIsNotNone(expected_content, "Test setup error: Known topic not found in SIMULATED_WEB_CONTENT.")
        
        result = harvest_content(topic)
        self.assertEqual(result, expected_content)

    def test_unknown_topic(self):
        """Test harvesting content for an unknown topic."""
        topic = "underwater basket weaving techniques"
        expected_message = f"No pre-defined content found for topic: {topic}"
        result = harvest_content(topic)
        self.assertEqual(result, expected_message)

    def test_case_insensitivity_and_spacing(self):
        """Test that topic matching is case-insensitive and handles extra spacing."""
        topic_variations = [
            "Ai In Healthcare",
            " ai in healthcare ",
            "AI IN HEALTHCARE"
        ]
        expected_content = SIMULATED_WEB_CONTENT.get("ai in healthcare")
        self.assertIsNotNone(expected_content, "Test setup error: Base topic 'ai in healthcare' not found for sensitivity test.")

        for topic_variation in topic_variations:
            with self.subTest(topic_variation=topic_variation):
                result = harvest_content(topic_variation)
                self.assertEqual(result, expected_content)
    
    def test_empty_topic(self):
        """Test harvesting content with an empty topic string."""
        topic = ""
        expected_message = "No pre-defined content found for topic: "
        if "" in SIMULATED_WEB_CONTENT:
             expected_message = SIMULATED_WEB_CONTENT[""]
        result = harvest_content(topic)
        self.assertEqual(result, expected_message)

    def test_none_topic(self):
        """Test harvesting content with topic as None."""
        expected_message = "No pre-defined content found for topic: "
        if "" in SIMULATED_WEB_CONTENT:
             expected_message = SIMULATED_WEB_CONTENT[""]
        result = harvest_content(None)
        self.assertEqual(result, expected_message)


class TestHarvestFromUrl(unittest.TestCase):

    @mock.patch('aethercast.wcha.main.requests.get')
    def test_successful_fetch_and_parse(self, mock_get):
        """Test successful fetching and parsing of HTML content."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.content = b"<html><head><title>Test Page</title></head><body><p>This is the first paragraph.</p><p>This is the second.</p></body></html>"
        mock_response.headers = {'Content-Type': 'text/html; charset=utf-8'}
        mock_get.return_value = mock_response
        
        url = "http://example.com/testpage"
        result = harvest_from_url(url)
        
        mock_get.assert_called_once_with(url, timeout=10, headers={'User-Agent': 'AethercastFetcher/0.1'})
        self.assertEqual(result, "This is the first paragraph.\n\nThis is the second.")

    @mock.patch('aethercast.wcha.main.requests.get')
    def test_http_error_status_code(self, mock_get):
        """Test handling of HTTP error status codes (e.g., 404)."""
        mock_response = mock.Mock()
        mock_response.status_code = 404
        mock_response.headers = {'Content-Type': 'text/html; charset=utf-8'} # To pass content type check
        mock_get.return_value = mock_response
        
        url = "http://example.com/notfound"
        result = harvest_from_url(url)
        
        self.assertIn("Failed to fetch URL", result)
        self.assertIn("Status code: 404", result)

    @mock.patch('aethercast.wcha.main.requests.get')
    def test_request_exception_timeout(self, mock_get):
        """Test handling of requests.exceptions.Timeout."""
        mock_get.side_effect = requests.exceptions.Timeout("Test timeout")
        
        url = "http://example.com/timeout"
        result = harvest_from_url(url)
        
        self.assertIn("Error fetching URL", result)
        self.assertIn("Timeout after 10 seconds", result) # Message from WCHA's exception handling

    @mock.patch('aethercast.wcha.main.requests.get')
    def test_request_exception_connection_error(self, mock_get):
        """Test handling of requests.exceptions.ConnectionError."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Test connection error")
        
        url = "http://example.com/connection_error"
        result = harvest_from_url(url)
        
        self.assertIn("Error fetching URL", result)
        self.assertIn("ConnectionError", result)

    @mock.patch('aethercast.wcha.main.requests.get')
    def test_non_html_content_type(self, mock_get):
        """Test handling of non-HTML content types."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'application/json'}
        mock_get.return_value = mock_response
        
        url = "http://example.com/api/data"
        result = harvest_from_url(url)
        
        self.assertIn("Content at URL", result)
        self.assertIn("is not HTML", result)
        self.assertIn("Content-Type: application/json", result)

    @mock.patch('aethercast.wcha.main.requests.get')
    def test_no_paragraph_tags_found(self, mock_get):
        """Test handling of HTML content with no <p> tags."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.content = b"<html><body><h1>A Page Without Paragraphs</h1><div>Some content here.</div></body></html>"
        mock_response.headers = {'Content-Type': 'text/html; charset=utf-8'}
        mock_get.return_value = mock_response
        
        url = "http://example.com/noparagraphs"
        result = harvest_from_url(url)
        
        self.assertEqual(result, f"No paragraph text found at URL: {url}")

    @mock.patch('aethercast.wcha.main.IMPORTS_SUCCESSFUL', False)
    @mock.patch('aethercast.wcha.main.MISSING_IMPORT_ERROR', "Simulated ImportError for testing")
    def test_imports_not_successful(self):
        """Test behavior when IMPORTS_SUCCESSFUL is False."""
        # No need to mock requests.get here as IMPORTS_SUCCESSFUL check should be first
        
        url = "http://example.com/anyurl"
        result = harvest_from_url(url)
        
        self.assertIn("Cannot 'harvest_from_url' because required libraries are missing", result)
        self.assertIn("Simulated ImportError for testing", result)


if __name__ == '__main__':
    unittest.main()
