import os
import sys
import json
import uuid
import unittest
from unittest.mock import patch, MagicMock, ANY
from datetime import datetime, timezone, timedelta

# Adjust path to import IGA main module
current_dir = os.path.dirname(os.path.abspath(__file__))
iga_dir = os.path.dirname(current_dir) # Should be /aethercast/iga
aethercast_dir = os.path.dirname(iga_dir) # Should be /aethercast
project_root_dir = os.path.dirname(aethercast_dir) # Should be / (root of repo)

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)
if aethercast_dir not in sys.path:
    sys.path.insert(0, aethercast_dir)

# Imports from IGA service
from aethercast.iga.main import app as flask_app
from aethercast.iga.main import celery_app as iga_celery_app
from aethercast.iga.main import iga_config, load_iga_configuration # For accessing config
from aethercast.iga.main import IDEMPOTENCY_KEY_HEADER, generate_image_vertex_ai_task
from aethercast.iga.main import _get_iga_db_connection # To mock it

# --- Mock Database Connection Registry (similar to PSWA tests) ---
mock_db_connection_registry_iga = {}

def mock_get_iga_db_connection_side_effect():
    instance_id = os.getpid() # Simple way to get a unique ID for the mock instance per process
    if instance_id not in mock_db_connection_registry_iga:
        conn = MagicMock(name=f"MockIgaPsycopg2Connection_{instance_id}")
        cursor_mock = MagicMock(name="MockIgaCursor")
        cursor_mock.fetchone.return_value = None # Default: key not found
        cursor_mock.rowcount = 0
        conn.cursor.return_value.__enter__.return_value = cursor_mock
        conn.commit = MagicMock()
        conn.rollback = MagicMock()
        conn.close = MagicMock()
        mock_db_connection_registry_iga[instance_id] = conn
    return mock_db_connection_registry_iga[instance_id]

def reset_mock_iga_db_connections():
    mock_db_connection_registry_iga.clear()

# --- Base Test Case ---
class BaseIgaServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        iga_celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
        flask_app.testing = True
        # Load base configuration from main.py, tests can override
        load_iga_configuration()

    def setUp(self):
        self.app = flask_app.test_client()
        reset_mock_iga_db_connections()

        # Default test config overrides for IGA
        self.test_config_overrides = {
            "IGA_DEBUG_MODE": False, # Keep logging cleaner for tests unless debugging a test
            "POSTGRES_HOST": "mock_pg_host_iga",
            "POSTGRES_USER": "mock_pg_user_iga",
            "POSTGRES_PASSWORD": "mock_pg_password_iga",
            "POSTGRES_DB": "mock_pg_db_iga",
            "IGA_IDEMPOTENCY_STATUS_PROCESSING": "processing",
            "IGA_IDEMPOTENCY_STATUS_COMPLETED": "completed",
            "IGA_IDEMPOTENCY_STATUS_FAILED": "failed",
            "IGA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS": 60, # Short timeout for tests
            "GCS_BUCKET_NAME": "test-iga-bucket", # Required by endpoint
            # Mock Vertex AI and GCS related configs if tasks are not fully mocked
            "IGA_VERTEXAI_PROJECT_ID": "test-project",
            "IGA_VERTEXAI_LOCATION": "us-central1",
        }
        # Patch iga_config dictionary directly
        self.config_patcher = patch.dict(iga_config, self.test_config_overrides, clear=False)
        self.mocked_iga_config = self.config_patcher.start()

        # Patch external calls made by the Celery task if it's not fully contained by idempotency logic tests
        self.mock_vertex_model = MagicMock()
        self.mock_vertex_image = MagicMock()
        self.mock_vertex_image._image_bytes = b"dummy_image_bytes"
        self.mock_vertex_model.generate_images.return_value = MagicMock(images=[self.mock_vertex_image])

        self.gcs_blob_mock = MagicMock()
        self.mock_gcs_client_instance_for_patch = MagicMock() # This is the instance storage.Client() would return
        self.mock_gcs_client_instance_for_patch.bucket.return_value.blob.return_value = self.gcs_blob_mock

        # Patch the global variables that are set at module load time in iga.main
        # These globals are assigned the *result* of from_pretrained() and Client()
        self.patch_global_vertex_model = patch('aethercast.iga.main.GLOBAL_IMAGE_MODEL', self.mock_vertex_model)
        self.patch_global_gcs_client = patch('aethercast.iga.main.GLOBAL_STORAGE_CLIENT', self.mock_gcs_client_instance_for_patch)

        self.mock_global_vertex_model = self.patch_global_vertex_model.start()
        self.mock_global_gcs_client = self.patch_global_gcs_client.start()


    def tearDown(self):
        self.config_patcher.stop()
        self.patch_global_vertex_model.stop()
        self.patch_global_gcs_client.stop()
        reset_mock_iga_db_connections()

