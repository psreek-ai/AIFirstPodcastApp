import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json
import sqlite3 # For direct DB assertions
from datetime import datetime # For timestamp comparisons

# Adjust path to import API Gateway main module and CPOA (for mocking)
current_dir = os.path.dirname(os.path.abspath(__file__))
api_gw_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(api_gw_dir)
project_root_dir = os.path.dirname(aethercast_dir)

sys.path.insert(0, project_root_dir) # project root
sys.path.insert(0, aethercast_dir)   # aethercast directory
sys.path.insert(0, api_gw_dir)       # api_gateway directory


from aethercast.api_gateway import main as api_gw_main
# We might need to mock cpoa.main if api_gateway.main imports it directly
# from aethercast.cpoa import main as cpoa_main_module # Example if needed for patching

# Store original DATABASE_FILE to restore after tests if it's not :memory:
# This is good practice if tests might run in an environment where original value matters,
# but for :memory: patching, it's mostly for completeness.
ORIGINAL_DATABASE_FILE = api_gw_main.DATABASE_FILE

class TestAPIGateway(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        api_gw_main.app.config['TESTING'] = True

        # Patch DATABASE_FILE in api_gw_main to use :memory: for all tests in this class
        cls.db_patcher = patch.object(api_gw_main, 'DATABASE_FILE', ":memory:")
        cls.mock_db_file = cls.db_patcher.start()

        # Initialize the in-memory database within the app context
        with api_gw_main.app.app_context():
            api_gw_main.init_db()
            # Sanity check: ensure the table exists
            conn = api_gw_main.get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='podcasts';")
                if cursor.fetchone() is None:
                    raise AssertionError("Podcasts table was not created in :memory: database.")
            finally:
                if conn: conn.close()

        cls.client = api_gw_main.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.db_patcher.stop()
        # No need to delete :memory: database, it's ephemeral.

    def setUp(self):
        # Ensure a clean database state for each test method
        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            # Clear all data from tables - more robust than dropping if other tables exist
            cursor.execute("DELETE FROM podcasts;")
            # Add other tables here if necessary: e.g., cursor.execute("DELETE FROM snippets;")
            conn.commit()
            # init_db() might not be needed here if schema is already created by setUpClass
            # and we are just clearing data. However, if tests could modify schema or if
            # init_db has other setup logic, it might be useful.
            # For now, assuming schema is stable after setUpClass.
            conn.close()

    def test_health_check(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        self.assertEqual(json_data['status'], "API Gateway is healthy")
        self.assertIn("cpoa_podcast_function_status", json_data)
        self.assertIn("database_status", json_data)
        self.assertIn("Database connection successful", json_data["database_status"])


    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_task_success(self, mock_orchestrate_podcast_func):
        # This is the result CPOA's orchestrate_podcast_generation would return
        mock_cpoa_result = {
            "task_id": "will_be_overwritten_by_api_gw_uuid", # API GW generates its own podcast_id
            "topic": "Test Success Topic", # This should match input to CPOA
            "status": "completed",
            "error_message": None,
            "asf_notification_status": "ASF notified successfully.",
            "asf_websocket_url": "ws://mockasf/stream/stream_mock_abc",
            "final_audio_details": {
                "status": "success", # VFA status
                "audio_filepath": "/srv/aethercast/audio/mock_audio.mp3",
                "stream_id": "stream_mock_abc"
            },
            "orchestration_log": [{"timestamp": "ts_now", "message": "All good from CPOA"}]
        }
        mock_orchestrate_podcast_func.return_value = mock_cpoa_result

        post_response = self.client.post('/api/v1/podcasts', json={'topic': 'Test Success Topic'})
        
        self.assertEqual(post_response.status_code, 201)
        json_data = post_response.get_json()
        self.assertIn('podcast_id', json_data)
        generated_podcast_id = json_data['podcast_id'] # Capture the generated ID
        
        self.assertEqual(json_data['topic'], 'Test Success Topic')
        self.assertEqual(json_data['generation_status'], 'completed')
        self.assertTrue(json_data['audio_url'].endswith(f'/audio.mp3'))
        
        # Verify CPOA was called correctly
        # The podcast_id is generated by API GW *before* calling CPOA
        mock_orchestrate_podcast_func.assert_called_once()
        call_args = mock_orchestrate_podcast_func.call_args[1] # kwargs
        self.assertEqual(call_args['topic'], 'Test Success Topic')
        self.assertEqual(call_args['task_id'], generated_podcast_id)
        self.assertEqual(call_args['db_path'], api_gw_main.DATABASE_FILE) # Check db_path

        # Verify data in DB
        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM podcasts WHERE podcast_id = ?", (generated_podcast_id,))
            row = cursor.fetchone()
            conn.close()
        
        self.assertIsNotNone(row, "Podcast record not found in DB.")
        self.assertEqual(row['topic'], 'Test Success Topic')
        self.assertEqual(row['cpoa_status'], 'completed') # This is updated by API GW from CPOA result
        self.assertEqual(row['final_audio_filepath'], '/srv/aethercast/audio/mock_audio.mp3')
        self.assertEqual(row['stream_id'], 'stream_mock_abc')
        self.assertIsNotNone(row['cpoa_full_orchestration_log'])
        log_content = json.loads(row['cpoa_full_orchestration_log'])
        self.assertEqual(log_content[0]['message'], "All good from CPOA")


    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_task_cpoa_reports_failure(self, mock_orchestrate_podcast_func):
        mock_cpoa_result = {
            "task_id": "some_id",
            "topic": "Test CPOA Internal Fail",
            "status": "failed_pswa_request_exception", # Example failure status from CPOA
            "error_message": "PSWA service call failed after retries during CPOA run.",
            "asf_notification_status": None,
            "asf_websocket_url": None,
            "final_audio_details": {"status": "not_run", "message": "PSWA failed, VFA not reached"},
            "orchestration_log": [{"timestamp": "ts_now", "message": "PSWA failed in CPOA"}]
        }
        mock_orchestrate_podcast_func.return_value = mock_cpoa_result

        response = self.client.post('/api/v1/podcasts', json={'topic': 'Test CPOA Internal Fail'})
        
        # API Gateway successfully handled the request and got a result from CPOA,
        # even if CPOA itself reported an internal failure.
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        generated_podcast_id = json_data['podcast_id']

        self.assertEqual(json_data['generation_status'], 'failed_pswa_request_exception')
        self.assertIn("PSWA service call failed after retries during CPOA run.", json_data['message'])
        self.assertIsNone(json_data.get('audio_url')) # No audio URL on CPOA failure

        # Verify DB state
        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM podcasts WHERE podcast_id = ?", (generated_podcast_id,))
            row = cursor.fetchone()
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['cpoa_status'], 'failed_pswa_request_exception')
        self.assertEqual(row['cpoa_error_message'], 'PSWA service call failed after retries during CPOA run.')
        self.assertIsNone(row['final_audio_filepath'])


    def test_create_podcast_task_missing_topic_payload(self):
        response = self.client.post('/api/v1/podcasts', json={}) # Empty JSON
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertIn("Missing or empty 'topic'", json_data['message'])

        response_no_topic_key = self.client.post('/api/v1/podcasts', json={'some_other_key': 'value'})
        self.assertEqual(response_no_topic_key.status_code, 400)
        json_data_no_topic_key = response_no_topic_key.get_json()
        self.assertIn("Missing or empty 'topic'", json_data_no_topic_key['message'])

        response_empty_topic_string = self.client.post('/api/v1/podcasts', json={'topic': ''})
        self.assertEqual(response_empty_topic_string.status_code, 400)
        json_data_empty_topic_string = response_empty_topic_string.get_json()
        self.assertIn("Missing or empty 'topic'", json_data_empty_topic_string['message'])

    def _insert_dummy_podcast(self, podcast_id, topic="Dummy Topic", cpoa_status="completed",
                              final_audio_filepath="/test/audio.mp3", stream_id="stream123",
                              asf_websocket_url="ws://test/ws", asf_notification_status="notified",
                              log_message="Log message", error_message=None,
                              task_created_ts=None):
        ts = task_created_ts if task_created_ts else datetime.now().isoformat()
        # Ensure log_data is a JSON string, as it would be stored from cpoa_result
        log_data_list = [{"timestamp": ts, "message": log_message, "data_preview": "N/A"}] # Match CPOA log structure
        log_data_str = json.dumps(log_data_list)

        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO podcasts (podcast_id, topic, cpoa_status, cpoa_error_message,
                                       final_audio_filepath, stream_id, asf_websocket_url,
                                       asf_notification_status, task_created_timestamp,
                                       last_updated_timestamp, cpoa_full_orchestration_log)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (podcast_id, topic, cpoa_status, error_message, final_audio_filepath, stream_id,
                 asf_websocket_url, asf_notification_status, ts, ts, log_data_str)
            )
            conn.commit()
            conn.close()
        return {"podcast_id": podcast_id, "topic": topic, "cpoa_status": cpoa_status,
                "final_audio_filepath": final_audio_filepath, "task_created_timestamp": ts,
                "cpoa_full_orchestration_log_json_str": log_data_str, "stream_id": stream_id,
                "asf_websocket_url": asf_websocket_url, "asf_notification_status": asf_notification_status,
                "cpoa_error_message": error_message, "last_updated_timestamp": ts}


    def test_get_podcast_details_success(self):
        dummy_data = self._insert_dummy_podcast("pdcast_detail_001", topic="Detail Test Topic")

        response = self.client.get(f'/api/v1/podcasts/{dummy_data["podcast_id"]}')
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        
        self.assertEqual(json_data['podcast_id'], dummy_data["podcast_id"])
        self.assertEqual(json_data['topic'], "Detail Test Topic")
        self.assertEqual(json_data['status'], dummy_data["cpoa_status"])
        self.assertTrue(json_data['audio_url'].endswith('/audio.mp3'))
        self.assertEqual(json_data['final_audio_filepath'], dummy_data["final_audio_filepath"])
        self.assertEqual(json_data['stream_id'], dummy_data["stream_id"])
        self.assertEqual(json_data['asf_websocket_url'], dummy_data["asf_websocket_url"])
        self.assertEqual(json_data['asf_notification_status'], dummy_data["asf_notification_status"])
        self.assertEqual(json_data['task_created_timestamp'], dummy_data["task_created_timestamp"])
        self.assertEqual(json_data['last_updated_timestamp'], dummy_data["last_updated_timestamp"])
        self.assertIsNone(json_data['error_message'])
        
        self.assertIsInstance(json_data['orchestration_log'], list)
        self.assertEqual(json_data['orchestration_log'][0]['message'], "Log message")

    def test_get_podcast_details_not_found(self):
        response = self.client.get('/api/v1/podcasts/non_existent_id')
        self.assertEqual(response.status_code, 404)
        json_data = response.get_json()
        self.assertEqual(json_data['error'], "Not Found")

    def test_list_podcasts_success(self):
        # Insert in a specific order for timestamp checking (older first)
        self._insert_dummy_podcast("pdcast_list_001", topic="List Test 1", task_created_ts="2023-01-01T10:00:00Z")
        self._insert_dummy_podcast("pdcast_list_002", topic="List Test 2", cpoa_status="failed_pswa", task_created_ts="2023-01-01T12:00:00Z")
        
        response = self.client.get('/api/v1/podcasts?page=1&per_page=10')
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()

        self.assertEqual(json_data['page'], 1)
        self.assertEqual(json_data['per_page'], 10)
        self.assertEqual(json_data['total_podcasts'], 2)
        self.assertEqual(json_data['total_pages'], 1)
        self.assertEqual(len(json_data['podcasts']), 2)

        self.assertEqual(json_data['podcasts'][0]['topic'], "List Test 2") # Most recent
        self.assertEqual(json_data['podcasts'][0]['status'], "failed_pswa")
        self.assertEqual(json_data['podcasts'][1]['topic'], "List Test 1")
        self.assertEqual(json_data['podcasts'][1]['status'], "completed")

    def test_list_podcasts_pagination(self):
        for i in range(15): # Create 15 podcasts
            # Create with slightly different timestamps to ensure order
            ts = datetime(2023, 1, 1, 10, i, 0).isoformat() + "Z"
            self._insert_dummy_podcast(f"pdcast_page_{i:02d}", topic=f"Page Test {i}", task_created_ts=ts)
        
        response_p1 = self.client.get('/api/v1/podcasts?page=1&per_page=5')
        json_data_p1 = response_p1.get_json()
        self.assertEqual(len(json_data_p1['podcasts']), 5)
        self.assertEqual(json_data_p1['page'], 1)
        self.assertEqual(json_data_p1['per_page'], 5)
        self.assertEqual(json_data_p1['total_podcasts'], 15)
        self.assertEqual(json_data_p1['total_pages'], 3)
        self.assertEqual(json_data_p1['podcasts'][0]['topic'], "Page Test 14") # Most recent (i=14)

        response_p2 = self.client.get('/api/v1/podcasts?page=2&per_page=5')
        json_data_p2 = response_p2.get_json()
        self.assertEqual(len(json_data_p2['podcasts']), 5)
        self.assertEqual(json_data_p2['page'], 2)
        self.assertEqual(json_data_p2['podcasts'][0]['topic'], "Page Test 9") # Next set of 5

    def test_list_podcasts_invalid_pagination_params(self):
        response_bad_page = self.client.get('/api/v1/podcasts?page=abc&per_page=5')
        self.assertEqual(response_bad_page.status_code, 400)
        json_data_bad_page = response_bad_page.get_json()
        self.assertIn("Invalid page or per_page parameters", json_data_bad_page['message'])

        response_bad_per_page = self.client.get('/api/v1/podcasts?page=1&per_page=xyz')
        self.assertEqual(response_bad_per_page.status_code, 400)

    @patch('aethercast.api_gateway.main.requests.post')
    @patch('aethercast.api_gateway.main.orchestrate_snippet_generation')
    def test_get_snippets_success(self, mock_cpoa_orchestrate_snippet, mock_tda_requests_post):
        # Mock TDA response
        mock_tda_response = MagicMock()
        mock_tda_response.ok = True
        mock_tda_response.status_code = 200
        mock_tda_response.json.return_value = {
            "topics": [{"id": "tda_topic_1", "title": "TDA Topic 1", "summary": "Summary 1", "keywords": ["k1"]}]
        }
        mock_tda_requests_post.return_value = mock_tda_response

        # Mock CPOA snippet generation response
        mock_cpoa_orchestrate_snippet.return_value = {
            "snippet_id": "cpoa_snip_1", "title": "CPOA Snippet Title 1",
            "snippet_text": "Text 1", "keywords": ["k1", "cpoa_k"],
            "topic_info": {"id": "tda_topic_1", "title": "TDA Topic 1"} # Ensure topic_info is present
        }

        response = self.client.get('/api/v1/snippets')
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        self.assertIn("snippets", json_data)
        self.assertEqual(len(json_data["snippets"]), 1)
        self.assertEqual(json_data["snippets"][0]["title"], "CPOA Snippet Title 1")

        mock_tda_requests_post.assert_called_once()
        self.assertEqual(mock_tda_requests_post.call_args[0][0], api_gw_main.TDA_SERVICE_URL)

        mock_cpoa_orchestrate_snippet.assert_called_once()
        passed_topic_info = mock_cpoa_orchestrate_snippet.call_args[1]['topic_info']
        self.assertEqual(passed_topic_info['topic_id'], "tda_topic_1")
        self.assertEqual(passed_topic_info['title_suggestion'], "TDA Topic 1")


    def test_serve_podcast_audio_success(self):
        audio_filename = f"test_audio_{self._testMethodName}.mp3" # Unique name
        # For testing, create the dummy file in a known, writable location like /tmp
        temp_audio_path_for_db = f"/tmp/{audio_filename}"

        dummy_data = self._insert_dummy_podcast("pdcast_audio_001", final_audio_filepath=temp_audio_path_for_db)
        
        with open(temp_audio_path_for_db, "wb") as f:
            f.write(b"dummy audio data")
        
        response = self.client.get(f'/api/v1/podcasts/{dummy_data["podcast_id"]}/audio.mp3')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'audio/mpeg')
        self.assertEqual(response.data, b"dummy audio data")
        
        os.remove(temp_audio_path_for_db)

    def test_serve_podcast_audio_not_found_db(self):
        response = self.client.get('/api/v1/podcasts/non_existent_audio_id/audio.mp3')
        self.assertEqual(response.status_code, 404)
        json_data = response.get_json()
        self.assertIn("Audio not found", json_data["message"])

    def test_serve_podcast_audio_file_missing(self):
        dummy_data = self._insert_dummy_podcast("pdcast_audio_missing_file", final_audio_filepath="/tmp/this_file_should_not_exist.mp3")
        response = self.client.get(f'/api/v1/podcasts/{dummy_data["podcast_id"]}/audio.mp3')
        self.assertEqual(response.status_code, 404)
        json_data = response.get_json()
        self.assertIn("Audio file missing", json_data["message"])


if __name__ == '__main__':
    unittest.main(verbosity=2)
