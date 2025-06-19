import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys
import json

# Adjust path
current_dir = os.path.dirname(os.path.abspath(__file__))
vfa_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(vfa_dir)
project_root_dir = os.path.dirname(aethercast_dir)

sys.path.insert(0, project_root_dir)
sys.path.insert(0, aethercast_dir)

from aethercast.vfa import main as vfa_main
# No longer directly import or mock Google Cloud TTS SDK components here
# import google.cloud.texttospeech
# import google.api_core.exceptions as google_exceptions
import requests # For mocking AIMS_TTS calls
from datetime import datetime, timezone, timedelta # For idempotency tests

import uuid # Added for idempotency key generation in tests

# Imports from VFA service (ensure celery_app is imported for config)
from aethercast.vfa.main import app as flask_app, celery_app as vfa_celery_app
from aethercast.vfa.main import vfa_config, load_vfa_configuration
from aethercast.vfa.main import IDEMPOTENCY_KEY_HEADER, forge_voice_task
from aethercast.vfa.main import _get_vfa_db_connection # To mock it

# --- Mock Database Connection Registry for VFA ---
mock_db_connection_registry_vfa = {}

def mock_get_vfa_db_connection_side_effect():
    instance_id = os.getpid()
    if instance_id not in mock_db_connection_registry_vfa:
        conn = MagicMock(name=f"MockVfaPsycopg2Connection_{instance_id}")
        cursor_mock = MagicMock(name="MockVfaCursor")
        cursor_mock.fetchone.return_value = None # Default: key not found
        cursor_mock.rowcount = 0
        conn.cursor.return_value.__enter__.return_value = cursor_mock
        conn.commit = MagicMock()
        conn.rollback = MagicMock()
        conn.close = MagicMock()
        mock_db_connection_registry_vfa[instance_id] = conn
    return mock_db_connection_registry_vfa[instance_id]

def reset_mock_vfa_db_connections():
    mock_db_connection_registry_vfa.clear()

# --- Base Test Case for Idempotency ---
class BaseVfaIdempotencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vfa_celery_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True,
            broker_url="memory://",
            result_backend="rpc://"
        )
        flask_app.testing = True
        load_vfa_configuration() # Load initial config

    def setUp(self):
        self.app = flask_app.test_client()
        reset_mock_vfa_db_connections()

        self.test_config_overrides = {
            "VFA_DEBUG_MODE": False,
            "POSTGRES_HOST": "mock_pg_host_vfa", # These will be used by _get_vfa_db_connection if not fully mocked
            "POSTGRES_USER": "mock_pg_user_vfa",
            "POSTGRES_PASSWORD": "mock_pg_password_vfa",
            "POSTGRES_DB": "mock_pg_db_vfa",
            "VFA_POSTGRES_DB_URL": "postgresql://mock_pg_user_vfa:mock_pg_password_vfa@mock_pg_host_vfa:5432/mock_pg_db_vfa",
            "IDEMPOTENCY_STATUS_PROCESSING": "processing", # Ensure vfa_config uses these keys
            "IDEMPOTENCY_STATUS_COMPLETED": "completed",
            "IDEMPOTENCY_STATUS_FAILED": "failed",
            "IDEMPOTENCY_LOCK_TIMEOUT_SECONDS": 60, # Short timeout for tests
            "VFA_TEST_MODE_ENABLED": False, # Usually False for idempotency tests of real logic path
            "AIMS_TTS_SERVICE_URL": "http://mock-aims-tts.test/v1/synthesize",
        }
        # Patch vfa_config directly as it's already loaded
        self.config_patcher = patch.dict(vfa_config, self.test_config_overrides, clear=False)
        self.mocked_vfa_config = self.config_patcher.start()

        # Mock for AIMS_TTS calls made by the Celery task, now using GLOBAL_REQUESTS_SESSION
        self.mock_aims_tts_success_payload = {
            "request_id": "mock_aims_tts_req_success",
            "voice_id": "mock-voice", "audio_url": "gs://mock-bucket/audio.mp3",
            "audio_duration_seconds": 5.0, "audio_format": "mp3"
        }

        self.patch_session_post = patch('aethercast.vfa.main.GLOBAL_REQUESTS_SESSION.post')
        self.mock_session_post = self.patch_session_post.start()

        self.patch_session_get = patch('aethercast.vfa.main.GLOBAL_REQUESTS_SESSION.get')
        self.mock_session_get = self.patch_session_get.start()

        # Default successful AIMS_TTS task submission (POST)
        mock_aims_initial_response = MagicMock(status_code=202)
        mock_aims_initial_response.json.return_value = {"task_id": "aims-tts-task-123", "status_url": "/aims_tts_tasks/aims-tts-task-123"}
        self.mock_session_post.return_value = mock_aims_initial_response

        # Default successful AIMS_TTS polling result (GET)
        mock_aims_poll_response = MagicMock(status_code=200)
        mock_aims_poll_response.json.return_value = {"status": "SUCCESS", "result": self.mock_aims_tts_success_payload}
        self.mock_session_get.return_value = mock_aims_poll_response


    def tearDown(self):
        self.config_patcher.stop()
        self.patch_session_post.stop()
        self.patch_session_get.stop()
        reset_mock_vfa_db_connections()

