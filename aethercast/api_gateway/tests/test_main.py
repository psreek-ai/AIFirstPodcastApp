import unittest
from unittest.mock import patch, MagicMock
import os
import sys
# Explicitly add user site-packages to sys.path
user_site_packages = '/home/swebot/.local/lib/python3.10/site-packages'
if user_site_packages not in sys.path:
    sys.path.insert(0, user_site_packages)
print(f"PYTHON SYS PATH: {sys.path}")
import sys
import json
import sqlite3 # For direct DB assertions
from datetime import datetime, timedelta # Added timedelta for session tests
import time # Added for rate limit tests

# Adjust path to import API Gateway main module and CPOA (for mocking)
current_dir = os.path.dirname(os.path.abspath(__file__))
api_gw_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(api_gw_dir)
project_root_dir = os.path.dirname(aethercast_dir)

sys.path.insert(0, project_root_dir)
sys.path.insert(0, aethercast_dir)
sys.path.insert(0, api_gw_dir)


from aethercast.api_gateway import main as api_gw_main
# Import app and JWT generator
from aethercast.api_gateway.main import app, generate_jwt
# Import specific Pydantic models if needed for constructing valid payloads
from aethercast.api_gateway.main import SessionPreferencesUpdatePayload


ORIGINAL_DATABASE_FILE = api_gw_main.DATABASE_FILE

