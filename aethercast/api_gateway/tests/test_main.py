import unittest
import json
import os
import uuid
import sqlite3 # Added
from datetime import datetime # Added
from unittest import mock
from unittest.mock import patch, Mock

# Ensure the 'aethercast' directory is in the Python path for absolute imports.
# This allows tests to be run from the root directory or within the tests directory.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
api_gateway_dir = os.path.dirname(current_script_dir)  # aethercast/api_gateway/
aethercast_dir = os.path.dirname(api_gateway_dir)      # aethercast/
project_root_dir = os.path.dirname(aethercast_dir)     # directory containing aethercast/

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

# Import app and DB functions from the main module
# We will patch DATABASE_FILE to use an in-memory DB for tests.
from aethercast.api_gateway.main import app, init_db, get_db_connection, DATABASE_FILE


@patch('aethercast.api_gateway.main.DATABASE_FILE', ':memory:') # Patch DB to be in-memory for all tests in this class
class TestApiEndpoints(unittest.TestCase):

    def setUp(self, mock_db_file_ignored): # mock_db_file_ignored is the mock object from class decorator
        """Set up the test client and initialize the in-memory database."""
        if hasattr(app, 'testing'):
            app.testing = True
        else:
            app.config['TESTING'] = True
        
        self.client = app.test_client()
        
        # Initialize the schema for the in-memory database
        # init_db from main.py will use the patched DATABASE_FILE (':memory:')
        with app.app_context(): # Ensure init_db runs within Flask app context if it uses app.logger
            init_db()

    def _get_db_connection_for_test(self):
        """Helper to get a connection to the in-memory test database."""
        # This ensures we're connecting to the same :memory: db used by the app during the test.
        # Since DATABASE_FILE is patched to ':memory:', get_db_connection from main will connect to it.
        return get_db_connection()

    def _insert_podcast_to_db(self, podcast_id, audio_filepath, topic, cpoa_details_dict=None):
        """Helper to insert a podcast record into the test DB."""
        timestamp = datetime.now().isoformat()
        cpoa_details_json = json.dumps(cpoa_details_dict or {"message": "test details"})
        with self._get_db_connection_for_test() as conn:
            conn.execute(
                "INSERT INTO podcasts (podcast_id, audio_filepath, topic, generation_timestamp, cpoa_details) VALUES (?, ?, ?, ?, ?)",
                (podcast_id, audio_filepath, topic, timestamp, cpoa_details_json)
            )
            conn.commit()
    
    def _get_podcast_from_db(self, podcast_id):
        """Helper to retrieve a podcast record from the test DB."""
        with self._get_db_connection_for_test() as conn:
            cursor = conn.execute("SELECT * FROM podcasts WHERE podcast_id = ?", (podcast_id,))
            return cursor.fetchone()


    # --- Tests for POST /api/v1/podcasts ---

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_success(self, mock_orchestrate_cpoa, mock_db_file_ignored=None): # mock_db_file_ignored for class decorator
        """Test successful podcast creation (201 Created) and DB record."""
        fake_filepath = "/tmp/fake_audio_success.mp3"
        cpoa_return_value = {
            "status": "completed",
            "final_audio_details": {"audio_filepath": fake_filepath, "audio_format": "mp3", "script_char_count": 100, "engine_used": "google_cloud_tts"},
            "orchestration_log": [{"message": "CPOA completed"}],
            "topic": "test success topic" # CPOA result includes topic
        }
        mock_orchestrate_cpoa.return_value = cpoa_return_value
        
        test_topic_input = "test success topic"
        response = self.client.post('/api/v1/podcasts', json={"topic": test_topic_input})
        
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertIn("podcast_id", data)
        podcast_id_from_response = data["podcast_id"]
        
        self.assertEqual(data["topic"], test_topic_input)
        self.assertEqual(data["generation_status"], "completed")
        self.assertTrue(data["audio_url"].endswith(f"/{podcast_id_from_response}/audio.mp3"))
        self.assertEqual(data["message"], "Podcast generated successfully and metadata saved.")
        
        # Verify database record
        db_record = self._get_podcast_from_db(podcast_id_from_response)
        self.assertIsNotNone(db_record)
        self.assertEqual(db_record["podcast_id"], podcast_id_from_response)
        self.assertEqual(db_record["audio_filepath"], fake_filepath)
        self.assertEqual(db_record["topic"], test_topic_input)
        self.assertIsNotNone(db_record["generation_timestamp"])
        self.assertEqual(json.loads(db_record["cpoa_details"]), cpoa_return_value)
        
        mock_orchestrate_cpoa.assert_called_once_with(topic=test_topic_input)

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_completed_with_warnings_no_filepath(self, mock_orchestrate_cpoa, mock_db_file_ignored=None):
        """Test 200 OK when CPOA completes with warnings (no audio filepath), no DB record."""
        mock_orchestrate_cpoa.return_value = {
            "status": "completed_with_warnings",
            "error_message": "VFA skipped audio generation",
            "final_audio_details": {"audio_filepath": None, "status": "skipped"},
            "orchestration_log": [{"message": "VFA skipped"}]
        }
        
        response = self.client.post('/api/v1/podcasts', json={"topic": "test warning topic"})
        
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertNotIn("podcast_id", data)
        self.assertEqual(data["message"], "VFA skipped audio generation")

        # Verify no record in DB
        with self._get_db_connection_for_test() as conn:
            count = conn.execute("SELECT COUNT(*) FROM podcasts").fetchone()[0]
        self.assertEqual(count, 0)
        mock_orchestrate_cpoa.assert_called_once_with(topic="test warning topic")

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_cpoa_failed_status(self, mock_orchestrate_cpoa, mock_db_file_ignored=None):
        """Test 500 when CPOA returns 'failed' status, no DB record."""
        mock_orchestrate_cpoa.return_value = {"status": "failed", "error_message": "CPOA process failed"}
        response = self.client.post('/api/v1/podcasts', json={"topic": "test cpoa fail topic"})
        self.assertEqual(response.status_code, 500)
        # Verify no record in DB
        with self._get_db_connection_for_test() as conn:
            count = conn.execute("SELECT COUNT(*) FROM podcasts").fetchone()[0]
        self.assertEqual(count, 0)

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_cpoa_raises_exception(self, mock_orchestrate_cpoa, mock_db_file_ignored=None):
        """Test 500 when CPOA call raises an exception, no DB record."""
        mock_orchestrate_cpoa.side_effect = Exception("CPOA exploded")
        response = self.client.post('/api/v1/podcasts', json={"topic": "test cpoa exception topic"})
        self.assertEqual(response.status_code, 500)
        # Verify no record in DB
        with self._get_db_connection_for_test() as conn:
            count = conn.execute("SELECT COUNT(*) FROM podcasts").fetchone()[0]
        self.assertEqual(count, 0)

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_no_topic_bad_request(self, mock_orchestrate_cpoa, mock_db_file_ignored=None):
        """Test 400 Bad Request when 'topic' is missing."""
        response = self.client.post('/api/v1/podcasts', json={})
        self.assertEqual(response.status_code, 400)
        mock_orchestrate_cpoa.assert_not_called()

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation_imported', False)
    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation') 
    def test_create_podcast_cpoa_module_not_loaded(self, mock_cpoa_func, mock_import_flag, mock_db_file_ignored=None):
        """Test 503 Service Unavailable if CPOA module isn't loaded."""
        response = self.client.post('/api/v1/podcasts', json={"topic": "test cpoa import fail"})
        self.assertEqual(response.status_code, 503)
        mock_cpoa_func.assert_not_called()


    # --- Tests for GET /api/v1/podcasts/<podcast_id>/audio.mp3 ---

    @patch('aethercast.api_gateway.main.send_file')
    @patch('aethercast.api_gateway.main.os.path.exists')
    def test_get_audio_success(self, mock_os_path_exists, mock_send_file, mock_db_file_ignored=None):
        """Test successful audio file retrieval from DB record."""
        test_podcast_id = str(uuid.uuid4())
        fake_filepath = f"/tmp/test_audio_{test_podcast_id}.mp3"
        self._insert_podcast_to_db(test_podcast_id, fake_filepath, "test topic")
        
        mock_os_path_exists.return_value = True
        mock_send_file.return_value = "dummy audio data" 
        
        response = self.client.get(f'/api/v1/podcasts/{test_podcast_id}/audio.mp3')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.decode(), "dummy audio data")
        mock_os_path_exists.assert_called_once_with(fake_filepath)
        mock_send_file.assert_called_once_with(fake_filepath, mimetype='audio/mpeg')

    @patch('aethercast.api_gateway.main.send_file')
    @patch('aethercast.api_gateway.main.os.path.exists')
    def test_get_audio_podcast_id_not_in_db(self, mock_os_path_exists, mock_send_file, mock_db_file_ignored=None):
        """Test 404 when podcast_id is not in the database."""
        non_existent_uuid = str(uuid.uuid4())
        response = self.client.get(f'/api/v1/podcasts/{non_existent_uuid}/audio.mp3')
        
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertEqual(data["error"], "Not Found")
        self.assertIn("Invalid or expired podcast_id", data["message"])
        mock_os_path_exists.assert_not_called()
        mock_send_file.assert_not_called()

    @patch('aethercast.api_gateway.main.send_file')
    @patch('aethercast.api_gateway.main.os.path.exists')
    def test_get_audio_file_path_not_exist(self, mock_os_path_exists, mock_send_file, mock_db_file_ignored=None):
        """Test 404 when audio file path (from DB) does not exist on disk."""
        test_podcast_id = str(uuid.uuid4())
        fake_filepath = f"/tmp/stale_audio_{test_podcast_id}.mp3"
        self._insert_podcast_to_db(test_podcast_id, fake_filepath, "stale topic")
        
        mock_os_path_exists.return_value = False 
        
        response = self.client.get(f'/api/v1/podcasts/{test_podcast_id}/audio.mp3')
        
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertEqual(data["error"], "Not Found")
        self.assertIn("Audio file missing or no longer available", data["message"])
        mock_os_path_exists.assert_called_once_with(fake_filepath)
        mock_send_file.assert_not_called()
        # Note: The main code does not delete the DB record if file is missing, so no need to check that.


    @patch('aethercast.api_gateway.main.send_file')
    @patch('aethercast.api_gateway.main.os.path.exists')
    def test_get_audio_send_file_raises_exception(self, mock_os_path_exists, mock_send_file, mock_db_file_ignored=None):
        """Test 500 when send_file itself raises an exception."""
        test_podcast_id = str(uuid.uuid4())
        fake_filepath = f"/tmp/problematic_audio_{test_podcast_id}.mp3"
        self._insert_podcast_to_db(test_podcast_id, fake_filepath, "problematic topic")
        
        mock_os_path_exists.return_value = True
        mock_send_file.side_effect = Exception("Simulated error during send_file")
        
        response = self.client.get(f'/api/v1/podcasts/{test_podcast_id}/audio.mp3')
        
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data["error"], "Internal Server Error")
        self.assertIn("An error occurred while trying to serve the audio file", data["message"])
        mock_os_path_exists.assert_called_once_with(fake_filepath)
        mock_send_file.assert_called_once_with(fake_filepath, mimetype='audio/mpeg')

if __name__ == '__main__':
    unittest.main()
