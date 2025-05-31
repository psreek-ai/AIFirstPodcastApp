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
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='topics_snippets';")
                if cursor.fetchone() is None:
                    raise AssertionError("topics_snippets table was not created in :memory: database.")
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
            cursor.execute("DELETE FROM podcasts;")
            cursor.execute("DELETE FROM topics_snippets;") # Clear topics_snippets table as well
            conn.commit()
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
        post_response = self.client.post('/api/v1/podcasts', json=post_payload)
        
        self.assertEqual(post_response.status_code, 201)
        json_data = post_response.get_json()
        self.assertIn('podcast_id', json_data)
        generated_podcast_id = json_data['podcast_id']
        
        self.assertEqual(json_data['topic'], 'Test Success Topic')
        self.assertEqual(json_data['generation_status'], 'completed')
        self.assertTrue(json_data['audio_url'].endswith(f'/audio.mp3'))
        
        mock_orchestrate_podcast_func.assert_called_once()
        call_args = mock_orchestrate_podcast_func.call_args[1]
        self.assertEqual(call_args['topic'], 'Test Success Topic')
        self.assertEqual(call_args['task_id'], generated_podcast_id)
        self.assertEqual(call_args['db_path'], api_gw_main.DATABASE_FILE)
        self.assertEqual(call_args['voice_params_input'], test_voice_params)

        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM podcasts WHERE podcast_id = ?", (generated_podcast_id,))
            row = cursor.fetchone()
            conn.close()
        
        self.assertIsNotNone(row, "Podcast record not found in DB.")
        self.assertEqual(row['topic'], 'Test Success Topic')
        self.assertEqual(row['cpoa_status'], 'completed')
        self.assertEqual(row['final_audio_filepath'], '/srv/aethercast/audio/mock_audio.mp3')
        self.assertEqual(row['stream_id'], 'stream_mock_abc')
        self.assertIsNotNone(row['cpoa_full_orchestration_log'])
        log_content = json.loads(row['cpoa_full_orchestration_log'])
        self.assertEqual(log_content[0]['message'], "All good from CPOA")
        self.assertIsNotNone(row['tts_settings_used'])
        tts_settings_content = json.loads(row['tts_settings_used'])
        self.assertEqual(tts_settings_content['voice_name'], "en-US-TestVoice") # This comes from CPOA's mock result
        self.assertEqual(tts_settings_content['speaking_rate'], 1.0)


    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_task_cpoa_reports_failure(self, mock_orchestrate_podcast_func):
        mock_cpoa_result = {
            "task_id": "some_id",
            "topic": "Test CPOA Internal Fail",
            "status": "failed_pswa_request_exception",
            "error_message": "PSWA service call failed after retries during CPOA run.",
            "asf_notification_status": None,
            "asf_websocket_url": None,
            "final_audio_details": {
                "status": "not_run", "message": "PSWA failed, VFA not reached",
                # VFA might return attempted/default tts_settings even on failure before synthesis
                "tts_settings_used": {"voice_name": "default_on_fail", "speaking_rate": 1.0, "pitch": 0.0, "audio_encoding":"MP3"}
            },
            "orchestration_log": [{"timestamp": "ts_now", "message": "PSWA failed in CPOA"}]
        }
        mock_orchestrate_podcast_func.return_value = mock_cpoa_result

        response = self.client.post('/api/v1/podcasts', json={'topic': 'Test CPOA Internal Fail'})
        
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        generated_podcast_id = json_data['podcast_id']

        self.assertEqual(json_data['generation_status'], 'failed_pswa_request_exception')
        self.assertIn("PSWA service call failed after retries during CPOA run.", json_data['message'])
        self.assertIsNone(json_data.get('audio_url'))

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
        self.assertIsNotNone(row['tts_settings_used']) # Check tts_settings_used is stored even on failure
        tts_settings_content = json.loads(row['tts_settings_used'])
        self.assertEqual(tts_settings_content['voice_name'], "default_on_fail")


    def test_create_podcast_task_missing_topic_payload(self):
        response = self.client.post('/api/v1/podcasts', json={})
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
                              task_created_ts=None, tts_settings_used=None): # Added tts_settings_used
        ts = task_created_ts if task_created_ts else datetime.now().isoformat()
        log_data_list = [{"timestamp": ts, "message": log_message, "data_preview": "N/A"}]
        log_data_str = json.dumps(log_data_list)
        tts_settings_str = json.dumps(tts_settings_used) if tts_settings_used else None


        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO podcasts (podcast_id, topic, cpoa_status, cpoa_error_message,
                                       final_audio_filepath, stream_id, asf_websocket_url,
                                       asf_notification_status, task_created_timestamp,
                                       last_updated_timestamp, cpoa_full_orchestration_log, tts_settings_used)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (podcast_id, topic, cpoa_status, error_message, final_audio_filepath, stream_id,
                 asf_websocket_url, asf_notification_status, ts, ts, log_data_str, tts_settings_str)
            )
            conn.commit()
            conn.close()
        return {"podcast_id": podcast_id, "topic": topic, "cpoa_status": cpoa_status,
                "final_audio_filepath": final_audio_filepath, "task_created_timestamp": ts,
                "cpoa_full_orchestration_log_json_str": log_data_str, "stream_id": stream_id,
                "asf_websocket_url": asf_websocket_url, "asf_notification_status": asf_notification_status,
                "cpoa_error_message": error_message, "last_updated_timestamp": ts,
                "tts_settings_used": tts_settings_used }

    def _insert_dummy_snippet(self, id, type='snippet', title="Dummy Snippet", summary="Summary of snippet",
                              keywords_list=None, source_url=None, source_name=None,
                              original_topic_details_dict=None, llm_model_used_for_snippet="gpt-test",
                              cover_art_prompt="A prompt", generation_timestamp=None,
                              last_accessed_timestamp=None, relevance_score=0.75):
        keywords_json = json.dumps(keywords_list if keywords_list else ["dummy", "test"])
        original_topic_details_json = json.dumps(original_topic_details_dict) if original_topic_details_dict else None
        gen_ts = generation_timestamp if generation_timestamp else datetime.now().isoformat()
        last_acc_ts = last_accessed_timestamp if last_accessed_timestamp else gen_ts

        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO topics_snippets (
                       id, type, title, summary, keywords, source_url, source_name,
                       original_topic_details, llm_model_used_for_snippet, cover_art_prompt,
                       generation_timestamp, last_accessed_timestamp, relevance_score
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (id, type, title, summary, keywords_json, source_url, source_name,
                 original_topic_details_json, llm_model_used_for_snippet, cover_art_prompt,
                 gen_ts, last_acc_ts, relevance_score)
            )
            conn.commit()
            conn.close()
        return {
            "id": id, "type": type, "title": title, "summary": summary, "keywords": keywords_list,
            "generation_timestamp": gen_ts, "last_accessed_timestamp": last_acc_ts
        }

    def test_get_podcast_details_success(self):
        tts_settings_to_insert = {"voice_name": "en-AU-Wavenet-C", "pitch": -2.0}
        dummy_data = self._insert_dummy_podcast(
            "pdcast_detail_001",
            topic="Detail Test Topic",
            tts_settings_used=tts_settings_to_insert # Pass here for insertion
        )

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
        self.assertIsNotNone(json_data['tts_settings_used'])
        self.assertEqual(json_data['tts_settings_used']['voice_name'], "en-AU-Wavenet-C")
        self.assertEqual(json_data['tts_settings_used']['pitch'], -2.0)

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

        response = self.client.get('/api/v1/snippets') # Default limit is 5 for TDA call in get_dynamic_snippets
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        self.assertIn("snippets", json_data)
        self.assertEqual(len(json_data["snippets"]), 1)
        self.assertEqual(json_data["snippets"][0]["title"], "CPOA Snippet Title 1")
        self.assertEqual(json_data["source"], "generation") # Should be 'generation' as cache is empty

        mock_tda_requests_post.assert_called_once()
        # Check that the limit passed to TDA was based on request.args.get('limit', 5, type=int)
        # In this test, no query param, so it defaults to 5.
        self.assertEqual(mock_tda_requests_post.call_args[1]['json']['limit'], 5)
        self.assertEqual(mock_tda_requests_post.call_args[0][0], api_gw_main.TDA_SERVICE_URL)

        mock_cpoa_orchestrate_snippet.assert_called_once()
        passed_topic_info = mock_cpoa_orchestrate_snippet.call_args[1]['topic_info']
        self.assertEqual(passed_topic_info['topic_id'], "tda_topic_1")
        self.assertEqual(passed_topic_info['title_suggestion'], "TDA Topic 1")
        # Check that the full topic_obj from TDA is passed for CPOA to store
        self.assertIn("original_topic_details_from_tda", passed_topic_info)
        self.assertEqual(passed_topic_info["original_topic_details_from_tda"]["id"], "tda_topic_1")


    @patch('aethercast.api_gateway.main.requests.post')
    @patch('aethercast.api_gateway.main.orchestrate_snippet_generation')
    @patch('aethercast.api_gateway.main.datetime') # To mock datetime.utcnow and datetime.now
    def test_get_snippets_from_cache_fresh_sufficient(self, mock_datetime, mock_cpoa_orchestrate_snippet, mock_tda_requests_post):
        # Mock current time
        fixed_now = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.utcnow.return_value = fixed_now
        mock_datetime.now.return_value = fixed_now # If API GW uses now() for DB updates

        # Insert fresh snippets (e.g., 5, if API_GW_SNIPPET_CACHE_SIZE is 10)
        for i in range(api_gw_main.API_GW_SNIPPET_CACHE_SIZE // 2):
            self._insert_dummy_snippet(
                id=f"fresh_snippet_{i}",
                title=f"Fresh Snippet {i}",
                generation_timestamp=(fixed_now - timedelta(hours=1)).isoformat(), # 1 hour old
                last_accessed_timestamp=(fixed_now - timedelta(hours=1)).isoformat()
            )

        response = self.client.get('/api/v1/snippets')
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()

        self.assertEqual(json_data.get("source"), "cache")
        self.assertEqual(len(json_data["snippets"]), api_gw_main.API_GW_SNIPPET_CACHE_SIZE // 2)
        self.assertEqual(json_data["snippets"][0]["title"], f"Fresh Snippet {(api_gw_main.API_GW_SNIPPET_CACHE_SIZE // 2) - 1}") # Ordered by gen desc

        mock_tda_requests_post.assert_not_called()
        mock_cpoa_orchestrate_snippet.assert_not_called()

        # Verify last_accessed_timestamp update
        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT last_accessed_timestamp FROM topics_snippets WHERE id = 'fresh_snippet_0'")
            row = cursor.fetchone()
            conn.close()
            self.assertIsNotNone(row)
            # Check if timestamp is very close to fixed_now.isoformat()
            # Allow for small differences due to execution time if not mocking datetime.now() inside the endpoint strictly.
            # Since we mocked datetime.now() at the module level for this test, it should be exact.
            self.assertEqual(row['last_accessed_timestamp'], fixed_now.isoformat())

    @patch('aethercast.api_gateway.main.requests.post')
    @patch('aethercast.api_gateway.main.orchestrate_snippet_generation')
    @patch('aethercast.api_gateway.main.datetime')
    def test_get_snippets_cache_stale_generates_new(self, mock_datetime, mock_cpoa_orchestrate_snippet, mock_tda_requests_post):
        fixed_now = datetime(2023, 1, 10, 12, 0, 0) # Current time
        mock_datetime.utcnow.return_value = fixed_now
        mock_datetime.now.return_value = fixed_now


        # Insert stale snippets
        for i in range(api_gw_main.API_GW_SNIPPET_CACHE_SIZE):
            self._insert_dummy_snippet(
                id=f"stale_snippet_{i}",
                title=f"Stale Snippet {i}",
                # Older than API_GW_SNIPPET_CACHE_MAX_AGE_HOURS (default 24)
                generation_timestamp=(fixed_now - timedelta(hours=api_gw_main.API_GW_SNIPPET_CACHE_MAX_AGE_HOURS + 1)).isoformat()
            )

        # Mock TDA and CPOA since cache should be considered stale
        mock_tda_response = MagicMock(ok=True, status_code=200)
        mock_tda_response.json.return_value = {"topics": [{"id": "tda_new_1", "title": "New TDA Topic"}]}
        mock_tda_requests_post.return_value = mock_tda_response
        mock_cpoa_orchestrate_snippet.return_value = {"snippet_id": "cpoa_new_1", "title": "Newly Generated Snippet"}

        response = self.client.get('/api/v1/snippets')
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()

        self.assertEqual(json_data.get("source"), "generation")
        self.assertEqual(len(json_data["snippets"]), 1)
        self.assertEqual(json_data["snippets"][0]["title"], "Newly Generated Snippet")
        mock_tda_requests_post.assert_called_once()
        mock_cpoa_orchestrate_snippet.assert_called_once()

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
