import uuid # Retain for generate_harvest_id
import logging
import json # Used for idempotency record payloads
import os # Added
from dotenv import load_dotenv # Added
from typing import Optional # Added for type hinting
from celery import Celery
from celery.result import AsyncResult
import socket # New import
import ipaddress # New import
from urllib.parse import urlparse # New import
from python_json_logger import jsonlogger # Added for JSON logging
import psycopg2
from psycopg2 import pool as psycopg2_pool
import time # For stale lock check
from datetime import datetime, timezone
from aethercast.common.celery import create_celery_app
from aethercast.common.db import get_db_connection, init_db_connection_pool
from aethercast.common.idempotency import check_idempotency_key, store_idempotency_record
import flask

# --- Load Environment Variables ---
load_dotenv() # Added

# --- Celery Configuration ---
celery_app = create_celery_app('wcha_tasks')

# --- Flask App Setup ---
app = flask.Flask(__name__)

# --- Logging Configuration ---
# ServiceNameFilter class
class ServiceNameFilter(logging.Filter):
    def filter(self, record):
        record.service_name = "wcha-service"
        # Ensure workflow_id and task_id are present, defaulting to "N/A"
        if not hasattr(record, 'workflow_id'):
            record.workflow_id = "N/A"
        if not hasattr(record, 'task_id'):
            record.task_id = "N/A"
        return True

logger = logging.getLogger(__name__)
logger.handlers.clear() # Clear existing handlers
stream_handler = logging.StreamHandler()
service_name_filter = ServiceNameFilter()
stream_handler.addFilter(service_name_filter)
# Format string aligned with Logging_Guide.md
formatter = jsonlogger.JsonFormatter(
    '%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s %(workflow_id)s %(task_id)s'
)
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)
logger.setLevel(logging.INFO)
logger.propagate = False # Disable propagation
app.logger = logger

# --- WCHA Configuration ---
WCHA_SEARCH_MAX_RESULTS = int(os.getenv('WCHA_SEARCH_MAX_RESULTS', '3'))
WCHA_REQUEST_TIMEOUT = int(os.getenv('WCHA_REQUEST_TIMEOUT', '10'))
WCHA_USER_AGENT = os.getenv('WCHA_USER_AGENT', 'AethercastContentHarvester/0.2')
USE_REAL_NEWS_API = os.getenv('USE_REAL_NEWS_API', 'False').lower() == 'true'
TDA_NEWS_API_KEY = os.getenv('TDA_NEWS_API_KEY')
TDA_NEWS_API_BASE_URL = os.getenv('TDA_NEWS_API_BASE_URL', 'https://newsapi.org/v2/')
TDA_NEWS_API_ENDPOINT = os.getenv('TDA_NEWS_API_ENDPOINT', 'everything')
TDA_NEWS_DEFAULT_KEYWORDS = os.getenv('TDA_NEWS_DEFAULT_KEYWORDS', 'technology,AI').split(',')
TDA_NEWS_DEFAULT_LANGUAGE = os.getenv('TDA_NEWS_DEFAULT_LANGUAGE', 'en')
TDA_NEWS_PAGE_SIZE = int(os.getenv('TDA_NEWS_PAGE_SIZE', '20'))
WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION = int(os.getenv('WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION', '150'))
IDEMPOTENCY_LOCK_TIMEOUT_SECONDS = int(os.getenv('IDEMPOTENCY_LOCK_TIMEOUT_SECONDS', 1800))
IDEMPOTENCY_STATUS_PROCESSING = os.getenv("IDEMPOTENCY_STATUS_PROCESSING", "processing")
IDEMPOTENCY_STATUS_COMPLETED = os.getenv("IDEMPOTENCY_STATUS_COMPLETED", "completed")
IDEMPOTENCY_STATUS_FAILED = os.getenv("IDEMPOTENCY_STATUS_FAILED", "failed")
SERVICE_NAME_FOR_IDEMPOTENCY = os.getenv("SERVICE_NAME_FOR_IDEMPOTENCY", "WCHA")


ERROR_PREFIX_HARVEST_FAILED_FETCH = "Error fetching URL"
ERROR_PREFIX_HARVEST_TRAFILATURA_FAILED = "WCHA: Trafilatura failed to extract content from URL"
ERROR_PREFIX_HARVEST_TRAFILATURA_NO_CONTENT = "WCHA: Trafilatura extracted no content from URL"
ERROR_PREFIX_HARVEST_LIB_MISSING_TRAFILATURA = "WCHA Error: Trafilatura library not installed"
ERROR_PREFIX_HARVEST_LIB_MISSING_REQUESTS = "Cannot 'harvest_from_url' because requests library is missing"
ERROR_WCHA_LIB_MISSING = "WCHA: Cannot 'get_content_for_topic' due to missing libraries:"
ERROR_WCHA_SEARCH_FAILED = "Error during web search for topic"
ERROR_WCHA_NO_SEARCH_RESULTS = "WCHA: No search results found for topic"
ERROR_WCHA_HARVEST_ALL_FAILED = "WCHA: Failed to harvest usable content from any of the"
WCHA_ERROR_TYPE_LIB_MISSING = "library_missing"
WCHA_ERROR_TYPE_FETCH = "fetch_error"
WCHA_ERROR_TYPE_EXTRACTION = "extraction_error"
WCHA_ERROR_TYPE_NO_CONTENT = "no_content_extracted"
WCHA_ERROR_TYPE_CONTENT_TOO_SHORT = "content_too_short"
WCHA_ERROR_TYPE_UNSAFE_URL = "unsafe_url"
WCHA_ERROR_TYPE_SSRF_BLOCKED = "ssrf_blocked"
WCHA_ERROR_TYPE_UNKNOWN = "unknown_harvest_error"
ENDPOINT_ERROR_INVALID_PAYLOAD = "INVALID_JSON_PAYLOAD_WCHA"
ENDPOINT_ERROR_MISSING_FIELDS = "MISSING_REQUIRED_FIELDS_WCHA"
ENDPOINT_ERROR_INTERNAL_SERVER = "INTERNAL_SERVER_ERROR_WCHA"

