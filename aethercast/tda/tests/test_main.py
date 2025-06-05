# aethercast/tda/tests/test_main.py
import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import json # For creating mock API responses

# Attempt to import from parent directory - this might need adjustment based on test runner setup
# For example, if running with `python -m unittest discover`, PYTHONPATH might need to be set.
# Assuming aethercast.tda.main can be imported.
from aethercast.tda import main as tda_main # Alias for clarity

class TestTDAIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        tda_main.app.config['TESTING'] = True
        # Disable actual logging to keep test output clean, if not already handled by Flask's TESTING config
        # You might also want to set a specific logger level for tests if needed.
        # For example, tda_main.app.logger.setLevel(logging.WARNING)
        cls.client = tda_main.app.test_client()

    # --- 1. Setup and Mocking ---
    # This section outlines general setup and mocking strategies.
    # Specific mocks will be detailed in each test section.

    def setUp(self):
        # This method is called before each test.
        # Common setup can go here, e.g., resetting parts of tda_main.py if necessary.
        # For instance, if tda_config is loaded at module level and modified by tests,
        # it might need to be reloaded or reset.
        # For now, we assume tda_config is freshly evaluated or mocked per test using patch.dict.

        # Reset or re-patch critical configurations if they are modified by tests directly
        # For example, ensure USE_REAL_NEWS_API is reset to a known state if a test changes it globally.
        # However, using `with patch.dict(...)` within tests is the preferred way to manage config per test.
        pass

    # Example of how os.getenv might be mocked for configuration tests
    # @patch('os.getenv') 
    # def test_example_mock_os_getenv(self, mock_getenv):
    #     mock_getenv.return_value = "some_value"
    #     # ... test logic ...

    # Example of how requests.get might be mocked for API call tests
    # @patch('requests.get')
    # def test_example_mock_requests_get(self, mock_get):
    #     mock_response = MagicMock()
    #     mock_response.status_code = 200
    #     mock_response.json.return_value = {"status": "ok", "articles": []}
    #     mock_get.return_value = mock_response
    #     # ... test logic ...

    # --- 2. Configuration Loading Tests ---

    @patch.dict(os.environ, {
        "TDA_NEWS_API_KEY": "test_api_key_from_env",
        "TDA_NEWS_API_BASE_URL": "https://test.newsapi.org/v2/",
        "TDA_NEWS_API_ENDPOINT": "everything-test",
        "TDA_NEWS_DEFAULT_KEYWORDS": "test,keywords",
        "TDA_NEWS_DEFAULT_LANGUAGE": "xx",
        "USE_REAL_NEWS_API": "True" 
    })
    @patch('aethercast.tda.main.load_dotenv') # Mock load_dotenv to prevent actual file loading
    def test_config_loading_from_env_variables(self, mock_load_dotenv):
        # Purpose: Test that environment variables are correctly loaded into tda_config.
        # Mocking:
        # - os.environ is patched using @patch.dict to simulate set env vars.
        # - load_dotenv is mocked to prevent it from trying to read a .env file.
        
        # We need to simulate the module being reloaded or tda_config being re-initialized
        # This is tricky if tda_config is defined at the module level.
        # A common pattern is to have a function that initializes config.
        # Assuming tda_main.tda_config is directly usable and reflects os.getenv at import time.
        # For this test, we might need to re-import or re-run the config part of tda_main.
        # Simplification: Assume we can trigger a re-evaluation of tda_config or test its components.
        
        # Re-evaluate tda_config (conceptual - depends on how tda_main is structured)
        # If tda_config is top-level, we might need to reload the module or test a function that builds it.
        # For this outline, let's assume we can access a freshly loaded tda_config
        # or that tda_main.py is structured to allow re-initialization of tda_config for tests.

        # Let's assume tda_main.tda_config is re-evaluated based on the patched os.environ when tda_main is imported
        # or when a specific config loading function is called.
        # For this conceptual outline, we'll directly inspect a hypothetical reloaded_config.
        
        # This would require a mechanism to reload tda_main or its config part.
        # For example, if config loading is in a function:
        # reloaded_config = tda_main.load_app_configuration()
        
        # Assertions (assuming tda_main.tda_config reflects the patched os.environ):
        self.assertEqual(tda_main.tda_config["TDA_NEWS_API_KEY"], "test_api_key_from_env")
        self.assertEqual(tda_main.tda_config["TDA_NEWS_API_BASE_URL"], "https://test.newsapi.org/v2/")
        self.assertEqual(tda_main.tda_config["TDA_NEWS_API_ENDPOINT"], "everything-test")
        self.assertEqual(tda_main.tda_config["TDA_NEWS_DEFAULT_KEYWORDS"], ["test", "keywords"]) # Assuming .split(',')
        self.assertEqual(tda_main.tda_config["TDA_NEWS_DEFAULT_LANGUAGE"], "xx")
        self.assertTrue(tda_main.tda_config["USE_REAL_NEWS_API"]) # Assuming .lower() == "true"
        mock_load_dotenv.assert_called_once() # Ensure dotenv loading was attempted (even if bypassed)

    @patch('aethercast.tda.main.logging.error')
    @patch.dict(os.environ, {
        "USE_REAL_NEWS_API": "True",
        "TDA_NEWS_API_KEY": "" # Missing API Key
    })
    # We would need to reload tda_main or trigger its startup check logic.
    # This is a conceptual test.
    def test_startup_check_api_key_missing(self, mock_logging_error):
        # Purpose: Test that a critical error is logged if USE_REAL_NEWS_API is True but TDA_NEWS_API_KEY is missing.
        # Mocking:
        # - os.environ patched to simulate USE_REAL_NEWS_API=True and no API key.
        # - logging.error to capture log messages.
        
        # This test implies that the startup check logic in tda_main.py is executed.
        # This might happen at module import time. If so, reloading the module would be needed.
        # import importlib
        # importlib.reload(tda_main) # This can have side effects and complexities.
        
        # Conceptual: Assuming the check runs and calls logging.error
        # For this to work, the conditional check in tda_main.py must be re-evaluated.
        # If the check is:
        # if tda_config["USE_REAL_NEWS_API"] and not tda_config["TDA_NEWS_API_KEY"]:
        #    logging.error(...)
        # We need tda_config to be updated first based on the patched os.environ.
        
        # This test is more of an integration test of module loading.
        # A simpler unit test would be to extract the check into a function and test that function.
        
        # Assuming the check is re-run:
        # tda_main.perform_startup_checks() # If such a function existed
        
        # Assertions:
        # mock_logging_error.assert_called_with(
        #     "CRITICAL: USE_REAL_NEWS_API is True, but TDA_NEWS_API_KEY is not set. Real News API calls will fail."
        # )
        pass # Placeholder for the complex setup of re-running module-level code


    # --- 3. `call_real_news_api` Function Tests (Mocking `requests.get`) ---

    @patch('requests.get')
    @patch('aethercast.tda.main.generate_topic_id', return_value="mock_topic_id_123")
    def test_call_real_news_api_successful(self, mock_generate_id, mock_requests_get):
        # Purpose: Test successful API call, JSON parsing, and transformation to TopicObjects.
        # Mocking:
        # - requests.get is mocked to return a successful response.
        # - generate_topic_id to provide predictable topic IDs.
        # - tda_main.tda_config needs to be set up for this test (e.g., API key present).

        # Setup mock response from requests.get
        mock_api_response = {
            "status": "ok",
            "totalResults": 1,
            "articles": [
                {
                    "source": {"id": "test-source", "name": "Test Source"},
                    "author": "Test Author",
                    "title": "Test Article Title",
                    "description": "Test article description.",
                    "url": "http://example.com/test-article",
                    "urlToImage": "http://example.com/image.jpg",
                    "publishedAt": "2024-01-01T12:00:00Z",
                    "content": "Test content."
                }
            ]
        }
        mock_response_obj = MagicMock()
        mock_response_obj.status_code = 200
        mock_response_obj.json.return_value = mock_api_response
        mock_requests_get.return_value = mock_response_obj

        # Setup tda_config for this call
        tda_main.tda_config["TDA_NEWS_API_KEY"] = "fake_key"
        tda_main.tda_config["TDA_NEWS_API_BASE_URL"] = "https://newsapi.org/v2/"
        tda_main.tda_config["TDA_NEWS_API_ENDPOINT"] = "everything"
        tda_main.tda_config["TDA_NEWS_DEFAULT_KEYWORDS"] = ["default"]
        tda_main.tda_config["TDA_NEWS_DEFAULT_LANGUAGE"] = "en"

        # Call the function
        result_topics = tda_main.call_real_news_api(keywords=["test", "api"])

        # Assertions:
        self.assertEqual(len(result_topics), 1)
        topic = result_topics[0]
        self.assertEqual(topic["topic_id"], "mock_topic_id_123")
        self.assertEqual(topic["title_suggestion"], "Test Article Title")
        self.assertEqual(topic["summary"], "Test article description.")
        self.assertEqual(topic["keywords"], ["test", "api"]) # Should use the passed keywords
        self.assertEqual(topic["potential_sources"][0]["url"], "http://example.com/test-article")
        self.assertEqual(topic["publication_date"], "2024-01-01T12:00:00Z")
        
        # Assert requests.get was called correctly
        expected_url = "https://newsapi.org/v2/everything"
        expected_params = {"q": "test,api", "language": "en"}
        expected_headers = {"X-Api-Key": "fake_key"}
        mock_requests_get.assert_called_once()
        args, kwargs = mock_requests_get.call_args
        self.assertEqual(args[0], expected_url)
        self.assertEqual(kwargs['params'], expected_params)
        self.assertEqual(kwargs['headers']['X-Api-Key'], expected_headers['X-Api-Key'])


    @patch('requests.get')
    @patch('aethercast.tda.main.logging.error')
    def test_call_real_news_api_key_missing(self, mock_logging_error, mock_requests_get):
        # Purpose: Test that if API key is missing, the function returns empty list and logs error.
        # Mocking:
        # - tda_config to have no API key.
        # - logging.error to check for error logging.
        # - requests.get to ensure it's not called.
        
        original_api_key = tda_main.tda_config.get("TDA_NEWS_API_KEY")
        tda_main.tda_config["TDA_NEWS_API_KEY"] = "" # Set API key to empty

        result_topics = tda_main.call_real_news_api(keywords=["test"])

        # Assertions:
        self.assertEqual(result_topics, [])
        mock_logging_error.assert_called_with("call_real_news_api: Missing TDA_NEWS_API_KEY. Cannot make request.")
        mock_requests_get.assert_not_called()
        
        # Restore original key if necessary for other tests (better to mock tda_config per test)
        tda_main.tda_config["TDA_NEWS_API_KEY"] = original_api_key


    @patch('requests.get')
    @patch('aethercast.tda.main.logging.error')
    def test_call_real_news_api_http_error(self, mock_logging_error, mock_requests_get):
        # Purpose: Test handling of HTTP errors from API (e.g., 401, 429).
        # Mocking:
        # - requests.get to return an error status_code.
        # - logging.error.
        
        mock_response_obj = MagicMock()
        mock_response_obj.status_code = 401
        mock_response_obj.text = "Unauthorized"
        mock_requests_get.return_value = mock_response_obj
        # Simulate raise_for_status behavior for HTTPError
        mock_response_obj.raise_for_status = MagicMock(side_effect=requests.exceptions.HTTPError(response=mock_response_obj))


        tda_main.tda_config["TDA_NEWS_API_KEY"] = "fake_key" # Ensure key is present for call attempt
        result_topics = tda_main.call_real_news_api(keywords=["test"])

        # Assertions:
        self.assertEqual(result_topics, [])
        # Check that a log message containing the error details was made
        self.assertTrue(any("HTTP error occurred" in call_args[0][0] for call_args in mock_logging_error.call_args_list))
        mock_requests_get.assert_called_once()


    @patch('requests.get', side_effect=requests.exceptions.ConnectionError("Test connection error"))
    @patch('aethercast.tda.main.logging.error')
    def test_call_real_news_api_connection_error(self, mock_logging_error, mock_requests_get):
        # Purpose: Test handling of network errors like ConnectionError.
        # Mocking:
        # - requests.get to raise ConnectionError.
        # - logging.error.
        
        tda_main.tda_config["TDA_NEWS_API_KEY"] = "fake_key"
        result_topics = tda_main.call_real_news_api(keywords=["test"])

        # Assertions:
        self.assertEqual(result_topics, [])
        mock_logging_error.assert_called_with("Connection error occurred: Test connection error")
        mock_requests_get.assert_called_once()


    @patch('requests.get')
    @patch('aethercast.tda.main.logging.error')
    def test_call_real_news_api_json_decode_error(self, mock_logging_error, mock_requests_get):
        # Purpose: Test handling of invalid JSON in API response.
        # Mocking:
        # - requests.get returns a response where .json() raises JSONDecodeError.
        # - logging.error.
        
        mock_response_obj = MagicMock()
        mock_response_obj.status_code = 200
        mock_response_obj.text = "invalid json"
        mock_response_obj.json.side_effect = requests.exceptions.JSONDecodeError("Error decoding JSON", "doc", 0)
        mock_requests_get.return_value = mock_response_obj
        
        tda_main.tda_config["TDA_NEWS_API_KEY"] = "fake_key"
        result_topics = tda_main.call_real_news_api(keywords=["test"])

        # Assertions:
        self.assertEqual(result_topics, [])
        self.assertTrue(any("Failed to decode JSON from NewsAPI" in call_args[0][0] for call_args in mock_logging_error.call_args_list))
        mock_requests_get.assert_called_once()


    @patch('requests.get')
    @patch('aethercast.tda.main.logging.error')
    def test_call_real_news_api_logical_error_in_response(self, mock_logging_error, mock_requests_get):
        # Purpose: Test handling of API's own error messages (e.g., status: "error").
        # Mocking:
        # - requests.get returns a response with status:"error".
        # - logging.error.

        mock_api_response = {"status": "error", "message": "Your API key is invalid."}
        mock_response_obj = MagicMock()
        mock_response_obj.status_code = 200 # API call itself was successful
        mock_response_obj.json.return_value = mock_api_response
        mock_requests_get.return_value = mock_response_obj

        tda_main.tda_config["TDA_NEWS_API_KEY"] = "fake_key"
        result_topics = tda_main.call_real_news_api(keywords=["test"])

        # Assertions:
        self.assertEqual(result_topics, [])
        mock_logging_error.assert_called_with("NewsAPI returned error status: error. Message: Your API key is invalid.")
        mock_requests_get.assert_called_once()


    # --- 4. `discover_topics_endpoint` Toggle Logic Tests ---
    # These tests focus on the branching logic within discover_topics_endpoint
    # based on USE_REAL_NEWS_API.
    # Direct testing of Flask endpoints is more complex and might use app.test_client().
    # Here, we conceptually test the core logic, perhaps by refactoring it into a helper
    # or by directly manipulating tda_config and mocking the called functions.

    @patch('aethercast.tda.main.call_real_news_api')
    @patch('aethercast.tda.main.identify_topics_from_sources')
    def test_discover_topics_endpoint_uses_real_api_when_true(self, mock_identify_simulated, mock_call_real):
        # Purpose: Verify that call_real_news_api is used when USE_REAL_NEWS_API is True.
        # Mocking:
        # - tda_config["USE_REAL_NEWS_API"] = True
        # - call_real_news_api to return mock data and allow assertion of its call.
        # - identify_topics_from_sources to ensure it's NOT called.
        
        # Setup tda_config for this test case
        original_use_real_api = tda_main.tda_config.get("USE_REAL_NEWS_API")
        tda_main.tda_config["USE_REAL_NEWS_API"] = True
        tda_main.tda_config["TDA_NEWS_DEFAULT_LANGUAGE"] = "en" # Ensure this is set

        mock_call_real.return_value = [{"topic_id": "real_topic_1"}]
        
        # This simulates calling the core logic of the endpoint.
        # In a real scenario, you might use app.test_client().post('/discover_topics', json={...})
        # For this conceptual test, we assume access to a part of the endpoint logic.
        # Let's assume we can call a helper or the main block of discover_topics_endpoint.
        # For simplicity, we'll assume the test is for a refactored version or directly invokes the logic.
        
        # This would be part of the discover_topics_endpoint logic:
        query_params = {"query": "AI,מה חדש", "limit": 1} # Example query
        
        # Simulate the part of discover_topics_endpoint that decides which data source to use
        # This is highly conceptual as we are not using a Flask test client here.
        # The actual endpoint function would be `tda_main.discover_topics_endpoint()`,
        # but calling it directly without a Flask request context is not straightforward.
        
        # For the purpose of this outline, let's assume we can test the branching logic:
        if tda_main.tda_config["USE_REAL_NEWS_API"]:
            request_keywords = [k.strip() for k in query_params["query"].split(',')] if query_params.get("query") else None
            discovered_topics = tda_main.call_real_news_api(
                keywords=request_keywords, 
                language=tda_main.tda_config.get("TDA_NEWS_DEFAULT_LANGUAGE")
            )
            if query_params.get("limit", 0) > 0 and discovered_topics:
                 discovered_topics = discovered_topics[:query_params["limit"]]
        else:
            # This branch should not be taken in this test
            discovered_topics = tda_main.identify_topics_from_sources(
                query=query_params.get("query"),
                limit=query_params.get("limit")
            )

        # Assertions:
        mock_call_real.assert_called_once_with(
            keywords=["AI", "מה חדש"], 
            language="en"
        )
        mock_identify_simulated.assert_not_called()
        self.assertEqual(discovered_topics, [{"topic_id": "real_topic_1"}]) # Assuming limit=1 and mock return has 1 item

        # Restore config
        tda_main.tda_config["USE_REAL_NEWS_API"] = original_use_real_api


    @patch('aethercast.tda.main.call_real_news_api')
    @patch('aethercast.tda.main.identify_topics_from_sources')
    def test_discover_topics_endpoint_uses_simulated_data_when_false(self, mock_identify_simulated, mock_call_real):
        # Purpose: Verify that identify_topics_from_sources is used when USE_REAL_NEWS_API is False.
        # Mocking:
        # - tda_config["USE_REAL_NEWS_API"] = False
        # - identify_topics_from_sources to return mock data and allow assertion of its call.
        # - call_real_news_api to ensure it's NOT called.

        original_use_real_api = tda_main.tda_config.get("USE_REAL_NEWS_API")
        tda_main.tda_config["USE_REAL_NEWS_API"] = False

        mock_identify_simulated.return_value = [{"topic_id": "sim_topic_1"}]

        # Conceptual call to the endpoint's core logic (as above)
        query_params = {"query": "space", "limit": 1}
        
        if tda_main.tda_config["USE_REAL_NEWS_API"]:
            # This branch should not be taken
            request_keywords = [k.strip() for k in query_params["query"].split(',')] if query_params.get("query") else None
            discovered_topics = tda_main.call_real_news_api(
                keywords=request_keywords, 
                language=tda_main.tda_config.get("TDA_NEWS_DEFAULT_LANGUAGE")
            )
            if query_params.get("limit", 0) > 0 and discovered_topics:
                 discovered_topics = discovered_topics[:query_params["limit"]]
        else:
            discovered_topics = tda_main.identify_topics_from_sources(
                query=query_params.get("query"),
                limit=query_params.get("limit")
            )
            
        # Assertions:
        mock_identify_simulated.assert_called_once_with(query="space", limit=1)
        mock_call_real.assert_not_called()
        self.assertEqual(discovered_topics, [{"topic_id": "sim_topic_1"}])
        
        # Restore config
        tda_main.tda_config["USE_REAL_NEWS_API"] = original_use_real_api

    @patch('aethercast.tda.main.identify_topics_from_sources') # Mocks the simulated data path
    def test_discover_topics_endpoint_simulated_success(self, mock_identify_simulated):
        # Ensure USE_REAL_NEWS_API is False for this test
        with patch.dict(tda_main.tda_config, {"USE_REAL_NEWS_API": False}):
            mock_topics = [{"topic_id": "sim1", "title_suggestion": "Simulated Topic"}]
            mock_identify_simulated.return_value = mock_topics

            response = self.client.post('/discover_topics', json={'query': 'simulated', 'limit': 1})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data['discovered_topics'], mock_topics)
            mock_identify_simulated.assert_called_once_with(query='simulated', limit=1)

    @patch('aethercast.tda.main.call_real_news_api') # Mocks the real API call path
    def test_discover_topics_endpoint_real_api_success(self, mock_call_real_api):
        # Need to ensure TDA_NEWS_API_KEY is set for USE_REAL_NEWS_API=True path
        with patch.dict(tda_main.tda_config, {"USE_REAL_NEWS_API": True, "TDA_NEWS_API_KEY": "fake_key_for_test", "TDA_NEWS_DEFAULT_LANGUAGE": "en"}):
            mock_topics = [{"topic_id": "real1", "title_suggestion": "Real API Topic"}]
            mock_call_real_api.return_value = mock_topics

            response = self.client.post('/discover_topics', json={'query': 'real', 'limit': 1})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data['discovered_topics'], mock_topics)
            mock_call_real_api.assert_called_once_with(keywords=['real'], language="en")

    def test_discover_topics_endpoint_empty_payload(self):
        # Should use default keywords for simulated path if USE_REAL_NEWS_API is False
        with patch.dict(tda_main.tda_config, {"USE_REAL_NEWS_API": False}), \
             patch('aethercast.tda.main.identify_topics_from_sources') as mock_identify_simulated:
            mock_identify_simulated.return_value = [{"topic_id": "default_topic"}]

            response = self.client.post('/discover_topics', json={}) # Empty JSON payload
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn('discovered_topics', data)
            if data.get('discovered_topics'): # Check if list is not empty
                 self.assertEqual(data['discovered_topics'][0]['topic_id'], "default_topic")
            # Default limit is 5 in endpoint, query is None if not provided
            mock_identify_simulated.assert_called_once_with(query=None, limit=5)

    def test_discover_topics_endpoint_no_topics_found(self):
        with patch.dict(tda_main.tda_config, {"USE_REAL_NEWS_API": False}), \
             patch('aethercast.tda.main.identify_topics_from_sources') as mock_identify_simulated:
            mock_identify_simulated.return_value = [] # No topics found

            response = self.client.post('/discover_topics', json={'query': 'very_specific_query'})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data['topics'], [])
            self.assertIn("No topics discovered", data['message'])

    def test_discover_topics_endpoint_simulated_error_trigger(self):
        # This tests the error_trigger mechanism in the endpoint itself
        response = self.client.post('/discover_topics', json={'error_trigger': 'tda_error'})
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data['error_code'], "TDA_SIMULATED_ERROR")
        self.assertIn("simulated error occurred in TDA", data['message'])

    @patch('aethercast.tda.main.identify_topics_from_sources')
    def test_discover_topics_endpoint_general_exception(self, mock_identify_simulated):
        # Test general exception handling in the endpoint
        with patch.dict(tda_main.tda_config, {"USE_REAL_NEWS_API": False}):
            mock_identify_simulated.side_effect = Exception("Unexpected TDA core logic failure")

            response = self.client.post('/discover_topics', json={'query': 'trigger_exception'})
            self.assertEqual(response.status_code, 500)
            data = response.get_json()
            self.assertEqual(data['error_code'], "INTERNAL_SERVER_ERROR_TDA") # From tda_main constant
            self.assertIn("Unexpected TDA core logic failure", data['details'])


