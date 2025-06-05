import unittest
import json
from unittest.mock import patch, MagicMock, call # Added call
import os
import sys
import logging

# Adjust path to import ASF main module
current_dir = os.path.dirname(os.path.abspath(__file__))
asf_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(asf_dir)
project_root_dir = os.path.dirname(aethercast_dir)

if asf_dir not in sys.path:
    sys.path.insert(0, asf_dir)
if aethercast_dir not in sys.path:
    sys.path.insert(0, aethercast_dir)
if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.asf import main as asf_main

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - ASF - %(message)s')

class TestASFFlaskEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        asf_main.app.testing = True
        cls.client = asf_main.app.test_client()

    def setUp(self):
        self.maxDiff = None
        self.mock_asf_config = {
            'ASF_UI_UPDATES_NAMESPACE': '/ui_updates_test_flask',
        }
        self.config_patcher = patch.dict(asf_main.asf_config, self.mock_asf_config, clear=True)
        self.mock_config_instance = self.config_patcher.start()

        self.namespace_global_patcher = patch.object(asf_main, 'ASF_UI_UPDATES_NAMESPACE', self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])
        self.namespace_global_patcher.start()


    def tearDown(self):
        self.config_patcher.stop()
        self.namespace_global_patcher.stop()

    def test_health_endpoint(self):
        response = self.client.get('/asf/health')
        self.assertEqual(response.status_code, 200)
        expected_response = {"status": "AudioStreamFeeder is healthy and running"}
        self.assertEqual(response.get_json(), expected_response)

    @patch('aethercast.asf.main.socketio.emit')
    def test_send_ui_update_success(self, mock_socketio_emit):
        payload = {
            "client_id": "test_client_123",
            "event_name": "TEST_EVENT",
            "data": {"key": "value"}
        }
        response = self.client.post('/asf/internal/send_ui_update', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "success", "message": "UI update sent to client."})

        mock_socketio_emit.assert_called_once_with(
            payload["event_name"],
            payload["data"],
            room=payload["client_id"],
            namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE']
        )

    def test_send_ui_update_missing_client_id(self):
        payload = {"event_name": "TEST_EVENT", "data": {"key": "value"}}
        response = self.client.post('/asf/internal/send_ui_update', json=payload)
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data.get('error_code'), "ASF_SENDUI_MISSING_PARAMETERS")
        self.assertIn("'client_id'", data.get("details", ""))

    def test_send_ui_update_missing_event_name(self):
        payload = {"client_id": "test_client_123", "data": {"key": "value"}}
        response = self.client.post('/asf/internal/send_ui_update', json=payload)
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data.get('error_code'), "ASF_SENDUI_MISSING_PARAMETERS")
        self.assertIn("'event_name'", data.get("details", ""))

    def test_send_ui_update_missing_data(self):
        payload = {"client_id": "test_client_123", "event_name": "TEST_EVENT"}
        response = self.client.post('/asf/internal/send_ui_update', json=payload)
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data.get('error_code'), "ASF_SENDUI_MISSING_PARAMETERS")
        self.assertIn("'data'", data.get("details", ""))

    def test_send_ui_update_empty_payload(self):
        response_no_json_body = self.client.post('/asf/internal/send_ui_update')
        self.assertEqual(response_no_json_body.status_code, 400)
        data_no_json_body = response_no_json_body.get_json()
        self.assertEqual(data_no_json_body.get('error_code'), "ASF_SENDUI_NO_PAYLOAD")

    @patch('aethercast.asf.main.socketio.emit')
    def test_send_ui_update_socketio_emit_exception(self, mock_socketio_emit):
        mock_socketio_emit.side_effect = Exception("Simulated SocketIO emit error")
        payload = {
            "client_id": "test_client_socket_error",
            "event_name": "EVENT_FAIL",
            "data": {"info": "this will fail"}
        }
        response = self.client.post('/asf/internal/send_ui_update', json=payload)
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data['error_code'], "ASF_SOCKETIO_EMIT_FAILED")
        self.assertIn("Simulated SocketIO emit error", data['details'])

    @patch.object(asf_main, 'ASF_UI_UPDATES_NAMESPACE', None)
    def test_send_ui_update_namespace_not_configured(self):
        payload = {
            "client_id": "test_client_ns_error",
            "event_name": "EVENT_NS_FAIL",
            "data": {"info": "namespace fail"}
        }
        self.namespace_global_patcher.stop()
        response = self.client.post('/asf/internal/send_ui_update', json=payload)
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data['error_code'], "ASF_CONFIG_ERROR_UI_NAMESPACE")
        self.namespace_global_patcher.start()