_IMPORTS_SUCCESSFUL_REQUESTS = True
_MISSING_IMPORT_ERROR_REQUESTS = None
try:
    import requests
except ImportError as e:
    _IMPORTS_SUCCESSFUL_REQUESTS = False
    _MISSING_IMPORT_ERROR_REQUESTS = str(e)
    if 'requests' not in globals():
        def requests_get_placeholder(*args, **kwargs): raise ImportError(f"requests library is not installed. Original error: {_MISSING_IMPORT_ERROR_REQUESTS}")
        class MockRequestsExceptions: RequestException = type('RequestException', (Exception,), {}); ConnectionError = type('ConnectionError', (RequestException,), {}); Timeout = type('Timeout', (RequestException,), {}); HTTPError = type('HTTPError', (RequestException,), {})
        class MockRequests: get = requests_get_placeholder; exceptions = MockRequestsExceptions()
        requests = MockRequests()

_IMPORTS_SUCCESSFUL_DDG = True
_MISSING_IMPORT_ERROR_DDG = None
try:
    from duckduckgo_search import DDGS
except ImportError as e:
    _IMPORTS_SUCCESSFUL_DDG = False
    _MISSING_IMPORT_ERROR_DDG = str(e)
    if 'DDGS' not in globals():
        def DDGS_placeholder(*args, **kwargs):
            class DummyDDGS:
                def __enter__(self): return self
                def __exit__(self, exc_type, exc_val, exc_tb): pass
                def text(self, *args, **kwargs): raise ImportError(f"duckduckgo_search library is not installed. Original error: {_MISSING_IMPORT_ERROR_DDG}")
            return DummyDDGS()
        DDGS = DDGS_placeholder

_IMPORTS_SUCCESSFUL_TRAFILATURA = True
_MISSING_IMPORT_ERROR_TRAFILATURA = None
try:
    import trafilatura
except ImportError as e:
    _IMPORTS_SUCCESSFUL_TRAFILATURA = False
    _MISSING_IMPORT_ERROR_TRAFILATURA = str(e)
    if 'trafilatura' not in globals():
        def trafilatura_placeholder_extract(*args, **kwargs): raise ImportError(f"trafilatura library is not installed. Original error: {_MISSING_IMPORT_ERROR_TRAFILATURA}")
        trafilatura = type('trafilatura', (object,), {'extract': trafilatura_placeholder_extract})()

IMPORTS_SUCCESSFUL_CORE = _IMPORTS_SUCCESSFUL_REQUESTS and _IMPORTS_SUCCESSFUL_DDG
IMPORTS_SUCCESSFUL_ADVANCED_EXTRACTION = _IMPORTS_SUCCESSFUL_TRAFILATURA
IMPORTS_SUCCESSFUL = IMPORTS_SUCCESSFUL_CORE and IMPORTS_SUCCESSFUL_ADVANCED_EXTRACTION

MISSING_IMPORT_ERROR = ""
if not IMPORTS_SUCCESSFUL:
    missing_libs_list = []
    if not _IMPORTS_SUCCESSFUL_REQUESTS: missing_libs_list.append(f"requests ({_MISSING_IMPORT_ERROR_REQUESTS})")
    if not _IMPORTS_SUCCESSFUL_DDG: missing_libs_list.append(f"duckduckgo_search ({_MISSING_IMPORT_ERROR_DDG})")
    if not _IMPORTS_SUCCESSFUL_TRAFILATURA: missing_libs_list.append(f"trafilatura ({_MISSING_IMPORT_ERROR_TRAFILATURA})")
    MISSING_IMPORT_ERROR = f"Missing libraries: {'; '.join(missing_libs_list)}."

SIMULATED_WEB_CONTENT = {
    "ai in healthcare": """AI is transforming healthcare by improving diagnostic accuracy,
personalizing treatment plans, and accelerating drug discovery.
Machine learning algorithms analyze medical images, detect anomalies,
and predict patient outcomes with increasing precision.""",
    "space exploration": """Recent advancements in space exploration include new missions
to Mars, the development of reusable rocket technology, and plans for
lunar bases. The search for extraterrestrial life and the study of
distant galaxies continue to drive innovation.""",
    "climate change": """Climate change remains a critical global challenge. Rising
temperatures, extreme weather events, and sea-level rise are impacting
ecosystems and communities worldwide. Efforts to transition to
renewable energy sources and reduce greenhouse gas emissions are crucial."""
}

def generate_harvest_id() -> str:
    return f"harvest_{uuid.uuid4().hex[:10]}"

def harvest_content(topic: str, task_id: Optional[str] = None, workflow_id: Optional[str] = None) -> str:
    log_extra = {'task_id': task_id or "N/A", 'workflow_id': workflow_id or "N/A"}
    logger.info(f"[WCHA_LOGIC_MOCK] harvest_content (mock data) called with topic: '{topic}'", extra=log_extra)
    normalized_topic = topic.lower().strip() if topic else ""
    if normalized_topic in SIMULATED_WEB_CONTENT:
        content = SIMULATED_WEB_CONTENT[normalized_topic]
        logger.info(f"[WCHA_LOGIC_MOCK] Found mock content for topic: '{topic}'", extra=log_extra)
        return content
    else:
        logger.warning(f"[WCHA_LOGIC_MOCK] No pre-defined mock content found for topic: '{topic}'.", extra=log_extra)
        return f"No pre-defined content found for topic: {topic}"

