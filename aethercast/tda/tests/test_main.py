import os
import sys
import json
import uuid
import unittest
from unittest.mock import patch, MagicMock, ANY
from datetime import datetime, timezone, timedelta

# Adjust path to import TDA main module
current_dir = os.path.dirname(os.path.abspath(__file__))
tda_dir = os.path.dirname(current_dir) # Should be /aethercast/tda
aethercast_dir = os.path.dirname(tda_dir) # Should be /aethercast
project_root_dir = os.path.dirname(aethercast_dir) # Should be / (root of repo)

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)
if aethercast_dir not in sys.path:
    sys.path.insert(0, aethercast_dir)

# Imports from TDA service
from aethercast.tda.main import app as flask_app
from aethercast.tda.main import celery_app as tda_celery_app
from aethercast.tda.main import tda_config, load_tda_configuration # For accessing config
from aethercast.tda.main import IDEMPOTENCY_KEY_HEADER, discover_topics_task
from aethercast.tda.main import _get_tda_db_connection # To mock it

# --- Mock Database Connection Registry ---
mock_db_connection_registry_tda = {}

def mock_get_tda_db_connection_side_effect():
    instance_id = os.getpid()
    if instance_id not in mock_db_connection_registry_tda:
        conn = MagicMock(name=f"MockTdaPsycopg2Connection_{instance_id}")
        cursor_mock = MagicMock(name="MockTdaCursor")
        cursor_mock.fetchone.return_value = None # Default: key not found
        cursor_mock.rowcount = 0
        conn.cursor.return_value.__enter__.return_value = cursor_mock
        conn.commit = MagicMock()
        conn.rollback = MagicMock()
        conn.close = MagicMock()
        mock_db_connection_registry_tda[instance_id] = conn
    return mock_db_connection_registry_tda[instance_id]

def reset_mock_tda_db_connections():
    mock_db_connection_registry_tda.clear()

# --- Base Test Case ---
class BaseTdaServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tda_celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
        flask_app.testing = True
        load_tda_configuration()

    def setUp(self):
        self.app = flask_app.test_client()
        reset_mock_tda_db_connections()

        self.test_config_overrides = {
            "TDA_DEBUG_MODE": False,
            "POSTGRES_HOST": "mock_pg_host_tda",
            "POSTGRES_USER": "mock_pg_user_tda",
            "POSTGRES_PASSWORD": "mock_pg_password_tda",
            "POSTGRES_DB": "mock_pg_db_tda",
            "TDA_IDEMPOTENCY_STATUS_PROCESSING": "processing",
            "TDA_IDEMPOTENCY_STATUS_COMPLETED": "completed",
            "TDA_IDEMPOTENCY_STATUS_FAILED": "failed",
            "TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS": 60,
            "USE_REAL_NEWS_API": False, # Default to simulated for most tests
        }
        self.config_patcher = patch.dict(tda_config, self.test_config_overrides, clear=False)
        self.mocked_tda_config = self.config_patcher.start()
        
        # Mock for the NewsAPI sub-task `fetch_news_from_newsapi_task`
        # This mock will return a successful-like structure.
        self.mock_news_task_success_payload = {
            "status": "success",
            "discovered_topics": [{"topic_id": "news_topic_1", "title_suggestion": "News Topic 1"}],
            "message": "Fetched 1 topics."
        }
        # If discover_topics_task directly calls call_real_news_api or identify_topics_from_sources
        # then those would be mocked instead/additionally.
        # Current discover_topics_task calls fetch_news_from_newsapi_task if USE_REAL_NEWS_API is true,
        # or identify_topics_from_sources if false.
        
        self.patch_fetch_news_task = patch('aethercast.tda.main.fetch_news_from_newsapi_task.delay')
        self.mock_fetch_news_task_delay = self.patch_fetch_news_task.start()
        # Configure the mock for .delay().get() or .delay().id for polling
        mock_async_result = MagicMock()
        mock_async_result.id = f"mock_news_task_id_{uuid.uuid4().hex[:8]}"
        mock_async_result.successful.return_value = True
        mock_async_result.result = self.mock_news_task_success_payload
        self.mock_fetch_news_task_delay.return_value = mock_async_result
        
        # Mock for identify_topics_from_sources (used when USE_REAL_NEWS_API is False)
        self.mock_simulated_topics_payload = [{"topic_id": "sim_topic_1", "title_suggestion": "Simulated Topic 1"}]
        self.patch_identify_simulated = patch('aethercast.tda.main.identify_topics_from_sources', return_value=self.mock_simulated_topics_payload)
        self.mock_identify_topics_from_sources = self.patch_identify_simulated.start()


    def tearDown(self):
        self.config_patcher.stop()
        self.patch_fetch_news_task.stop()
        self.patch_identify_simulated.stop()
        reset_mock_tda_db_connections()