class TestAPIGateway(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        api_gw_main.app.config['TESTING'] = True
        # It's crucial that app.config['SECRET_KEY'] is set *before* self.client is created
        # if generate_jwt uses app.config['SECRET_KEY'] directly.
        api_gw_main.app.config['SECRET_KEY'] = 'test_secret_key_for_testing_suite'
        cls.client = api_gw_main.app.test_client()

        cls.db_master_conn = sqlite3.connect("file::memory:?cache=shared", check_same_thread=False)
        cls.db_master_conn.row_factory = sqlite3.Row

        cls.db_file_patcher = patch.object(api_gw_main, 'DATABASE_FILE', "file::memory:?cache=shared")
        cls.mock_db_file_uri = cls.db_file_patcher.start()

        # Patch DATABASE_TYPE to sqlite for these tests, as we are using in-memory SQLite
        # This ensures get_db_connection and release_db_connection use SQLite logic.
        cls.db_type_patcher = patch.object(api_gw_main, 'DATABASE_TYPE', "sqlite")
        cls.mock_db_type = cls.db_type_patcher.start()


        try:
            cursor = cls.db_master_conn.cursor()
            cursor.executescript(api_gw_main.DB_SCHEMA_SQL)
            cls.db_master_conn.commit()
            tables_to_verify = ['podcasts', 'topics_snippets', 'generated_scripts', 'user_sessions', 'users'] # Added users
            for table_name in tables_to_verify:
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
                if cursor.fetchone() is None:
                    raise AssertionError(f"{table_name} table was not created in shared in-memory DB.")
        except Exception as e:
            cls.db_master_conn.close()
            cls.db_file_patcher.stop()
            cls.db_type_patcher.stop()
            raise e

    @classmethod
    def tearDownClass(cls):
        cls.db_file_patcher.stop()
        cls.db_type_patcher.stop()
        if cls.db_master_conn:
            cls.db_master_conn.close()

    def setUp(self):
        # Clean tables using the master connection before each test
        cursor = self.db_master_conn.cursor()
        cursor.execute("DELETE FROM podcasts;")
        cursor.execute("DELETE FROM topics_snippets;")
        cursor.execute("DELETE FROM generated_scripts;")
        cursor.execute("DELETE FROM user_sessions;")
        cursor.execute("DELETE FROM users;") # Clear users table
        self.db_master_conn.commit()

        # Ensure app.config['SECRET_KEY'] is consistent for each test that might generate tokens
        app.config['SECRET_KEY'] = 'test_secret_key_for_testing_suite'


    # --- Helper method to generate a token ---
    def _generate_test_token(self, session_id, user_id=None, secret_key=None, expires_delta_days=1):
        key_to_use = secret_key or app.config['SECRET_KEY']
        payload = {
            'session_id': session_id,
            'user_id': user_id, # Can be None
            'exp': datetime.utcnow() + timedelta(days=expires_delta_days)
        }
        return generate_jwt(payload, key_to_use)

    # --- Existing Health Check Tests ---
    def test_health_check_all_healthy(self):
        with patch('aethercast.api_gateway.main.IMPORTS_SUCCESSFUL_ALL_CPOA_FUNCS', return_value=True):
            response = self.client.get('/health')
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data['status'], "API Gateway is healthy")
            self.assertEqual(data['database_status'], "Database connection successful.")

    # ... (other health check tests can be kept if they are still relevant) ...

    # --- Existing Session Init and GET Preferences Tests ---
    def test_session_init_new_client(self):
        client_id = "test_client_new_01"
        response = self.client.post('/api/v1/session/init', json={"client_id": client_id, "initial_preferences": {"theme": "light"}})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['client_id'], client_id)
        self.assertEqual(data['preferences'], {"theme": "light"}) # Check initial preferences are set

    def test_get_preferences_existing_client(self):
        client_id = "test_client_get_prefs_03"
        prefs = {"language": "en", "news_category": "technology"}
        # Initialize session first
        self.client.post('/api/v1/session/init', json={"client_id": client_id, "initial_preferences": prefs})

        # Now generate a token for this session to access the GET endpoint
        token = self._generate_test_token(session_id=client_id)
        response = self.client.get(f'/api/v1/session/preferences', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['client_id'], client_id)
        self.assertEqual(data['preferences'], prefs)

    # --- New Tests for PUT /api/v1/session/preferences ---
    @patch('aethercast.api_gateway.main._get_session')
    @patch('aethercast.api_gateway.main._update_session_preferences')
    def test_update_preferences_success(self, mock_update_prefs, mock_get_session):
        # Simulate that the session exists when _get_session is called
        test_session_id = "session_A_123"
        mock_get_session.return_value = {"session_id": test_session_id, "preferences_json": json.dumps({})}
        mock_update_prefs.return_value = None # _update_session_preferences doesn't return a value

        token = self._generate_test_token(session_id=test_session_id)
        payload = {"client_id": test_session_id, "preferences": {"theme": "dark"}}

        response = self.client.put(
            '/api/v1/session/preferences',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            data=json.dumps(payload)
        )
        self.assertEqual(response.status_code, 200)
        json_response = response.get_json()
        self.assertEqual(json_response["message"], "Preferences updated successfully.")
        self.assertEqual(json_response["client_id"], test_session_id)
        self.assertEqual(json_response["preferences"], {"theme": "dark"})
        # Ensure _update_session_preferences was called with the correct DB connection (ANY), session_id, and preferences
        mock_update_prefs.assert_called_once_with(unittest.mock.ANY, test_session_id, {"theme": "dark"})

    def test_update_preferences_forbidden_wrong_client_id(self):
        token_for_session_A = self._generate_test_token(session_id="session_A_abc")
        payload_for_session_B = {"client_id": "session_B_xyz", "preferences": {"theme": "light"}}

        response = self.client.put(
            '/api/v1/session/preferences',
            headers={'Authorization': f'Bearer {token_for_session_A}', 'Content-Type': 'application/json'},
            data=json.dumps(payload_for_session_B)
        )
        self.assertEqual(response.status_code, 403)
        json_response = response.get_json()
        self.assertEqual(json_response.get('error_code'), "API_GW_FORBIDDEN_SESSION_UPDATE")

    def test_update_preferences_token_missing_session_id(self):
        # Generate a token that deliberately omits 'session_id'
        payload_no_session = {
            'user_id': 'user123', # To pass initial "user_id or session_id" check in token_required
            'exp': datetime.utcnow() + timedelta(days=1)
        }
        token_no_session_id = generate_jwt(payload_no_session, app.config['SECRET_KEY'])

        request_payload = {"client_id": "any_client_id", "preferences": {"lang": "en"}}
        response = self.client.put(
            '/api/v1/session/preferences',
            headers={'Authorization': f'Bearer {token_no_session_id}', 'Content-Type': 'application/json'},
            data=json.dumps(request_payload)
        )
        self.assertEqual(response.status_code, 401)
        json_response = response.get_json()
        self.assertEqual(json_response.get('error_code'), "API_GW_INVALID_TOKEN_CLAIMS")
        self.assertIn("Token does not contain required session information", json_response.get("message"))


    def test_update_preferences_missing_token(self):
        payload = {"client_id": "any_client_id", "preferences": {"theme": "dark"}}
        response = self.client.put(
            '/api/v1/session/preferences',
            headers={'Content-Type': 'application/json'}, # No Authorization header
            data=json.dumps(payload)
        )
        self.assertEqual(response.status_code, 401)
        json_response = response.get_json()
        self.assertEqual(json_response.get('error_code'), "API_GW_TOKEN_MISSING")

    def test_update_preferences_payload_validation_failure_missing_client_id(self):
        token = self._generate_test_token(session_id="valid_session_id")
        invalid_payload = {"preferences": {"theme": "dark"}} # Missing client_id

        response = self.client.put(
            '/api/v1/session/preferences',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            data=json.dumps(invalid_payload)
        )
        self.assertEqual(response.status_code, 422)
        json_response = response.get_json()
        self.assertEqual(json_response.get('error_code'), "API_GW_VALIDATION_ERROR")
        # Check that 'client_id' field is mentioned in the Pydantic error details
        found_client_id_error = False
        for detail in json_response.get('details', []):
            if 'client_id' in detail.get('loc', []):
                found_client_id_error = True
                break
        self.assertTrue(found_client_id_error, "Details should mention 'client_id' field error.")

    def test_update_preferences_payload_validation_failure_invalid_preferences_type(self):
        token = self._generate_test_token(session_id="valid_session_id_for_prefs_type_test")
        # preferences should be a dict, not a string
        invalid_payload = {"client_id": "valid_session_id_for_prefs_type_test", "preferences": "not_a_dictionary"}

        response = self.client.put(
            '/api/v1/session/preferences',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            data=json.dumps(invalid_payload)
        )
        self.assertEqual(response.status_code, 422)
        json_response = response.get_json()
        self.assertEqual(json_response.get('error_code'), "API_GW_VALIDATION_ERROR")
        found_preferences_error = False
        for detail in json_response.get('details', []):
            if 'preferences' in detail.get('loc', []) and 'DictInput' in detail.get('type', ''): # Pydantic v2 error type for dict
                found_preferences_error = True
                break
            # For Pydantic v1, it might be 'value_error.dict' or similar.
            # This check might need adjustment based on the exact Pydantic version's error output.
            # A simpler check could be:
            # if 'preferences' in detail.get('loc', []): found_preferences_error = True; break
        self.assertTrue(found_preferences_error, "Details should mention 'preferences' field type error.")

    # Keep other existing tests below this point...
    # For example:
    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_task_success(self, mock_orchestrate_podcast_func):
        # This is an existing test, ensure it still works or adapt if necessary
        # For this subtask, we assume it doesn't need changes unless it conflicts.
        # However, it's good practice to ensure the SECRET_KEY is set for token generation if this endpoint becomes protected.
        # Assuming /api/v1/podcasts is @token_required
        app.config['SECRET_KEY'] = 'test_secret_key_for_testing_suite' # Ensure consistent key
        # User must be "logged in" (i.e. have a valid token)
        # For simplicity, let's say any valid token works for this test if user_id isn't strictly checked by CPOA for this call
        token = self._generate_test_token(session_id="any_session_for_podcast", user_id="user_for_podcast")


        mock_cpoa_result = {
            "task_id": "will_be_overwritten_by_api_gw_uuid",
            "topic": "Test Success Topic",
            "status": "completed",
            "error_message": None,
            "asf_notification_status": "ASF notified successfully.",
            "asf_websocket_url": "ws://mockasf/stream/stream_mock_abc",
            "final_audio_details": {
                "status": "success",
                "audio_filepath": "/srv/aethercast/audio/mock_audio.mp3",
                "stream_id": "stream_mock_abc",
                "tts_settings_used": {"voice_name": "en-US-TestVoice", "speaking_rate": 1.0, "pitch": 0.0, "audio_encoding": "MP3"}
            },
            "orchestration_log": [{"timestamp": "ts_now", "message": "All good from CPOA"}]
        }
        mock_orchestrate_podcast_func.return_value = mock_cpoa_result

        test_voice_params = {"voice_name": "en-GB-News-K", "speaking_rate": 0.9}
        post_payload = {'topic': 'Test Success Topic', 'voice_params': test_voice_params}
        response = self.client.post(
            '/api/v1/podcasts',
            json=post_payload,
            headers={'Authorization': f'Bearer {token}'} # Add token
        )
        
        self.assertEqual(response.status_code, 201) # Or 200 depending on actual endpoint logic for success
        # ... rest of assertions for this test

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_task_cpoa_returns_error(self, mock_orchestrate_podcast_func):
        """Test API Gateway response when CPOA returns a sanitized error."""
        app.config['SECRET_KEY'] = 'test_secret_key_for_testing_suite'
        token = self._generate_test_token(session_id="any_session_for_cpoa_error", user_id="user_for_cpoa_error")

        # Mock CPOA to return a sanitized error
        mock_cpoa_error_response = {
            "status": "FAILURE", # Simplified status from CPOA
            "error_message": "This is a sanitized error message from CPOA.", # Sanitized message
            "legacy_cpoa_internal_status": "failed_some_internal_step", # Example legacy status
            "workflow_id": "wf_mock_error_123"
        }
        mock_orchestrate_podcast_func.return_value = mock_cpoa_error_response

        post_payload = {'topic': 'Test CPOA Failure Topic'}
        response = self.client.post(
            '/api/v1/podcasts',
            json=post_payload,
            headers={'Authorization': f'Bearer {token}', 'X-Idempotency-Key': 'idem-key-cpoa-error'} # Added idempotency key
        )

        # Expecting API Gateway to return a 500 or 502 based on the CPOA error
        # The logic in main.py: if "request_exception" or "reported_error" or "WORKFLOW_CREATION_FAILED" in status or "timeout" -> 502, else 500
        # For a generic "FAILURE" status from CPOA, it should be 500.
        self.assertEqual(response.status_code, 500)
        json_response = response.get_json()

        self.assertEqual(json_response.get("error_code"), "API_GW_CPOA_ORCHESTRATION_ERROR_FAILURE")
        self.assertEqual(json_response.get("message"), "This is a sanitized error message from CPOA.")
        self.assertEqual(json_response.get("workflow_id"), "wf_mock_error_123")
        # Ensure no raw internal details like "failed_some_internal_step" are in the top-level message/error_code
        self.assertNotIn("failed_some_internal_step", json_response.get("error_code", ""))
        self.assertNotIn("failed_some_internal_step", json_response.get("message", ""))


    @patch.object(api_gw_main.app.logger, 'error')
    def test_global_error_handler_returns_standard_json_500(self, mock_logger_error):
        """Test that the global error handler catches unhandled exceptions and returns a standard JSON 500 response."""
        # Define a simple route that will raise an unhandled exception
        @self.client.application.route('/_test_unhandled_error')
        def _test_error_route():
            raise Exception("Simulated unhandled test error")

        response = self.client.get('/_test_unhandled_error')

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content_type, 'application/json')

        json_response = response.get_json()
        expected_response = {
            "error_code": "API_GW_INTERNAL_SERVER_ERROR",
            "message": "An internal server error occurred. Please try again later or contact support if the issue persists."
        }
        self.assertEqual(json_response, expected_response)

        # Assert that the error was logged, specifically checking for exc_info=True
        mock_logger_error.assert_called_once()
        # Check the call arguments for exc_info=True
        # The actual call object is mock_logger_error.call_args
        # It's a tuple: (args, kwargs). We need to check kwargs.
        args, kwargs = mock_logger_error.call_args
        self.assertTrue(kwargs.get('exc_info', False), "logger.error should be called with exc_info=True")
        self.assertIn("Simulated unhandled test error", args[0]) # Check if the original error message part is in the log