def is_url_safe(url_string: str, task_id: Optional[str] = None, workflow_id: Optional[str] = None) -> tuple[bool, str]:
    log_extra = {'task_id': task_id or "N/A", 'workflow_id': workflow_id or "N/A"}
    logger.debug(f"[WCHA_URL_VALIDATION] Validating URL: {url_string}", extra=log_extra)
    try:
        parsed_url = urlparse(url_string)
        if parsed_url.scheme not in ('http', 'https'):
            reason = f"Invalid URL scheme: '{parsed_url.scheme}'. Only 'http' or 'https' allowed."
            logger.warning(f"[WCHA_URL_VALIDATION] {reason}", extra=log_extra)
            return False, reason
        hostname = parsed_url.hostname
        if not hostname:
            reason = "URL has no hostname."
            logger.warning(f"[WCHA_URL_VALIDATION] {reason}", extra=log_extra)
            return False, reason
        try:
            addr_info_list = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            reason = f"Could not resolve hostname: '{hostname}'."
            logger.warning(f"[WCHA_URL_VALIDATION] {reason}", extra=log_extra)
            return False, reason
        if not addr_info_list:
            reason = f"No address information found for hostname: '{hostname}'."
            logger.warning(f"[WCHA_URL_VALIDATION] {reason}", extra=log_extra)
            return False, reason
        all_ips_safe = True
        unsafe_ip_details = ""
        for family, socktype, proto, canonname, sockaddr in addr_info_list:
            ip_str = sockaddr[0]
            try:
                ip_addr = ipaddress.ip_address(ip_str)
                logger.debug(f"[WCHA_URL_VALIDATION] URL '{url_string}' (hostname: '{hostname}') resolved to IP: {ip_str} (Family: {family})", extra=log_extra)
                if not ip_addr.is_global:
                    check_details = []
                    if ip_addr.is_loopback: check_details.append("is loopback")
                    if ip_addr.is_private: check_details.append("is private")
                    if ip_addr.is_link_local: check_details.append("is link-local")
                    if ip_addr.is_multicast: check_details.append("is multicast")
                    if ip_addr.is_unspecified: check_details.append("is unspecified")
                    unsafe_ip_details = f"Resolved IP address '{ip_str}' for hostname '{hostname}' is not a public IP ({', '.join(check_details)})."
                    all_ips_safe = False
                    break
            except ValueError:
                unsafe_ip_details = f"Invalid IP address format received from getaddrinfo: '{ip_str}'."
                all_ips_safe = False
                break
        if not all_ips_safe:
            logger.warning(f"[WCHA_URL_VALIDATION] {unsafe_ip_details}", extra=log_extra)
            return False, unsafe_ip_details
        logger.info(f"[WCHA_URL_VALIDATION] URL '{url_string}' (all resolved IPs are public) is deemed safe.", extra=log_extra)
        return True, "URL is safe."
    except ValueError as ve:
        reason = f"URL parsing error: {ve}"
        logger.warning(f"[WCHA_URL_VALIDATION] {reason}", extra=log_extra)
        return False, reason
    except Exception as e:
        reason = f"Unexpected error during URL validation: {e}"
        logger.error(f"[WCHA_URL_VALIDATION] {reason}", exc_info=True, extra=log_extra)
        return False, reason