# The TestOldForgeVoiceLogic class and its methods are removed.
# @unittest.skip("Skipping tests for deprecated forge_voice logic")
# class TestOldForgeVoiceLogic(unittest.TestCase): # Renamed original class
# ... (all methods of TestOldForgeVoiceLogic removed) ...

    def test_forge_voice_test_mode_default_scenario(self):
        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": True, "VFA_SHARED_AUDIO_DIR": "/test/dummy_audio"}):
            # No X-Test-Scenario header
            response = vfa_main.forge_voice({"topic": "Test Default", "segments": [{"content": "Sufficiently long script for test."}]})
            self.assertEqual(response["status"], "success")
            self.assertIn("(VFA TEST MODE - dummy file, AIMS_TTS call bypassed).", response["message"])
            self.assertTrue(response["audio_filepath"].startswith("/test/dummy_audio/aethercast_audio_vfa_testmode_"))
            self.assertEqual(response["engine_used"], "test_mode_bypassed_aims_tts")

    def test_forge_voice_test_mode_aims_tts_error_scenario(self):
        with patch.dict(vfa_main.vfa_config, {"VFA_TEST_MODE_ENABLED": True}):
            # Simulate request object for header access
            with patch.object(vfa_main, 'request', MagicMock(headers={'X-Test-Scenario': 'vfa_error_aims_tts'})):
                response = vfa_main.forge_voice({"topic": "Test AIMS TTS Error", "segments": [{"content": "Script for AIMS TTS error test."}]})
                self.assertEqual(response["error_code"], "VFA_TEST_MODE_AIMS_TTS_ERROR")
                self.assertIn(vfa_main.VFA_TEST_SCENARIO_AIMS_TTS_ERROR_MSG, response["message"]) # Corrected constant name
                self.assertEqual(response["engine_used"], "test_mode_aims_tts_error")


