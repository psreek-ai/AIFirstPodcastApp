import sys
print("--- WCHA Test sys.path START ---")
for p_idx, p_val in enumerate(sys.path):
    print(f"sys.path[{p_idx}]: {p_val}")
print("--- WCHA Test sys.path END ---")

print("--- WCHA Test Current Working Directory ---")
import os
print(f"CWD: {os.getcwd()}")
print("--- WCHA Test CWD END ---")

print("--- WCHA Test Attempting direct import of python_json_logger ---")
try:
    import python_json_logger
    print(f"Successfully imported python_json_logger directly in test file. Version: {getattr(python_json_logger, '__version__', 'unknown')}")
except ImportError as e:
    print(f"Failed to import python_json_logger directly in test file: {e}")
    print("This suggests python_json_logger is not installed correctly in the Python environment being used by the test runner, or there's a path issue.")
except Exception as e_other:
    print(f"An unexpected error occurred during direct import of python_json_logger: {e_other}")
print("--- WCHA Test Direct import attempt END ---")

import unittest
from unittest.mock import patch, MagicMock, call
import os
import sys
import logging
import requests
import socket
from urllib.parse import urlparse
import uuid
from celery.result import AsyncResult
import json # Added this import

# Adjust path to import WCHA main module
current_dir = os.path.dirname(os.path.abspath(__file__))
wcha_dir = os.path.dirname(current_dir)
aethercast_dir = os.path.dirname(wcha_dir)
project_root_dir = os.path.dirname(aethercast_dir)

if wcha_dir not in sys.path:
    sys.path.insert(0, wcha_dir)
if aethercast_dir not in sys.path:
    sys.path.insert(0, aethercast_dir)
if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

from aethercast.wcha.main import is_url_safe
from aethercast.wcha import main as wcha_main

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TestIsUrlSafe(unittest.TestCase):
    @patch('socket.getaddrinfo')
    def test_valid_url_public_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 0))]
        safe, reason = is_url_safe("http://example.com")
        self.assertTrue(safe)
        self.assertEqual(reason, "URL is safe.")
        mock_getaddrinfo.assert_called_once_with("example.com", None)

    @patch('socket.getaddrinfo')
    def test_valid_url_public_ipv6(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:4860:4860::8888', 0, 0, 0))]
        safe, reason = is_url_safe("http://example.com")
        self.assertTrue(safe)
        self.assertEqual(reason, "URL is safe.")

    @patch('socket.getaddrinfo')
    def test_url_private_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.1', 0))]
        safe, reason = is_url_safe("http://private.local")
        self.assertFalse(safe)
        self.assertIn("is not a public IP (is private)", reason)

    @patch('socket.getaddrinfo')
    def test_url_loopback_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0))]
        safe, reason = is_url_safe("http://localhost")
        self.assertFalse(safe)
        self.assertIn("is not a public IP (is loopback)", reason)

    @patch('socket.getaddrinfo')
    def test_url_loopback_ipv6(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 0, 0, 0))]
        safe, reason = is_url_safe("http://localhost6")
        self.assertFalse(safe)
        self.assertIn("is not a public IP (is loopback)", reason)

    @patch('socket.getaddrinfo')
    def test_url_multiple_ips_one_private(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 0))
        ]
        safe, reason = is_url_safe("http://mixed.example.com")
        self.assertFalse(safe)
        self.assertIn("10.0.0.1", reason)
        self.assertIn("is not a public IP (is private)", reason)

    @patch('socket.getaddrinfo')
    def test_url_multiple_public_ips(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:4860:4860::8888', 0, 0, 0))
        ]
        safe, reason = is_url_safe("http://multi.example.com")
        self.assertTrue(safe)
        self.assertEqual(reason, "URL is safe.")

    @patch('socket.getaddrinfo')
    def test_url_non_resolvable_hostname(self, mock_getaddrinfo):
        mock_getaddrinfo.side_effect = socket.gaierror("DNS resolution failed")
        safe, reason = is_url_safe("http://nonexistentdomain12345.com")
        self.assertFalse(safe)
        self.assertIn("Could not resolve hostname", reason)

    def test_url_invalid_scheme_ftp(self):
        safe, reason = is_url_safe("ftp://example.com")
        self.assertFalse(safe)
        self.assertIn("Invalid URL scheme: 'ftp'", reason)

    def test_url_invalid_scheme_file(self):
        safe, reason = is_url_safe("file:///etc/passwd")
        self.assertFalse(safe)
        self.assertIn("Invalid URL scheme: 'file'", reason)

    def test_url_no_hostname(self):
        safe, reason = is_url_safe("http:///path")
        self.assertFalse(safe)
        self.assertIn("URL has no hostname.", reason)

    @patch('socket.getaddrinfo')
    def test_url_link_local_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.1.1', 0))]
        safe, reason = is_url_safe("http://linklocal.corp")
        self.assertFalse(safe)
        self.assertIn("is not a public IP (is link-local)", reason)

    @patch('socket.getaddrinfo')
    def test_url_unspecified_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('0.0.0.0', 0))]
        safe, reason = is_url_safe("http://any.host")
        self.assertFalse(safe)
        self.assertTrue("is unspecified" in reason and "is not a public IP" in reason)