@celery_app.task(bind=True, name='fetch_news_articles_task')
def fetch_news_articles_task(self, request_id: str, topic: str, language: Optional[str] = None, max_results: Optional[int] = None):
    log_extra = {'task_id': request_id, 'workflow_id': 'N/A'}
    idempotency_key = request_id
    task_name = self.name
    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id}): Starting task '{task_name}' for topic '{topic}'.", extra=log_extra)
    try:
        with get_db_connection(service_name='wcha') as db_conn:
            db_conn.autocommit = False

            existing_record = check_idempotency_key(db_conn, idempotency_key, task_name)

            if existing_record:
                status = existing_record['status']
                locked_at = existing_record['locked_at']
                if status == IDEMPOTENCY_STATUS_COMPLETED:
                    logger.info(f"Idempotency: Found COMPLETED record for key '{idempotency_key}'. Returning stored result.", extra=log_extra)
                    db_conn.rollback()
                    return existing_record['result_payload']
                elif status == IDEMPOTENCY_STATUS_PROCESSING:
                    lock_timeout_seconds = IDEMPOTENCY_LOCK_TIMEOUT_SECONDS
                    if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < lock_timeout_seconds:
                        logger.warning(f"Idempotency: Key '{idempotency_key}' is already PROCESSING. Returning conflict status.", extra=log_extra)
                        db_conn.rollback()
                        return {"status": "PROCESSING_CONFLICT", "message": "Task with this idempotency key is already processing.", "idempotency_key": idempotency_key}
                    else: # Lock expired or was null
                        logger.warning(f"Idempotency: Key '{idempotency_key}' was 'processing' but lock timed out/null. Re-processing.", extra=log_extra)
                        store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_PROCESSING, workflow_id=log_extra['workflow_id'], is_new_key=False)
                elif status == IDEMPOTENCY_STATUS_FAILED:
                     logger.info(f"Idempotency: Key '{idempotency_key}' previously FAILED. Retrying.", extra=log_extra)
                     store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_PROCESSING, workflow_id=log_extra['workflow_id'], is_new_key=False)
            else: # No existing record for this service
                store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_PROCESSING, workflow_id=log_extra['workflow_id'], is_new_key=True)

            db_conn.commit()

            if not USE_REAL_NEWS_API:
                logger.info(f"Celery Task {self.request.id}: USE_REAL_NEWS_API is false. Returning mock success.", extra=log_extra)
                result = {"status": "success_mock", "articles": [], "message": "News API is not enabled; mock response."}
                store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_COMPLETED, result_payload=result, workflow_id=log_extra['workflow_id'], is_new_key=False)
                db_conn.commit()
                return result
            if not TDA_NEWS_API_KEY:
                logger.error(f"Celery Task {self.request.id}: TDA_NEWS_API_KEY not configured.", extra=log_extra)
                error_payload = {"error_type": "ConfigurationError", "message": "TDA_NEWS_API_KEY not configured."}
                store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_FAILED, error_payload=error_payload, workflow_id=log_extra['workflow_id'], is_new_key=False)
                db_conn.commit()
                raise ValueError("NewsAPI key not configured.")
            base_url = TDA_NEWS_API_BASE_URL
            endpoint = TDA_NEWS_API_ENDPOINT
            api_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
            params = {}
            query_keywords_list = [kw.strip() for kw in topic.split(',')] if topic else TDA_NEWS_DEFAULT_KEYWORDS
            if query_keywords_list: params["q"] = " OR ".join(query_keywords_list)
            current_language = language if language else TDA_NEWS_DEFAULT_LANGUAGE
            if current_language: params["language"] = current_language
            params["pageSize"] = max_results if max_results else TDA_NEWS_PAGE_SIZE
            headers = {"X-Api-Key": TDA_NEWS_API_KEY, "User-Agent": WCHA_USER_AGENT}
            request_timeout = WCHA_REQUEST_TIMEOUT
            logger.info(f"Celery Task {self.request.id}: Calling NewsAPI: URL={api_url}, Params={params}", extra=log_extra)
            response = requests.get(api_url, headers=headers, params=params, timeout=request_timeout)
            response.raise_for_status()
            response_json = response.json()
            if response_json.get("status") != "ok":
                error_msg = f"NewsAPI returned error: {response_json.get('message', 'Unknown NewsAPI error')}"
                logger.error(f"Celery Task {self.request.id}: {error_msg}", extra=log_extra)
                error_payload = {"error_type": "NewsAPIError", "message": error_msg, "details": response_json}
                store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_FAILED, error_payload=error_payload, workflow_id=log_extra['workflow_id'], is_new_key=False)
                db_conn.commit()
                raise requests.exceptions.HTTPError(error_msg, response=response)
            articles = response_json.get("articles", [])
            logger.info(f"Celery Task {self.request.id}: Fetched {len(articles)} articles.", extra=log_extra)
            result = {"status": "success", "articles": articles, "message": f"Fetched {len(articles)} articles."}
            store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_COMPLETED, result_payload=result, workflow_id=log_extra['workflow_id'], is_new_key=False)
            db_conn.commit()
            return result
    except requests.exceptions.RequestException as e_req:
        error_msg = f"NewsAPI request error: {e_req}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True, extra=log_extra)
        with get_db_connection(service_name='wcha') as db_conn:
            error_payload = {"error_type": type(e_req).__name__, "message": str(e_req)}
            store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_FAILED, error_payload=error_payload, workflow_id=log_extra['workflow_id'], is_new_key=False)
            db_conn.commit()
        raise self.retry(exc=e_req, countdown=60, max_retries=3)
    except Exception as e_unexp:
        error_msg = f"Unexpected error fetching news: {e_unexp}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True, extra=log_extra)
        with get_db_connection(service_name='wcha') as db_conn:
            error_payload = {"error_type": type(e_unexp).__name__, "message": str(e_unexp)}
            store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_FAILED, error_payload=error_payload, workflow_id=log_extra['workflow_id'], is_new_key=False)
            db_conn.commit()
        raise self.retry(exc=e_unexp, countdown=60, max_retries=1)

