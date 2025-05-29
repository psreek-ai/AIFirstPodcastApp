import unittest
import json
import os
import uuid
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

from aethercast.api_gateway.main import app, PODCAST_FILE_MAP

class TestApiEndpoints(unittest.TestCase):

    def setUp(self):
        """Set up the test client and clear any global state."""
        # For Flask 2.3+ app.testing = True is preferred
        # For older versions, app.config['TESTING'] = True
        if hasattr(app, 'testing'):
            app.testing = True
        else:
            app.config['TESTING'] = True
        
        self.client = app.test_client()
        PODCAST_FILE_MAP.clear() # Clear map before each test

    # --- Tests for POST /api/v1/podcasts ---

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_success(self, mock_orchestrate_cpoa):
        """Test successful podcast creation (201 Created)."""
        fake_filepath = "/tmp/fake_audio_success.mp3"
        mock_orchestrate_cpoa.return_value = {
            "status": "completed",
            "final_audio_details": {"audio_filepath": fake_filepath, "audio_format": "mp3"},
            "orchestration_log": [{"message": "CPOA completed"}]
        }
        
        response = self.client.post('/api/v1/podcasts', json={"topic": "test success topic"})
        
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertIn("podcast_id", data)
        self.assertEqual(data["topic"], "test success topic")
        self.assertEqual(data["generation_status"], "completed")
        self.assertTrue(data["audio_url"].startswith(f"/api/v1/podcasts/{data['podcast_id']}/audio.mp3"))
        self.assertEqual(data["message"], "Podcast generated successfully.")
        
        # Verify PODCAST_FILE_MAP
        self.assertIn(data["podcast_id"], PODCAST_FILE_MAP)
        self.assertEqual(PODCAST_FILE_MAP[data["podcast_id"]], fake_filepath)
        mock_orchestrate_cpoa.assert_called_once_with(topic="test success topic")

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_completed_with_warnings_no_filepath(self, mock_orchestrate_cpoa):
        """Test 200 OK when CPOA completes with warnings and no audio filepath."""
        mock_orchestrate_cpoa.return_value = {
            "status": "completed_with_warnings",
            "error_message": "VFA skipped audio generation",
            "final_audio_details": {"audio_filepath": None, "status": "skipped"}, # VFA might still provide status
            "orchestration_log": [{"message": "VFA skipped"}]
        }
        
        response = self.client.post('/api/v1/podcasts', json={"topic": "test warning topic"})
        
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertNotIn("podcast_id", data) # No podcast_id if no audio file
        self.assertNotIn("audio_url", data)
        self.assertEqual(data["topic"], "test warning topic")
        self.assertEqual(data["generation_status"], "completed_with_warnings")
        self.assertEqual(data["message"], "VFA skipped audio generation")
        self.assertTrue(len(PODCAST_FILE_MAP) == 0) # No file mapped
        mock_orchestrate_cpoa.assert_called_once_with(topic="test warning topic")

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_cpoa_failed_status(self, mock_orchestrate_cpoa):
        """Test 500 when CPOA returns a 'failed' status."""
        mock_orchestrate_cpoa.return_value = {
            "status": "failed",
            "error_message": "CPOA process failed at some point",
            "orchestration_log": [{"message": "Error in CPOA"}]
        }
        
        response = self.client.post('/api/v1/podcasts', json={"topic": "test cpoa fail topic"})
        
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data["topic"], "test cpoa fail topic")
        self.assertEqual(data["generation_status"], "failed")
        self.assertEqual(data["message"], "CPOA process failed at some point")
        self.assertTrue(len(PODCAST_FILE_MAP) == 0)
        mock_orchestrate_cpoa.assert_called_once_with(topic="test cpoa fail topic")

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_cpoa_raises_exception(self, mock_orchestrate_cpoa):
        """Test 500 when CPOA call raises an unexpected exception."""
        mock_orchestrate_cpoa.side_effect = Exception("CPOA exploded unexpectedly")
        
        response = self.client.post('/api/v1/podcasts', json={"topic": "test cpoa exception topic"})
        
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data["error"], "Internal Server Error")
        self.assertEqual(data["message"], "An unexpected error occurred during podcast generation.")
        self.assertTrue(len(PODCAST_FILE_MAP) == 0)
        mock_orchestrate_cpoa.assert_called_once_with(topic="test cpoa exception topic")

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation')
    def test_create_podcast_no_topic_bad_request(self, mock_orchestrate_cpoa):
        """Test 400 Bad Request when 'topic' is missing."""
        response = self.client.post('/api/v1/podcasts', json={}) # Empty JSON
        
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data["error"], "Bad Request")
        self.assertIn("Missing or empty 'topic'", data["message"])
        mock_orchestrate_cpoa.assert_not_called()

    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation_imported', False)
    @patch('aethercast.api_gateway.main.orchestrate_podcast_generation') # Still need to patch the actual function
    def test_create_podcast_cpoa_module_not_loaded(self, mock_orchestrate_cpoa_func, mock_import_flag_ignored):
        """Test 503 Service Unavailable if CPOA module isn't loaded."""
        # mock_import_flag_ignored is the mock for orchestrate_podcast_generation_imported
        # mock_orchestrate_cpoa_func is the mock for the function itself
        
        response = self.client.post('/api/v1/podcasts', json={"topic": "test cpoa import fail"})
        
        self.assertEqual(response.status_code, 503)
        data = response.get_json()
        self.assertEqual(data["error"], "Service Unavailable")
        self.assertIn("Core podcast orchestration module not loaded", data["message"])
        mock_orchestrate_cpoa_func.assert_not_called()


    # --- Tests for GET /api/v1/podcasts/<podcast_id>/audio.mp3 ---

    @patch('aethercast.api_gateway.main.send_file')
    @patch('aethercast.api_gateway.main.os.path.exists')
    def test_get_audio_success(self, mock_os_path_exists, mock_send_file):
        """Test successful audio file retrieval."""
        test_podcast_id = str(uuid.uuid4())
        fake_filepath = f"/tmp/test_audio_{test_podcast_id}.mp3"
        PODCAST_FILE_MAP[test_podcast_id] = fake_filepath
        
        mock_os_path_exists.return_value = True
        # send_file can return a Response object, or raise an exception. 
        # For a successful call, we can mock it to return a simple string or a mock response.
        mock_send_file.return_value = "dummy audio data from send_file" 
        
        response = self.client.get(f'/api/v1/podcasts/{test_podcast_id}/audio.mp3')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.decode(), "dummy audio data from send_file")
        mock_os_path_exists.assert_called_once_with(fake_filepath)
        mock_send_file.assert_called_once_with(fake_filepath, mimetype='audio/mpeg')

    @patch('aethercast.api_gateway.main.send_file')
    @patch('aethercast.api_gateway.main.os.path.exists')
    def test_get_audio_podcast_id_not_in_map(self, mock_os_path_exists, mock_send_file):
        """Test 404 when podcast_id is not in PODCAST_FILE_MAP."""
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
    def test_get_audio_file_path_not_exist(self, mock_os_path_exists, mock_send_file):
        """Test 404 when audio file path does not exist on disk."""
        test_podcast_id = str(uuid.uuid4())
        fake_filepath = f"/tmp/stale_audio_{test_podcast_id}.mp3"
        PODCAST_FILE_MAP[test_podcast_id] = fake_filepath
        
        mock_os_path_exists.return_value = False # Simulate file not existing
        
        response = self.client.get(f'/api/v1/podcasts/{test_podcast_id}/audio.mp3')
        
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertEqual(data["error"], "Not Found")
        self.assertIn("Audio file missing or no longer available", data["message"])
        mock_os_path_exists.assert_called_once_with(fake_filepath)
        mock_send_file.assert_not_called()
        # Check if the entry was removed from PODCAST_FILE_MAP
        self.assertNotIn(test_podcast_id, PODCAST_FILE_MAP)


    @patch('aethercast.api_gateway.main.send_file')
    @patch('aethercast.api_gateway.main.os.path.exists')
    def test_get_audio_send_file_raises_exception(self, mock_os_path_exists, mock_send_file):
        """Test 500 when send_file itself raises an exception."""
        test_podcast_id = str(uuid.uuid4())
        fake_filepath = f"/tmp/problematic_audio_{test_podcast_id}.mp3"
        PODCAST_FILE_MAP[test_podcast_id] = fake_filepath
        
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