# --- Flask Endpoint Idempotency Tests ---
@patch('aethercast.tda.main._get_tda_db_connection', side_effect=mock_get_tda_db_connection_side_effect)
class TestTdaIdempotencyFlask(BaseTdaServiceTest):

    def test_missing_idempotency_key_header(self, mock_db_conn_getter):
        """Test TDA Flask endpoint /discover_topics rejects if X-Idempotency-Key is missing."""
        payload = {"query": "AI"}
        response = self.app.post('/discover_topics', json=payload, headers={})
        self.assertEqual(response.status_code, 400)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "TDA_MISSING_IDEMPOTENCY_KEY")

    def test_new_idempotency_key_task_success(self, mock_db_conn_getter):
        """Test TDA Flask endpoint with a new idempotency key, Celery task runs and succeeds."""
        idempotency_key = f"tda-test-new-{uuid.uuid4()}"
        payload = {"query": "latest tech"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}
        
        # USE_REAL_NEWS_API is False by default, so identify_topics_from_sources will be called by the task
        
        response = self.app.post('/discover_topics', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202, f"Response JSON: {response.get_data(as_text=True)}")
        json_response = response.get_json()
        self.assertIn("task_id", json_response)
        task_id = json_response["task_id"]
        self.assertEqual(json_response.get("idempotency_key_processed"), idempotency_key)

        status_response = self.app.get(json_response["status_url"])
        self.assertEqual(status_response.status_code, 200)
        status_json = status_response.get_json()
        self.assertEqual(status_json["status"], "SUCCESS")
        self.assertIn("discovered_topics", status_json["result"])
        # Check content from the mocked identify_topics_from_sources
        self.assertEqual(status_json["result"]["discovered_topics"], self.mock_simulated_topics_payload)

        mock_conn = mock_db_connection_registry_tda[os.getpid()]
        self.assertTrue(mock_db_conn_getter.called)
        execute_calls = mock_conn.cursor.return_value.__enter__.return_value.execute.call_args_list
        
        self.assertGreaterEqual(len(execute_calls), 3)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(execute_calls[1][0][1][4], tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'])
        self.assertIn("UPDATE idempotency_keys SET status = %s", execute_calls[2][0][0])
        self.assertEqual(execute_calls[2][0][1][0], tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertEqual(mock_conn.commit.call_count, 2)

    def test_completed_idempotency_key_returns_stored_result(self, mock_db_conn_getter):
        """Test TDA Flask endpoint returns stored result for a COMPLETED idempotency key."""
        idempotency_key = f"tda-test-completed-{uuid.uuid4()}"
        payload = {"query": "completed query"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}

        stored_task_result = {
            "status": "success",
            "discovered_topics": [{"topic_id": "tda_completed_1", "title_suggestion": "Stored TDA Topic"}],
            "message": "Successfully discovered 1 topics."
        }
        
        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        completed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'discover_topics_task',
            'status': tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED'],
            'result_payload': stored_task_result,
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = completed_record

        response = self.app.post('/discover_topics', json=payload, headers=headers)
        self.assertEqual(response.status_code, 200) # Now expects 200 due to pre-check
        json_response = response.get_json()
        self.assertEqual(json_response, stored_task_result) # Should be the stored result directly

        # Verify DB: Only one SELECT call from endpoint pre-check.
        execute_calls = cursor_mock.execute.call_args_list
        self.assertEqual(len(execute_calls), 1)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(execute_calls[0][0][1], (idempotency_key, 'discover_topics_task'))
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1) # Rollback after SELECT by endpoint

    def test_processing_idempotency_key_conflict(self, mock_db_conn_getter):
        """Test TDA Flask endpoint returns 409 for a 'processing' and not timed out key due to pre-check."""
        idempotency_key = f"tda-test-processing-{uuid.uuid4()}"
        workflow_id = f"wf-tda-test-processing-{uuid.uuid4()}" # Added for completeness
        payload = {"query": "processing query"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": workflow_id}

        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'discover_topics_task',
            'status': tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'],
            'workflow_id': workflow_id,
            'locked_at': datetime.now(timezone.utc)
        }
        cursor_mock.fetchone.return_value = processing_record

        response = self.app.post('/discover_topics', json=payload, headers=headers)
        self.assertEqual(response.status_code, 409) # Endpoint pre-check returns 409
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "TDA_IDEMPOTENCY_CONFLICT")
        
        # Verify DB: Only one SELECT from endpoint pre-check.
        execute_calls = cursor_mock.execute.call_args_list
        self.assertEqual(len(execute_calls), 1)
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1) # Rollback after SELECT by endpoint

    def test_processing_key_lock_timeout(self, mock_db_conn_getter):
        """Test TDA Flask endpoint re-processes if 'processing' lock has timed out."""
        idempotency_key = f"tda-test-lock-timeout-{uuid.uuid4()}"
        payload = {"query": "lock timeout query"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}

        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        
        lock_timeout_seconds = tda_config['TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']
        stale_locked_at = datetime.now(timezone.utc) - timedelta(seconds=lock_timeout_seconds + 60)

        stale_processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'discover_topics_task',
            'status': tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': stale_locked_at
        }
        cursor_mock.fetchone.return_value = stale_processing_record

        response = self.app.post('/discover_topics', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202, f"Response JSON: {response.get_data(as_text=True)}")
        task_id = response.get_json()["task_id"]

        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 200) # Task should succeed
        status_json = status_response.get_json()
        self.assertEqual(status_json["status"], "SUCCESS")
        self.assertEqual(status_json["result"]["discovered_topics"], self.mock_simulated_topics_payload)


        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        
        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'] and
            call[0][1][3] > stale_locked_at
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_reprocessing_update, "Update to re-lock PROCESSING not found or params incorrect.")

        update_completed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_completed_update = any(
            update_completed_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED']
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_completed_update, "Update to COMPLETED not found or params incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)

    def test_task_failure_marks_idempotency_failed(self, mock_db_conn_getter):
        """Test task failure updates idempotency record to 'failed' via Flask for TDA."""
        idempotency_key = f"tda-test-failure-{uuid.uuid4()}"
        # Pass error_trigger to make the task fail intentionally
        payload = {"query": "failure query", "error_trigger": "tda_error"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}
        
        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key

        response = self.app.post('/discover_topics', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202) # Task accepted
        task_id = response.get_json()["task_id"]

        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 500)
        json_result = status_response.get_json()
        self.assertEqual(json_result["status"], "FAILURE")
        self.assertIn("Simulated TDA error in Celery task", str(json_result["result"]["error"]["message"]))

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3) # SELECT, INSERT (PROC), UPDATE (FAILED)
        
        update_failed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_failed_update = any(
            update_failed_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_FAILED'] and
            "Simulated TDA error" in call[0][1][2] # error_payload
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_failed_update, "Update to FAILED status not found or error payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2) # PROCESSING, then FAILED

    def test_retry_after_failure_succeeds(self, mock_db_conn_getter):
        """Test task re-processes and succeeds after a 'failed' record via Flask for TDA."""
        idempotency_key = f"tda-test-retry-{uuid.uuid4()}"
        # No error_trigger on retry, expect success
        payload = {"query": "retry query"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}

        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        
        failed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'discover_topics_task',
            'status': tda_config['TDA_IDEMPOTENCY_STATUS_FAILED'],
            'error_payload': json.dumps({"error": "Previous TDA failure"}),
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = failed_record

        # Ensure underlying task mocks (simulated data) are set for success
        self.mock_identify_topics_from_sources.return_value = self.mock_simulated_topics_payload
        self.mock_identify_topics_from_sources.side_effect = None


        response = self.app.post('/discover_topics', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202)
        task_id = response.get_json()["task_id"]

        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 200)
        json_result = status_response.get_json()
        self.assertEqual(json_result["status"], "SUCCESS")
        self.assertEqual(json_result["result"]["discovered_topics"], self.mock_simulated_topics_payload)

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)
        
        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING']
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_reprocessing_update, "Update to re-lock PROCESSING not found.")

        update_completed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_completed_update = any(
            update_completed_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED'] and
            json.dumps(self.mock_simulated_topics_payload) in call[0][1][1] # Check result_payload
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_completed_update, "Update to COMPLETED not found or result payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)


# --- Direct Celery Task Idempotency Tests for TDA ---
@patch('aethercast.tda.main._get_tda_db_connection', side_effect=mock_get_tda_db_connection_side_effect)
class TestTdaTaskDirectlyIdempotency(BaseTdaServiceTest):

    def test_new_key_task_success_direct_call(self, mock_db_conn_getter):
        """Test discover_topics_task directly with a new idempotency key."""
        idempotency_key = f"tda-direct-new-{uuid.uuid4()}"
        
        # Ensure underlying data source mock (simulated by default) is set for success
        self.mock_identify_topics_from_sources.return_value = self.mock_simulated_topics_payload
        self.mock_identify_topics_from_sources.side_effect = None

        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key

        task_result = discover_topics_task.apply(
            kwargs={
                'request_id_main': "req_tda_direct_new", 'query': "direct new query", 'limit': 3,
                'use_real_news_api_flag': False, # Uses identify_topics_from_sources mock
                'idempotency_key': idempotency_key
            }
        ).get()

        self.assertIsNotNone(task_result)
        self.assertEqual(task_result.get("status"), "success")
        self.assertEqual(task_result.get("discovered_topics"), self.mock_simulated_topics_payload)

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(execute_calls[1][0][1][4], tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'])
        self.assertIn("UPDATE idempotency_keys SET status = %s", execute_calls[2][0][0])
        self.assertEqual(execute_calls[2][0][1][0], tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertEqual(mock_conn.commit.call_count, 2)

    def test_completed_key_task_returns_stored_result_direct_call(self, mock_db_conn_getter):
        """Test direct task call with a COMPLETED key returns stored result (TDA)."""
        idempotency_key = f"tda-direct-completed-{uuid.uuid4()}"
        stored_result_payload = {
            "status": "success",
            "discovered_topics": [{"topic_id": "tda_direct_completed_1", "title_suggestion": "Stored TDA Topic Direct"}]
        }
        
        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        completed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'discover_topics_task',
            'status': tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED'],
            'result_payload': stored_result_payload,
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = completed_record

        # Ensure actual data fetching functions are not called
        self.mock_identify_topics_from_sources.reset_mock()
        self.mock_fetch_news_task_delay.reset_mock()

        task_result = discover_topics_task.apply(
            kwargs={
                'request_id_main': "req_tda_direct_compl", 'query': "direct completed query", 'limit': 3,
                'use_real_news_api_flag': False,
                'idempotency_key': idempotency_key
            }
        ).get()

        self.assertEqual(task_result, stored_result_payload)
        self.mock_identify_topics_from_sources.assert_not_called()
        self.mock_fetch_news_task_delay.assert_not_called()

        execute_calls = cursor_mock.execute.call_args_list
        self.assertEqual(len(execute_calls), 1) # Only SELECT
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1)

    def test_processing_key_conflict_direct_call(self, mock_db_conn_getter):
        """Test direct task call with 'processing' key (not timed out) returns conflict (TDA)."""
        idempotency_key = f"tda-direct-processing-conflict-{uuid.uuid4()}"
        
        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'discover_topics_task',
            'status': tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': datetime.now(timezone.utc)
        }
        cursor_mock.fetchone.return_value = processing_record

        task_result = discover_topics_task.apply(
            kwargs={'request_id_main': 'req_id', 'query': 'query', 'limit': 1,
                    'use_real_news_api_flag': False, 'idempotency_key': idempotency_key}
        ).get()

        self.assertEqual(task_result.get("status"), "PROCESSING_CONFLICT")
        self.assertEqual(task_result.get("idempotency_key"), idempotency_key)
        self.assertEqual(len(cursor_mock.execute.call_args_list), 1)
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1)

    def test_processing_key_lock_timeout_direct_call(self, mock_db_conn_getter):
        """Test direct task call with 'processing' key (timed out) re-processes (TDA)."""
        idempotency_key = f"tda-direct-lock-timeout-{uuid.uuid4()}"
        lock_timeout_seconds = tda_config['TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']
        stale_locked_at = datetime.now(timezone.utc) - timedelta(seconds=lock_timeout_seconds + 120)

        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        stale_processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'discover_topics_task',
            'status': tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': stale_locked_at
        }
        cursor_mock.fetchone.return_value = stale_processing_record
        
        self.mock_identify_topics_from_sources.return_value = self.mock_simulated_topics_payload
        self.mock_identify_topics_from_sources.side_effect = None

        task_result = discover_topics_task.apply(
            kwargs={'request_id_main': 'req_id', 'query': 'Lock Timeout Query TDA', 'limit': 1,
                    'use_real_news_api_flag': False, 'idempotency_key': idempotency_key}
        ).get()

        self.assertEqual(task_result.get("status"), "success")
        self.assertEqual(task_result.get("discovered_topics"), self.mock_simulated_topics_payload)

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)

        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'] and
            call[0][1][3] > stale_locked_at
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_reprocessing_update, "Update to re-lock PROCESSING not found or params incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)

    def test_task_failure_direct_call_marks_idempotency_failed(self, mock_db_conn_getter):
        """Test direct task call, if task logic fails, idempotency record is 'failed' (TDA)."""
        idempotency_key = f"tda-direct-failure-{uuid.uuid4()}"

        # Use error_trigger to cause failure in the task
        error_trigger_message = "Simulated TDA direct task internal failure"
        
        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key

        with self.assertRaises(Exception) as context:
            discover_topics_task.apply(
                kwargs={'request_id_main': 'req_id', 'query': 'query', 'limit': 1,
                        'use_real_news_api_flag': False,
                        'idempotency_key': idempotency_key,
                        'error_trigger': 'tda_error'} # Trigger internal failure
            ).get()
        self.assertIn("Simulated TDA error in Celery task", str(context.exception)) # Matches error_trigger

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)

        update_failed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_update_failed = any(
            update_failed_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_FAILED'] and
            "Simulated TDA error" in call[0][1][2] # error_payload check
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_update_failed, "Update to FAILED status not found or error payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)

    def test_retry_after_failure_direct_call_succeeds(self, mock_db_conn_getter):
        """Test direct task call re-processes and succeeds after 'failed' record (TDA)."""
        idempotency_key = f"tda-direct-retry-{uuid.uuid4()}"

        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        failed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'discover_topics_task',
            'status': tda_config['TDA_IDEMPOTENCY_STATUS_FAILED'],
            'error_payload': json.dumps({"error": "Previous direct TDA failure"}),
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = failed_record

        self.mock_identify_topics_from_sources.return_value = self.mock_simulated_topics_payload
        self.mock_identify_topics_from_sources.side_effect = None

        task_result = discover_topics_task.apply(
            kwargs={'request_id_main': 'req_id', 'query': 'Retry Query TDA', 'limit': 1,
                    'use_real_news_api_flag': False,
                    'idempotency_key': idempotency_key,
                    'error_trigger': None} # No error on retry
        ).get()

        self.assertEqual(task_result.get("status"), "success")
        self.assertEqual(task_result.get("discovered_topics"), self.mock_simulated_topics_payload)

        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)

        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING']
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_reprocessing_update, "Update to re-lock PROCESSING not found.")

        update_completed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_completed_update = any(
            update_completed_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED'] and
            json.dumps(self.mock_simulated_topics_payload) in call[0][1][1] # result_payload
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_completed_update, "Update to COMPLETED not found or result payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
