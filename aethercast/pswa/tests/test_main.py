import os
import sys
import json
import time
import uuid
import unittest
from unittest.mock import patch, MagicMock, ANY

# Explicitly add user site-packages to sys.path
# This is a workaround for potential PYTHONPATH issues in the execution environment.
user_site_packages = '/home/swebot/.local/lib/python3.10/site-packages'
if user_site_packages not in sys.path:
    sys.path.insert(0, user_site_packages)

# Adjust path to import PSWA main module
current_dir = os.path.dirname(os.path.abspath(__file__))
pswa_dir = os.path.dirname(current_dir) # Should be /aethercast/pswa
aethercast_dir = os.path.dirname(pswa_dir) # Should be /aethercast
project_root_dir = os.path.dirname(aethercast_dir) # Should be / (root of repo)

# Add project root and aethercast to allow aethercast.pswa.main import
if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)
if aethercast_dir not in sys.path:
    sys.path.insert(0, aethercast_dir)


# Now, try to import the main module components
# Assuming main.py is structured to allow importing 'app' and 'pswa_celery_app'
# This might require adjustments in main.py if it's not set up for testing.
from aethercast.pswa.main import app as flask_app
from aethercast.pswa.main import pswa_celery_app, weave_script_task, pswa_config, load_pswa_config
from aethercast.pswa.main import IDEMPOTENCY_KEY_HEADER
from aethercast.pswa.main import _get_pswa_db_connection_idempotency # For patching

# For mocking datetime
from datetime import datetime as dt, timezone, timedelta


# Helper to simulate DB connection for idempotency checks
mock_db_connection_registry = {}

def mock_get_pswa_db_connection_idempotency_side_effect():
    # This allows us to return different mocks or the same mock for specific tests
    # For basic tests, a simple MagicMock is often enough.
    # For more advanced tests (e.g. context manager behavior), it might need to be more complex.
    instance_id = os.getpid() # Or some other unique identifier for the call if needed
    if instance_id not in mock_db_connection_registry:
        # Default mock: key not found, successful commit/rollback
        conn = MagicMock(name=f"MockPsycopg2Connection_{instance_id}")
        cursor_mock = MagicMock(name="MockCursor")
        cursor_mock.fetchone.return_value = None # Default: key not found
        cursor_mock.rowcount = 0
        conn.cursor.return_value.__enter__.return_value = cursor_mock
        conn.commit = MagicMock()
        conn.rollback = MagicMock()
        conn.close = MagicMock()
        mock_db_connection_registry[instance_id] = conn
    return mock_db_connection_registry[instance_id]

def reset_mock_db_connections():
    mock_db_connection_registry.clear()