class TestGetContentForTopic(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.mock_wcha_config_defaults = {
            "WCHA_SEARCH_MAX_RESULTS": 3,
            "WCHA_REQUEST_TIMEOUT": 10,
            "WCHA_USER_AGENT": "TestAgent/1.0",
            "WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION": 50,
            "USE_REAL_NEWS_API": False
        }
        self.config_patcher = patch.dict(wcha_main.wcha_config, self.mock_wcha_config_defaults, clear=True)
        self.mock_config = self.config_patcher.start()
        self.addCleanup(self.config_patcher.stop)

    @patch('aethercast.wcha.main.trafilatura.extract') # Still needed if harvest_from_url is called by a test not updated yet
    @patch('aethercast.wcha.main.requests.get') # Still needed if harvest_from_url is called by a test not updated yet
    @patch('aethercast.wcha.main.DDGS')
    def test_get_content_for_topic_ddgs_async_success(self, mock_ddgs_constructor, mock_requests_get, mock_trafilatura_extract):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = [{'href': 'http://example.com/page1'}, {'href': 'http://example.com/page2'}]
        mock_ddgs_context_manager = MagicMock()
        mock_ddgs_context_manager.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_constructor.return_value = mock_ddgs_context_manager

        mock_harvest_task_delay = MagicMock()
        mock_aggregate_task_delay = MagicMock()
        # Simulate different IDs for dispatched harvest tasks
        mock_harvest_task_delay.side_effect = [MagicMock(id="harvest_task_id_1"), MagicMock(id="harvest_task_id_2")]
        mock_aggregate_task_delay.return_value = MagicMock(id="aggregate_task_id_123")

        with patch('aethercast.wcha.main.harvest_url_content_task.delay', mock_harvest_task_delay), \
             patch('aethercast.wcha.main.aggregate_ddgs_harvest_results_task.delay', mock_aggregate_task_delay), \
             patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):

            result = wcha_main.get_content_for_topic("test topic async ddgs")

        self.assertEqual(result["status"], "pending_ddgs_aggregation")
        self.assertEqual(result["task_id"], "aggregate_task_id_123")
        self.assertIn("DDGS content harvesting and aggregation initiated", result["message"])

        mock_ddgs_instance.text.assert_called_once_with(keywords="test topic async ddgs", region='wt-wt', safesearch='moderate', max_results=3)
        mock_requests_get.assert_not_called()
        mock_trafilatura_extract.assert_not_called()

        self.assertEqual(mock_harvest_task_delay.call_count, 2)
        mock_harvest_task_delay.assert_any_call(request_id=unittest.mock.ANY, url_to_harvest='http://example.com/page1', min_length=50)
        mock_harvest_task_delay.assert_any_call(request_id=unittest.mock.ANY, url_to_harvest='http://example.com/page2', min_length=50)

        mock_aggregate_task_delay.assert_called_once()
        args, kwargs = mock_aggregate_task_delay.call_args
        self.assertIn("harvest_task_ids", kwargs)
        self.assertListEqual(kwargs["harvest_task_ids"], ["harvest_task_id_1", "harvest_task_id_2"])
        self.assertEqual(kwargs["original_topic"], "test topic async ddgs")

    @patch('aethercast.wcha.main.DDGS')
    def test_get_content_for_topic_no_search_results(self, mock_ddgs_constructor):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = []
        mock_ddgs_context_manager = MagicMock()
        mock_ddgs_context_manager.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_constructor.return_value = mock_ddgs_context_manager

        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            result = wcha_main.get_content_for_topic("obscure topic")

        # This path should now return "failure_no_results" before trying to dispatch Celery tasks
        self.assertEqual(result["status"], "failure_no_results")
        self.assertIsNone(result["content"])
        self.assertEqual(len(result["source_urls"]), 0)
        self.assertIn(wcha_main.ERROR_WCHA_NO_SEARCH_RESULTS, result["message"])
        self.assertIsNone(result["task_id"]) # No Celery task should be dispatched

    @patch('aethercast.wcha.main.DDGS')
    def test_get_content_for_topic_search_exception(self, mock_ddgs_constructor):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.side_effect = Exception("DDG API Error")
        mock_ddgs_context_manager = MagicMock()
        mock_ddgs_context_manager.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_constructor.return_value = mock_ddgs_context_manager

        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            result = wcha_main.get_content_for_topic("search error topic")
        self.assertEqual(result["status"], "failure_search")
        self.assertIn(wcha_main.ERROR_WCHA_SEARCH_FAILED, result["message"])
        self.assertIsNone(result["task_id"])

    @patch('aethercast.wcha.main.harvest_url_content_task.delay')
    @patch('aethercast.wcha.main.aggregate_ddgs_harvest_results_task.delay')
    @patch('aethercast.wcha.main.DDGS')
    def test_get_content_for_topic_ddgs_no_safe_urls(self, mock_ddgs_constructor, mock_aggregate_task_delay, mock_harvest_task_delay):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = [{'href': 'http://unsafe.example.com/page1'}]
        mock_ddgs_context_manager = MagicMock()
        mock_ddgs_context_manager.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_constructor.return_value = mock_ddgs_context_manager

        with patch('aethercast.wcha.main.is_url_safe', return_value=(False, "URL is not safe.")):
            result = wcha_main.get_content_for_topic("no safe urls topic")

        self.assertEqual(result["status"], "failure_no_safe_urls")
        self.assertIn("No safe URLs found", result["message"])
        mock_harvest_task_delay.assert_not_called()
        mock_aggregate_task_delay.assert_not_called()


    @patch('aethercast.wcha.main.fetch_news_articles_task.delay')
    def test_get_content_for_topic_news_api_path(self, mock_fetch_news_delay):
        mock_task_instance = MagicMock()
        mock_task_instance.id = "news_task_id_789"
        mock_fetch_news_delay.return_value = mock_task_instance

        with patch.dict(wcha_main.wcha_config, {"USE_REAL_NEWS_API": True, "TDA_NEWS_API_KEY": "fakekey"}):
            result = wcha_main.get_content_for_topic("news topic")

        self.assertEqual(result["status"], "pending_news_api")
        self.assertEqual(result["task_id"], "news_task_id_789")
        mock_fetch_news_delay.assert_called_once()


    @patch('aethercast.wcha.main.IMPORTS_SUCCESSFUL', False)
    @patch('aethercast.wcha.main.MISSING_IMPORT_ERROR', "Simulated missing library")
    def test_get_content_for_topic_imports_not_successful(self):
        result = wcha_main.get_content_for_topic("any topic")
        self.assertEqual(result["status"], "failure_dependency")
        self.assertIn(wcha_main.ERROR_WCHA_LIB_MISSING, result["message"])


class TestWCHAFlaskEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if wcha_main.app:
            wcha_main.app.config['TESTING'] = True
            cls.client = wcha_main.app.test_client()
        else:
            cls.client = None

    def setUp(self):
        if not self.client:
            self.skipTest("Flask app not initialized in wcha_main. Skipping endpoint tests.")
        self.mock_wcha_config_for_endpoint = {
             "WCHA_SEARCH_MAX_RESULTS": 3,
             "WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION": 50,
             "USE_REAL_NEWS_API": False
        }
        self.config_patcher = patch.dict(wcha_main.wcha_config, self.mock_wcha_config_for_endpoint, clear=True)
        self.mock_config = self.config_patcher.start()
        self.addCleanup(self.config_patcher.stop) # Ensure patch is stopped

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_use_search_ddgs_async_pending(self, mock_get_content_for_topic):
        mock_pending_agg_data = {
            "status": "pending_ddgs_aggregation",
            "task_id": "agg_task_for_cpoa_poll_123",
            "message": "DDGS content harvesting and aggregation initiated."
        }
        mock_get_content_for_topic.return_value = mock_pending_agg_data

        response = self.client.post('/harvest', json={"topic": "test ddgs async topic", "use_search": True})

        self.assertEqual(response.status_code, 202)
        json_response = response.get_json()
        self.assertEqual(json_response["task_id"], "agg_task_for_cpoa_poll_123")
        self.assertIn("/v1/tasks/agg_task_for_cpoa_poll_123", json_response["status_url"])
        self.assertEqual(json_response["message"], "DDGS content harvesting and aggregation accepted.")
        mock_get_content_for_topic.assert_called_once_with('test ddgs async topic', task_id=unittest.mock.ANY, workflow_id='N/A', max_results_override=None)

    def test_harvest_endpoint_missing_parameters(self):
        response = self.client.post('/harvest', json={})
        self.assertEqual(response.status_code, 400)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "WCHA_MISSING_PARAMETERS")

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_use_search_failure_from_logic(self, mock_get_content_for_topic):
        mock_failure_data = {"status": "failure_no_results", "message": wcha_main.ERROR_WCHA_NO_SEARCH_RESULTS, "content": None, "source_urls": [], "task_id": None}
        mock_get_content_for_topic.return_value = mock_failure_data
        response = self.client.post('/harvest', json={"topic": "test failure topic", "use_search": True})
        self.assertEqual(response.status_code, 404) # As per new status code mapping in endpoint

    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_internal_error_in_logic(self, mock_get_content_for_topic):
        mock_get_content_for_topic.side_effect = Exception("Core logic unexpected error")
        response = self.client.post('/harvest', json={"topic": "test internal error", "use_search": True})
        self.assertEqual(response.status_code, 500)
        json_response = response.get_json()
        self.assertEqual(json_response.get("error_code"), "WCHA_INTERNAL_SERVER_ERROR")


    @patch('aethercast.wcha.main.get_content_for_topic')
    def test_harvest_endpoint_with_max_results_override(self, mock_get_content_for_topic):
        # Assuming the async path is taken, it should still pass max_results
        mock_get_content_for_topic.return_value = {"status": "pending_ddgs_aggregation", "task_id": "some_task"}
        self.client.post('/harvest', json={"topic": "test max results", "use_search": True, "max_results": "3"})
        self.assertEqual(mock_get_content_for_topic.call_count, 1)
        expected_call_args = call("test max results", task_id=unittest.mock.ANY, workflow_id='N/A', max_results_override=3)
        self.assertEqual(mock_get_content_for_topic.call_args, expected_call_args)


    @patch('aethercast.wcha.main.harvest_url_content_task.delay')
    def test_harvest_endpoint_direct_url_async_dispatch(self, mock_delay):
        mock_task_instance = MagicMock()
        mock_task_instance.id = "test_celery_task_id_123"
        mock_delay.return_value = mock_task_instance

        with patch('aethercast.wcha.main.is_url_safe', return_value=(True, "URL is safe.")):
            response = self.client.post('/harvest', json={"url": "http://example.com/direct_async"})

        self.assertEqual(response.status_code, 202)
        json_response = response.get_json()
        self.assertEqual(json_response["task_id"], "test_celery_task_id_123")
        self.assertIn("/v1/tasks/test_celery_task_id_123", json_response["status_url"])
        mock_delay.assert_called_once()
        args, kwargs_call = mock_delay.call_args # Renamed to avoid conflict
        self.assertIn('url_to_harvest', kwargs_call)
        self.assertEqual(kwargs_call['url_to_harvest'], "http://example.com/direct_async")
        self.assertIn('request_id', kwargs_call)


