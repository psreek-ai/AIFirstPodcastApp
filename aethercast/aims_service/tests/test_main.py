import unittest
from unittest.mock import patch, MagicMock, call
import os
import sys

# Adjust path to import AIMS main module components
current_dir = os.path.dirname(os.path.abspath(__file__))
aims_service_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(aims_service_dir)
project_root_dir = os.path.dirname(aethercast_dir)

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

# Imports from AIMS service
from aethercast.aims_service.main import invoke_llm_vertex_ai_task, celery_app as aims_celery_app
from aethercast.aims_service.main import AIMS_GOOGLE_LLM_MODEL_ID # Default model ID
# Import GenerativeModel for patching, and the global cache
from aethercast.aims_service.main import GenerativeModel, GLOBAL_GENERATIVE_MODELS

# Import specific exceptions if they are caught and handled in the task
from google.api_core import exceptions as google_exceptions
import psycopg2


class TestInvokeLlmVertexAiTaskOptimized(unittest.TestCase):

    def setUp(self):
        aims_celery_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True
        )
        # Clear the global model cache before each test
        GLOBAL_GENERATIVE_MODELS.clear()

        # Mock DB interactions for idempotency as they are part of the task
        self.mock_db_conn_patcher = patch('aethercast.aims_service.main.get_db_connection')
        self.mock_get_db_conn = self.mock_db_conn_patcher.start()

        self.mock_db_conn_instance = MagicMock(name="MockAimsDbConnection")
        self.mock_db_cursor_instance = MagicMock(name="MockAimsDbCursor")
        self.mock_get_db_conn.return_value = self.mock_db_conn_instance
        self.mock_db_conn_instance.cursor.return_value.__enter__.return_value = self.mock_db_cursor_instance
        self.mock_db_cursor_instance.fetchone.return_value = None # Simulate new idempotency key

    def tearDown(self):
        self.mock_db_conn_patcher.stop()
        GLOBAL_GENERATIVE_MODELS.clear() # Ensure clean state after tests

    @patch('aethercast.aims_service.main.GenerativeModel') # Patch the class itself for on-demand loading
    def test_invoke_llm_on_demand_model_loading_and_caching(self, mock_generative_model_class):
        """Test that a model is loaded on demand if not in cache, and then cached."""
        model_name = "gemini-1.0-pro-test-on-demand"

        mock_model_instance = MagicMock(spec=GenerativeModel)
        mock_model_instance.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[MagicMock(text="Test response")]))],
            usage_metadata=MagicMock(prompt_token_count=10, candidates_token_count=5, total_token_count=15)
        )
        mock_generative_model_class.return_value = mock_model_instance

        # 1. First call: Model should be loaded and cached
        invoke_llm_vertex_ai_task(
            request_id="req_id_on_demand_1", prompt_text="Hello", model_name_to_use=model_name,
            temperature=0.5, max_output_tokens=50, idempotency_key="idem_key_1" # Added idempotency_key
        )

        mock_generative_model_class.assert_called_once_with(model_name)
        self.assertIn(model_name, GLOBAL_GENERATIVE_MODELS)
        self.assertEqual(GLOBAL_GENERATIVE_MODELS[model_name], mock_model_instance)
        mock_model_instance.generate_content.assert_called_once()

        # 2. Second call: Model should be used from cache
        mock_generative_model_class.reset_mock() # Reset mock for the class call
        mock_model_instance.generate_content.reset_mock() # Reset mock for the instance method call

        invoke_llm_vertex_ai_task(
            request_id="req_id_on_demand_2", prompt_text="Hi again", model_name_to_use=model_name,
            temperature=0.5, max_output_tokens=50, idempotency_key="idem_key_2" # Added idempotency_key
        )

        mock_generative_model_class.assert_not_called() # Should not be called again
        self.assertIn(model_name, GLOBAL_GENERATIVE_MODELS) # Still in cache
        mock_model_instance.generate_content.assert_called_once() # generate_content on the cached instance is called

    def test_invoke_llm_uses_pre_initialized_default_model(self):
        """Test that the pre-initialized default model is used if requested."""
        # This test assumes the default model (AIMS_GOOGLE_LLM_MODEL_ID) was pre-loaded
        # into GLOBAL_GENERATIVE_MODELS during app startup.
        # We will simulate this pre-loading by adding a mock model to the cache.

        default_model_id = AIMS_GOOGLE_LLM_MODEL_ID
        mock_default_model_instance = MagicMock(spec=GenerativeModel)
        mock_default_model_instance.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[MagicMock(text="Default model response")]))],
            usage_metadata=MagicMock(prompt_token_count=12, candidates_token_count=6, total_token_count=18)
        )
        GLOBAL_GENERATIVE_MODELS[default_model_id] = mock_default_model_instance

        # Patch GenerativeModel class to ensure it's NOT called for this default model
        with patch('aethercast.aims_service.main.GenerativeModel') as mock_generative_model_class_not_called:
            invoke_llm_vertex_ai_task(
                request_id="req_id_default_model", prompt_text="Test default", model_name_to_use=default_model_id,
                temperature=0.5, max_output_tokens=50, idempotency_key="idem_key_default" # Added idempotency_key
            )

            mock_generative_model_class_not_called.assert_not_called() # Verify it wasn't initialized on demand
            mock_default_model_instance.generate_content.assert_called_once() # Verify the cached instance was used

    @patch('aethercast.aims_service.main.GenerativeModel')
    def test_invoke_llm_on_demand_model_initialization_failure(self, mock_generative_model_class):
        """Test that task handles failure during on-demand model initialization."""
        model_name_fail = "gemini-pro-fail-init"
        mock_generative_model_class.side_effect = Exception("Simulated model init failure")

        with self.assertRaises(Exception) as context:
            invoke_llm_vertex_ai_task(
                request_id="req_id_init_fail", prompt_text="Test fail", model_name_to_use=model_name_fail,
                temperature=0.5, max_output_tokens=50, idempotency_key="idem_key_fail_init" # Added idempotency_key
            )

        self.assertIn("Simulated model init failure", str(context.exception))
        mock_generative_model_class.assert_called_once_with(model_name_fail)
        self.assertNotIn(model_name_fail, GLOBAL_GENERATIVE_MODELS) # Should not be cached on failure

        # Verify idempotency record was updated to 'failed'
        # The task's main exception handler calls update_idempotency_record
        # We check that the cursor was used for this update.
        # The actual check for 'failed' status would be more involved here,
        # but we can infer it from the call to update after an error.
        # The error_payload in the call to update_idempotency_record should contain details of ModelInitializationError
        found_failed_update = False
        for call_args_tuple in self.mock_db_cursor_instance.execute.call_args_list:
            sql_command = str(call_args_tuple[0]) # SQL command is the first element
            params = call_args_tuple[1] if len(call_args_tuple) > 1 else None # Params are the second element
            if "UPDATE idempotency_keys" in sql_command and params and params[0] == 'failed':
                 # params[1] is result_payload (None), params[2] is error_payload
                error_payload_in_db = json.loads(params[2]) if params[2] else {}
                if error_payload_in_db.get("error_type") == "ModelInitializationError":
                    found_failed_update = True
                    break
        self.assertTrue(found_failed_update, "Idempotency record was not updated to 'failed' with ModelInitializationError.")


if __name__ == '__main__':
    unittest.main()