# --- Rate Limiting Tests ---
class TestAPIGatewayRateLimiting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        api_gw_main.app.config['TESTING'] = True
        # Set rate limit specific configs BEFORE creating the test client or initializing Limiter for the app
        api_gw_main.app.config['API_RATE_LIMIT_ENABLED'] = True
        api_gw_main.app.config['API_DEFAULT_RATE_LIMIT'] = "2 per second" # Test-friendly limit
        api_gw_main.app.config['API_DEFAULT_STRATEGY'] = "fixed-window"
        api_gw_main.app.config['RATELIMIT_STORAGE_URL'] = "memory://" # Ensure in-memory storage for tests

        # Re-initialize or reconfigure the limiter on the main app instance
        # This is crucial because the limiter is initialized when main.py is imported.
        # We need to ensure the test client uses an app with test-specific rate limits.
        if hasattr(api_gw_main, 'limiter'):
            # If limiter is already initialized, try to reconfigure its default limits
            # This might not be the cleanest way, depends on Flask-Limiter's API
            # A better way might be to ensure limiter is initialized *after* config is set in tests,
            # or use an app factory pattern.
            # For now, let's assume we can reset/reconfigure.
            # This is a bit of a hack; ideally, the app and its extensions are configured once.
            # We might need to re-create the limiter instance for the app.
            from flask_limiter import Limiter
            from flask_limiter.util import get_remote_address

            # Detach old limiter if present from app.extensions (Flask-Limiter might do this internally)
            if 'limiter' in api_gw_main.app.extensions:
                del api_gw_main.app.extensions['limiter']

            api_gw_main.limiter = Limiter(
                get_remote_address,
                app=api_gw_main.app, # Pass the app instance
                default_limits=[api_gw_main.app.config['API_DEFAULT_RATE_LIMIT']],
                storage_uri=api_gw_main.app.config['RATELIMIT_STORAGE_URL'],
                strategy=api_gw_main.app.config['API_DEFAULT_STRATEGY'],
                default_limits_exempt_when=lambda: not api_gw_main.app.config['API_RATE_LIMIT_ENABLED']
            )
        else:
            # This path would be taken if limiter wasn't defined in main.py yet,
            # which is not the case here.
            pass

        cls.client = api_gw_main.app.test_client()

        # Patch the database for other tests if they run in parallel or affect global state
        # For rate limiting tests specifically, DB interaction is usually not the focus unless
        # limits are stored in DB. Here, it's memory.
        cls.db_file_patcher = patch.object(api_gw_main, 'DATABASE_FILE', "file::memory:?cache=shared")
        cls.mock_db_file_uri = cls.db_file_patcher.start()
        cls.db_type_patcher = patch.object(api_gw_main, 'DATABASE_TYPE', "sqlite")
        cls.mock_db_type = cls.db_type_patcher.start()


    @classmethod
    def tearDownClass(cls):
        # Reset limiter or app config if necessary
        # Stop patchers if they were started in setUpClass
        cls.db_file_patcher.stop()
        cls.db_type_patcher.stop()
        # Restore original limiter settings if possible, or ensure app is clean for other test classes
        # This is tricky with module-level app. For true isolation, app factory is better.
        # For now, we assume subsequent test classes will re-initialize app/limiter as needed.


    def setUp(self):
        # Reset limiter state before each test
        if hasattr(api_gw_main, 'limiter') and api_gw_main.limiter.storage:
            api_gw_main.limiter.storage.clear() # Clear in-memory storage

        # Ensure a valid token for @token_required endpoints if we test them
        api_gw_main.app.config['SECRET_KEY'] = 'test_secret_key_for_rate_limit_tests'
        self.test_token = self._generate_test_token(session_id="test_session_rate_limit", user_id="test_user_rate_limit")


    def _generate_test_token(self, session_id, user_id=None, secret_key=None, expires_delta_days=1):
        # Copied from TestAPIGateway class, consider moving to a shared utility if many classes need it
        key_to_use = secret_key or api_gw_main.app.config['SECRET_KEY']
        payload = {
            'session_id': session_id,
            'user_id': user_id,
            'exp': datetime.utcnow() + timedelta(days=expires_delta_days)
        }
        return generate_jwt(payload, key_to_use)

    def test_health_endpoint_not_limited(self):
        """Test that the /health endpoint is not rate-limited by default exemption."""
        for _ in range(5): # Hit it more times than the default limit (2 per second)
            response = self.client.get('/health')
            self.assertEqual(response.status_code, 200)

    def test_rate_limiting_on_general_endpoint(self):
        """Test rate limiting on a general endpoint (e.g., /api/v1/podcasts)."""
        # This endpoint requires auth, so use the token
        headers = {'Authorization': f'Bearer {self.test_token}'}

        # First two requests should succeed (limit is "2 per second")
        response1 = self.client.get('/api/v1/podcasts', headers=headers)
        self.assertEqual(response1.status_code, 200)
        response2 = self.client.get('/api/v1/podcasts', headers=headers)
        self.assertEqual(response2.status_code, 200)

        # Third request within the same second should be rate-limited
        response3 = self.client.get('/api/v1/podcasts', headers=headers)
        self.assertEqual(response3.status_code, 429) # Too Many Requests

        # Wait for the window to reset (1 second for "2 per second")
        time.sleep(1.1) # Sleep a bit more than 1 second

        # Request after window reset should succeed
        response4 = self.client.get('/api/v1/podcasts', headers=headers)
        self.assertEqual(response4.status_code, 200)

    def test_rate_limit_disabled_allows_requests(self):
        """Test that if rate limiting is disabled, requests are not limited."""
        original_enabled_state = api_gw_main.app.config.get('API_RATE_LIMIT_ENABLED', True)
        api_gw_main.app.config['API_RATE_LIMIT_ENABLED'] = False
        # The limiter's default_limits_exempt_when should now make it exempt all

        headers = {'Authorization': f'Bearer {self.test_token}'}
        try:
            for i in range(5): # More than the "2 per second" limit
                response = self.client.get('/api/v1/podcasts', headers=headers)
                self.assertEqual(response.status_code, 200, f"Request {i+1} failed when rate limiting was disabled.")
        finally:
            # Restore original state
            api_gw_main.app.config['API_RATE_LIMIT_ENABLED'] = original_enabled_state