class TestPswaIdempotency(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Configure Celery for testing (task_always_eager=True runs tasks synchronously)
        # Also explicitly set broker and backend to in-memory for tests
        pswa_celery_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True,
            broker_url="memory://",
            result_backend="rpc://"
        )
        flask_app.testing = True

        # Ensure pswa_config is loaded initially. Tests can override specific values.
        # The main.py now has load_pswa_config() which initializes pswa_config from os.getenv
        # We call it here to ensure it's populated with defaults or test .env values.
        load_pswa_config()


    def setUp(self):
        self.app = flask_app.test_client()
        reset_mock_db_connections() # Ensure fresh mock DB for each test

        # Patch pswa_config for specific test needs if necessary.
        # Default test config values:
        self.test_config_overrides = {
            "PSWA_TEST_MODE_ENABLED": True, # Ensures AIMS is not actually called
            "DATABASE_TYPE": "postgres", # Idempotency always uses PostgreSQL
            "POSTGRES_HOST": "mock_pg_host", # Mocked, not connecting
            "POSTGRES_USER": "mock_pg_user",
            "POSTGRES_PASSWORD": "mock_pg_password",
            "POSTGRES_DB": "mock_pg_db",
            "IDEMPOTENCY_STATUS_PROCESSING": "processing", # Ensure these are strings
            "IDEMPOTENCY_STATUS_COMPLETED": "completed",
            "IDEMPOTENCY_STATUS_FAILED": "failed",
            "IDEMPOTENCY_LOCK_TIMEOUT_SECONDS": 60, # Short timeout for tests
            "SERVICE_NAME_FOR_IDEMPOTENCY": "PSWA_Test", # Ensure this is a string
            "PSWA_SCRIPT_CACHE_ENABLED": False, # Disable script caching for tests
            # CELERY_BROKER_URL and CELERY_RESULT_BACKEND are now set directly in pswa_celery_app.conf
            # in setUpClass, so they are not strictly needed in pswa_config for the app's broker/backend
            # but other parts of the code might still read them from pswa_config.
            "CELERY_BROKER_URL": "memory://",
            "CELERY_RESULT_BACKEND": "rpc://"
        }
        self.config_patcher = patch.dict(pswa_config, self.test_config_overrides, clear=False)
        self.mocked_pswa_config = self.config_patcher.start()


    def tearDown(self):
        self.config_patcher.stop()
        reset_mock_db_connections()

    def test_missing_idempotency_key_header_flask_endpoint(self):
        """Test Flask endpoint /v1/weave_script rejects request if X-Idempotency-Key header is missing."""
        payload = {"topic": "Test Topic", "content": "Some test content."}
        response = self.app.post('/v1/weave_script', json=payload, headers={}) # No Idempotency Key header
        self.assertEqual(response.status_code, 400)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "PSWA_MISSING_IDEMPOTENCY_KEY")

    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    # We are testing the integrated behavior, so we don't mock _check and _store directly here for the endpoint test.
    # Instead, we let the Celery task (running eagerly) call them.
    def test_new_idempotency_key_flask_endpoint_task_success(self, mock_db_conn_fn_getter):
        """Test Flask endpoint /v1/weave_script with a new idempotency key. Task runs and succeeds."""
        idempotency_key = f"test-key-new-{uuid.uuid4()}"
        payload = {"topic": "New Topic", "content": "Fresh content for testing."}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, 'X-Test-Scenario': 'default_success'}

        # Get the mock connection that will be used by the task
        # (via mock_get_pswa_db_connection_idempotency_side_effect)
        # This setup is a bit complex because the connection is obtained within the Celery task.
        # We rely on the side_effect to provide a mock we can inspect.

        # Expected sequence of DB interactions for a new key and successful task:
        # 1. _check_pswa_idempotency_key: returns None (key not found)
        # 2. _store_pswa_idempotency_result: (key, task_name, PROCESSING, is_new_key=True)
        # 3. (Task executes - in test mode, returns dummy script)
        # 4. _store_pswa_idempotency_result: (key, task_name, COMPLETED, result_payload, is_new_key=False)

        # Configure the mock cursor for the first _check_pswa_idempotency_key call (key not found)
        # This happens inside the Celery task.
        # We need to ensure the mock_db_connection_registry provides a cursor that returns None initially.
        # The default mock_get_pswa_db_connection_idempotency_side_effect already does this.

        response = self.app.post('/v1/weave_script', json=payload, headers=headers)

        self.assertEqual(response.status_code, 202, f"Response JSON: {response.get_data(as_text=True)}") # Task accepted
        json_response = response.get_json()
        self.assertIn("task_id", json_response)
        task_id = json_response["task_id"]
        self.assertEqual(json_response.get("idempotency_key_processed"), idempotency_key)

        # Since task_always_eager=True, the task has run. Now check its status.
        status_response = self.app.get(json_response["status_url"])
        self.assertEqual(status_response.status_code, 200)
        status_json = status_response.get_json()
        self.assertEqual(status_json["status"], "SUCCESS")
        self.assertIsNotNone(status_json["result"])
        self.assertIn("script_data", status_json["result"]) # Test mode returns dummy script in script_data

        # Verify database interactions (these happened inside the Celery task)
        # Get the connection mock that was used for this test
        mock_conn = mock_db_connection_registry[os.getpid()]
        self.assertTrue(mock_db_conn_fn_getter.called) # Ensure our mock connection factory was used

        # Check calls to cursor methods
        # _check_pswa_idempotency_key: SELECT ... WHERE idempotency_key = %s
        # _store_pswa_idempotency_result (PROCESSING): INSERT INTO idempotency_keys ...
        # _store_pswa_idempotency_result (COMPLETED): UPDATE idempotency_keys SET status = %s, result_payload = %s ...

        # Example check on execute calls (very detailed, might be brittle)
        execute_calls = mock_conn.cursor.return_value.__enter__.return_value.execute.call_args_list

        # 1. SELECT call from _check_pswa_idempotency_key
        self.assertIn(f"SELECT idempotency_key, task_name, workflow_id, created_at, locked_at, status, result_payload, error_payload FROM idempotency_keys WHERE idempotency_key = %s AND task_name = %s", execute_calls[0][0][0])
        self.assertEqual(execute_calls[0][0][1], (idempotency_key, 'pswa.weave_script_task'))

        # 2. INSERT call from _store_pswa_idempotency_result (status=PROCESSING)
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(execute_calls[1][0][1][0], idempotency_key) # key
        self.assertEqual(execute_calls[1][0][1][1], 'pswa.weave_script_task') # task_name
        self.assertEqual(execute_calls[1][0][1][4], pswa_config['IDEMPOTENCY_STATUS_PROCESSING']) # status

        # 3. UPDATE call from _store_pswa_idempotency_result (status=COMPLETED)
        self.assertIn("UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s", execute_calls[2][0][0])
        self.assertEqual(execute_calls[2][0][1][0], pswa_config['IDEMPOTENCY_STATUS_COMPLETED']) # status
        self.assertIsNotNone(execute_calls[2][0][1][1]) # result_payload (JSON string)
        self.assertIsNone(execute_calls[2][0][1][2]) # error_payload
        self.assertEqual(execute_calls[2][0][1][4], idempotency_key) # key
        self.assertEqual(execute_calls[2][0][1][5], 'pswa.weave_script_task') # task_name

        # Check commits
        self.assertEqual(mock_conn.commit.call_count, 2) # One for PROCESSING, one for COMPLETED


    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency') # Apply side_effect inside test
    def test_repeated_idempotency_key_for_completed_task_flask_endpoint(self, mock_db_conn_fn_getter):
        """Test Flask endpoint returns 200 with stored result for a COMPLETED key."""
        idempotency_key = f"test-key-completed-{uuid.uuid4()}"
        payload = {"topic": "Completed Topic", "content": "Content for completed task."}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, 'X-Test-Scenario': 'default_success'}
        service_name = pswa_config['SERVICE_NAME_FOR_IDEMPOTENCY']

        # --- First Request (new key) ---
        # Mock for Flask pre-check (key not found)
        mock_conn_flask_initial = MagicMock(name="FlaskInitialConn")
        cursor_mock_flask_initial = mock_conn_flask_initial.cursor.return_value.__enter__.return_value
        cursor_mock_flask_initial.fetchone.return_value = None # Key not found
        mock_conn_flask_initial.autocommit = True # As in main.py

        # Mock for Celery task (key also not found initially by task, or finds 'PROCESSING')
        mock_conn_task = MagicMock(name="TaskConn")
        cursor_mock_task = mock_conn_task.cursor.return_value.__enter__.return_value
        # Task will find the 'PROCESSING' record stored by Flask pre-check.
        processing_record_for_task = {
            'idempotency_key': idempotency_key, 'task_name': service_name,
            'status': pswa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': dt.now(timezone.utc) # Should be fresh
        }
        cursor_mock_task.fetchone.return_value = processing_record_for_task
        mock_conn_task.autocommit = False # Task manages commits

        # Set up side_effect for the db connection getter
        # First call (Flask pre-check for initial request): returns mock_conn_flask_initial
        # Second call (Celery task): returns mock_conn_task
        mock_db_conn_fn_getter.side_effect = [mock_conn_flask_initial, mock_conn_task]

        response1 = self.app.post('/v1/weave_script', json=payload, headers=headers)
        self.assertEqual(response1.status_code, 202) # Task dispatched
        task_id1 = response1.get_json()["task_id"]

        # Trigger task execution by getting status (due to eager config)
        status_response1 = self.app.get(f'/tasks/{task_id1}')
        self.assertEqual(status_response1.status_code, 200)
        result1_payload = status_response1.get_json()["result"] # This is the task's result

        # --- Second Request (repeated key) ---
        # Mock for Flask pre-check (key found as COMPLETED)
        completed_record_for_flask_check = {
            'idempotency_key': idempotency_key,
            'task_name': service_name,
            'status': pswa_config['IDEMPOTENCY_STATUS_COMPLETED'],
            'result_payload': result1_payload, # Stored result from first call
            'locked_at': None
        }
        mock_conn_flask_repeat = MagicMock(name="FlaskRepeatConn")
        cursor_mock_flask_repeat = mock_conn_flask_repeat.cursor.return_value.__enter__.return_value
        cursor_mock_flask_repeat.fetchone.return_value = completed_record_for_flask_check
        mock_conn_flask_repeat.autocommit = True

        # Update side_effect: next call to getter is for the second Flask pre-check
        mock_db_conn_fn_getter.side_effect = [mock_conn_flask_repeat]

        response2 = self.app.post('/v1/weave_script', json=payload, headers=headers)

        self.assertEqual(response2.status_code, 200, f"Response JSON: {response2.get_data(as_text=True)}")
        result2_payload_direct = response2.get_json()
        # The result1_payload is what was stored in DB, which should be what's returned directly.
        self.assertEqual(result1_payload, result2_payload_direct, "Result from repeated key (direct from endpoint) should match original.")

        # Verify DB interactions:
        # Flask Pre-check 1 (mock_conn_flask_initial): SELECT (no key), INSERT (PROCESSING)
        self.assertEqual(cursor_mock_flask_initial.execute.call_count, 2)
        self.assertIn("SELECT", cursor_mock_flask_initial.execute.call_args_list[0][0][0])
        self.assertIn("INSERT INTO idempotency_keys", cursor_mock_flask_initial.execute.call_args_list[1][0][0])

        # Celery Task (mock_conn_task): SELECT (finds PROCESSING), then UPDATE (COMPLETED)
        self.assertEqual(cursor_mock_task.execute.call_count, 2) # SELECT, then UPDATE to COMPLETED
        self.assertIn("SELECT", cursor_mock_task.execute.call_args_list[0][0][0])
        self.assertIn("UPDATE idempotency_keys SET status = %s, result_payload = %s", cursor_mock_task.execute.call_args_list[1][0][0])
        self.assertEqual(mock_conn_task.commit.call_count, 1) # For COMPLETED state

        # Flask Pre-check 2 (mock_conn_flask_repeat): SELECT (finds COMPLETED)
        self.assertEqual(cursor_mock_flask_repeat.execute.call_count, 1)
        self.assertIn("SELECT", cursor_mock_flask_repeat.execute.call_args_list[0][0][0])
        self.assertEqual(mock_conn_flask_repeat.commit.call_count, 0) # Autocommit=True, no explicit commit

    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency') # Apply side_effect inside test
    def test_repeated_key_for_processing_task_conflict_flask_endpoint(self, mock_db_conn_fn_getter):
        """Test Flask endpoint returns 409 conflict for a key already 'processing' and not timed out."""
        idempotency_key = f"test-key-processing-{uuid.uuid4()}"
        payload = {"topic": "Processing Topic", "content": "Content for processing task."}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, 'X-Test-Scenario': 'default_success'}
        service_name = pswa_config['SERVICE_NAME_FOR_IDEMPOTENCY']

        # Mock DB for Flask endpoint's _check_pswa_idempotency_key to return "processing" record
        mock_conn_flask_processing_check = MagicMock(name="FlaskProcessingCheckConn")
        cursor_mock_flask_processing_check = mock_conn_flask_processing_check.cursor.return_value.__enter__.return_value
        mock_conn_flask_processing_check.autocommit = True # As in main.py

        processing_record = {
            'idempotency_key': idempotency_key,
            'task_name': service_name,
            'status': pswa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'result_payload': None,
            'locked_at': dt.now(timezone.utc) # Current time, so not timed out
        }
        cursor_mock_flask_processing_check.fetchone.return_value = processing_record

        # Point the main mock getter to return this Flask-specific mock
        mock_db_conn_fn_getter.side_effect = [mock_conn_flask_processing_check]

        # Make the POST request
        response = self.app.post('/v1/weave_script', json=payload, headers=headers)

        # Expect 409 Conflict directly from the endpoint
        self.assertEqual(response.status_code, 409)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "PSWA_IDEMPOTENCY_CONFLICT")
        self.assertIn("currently processing", json_response.get("message"))

        # Verify DB interactions for this Flask pre-check:
        # _check_idempotency_key was called and returned the processing_record.
        # _store_idempotency_record should NOT have been called by this pre-check path.
        execute_calls = cursor_mock_flask_processing_check.execute.call_args_list
        self.assertEqual(len(execute_calls), 1) # Only the SELECT call
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(execute_calls[0][0][1], (idempotency_key, service_name))

        self.assertEqual(mock_conn_flask_processing_check.commit.call_count, 0) # Autocommit=True
        self.assertEqual(mock_conn_flask_processing_check.rollback.call_count, 0) # No failure, no rollback

    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    def test_repeated_key_for_processing_task_lock_timeout_flask_endpoint(self, mock_db_conn_fn_getter):
        """Test Flask endpoint re-processes a task if the 'processing' lock has timed out."""
        idempotency_key = f"test-key-lock-timeout-{uuid.uuid4()}"
        payload = {"topic": "Lock Timeout Topic", "content": "Content for lock timeout task."}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, 'X-Test-Scenario': 'default_success'}

        mock_conn = mock_db_connection_registry.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value

        lock_timeout_seconds = pswa_config['IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']
        stale_locked_at = dt.now(timezone.utc) - timedelta(seconds=lock_timeout_seconds + 60) # Expired lock

        stale_processing_record = {
            'idempotency_key': idempotency_key,
            'task_name': 'pswa.weave_script_task',
            'status': pswa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'result_payload': None,
            'locked_at': stale_locked_at
        }
        cursor_mock.fetchone.return_value = stale_processing_record # Simulate finding this stale record

        response = self.app.post('/v1/weave_script', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202, f"Response JSON: {response.get_data(as_text=True)}")
        task_id = response.get_json()["task_id"]

        # Task should run and succeed (due to test mode)
        status_response = self.app.get(f'/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 200)
        status_json = status_response.get_json()
        self.assertEqual(status_json["status"], "SUCCESS")
        self.assertIn("script_data", status_json["result"])

        # Verify database interactions:
        # 1. _check_pswa_idempotency_key: returns stale_processing_record
        # 2. _store_pswa_idempotency_result: (key, task_name, PROCESSING, is_new_key=False) -> updates lock
        # 3. _store_pswa_idempotency_result: (key, task_name, COMPLETED, result_payload, is_new_key=False)

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)

        # 1. SELECT call
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(execute_calls[0][0][1], (idempotency_key, 'pswa.weave_script_task'))

        # 2. UPDATE call for new PROCESSING state (is_new_key=False, but status is PROCESSING)
        # The actual SQL might be an UPDATE ... SET status = %s, locked_at = %s ...
        # In _store_pswa_idempotency_result, if is_new_key is False, it's an UPDATE.
        # If status is PROCESSING, locked_at is updated.
        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_update_processing = False
        for call_args in execute_calls:
            if update_processing_sql_part in str(call_args[0][0]):
                 # Check params for this call
                params = call_args[0][1]
                self.assertEqual(params[0], pswa_config['IDEMPOTENCY_STATUS_PROCESSING']) # status
                self.assertIsNone(params[1]) # result_payload
                self.assertIsNone(params[2]) # error_payload
                self.assertIsNotNone(params[3]) # locked_at (new timestamp)
                self.assertGreater(params[3], stale_locked_at) # ensure locked_at is updated
                self.assertEqual(params[4], idempotency_key) # key
                self.assertEqual(params[5], 'pswa.weave_script_task') # task_name
                found_update_processing = True
                break
        self.assertTrue(found_update_processing, "UPDATE call to re-lock for PROCESSING not found or params incorrect.")

        # 3. UPDATE call for COMPLETED state
        update_completed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_update_completed = False
        for call_args in execute_calls:
            if update_completed_sql_part in str(call_args[0][0]):
                params = call_args[0][1]
                self.assertEqual(params[0], pswa_config['IDEMPOTENCY_STATUS_COMPLETED']) # status
                self.assertIsNotNone(params[1]) # result_payload
                self.assertEqual(params[4], idempotency_key)
                found_update_completed = True
                break
        self.assertTrue(found_update_completed, "UPDATE call for COMPLETED not found or params incorrect.")

        self.assertEqual(mock_conn.commit.call_count, 2) # Commit for re-lock, commit for completion

    @patch('aethercast.pswa.main.pswa_config', new_callable=MagicMock) # To control PSWA_TEST_MODE_ENABLED
    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    @patch('aethercast.pswa.main._call_aims_service_for_script') # Mock the actual work part of the task
    def test_task_failure_marks_idempotency_failed(self, mock_call_aims, mock_db_conn_fn_getter, mock_dynamic_pswa_config):
        """Test that if a task fails, the idempotency record is marked as 'failed'."""
        # Override specific config values for this test if needed, otherwise defaults from setUp are used.
        # Ensure test mode is OFF for this test so it tries to call AIMS, which we mock to fail.
        # Accessing the global pswa_config dictionary updated by the patcher in setUp
        current_config = pswa_config.copy()
        current_config["PSWA_TEST_MODE_ENABLED"] = False # Force it to go through _call_aims_service_for_script
        mock_dynamic_pswa_config.return_value = current_config # Mock if pswa_config is accessed as a function
                                                              # If it's a dict, patch.dict in setUp is enough.
                                                              # For safety, let's assume it might be a module-level dict.
                                                              # The setup already patches pswa_config dict.

        idempotency_key = f"test-key-task-fails-{uuid.uuid4()}"
        payload = {"topic": "Failure Topic", "content": "Content that causes failure."}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key} # No X-Test-Scenario, relying on PSWA_TEST_MODE_ENABLED=False

        # Mock the AIMS call to simulate an exception
        mock_call_aims.side_effect = Exception("Simulated AIMS call failure")

        mock_conn = mock_db_connection_registry.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key initially

        response = self.app.post('/v1/weave_script', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202) # Task still accepted
        task_id = response.get_json()["task_id"]

        # Check task status - it should be FAILURE
        status_response = self.app.get(f'/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 500) # Flask endpoint returns 500 for failed task
        json_result = status_response.get_json()
        self.assertEqual(json_result["status"], "FAILURE")
        self.assertIn("Simulated AIMS call failure", str(json_result["result"]["error"]["message"]))

        # Verify DB interactions:
        # 1. _check_pswa_idempotency_key: returns None
        # 2. _store_pswa_idempotency_result: (key, PROCESSING, is_new_key=True)
        # 3. _store_pswa_idempotency_result: (key, FAILED, error_payload, is_new_key=False) - called by on_failure

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 2) # SELECT, INSERT (processing), UPDATE (failed)

        # Check INSERT for PROCESSING
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[0][0][0]) # Might be 1 if SELECT isn't counted by some mock setups
                                                                              # Let's assume SELECT is the first one.
        select_call_index = 0 if "SELECT" in execute_calls[0][0][0] else -1 # Adjust if needed
        insert_processing_call_index = select_call_index + 1

        self.assertIn("INSERT INTO idempotency_keys", execute_calls[insert_processing_call_index][0][0])
        self.assertEqual(execute_calls[insert_processing_call_index][0][1][4], pswa_config['IDEMPOTENCY_STATUS_PROCESSING'])

        # Check UPDATE for FAILED (this is the crucial part for this test)
        # This call is made by the WeaveScriptTask.on_failure handler
        update_failed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_update_failed = False
        for call_args in execute_calls:
            # Check if the SQL string contains the key components of the update statement for failure
            sql_statement = str(call_args[0][0])
            if "UPDATE idempotency_keys" in sql_statement and "status = %s" in sql_statement and "error_payload = %s" in sql_statement:
                params = call_args[0][1]
                if params[0] == pswa_config['IDEMPOTENCY_STATUS_FAILED'] and params[4] == idempotency_key:
                    self.assertIsNotNone(params[2]) # error_payload should be populated
                    self.assertIn("Simulated AIMS call failure", params[2]) # Check error message in payload
                    found_update_failed = True
                    break
        self.assertTrue(found_update_failed, "UPDATE call for FAILED status not found or params incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2) # Commit for PROCESSING, commit for FAILED


    @patch('aethercast.pswa.main.pswa_config', new_callable=MagicMock)
    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    @patch('aethercast.pswa.main._call_aims_service_for_script')
    def test_retry_after_failure_with_same_key_success(self, mock_call_aims, mock_db_conn_fn_getter, mock_dynamic_pswa_config):
        """Test task re-processes and succeeds after a previous failure with the same key."""
        current_config = pswa_config.copy()
        current_config["PSWA_TEST_MODE_ENABLED"] = False # Ensure AIMS call path
        mock_dynamic_pswa_config.return_value = current_config

        idempotency_key = f"test-key-retry-success-{uuid.uuid4()}"
        payload = {"topic": "Retry Topic", "content": "Content for retry."}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}

        mock_conn = mock_db_connection_registry.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value

        # Simulate that the key was previously recorded as FAILED
        failed_record = {
            'idempotency_key': idempotency_key,
            'task_name': 'pswa.weave_script_task',
            'status': pswa_config['IDEMPOTENCY_STATUS_FAILED'],
            'result_payload': None,
            'error_payload': json.dumps({"error": "Previous failure"}),
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = failed_record # First check finds this failed record

        # Mock AIMS call to succeed on the retry
        mock_call_aims.return_value = { # This is the structured_script like object
            "title": "Retry Success Title", "intro": "Intro",
            "segments": [{"segment_title": "s1", "content": "c1"}], "outro": "Outro",
            "model_id_used": "test-model-on-retry" # from AIMS actual response
        }


        response = self.app.post('/v1/weave_script', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202)
        task_id = response.get_json()["task_id"]

        status_response = self.app.get(f'/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 200)
        json_result = status_response.get_json()
        self.assertEqual(json_result["status"], "SUCCESS")
        self.assertIn("script_data", json_result["result"])
        self.assertEqual(json_result["result"]["script_data"]["title"], "Retry Success Title")

        # Verify DB interactions:
        # 1. _check_pswa_idempotency_key: returns failed_record
        # 2. _store_pswa_idempotency_result: (key, PROCESSING, is_new_key=False) -> updates from FAILED
        # 3. (Task executes successfully via mock_call_aims)
        # 4. _store_pswa_idempotency_result: (key, COMPLETED, result_payload, is_new_key=False)

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)

        # 1. SELECT call
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])

        # 2. UPDATE to PROCESSING from FAILED
        # SQL: UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s ...
        # Check params for this call (status=PROCESSING, locked_at=new_timestamp)
        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_update_reprocessing = False
        for call_args in execute_calls:
             if update_processing_sql_part in str(call_args[0][0]):
                params = call_args[0][1]
                if params[0] == pswa_config['IDEMPOTENCY_STATUS_PROCESSING'] and params[4] == idempotency_key:
                    self.assertIsNotNone(params[3]) # new locked_at
                    found_update_reprocessing = True
                    break
        self.assertTrue(found_update_reprocessing, "UPDATE call to re-set to PROCESSING not found or params incorrect.")

        # 3. UPDATE to COMPLETED
        update_completed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_update_completed = False
        for call_args in execute_calls:
            if update_completed_sql_part in str(call_args[0][0]):
                params = call_args[0][1]
                if params[0] == pswa_config['IDEMPOTENCY_STATUS_COMPLETED'] and params[4] == idempotency_key:
                    self.assertIsNotNone(params[1]) # result_payload
                    self.assertIn("Retry Success Title", params[1])
                    found_update_completed = True
                    break
        self.assertTrue(found_update_completed, "UPDATE call for COMPLETED status not found or params incorrect.")

        self.assertEqual(mock_conn.commit.call_count, 2) # For PROCESSING, For COMPLETED

    # TODO: Add tests for:
    # - Direct Celery task unit tests (not just via Flask endpoint)