@patch('aethercast.vfa.main._get_vfa_db_connection', side_effect=mock_get_vfa_db_connection_side_effect)
class TestVfaApiGeneral(BaseVfaIdempotencyTest): # Renamed and now inherits base for consistency
    def setUp(self):
        super().setUp() # Call base setUp for Celery, Flask app, config patcher, DB mock patcher, AIMS mock
        self.client = flask_app.test_client() # Add test client
        # Specific config for these general API tests, can override base if needed
        self.mock_vfa_config_for_general_api = {
            "VFA_TEST_MODE_ENABLED": True, # Most of these tests rely on test mode
            "VFA_SHARED_AUDIO_DIR": "/tmp/vfa_test_audio_endpoint_general",
        }
        # Apply additional or override configs on top of what BaseVfaIdempotencyTest set up
        # Note: self.config_patcher is from BaseVfaIdempotencyTest. We are adding to its dictionary.
        # It's better if BaseVfaIdempotencyTest's setUp is more minimal or this class doesn't inherit if configs widely diverge.
        # For now, let's assume this an additive/override patch.
        self.config_patcher_general_api = patch.dict(vfa_config, self.mock_vfa_config_for_general_api, clear=False)
        self.mock_config_general_api = self.config_patcher_general_api.start()

        # If these tests truly mock out `forge_voice_task.delay` or `forge_voice` itself,
        # they might not need the DB idempotency mocks.
        # However, since the endpoint now has pre-checks, DB mock is needed.
        # The @patch for _get_vfa_db_connection will be applied by the class decorator later.

    def tearDown(self):
        self.config_patcher_general_api.stop()
        super().tearDown() # Call base tearDown

    @patch('aethercast.vfa.main.forge_voice_task.delay') # Mock the .delay() to prevent Celery task execution
    def test_handle_forge_voice_success_dispatch(self, mock_celery_task_delay): # mock_db_conn_getter implicitly added by class decorator
        # This test now checks if the task is dispatched correctly, assuming idempotency pre-check passes.
        # Mock the DB connection to simulate a new key for the pre-check.
        mock_db_conn = mock_get_vfa_db_connection_side_effect()
        mock_cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        mock_cursor.fetchone.return_value = None # Simulate new key

        # Mock the return value of .delay()
        mock_async_result = MagicMock()
        mock_async_result.id = "test_celery_task_id_dispatch"
        mock_celery_task_delay.return_value = mock_async_result

        idempotency_key = f"vfa-dispatch-test-{uuid.uuid4()}"
        payload = {
            "script": {"script_id": "s1", "topic": "Test", "title": "Test", "full_raw_script": "Test script", "segments": []},
            "voice_params": {"voice_name": "custom-voice"}
        }
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": "wf-test-dispatch"}
        response = self.client.post('/v1/forge_voice', json=payload, headers=headers) # Use self.client and correct path

        self.assertEqual(response.status_code, 202) # Task accepted
        json_data = response.get_json()
        self.assertEqual(json_data["task_id"], "test_celery_task_id_dispatch")
        self.assertEqual(json_data["idempotency_key_processed"], idempotency_key)

        mock_celery_task_delay.assert_called_once_with(
            request_id_celery=ANY, # request_id_main is generated internally
            script_input=payload["script"],
            voice_params_input=payload["voice_params"],
            test_scenario_header=None, # Not provided in this test
            idempotency_key=idempotency_key,
            workflow_id="wf-test-dispatch"
        )
        # Verify DB pre-check: SELECT, INSERT (PROCESSING)
        execute_calls = mock_cursor.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 2)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(mock_db_conn.commit.call_count, 1)


    def test_handle_forge_voice_missing_script(self, mock_db_conn_getter_unused): # Add unused mock getter from class decorator
        response = self.client.post('/v1/forge_voice', json={}, headers={IDEMPOTENCY_KEY_HEADER: "some-key"}) # Path updated, key added
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data.get("error_code"), "VFA_VALIDATION_ERROR")
        self.assertEqual(json_data.get("message"), "Invalid input")
        self.assertEqual(json_data.get("message"), "Valid 'script' object with 'script_id' is required.") # Updated expected message

    def test_handle_forge_voice_script_not_dict(self, mock_db_conn_getter_unused):
        response = self.client.post('/v1/forge_voice', json={"script": "this is a string, not a dict"}, headers={IDEMPOTENCY_KEY_HEADER: "some-key"}) # Path updated
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data.get("error_code"), "VFA_INVALID_SCRIPT_PAYLOAD") # Corrected error code
        self.assertEqual(json_data.get("message"), "Valid 'script' object with 'script_id' is required.")

    def test_handle_forge_voice_no_json_payload(self, mock_db_conn_getter_unused):
        response = self.client.post('/v1/forge_voice', data="not a json payload", content_type="text/plain", headers={IDEMPOTENCY_KEY_HEADER: "some-key"}) # Path updated
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data.get("error_code"), "VFA_MALFORMED_JSON") # Corrected error code
        self.assertIn("Malformed JSON", json_data.get("message"))

    def test_handle_forge_voice_voice_params_not_dict(self, mock_db_conn_getter_unused):
        payload = {
            "script": {"script_id": "s_vp_err", "topic": "VP Error", "full_raw_script": "Test script"},
            "voice_params": "this is a string, not a dict"
        }
        response = self.client.post('/v1/forge_voice', json=payload, headers={IDEMPOTENCY_KEY_HEADER: "some-key"}) # Path updated
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data.get("error_code"), "VFA_INVALID_VOICE_PARAMS_TYPE") # Corrected error code
        self.assertEqual(json_data.get("message"), "'voice_params' must be an object if provided.")

    # The following tests mock `forge_voice_task.delay` to simplify testing specific endpoint logic paths
    # without fully running the Celery task or its idempotency.
    # These are more about endpoint behavior given a certain outcome from task dispatch.
    @patch('aethercast.vfa.main.forge_voice_task.delay')
    def test_handle_forge_voice_skipped_from_task_result(self, mock_celery_task_delay, mock_db_conn_getter_unused):
        # Simulate that the endpoint pre-check passes (new key)
        mock_db_conn = mock_get_vfa_db_connection_side_effect()
        mock_cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        mock_cursor.fetchone.return_value = None # New key

        mock_async_result = MagicMock()
        mock_async_result.id = "task_skipped_id"
        # Simulate the Celery task itself returning a "skipped" status in its result payload
        mock_async_result.successful.return_value = True # Task completed successfully
        mock_async_result.result = {"status": vfa_main.VFA_STATUS_SKIPPED, "message": "Script too short"}
        mock_celery_task_delay.return_value = mock_async_result

        payload = {"script": {"script_id":"s_skip", "full_raw_script": "short"}}
        response = self.client.post('/v1/forge_voice', json=payload, headers={IDEMPOTENCY_KEY_HEADER: "skip-key"})
        self.assertEqual(response.status_code, 202) # Endpoint accepts the task

        # Now check the status URL
        status_response = self.client.get(f'/v1/tasks/{mock_async_result.id}')
        self.assertEqual(status_response.status_code, 200) # Skipped is a valid final state
        json_data = status_response.get_json()
        self.assertEqual(json_data["result"]["status"], vfa_main.VFA_STATUS_SKIPPED)


    @patch('aethercast.vfa.main.forge_voice_task.delay')
    def test_handle_forge_voice_error_from_task_result(self, mock_celery_task_delay, mock_db_conn_getter_unused):
        mock_db_conn = mock_get_vfa_db_connection_side_effect()
        mock_cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        mock_cursor.fetchone.return_value = None # New key

        mock_async_result = MagicMock()
        mock_async_result.id = "task_error_id"
        mock_async_result.successful.return_value = True # Task completed successfully (but with error in payload)
        mock_async_result.result = {"error_code": "VFA_TTS_FAILED", "message": "TTS failed"}
        mock_celery_task_delay.return_value = mock_async_result

        payload = {"script": {"script_id":"s_err", "full_raw_script": "test"}}
        response = self.client.post('/v1/forge_voice', json=payload, headers={IDEMPOTENCY_KEY_HEADER: "error-key"})
        self.assertEqual(response.status_code, 202)

        status_response = self.client.get(f'/v1/tasks/{mock_async_result.id}')
        self.assertEqual(status_response.status_code, 500) # Endpoint returns 500 for business logic error
        json_data = status_response.get_json()
        self.assertEqual(json_data["result"]["error_code"], "VFA_TTS_FAILED")

    # Test mode tests remain relevant for endpoint behavior with X-Test-Scenario
    @patch('aethercast.vfa.main.forge_voice_task.delay') # Still mock delay, as pre-check is what we test for test-mode routing
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_endpoint_test_mode_default_scenario(self, mock_file_open, mock_os_path_exists, mock_celery_task_delay, mock_db_conn_getter_unused):
        """Test VFA endpoint in test mode with default success scenario."""
        mock_os_path_exists.return_value = True # Assume file "created" by test mode exists for this check
        mock_db_conn = mock_get_vfa_db_connection_side_effect()
        mock_cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        mock_cursor.fetchone.return_value = None # New key for pre-check

        payload = {"script": {"script_id": "s_test_default", "topic": "Test Default", "full_raw_script":"Sufficiently long script for test."}}
        headers = {IDEMPOTENCY_KEY_HEADER: "test-default-key"}
        # No X-Test-Scenario header, should use default success (which now calls Celery task)

        mock_async_result = MagicMock()
        mock_async_result.id = "test_celery_task_id_test_mode"
        mock_celery_task_delay.return_value = mock_async_result

        response = self.client.post('/v1/forge_voice', json=payload, headers=headers)

        self.assertEqual(response.status_code, 202) # Task accepted
        # Further checks would involve polling the task status, which is more complex for this unit test
        # The key is that it dispatched correctly with test_mode settings potentially passed via header/config.


    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_endpoint_test_mode_vfa_error_tts_scenario(self, mock_file_open, mock_os_path_exists, mock_celery_task_delay, mock_db_conn_getter_unused):
        """Test VFA endpoint in test mode for 'vfa_error_tts' scenario."""
        mock_db_conn = mock_get_vfa_db_connection_side_effect()
        mock_cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        mock_cursor.fetchone.return_value = None # New key for pre-check

        headers = {'X-Test-Scenario': 'vfa_error_aims_tts', IDEMPOTENCY_KEY_HEADER: "test-tts-error-key"} # Corrected scenario name
        payload = {"script": {"script_id": "s_test_tts_err", "topic": "Test TTS Error", "full_raw_script":"Script for TTS error test."}}

        mock_async_result = MagicMock()
        mock_async_result.id = "test_celery_task_id_tts_error"
        mock_celery_task_delay.return_value = mock_async_result

        response = self.client.post('/v1/forge_voice', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202) # Task accepted
        # To verify the error, one would poll the task. The 'X-Test-Scenario' is passed to the Celery task.

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_forge_voice_endpoint_test_mode_vfa_error_file_save_scenario(self, mock_file_open, mock_os_path_exists, mock_celery_task_delay, mock_db_conn_getter_unused):
        """Test VFA endpoint in test mode for 'vfa_error_file_save' scenario."""
        mock_db_conn = mock_get_vfa_db_connection_side_effect()
        mock_cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        mock_cursor.fetchone.return_value = None # New key for pre-check

        headers = {'X-Test-Scenario': 'vfa_error_file_save', IDEMPOTENCY_KEY_HEADER: "test-file-save-key"}
        payload = {"script": {"script_id": "s_test_file_err", "topic": "Test File Save Error", "full_raw_script":"Script for file save error test."}}

        mock_async_result = MagicMock()
        mock_async_result.id = "test_celery_task_id_file_error"
        mock_celery_task_delay.return_value = mock_async_result

        response = self.client.post('/v1/forge_voice', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202) # Task accepted