@celery_app.task(bind=True, name='harvest_url_content_task')
def harvest_url_content_task(self, request_id: str, url_to_harvest: str, min_length: int = 150):
    log_extra = {'task_id': request_id, 'workflow_id': 'N/A'}
    idempotency_key = request_id
    task_name = self.name
    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id}): Starting task '{task_name}' for URL: {url_to_harvest}", extra=log_extra)
    try:
        with get_db_connection(service_name='wcha') as db_conn:
            db_conn.autocommit = False

            existing_record = check_idempotency_key(db_conn, idempotency_key, task_name)

            if existing_record:
                status = existing_record['status']
                locked_at = existing_record['locked_at']
                if status == IDEMPOTENCY_STATUS_COMPLETED:
                    logger.info(f"Idempotency: Found COMPLETED record for key '{idempotency_key}'. Returning stored result.", extra=log_extra)
                    db_conn.rollback()
                    return existing_record['result_payload']
                elif status == IDEMPOTENCY_STATUS_PROCESSING:
                    lock_timeout_seconds = IDEMPOTENCY_LOCK_TIMEOUT_SECONDS
                    if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < lock_timeout_seconds:
                        logger.warning(f"Idempotency: Key '{idempotency_key}' is already PROCESSING. Returning conflict status.", extra=log_extra)
                        db_conn.rollback()
                        return {"status": "PROCESSING_CONFLICT", "message": "Task with this idempotency key is already processing.", "idempotency_key": idempotency_key}
                    else: # Lock expired or was null
                        logger.warning(f"Idempotency: Key '{idempotency_key}' was 'processing' but lock timed out/null. Re-processing.", extra=log_extra)
                        store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_PROCESSING, workflow_id=log_extra['workflow_id'], is_new_key=False)
                elif status == IDEMPOTENCY_STATUS_FAILED:
                     logger.info(f"Idempotency: Key '{idempotency_key}' previously FAILED. Retrying.", extra=log_extra)
                     store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_PROCESSING, workflow_id=log_extra['workflow_id'], is_new_key=False)
            else: # No existing record for this service
                store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_PROCESSING, workflow_id=log_extra['workflow_id'], is_new_key=True)

            db_conn.commit()

            is_safe, reason = is_url_safe(url_to_harvest, task_id=request_id, workflow_id='N/A')
            if not is_safe:
                logger.warning(f"Celery Task {self.request.id}: URL '{url_to_harvest}' is not safe: {reason}. Skipping harvest.", extra=log_extra)
                result = {"url": url_to_harvest, "content": None, "error_type": WCHA_ERROR_TYPE_SSRF_BLOCKED, "error_message": reason}
                store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_COMPLETED, result_payload=result, workflow_id=log_extra['workflow_id'], is_new_key=False)
                db_conn.commit()
                return result
            request_timeout = WCHA_REQUEST_TIMEOUT
            headers = {'User-Agent': WCHA_USER_AGENT}
            if not _IMPORTS_SUCCESSFUL_REQUESTS:
                error_msg = f"Required library missing: requests ({_MISSING_IMPORT_ERROR_REQUESTS})"
                logger.error(f"Celery Task {self.request.id}: {error_msg}", extra=log_extra)
                error_payload = {"error_type": "ImportError", "message": error_msg}
                store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_FAILED, error_payload=error_payload, workflow_id=log_extra['workflow_id'], is_new_key=False)
                db_conn.commit()
                raise ImportError(error_msg)
            if not _IMPORTS_SUCCESSFUL_TRAFILATURA:
                error_msg = f"Required library missing: trafilatura ({_MISSING_IMPORT_ERROR_TRAFILATURA})"
                logger.error(f"Celery Task {self.request.id}: {error_msg}", extra=log_extra)
                error_payload = {"error_type": "ImportError", "message": error_msg}
                store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_FAILED, error_payload=error_payload, workflow_id=log_extra['workflow_id'], is_new_key=False)
                db_conn.commit()
                raise ImportError(error_msg)
            logger.info(f"Celery Task {self.request.id}: Attempting to harvest content from URL: {url_to_harvest} using Trafilatura", extra=log_extra)
            response = requests.get(url_to_harvest, headers=headers, timeout=request_timeout, allow_redirects=False)
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
                logger.warning(f"Celery Task {self.request.id}: Content at URL '{url_to_harvest}' may not be HTML (Content-Type: {content_type}).", extra=log_extra)
            extracted_text = trafilatura.extract(response.content, url=url_to_harvest, output_format='txt',
                                                 include_comments=False, include_tables=False, favor_precision=True)
            result = None
            if extracted_text:
                if len(extracted_text) < min_length:
                    logger.warning(f"Celery Task {self.request.id}: Content from {url_to_harvest} is shorter ({len(extracted_text)}) than min_length ({min_length}).", extra=log_extra)
                logger.info(f"Celery Task {self.request.id}: Trafilatura successfully extracted {len(extracted_text)} characters from {url_to_harvest}.", extra=log_extra)
                result = {"url": url_to_harvest, "content": extracted_text, "error_type": None, "error_message": None}
            else:
                logger.warning(f"Celery Task {self.request.id}: Trafilatura extracted no content from URL: {url_to_harvest}.", extra=log_extra)
                result = {"url": url_to_harvest, "content": None, "error_type": WCHA_ERROR_TYPE_NO_CONTENT, "error_message": "Trafilatura extracted no content."}
            store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_COMPLETED, result_payload=result, workflow_id=log_extra['workflow_id'], is_new_key=False)
            db_conn.commit()
            return result
    except requests.exceptions.RequestException as e_req:
        error_msg = f"RequestException ({type(e_req).__name__}) while fetching '{url_to_harvest}': {e_req}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True, extra=log_extra)
        with get_db_connection(service_name='wcha') as db_conn:
            error_payload = {"error_type": type(e_req).__name__, "message": str(e_req), "url": url_to_harvest}
            store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_FAILED, error_payload=error_payload, workflow_id=log_extra['workflow_id'], is_new_key=False)
            db_conn.commit()
        raise self.retry(exc=e_req, countdown=60, max_retries=3)
    except Exception as e_gen:
        error_msg = f"General error during harvest for '{url_to_harvest}': {type(e_gen).__name__} - {e_gen}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True, extra=log_extra)
        with get_db_connection(service_name='wcha') as db_conn:
            error_payload = {"error_type": type(e_gen).__name__, "message": str(e_gen), "url": url_to_harvest}
            store_idempotency_record(db_conn, idempotency_key, task_name, IDEMPOTENCY_STATUS_FAILED, error_payload=error_payload, workflow_id=log_extra['workflow_id'], is_new_key=False)
            db_conn.commit()
        raise self.retry(exc=e_gen, countdown=60, max_retries=1)

