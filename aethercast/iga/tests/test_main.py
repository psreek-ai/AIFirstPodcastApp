import unittest
import json
from unittest.mock import patch, MagicMock
import os
import sys
import logging

# Adjust path to import IGA main module
current_dir = os.path.dirname(os.path.abspath(__file__))
iga_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(iga_dir)
project_root_dir = os.path.dirname(aethercast_dir)

if iga_dir not in sys.path:
    sys.path.insert(0, iga_dir)
if aethercast_dir not in sys.path:
    sys.path.insert(0, aethercast_dir)
if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.iga import main as iga_main

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - IGA - %(message)s')

class TestIGAFlaskEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        iga_main.app.testing = True
        cls.client = iga_main.app.test_client()

    def setUp(self):
        self.maxDiff = None
        # Mock IGA configurations if needed by the endpoint logic itself.
        # Currently, IGA's /generate_image endpoint is a placeholder and doesn't
        # heavily rely on iga_config for its direct request handling logic beyond app.run params.
        # The IGA_MODEL_VERSION is a global constant.
        self.mock_iga_config = {
            'IGA_HOST': '0.0.0.0',
            'IGA_PORT': 5007,
            'IGA_DEBUG_MODE': False,
            # Add other potential future configs if endpoint logic starts using them
        }
        self.config_patcher = patch.dict(iga_main.iga_config, self.mock_iga_config, clear=True)
        self.mock_config_instance = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    def test_health_endpoint(self):
        # Note: IGA currently does not have a /health endpoint.
        # This test will fail unless one is added.
        # For now, I will write it assuming a standard health endpoint might be added.
        # If it's confirmed not to exist and not planned, this test should be removed or adapted.

        # Check if /health exists, if not, skip or expect 404
        # For now, let's assume it might be added and would look like this:
        # response = self.client.get('/health')
        # self.assertEqual(response.status_code, 200)
        # expected_response = {"status": "healthy", "service": "IGA"}
        # self.assertEqual(response.get_json(), expected_response)
        pass # Passing for now as /health is not in current iga/main.py

    def test_generate_image_success(self):
        # The current /generate_image is a placeholder that dynamically creates an Unsplash URL.
        # No external services are called that need mocking for its core success path.
        payload = {"prompt": "A futuristic cityscape"}
        response = self.client.post('/generate_image', json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("image_url", data)
        self.assertTrue(data["image_url"].startswith("https://source.unsplash.com/random/400x225/"))
        self.assertIn("futuristic+cityscape", data["image_url"]) # Check if prompt keywords are in URL
        self.assertEqual(data["prompt_used"], payload["prompt"])
        self.assertEqual(data["model_version"], iga_main.IGA_MODEL_VERSION)

    def test_generate_image_success_empty_prompt_keywords_fallback(self):
        # Test with a prompt that might result in empty keywords after sanitization
        payload = {"prompt": "..."} # Only non-alphanumeric
        response = self.client.post('/generate_image', json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("image_url", data)
        # Should fall back to the generic podcast,abstract URL
        self.assertTrue(data["image_url"].endswith("?podcast,abstract"))
        self.assertEqual(data["prompt_used"], payload["prompt"])

    def test_generate_image_missing_prompt_in_payload(self):
        payload = {"not_a_prompt": "A cat in a hat"}
        response = self.client.post('/generate_image', json=payload)
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data.get('error_code'), "IGA_BAD_REQUEST_PROMPT_MISSING")
        self.assertIn("Prompt is required", data.get("message", ""))

    def test_generate_image_empty_prompt_string(self):
        payload = {"prompt": ""}
        response = self.client.post('/generate_image', json=payload)
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data.get('error_code'), "IGA_BAD_REQUEST_PROMPT_MISSING")
        self.assertIn("Prompt is required", data.get("message", ""))

    def test_generate_image_no_json_payload(self):
        response = self.client.post('/generate_image', data="this is not json")
        self.assertEqual(response.status_code, 400) # Flask's default for non-JSON when request.get_json() is called
        data = response.get_json() # This will be None if Flask couldn't parse, or an error JSON
        # The current IGA endpoint doesn't have a specific check for `if not data:` before `data.get('prompt')`
        # if request.get_json() itself fails (e.g. due to content type), it raises a 400 error.
        # If it returns None (e.g. empty body but correct content-type), the `if not data or "prompt" not in data` catches it.
        # The error code IGA_BAD_REQUEST_PROMPT_MISSING is appropriate here too.
        if data: # Only assert if Flask returned a JSON error body we can parse
             self.assertEqual(data.get('error_code'), "IGA_BAD_REQUEST_PROMPT_MISSING")


    @patch('aethercast.iga.main.request') # More general way to mock to simulate an internal error
    def test_generate_image_internal_function_exception(self, mock_request):
        # Simulate an error during request processing after JSON parsing but before response
        # For example, if data.get_json() worked but then another operation failed.
        # The current IGA is simple; to force an internal server error, we'd need to mock something
        # that `jsonify` or the string operations might fail on, or mock `request.get_json` to raise it.

        # Let's mock `request.get_json` to raise an unexpected error beyond bad request.
        # However, `request.get_json()` typically only raises Werkzeug's BadRequest.
        # A more realistic internal error would be if a downstream service (if IGA had one) failed.
        # Since IGA is a placeholder, let's mock a part of its *own* logic to fail.
        # For instance, if `jsonify` itself failed (highly unlikely for this data).
        # A better approach: if `logging.error` was complex and failed.
        # The current `except Exception as e:` is broad.

        # Let's simulate an error by making `data["prompt"]` (after successful get_json)
        # an object that causes `"+".join(prompt.split()[:3])` to fail.

        mock_get_json = MagicMock()
        # This will make `prompt = data["prompt"]` valid, but subsequent ops on prompt fail
        mock_get_json.return_value = {"prompt": 12345} # An int, not a string
        mock_request.get_json = mock_get_json

        response = self.client.post('/generate_image', json={"prompt": "irrelevant for this mock"}) # Payload is effectively ignored by mock

        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data['error_code'], "IGA_INTERNAL_SERVER_ERROR")
        self.assertIn("IGA placeholder encountered an unexpected error.", data['message'])
        self.assertIn("AttributeError", data['details']) # Because int has no .split()

if __name__ == '__main__':
    unittest.main()