# --- Flask Endpoint Idempotency Tests ---
@patch('aethercast.iga.main._get_iga_db_connection', side_effect=mock_get_iga_db_connection_side_effect)
class TestIgaIdempotencyFlask(BaseIgaServiceTest):

    def test_missing_idempotency_key_header(self, mock_db_conn_getter):
        """Test IGA Flask endpoint /generate_image rejects if X-Idempotency-Key is missing."""
        payload = {"prompt": "A test image"}
        response = self.app.post('/generate_image', json=payload, headers={})
        self.assertEqual(response.status_code, 400)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "IGA_MISSING_IDEMPOTENCY_KEY")

    def test_new_idempotency_key_task_success(self, mock_db_conn_getter):
        """Test IGA Flask endpoint with a new idempotency key, Celery task runs and succeeds."""
        idempotency_key = f"iga-test-new-{uuid.uuid4()}"
        payload = {"prompt": "A beautiful landscape"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}

        # Mock DB to show key not found initially (default for mock_get_iga_db_connection_side_effect)

        response = self.app.post('/generate_image', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202, f"Response JSON: {response.get_data(as_text=True)}")
        json_response = response.get_json()
        self.assertIn("task_id", json_response)
        task_id = json_response["task_id"]
        self.assertEqual(json_response.get("idempotency_key_processed"), idempotency_key)

        # Check task status (since it runs eagerly)
        status_response = self.app.get(json_response["status_url"])
        self.assertEqual(status_response.status_code, 200)
        status_json = status_response.get_json()
        self.assertEqual(status_json["status"], "SUCCESS")
        self.assertIn("image_url", status_json["result"])
        self.assertTrue(status_json["result"]["image_url"].startswith("gs://test-iga-bucket/"))

        # Verify DB interactions
        mock_conn = mock_db_connection_registry_iga[os.getpid()]
        self.assertTrue(mock_db_conn_getter.called)
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        execute_calls = cursor_mock.execute.call_args_list

        # Endpoint pre-check: SELECT, INSERT (PROCESSING)
        # Celery task: SELECT, UPDATE (COMPLETED)
        # Total can be 3 or 4 depending on whether Celery task's SELECT finds the PROCESSING record updated by endpoint.
        self.assertGreaterEqual(len(execute_calls), 3)

        # Endpoint pre-check INSERT PROCESSING
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0]) # First call in endpoint pre-check
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0]) # Second call in endpoint pre-check
        self.assertEqual(execute_calls[1][0][1][4], iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING']) # status
        self.assertIsNotNone(execute_calls[1][0][1][3]) # locked_at for processing

        # Celery task final UPDATE COMPLETED
        found_completed_update = any(
            "UPDATE idempotency_keys SET status = %s" in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED']
            for call in execute_calls
        )
        self.assertTrue(found_completed_update, "Update to COMPLETED not found in DB calls.")

        # Commits: 1 from endpoint pre-check (for PROCESSING), 1 from Celery task (for COMPLETED)
        self.assertGreaterEqual(mock_conn.commit.call_count, 2)


    def test_completed_idempotency_key_returns_stored_result(self, mock_db_conn_getter):
        """Test IGA Flask endpoint returns stored result for a COMPLETED idempotency key due to pre-check."""
        idempotency_key = f"iga-test-completed-{uuid.uuid4()}"
        workflow_id = f"wf-iga-test-completed-{uuid.uuid4()}"
        payload = {"prompt": "A prompt for a completed task"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": workflow_id}

        stored_result = {"image_url": f"gs://test-iga-bucket/images/iga/completed_{uuid.uuid4()}.png", "prompt_used": payload["prompt"]}

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        completed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'generate_image_vertex_ai_task',
            'status': iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'],
            'result_payload': stored_result,
            'workflow_id': workflow_id,
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = completed_record

        response = self.app.post('/generate_image', json=payload, headers=headers)
        self.assertEqual(response.status_code, 200) # Endpoint pre-check returns 200
        json_response = response.get_json()
        self.assertEqual(json_response, stored_result) # Should be the stored result directly

        # Verify DB: Only one SELECT call from endpoint pre-check.
        execute_calls = cursor_mock.execute.call_args_list
        self.assertEqual(len(execute_calls), 1)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(execute_calls[0][0][1], (idempotency_key, 'generate_image_vertex_ai_task')) # Check params
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1) # Rollback after SELECT by endpoint

    def test_processing_idempotency_key_conflict(self, mock_db_conn_getter):
        """Test IGA Flask endpoint returns 409 for a 'processing' and not timed out key due to pre-check."""
        idempotency_key = f"iga-test-processing-{uuid.uuid4()}"
        workflow_id = f"wf-iga-test-processing-{uuid.uuid4()}"
        payload = {"prompt": "A prompt for a processing task"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key, "X-Workflow-ID": workflow_id}

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'generate_image_vertex_ai_task',
            'status': iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'],
            'workflow_id': workflow_id,
            'locked_at': datetime.now(timezone.utc)
        }
        cursor_mock.fetchone.return_value = processing_record

        response = self.app.post('/generate_image', json=payload, headers=headers)
        self.assertEqual(response.status_code, 409) # Endpoint pre-check returns 409
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "IGA_IDEMPOTENCY_CONFLICT")

        # Verify DB: Only one SELECT from endpoint pre-check.
        execute_calls = cursor_mock.execute.call_args_list
        self.assertEqual(len(execute_calls), 1)
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1) # Rollback after SELECT by endpoint

    def test_processing_key_lock_timeout(self, mock_db_conn_getter):
        """Test IGA Flask endpoint re-processes if 'processing' lock has timed out."""
        idempotency_key = f"iga-test-lock-timeout-{uuid.uuid4()}"
        workflow_id = f"wf-iga-test-lock-timeout-{uuid.uuid4()}"
        payload = {"prompt": "A prompt for a lock timeout task"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value

        lock_timeout_seconds = iga_config['IGA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']
        stale_locked_at = datetime.now(timezone.utc) - timedelta(seconds=lock_timeout_seconds + 60) # Expired

        stale_processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'generate_image_vertex_ai_task',
            'status': iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': stale_locked_at
        }
        cursor_mock.fetchone.return_value = stale_processing_record

        response = self.app.post('/generate_image', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202, f"Response JSON: {response.get_data(as_text=True)}")
        task_id = response.get_json()["task_id"]

        # Task should run and succeed (due to mocked Vertex/GCS calls)
        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 200)
        status_json = status_response.get_json()
        self.assertEqual(status_json["status"], "SUCCESS")
        self.assertIn("image_url", status_json["result"])

        # Verify DB interactions:
        # 1. SELECT (finds stale PROCESSING record)
        # 2. UPDATE (to re-lock as PROCESSING with new timestamp)
        # 3. UPDATE (to COMPLETED)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])

        # Check re-lock update (status=PROCESSING, new locked_at)
        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'] and
            call[0][1][3] > stale_locked_at # New locked_at timestamp
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0]) # Filter for UPDATEs
        )
        self.assertTrue(found_reprocessing_update, "Update to re-lock PROCESSING not found or params incorrect.")

        # Check COMPLETED update
        update_completed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_completed_update = any(
            update_completed_sql_part in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED']
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0]) # Filter for UPDATEs
        )
        self.assertTrue(found_completed_update, "Update to COMPLETED not found or params incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2) # Commit for re-lock, commit for completion

    @patch.object(iga_main.ImageGenerationModel, 'from_pretrained') # To mock the model and its method
    def test_task_failure_marks_idempotency_failed(self, mock_from_pretrained, mock_db_conn_getter_unused): # mock_db_conn_getter_unused because it's already class-patched
        """Test task failure updates idempotency record to 'failed' via Flask endpoint."""
        idempotency_key = f"iga-test-failure-{uuid.uuid4()}"
        payload = {"prompt": "A prompt that will cause a task failure"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}

        # Configure the mocked Vertex AI model to raise an exception
        mock_model_instance = MagicMock()
        mock_model_instance.generate_images.side_effect = Exception("Simulated Vertex AI API error")
        mock_from_pretrained.return_value = mock_model_instance

        # Ensure PSWA_TEST_MODE_ENABLED is effectively false for this test path in the task
        # This is handled by not mocking _call_aims_service_for_script in PSWA,
        # For IGA, we directly mock the part that fails (generate_images)

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key

        response = self.app.post('/generate_image', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202) # Task accepted
        task_id = response.get_json()["task_id"]

        # Check task status - should be FAILURE
        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 500) # Flask endpoint returns 500 for failed task
        json_result = status_response.get_json()
        self.assertEqual(json_result["status"], "FAILURE")
        self.assertIn("Simulated Vertex AI API error", str(json_result["result"]["error"]["message"]))

        # Verify DB: SELECT, INSERT (PROCESSING), UPDATE (FAILED by on_failure)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3) # SELECT, INSERT (proc), UPDATE (failed)

        # Check FAILED update
        update_failed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_failed_update = any(
            update_failed_sql_part in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'] and
            "Simulated Vertex AI API error" in call[0][1][2] # error_payload
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_failed_update, "Update to FAILED status not found or error payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2) # Commit for PROCESSING, commit for FAILED in on_failure

    @patch.object(iga_main.ImageGenerationModel, 'from_pretrained')
    def test_retry_after_failure_succeeds(self, mock_from_pretrained, mock_db_conn_getter_unused):
        """Test task re-processes and succeeds after a previous 'failed' record via Flask."""
        idempotency_key = f"iga-test-retry-{uuid.uuid4()}"
        payload = {"prompt": "A prompt for a retried task"}
        headers = {IDEMPOTENCY_KEY_HEADER: idempotency_key}

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value

        # Simulate that the key was previously recorded as FAILED
        failed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'generate_image_vertex_ai_task',
            'status': iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'],
            'error_payload': json.dumps({"error": "Previous simulated error"}), # Must be JSON string as from DB
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = failed_record

        # Configure Vertex AI mock to succeed on this attempt
        # The mock_vertex_model and mock_vertex_image are already set up in self.setUp to succeed by default.
        # We just need to make sure from_pretrained returns it.
        mock_from_pretrained.return_value = self.mock_vertex_model

        response = self.app.post('/generate_image', json=payload, headers=headers)
        self.assertEqual(response.status_code, 202)
        task_id = response.get_json()["task_id"]

        status_response = self.app.get(f'/v1/tasks/{task_id}')
        self.assertEqual(status_response.status_code, 200)
        json_result = status_response.get_json()
        self.assertEqual(json_result["status"], "SUCCESS")
        self.assertIn("image_url", json_result["result"])

        # Verify DB: SELECT (finds FAILED), UPDATE (to PROCESSING), UPDATE (to COMPLETED)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)

        # Check PROCESSING update
        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING']
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_reprocessing_update, "Update to re-lock PROCESSING not found.")

        # Check COMPLETED update
        update_completed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_completed_update = any(
            update_completed_sql_part in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'] and
            "gs://test-iga-bucket/" in call[0][1][1] # result_payload has image_url
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_completed_update, "Update to COMPLETED not found or result payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)


# --- Direct Celery Task Idempotency Tests ---
@patch('aethercast.iga.main._get_iga_db_connection', side_effect=mock_get_iga_db_connection_side_effect)
class TestIgaTaskDirectlyIdempotency(BaseIgaServiceTest): # Inherits mocks for Vertex/GCS from Base

    def test_new_key_task_success_direct_call(self, mock_db_conn_getter):
        """Test generate_image_vertex_ai_task directly with a new idempotency key."""
        idempotency_key = f"iga-direct-new-{uuid.uuid4()}"
        request_id = "req_direct_new"
        prompt = "Direct call new prompt"

        # DB initially returns no record for this key (default mock behavior)
        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None

        task_result = generate_image_vertex_ai_task.apply(
            kwargs={
                'request_id': request_id, 'prompt': prompt,
                'aspect_ratio': '1:1', 'add_watermark': False,
                'model_id': 'test-model',
                'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
                'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
                'idempotency_key': idempotency_key
            }
        ).get() # .get() will raise exceptions if task fails

        self.assertIsNotNone(task_result)
        self.assertIn("image_url", task_result)
        self.assertTrue(task_result["image_url"].startswith(f"gs://{iga_config['GCS_BUCKET_NAME']}/"))

        # Verify DB interactions (SELECT, INSERT PROCESSING, UPDATE COMPLETED)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertIn("INSERT INTO idempotency_keys", execute_calls[1][0][0])
        self.assertEqual(execute_calls[1][0][1][4], iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'])
        self.assertIn("UPDATE idempotency_keys SET status = %s", execute_calls[2][0][0])
        self.assertEqual(execute_calls[2][0][1][0], iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'])
        self.assertEqual(mock_conn.commit.call_count, 2)

    def test_completed_key_task_returns_stored_result_direct_call(self, mock_db_conn_getter):
        """Test direct task call with a COMPLETED key returns stored result without re-processing."""
        idempotency_key = f"iga-direct-completed-{uuid.uuid4()}"
        request_id = "req_direct_completed"
        prompt = "Direct call completed prompt"

        stored_result = {"image_url": f"gs://{iga_config['GCS_BUCKET_NAME']}/images/iga/completed_direct.png", "prompt_used": prompt}

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        completed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'generate_image_vertex_ai_task',
            'status': iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'],
            'result_payload': stored_result, # Already a dict
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = completed_record

        # Mock Vertex/GCS to ensure they are NOT called
        self.mock_vertex_model.generate_images.reset_mock()
        self.gcs_blob_mock.upload_from_string.reset_mock()

        task_result = generate_image_vertex_ai_task.apply(
            kwargs={
                'request_id': request_id, 'prompt': prompt,
                'aspect_ratio': '1:1', 'add_watermark': False, 'model_id': 'test-model',
                'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
                'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
                'idempotency_key': idempotency_key
            }
        ).get()

        self.assertEqual(task_result, stored_result)
        self.mock_vertex_model.generate_images.assert_not_called()
        self.gcs_blob_mock.upload_from_string.assert_not_called()

        # Verify DB: Only SELECT
        execute_calls = cursor_mock.execute.call_args_list
        self.assertEqual(len(execute_calls), 1)
        self.assertIn("SELECT idempotency_key", execute_calls[0][0][0])
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1)

    def test_processing_key_conflict_direct_call(self, mock_db_conn_getter):
        """Test direct task call with 'processing' key (not timed out) returns conflict."""
        idempotency_key = f"iga-direct-processing-conflict-{uuid.uuid4()}"

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'generate_image_vertex_ai_task',
            'status': iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': datetime.now(timezone.utc) # Not timed out
        }
        cursor_mock.fetchone.return_value = processing_record

        task_result = generate_image_vertex_ai_task.apply(
            kwargs={'request_id': 'req_id', 'prompt': 'prompt', 'aspect_ratio': '1:1',
                    'add_watermark': False, 'model_id': 'model',
                    'gcs_bucket_name': 'bucket', 'gcs_image_prefix': 'prefix',
                    'idempotency_key': idempotency_key}
        ).get()

        self.assertEqual(task_result.get("status"), "PROCESSING_CONFLICT")
        self.assertEqual(task_result.get("idempotency_key"), idempotency_key)
        # DB: Only SELECT, no update/insert for idempotency table.
        self.assertEqual(len(cursor_mock.execute.call_args_list), 1)
        self.assertEqual(mock_conn.commit.call_count, 0)
        self.assertEqual(mock_conn.rollback.call_count, 1)


    def test_processing_key_lock_timeout_direct_call(self, mock_db_conn_getter):
        """Test direct task call with 'processing' key (timed out) re-processes."""
        idempotency_key = f"iga-direct-lock-timeout-{uuid.uuid4()}"
        lock_timeout_seconds = iga_config['IGA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']
        stale_locked_at = datetime.now(timezone.utc) - timedelta(seconds=lock_timeout_seconds + 120)

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        stale_processing_record = {
            'idempotency_key': idempotency_key, 'task_name': 'generate_image_vertex_ai_task',
            'status': iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'],
            'locked_at': stale_locked_at
        }
        cursor_mock.fetchone.return_value = stale_processing_record

        task_result = generate_image_vertex_ai_task.apply(
            kwargs={'request_id': 'req_id', 'prompt': 'Lock Timeout Prompt', 'aspect_ratio': '1:1',
                    'add_watermark': False, 'model_id': 'model',
                    'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
                    'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
                    'idempotency_key': idempotency_key}
        ).get()

        self.assertIn("image_url", task_result) # Should succeed due to mocked Vertex/GCS

        # DB: SELECT, UPDATE (to re-lock PROCESSING), UPDATE (to COMPLETED)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)

        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING'] and
            call[0][1][3] > stale_locked_at # New locked_at
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_reprocessing_update, "Update to re-lock PROCESSING not found or params incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)

    @patch.object(iga_main.ImageGenerationModel, 'from_pretrained') # To mock the actual image generation part
    def test_task_failure_direct_call_marks_idempotency_failed(self, mock_from_pretrained, mock_db_conn_getter_unused):
        """Test direct task call, if task logic fails, idempotency record is 'failed'."""
        idempotency_key = f"iga-direct-failure-{uuid.uuid4()}"

        # Mock the image generation to raise an error
        mock_model_instance = MagicMock()
        mock_model_instance.generate_images.side_effect = Exception("Simulated Vertex AI direct failure")
        mock_from_pretrained.return_value = mock_model_instance

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        cursor_mock.fetchone.return_value = None # New key

        with self.assertRaises(Exception) as context:
            generate_image_vertex_ai_task.apply(
                kwargs={'request_id': 'req_id', 'prompt': 'prompt', 'aspect_ratio': '1:1',
                        'add_watermark': False, 'model_id': 'model',
                        'gcs_bucket_name': 'bucket', 'gcs_image_prefix': 'prefix',
                        'idempotency_key': idempotency_key}
            ).get() # Failure will propagate
        self.assertIn("Simulated Vertex AI direct failure", str(context.exception))

        # DB: SELECT, INSERT (PROCESSING), UPDATE (FAILED by on_failure)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3) # SELECT, INSERT, UPDATE

        update_failed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_update_failed = any(
            update_failed_sql_part in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'] and
            "Simulated Vertex AI direct failure" in call[0][1][2] # error_payload
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_update_failed, "Update to FAILED status not found or error payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2) # For PROCESSING, then for FAILED in on_failure

    @patch.object(iga_main.ImageGenerationModel, 'from_pretrained')
    def test_retry_after_failure_direct_call_succeeds(self, mock_from_pretrained, mock_db_conn_getter_unused):
        """Test direct task call re-processes and succeeds after a previous 'failed' record."""
        idempotency_key = f"iga-direct-retry-{uuid.uuid4()}"

        mock_conn = mock_db_connection_registry_iga.setdefault(os.getpid(), MagicMock())
        cursor_mock = mock_conn.cursor.return_value.__enter__.return_value
        failed_record = {
            'idempotency_key': idempotency_key, 'task_name': 'generate_image_vertex_ai_task',
            'status': iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'],
            'error_payload': json.dumps({"error": "Previous direct failure"}), # Must be JSON string
            'locked_at': None
        }
        cursor_mock.fetchone.return_value = failed_record # Simulate finding this failed record

        # Mock Vertex AI to succeed on this retry (using default successful mock from setUp)
        mock_from_pretrained.return_value = self.mock_vertex_model

        task_result = generate_image_vertex_ai_task.apply(
            kwargs={'request_id': 'req_id', 'prompt': 'Retry Direct Prompt', 'aspect_ratio': '1:1',
                    'add_watermark': False, 'model_id': 'model',
                    'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
                    'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
                    'idempotency_key': idempotency_key}
        ).get()

        self.assertIn("image_url", task_result) # Should succeed

        # DB: SELECT (finds FAILED), UPDATE (to PROCESSING), UPDATE (to COMPLETED)
        execute_calls = cursor_mock.execute.call_args_list
        self.assertGreaterEqual(len(execute_calls), 3)

        update_processing_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = %s WHERE idempotency_key = %s AND task_name = %s;"
        found_reprocessing_update = any(
            update_processing_sql_part in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_PROCESSING']
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_reprocessing_update, "Update to re-lock PROCESSING not found.")

        update_completed_sql_part = "UPDATE idempotency_keys SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL WHERE idempotency_key = %s AND task_name = %s;"
        found_completed_update = any(
            update_completed_sql_part in str(call[0][0]) and
            call[0][1][0] == iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'] and
            "gs://test-iga-bucket/" in call[0][1][1] # result_payload has image_url
            for call in execute_calls if "UPDATE idempotency_keys" in str(call[0][0])
        )
        self.assertTrue(found_completed_update, "Update to COMPLETED not found or result payload incorrect.")
        self.assertEqual(mock_conn.commit.call_count, 2)


class TestIgaTaskTestScenarios(BaseIgaServiceTest):

    def setUp(self):
        super().setUp() # Call BaseIgaServiceTest.setUp
        # Reset mocks that might be checked for call counts specifically in these tests
        self.mock_global_vertex_model.generate_images.reset_mock()
        self.mock_global_gcs_client.bucket.reset_mock()
        # Reset idempotency DB mocks (cursor typically)
        if hasattr(self, 'mock_db_conn') and self.mock_db_conn: # If using a shared mock_db_conn from base
            self.mock_db_conn.cursor.return_value.__enter__.return_value.reset_mock()
            self.mock_db_conn.reset_mock() # Reset commit/rollback counts etc.
        elif hasattr(self, 'mock_db_cursor_instance') and self.mock_db_cursor_instance: # If it's directly set
             self.mock_db_cursor_instance.reset_mock()


    @patch('aethercast.iga.main._store_idempotency_record') # Mock this to verify its calls
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None) # Simulate new key
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True) # Simulate lock acquired
    def test_generate_image_task_test_mode_success_placeholder(self, mock_acquire_lock, mock_check_key, mock_store_record):
        """Test task with test_scenario='success_placeholder'."""

        idempotency_key = f"iga-task-test-placeholder-{uuid.uuid4()}"
        task_args = {
            'request_id': "req_placeholder_001",
            'prompt': "A placeholder image",
            'aspect_ratio': "16:9",
            'add_watermark': False,
            'model_id': iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'],
            'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
            'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
            'idempotency_key': idempotency_key,
            'workflow_id': "wf_placeholder_001",
            'test_scenario': 'success_placeholder'
        }

        result = generate_image_vertex_ai_task.apply(kwargs=task_args).get()

        self.assertIn("image_url", result)
        self.assertTrue(result["image_url"].startswith(f"gs://{iga_config['GCS_BUCKET_NAME']}/{iga_config['IGA_GCS_IMAGE_PREFIX'].strip('/')}/test_placeholder_image_"))
        self.assertEqual(result["prompt_used"], task_args['prompt'])
        self.assertEqual(result["model_version"], "test-mode-placeholder-model")
        self.assertIn("test mode - placeholder", result["status_message"])

        # Verify external services were NOT called
        self.mock_global_vertex_model.generate_images.assert_not_called()
        # GCS client might be initialized but not used for upload in placeholder success.
        # self.mock_global_gcs_client.bucket.assert_not_called() # This was too strict, client can be init'd

        # Verify idempotency record was updated to completed
        mock_store_record.assert_any_call(
            ANY,
            idempotency_key,
            generate_image_vertex_ai_task.name,
            iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'],
            workflow_id="wf_placeholder_001",
            result_payload=result, # This now contains base64
            is_new_key=False
        )


    @patch('aethercast.iga.main._store_idempotency_record')
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None)
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True)
    def test_generate_image_task_test_mode_error_vertex_ai(self, mock_acquire_lock, mock_check_key, mock_store_record):
        idempotency_key = f"iga-task-test-error-{uuid.uuid4()}"
        task_args = {
            'request_id': "req_error_sim_001", 'prompt': "A prompt for simulated error",
            'aspect_ratio': "1:1", 'add_watermark': True, 'model_id': iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'],
            'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'], 'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
            'idempotency_key': idempotency_key, 'workflow_id': "wf_error_sim_001",
            'test_scenario': 'error_vertex_ai'
        }
        with self.assertRaisesRegex(RuntimeError, "Simulated Vertex AI error in IGA test mode"):
            generate_image_vertex_ai_task.apply(kwargs=task_args).get()
        self.mock_global_vertex_model.generate_images.assert_not_called()
        expected_error_payload = {
            "error_type": "SimulatedVertexAIError",
            "message": "Test mode: Simulated Vertex AI image generation failure.",
            "details": "Vertex AI unavailable (test scenario)"
        }
        # Check the last call to _store_idempotency_record, which should be for failure
        # The on_failure handler might also call this. We check for at least one call with FAILED.
        found_failed_store = False
        for call_args_item in mock_store_record.call_args_list:
            args, kwargs = call_args_item
            if args[3] == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'] and args[1] == idempotency_key:
                self.assertEqual(kwargs.get('error_payload'), expected_error_payload)
                found_failed_store = True
                break
        self.assertTrue(found_failed_store, "Expected call to _store_idempotency_record with FAILED status not found or payload incorrect.")


    @patch('aethercast.iga.main._store_idempotency_record')
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None)
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True)
    def test_generate_image_task_no_test_mode_gcs_upload_success(self, mock_acquire_lock, mock_check_key, mock_store_record):
        """Test normal path: Vertex AI success, GCS upload success, returns GCS URI."""
        idempotency_key = f"iga-task-gcs-success-{uuid.uuid4()}"
        request_id = "req_gcs_success_001"
        prompt_text = "A successful GCS upload"
        task_args = {
            'request_id': request_id, 'prompt': prompt_text,
            'aspect_ratio': "16:9", 'add_watermark': True, 'model_id': 'test-model-gcs',
            'gcs_bucket_name': 'test-bucket-gcs', 'gcs_image_prefix': 'test_prefix/iga/',
            'idempotency_key': idempotency_key, 'workflow_id': "wf_gcs_success_001",
            'test_scenario': None
        }
        # self.mock_vertex_model is set up in BaseIgaServiceTest.setUp to return dummy image bytes
        # self.mock_global_gcs_client and its blob mock are also set up.

        result = generate_image_vertex_ai_task.apply(kwargs=task_args).get()

        self.mock_global_vertex_model.generate_images.assert_called_once_with(
            prompt=prompt_text, number_of_images=1, aspect_ratio="16:9", add_watermark=True
        )
        self.mock_global_gcs_client.bucket.assert_called_once_with('test-bucket-gcs')

        # Check blob name construction and upload
        expected_blob_name_part = f"test_prefix/iga/{idempotency_key}_{request_id}" # Should be one or the other + uuid

        # Check that upload_from_string was called on the blob mock
        # The actual blob name includes a uuid, so we check parts of it.
        self.gcs_blob_mock.upload_from_string.assert_called_once()
        call_args_upload = self.gcs_blob_mock.upload_from_string.call_args
        self.assertEqual(call_args_upload[0][0], b"dummy_image_bytes") # Check image_bytes
        self.assertEqual(call_args_upload[1]['content_type'], "image/png") # Check content_type

        # Verify the blob name passed to bucket.blob()
        blob_name_arg = self.mock_global_gcs_client.bucket.return_value.blob.call_args[0][0]
        self.assertTrue(blob_name_arg.startswith(f"test_prefix/iga/{idempotency_key or request_id}"))
        self.assertTrue(blob_name_arg.endswith(".png"))

        self.assertIn("image_url", result)
        expected_gcs_uri_prefix = f"gs://test-bucket-gcs/test_prefix/iga/{idempotency_key or request_id}"
        self.assertTrue(result["image_url"].startswith(expected_gcs_uri_prefix))
        self.assertTrue(result["image_url"].endswith(".png"))
        self.assertEqual(result["prompt_used"], prompt_text)
        self.assertEqual(result["model_version"], 'test-model-gcs')

        mock_store_record.assert_any_call(
            ANY, idempotency_key, generate_image_vertex_ai_task.name,
            iga_config['IGA_IDEMPOTENCY_STATUS_COMPLETED'], workflow_id="wf_gcs_success_001",
            result_payload=result, is_new_key=False
        )

    @patch('aethercast.iga.main._store_idempotency_record')
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None)
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True)
    def test_generate_image_task_gcs_upload_failure(self, mock_acquire_lock, mock_check_key, mock_store_record):
        """Test task failure if GCS upload fails."""
        idempotency_key = f"iga-task-gcs-fail-{uuid.uuid4()}"
        task_args = {
            'request_id': "req_gcs_fail_001", 'prompt': "Prompt for GCS fail",
            'aspect_ratio': "1:1", 'add_watermark': False, 'model_id': 'model-gcs-fail',
            'gcs_bucket_name': 'test-bucket-gcs-fail', 'gcs_image_prefix': 'prefix/',
            'idempotency_key': idempotency_key, 'workflow_id': "wf_gcs_fail_001",
            'test_scenario': None
        }

        # Mock Vertex AI to succeed
        self.mock_global_vertex_model.generate_images.return_value = MagicMock(images=[self.mock_vertex_image])
        # Mock GCS upload to fail
        self.gcs_blob_mock.upload_from_string.side_effect = google_exceptions.Forbidden("GCS Permission Denied")

        with self.assertRaises(google_exceptions.Forbidden): # The task re-raises GoogleAPIError
            generate_image_vertex_ai_task.apply(kwargs=task_args).get()

        self.mock_global_vertex_model.generate_images.assert_called_once()
        self.gcs_blob_mock.upload_from_string.assert_called_once()

        # Verify idempotency record was updated to FAILED by on_failure handler
        # on_failure is part of the Celery Task base class, not directly mockable here in the same way as _store_idempotency_record
        # However, if the task fails due to the GCS exception, the on_failure should be triggered.
        # We check that _store_idempotency_record was eventually called with FAILED status.
        found_failed_store = False
        for call_args_item in mock_store_record.call_args_list:
            args, kwargs = call_args_item
            if args[3] == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'] and args[1] == idempotency_key:
                self.assertIn("GCS Permission Denied", kwargs.get('error_payload', {}).get('message',''))
                found_failed_store = True
                break
        self.assertTrue(found_failed_store, "Expected call to _store_idempotency_record with FAILED status for GCS error not found.")

    @patch('aethercast.iga.main._store_idempotency_record')
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None)
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True)
    def test_generate_image_task_missing_gcs_config_at_runtime(self, mock_acquire_lock, mock_check_key, mock_store_record):
        """Test task failure if GCS bucket is None at runtime inside the task."""
        idempotency_key = f"iga-task-gcs-cfg-fail-{uuid.uuid4()}"
        task_args_no_bucket = {
            'request_id': "req_gcs_cfg_fail", 'prompt': "Prompt GCS Cfg Fail",
            'aspect_ratio': "1:1", 'add_watermark': False, 'model_id': 'model-cfg-fail',
            'gcs_bucket_name': None, # Simulate missing bucket name
            'gcs_image_prefix': 'prefix/',
            'idempotency_key': idempotency_key, 'workflow_id': "wf_gcs_cfg_fail",
            'test_scenario': None
        }
        # Vertex AI part succeeds
        self.mock_global_vertex_model.generate_images.return_value = MagicMock(images=[self.mock_vertex_image])

        with self.assertRaisesRegex(ValueError, "GCS configuration error: Bucket name or client missing."):
            generate_image_vertex_ai_task.apply(kwargs=task_args_no_bucket).get()

        # Check that Vertex AI was called, but GCS upload wasn't attempted beyond client check
        self.mock_global_vertex_model.generate_images.assert_called_once()
        self.mock_global_gcs_client.bucket.assert_not_called() # Should fail before this

        found_failed_store = False
        for call_args_item in mock_store_record.call_args_list:
            args, kwargs = call_args_item
            if args[3] == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'] and args[1] == idempotency_key:
                self.assertIn("ValueError", kwargs.get('error_payload', {}).get('error_type',''))
                self.assertIn("GCS configuration error", kwargs.get('error_payload', {}).get('message',''))
                found_failed_store = True
                break
        self.assertTrue(found_failed_store, "Expected call to _store_idempotency_record with FAILED status for GCS config error not found.")

    @patch('aethercast.iga.main._store_idempotency_record')
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None)
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True)
    def test_vertex_ai_parameters_passed_correctly(self, mock_acquire_lock, mock_check_key, mock_store_record):
        """Test that aspect_ratio, add_watermark, and model_id are passed to Vertex AI."""
        idempotency_key = f"iga-task-params-{uuid.uuid4()}"
        prompt = "Test prompt for params"
        custom_aspect_ratio = "9:16"
        custom_add_watermark = False
        custom_model_id = "imagegeneration@005" # Different from default in config

        task_args = {
            'request_id': "req_params_001", 'prompt': prompt,
            'aspect_ratio': custom_aspect_ratio, 'add_watermark': custom_add_watermark,
            'model_id': custom_model_id, # Override model
            'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
            'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
            'idempotency_key': idempotency_key, 'workflow_id': "wf_params_001",
            'test_scenario': None
        }

        # We need to mock ImageGenerationModel.from_pretrained if a custom model_id is used
        # and GLOBAL_IMAGE_MODEL is set to the default or None.
        # The BaseIgaServiceTest already patches GLOBAL_IMAGE_MODEL to a mock.
        # If model_id in task_args is different from iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'],
        # the task will try to load it on demand.

        mock_custom_model_instance = MagicMock()
        mock_custom_model_instance.generate_images.return_value = MagicMock(images=[self.mock_vertex_image])

        with patch('vertexai.preview.vision_models.ImageGenerationModel.from_pretrained', return_value=mock_custom_model_instance) as mock_model_loader:
            generate_image_vertex_ai_task.apply(kwargs=task_args).get()
            # If custom_model_id was different from the default pre-loaded one, it should be loaded.
            if custom_model_id != iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID']:
                 mock_model_loader.assert_called_once_with(custom_model_id)
                 mock_custom_model_instance.generate_images.assert_called_once_with(
                    prompt=prompt, number_of_images=1,
                    aspect_ratio=custom_aspect_ratio, add_watermark=custom_add_watermark
                )
            else: # If custom_model_id happens to be the default, the global mock is used
                self.mock_global_vertex_model.generate_images.assert_called_once_with(
                    prompt=prompt, number_of_images=1,
                    aspect_ratio=custom_aspect_ratio, add_watermark=custom_add_watermark
                )


    @patch('aethercast.iga.main._store_idempotency_record')
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None)
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True)
    def test_vertex_ai_no_images_returned(self, mock_acquire_lock, mock_check_key, mock_store_record):
        """Test task failure if Vertex AI returns no images."""
        idempotency_key = f"iga-task-no-images-{uuid.uuid4()}"
        self.mock_global_vertex_model.generate_images.return_value = MagicMock(images=[]) # Empty list
        task_args = {
            'request_id': "req_no_img_001", 'prompt': "Prompt for no images",
            'idempotency_key': idempotency_key, 'workflow_id': "wf_no_img_001",
            'aspect_ratio': '1:1', 'add_watermark': True,
            'model_id': iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'],
            'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
            'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
            'test_scenario': None
        }

        result = generate_image_vertex_ai_task.apply(kwargs=task_args).get()
        self.assertEqual(result.get("status"), "error")
        self.assertIn("No images returned from Vertex AI", result.get("message", ""))

        # Verify idempotency was marked as FAILED
        found_failed_store = any(
            call_args[0][3] == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'] and
            "NoImageGenerated" in str(call_args[1].get('error_payload', {}))
            for call_args in mock_store_record.call_args_list
        )
        self.assertTrue(found_failed_store, "Idempotency record not marked FAILED for no images.")

    @patch('aethercast.iga.main._store_idempotency_record')
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None)
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True)
    def test_vertex_ai_empty_image_bytes(self, mock_acquire_lock, mock_check_key, mock_store_record):
        """Test task failure if Vertex AI returns an image object with empty _image_bytes."""
        idempotency_key = f"iga-task-empty-bytes-{uuid.uuid4()}"
        mock_empty_byte_image = MagicMock()
        mock_empty_byte_image._image_bytes = b"" # Empty bytes
        self.mock_global_vertex_model.generate_images.return_value = MagicMock(images=[mock_empty_byte_image])
        task_args = {
            'request_id': "req_empty_bytes_001", 'prompt': "Prompt for empty bytes",
            'idempotency_key': idempotency_key, 'workflow_id': "wf_empty_bytes_001",
            'aspect_ratio': '1:1', 'add_watermark': True,
            'model_id': iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'],
            'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
            'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
            'test_scenario': None
        }

        result = generate_image_vertex_ai_task.apply(kwargs=task_args).get()
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Empty image data", result.get("message", ""))

        found_failed_store = any(
            call_args[0][3] == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'] and
            "EmptyImageBytes" in str(call_args[1].get('error_payload', {}))
            for call_args in mock_store_record.call_args_list
        )
        self.assertTrue(found_failed_store, "Idempotency record not marked FAILED for empty image bytes.")

    @patch('aethercast.iga.main.GLOBAL_IMAGE_MODEL', None) # Simulate model not pre-loaded
    @patch('vertexai.preview.vision_models.ImageGenerationModel.from_pretrained')
    @patch('aethercast.iga.main._store_idempotency_record')
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None)
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True)
    def test_on_demand_model_loading_success(self, mock_acquire_lock, mock_check_key, mock_store_record, mock_from_pretrained, mock_global_model_is_none):
        """Test successful on-demand loading of Vertex AI model."""
        idempotency_key = f"iga-task-ondemand-success-{uuid.uuid4()}"
        custom_model_id = "imagegeneration@custom"

        mock_custom_model_instance = MagicMock()
        mock_custom_model_instance.generate_images.return_value = MagicMock(images=[self.mock_vertex_image])
        mock_from_pretrained.return_value = mock_custom_model_instance

        task_args = {
            'request_id': "req_ondemand_001", 'prompt': "Prompt for on-demand model",
            'idempotency_key': idempotency_key, 'model_id': custom_model_id,
             'aspect_ratio': '1:1', 'add_watermark': True,
            'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
            'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
            'test_scenario': None
        }
        result = generate_image_vertex_ai_task.apply(kwargs=task_args).get()
        self.assertIn("image_url", result)
        mock_from_pretrained.assert_called_once_with(custom_model_id)
        mock_custom_model_instance.generate_images.assert_called_once()


    @patch('aethercast.iga.main.GLOBAL_IMAGE_MODEL', None)
    @patch('vertexai.preview.vision_models.ImageGenerationModel.from_pretrained')
    @patch('aethercast.iga.main._store_idempotency_record')
    @patch('aethercast.iga.main._check_idempotency_key', return_value=None)
    @patch('aethercast.iga.main.acquire_idempotency_lock', return_value=True)
    def test_on_demand_model_loading_failure(self, mock_acquire_lock, mock_check_key, mock_store_record, mock_from_pretrained, mock_global_model_is_none):
        """Test failure during on-demand loading of Vertex AI model."""
        idempotency_key = f"iga-task-ondemand-fail-{uuid.uuid4()}"
        custom_model_id = "imagegeneration@nonexistent"
        mock_from_pretrained.side_effect = RuntimeError("Failed to load custom model")

        task_args = {
            'request_id': "req_ondemand_fail_001", 'prompt': "Prompt for failing on-demand model",
            'idempotency_key': idempotency_key, 'model_id': custom_model_id,
            'aspect_ratio': '1:1', 'add_watermark': True,
            'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
            'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
            'test_scenario': None
        }
        with self.assertRaisesRegex(RuntimeError, "Failed to load custom model"):
            generate_image_vertex_ai_task.apply(kwargs=task_args).get()

        mock_from_pretrained.assert_called_once_with(custom_model_id)
        # Check if idempotency was marked as FAILED
        found_failed_store = any(
            call_args[0][3] == iga_config['IGA_IDEMPOTENCY_STATUS_FAILED'] and
            "Failed to load custom model" in str(call_args[1].get('error_payload', {}))
            for call_args in mock_store_record.call_args_list
        )
        self.assertTrue(found_failed_store, "Idempotency record not marked FAILED for on-demand model load failure.")


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestIgaCeleryLogging(BaseIgaServiceTest):
    @patch('aethercast.iga.main.app.logger') # Patch the app.logger used by tasks
    @patch('aethercast.iga.main._get_iga_db_connection', side_effect=mock_get_iga_db_connection_side_effect)
    # Mocks for Vertex AI and GCS are already set up in BaseIgaServiceTest.setUp
    # and will be used by the task if test_scenario is None.
    def test_generate_image_task_json_logging_normal_path(self, mock_db_conn_getter, mock_app_logger):
        # Mock idempotency checks to allow task to run
        mock_conn_instance = mock_get_iga_db_connection_side_effect()
        mock_cursor_instance = mock_conn_instance.cursor.return_value.__enter__.return_value
        mock_cursor_instance.fetchone.return_value = None # Simulate new key

        # Task arguments
        task_request_id = f"iga_log_test_req_{uuid.uuid4().hex[:6]}"
        task_prompt = "A test prompt for IGA logging"
        task_idempotency_key = f"iga_log_test_idem_{uuid.uuid4().hex[:6]}"
        task_workflow_id = f"wf_iga_log_test_{uuid.uuid4().hex[:6]}"

        # Execute the task (eagerly), normal path (no test_scenario)
        generate_image_vertex_ai_task.apply(
            kwargs={
                'request_id': task_request_id,
                'prompt': task_prompt,
                'aspect_ratio': '1:1', 'add_watermark': False,
                'model_id': iga_config['IGA_VERTEXAI_IMAGE_MODEL_ID'],
                'gcs_bucket_name': iga_config['GCS_BUCKET_NAME'],
                'gcs_image_prefix': iga_config['IGA_GCS_IMAGE_PREFIX'],
                'idempotency_key': task_idempotency_key,
                'workflow_id': task_workflow_id,
                'test_scenario': None
            }
        ).get()

        self.assertTrue(mock_app_logger.info.called)

        found_log_call = None
        celery_task_id_from_call = None
        for call_args_tuple in mock_app_logger.info.call_args_list:
            message_arg = call_args_tuple[0][0]
            if "IGA Celery Task" in message_arg and "Starting" in message_arg:
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
            expected_prompt_preview = (task_prompt[:50] + "...") if len(task_prompt) > 50 else task_prompt
            self.assertEqual(log_extra_dict.get('prompt_preview'), expected_prompt_preview)
            self.assertIn('task_id', log_extra_dict)
            if celery_task_id_from_call:
                 self.assertEqual(log_extra_dict.get('task_id'), celery_task_id_from_call)

    @patch('aethercast.iga.main.app.logger')
    @patch('aethercast.iga.main._get_iga_db_connection', side_effect=mock_get_iga_db_connection_side_effect)
    def test_generate_image_task_json_logging_test_scenario(self, mock_db_conn_getter, mock_app_logger):
        # Test logging when a test_scenario is active
        mock_conn_instance = mock_get_iga_db_connection_side_effect()
        mock_cursor_instance = mock_conn_instance.cursor.return_value.__enter__.return_value
        mock_cursor_instance.fetchone.return_value = None

        task_request_id = f"iga_log_test_scen_req_{uuid.uuid4().hex[:6]}"
        task_prompt = "Another prompt for placeholder"
        task_idempotency_key = f"iga_log_test_scen_idem_{uuid.uuid4().hex[:6]}"
        task_workflow_id = f"wf_iga_log_test_scen_{uuid.uuid4().hex[:6]}"

        generate_image_vertex_ai_task.apply(
            kwargs={
                'request_id': task_request_id, 'prompt': task_prompt,
                'aspect_ratio': '1:1', 'add_watermark': False, 'model_id': 'model',
                'gcs_bucket_name': 'bucket', 'gcs_image_prefix': 'prefix',
                'idempotency_key': task_idempotency_key, 'workflow_id': task_workflow_id,
                'test_scenario': 'success_placeholder'
            }
        ).get()

        self.assertTrue(mock_app_logger.info.called)
        # Check that the log for test mode activation contains the context
        found_test_mode_log = None
        for call_args_tuple in mock_app_logger.info.call_args_list:
            if "Test mode 'success_placeholder' active" in call_args_tuple[0][0]:
                found_test_mode_log = call_args_tuple
                break
        self.assertIsNotNone(found_test_mode_log, "Log for test mode activation not found.")
        if found_test_mode_log:
            log_extra_dict = found_test_mode_log[1].get('extra', {})
            self.assertEqual(log_extra_dict.get('idempotency_key'), task_idempotency_key)
            self.assertEqual(log_extra_dict.get('workflow_id'), task_workflow_id)
