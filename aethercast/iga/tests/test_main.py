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

        self.patch_vertex_from_pretrained = patch('aethercast.iga.main.ImageGenerationModel.from_pretrained', return_value=self.mock_vertex_model)
        self.patch_gcs_client = patch('aethercast.iga.main.storage.Client')

        self.mock_gcs_client_instance = self.patch_gcs_client.start()
        self.mock_gcs_client_instance.return_value.bucket.return_value.blob.return_value = self.gcs_blob_mock
        self.mock_vertex_init = self.patch_vertex_from_pretrained.start()


    def tearDown(self):
        self.config_patcher.stop()
        self.patch_vertex_from_pretrained.stop()
        self.patch_gcs_client.stop()
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