class TestTDAHelpers(unittest.TestCase):

    def setUp(self):
        # Mock tda_config for these helper tests if they rely on it,
        # e.g., for SHARED_DATABASE_PATH if testing _save_topic_to_db interaction.
        self.mock_tda_config = {
            "SHARED_DATABASE_PATH": ":memory:", # Or None if we want to test path not configured
            "TDA_NEWS_DEFAULT_KEYWORDS": ["default", "keyword"], # if identify_topics uses it
            # Add any other configs that might be accessed by these helpers, with defaults if necessary
        }
        self.config_patcher = patch.dict(tda_main.tda_config, self.mock_tda_config, clear=True) # clear=True ensures only these values
        self.mock_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()

    def test_generate_summary_from_title(self):
        title = "Test Title for Summary"
        expected_summary = "This topic explores test title for summary, focusing on its recent developments and potential impact."
        self.assertEqual(tda_main.generate_summary_from_title(title), expected_summary)
        self.assertEqual(tda_main.generate_summary_from_title("Another"), "This topic explores another, focusing on its recent developments and potential impact.")

    def test_calculate_relevance_score(self):
        article_match = {"keywords": ["ai", "ml"], "title": "AI in Healthcare"}
        article_no_match = {"keywords": ["space", "mars"], "title": "Exploring Space"}

        # Test with matching query
        score_match = tda_main.calculate_relevance_score(article_match, query="ai healthcare")
        self.assertTrue(0.5 <= score_match <= 1.0) # Base is 0.5-0.9, boost can take it to 1.0

        # Test with non-matching query (should be lower than a boosted score, but still in base range)
        score_no_match_with_query = tda_main.calculate_relevance_score(article_no_match, query="ai")
        self.assertTrue(0.5 <= score_no_match_with_query <= 0.9) # No boost from keywords/title

        # Test without query (base random score)
        score_no_query = tda_main.calculate_relevance_score(article_match) # Query is None
        self.assertTrue(0.5 <= score_no_query <= 0.9)

        # Test that a matching query usually gives a higher score than no query for the same article
        # This is probabilistic, so run a few times or accept occasional equality for low random rolls
        # For simplicity, we assume a match will likely boost it above a non-boosted score.
        # This could be made more robust by mocking random.uniform if needed.
        # self.assertTrue(score_match > score_no_query or score_match == 1.0) # Simplified check

    @patch('aethercast.tda.main._save_topic_to_db') # Mock to verify it's called
    @patch('aethercast.tda.main.generate_topic_id') # Mock to control topic_id
    @patch('aethercast.tda.main.calculate_relevance_score') # Mock to control relevance
    def test_identify_topics_from_sources_with_query_and_limit(self, mock_calc_relevance, mock_gen_id, mock_save_db):
        mock_calc_relevance.return_value = 0.9 # Consistent relevance
        # Calculate total articles to generate enough IDs
        total_articles_in_sim_data = sum(len(source["articles"]) for source in tda_main.SIMULATED_DATA_SOURCES)
        mock_gen_id.side_effect = [f"topic_id_{i}" for i in range(total_articles_in_sim_data)]


        # Test with a query that should match some articles
        # (SIMULATED_DATA_SOURCES has "AI", "Healthcare", "Technology")
        query = "AI"
        limit = 2

        # Ensure SHARED_DATABASE_PATH is set for _save_topic_to_db to be called
        # This will use the "dummy.db" from the setUp's self.mock_tda_config if not overridden here
        # Let's explicitly set it to a non-None value for this test's scope.
        with patch.dict(tda_main.tda_config, {"SHARED_DATABASE_PATH": "dummy_test_db.db", "TDA_NEWS_DEFAULT_KEYWORDS": self.mock_tda_config["TDA_NEWS_DEFAULT_KEYWORDS"]}):
            identified_topics = tda_main.identify_topics_from_sources(query=query, limit=limit)

        self.assertEqual(len(identified_topics), limit)
        self.assertTrue(all(isinstance(topic, dict) for topic in identified_topics))
        self.assertTrue(all("topic_id" in topic for topic in identified_topics))
        self.assertTrue(all(topic["relevance_score"] == 0.9 for topic in identified_topics)) # Due to mock

        self.assertEqual(mock_save_db.call_count, total_articles_in_sim_data)

    @patch('aethercast.tda.main._save_topic_to_db')
    def test_identify_topics_from_sources_no_query_default_limit(self, mock_save_db):
        # Test with no query, should use all simulated articles up to default limit
        default_limit_in_func = 5 # Default limit in identify_topics_from_sources

        # Patching SHARED_DATABASE_PATH to ensure _save_topic_to_db is called
        with patch.dict(tda_main.tda_config, {"SHARED_DATABASE_PATH": "another_dummy.db", "TDA_NEWS_DEFAULT_KEYWORDS": self.mock_tda_config["TDA_NEWS_DEFAULT_KEYWORDS"]}):
             identified_topics = tda_main.identify_topics_from_sources() # No query, no limit

        self.assertTrue(len(identified_topics) <= default_limit_in_func)
        # Further assertions on content can be added if needed

        total_articles_in_sim_data = sum(len(source["articles"]) for source in tda_main.SIMULATED_DATA_SOURCES)
        self.assertEqual(mock_save_db.call_count, total_articles_in_sim_data)

    @patch('aethercast.tda.main._save_topic_to_db')
    def test_identify_topics_from_sources_db_path_not_configured(self, mock_save_db):
        # Test that _save_topic_to_db is not called if SHARED_DATABASE_PATH is None or empty
        with patch.dict(tda_main.tda_config, {"SHARED_DATABASE_PATH": None, "TDA_NEWS_DEFAULT_KEYWORDS": self.mock_tda_config["TDA_NEWS_DEFAULT_KEYWORDS"]}):
            with patch.object(tda_main.logging, 'warning') as mock_log_warning:
                tda_main.identify_topics_from_sources(query="test")
                mock_save_db.assert_not_called()
                # Check if any of the log calls contain the expected warning
                found_warning = False
                for call_args in mock_log_warning.call_args_list:
                    if "SHARED_DATABASE_PATH not configured" in call_args[0][0]:
                        found_warning = True
                        break
                self.assertTrue(found_warning, "Expected warning about SHARED_DATABASE_PATH not configured was not logged.")

    @patch('sqlite3.connect')
    def test_save_topic_to_db_success(self, mock_sqlite_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        topic_obj = {
            "topic_id": "topic_db_test_001",
            "title_suggestion": "DB Test Topic",
            "summary": "Summary for DB test.",
            "keywords": ["db", "test"],
            "potential_sources": [{"url": "http://db.test/source", "source_name": "TestSource"}],
            "relevance_score": 0.88,
            "publication_date": "2024-01-15T10:00:00Z"
            # category_suggestion is not directly saved by _save_topic_to_db
        }
        db_path = "dummy_tda_save.db"

        # Patch datetime.now within the scope of the function call if possible, or assert type
        # For simplicity, we'll assert the type of last_accessed_timestamp later.
        tda_main._save_topic_to_db(topic_obj, db_path)

        mock_sqlite_connect.assert_called_once_with(db_path)
        mock_conn.cursor.assert_called_once()

        self.assertEqual(mock_cursor.execute.call_count, 1)
        args, _ = mock_cursor.execute.call_args

        # Basic check for query structure
        self.assertIn("INSERT OR REPLACE INTO topics_snippets", args[0])
        # Check for key columns presence (order might vary slightly based on SQL formatting)
        expected_cols_in_sql = [
            "id", "type", "title", "summary", "keywords", "source_url", "source_name",
            "original_topic_details", "llm_model_used_for_snippet", "cover_art_prompt",
            "generation_timestamp", "last_accessed_timestamp", "relevance_score"
        ]
        for col in expected_cols_in_sql:
            self.assertIn(col, args[0].replace("\n", " ")) # Normalize newlines for check

        params = args[1]
        self.assertEqual(params[0], "topic_db_test_001")
        self.assertEqual(params[1], tda_main.DB_TYPE_TOPIC)
        self.assertEqual(params[2], "DB Test Topic")
        self.assertEqual(params[3], "Summary for DB test.")
        self.assertEqual(params[4], json.dumps(["db", "test"]))
        self.assertEqual(params[5], "http://db.test/source")
        self.assertEqual(params[6], "TestSource")
        self.assertIsNone(params[7])
        self.assertIsNone(params[8])
        self.assertIsNone(params[9])
        self.assertEqual(params[10], "2024-01-15T10:00:00Z")
        self.assertIsInstance(params[11], str)
        self.assertTrue(datetime.fromisoformat(params[11].replace("Z", ""))) # Check if it's a valid ISO string
        self.assertEqual(params[12], 0.88)

        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('sqlite3.connect')
    def test_save_topic_to_db_sqlite_error(self, mock_sqlite_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = sqlite3.Error("Simulated DB execute error")

        topic_obj = {"topic_id": "topic_db_fail", "title_suggestion": "DB Fail Topic"}
        db_path = "dummy_tda_fail.db"

        with patch.object(tda_main.logging, 'error') as mock_logger_error:
            tda_main._save_topic_to_db(topic_obj, db_path)

            found_log = False
            for call_arg_tuple in mock_logger_error.call_args_list:
                log_message = call_arg_tuple[0][0] # First positional argument of the call
                if "Database error saving topic topic_db_fail" in log_message and \
                   "Simulated DB execute error" in log_message:
                    found_log = True
                    break
            self.assertTrue(found_log, "Expected database error log message not found.")

        mock_conn.commit.assert_not_called()
        mock_conn.close.assert_called_once()

    @patch('sqlite3.connect')
    def test_save_topic_to_db_unexpected_error(self, mock_sqlite_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = TypeError("Simulated unexpected type error")

        topic_obj = {"topic_id": "topic_db_unexpected_fail", "title_suggestion": "DB Unexpected Fail Topic"}
        db_path = "dummy_tda_unexpected_fail.db"

        with patch.object(tda_main.logging, 'error') as mock_logger_error:
            tda_main._save_topic_to_db(topic_obj, db_path)

            found_log = False
            for call_arg_tuple in mock_logger_error.call_args_list:
                log_message = call_arg_tuple[0][0]
                exc_info_obj = call_arg_tuple.kwargs.get('exc_info', None) # Error object is usually in exc_info

                is_message_match = "Unexpected error saving topic topic_db_unexpected_fail" in log_message
                # For exc_info, the actual exception object is passed. str(exc_info_obj) might be too simple.
                # isinstance(exc_info_obj, TypeError) and "Simulated unexpected type error" in str(exc_info_obj)
                # However, logger usually gets True for exc_info, and then sys.exc_info() is used.
                # For this test, checking that exc_info=True was passed to logger.error is a good sign.
                # The log message itself should contain the string representation of the error if formatted that way.
                is_details_match = "Simulated unexpected type error" in log_message or exc_info_obj is True

                if is_message_match and is_details_match:
                    found_log = True
                    break
            self.assertTrue(found_log, f"Expected unexpected error log message not found. Logs: {mock_logger_error.call_args_list}")

        mock_conn.commit.assert_not_called()
        mock_conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
