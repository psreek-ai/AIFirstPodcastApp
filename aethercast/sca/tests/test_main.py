import unittest
from unittest.mock import patch, MagicMock
import json
import os

# Ensure the SCA main module can be imported
from aethercast.sca import main as sca_main

class TestCraftSnippetEndpoint(unittest.TestCase):

    def setUp(self):
        sca_main.app.config['TESTING'] = True
        self.client = sca_main.app.test_client()

        # Default mock config for most tests
        self.mock_sca_config = {
            "AIMS_SERVICE_URL": "http://mockaims.test/v1/generate",
            "AIMS_REQUEST_TIMEOUT_SECONDS": 10,
            "SCA_LLM_MODEL_ID": "test-model-sca",
            "SCA_LLM_MAX_TOKENS_SNIPPET": 100,
            "SCA_LLM_TEMPERATURE_SNIPPET": 0.5,
            "USE_REAL_LLM_SERVICE": False # Default to placeholder for many tests
        }
        self.config_patcher = patch.dict(sca_main.sca_config, self.mock_sca_config)
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    def test_craft_snippet_missing_payload(self):
        response = self.client.post('/craft_snippet', data=None, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data['error_code'], 'SCA_INVALID_PAYLOAD')

    def test_craft_snippet_missing_fields(self):
        response = self.client.post('/craft_snippet', json={'topic_id': 't1'})
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data['error_code'], 'SCA_MISSING_FIELDS')
        self.assertIn("'content_brief' are required", json_data['message'])

    def test_craft_snippet_placeholder_success(self):
        # USE_REAL_LLM_SERVICE is False by default from setUp
        payload = {
            "topic_id": "topic_placeholder_test",
            "content_brief": "Brief for placeholder",
            "topic_info": {"title_suggestion": "Placeholder Topic Suggestion"}
        }
        response = self.client.post('/craft_snippet', json=payload)
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()

        self.assertTrue(json_data['snippet_id'].startswith("snippet_"))
        self.assertEqual(json_data['topic_id'], "topic_placeholder_test")
        self.assertIn("Placeholder Topic Suggestion", json_data['title']) # Placeholder title is dynamic
        self.assertIn("placeholder response", json_data['summary'].lower()) # Placeholder content
        self.assertEqual(json_data['llm_model_used'], "AetherLLM-Placeholder-DynamicSnippet-v0.2")

    @patch('requests.post')
    def test_craft_snippet_aims_success(self, mock_requests_post):
        # Override config for this test to use "real" AIMS
        with patch.dict(sca_main.sca_config, {"USE_REAL_LLM_SERVICE": True}):
            aims_llm_text_output = "AIMS Generated Title\nAIMS generated content for the snippet."
            mock_aims_response = MagicMock()
            mock_aims_response.status_code = 200
            mock_aims_response.json.return_value = {
                "request_id": "aims_req_sca_success",
                "model_id": "aims-model-dynamic",
                "choices": [{"text": aims_llm_text_output, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50}
            }
            mock_requests_post.return_value = mock_aims_response

            payload = {
                "topic_id": "topic_aims_ok",
                "content_brief": "AIMS success brief",
                "topic_info": {"title_suggestion": "AIMS Success Suggestion"}
            }
            response = self.client.post('/craft_snippet', json=payload)
            self.assertEqual(response.status_code, 200)
            json_data = response.get_json()

            mock_requests_post.assert_called_once()
            called_url = mock_requests_post.call_args[0][0]
            called_payload = mock_requests_post.call_args[1]['json']
            self.assertEqual(called_url, self.mock_sca_config['AIMS_SERVICE_URL'])
            self.assertIn("AIMS success brief", called_payload['prompt'])
            self.assertEqual(called_payload['model_id_override'], self.mock_sca_config['SCA_LLM_MODEL_ID'])

            self.assertEqual(json_data['title'], "AIMS Generated Title")
            self.assertEqual(json_data['summary'], "AIMS generated content for the snippet.")
            self.assertEqual(json_data['llm_model_used'], "aims-model-dynamic")
            self.assertEqual(json_data['topic_id'], "topic_aims_ok")

    @patch('requests.post')
    def test_craft_snippet_aims_http_error(self, mock_requests_post):
        with patch.dict(sca_main.sca_config, {"USE_REAL_LLM_SERVICE": True}):
            mock_aims_response = MagicMock()
            mock_aims_response.status_code = 500
            mock_aims_response.reason = "AIMS Internal Server Error"
            mock_aims_response.text = '{"error": {"type": "aims_internal_error", "message": "AIMS exploded"}}'
            def json_raise(): raise json.JSONDecodeError("err", "doc", 0)
            mock_aims_response.json.side_effect = json_raise # Simulate non-JSON error payload for one path

            # Simulate requests.post raising an HTTPError
            mock_requests_post.side_effect = requests.exceptions.HTTPError(response=mock_aims_response)

            payload = {"topic_id": "topic_aims_http_err", "content_brief": "AIMS HTTP error test"}
            response = self.client.post('/craft_snippet', json=payload)

            self.assertEqual(response.status_code, 500) # Should reflect AIMS error or SCA's interpretation
            json_data = response.get_json()
            self.assertEqual(json_data['error_code'], 'SCA_AIMS_HTTP_ERROR')
            self.assertIn("AIMS HTTP Error 500", json_data['details'])
            self.assertIn("AIMS Internal Server Error", json_data['details'])
            self.assertIn("AIMS exploded", json_data['details']) # Check if raw text is included

    @patch('requests.post')
    def test_craft_snippet_aims_timeout(self, mock_requests_post):
        with patch.dict(sca_main.sca_config, {"USE_REAL_LLM_SERVICE": True}):
            mock_requests_post.side_effect = requests.exceptions.Timeout("AIMS request timed out")

            payload = {"topic_id": "topic_aims_timeout", "content_brief": "AIMS timeout test"}
            response = self.client.post('/craft_snippet', json=payload)
            self.assertEqual(response.status_code, 408) # Request Timeout
            json_data = response.get_json()
            self.assertEqual(json_data['error_code'], 'SCA_AIMS_REQUEST_TIMEOUT')
            self.assertIn("Request to AIMS timed out", json_data['message'])

    @patch('requests.post')
    def test_craft_snippet_aims_bad_response_structure(self, mock_requests_post):
        with patch.dict(sca_main.sca_config, {"USE_REAL_LLM_SERVICE": True}):
            mock_aims_response = MagicMock()
            mock_aims_response.status_code = 200
            mock_aims_response.json.return_value = {"model_id": "aims_bad_struct", "choices": [{"wrong_key": "no text here"}]} # Missing 'text'
            mock_requests_post.return_value = mock_aims_response

            payload = {"topic_id": "topic_aims_bad_struct", "content_brief": "AIMS bad structure"}
            response = self.client.post('/craft_snippet', json=payload)
            self.assertEqual(response.status_code, 500) # SCA internal error due to bad structure from AIMS
            json_data = response.get_json()
            self.assertEqual(json_data['error_code'], 'SCA_AIMS_BAD_RESPONSE_STRUCTURE')
            self.assertIn("Missing 'choices[0].text'", json_data['details'])

    def test_craft_snippet_simulated_sca_error(self):
        # Test the error_trigger mechanism
        payload = {
            "topic_id": "topic_sim_error",
            "content_brief": "Simulate SCA internal error",
            "error_trigger": "sca_error"
        }
        response = self.client.post('/craft_snippet', json=payload)
        self.assertEqual(response.status_code, 500)
        json_data = response.get_json()
        self.assertEqual(json_data['error_code'], 'SCA_SIMULATED_ERROR')
        self.assertIn("simulated error occurred in SCA", json_data['message'].lower())

if __name__ == '__main__':
    unittest.main(verbosity=2)