def harvest_from_url(url: str, min_length: int = 150, **kwargs) -> dict:
    local_task_id = kwargs.pop('task_id', f"harvest_sync_{uuid.uuid4().hex[:8]}")
    local_workflow_id = kwargs.pop('workflow_id', "N/A")
    log_extra_sync = {'task_id': local_task_id, 'workflow_id': local_workflow_id}
    safe, reason = is_url_safe(url, task_id=local_task_id, workflow_id=local_workflow_id)
    if not safe:
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_SSRF_BLOCKED, "error_message": reason}
    request_timeout = WCHA_REQUEST_TIMEOUT
    headers = {'User-Agent': WCHA_USER_AGENT}
    if not _IMPORTS_SUCCESSFUL_REQUESTS:
        error_msg = f"Required library missing: requests ({_MISSING_IMPORT_ERROR_REQUESTS})"
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}", extra=log_extra_sync)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_LIB_MISSING, "error_message": error_msg}
    if not _IMPORTS_SUCCESSFUL_TRAFILATURA:
        error_msg = f"Required library missing: trafilatura ({_MISSING_IMPORT_ERROR_TRAFILATURA})"
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}", extra=log_extra_sync)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_LIB_MISSING, "error_message": error_msg}
    logger.info(f"[WCHA_LOGIC_WEB_SYNC] Attempting to harvest content from URL: {url} using Trafilatura", extra=log_extra_sync)
    try:
        response = requests.get(url, headers=headers, timeout=request_timeout, allow_redirects=False)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
            logger.warning(f"[WCHA_LOGIC_WEB_SYNC] Content at URL '{url}' may not be HTML (Content-Type: {content_type}). Trafilatura will attempt extraction.", extra=log_extra_sync)
        extracted_text = trafilatura.extract(response.content, url=url, output_format='txt',
                                             include_comments=False, include_tables=False, favor_precision=True)
        if extracted_text:
            if len(extracted_text) < min_length:
                logger.warning(f"[WCHA_LOGIC_WEB_SYNC] Content from {url} is shorter ({len(extracted_text)} chars) than min_length ({min_length} chars).", extra=log_extra_sync)
            logger.info(f"[WCHA_LOGIC_WEB_SYNC] Trafilatura successfully extracted {len(extracted_text)} characters from {url}.", extra=log_extra_sync)
            return {"url": url, "content": extracted_text, "error_type": None, "error_message": None}
        else:
            logger.warning(f"[WCHA_LOGIC_WEB_SYNC] Trafilatura extracted no content from URL: {url}.", extra=log_extra_sync)
            return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_NO_CONTENT, "error_message": "Trafilatura extracted no content."}
    except requests.exceptions.Timeout as e_timeout:
        error_msg = f"Timeout after {request_timeout} seconds while fetching '{url}'."
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}", exc_info=True, extra=log_extra_sync)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_FETCH, "error_message": error_msg}
    except requests.exceptions.HTTPError as e_http:
        error_msg = f"HTTP Status {e_http.response.status_code} while fetching '{url}'."
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg} Response: {e_http.response.text[:200]}", exc_info=True, extra=log_extra_sync)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_FETCH, "error_message": error_msg}
    except requests.exceptions.RequestException as e_req:
        error_msg = f"RequestException ({type(e_req).__name__}) while fetching '{url}': {e_req}"
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}", exc_info=True, extra=log_extra_sync)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_FETCH, "error_message": error_msg}
    except Exception as e_traf:
        error_msg = f"Trafilatura processing or other unexpected error for '{url}': {type(e_traf).__name__} - {e_traf}"
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}", exc_info=True, extra=log_extra_sync)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_EXTRACTION, "error_message": error_msg}

