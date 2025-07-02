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
# Assuming tda_main will be used for _save_topic_to_db if it's in tda.main
from aethercast.tda import main as tda_main


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
        
        self.mock_news_task_success_payload = {
            "status": "success",
            "discovered_topics": [{"topic_id": "news_topic_1", "title_suggestion": "News Topic 1"}],
            "message": "Fetched 1 topics."
        }
        
        self.patch_fetch_news_task = patch('aethercast.tda.main.fetch_news_from_newsapi_task.delay')
        self.mock_fetch_news_task_delay = self.patch_fetch_news_task.start()
        mock_async_result = MagicMock()
        mock_async_result.id = f"mock_news_task_id_{uuid.uuid4().hex[:8]}"
        mock_async_result.successful.return_value = True
        mock_async_result.result = self.mock_news_task_success_payload
        self.mock_fetch_news_task_delay.return_value = mock_async_result
        
        self.mock_simulated_topics_payload = [{"topic_id": "sim_topic_1", "title_suggestion": "Simulated Topic 1"}]
        self.patch_identify_simulated = patch('aethercast.tda.main.identify_topics_from_sources', return_value=self.mock_simulated_topics_payload)
        self.mock_identify_topics_from_sources = self.patch_identify_simulated.start()


    def tearDown(self):
        self.config_patcher.stop()
        self.patch_fetch_news_task.stop()
        self.patch_identify_simulated.stop()
        reset_mock_tda_db_connections()

