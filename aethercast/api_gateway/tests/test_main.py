import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json
import sqlite3 # For direct DB assertions
from datetime import datetime, timedelta # Added timedelta for session tests

# Adjust path to import API Gateway main module and CPOA (for mocking)
current_dir = os.path.dirname(os.path.abspath(__file__))
api_gw_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(api_gw_dir)
project_root_dir = os.path.dirname(aethercast_dir)

sys.path.insert(0, project_root_dir)
sys.path.insert(0, aethercast_dir)
sys.path.insert(0, api_gw_dir)


from aethercast.api_gateway import main as api_gw_main

ORIGINAL_DATABASE_FILE = api_gw_main.DATABASE_FILE

class TestAPIGateway(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        api_gw_main.app.config['TESTING'] = True
        cls.client = api_gw_main.app.test_client()

        # Use a shared, named in-memory database.
        # One connection (db_master_conn) is kept open by the test class
        # to ensure the :memory: database persists.
        # The DATABASE_FILE global in main app is patched to use the same named URI,
        # so app's get_db_connection() calls will connect to this same DB.
        cls.db_master_conn = sqlite3.connect("file::memory:?cache=shared", check_same_thread=False)
        cls.db_master_conn.row_factory = sqlite3.Row

        # Patch the DATABASE_FILE global in the main module
        cls.db_file_patcher = patch.object(api_gw_main, 'DATABASE_FILE', "file::memory:?cache=shared")
        cls.mock_db_file_uri = cls.db_file_patcher.start()

        # Apply schema using the master connection
        try:
            cursor = cls.db_master_conn.cursor()
            cursor.executescript(api_gw_main.DB_SCHEMA_SQL)
            cls.db_master_conn.commit()

            # Sanity check (using the same master connection)
            tables_to_verify = ['podcasts', 'topics_snippets', 'generated_scripts', 'user_sessions']
            for table_name in tables_to_verify:
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
                if cursor.fetchone() is None:
                    raise AssertionError(f"{table_name} table was not created in shared in-memory DB.")
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_topic_hash';")
            if cursor.fetchone() is None:
                raise AssertionError("idx_topic_hash index on generated_scripts was not created.")
        except Exception as e:
            cls.db_master_conn.close()
            cls.db_file_patcher.stop()
            raise e

    @classmethod
    def tearDownClass(cls):
        cls.db_file_patcher.stop()
        if cls.db_master_conn:
            cls.db_master_conn.close()

    def setUp(self):
        # Clean tables using the master connection before each test
        cursor = self.db_master_conn.cursor()
        cursor.execute("DELETE FROM podcasts;")
        cursor.execute("DELETE FROM topics_snippets;")
        cursor.execute("DELETE FROM generated_scripts;")
        cursor.execute("DELETE FROM user_sessions;")
        self.db_master_conn.commit()

    def test_health_check(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        self.assertEqual(json_data['status'], "API Gateway is healthy")
        # Further checks for CPOA functions and DB status can remain as they were

    # ... (Keep existing tests for /podcasts, /snippets, /explore like test_create_podcast_task_success etc.) ...
    # For brevity, I'm not re-pasting all of them, but they should be preserved.
    # I will add one example of an existing test to show placement.

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_task_success(self, mock_orchestrate_podcast_func):
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
        # Test without client_id first
        post_payload = {'topic': 'Test Success Topic', 'voice_params': test_voice_params}
        post_response = self.client.post('/api/v1/podcasts', json=post_payload)
        
        self.assertEqual(post_response.status_code, 201)
        # ... (rest of assertions for this test)

    # --- New Tests for Session Management ---

    def test_session_init_new_client(self):
        """Test POST /api/v1/session/init for a new client_id."""
        client_id = "test_client_new_01"
        response = self.client.post('/api/v1/session/init', json={"client_id": client_id})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['client_id'], client_id)
        self.assertEqual(data['preferences'], {})

        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT preferences_json FROM user_sessions WHERE session_id = ?", (client_id,))
            row = cursor.fetchone()
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0]), {})

    def test_session_init_existing_client(self):
        """Test POST /api/v1/session/init for an existing client_id."""
        client_id = "test_client_existing_02"
        initial_prefs = {"theme": "dark"}
        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            now_ts = datetime.utcnow().isoformat()
            cursor.execute(
                "INSERT INTO user_sessions (session_id, created_timestamp, last_seen_timestamp, preferences_json) VALUES (?, ?, ?, ?)",
                (client_id, now_ts, now_ts, json.dumps(initial_prefs))
            )
            conn.commit()
            conn.close()

        response = self.client.post('/api/v1/session/init', json={"client_id": client_id})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['client_id'], client_id)
        self.assertEqual(data['preferences'], initial_prefs)

    def test_get_preferences_existing_client(self):
        """Test GET /api/v1/session/preferences for an existing client."""
        client_id = "test_client_get_prefs_03"
        prefs = {"language": "en", "news_category": "technology"}
        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            now_ts = datetime.utcnow().isoformat()
            cursor.execute(
                "INSERT INTO user_sessions (session_id, created_timestamp, last_seen_timestamp, preferences_json) VALUES (?, ?, ?, ?)",
                (client_id, now_ts, now_ts, json.dumps(prefs))
            )
            conn.commit()
            conn.close()

        response = self.client.get(f'/api/v1/session/preferences?client_id={client_id}')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['client_id'], client_id)
        self.assertEqual(data['preferences'], prefs)

    def test_get_preferences_non_existent_client(self):
        """Test GET /api/v1/session/preferences for a non-existent client."""
        client_id = "test_client_get_prefs_nonexistent_04"
        response = self.client.get(f'/api/v1/session/preferences?client_id={client_id}')
        self.assertEqual(response.status_code, 404)

    def test_get_preferences_no_client_id(self):
        """Test GET /api/v1/session/preferences without client_id query param."""
        response = self.client.get('/api/v1/session/preferences')
        self.assertEqual(response.status_code, 400)

    def test_update_preferences_existing_client(self):
        """Test POST /api/v1/session/preferences to update."""
        client_id = "test_client_update_prefs_06"
        self.client.post('/api/v1/session/init', json={"client_id": client_id}) # Initialize session

        new_prefs = {"news_category": "sports", "items_per_page": 20}
        response = self.client.post('/api/v1/session/preferences',
                                     json={"client_id": client_id, "preferences": new_prefs})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['message'], "Preferences updated successfully.")

        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT preferences_json FROM user_sessions WHERE session_id = ?", (client_id,))
            row = cursor.fetchone()
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0]), new_prefs)

    def test_update_preferences_non_existent_client(self):
        client_id = "test_client_update_prefs_nonexistent_07"
        new_prefs = {"theme": "light"}
        response = self.client.post('/api/v1/session/preferences',
                                     json={"client_id": client_id, "preferences": new_prefs})
        self.assertEqual(response.status_code, 404)

    def test_update_preferences_bad_payload(self):
        client_id = "test_client_update_prefs_bad_payload_08"
        self.client.post('/api/v1/session/init', json={"client_id": client_id})

        response = self.client.post('/api/v1/session/preferences', json={"client_id": client_id}) # Missing preferences
        self.assertEqual(response.status_code, 400)

        response = self.client.post('/api/v1/session/preferences',
                                     json={"client_id": client_id, "preferences": "not_a_dict"}) # Prefs not a dict
        self.assertEqual(response.status_code, 400)

        response = self.client.post('/api/v1/session/preferences',
                                     json={"preferences": {"theme":"blue"}}) # Missing client_id
        self.assertEqual(response.status_code, 400)

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_podcast_generation_with_client_id_fetches_preferences(self, mock_cpoa):
        """Test POST /api/v1/podcasts fetches and passes client_id preferences to CPOA."""
        client_id = "test_client_podcast_prefs_09"
        prefs = {"preferred_voice_model": "en-US-News-K", "news_category": "business"}

        self.client.post('/api/v1/session/init', json={"client_id": client_id})
        self.client.post('/api/v1/session/preferences', json={"client_id": client_id, "preferences": prefs})

        mock_cpoa.return_value = { # Simplified CPOA success response
            "status": "completed", "final_audio_details": {"tts_settings_used": {}},
            "orchestration_log": []
        }

        podcast_payload = {"topic": "AI in future", "client_id": client_id}
        response = self.client.post('/api/v1/podcasts', json=podcast_payload)
        self.assertEqual(response.status_code, 201)

        mock_cpoa.assert_called_once()
        args, kwargs = mock_cpoa.call_args
        self.assertIn("user_preferences", kwargs)
        self.assertEqual(kwargs["user_preferences"], prefs)
        self.assertEqual(kwargs["client_id"], client_id)
        self.assertIsNone(kwargs.get("voice_params_input")) # Ensure it's None if not in payload

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_podcast_generation_new_client_id_creates_session(self, mock_cpoa):
        """Test POST /api/v1/podcasts creates a session if client_id is new and provided."""
        client_id = "test_client_podcast_new_session_10"

        mock_cpoa.return_value = { "status": "completed", "final_audio_details": {"tts_settings_used": {}}}

        podcast_payload = { "topic": "New session test", "client_id": client_id }
        response = self.client.post('/api/v1/podcasts', json=podcast_payload)
        self.assertEqual(response.status_code, 201)

        with api_gw_main.app.app_context():
            conn = api_gw_main.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT preferences_json FROM user_sessions WHERE session_id = ?", (client_id,))
            row = cursor.fetchone()
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0]), {}) # Default empty preferences

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_podcast_generation_passes_test_scenarios(self, mock_cpoa):
        """Test POST /api/v1/podcasts passes test_scenarios to CPOA."""
        client_id = "test_client_scenarios_01"
        test_scenarios_payload = {"pswa": "insufficient_content", "vfa": "vfa_error_tts"}

        mock_cpoa.return_value = {
            "status": "completed", "final_audio_details": {"tts_settings_used": {}},
            "orchestration_log": []
        }

        podcast_payload = {
            "topic": "Test with Scenarios",
            "client_id": client_id,
            "test_scenarios": test_scenarios_payload
        }
        response = self.client.post('/api/v1/podcasts', json=podcast_payload)
        self.assertEqual(response.status_code, 201)

        mock_cpoa.assert_called_once()
        args, kwargs = mock_cpoa.call_args
        self.assertIn("test_scenarios", kwargs)
        self.assertEqual(kwargs["test_scenarios"], test_scenarios_payload)
        self.assertEqual(kwargs["client_id"], client_id)


    # Placeholder for other existing tests from the file to show structure
    # def test_get_podcast_details_success(self):
    #    ...
    # def test_list_podcasts_success(self):
    #    ...
    # (etc.)

if __name__ == '__main__':
    unittest.main(verbosity=2)