def get_content_for_topic(topic: str, max_results_override: Optional[int] = None, **kwargs) -> dict:
    request_id = f"wcha_topic_req_{uuid.uuid4().hex[:8]}"
    log_extra = {'task_id': request_id, 'workflow_id': 'N/A'}
    if not IMPORTS_SUCCESSFUL:
        error_msg = f"{ERROR_WCHA_LIB_MISSING} {MISSING_IMPORT_ERROR}"
        logger.error(error_msg, extra=log_extra)
        return {"status": "failure_dependency", "content": None, "source_urls": [], "message": error_msg, "task_id": None}
    if USE_REAL_NEWS_API:
        logger.info(f"[WCHA_GET_CONTENT] Using REAL NewsAPI for topic: '{topic}'. Dispatching Celery task.", extra=log_extra)
        task = fetch_news_articles_task.delay(
            request_id=request_id, topic=topic, language=TDA_NEWS_DEFAULT_LANGUAGE,
            max_results=(max_results_override if max_results_override is not None else WCHA_SEARCH_MAX_RESULTS)
        )
        logger.info(f"[WCHA_GET_CONTENT] Dispatched NewsAPI fetch task {task.id} for topic '{topic}'.", extra=log_extra)
        return {"status": "pending_news_api", "task_id": task.id, "message": "News article fetching initiated.", "source_urls": [], "content": None}
    if max_results_override is not None: actual_max_search_results = max_results_override
    else: actual_max_search_results = WCHA_SEARCH_MAX_RESULTS
    logger.info(f"[WCHA_SEARCH_HARVEST] Starting content search and harvest for topic: '{topic}' (max_results: {actual_max_search_results})", extra=log_extra)
    search_urls = []
    try:
        with DDGS() as ddgs:
            ddgs_results = list(ddgs.text(keywords=topic, region='wt-wt', safesearch='moderate', max_results=actual_max_search_results))
            if ddgs_results: search_urls = [r['href'] for r in ddgs_results if r.get('href')]
        logger.info(f"[WCHA_SEARCH_HARVEST] Found {len(search_urls)} URLs for topic '{topic}': {search_urls}", extra=log_extra)
    except Exception as e:
        error_msg = f"{ERROR_WCHA_SEARCH_FAILED} '{topic}': {type(e).__name__} - {e}."
        logger.error(f"[WCHA_SEARCH_HARVEST] {error_msg}", exc_info=True, extra=log_extra)
        logger.info("WCHA metric", extra={'metric_name': 'wcha_search_failure_count', 'value': 1, 'tags': {'topic': topic, 'reason': type(e).__name__}, **log_extra})
        return {"status": "failure", "content": None, "source_urls": [], "message": error_msg}
    if not search_urls:
        message = f"{ERROR_WCHA_NO_SEARCH_RESULTS}: {topic}"
        logger.warning(f"[WCHA_SEARCH_HARVEST] {message}", extra=log_extra)
        logger.info("WCHA metric", extra={'metric_name': 'wcha_no_search_results_count', 'value': 1, 'tags': {'topic': topic}, **log_extra})
        return {"status": "failure", "content": None, "source_urls": [], "message": message}
    all_harvested_content_parts = []
    successfully_harvested_urls = []
    failed_harvest_details = []
    min_content_length_for_aggregation = WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION
    for i, url in enumerate(search_urls):
        logger.info(f"[WCHA_SEARCH_HARVEST] Attempting to harvest from URL ({i+1}/{len(search_urls)}): {url}", extra=log_extra)
        harvest_result = harvest_from_url(url, min_length=min_content_length_for_aggregation, task_id=request_id, workflow_id=log_extra['workflow_id'])
        if harvest_result.get("content"):
            if len(harvest_result["content"]) >= min_content_length_for_aggregation:
                all_harvested_content_parts.append(f"Source: {harvest_result['url']}\n{harvest_result['content']}")
                successfully_harvested_urls.append(harvest_result['url'])
                logger.info(f"[WCHA_SEARCH_HARVEST] Successfully harvested and validated content from: {url}", extra=log_extra)
                logger.info("WCHA metric", extra={'metric_name': 'wcha_single_harvest_success_count', 'value': 1, 'tags': {'url': url, 'topic': topic}, **log_extra})
            else:
                short_content_message = f"Content from {url} was too short ({len(harvest_result['content'])} chars, min: {min_content_length_for_aggregation}) and was not aggregated."
                logger.warning(f"[WCHA_SEARCH_HARVEST] {short_content_message}", extra=log_extra)
                failed_harvest_details.append(f"URL: {url}, Status: Skipped (too short), Message: {short_content_message}")
                logger.info("WCHA metric", extra={'metric_name': 'wcha_harvest_skipped_short_content_count', 'value': 1, 'tags': {'url': url, 'topic': topic, 'length': len(harvest_result["content"]) }, **log_extra})
        else:
            error_type = harvest_result.get("error_type", WCHA_ERROR_TYPE_UNKNOWN)
            error_message = harvest_result.get("error_message", "Unknown error during harvest.")
            failed_harvest_details.append(f"URL: {url}, Status: Failed, Type: {error_type}, Message: {error_message}")
            logger.warning(f"[WCHA_SEARCH_HARVEST] Failed to harvest content from URL: {url}. Type: {error_type}, Reason: {error_message}", extra=log_extra)
            logger.info("WCHA metric", extra={'metric_name': 'wcha_single_harvest_failure_count', 'value': 1, 'tags': {'url': url, 'topic': topic, 'error_type': error_type}, **log_extra})
    if not successfully_harvested_urls:
        failure_message = f"{ERROR_WCHA_HARVEST_ALL_FAILED} {len(search_urls)} search results for topic: {topic}. Failures: {'; '.join(failed_harvest_details)}"
        logger.warning(f"[WCHA_SEARCH_HARVEST] {failure_message}", extra=log_extra)
        logger.info("WCHA metric", extra={'metric_name': 'wcha_topic_harvest_failure_count', 'value': 1, 'tags': {'topic': topic, 'search_url_count': len(search_urls)}, **log_extra})
        return {"status": "failure", "content": None, "source_urls": [], "message": failure_message}
    final_content = "\n\n---\n\n".join(all_harvested_content_parts)
    success_message = f"Successfully consolidated content from {len(successfully_harvested_urls)} out of {len(search_urls)} URLs for topic '{topic}'."
    if failed_harvest_details: success_message += f" Failures: {'; '.join(failed_harvest_details)}"
    logger.info(f"[WCHA_SEARCH_HARVEST] {success_message} Total length: {len(final_content)} chars.", extra=log_extra)
    logger.info("WCHA metric", extra={'metric_name': 'wcha_topic_harvest_success_count', 'value': 1, 'tags': {'topic': topic, 'successful_urls': len(successfully_harvested_urls), 'total_urls_tried': len(search_urls), 'content_length': len(final_content)}, **log_extra})
    return {"status": "success", "content": final_content, "source_urls": successfully_harvested_urls, "message": success_message}