# --- VFA Flask Endpoint Idempotency Tests ---
@patch('aethercast.vfa.main._get_vfa_db_connection', side_effect=mock_get_vfa_db_connection_side_effect)
class TestVfaFlaskIdempotency(BaseVfaIdempotencyTest):

    def test_missing_idempotency_key_header(self, mock_db_conn_getter):
        """Test VFA Flask endpoint /v1/forge_voice rejects if X-Idempotency-Key is missing."""
        payload = {"script": {"script_id": "s1", "topic": "Test", "full_raw_script": "Test script"}}
        response = self.client.post('/v1/forge_voice', json=payload, headers={}) # Use self.client
        self.assertEqual(response.status_code, 400)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "VFA_MISSING_IDEMPOTENCY_KEY")
        # Assert that DB connection was not even attempted for this client error
        mock_db_conn_getter.assert_not_called()

    def test_completed_key_returns_200_from_endpoint(self, mock_db_conn_getter):
        """Test VFA Flask endpoint returns 200 directly if idempotency key is already COMPLETED."""
        idempotency_key = f"vfa-flask-completed-{uuid.uuid4()}"
        workflow_id = f"wf-vfa-flask-completed-{uuid.uuid4()}"
        payload = {"script": {"script_id": "s_compl", "topic": "Completed Topic", "full_raw_script":"Valid script."}}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": workflow_id}

        stored_result = {"audio_url": "gs://completed/audio.mp3", "status": "success_from_db"}

        mock_conn = mock_db_connection_registry_vfa[os.getpid()] # Get the mock connection for this process
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        completed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'workflow_id': workflow_id, 'status': vfa_config['IDEMPOTENCY_STATUS_COMPLETED'],
            'result_payload': stored_result, 'locked_at': None
        }
        cursor_mock.fetchone.return_value = completed_record

        response = self.app.post('/v1/forge_voice', json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), stored_result)

        cursor_mock.execute.assert_called_once_with(ANY, (idempotency_key, 'forge_voice_task'))
        mock_conn.commit.assert_not_called()
        mock_conn.rollback.assert_called_once()
        self.mock_requests_post.assert_not_called() # AIMS_TTS should not be called

    def test_processing_key_returns_409_from_endpoint(self, mock_db_conn_getter):
        """Test VFA Flask endpoint returns 409 if key is PROCESSING and not timed out."""
        idempotency_key = f"vfa-flask-processing-{uuid.uuid4()}"
        workflow_id = f"wf-vfa-flask-processing-{uuid.uuid4()}"
        payload = {"script": {"script_id": "s_proc", "topic": "Processing Topic", "full_raw_script":"Valid script."}}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": workflow_id}

        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'workflow_id': workflow_id, 'status': vfa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': datetime.now(timezone.utc) # Recent lock
        }
        cursor_mock.fetchone.return_value = processing_record

        response = self.app.post('/v1/forge_voice', json=payload, headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error_code"], "VFA_IDEMPOTENCY_CONFLICT")

        cursor_mock.execute.assert_called_once_with(ANY, (idempotency_key, 'forge_voice_task'))
        mock_conn.commit.assert_not_called()
        mock_conn.rollback.assert_called_once()
        self.mock_requests_post.assert_not_called() # AIMS_TTS should not be called

    # TODO: Add more tests for Flask idempotency:
    # - test_new_key_dispatches_task_and_succeeds (verify endpoint PROCESSING, task COMPLETED) # Partially covered by previous test, can be enhanced.
    # - test_processing_key_lock_timeout_reprocesses (verify endpoint re-PROCESSING, task COMPLETED)
    # - test_failed_key_reprocesses (verify endpoint PROCESSING, task COMPLETED)
    # - test_task_failure_after_pre_check_updates_db_via_on_failure (endpoint PROCESSING, task FAILS, on_failure FAILED)

    def test_new_key_full_flow_success(self, mock_db_conn_getter): # Renamed from test_new_key_dispatches_task_and_succeeds
        """Test new key: endpoint stores PROCESSING, dispatches task, task runs & stores COMPLETED."""
        idempotency_key = f"vfa-flask-new-full-{uuid.uuid4()}"
        workflow_id = f"wf-vfa-flask-new-full-{uuid.uuid4()}"
        payload = {"script": {"script_id": "s_new_full", "topic": "New Key Full", "full_raw_script":"Valid script for full flow."}}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": workflow_id}

        # DB mock: _check returns None (new key) initially for endpoint.
        # Then, for Celery task, _check returns the PROCESSING record.
        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value

        # Simulate DB records for different stages
        processing_record_for_task = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'workflow_id': workflow_id, 'status': vfa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': datetime.now(timezone.utc)
        }
        # First call (endpoint pre-check): No record found
        # Second call (Celery task initial check): Processing record found
        cursor_mock.fetchone.side_effect = [None, processing_record_for_task]

        # AIMS_TTS mock is already set up for success in BaseVfaIdempotencyTest.setUp()

        response = self.app.post('/v1/forge_voice', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202)
        json_response = response.get_json()
        task_id = json_response["task_id"]

        # Verify endpoint DB interactions (SELECT miss, INSERT PROCESSING)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 2) # At least SELECT and INSERT from endpoint
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(execute_calls[1][0][1][4], vfa_config['IDEMPOTENCY_STATUS_PROCESSING'])
        self.assertEqual(mock_conn.commit.call_count, 1) # Endpoint commit

        # Verify Celery Task Outcome
        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 200)
        status_json = status_response.get_json()
        self.assertEqual(status_json["status"], "SUCCESS")
        self.assertEqual(status_json["result"]["audio_url"], self.mock_aims_tts_success_payload["audio_url"])

        # Verify Celery task's DB interactions (SELECT PROCESSING, UPDATE COMPLETED)
        # Total calls: EP_SELECT, EP_INSERT, TASK_SELECT, TASK_UPDATE
        self.assertGreaterEqual(len(execute_calls), 4)
        self.assertIn("SELECT idempotency_key", execute_calls[2][0][0]) # Task's _check
        self.assertIn("UPDATE idempotency_keys", execute_calls[3][0][0]) # Task's _store (COMPLETED)
        self.assertEqual(execute_calls[3][0][1][0], vfa_config['IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertEqual(mock_conn.commit.call_count, 2) # Endpoint commit + Celery task commit


    def test_processing_key_timeout_leads_to_reprocessing(self, mock_db_conn_getter):
        idempotency_key = f"vfa-flask-proc-timeout-{uuid.uuid4()}"
        workflow_id = f"wf-vfa-flask-proc-timeout-{uuid.uuid4()}"
        payload = {"script": {"script_id": "s_proc_timeout", "topic": "Processing Timeout", "full_raw_script":"Valid script."}}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": workflow_id}

        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value

        stale_locked_at = datetime.now(timezone.utc) - timedelta(seconds=vfa_config['IDEMPOTENCY_LOCK_TIMEOUT_SECONDS'] + 60)
        stale_processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'workflow_id': workflow_id, 'status': vfa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': stale_locked_at
        }
        # First call (endpoint pre-check): Stale PROCESSING record
        # Second call (Celery task initial check): Freshly updated PROCESSING record by endpoint
        cursor_mock.fetchone.side_effect = [stale_processing_record, stale_processing_record] # Simulate it finds the re-locked one

        response = self.app.post('/v1/forge_voice', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202)
        task_id = response.get_json()["task_id"]

        # Verify endpoint DB: SELECT (finds stale), UPDATE (re-lock PROCESSING)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 2) # EP_SELECT, EP_UPDATE_REPROCESSING
        self.assertIn("UPDATE idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(execute_calls[1][0][1][0], vfa_config['IDEMPOTENCY_STATUS_PROCESSING']) # Status
        self.assertIsNotNone(execute_calls[1][0][1][3]) # new locked_at (params are status, result, error, locked_at, key, name)
        self.assertGreater(execute_calls[1][0][1][3], stale_locked_at)
        self.assertEqual(mock_conn.commit.call_count, 1) # Endpoint commit

        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 200) # Task succeeds
        self.assertEqual(status_response.get_json()["result"]["audio_url"], self.mock_aims_tts_success_payload["audio_url"])

        self.assertGreaterEqual(len(execute_calls), 4) # EP_SELECT, EP_UPDATE, TASK_SELECT, TASK_UPDATE
        self.assertIn("UPDATE idempotency_keys", execute_calls[3][0][0]) # Task's _store (COMPLETED)
        self.assertEqual(execute_calls[3][0][1][0], vfa_config['IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertEqual(mock_conn.commit.call_count, 2)


    def test_failed_key_leads_to_reprocessing_and_success(self, mock_db_conn_getter):
        idempotency_key = f"vfa-flask-failed-retry-{uuid.uuid4()}"
        workflow_id = f"wf-vfa-flask-failed-retry-{uuid.uuid4()}"
        payload = {"script": {"script_id": "s_failed_retry", "topic": "Failed Retry", "full_raw_script":"Valid script."}}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": workflow_id}

        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        failed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'workflow_id': workflow_id, 'status': vfa_config['IDEMPOTENCY_STATUS_FAILED'],
            'error_payload': {"error": "previous failure"}, 'locked_at': None
        }
        processing_record_for_task = { # What task will see after endpoint re-locks
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'workflow_id': workflow_id, 'status': vfa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': datetime.now(timezone.utc)
        }
        cursor_mock.fetchone.side_effect = [failed_record, processing_record_for_task]

        response = self.app.post('/v1/forge_voice', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202)
        task_id = response.get_json()["task_id"]

        # Verify endpoint DB: SELECT (finds FAILED), UPDATE (to PROCESSING)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 2)
        self.assertIn("UPDATE idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(execute_calls[1][0][1][0], vfa_config['IDEMPOTENCY_STATUS_PROCESSING'])
        self.assertEqual(mock_conn.commit.call_count, 1)

        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 200) # Task succeeds
        self.assertEqual(status_response.get_json()["result"]["audio_url"], self.mock_aims_tts_success_payload["audio_url"])

        self.assertGreaterEqual(len(execute_calls), 4) # EP_SELECT, EP_UPDATE, TASK_SELECT, TASK_UPDATE
        self.assertIn("UPDATE idempotency_keys", execute_calls[3][0][0]) # Task's _store (COMPLETED)
        self.assertEqual(execute_calls[3][0][1][0], vfa_config['IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertEqual(mock_conn.commit.call_count, 2)

    @patch('aethercast.vfa.main.forge_voice_task.apply_async') # To intercept Celery dispatch
    def test_task_fails_after_flask_precheck_updates_db_to_failed(self, mock_apply_async, mock_db_conn_getter):
        idempotency_key = f"vfa-flask-taskfail-{uuid.uuid4()}"
        workflow_id = f"wf-vfa-flask-taskfail-{uuid.uuid4()}"
        payload = {"script": {"script_id": "s_taskfail", "topic": "Task Failure", "full_raw_script":"Valid script."}}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": workflow_id}

        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key for endpoint pre-check

        # Simulate Celery task execution and its direct failure
        # This requires the task to actually run and fail, and on_failure to be called.
        # We'll mock the AIMS call within the task to cause the failure.
        self.mock_requests_post.side_effect = Exception("Simulated AIMS_TTS Network Error During Task")

        # Mock apply_async to capture arguments and then allow the real task to run (eagerly)
        # so on_failure can be triggered.
        original_apply_async = forge_voice_task.apply_async # Store original
        def side_effect_apply_async(*args, **kwargs):
            # Call on_failure directly for testing if task was guaranteed to fail
            # This is a bit of a hack for testing on_failure when eager.
            # A better way might be to have a dedicated test for on_failure.
            # For now, let's assume the exception in requests.post will trigger it via Celery's eager runner.
            return original_apply_async(*args, **kwargs)
        mock_apply_async.side_effect = side_effect_apply_async

        response = self.app.post('/v1/forge_voice', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202)
        task_id_from_endpoint = response.get_json()["task_id"]

        # Endpoint DB: SELECT (miss), INSERT (PROCESSING)
        ep_execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(ep_execute_calls), 2)
        self.assertEqual(mock_conn.commit.call_count, 1) # Endpoint commit for PROCESSING

        # Check task status
        status_response = self.app.get(f'/v1/tasks/{task_id_from_endpoint}')
        self.assertEqual(status_response.status_code, 500) # Task failed
        status_json = status_response.get_json()
        self.assertEqual(status_json["status"], "FAILURE")
        self.assertIn("Simulated AIMS_TTS Network Error During Task", str(status_json["result"]["error"]["message"]))

        # Verify on_failure DB update: UPDATE (FAILED)
        # The execute_calls list will be longer now.
        # We need to find the last UPDATE call which should be from on_failure.
        final_update_call_args = None
        for call_args_tuple in reversed(cursor_mock.execute.call_args_list):
            sql_command = str(call_args_tuple[0][0])
            if "UPDATE idempotency_keys" in sql_command:
                final_update_call_args = call_args_tuple[0][1]
                break

        self.assertIsNotNone(final_update_call_args, "No UPDATE call found for idempotency table from on_failure.")
        self.assertEqual(final_update_call_args[0], vfa_config['IDEMPOTENCY_STATUS_FAILED'])
        self.assertIn("Simulated AIMS_TTS Network Error During Task", final_update_call_args[2]) # error_payload
        self.assertEqual(mock_conn.commit.call_count, 2) # EP PROCESSING + on_failure FAILED


@patch('aethercast.vfa.main._get_vfa_db_connection', side_effect=mock_get_vfa_db_connection_side_effect)
class TestVfaCeleryIdempotency(BaseVfaIdempotencyTest):

    def test_new_key_direct_task_call_success(self, mock_db_conn_getter):
        idempotency_key = f"vfa-celery-new-{uuid.uuid4()}"
        workflow_id = f"wf-vfa-celery-new-{uuid.uuid4()}"
        script_input = {"script_id": "s_celery_new", "topic": "Celery New", "full_raw_script":"Valid script."}

        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key

        task_result = forge_voice_task.apply(kwargs={
            'request_id_celery': 'req_celery_new', 'script_input': script_input,
            'idempotency_key': idempotency_key, 'workflow_id': workflow_id
        }).get()

        self.assertEqual(task_result["status"], vfa_main.VFA_STATUS_SUCCESS)
        self.assertEqual(task_result["audio_url"], self.mock_aims_tts_success_payload["audio_url"])

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3) # SELECT, INSERT (PROC), UPDATE (COMPL)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(execute_calls[1][0][1][4], vfa_config['IDEMPOTENCY_STATUS_PROCESSING'])
        self.assertIn("UPDATE idempotency_keys", execute_calls[2][0][0])
        self.assertEqual(execute_calls[2][0][1][0], vfa_config['IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertEqual(mock_conn.commit.call_count, 2)

    def test_completed_key_direct_task_call_returns_stored(self, mock_db_conn_getter):
        idempotency_key = f"vfa-celery-completed-{uuid.uuid4()}"
        stored_payload = {"audio_url": "gs://celery_completed/audio.mp3", "status": "success"}

        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        completed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'status': vfa_config['IDEMPOTENCY_STATUS_COMPLETED'], 'result_payload': stored_payload
        }
        cursor_mock.fetchone.return_value = completed_record

        self.mock_requests_post.reset_mock() # Ensure AIMS is not called

        task_result = forge_voice_task.apply(kwargs={
            'request_id_celery': 'req_celery_compl', 'script_input': {"script_id":"s1"},
            'idempotency_key': idempotency_key
        }).get()

        self.assertEqual(task_result, stored_payload)
        self.mock_requests_post.assert_not_called()
        cursor_mock.execute.assert_called_once()
        mock_conn.commit.assert_not_called()
        mock_conn.rollback.assert_called_once()

    def test_processing_key_direct_task_call_conflict(self, mock_db_conn_getter):
        idempotency_key = f"vfa-celery-proc-conflict-{uuid.uuid4()}"
        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'status': vfa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': datetime.now(timezone.utc)
        }
        cursor_mock.fetchone.return_value = processing_record

        task_result = forge_voice_task.apply(kwargs={
            'request_id_celery': 'req_celery_conflict', 'script_input': {"script_id":"s1"},
            'idempotency_key': idempotency_key
        }).get()

        self.assertEqual(task_result["status"], "PROCESSING_CONFLICT")
        self.assertEqual(task_result["idempotency_key"], idempotency_key)
        cursor_mock.execute.assert_called_once()
        mock_conn.commit.assert_not_called()
        mock_conn.rollback.assert_called_once()

    def test_processing_key_timeout_direct_task_call_reprocesses(self, mock_db_conn_getter):
        idempotency_key = f"vfa-celery-proc-timeout-{uuid.uuid4()}"
        stale_locked_at = datetime.now(timezone.utc) - timedelta(seconds=vfa_config['IDEMPOTENCY_LOCK_TIMEOUT_SECONDS'] + 1)

        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        stale_processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'status': vfa_config['IDEMPOTENCY_STATUS_PROCESSING'], 'locked_at': stale_locked_at
        }
        # Task will find stale, then re-check (mocking it finds its own fresh processing lock)
        # then complete.
        fresh_processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'status': vfa_config['IDEMPOTENCY_STATUS_PROCESSING'], 'locked_at': datetime.now(timezone.utc)
        }
        cursor_mock.fetchone.side_effect = [stale_processing_record, fresh_processing_record]


        task_result = forge_voice_task.apply(kwargs={
            'request_id_celery': 'req_celery_timeout', 'script_input': {"script_id":"s1", "full_raw_script":"Long enough"},
            'idempotency_key': idempotency_key
        }).get()

        self.assertEqual(task_result["status"], vfa_main.VFA_STATUS_SUCCESS)

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3) # SELECT stale, UPDATE re-lock, UPDATE completed
        self.assertIn("UPDATE idempotency_keys", execute_calls[1][0][0]) # Re-lock
        self.assertEqual(execute_calls[1][0][1][0], vfa_config['IDEMPOTENCY_STATUS_PROCESSING'])
        self.assertGreater(execute_calls[1][0][1][3], stale_locked_at) # new locked_at
        self.assertIn("UPDATE idempotency_keys", execute_calls[2][0][0]) # Completed
        self.assertEqual(execute_calls[2][0][1][0], vfa_config['IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertEqual(mock_conn.commit.call_count, 2)


    def test_task_failure_direct_task_call_marks_failed(self, mock_db_conn_getter):
        idempotency_key = f"vfa-celery-fail-{uuid.uuid4()}"
        self.mock_requests_post.side_effect = Exception("Celery task AIMS_TTS direct fail")

        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key

        with self.assertRaises(Exception) as context:
            forge_voice_task.apply(kwargs={
                'request_id_celery': 'req_celery_fail', 'script_input': {"script_id":"s1", "full_raw_script":"valid"},
                'idempotency_key': idempotency_key
            }).get()
        self.assertIn("Celery task AIMS_TTS direct fail", str(context.exception))

        execute_calls = cursor_mock.execute.call_args_list
        # Expected: SELECT (miss), INSERT (PROCESSING), on_failure's UPDATE (FAILED)
        self.assertGreaterEqual(len(execute_calls), 3)
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0]) # Initial PROCESSING

        on_failure_update_call = execute_calls[2] # The third call should be the on_failure update
        self.assertIn("UPDATE idempotency_keys", on_failure_update_call[0][0])
        self.assertEqual(on_failure_update_call[0][1][0], vfa_config['IDEMPOTENCY_STATUS_FAILED'])
        self.assertIn("Celery task AIMS_TTS direct fail", on_failure_update_call[0][1][2]) # error_payload

        self.assertEqual(mock_conn.commit.call_count, 2) # PROCESSING commit, FAILED commit

    def test_retry_after_failure_direct_task_call_succeeds(self, mock_db_conn_getter):
        idempotency_key = f"vfa-celery-retry-{uuid.uuid4()}"

        mock_conn = mock_db_connection_registry_vfa[os.getpid()]
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        failed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'status': vfa_config['IDEMPOTENCY_STATUS_FAILED'],
            'error_payload': json.dumps({"error": "Previous direct VFA failure"}),
        }
        processing_record_for_task = {
            'idempotency_key': idempotency_key, 'task_name': 'forge_voice_task',
            'status': vfa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': datetime.now(timezone.utc)
        }
        cursor_mock.fetchone.side_effect = [failed_record, processing_record_for_task]

        # Reset AIMS_TTS mock to succeed for the retry
        mock_aims_initial_response = MagicMock(status_code=202)
        mock_aims_initial_response.json.return_value = {"task_id": "aims-tts-task-retry", "status_url": "/aims_tts_tasks/aims-tts-task-retry"}
        mock_aims_poll_response = MagicMock(status_code=200)
        mock_aims_poll_response.json.return_value = {"status": "SUCCESS", "result": self.mock_aims_tts_success_payload}
        self.mock_requests_post.side_effect = [mock_aims_initial_response, mock_aims_poll_response]


        task_result = forge_voice_task.apply(kwargs={
            'request_id_celery': 'req_celery_retry', 'script_input': {"script_id":"s1", "full_raw_script":"valid"},
            'idempotency_key': idempotency_key
        }).get()

        self.assertEqual(task_result["status"], vfa_main.VFA_STATUS_SUCCESS)

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3) # SELECT (finds FAILED), UPDATE (to PROCESSING), UPDATE (to COMPLETED)
        self.assertIn("UPDATE idempotency_keys", execute_calls[1][0][0]) # To PROCESSING
        self.assertEqual(execute_calls[1][0][1][0], vfa_config['IDEMPOTENCY_STATUS_PROCESSING'])
        self.assertIn("UPDATE idempotency_keys", execute_calls[2][0][0]) # To COMPLETED
        self.assertEqual(execute_calls[2][0][1][0], vfa_config['IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertEqual(mock_conn.commit.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
