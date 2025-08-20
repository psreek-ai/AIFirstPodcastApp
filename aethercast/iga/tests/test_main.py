import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import uuid

# Adjust path to import IGA main module components
current_dir = os.path.dirname(os.path.abspath(__file__))
iga_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(iga_dir)
project_root_dir = os.path.dirname(aethercast_dir)

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)
if aethercast_dir not in sys.path:
    sys.path.insert(0, aethercast_dir)

from aethercast.iga.main import generate_image_vertex_ai_task, app as flask_app, celery_app as iga_celery_app

# Import specific exceptions if they are caught and handled in the task
from google.api_core import exceptions as google_exceptions
import psycopg2


class TestIgaService(unittest.TestCase):

    def setUp(self):
        # Configure Celery for testing (task_always_eager=True runs tasks synchronously)
        iga_celery_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True # Propagates exceptions raised by tasks
        )
        flask_app.testing = True
        self.app = flask_app.test_client()

    def tearDown(self):
        patch.stopall()

    @patch('aethercast.common.db.get_db_connection')
    @patch('aethercast.iga.main.aiplatform.init')
    @patch('aethercast.iga.main.ImageGenerationModel.from_pretrained')
    @patch('aethercast.iga.main.storage.Client')
    def test_generate_image_success(self, mock_storage_client, mock_from_pretrained, mock_aiplatform_init, mock_get_db_conn):
        """
        Test successful invocation of generate_image_vertex_ai_task using mocked global clients.
        """
        mock_db_conn_instance = MagicMock()
        mock_db_cursor_instance = MagicMock()
        mock_get_db_conn.return_value = mock_db_conn_instance
        mock_db_conn_instance.cursor.return_value.__enter__.return_value = mock_db_cursor_instance
        mock_db_cursor_instance.fetchone.side_effect = [None, (1,)]

        # --- Mock Google TTS Client ---
        mock_model_instance = MagicMock()
        mock_image_instance = MagicMock()
        mock_image_instance._image_bytes = b'test_image_bytes'
        mock_model_instance.generate_images.return_value = [mock_image_instance]
        mock_from_pretrained.return_value = mock_model_instance

        # --- Mock Google Storage Client ---
        mock_blob = MagicMock()
        mock_storage_client.return_value.bucket.return_value.blob.return_value = mock_blob

        # --- Task Arguments ---
        request_id = "test_req_001"
        prompt = "A beautiful landscape"
        aspect_ratio = "1:1"
        add_watermark = True
        model_id = "imagegeneration@006"
        gcs_bucket_name = "test-bucket"
        gcs_image_prefix = "images/iga/"
        idempotency_key = f"iga-test-new-{uuid.uuid4()}"
        workflow_id = f"wf-iga-test-new-{uuid.uuid4()}"


        # --- Execute Task ---
        with patch('aethercast.iga.main.GCS_BUCKET_NAME', "test-bucket"), \
             patch('aethercast.iga.main.IGA_GCS_IMAGE_PREFIX', "images/iga/"):
            result = generate_image_vertex_ai_task(
                request_id, prompt, aspect_ratio, add_watermark, model_id, gcs_bucket_name, gcs_image_prefix, idempotency_key, workflow_id
            )

        # --- Assertions ---
        mock_from_pretrained.assert_called_once_with(model_id)
        mock_model_instance.generate_images.assert_called_once_with(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio=aspect_ratio,
            add_watermark=add_watermark,
        )
        mock_storage_client.return_value.bucket.assert_called_once_with("test-bucket")
        mock_blob.upload_from_string.assert_called_once_with(
            b"test_image_bytes", content_type="image/png"
        )

        self.assertIn("image_url", result)
        self.assertTrue(result["image_url"].startswith("gs://test-bucket/images/iga/"))
        self.assertEqual(result["prompt_used"], prompt)
        self.assertEqual(result["model_version"], model_id)


if __name__ == '__main__':
    unittest.main()