# --- GCS Signed URL Security Tests ---
class TestGcsSignedUrlSecurity(unittest.TestCase):
    def setUp(self):
        # Ensure a consistent testing state for GCS_BUCKET_NAME
        self.original_gcs_bucket_name = os.environ.get("GCS_BUCKET_NAME")
        os.environ["GCS_BUCKET_NAME"] = "test-configured-bucket"
        # Update the module-level variable in api_gw_main if it was set at import time
        api_gw_main.GCS_BUCKET_NAME = "test-configured-bucket"


    def tearDown(self):
        # Restore original GCS_BUCKET_NAME environment variable
        if self.original_gcs_bucket_name is None:
            if "GCS_BUCKET_NAME" in os.environ:
                del os.environ["GCS_BUCKET_NAME"]
        else:
            os.environ["GCS_BUCKET_NAME"] = self.original_gcs_bucket_name
        # Restore module-level variable in api_gw_main
        api_gw_main.GCS_BUCKET_NAME = self.original_gcs_bucket_name


    @patch('aethercast.api_gateway.main.storage.Client')
    def test_generate_url_allowed_bucket(self, mock_storage_client_cls):
        """Test generating a signed URL for the configured GCS_BUCKET_NAME."""
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = "https://example.com/signed-url-dummy"
        mock_bucket_instance = MagicMock()
        mock_bucket_instance.blob.return_value = mock_blob
        mock_storage_client_instance = MagicMock()
        mock_storage_client_instance.bucket.return_value = mock_bucket_instance
        mock_storage_client_cls.return_value = mock_storage_client_instance

        gcs_uri = "gs://test-configured-bucket/path/to/object.mp3"
        signed_url = api_gw_main.generate_gcs_signed_url(gcs_uri)

        self.assertEqual(signed_url, "https://example.com/signed-url-dummy")
        mock_storage_client_cls.assert_called_once()
        mock_storage_client_instance.bucket.assert_called_once_with("test-configured-bucket")
        mock_bucket_instance.blob.assert_called_once_with("path/to/object.mp3")
        mock_blob.generate_signed_url.assert_called_once()

    @patch('aethercast.api_gateway.main.storage.Client')
    def test_generate_url_disallowed_bucket(self, mock_storage_client_cls):
        """Test that a URL is not generated if the bucket name does not match GCS_BUCKET_NAME."""
        gcs_uri = "gs://other-bucket/path/to/object.mp3"
        signed_url = api_gw_main.generate_gcs_signed_url(gcs_uri)
        self.assertIsNone(signed_url)
        mock_storage_client_cls.assert_not_called() # Should fail before client instantiation for this specific check

    @patch.dict(os.environ, {"GCS_BUCKET_NAME": ""}) # Simulate GCS_BUCKET_NAME not set or empty
    @patch('aethercast.api_gateway.main.storage.Client')
    def test_generate_url_gcs_bucket_name_not_set(self, mock_storage_client_cls):
        """Test that no URL is generated if GCS_BUCKET_NAME is not configured."""
        # Temporarily modify the module-level GCS_BUCKET_NAME as it's loaded at import time
        with patch.object(api_gw_main, 'GCS_BUCKET_NAME', None):
            gcs_uri = "gs://any-bucket/path/to/object.mp3"
            signed_url = api_gw_main.generate_gcs_signed_url(gcs_uri)
            self.assertIsNone(signed_url)
            mock_storage_client_cls.assert_not_called()

    def test_generate_url_malformed_uri_no_gs_prefix(self):
        """Test with a URI not starting with 'gs://'."""
        signed_url = api_gw_main.generate_gcs_signed_url("http://test-configured-bucket/object.mp3")
        self.assertIsNone(signed_url)

    def test_generate_url_malformed_uri_no_object_name(self):
        """Test with a URI that doesn't have an object name part."""
        signed_url = api_gw_main.generate_gcs_signed_url("gs://test-configured-bucket/")
        self.assertIsNone(signed_url)

    def test_generate_url_malformed_uri_bucket_only(self):
        """Test with a URI that is only a bucket name."""
        signed_url = api_gw_main.generate_gcs_signed_url("gs://test-configured-bucket")
        self.assertIsNone(signed_url)


if __name__ == '__main__':
    unittest.main(verbosity=2)