@patch('aethercast.tda.main._get_tda_db_connection', side_effect=mock_get_tda_db_connection_side_effect)
class TestTdaIdempotencyFlask(BaseTdaServiceTest):

    def test_missing_idempotency_key_header(self, mock_db_conn_getter):
        payload = {"query": "AI"}
        response = self.app.post('/discover_topics', json=payload, headers={})
        self.assertEqual(response.status_code, 400)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "TDA_MISSING_IDEMPOTENCY_KEY")

    def test_new_idempotency_key_task_success(self, mock_db_conn_getter):
        idempotency_key = f"tda-test-new-{uuid.uuid4()}"
        payload = {"query": "latest tech"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}
        
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
        self.assertEqual(response.status_code, 200)
        json_response = response.get_json()
        self.assertEqual(json_response, stored_task_result)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertEqual(len(execute_calls), 1)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(execute_calls[0][0][1], (idempotency_key, 'discover_topics_task'))
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1)

    def test_processing_idempotency_key_conflict(self, mock_db_conn_getter):
        idempotency_key = f"tda-test-processing-{uuid.uuid4()}"
        workflow_id = f"wf-tda-test-processing-{uuid.uuid4()}"
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
        self.assertEqual(response.status_code, 409)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "TDA_IDEMPOTENCY_CONFLICT")
        execute_calls = cursor_mock.execute.call_args_list
        self.assertEqual(len(execute_calls), 1)
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1)

    def test_processing_key_lock_timeout(self, mock_db_conn_getter):
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
        self.assertEqual(status_response.status_code, 200)
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
        idempotency_key = f"tda-test-failure-{uuid.uuid4()}"
        payload = {"query": "failure query", "error_trigger": "tda_error"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}
        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None
        response = self.app.post('/discover_topics', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202)
        task_id = response.get_json()["task_id"]
        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 500)
        json_result = status_response.get_json()
        self.assertEqual(json_result["status"], "FAILURE")
        self.assertIn("Simulated TDA error in Celery task", str(json_result["result"]["error"]["message"]))
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)
        update_failed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_failed_update = any(
            update_failed_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_FAILED'] and
            "Simulated TDA error" in call[0][1][2]
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_failed_update, "Update to FAILED status not found or error payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)

    def test_retry_after_failure_succeeds(self, mock_db_conn_getter):
        idempotency_key = f"tda-test-retry-{uuid.uuid4()}"
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
            json.dumps(self.mock_simulated_topics_payload) in call[0][1][1]
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_completed_update, "Update to COMPLETED not found or result payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)


@patch('aethercast.tda.main._get_tda_db_connection', side_effect=mock_get_tda_db_connection_side_effect)
class TestTdaTaskDirectlyIdempotency(BaseTdaServiceTest):

    def test_new_key_task_success_direct_call(self, mock_db_conn_getter):
        idempotency_key = f"tda-direct-new-{uuid.uuid4()}"
        self.mock_identify_topics_from_sources.return_value = self.mock_simulated_topics_payload
        self.mock_identify_topics_from_sources.side_effect = None
        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None
        task_result = discover_topics_task.apply(
            kwargs={
                'request_id_main': "req_tda_direct_new", 'query': "direct new query", 'limit': 3,
                'use_real_news_api_flag': False,
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
        self.assertEqual(len(execute_calls), 1)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1)

    def test_processing_key_conflict_direct_call(self, mock_db_conn_getter):
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
        idempotency_key = f"tda-direct-failure-{uuid.uuid4()}"
        error_trigger_message = "Simulated TDA direct task internal failure"
        mock_conn = mock_db_connection_registry_tda.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None
        with self.assertRaises(Exception) as context:
            discover_topics_task.apply(
                kwargs={'request_id_main': 'req_id', 'query': 'query', 'limit': 1,
                        'use_real_news_api_flag': False,
                        'idempotency_key': idempotency_key,
                        'error_trigger': 'tda_error'}
            ).get()
        self.assertIn("Simulated TDA error in Celery task", str(context.exception))
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)
        update_failed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_update_failed = any(
            update_failed_sql_part in str(call[0][0]) and
            call[0][1][0] == tda_config['TDA_IDEMPOTENCY_STATUS_FAILED'] and
            "Simulated TDA error" in call[0][1][2]
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_update_failed, "Update to FAILED status not found or error payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)

    def test_retry_after_failure_direct_call_succeeds(self, mock_db_conn_getter):
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
                    'error_trigger': None}
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
            json.dumps(self.mock_simulated_topics_payload) in call[0][1][1]
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_completed_update, "Update to COMPLETED not found or result payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)


class TestSaveTopicToDb(BaseTdaServiceTest):

    @patch('aethercast.tda.main._get_tda_db_connection')
    def test_save_topic_summary_no_truncation(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        short_summary = "This is a short summary."
        topic = {"topic_id": "t1", "title_suggestion": "Short Sum", "summary": short_summary}
        tda_main._save_topic_to_db(topic)
        args, _ = mock_cursor.execute.call_args
        self.assertEqual(args[1][3], short_summary)

    @patch('aethercast.tda.main._get_tda_db_connection')
    def test_save_topic_summary_truncation_with_space(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        with patch.object(tda_main, 'MAX_SUMMARY_LENGTH', 50):
            test_summary_long = "This is a test sentence that is very long and should be truncated at a word boundary. This part should be cut off."
            expected_truncated = "This is a test sentence that is very long and..."
            topic = {"topic_id": "t2", "title_suggestion": "Long Sum Space", "summary": test_summary_long}
            tda_main._save_topic_to_db(topic)
            args, _ = mock_cursor.execute.call_args
            self.assertEqual(args[1][3], expected_truncated)
            self.assertTrue(len(args[1][3]) <= 50)


    @patch('aethercast.tda.main._get_tda_db_connection')
    def test_save_topic_summary_truncation_no_space_hard_cut(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        long_word_summary = "a" * (tda_main.MAX_SUMMARY_LENGTH + 50)
        with patch.object(tda_main, 'MAX_SUMMARY_LENGTH', 30):
            expected_truncated = ("a" * (30 - 3)) + "..."
            topic = {"topic_id": "t3", "title_suggestion": "Long Word Sum", "summary": long_word_summary}
            tda_main._save_topic_to_db(topic)
            args, _ = mock_cursor.execute.call_args
            self.assertEqual(args[1][3], expected_truncated)
            self.assertTrue(len(args[1][3]) <= 30)

    @patch('aethercast.tda.main._get_tda_db_connection')
    def test_save_topic_summary_none_or_empty(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        topic_none = {"topic_id": "t4", "title_suggestion": "None Sum", "summary": None}
        tda_main._save_topic_to_db(topic_none)
        args_none, _ = mock_cursor.execute.call_args
        self.assertIsNone(args_none[1][3])
        topic_empty = {"topic_id": "t5", "title_suggestion": "Empty Sum", "summary": ""}
        tda_main._save_topic_to_db(topic_empty)
        args_empty, _ = mock_cursor.execute.call_args
        self.assertEqual(args_empty[1][3], "")


    @patch('aethercast.tda.main._get_tda_db_connection')
    def test_save_topic_summary_at_max_length(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        summary_at_max = "a" * tda_main.MAX_SUMMARY_LENGTH
        topic = {"topic_id": "t6", "title_suggestion": "Max Length Sum", "summary": summary_at_max}
        tda_main._save_topic_to_db(topic)
        args, _ = mock_cursor.execute.call_args
        self.assertEqual(args[1][3], summary_at_max)


class TestTdaCeleryLogging(BaseTdaServiceTest):
    @patch('aethercast.tda.main.app.logger')
    @patch('aethercast.tda.main.call_real_news_api')
    @patch('aethercast.tda.main._get_tda_db_connection', side_effect=mock_get_tda_db_connection_side_effect)
    def test_fetch_news_from_newsapi_task_json_logging(self, mock_db_conn_getter, mock_call_real_news_api, mock_app_logger):
        mock_call_real_news_api.return_value = [{"topic_id": "news1", "title_suggestion": "Mock News Article"}]
        mock_conn_instance = mock_get_tda_db_connection_side_effect()
        mock_cursor_instance = mock_conn_instance.cursor.return_value.__enter__.return_value
        mock_cursor_instance.fetchone.return_value = None
        task_request_id = f"tda_log_test_req_{uuid.uuid4().hex[:6]}"
        task_idempotency_key = f"tda_log_test_idem_{uuid.uuid4().hex[:6]}"
        task_workflow_id = f"wf_tda_log_test_{uuid.uuid4().hex[:6]}"
        tda_main.fetch_news_from_newsapi_task(
            request_id_celery=task_request_id,
            keywords=["test"],
            idempotency_key=task_idempotency_key,
            workflow_id=task_workflow_id
        )
        self.assertTrue(mock_app_logger.info.called)
        found_log_call = None
        celery_task_id_from_call = None
        for call_args_tuple in mock_app_logger.info.call_args_list:
            message_arg = call_args_tuple[0][0]
            if "TDA NewsAPI Task" in message_arg and "Starting" in message_arg:
                found_log_call = call_args_tuple
                if found_log_call[1].get('extra', {}).get('task_id'):
                     celery_task_id_from_call = found_log_call[1]['extra']['task_id']
                break
        self.assertIsNotNone(found_log_call, "Expected starting log message not found.")
        if found_log_call:
            log_kwargs = found_log_call[1]
            self.assertIn('extra', log_kwargs)
            log_extra_dict = log_kwargs['extra']
            self.assertEqual(log_extra_dict.get('orig_req_id'), task_request_id)
            self.assertEqual(log_extra_dict.get('idempotency_key'), task_idempotency_key)
            self.assertEqual(log_extra_dict.get('workflow_id'), task_workflow_id)
            self.assertIn('task_id', log_extra_dict)
            if celery_task_id_from_call:
                 self.assertEqual(log_extra_dict.get('task_id'), celery_task_id_from_call)

if __name__ == '__main__':
    unittest.main(verbosity=2)