class TestAggregateDdgsHarvestResultsTask(unittest.TestCase):
    def setUp(self):
        wcha_main.celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
        self.mock_db_conn = MagicMock()
        self.get_db_patcher = patch('aethercast.wcha.main.get_db_connection', return_value=self.mock_db_conn)
        self.mock_get_db_conn = self.get_db_patcher.start()
        self.addCleanup(self.get_db_patcher.stop)

        self.check_idempotency_patcher = patch('aethercast.wcha.main.check_idempotency')
        self.mock_check_idempotency = self.check_idempotency_patcher.start()
        self.addCleanup(self.check_idempotency_patcher.stop)

        self.acquire_lock_patcher = patch('aethercast.wcha.main.acquire_idempotency_lock')
        self.mock_acquire_lock = self.acquire_lock_patcher.start()
        self.addCleanup(self.acquire_lock_patcher.stop)

        self.update_record_patcher = patch('aethercast.wcha.main.update_idempotency_record')
        self.mock_update_record = self.update_record_patcher.start()
        self.addCleanup(self.update_record_patcher.stop)

        self.wcha_config_patcher = patch.dict(wcha_main.wcha_config, {'WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION': 10})
        self.wcha_config_patcher.start()
        self.addCleanup(self.wcha_config_patcher.stop)


    @patch('aethercast.wcha.main.AsyncResult')
    def test_aggregation_all_sub_tasks_success(self, MockAsyncResult):
        self.mock_check_idempotency.return_value = None
        self.mock_acquire_lock.return_value = True

        harvest_task_ids = ["task_1", "task_2"]
        original_topic = "Test Topic Aggregation"

        mock_result_1 = MagicMock()
        mock_result_1.successful.return_value = True
        mock_result_1.get.return_value = {"url": "url1", "content": "Content from URL1.", "error_type": None}

        mock_result_2 = MagicMock()
        mock_result_2.successful.return_value = True
        mock_result_2.get.return_value = {"url": "url2", "content": "More content from URL2.", "error_type": None}

        MockAsyncResult.side_effect = [mock_result_1, mock_result_2]

        agg_task_id = f"agg_req_{uuid.uuid4().hex[:6]}"

        mock_self = MagicMock()
        mock_self.request.id = f"celery_agg_task_{uuid.uuid4().hex[:6]}"

        result = wcha_main.aggregate_ddgs_harvest_results_task.__wrapped__(
            mock_self,
            request_id=agg_task_id,
            harvest_task_ids=harvest_task_ids,
            original_topic=original_topic
        )

        self.assertEqual(result["status"], "success")
        self.assertIn("Content from URL1", result["content"])
        self.assertIn("More content from URL2", result["content"])
        self.assertListEqual(result["source_urls"], ["url1", "url2"])
        self.assertEqual(len(result["failed_harvest_details"]), 0)
        self.mock_update_record.assert_called_once_with(
            self.mock_db_conn, agg_task_id, "wcha_aggregate_ddgs_harvest_task", 'completed', result_payload=result
        )

    @patch('aethercast.wcha.main.AsyncResult')
    def test_aggregation_partial_success_one_sub_task_fails(self, MockAsyncResult):
        self.mock_check_idempotency.return_value = None
        self.mock_acquire_lock.return_value = True

        harvest_task_ids = ["task_ok", "task_fail"]

        mock_result_ok = MagicMock()
        mock_result_ok.successful.return_value = True
        mock_result_ok.get.return_value = {"url": "url_ok", "content": "Good content here.", "error_type": None}

        mock_result_fail = MagicMock()
        mock_result_fail.successful.return_value = False
        mock_result_fail.status = "FAILURE"
        mock_result_fail.info = "Simulated sub-task failure"

        MockAsyncResult.side_effect = [mock_result_ok, mock_result_fail]

        agg_task_id = "agg_partial_fail"
        mock_self = MagicMock(); mock_self.request.id = "celery_agg_partial"
        result = wcha_main.aggregate_ddgs_harvest_results_task.__wrapped__(
            mock_self, agg_task_id, harvest_task_ids, "Partial Fail Topic"
        )

        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["content"], "Source: url_ok\nGood content here.")
        self.assertListEqual(result["source_urls"], ["url_ok"])
        self.assertEqual(len(result["failed_harvest_details"]), 1)
        self.assertEqual(result["failed_harvest_details"][0]["error"], "sub_task_failed")
        self.assertIn("Simulated sub-task failure", result["failed_harvest_details"][0]["message"])

    @patch('aethercast.wcha.main.AsyncResult')
    def test_aggregation_all_sub_tasks_fail(self, MockAsyncResult):
        self.mock_check_idempotency.return_value = None
        self.mock_acquire_lock.return_value = True
        harvest_task_ids = ["task_f1", "task_f2"]

        mock_result_f1 = MagicMock()
        mock_result_f1.successful.return_value = True
        mock_result_f1.get.return_value = {"url": "url_f1", "content": None, "error_type": "fetch_error", "error_message": "404 Not Found"}

        mock_result_f2 = MagicMock()
        mock_result_f2.successful.return_value = False
        mock_result_f2.status = "FAILURE"
        mock_result_f2.info = "Sub-task 2 crashed"

        MockAsyncResult.side_effect = [mock_result_f1, mock_result_f2]

        agg_task_id = "agg_all_fail"
        mock_self = MagicMock(); mock_self.request.id = "celery_agg_all_fail"
        result = wcha_main.aggregate_ddgs_harvest_results_task.__wrapped__(
            mock_self, agg_task_id, harvest_task_ids, "All Fail Topic"
        )

        self.assertEqual(result["status"], "failure")
        self.assertIsNone(result["content"])
        self.assertEqual(len(result["source_urls"]), 0)
        self.assertEqual(len(result["failed_harvest_details"]), 2)

    @patch('aethercast.wcha.main.AsyncResult')
    def test_aggregation_sub_task_timeout(self, MockAsyncResult):
        self.mock_check_idempotency.return_value = None
        self.mock_acquire_lock.return_value = True
        harvest_task_ids = ["task_timeout"]

        mock_result_timeout = MagicMock()
        mock_result_timeout.get.side_effect = TimeoutError("Sub-task timed out")
        MockAsyncResult.return_value = mock_result_timeout

        agg_task_id = "agg_timeout"
        mock_self = MagicMock(); mock_self.request.id = "celery_agg_timeout"
        result = wcha_main.aggregate_ddgs_harvest_results_task.__wrapped__(
            mock_self, agg_task_id, harvest_task_ids, "Timeout Topic"
        )

        self.assertEqual(result["status"], "failure")
        self.assertEqual(len(result["failed_harvest_details"]), 1)
        self.assertEqual(result["failed_harvest_details"][0]["error"], "sub_task_timeout")

    def test_aggregation_idempotency_completed(self):
        agg_task_id = "agg_idem_completed"
        stored_result = {"status": "success", "content": "Already done", "source_urls": ["url_done"], "message": "From cache"}
        self.mock_check_idempotency.return_value = {'status': 'completed', 'result': stored_result}

        mock_self = MagicMock(); mock_self.request.id = "celery_agg_idem_comp"
        result = wcha_main.aggregate_ddgs_harvest_results_task.__wrapped__(
            mock_self, agg_task_id, [], "Idempotent Completed Topic"
        )

        self.assertEqual(result, stored_result)
        self.mock_acquire_lock.assert_not_called()

    def test_aggregation_idempotency_conflict(self):
        agg_task_id = "agg_idem_conflict"
        self.mock_check_idempotency.return_value = {'status': 'conflict', 'message': 'Task already processing'}

        mock_self = MagicMock(); mock_self.request.id = "celery_agg_idem_conf"
        result = wcha_main.aggregate_ddgs_harvest_results_task.__wrapped__(
            mock_self, agg_task_id, [], "Idempotent Conflict Topic"
        )

        self.assertEqual(result["status"], "conflict")
        self.mock_acquire_lock.assert_not_called()

    @patch('aethercast.wcha.main.AsyncResult')
    def test_aggregation_content_too_short(self, MockAsyncResult):
        self.mock_check_idempotency.return_value = None
        self.mock_acquire_lock.return_value = True
        harvest_task_ids = ["task_short"]

        mock_result_short = MagicMock()
        mock_result_short.successful.return_value = True
        mock_result_short.get.return_value = {"url": "url_short", "content": "Too short.", "error_type": None} # Length is 10

        MockAsyncResult.return_value = mock_result_short

        with patch.dict(wcha_main.wcha_config, {'WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION': 15}):
            agg_task_id = "agg_content_short"
            mock_self = MagicMock(); mock_self.request.id = "celery_agg_short"
            result = wcha_main.aggregate_ddgs_harvest_results_task.__wrapped__(
                mock_self, agg_task_id, harvest_task_ids, "Content Too Short Topic"
            )

        self.assertEqual(result["status"], "failure")
        self.assertIsNone(result["content"])
        self.assertEqual(len(result["source_urls"]), 0)
        self.assertEqual(len(result["failed_harvest_details"]), 1)
        self.assertEqual(result["failed_harvest_details"][0]["error"], "content_too_short")

if __name__ == '__main__':
    unittest.main(verbosity=2)

[end of aethercast/wcha/tests/test_main.py]