@app.route("/harvest", methods=["POST"])
def harvest_api_endpoint():
    api_request_id = f"wcha_api_req_{uuid.uuid4().hex[:8]}"
    log_extra_api = {'task_id': api_request_id, 'workflow_id': 'N/A'}
    try:
        try:
            request_data = flask.request.get_json()
            if not request_data:
                logger.warning("[WCHA_API] Received empty or non-JSON payload for /harvest.", extra=log_extra_api)
                return flask.jsonify({"error_code": "WCHA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload.", "details": "Request body must be a valid non-empty JSON object."}), 400
        except Exception as e_json_decode:
            logger.warning(f"[WCHA_API] Failed to decode JSON payload for /harvest: {e_json_decode}", exc_info=True, extra=log_extra_api)
            return flask.jsonify({"error_code": "WCHA_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json_decode)}), 400
        topic = request_data.get("topic")
        url_to_harvest = request_data.get("url")
        use_search = request_data.get("use_search", False)
        max_results_override = request_data.get("max_results")
        min_length_override = request_data.get("min_length")
        if use_search and topic:
            logger.info(f"[WCHA_API] Received API request to search and harvest for topic: '{topic}'", extra=log_extra_api)
            harvest_params_for_search = {}
            if max_results_override is not None:
                try: harvest_params_for_search["max_results_override"] = int(max_results_override)
                except ValueError: logger.warning(f"[WCHA_API] Invalid max_results value '{max_results_override}'. Using default.", extra=log_extra_api)
            result_dict_or_task = get_content_for_topic(topic, task_id=api_request_id, workflow_id='N/A', **harvest_params_for_search)
            if result_dict_or_task.get("status") == "pending_news_api":
                logger.info(f"[WCHA_API] NewsAPI task {result_dict_or_task['task_id']} dispatched for topic '{topic}'.", extra=log_extra_api)
                return flask.jsonify({"task_id": result_dict_or_task['task_id'], "status_url": f"/v1/tasks/{result_dict_or_task['task_id']}", "message": "News article fetching initiated. Poll task ID for results. Then, optionally re-call /harvest with specific article URLs if needed."}), 202
            else:
                status_code = 500
                if result_dict_or_task["status"] == "success": status_code = 200
                elif result_dict_or_task["message"].startswith(ERROR_WCHA_LIB_MISSING): status_code = 503
                elif result_dict_or_task["message"].startswith(ERROR_WCHA_NO_SEARCH_RESULTS): status_code = 404
                elif result_dict_or_task["message"].startswith(ERROR_WCHA_SEARCH_FAILED): status_code = 502
                return flask.jsonify(result_dict_or_task), status_code
        elif url_to_harvest:
            logger.info(f"[WCHA_API] Received API request for async direct URL harvest: '{url_to_harvest}'", extra=log_extra_api)
            safe, reason = is_url_safe(url_to_harvest, task_id=api_request_id, workflow_id='N/A')
            if not safe:
                return flask.jsonify({"error_code": WCHA_ERROR_TYPE_SSRF_BLOCKED, "message": reason, "url": url_to_harvest}), 400
            min_length_val = 150
            if min_length_override is not None:
                try: min_length_val = int(min_length_override)
                except ValueError: logger.warning(f"Invalid min_length override: {min_length_override}, using default {min_length_val}.", extra=log_extra_api)
            celery_task_request_id = f"wcha_harvest_direct_{uuid.uuid4().hex[:8]}"
            task = harvest_url_content_task.delay(request_id=celery_task_request_id, url_to_harvest=url_to_harvest, min_length=min_length_val)
            logger.info(f"[WCHA_API] Dispatched harvest task {task.id} for URL: {url_to_harvest} (Celery task request_id: {celery_task_request_id})", extra=log_extra_api)
            return flask.jsonify({"task_id": task.id, "status_url": f"/v1/tasks/{task.id}", "message": "Harvest task accepted."}), 202
        elif topic:
            logger.info(f"[WCHA_API] Received API request for mock topic (no use_search or url): '{topic}'", extra=log_extra_api)
            content_result_mock_str = harvest_content(topic, task_id=api_request_id, workflow_id='N/A')
            if content_result_mock_str.startswith("No pre-defined content found"):
                return flask.jsonify({"status": "success", "content": None, "source_urls": ["mock_data_source"], "message": content_result_mock_str }), 200
            return flask.jsonify({"status": "success", "content": content_result_mock_str, "source_urls": ["mock_data_source"], "message": f"Mock content provided for topic: {topic}"}), 200
        else:
            logger.warning("[WCHA_API] Invalid API request. 'url' or 'topic' (with use_search=true for web search, or alone for mock) must be provided.", extra=log_extra_api)
            return flask.jsonify({"error_code": "WCHA_MISSING_PARAMETERS", "message": "Invalid input", "details": "'topic' (with use_search=true) or 'url' must be provided."}), 400
    except Exception as e:
        logger.error(f"Unexpected error in /harvest endpoint: {e}", exc_info=True, extra=log_extra_api)
        return flask.jsonify({"error_code": "WCHA_INTERNAL_SERVER_ERROR", "message": "Internal server error", "details": str(e)}), 500

@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    log_extra_status = {'task_id': task_id, 'workflow_id': 'N/A'}
    logger.info(f"Received request for WCHA task status: {task_id}", extra=log_extra_status)
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}
    if task_result.successful():
        response_data["result"] = task_result.result
        return flask.jsonify(response_data), 200
    elif task_result.failed():
        error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
        response_data["result"] = error_info
        logger.warning(f"Task {task_id} failed. Info: {task_result.info}", extra=log_extra_status)
        return flask.jsonify(response_data), 500
    else:
        return flask.jsonify(response_data), 202

def init_wcha_db():
    """Initializes the database connection pool for the WCHA service."""
    init_db_connection_pool(service_name='wcha')

if __name__ == "__main__":
    init_wcha_db()
    host = os.getenv("WCHA_HOST", "0.0.0.0")
    port = int(os.getenv("WCHA_PORT", 5003))
    debug_mode = os.getenv("FLASK_DEBUG", "True").lower() == "true"
    logger.info(f"--- WCHA Service starting on {host}:{port} (Debug: {debug_mode}) ---")
    app.run(host=host, port=port, debug=debug_mode)