# --- Direct Celery Task Idempotency Tests ---
class TestWeaveScriptTaskDirectly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pswa_celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
        # pswa_config should be loaded by main.py, tests can override
        load_pswa_config()

    def setUp(self):
        reset_mock_db_connections()
        self.test_config_overrides = {
            "PSWA_TEST_MODE_ENABLED": True,
            "DATABASE_TYPE": "postgres",
            "POSTGRES_HOST": "mock_pg_host",
            "IDEMPOTENCY_STATUS_PROCESSING": "processing",
            "IDEMPOTENCY_STATUS_COMPLETED": "completed",
            "IDEMPOTENCY_STATUS_FAILED": "failed",
            "IDEMPOTENCY_LOCK_TIMEOUT_SECONDS": 60,
        }
        self.config_patcher = patch.dict(pswa_config, self.test_config_overrides, clear=False)
        self.mocked_pswa_config = self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        reset_mock_db_connections()

    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    def test_new_key_task_success_direct_call(self, mock_db_conn_fn_getter):
        """Test weave_script_task directly with a new idempotency key, expecting success."""
        idempotency_key = f"direct-task-new-{uuid.uuid4()}"
        request_id_celery = "test_req_id_direct_new"
        topic = "Direct Task New Topic"
        content = "Direct task new content."

        # DB initially returns no record for this key (default mock behavior)
        mock_conn = mock_db_connection_registry.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None

        # Call the task. Since task_always_eager=True, this will execute synchronously.
        task_result = weave_script_task.apply(
            args=[request_id_celery, content, topic],
            kwargs={'idempotency_key': idempotency_key, 'test_scenario_header': 'default_success'}
        ).get() # .get() will raise exceptions if the task failed

        self.assertIsNotNone(task_result)
        self.assertIn("script_data", task_result) # From test mode
        self.assertEqual(task_result["script_data"]["topic"], topic)

        # Verify DB interactions (similar to the Flask endpoint test)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3) # SELECT, INSERT (proc), UPDATE (compl)

        # 1. SELECT call
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(execute_calls[0][0][1], (idempotency_key, 'pswa.weave_script_task'))

        # 2. INSERT call for PROCESSING
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(execute_calls[1][0][1][4], pswa_config['IDEMPOTENCY_STATUS_PROCESSING'])

        # 3. UPDATE call for COMPLETED
        self.assertIn("UPDATE idempotency_keys SET status = %s, result_payload = %s", execute_calls[2][0][0])
        self.assertEqual(execute_calls[2][0][1][0], pswa_config['IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertIsNotNone(execute_calls[2][0][1][1]) # result_payload

        self.assertEqual(mock_conn.commit.call_count, 2)


    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    def test_completed_key_task_returns_stored_result_direct_call(self, mock_db_conn_fn_getter):
        """Test weave_script_task directly with a completed key, returns stored result."""
        idempotency_key = f"direct-task-completed-{uuid.uuid4()}"
        request_id_celery = "test_req_id_direct_completed"
        topic = "Direct Task Completed Topic"
        content = "Direct task completed content."

        stored_result_payload = {"script_data": {"topic": topic, "title": "Previously Stored Title", "source": "cache_or_previous_run"}, "status_for_metric": "success_from_idempotency"}

        mock_conn = mock_db_connection_registry.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        completed_record = {
            'idempotency_key': idempotency_key,
            'task_name': 'pswa.weave_script_task',
            'status': pswa_config['IDEMPOTENCY_STATUS_COMPLETED'],
            'result_payload': stored_result_payload, # Already a dict, _check_pswa_idempotency_key should handle if it's JSON string
            'locked_at': None
        }
        # If result_payload from DB is a string, _check_pswa_idempotency_key is expected to parse it.
        # For this mock, we provide it as dict as that's what the task expects after parsing.
        cursor_mock.fetchone.return_value = completed_record

        task_result = weave_script_task.apply(
            args=[request_id_celery, content, topic],
            kwargs={'idempotency_key': idempotency_key}
        ).get()

        self.assertEqual(task_result, stored_result_payload)

        # Verify DB: Only a SELECT call, no INSERT or UPDATE for idempotency table.
        execute_calls = cursor_mock.execute.call_args_list
        self.assertEqual(len(execute_calls), 1)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1) # Rollback after SELECT

    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    def test_processing_key_conflict_direct_call(self, mock_db_conn_fn_getter):
        """Test direct task call with a 'processing' key (not timed out) returns conflict."""
        idempotency_key = f"direct-task-processing-{uuid.uuid4()}"

        mock_conn = mock_db_connection_registry.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'pswa.weave_script_task',
            'status': pswa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': dt.now(timezone.utc) # Not timed out
        }
        cursor_mock.fetchone.return_value = processing_record

        task_result = weave_script_task.apply(
            args=["req_id", "content", "topic"], kwargs={'idempotency_key': idempotency_key}
        ).get()

        self.assertEqual(task_result.get("status"), "PROCESSING_CONFLICT")
        self.assertEqual(task_result.get("idempotency_key"), idempotency_key)
        # DB: Only SELECT, no update/insert for idempotency table
        self.assertEqual(len(cursor_mock.execute.call_args_list), 1)
        self.assertEqual(mock_conn.commit.call_count, 0)


    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    def test_processing_key_lock_timeout_direct_call(self, mock_db_conn_fn_getter):
        """Test direct task call with 'processing' key (timed out) re-processes."""
        idempotency_key = f"direct-task-lock-timeout-{uuid.uuid4()}"
        lock_timeout_seconds = pswa_config['IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']
        stale_locked_at = dt.now(timezone.utc) - timedelta(seconds=lock_timeout_seconds + 120)

        mock_conn = mock_db_connection_registry.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        stale_processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'pswa.weave_script_task',
            'status': pswa_config['IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': stale_locked_at
        }
        cursor_mock.fetchone.return_value = stale_processing_record

        # Task should run and succeed (due to test mode)
        task_result = weave_script_task.apply(
            args=["req_id", "content", "Lock Timeout Topic"],
            kwargs={'idempotency_key': idempotency_key, 'test_scenario_header': 'default_success'}
        ).get()

        self.assertIn("script_data", task_result)
        self.assertEqual(task_result["script_data"]["topic"], "Lock Timeout Topic")

        # DB: SELECT, UPDATE (to re-lock PROCESSING), UPDATE (to COMPLETED)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)
        # Check re-lock update
        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and call[0][1][0] == pswa_config['IDEMPOTENCY_STATUS_PROCESSING']
            for call in execute_calls
        )
        self.assertTrue(found_reprocessing_update)
        self.assertEqual(mock_conn.commit.call_count, 2)

    @patch('aethercast.pswa.main.pswa_config', new_callable=MagicMock)
    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    @patch('aethercast.pswa.main._call_aims_service_for_script')
    def test_task_failure_direct_call_marks_failed(self, mock_call_aims, mock_db_conn_fn_getter, mock_dynamic_pswa_config):
        """Test direct task call, if task logic fails, idempotency record is 'failed'."""
        current_config = pswa_config.copy()
        current_config["PSWA_TEST_MODE_ENABLED"] = False # To make it call _call_aims_service_for_script
        mock_dynamic_pswa_config.return_value = current_config


        idempotency_key = f"direct-task-failure-{uuid.uuid4()}"
        mock_call_aims.side_effect = Exception("Simulated direct task internal failure")

        mock_conn = mock_db_connection_registry.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key

        with self.assertRaises(Exception) as context: # Task failure will propagate due to task_eager_propagates
            weave_script_task.apply(
                args=["req_id", "content", "topic"],
                kwargs={'idempotency_key': idempotency_key}
            ).get()
        self.assertIn("Simulated direct task internal failure", str(context.exception))

        # DB: SELECT, INSERT (PROCESSING), UPDATE (FAILED by on_failure)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 2) # SELECT, INSERT, then UPDATE in on_failure

        update_failed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_update_failed = any(
            update_failed_sql_part in str(call[0][0]) and
            call[0][1][0] == pswa_config['IDEMPOTENCY_STATUS_FAILED'] and
            "Simulated direct task internal failure" in call[0][1][2] # error_payload
            for call in execute_calls
        )
        self.assertTrue(found_update_failed, "Update to FAILED status not found or error payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2) # For PROCESSING, then for FAILED in on_failure


    @patch('aethercast.pswa.main.pswa_config', new_callable=MagicMock)
    @patch('aethercast.pswa.main._get_pswa_db_connection_idempotency', side_effect=mock_get_pswa_db_connection_idempotency_side_effect)
    @patch('aethercast.pswa.main._call_aims_service_for_script')
    def test_retry_after_failure_direct_call_success(self, mock_call_aims, mock_db_conn_fn_getter, mock_dynamic_pswa_config):
        """Test direct task call re-processes and succeeds after a previous 'failed' record."""
        current_config = pswa_config.copy()
        current_config["PSWA_TEST_MODE_ENABLED"] = False
        mock_dynamic_pswa_config.return_value = current_config

        idempotency_key = f"direct-task-retry-success-{uuid.uuid4()}"
        
        mock_conn = mock_db_connection_registry.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        failed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'pswa.weave_script_task',
            'status': pswa_config['IDEMPOTENCY_STATUS_FAILED'],
            'error_payload': json.dumps({"error": "Previous failure details"}),
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = failed_record

        # Mock AIMS to succeed on this retry
        mock_call_aims.return_value = {
            "title": "Retry Direct Success", "intro": "Intro", "segments": [], "outro": "Outro",
            "model_id_used": "test-model-retry-direct"
        }

        task_result = weave_script_task.apply(
            args=["req_id", "content", "Retry Success Topic"],
            kwargs={'idempotency_key': idempotency_key}
        ).get()

        self.assertIn("script_data", task_result)
        self.assertEqual(task_result["script_data"]["title"], "Retry Direct Success")

        # DB: SELECT (finds FAILED), UPDATE (to PROCESSING), UPDATE (to COMPLETED)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)

        # Check for update to PROCESSING
        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and call[0][1][0] == pswa_config['IDEMPOTENCY_STATUS_PROCESSING']
            for call in execute_calls
        )
        self.assertTrue(found_reprocessing_update)

        # Check for update to COMPLETED
        update_completed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_completed_update = any(
            update_completed_sql_part in str(call[0][0]) and
            call[0][1][0] == pswa_config['IDEMPOTENCY_STATUS_COMPLETED'] and
            "Retry Direct Success" in call[0][1][1] # result_payload
            for call in execute_calls
        )
        self.assertTrue(found_completed_update)
        self.assertEqual(mock_conn.commit.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