class TestASFSocketIOHandlers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        asf_main.app.testing = True
        # No client here, created per test or in setUp if all use same namespace

    def setUp(self):
        self.maxDiff = None
        self.mock_asf_config = {
            'ASF_UI_UPDATES_NAMESPACE': '/ui_updates_socket_test',
        }
        self.config_patcher = patch.dict(asf_main.asf_config, self.mock_asf_config)
        self.mock_config_instance = self.config_patcher.start()

        self.namespace_global_patcher = patch.object(asf_main, 'ASF_UI_UPDATES_NAMESPACE', self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])
        self.namespace_global_patcher.start()

        # Create a test client for Socket.IO
        self.client = asf_main.socketio.test_client(asf_main.app, namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])
        # Connect the client
        self.assertTrue(self.client.connect(namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE']))
        # Get the initial connection acknowledgement event
        received = self.client.get_received(namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['name'], asf_main.UI_EVENT_CONNECT_ACK)


    def tearDown(self):
        if self.client.is_connected(namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE']):
            self.client.disconnect(namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])
        self.config_patcher.stop()
        self.namespace_global_patcher.stop()

    @patch.object(asf_main.logger, 'info')
    def test_handle_ui_connect_logs_and_acks(self, mock_logger_info):
        # Connection is handled in setUp, including receiving the ACK
        self.assertTrue(self.client.is_connected(namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE']))

        self.assertTrue(any(f"Client {self.client.sid} connected to UI updates namespace" in call_args[0][0]
                            for call_args in mock_logger_info.call_args_list))

    @patch.object(asf_main.logger, 'info')
    def test_handle_ui_disconnect_logs(self, mock_logger_info):
        client_sid = self.client.sid
        self.client.disconnect(namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])
        self.assertFalse(self.client.is_connected(namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE']))

        self.assertTrue(any(f"Client {client_sid} disconnected from UI updates namespace" in call_args[0][0]
                            for call_args in mock_logger_info.call_args_list))

    @patch('aethercast.asf.main.join_room')
    @patch.object(asf_main.logger, 'info')
    def test_handle_subscribe_ui_updates_success(self, mock_logger_info, mock_join_room):
        room_name = "test_client_room_789"
        self.client.emit('subscribe_to_ui_updates', {"client_id": room_name}, namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])

        received = self.client.get_received(namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['name'], asf_main.UI_EVENT_SUBSCRIBED)
        self.assertEqual(received[0]['args'][0]['client_id'], room_name)

        mock_join_room.assert_called_once_with(room_name)
        self.assertTrue(any(f"Client {self.client.sid} (client_id: {room_name}) subscribed to UI updates in room '{room_name}'" in call_args[0][0]
                            for call_args in mock_logger_info.call_args_list))

    @patch('aethercast.asf.main.join_room')
    @patch.object(asf_main.logger, 'warning')
    def test_handle_subscribe_ui_updates_missing_client_id(self, mock_logger_warning, mock_join_room):
        self.client.emit('subscribe_to_ui_updates', {}, namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])

        received = self.client.get_received(namespace=self.mock_asf_config['ASF_UI_UPDATES_NAMESPACE'])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['name'], asf_main.UI_EVENT_ERROR)
        self.assertIn("client_id is required", received[0]['args'][0]['message'])

        mock_join_room.assert_not_called()
        self.assertTrue(any(f"Client {self.client.sid} attempted to subscribe to UI updates without a client_id." in call_args[0][0]
                            for call_args in mock_logger_warning.call_args_list))


if __name__ == '__main__':
    unittest.main()
