import uuid # Retain for generate_harvest_id
import datetime # Not directly used by new functions but often useful
import logging
import json # Not directly used by new functions but often useful
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
# --- Load Environment Variables ---
load_dotenv() # Added

# --- Celery Configuration ---
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

celery_app = Celery(
    'wcha_tasks',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND
)
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)
celery_app.finalize() # Explicitly finalize the app

# --- WCHA Configuration ---
wcha_config = {}

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

# --- Idempotency Configuration ---
IDEMPOTENCY_LOCK_TIMEOUT_SECONDS = 300 # 5 minutes
db_connection_pool = None

def get_db_connection():
    """Establishes and returns a database connection from the pool."""
    global db_connection_pool
    if db_connection_pool is None:
        try:
            db_connection_pool = psycopg2_pool.SimpleConnectionPool(
                minconn=1,
                maxconn=5, # Adjust maxconn as needed
                user=os.getenv("POSTGRES_USER"),
                password=os.getenv("POSTGRES_PASSWORD"),
                host=os.getenv("POSTGRES_HOST"),
                port=os.getenv("POSTGRES_PORT", "5432"),
                database=os.getenv("POSTGRES_DB")
            )
            logger.info("Database connection pool created successfully.")
        except (Exception, psycopg2.Error) as error:
            logger.error(f"Error while creating PostgreSQL connection pool: {error}", exc_info=True)
            raise # Re-raise the exception to signal failure

    try:
        return db_connection_pool.getconn()
    except Exception as error:
        logger.error(f"Error getting connection from pool: {error}", exc_info=True)
        raise

def release_db_connection(conn):
    """Releases a database connection back to the pool."""
    global db_connection_pool
    if db_connection_pool and conn:
        db_connection_pool.putconn(conn)

def check_idempotency(db_conn, idempotency_key: str, task_name: str):
    """Checks if a task with the given idempotency key has already been processed or is processing."""
    log_extra = {'idempotency_key': idempotency_key, 'task_name': task_name}
    try:
        with db_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
            cursor.execute(
                """
                SELECT status, result_payload, locked_at, error_payload
                FROM idempotency_keys
                WHERE key = %s AND task_name = %s
                """,
                (idempotency_key, task_name)
            )
            record = cursor.fetchone()
            if record:
                logger.info(f"Idempotency record found: Status - {record['status']}", extra=log_extra)
                if record['status'] == 'completed':
                    return {'status': 'completed', 'result': record['result_payload']}
                elif record['status'] == 'processing':
                    if record['locked_at'] and (time.time() - record['locked_at'].timestamp()) < IDEMPOTENCY_LOCK_TIMEOUT_SECONDS:
                        logger.warning("Task is already processing (lock not expired).", extra=log_extra)
                        return {'status': 'conflict', 'message': 'Task already processing'}
                    else:
                        logger.warning("Task was 'processing' but lock expired or missing. Will attempt to re-acquire.", extra=log_extra)
                        return None # Stale lock, proceed to acquire
                elif record['status'] == 'failed':
                     logger.warning("Previous attempt for this task failed. Will attempt to re-run.", extra=log_extra)
                     return None # Failed, proceed to acquire lock and re-run
            return None # No record found or status allows re-processing
    except (Exception, psycopg2.Error) as error:
        logger.error(f"Error checking idempotency: {error}", exc_info=True, extra=log_extra)
        raise # Propagate error to task to handle as failure

def acquire_idempotency_lock(db_conn, idempotency_key: str, task_name: str, workflow_id: Optional[str] = None):
    """Acquires a lock for the task by inserting/updating the idempotency record."""
    log_extra = {'idempotency_key': idempotency_key, 'task_name': task_name, 'workflow_id': workflow_id or "N/A"}
    try:
        with db_conn.cursor() as cursor:
            # Using ON CONFLICT to handle race conditions: if key+task_name already exists, update it.
            # This works if another process just inserted it, or if we are retrying after a stale lock/failure.
            cursor.execute(
                """
                INSERT INTO idempotency_keys (key, task_name, workflow_id, status, locked_at, created_at, updated_at)
                VALUES (%s, %s, %s, 'processing', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (key, task_name) DO UPDATE SET
                    status = 'processing',
                    locked_at = CURRENT_TIMESTAMP,
                    workflow_id = EXCLUDED.workflow_id, -- Use the workflow_id from the current attempt
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id;
                """,
                (idempotency_key, task_name, workflow_id)
            )
            lock_id = cursor.fetchone()
            db_conn.commit()
            if lock_id:
                logger.info("Idempotency lock acquired successfully.", extra=log_extra)
                return True
            else: # Should not happen with RETURNING id on successful insert/update
                logger.error("Failed to acquire idempotency lock (no id returned).", extra=log_extra)
                return False
    except (Exception, psycopg2.Error) as error:
        db_conn.rollback()
        logger.error(f"Error acquiring idempotency lock: {error}", exc_info=True, extra=log_extra)
        # Specific check for unique violation if not using ON CONFLICT (though ON CONFLICT is preferred)
        # if isinstance(error, psycopg2.errors.UniqueViolation):
        #    logger.warning(f"Race condition or pre-existing key during lock acquisition: {error}", extra=log_extra)
        #    return False # Indicate lock was not acquired by this call specifically
        raise

def update_idempotency_record(db_conn, idempotency_key: str, task_name: str, final_status: str, result_payload: Optional[dict] = None, error_payload: Optional[dict] = None):
    """Updates the idempotency record with the final status and result/error."""
    log_extra = {'idempotency_key': idempotency_key, 'task_name': task_name, 'final_status': final_status}
    try:
        with db_conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE idempotency_keys
                SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE key = %s AND task_name = %s
                """,
                (final_status, json.dumps(result_payload) if result_payload else None, json.dumps(error_payload) if error_payload else None, idempotency_key, task_name)
            )
            db_conn.commit()
            logger.info("Idempotency record updated successfully.", extra=log_extra)
    except (Exception, psycopg2.Error) as error:
        db_conn.rollback()
        logger.error(f"Error updating idempotency record: {error}", exc_info=True, extra=log_extra)
        raise

def load_wcha_configuration():
    """Loads WCHA configurations from environment variables with defaults."""
    global wcha_config
    wcha_config['WCHA_SEARCH_MAX_RESULTS'] = int(os.getenv('WCHA_SEARCH_MAX_RESULTS', '3'))
    wcha_config['WCHA_REQUEST_TIMEOUT'] = int(os.getenv('WCHA_REQUEST_TIMEOUT', '10'))
    wcha_config['WCHA_USER_AGENT'] = os.getenv('WCHA_USER_AGENT', 'AethercastContentHarvester/0.2')
    # Added NewsAPI related configurations from previous context to ensure they are loaded
    wcha_config['USE_REAL_NEWS_API'] = os.getenv('USE_REAL_NEWS_API', 'False').lower() == 'true'
    wcha_config['TDA_NEWS_API_KEY'] = os.getenv('TDA_NEWS_API_KEY')
    wcha_config['TDA_NEWS_API_BASE_URL'] = os.getenv('TDA_NEWS_API_BASE_URL', 'https://newsapi.org/v2/')
    wcha_config['TDA_NEWS_API_ENDPOINT'] = os.getenv('TDA_NEWS_API_ENDPOINT', 'everything')
    wcha_config['TDA_NEWS_DEFAULT_KEYWORDS'] = os.getenv('TDA_NEWS_DEFAULT_KEYWORDS', 'technology,AI').split(',')
    wcha_config['TDA_NEWS_DEFAULT_LANGUAGE'] = os.getenv('TDA_NEWS_DEFAULT_LANGUAGE', 'en')
    wcha_config['TDA_NEWS_PAGE_SIZE'] = int(os.getenv('TDA_NEWS_PAGE_SIZE', '20'))
    wcha_config['WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION'] = int(os.getenv('WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION', '150'))


    logger.info("--- WCHA Configuration ---")
    logger.info(f"  WCHA_SEARCH_MAX_RESULTS: {wcha_config['WCHA_SEARCH_MAX_RESULTS']}")
    logger.info(f"  WCHA_REQUEST_TIMEOUT: {wcha_config['WCHA_REQUEST_TIMEOUT']}")
    logger.info(f"  WCHA_USER_AGENT: {wcha_config['WCHA_USER_AGENT']}")
    logger.info(f"  USE_REAL_NEWS_API: {wcha_config['USE_REAL_NEWS_API']}")
    logger.info("--- End WCHA Configuration ---")

# Load configuration at startup
load_wcha_configuration()

# --- Database Initialization ---
# Attempt to initialize the DB connection pool at startup
# This is a best-effort initialization. Actual connection usage is on-demand.
try:
    # Prime the pump, establish a connection to check if DB is available.
    # The actual connection for a task will be fetched from the pool.
    conn = get_db_connection()
    if conn:
        logger.info("Successfully connected to PostgreSQL and primed the connection pool.")
        release_db_connection(conn)
    else:
        logger.warning("Failed to get a DB connection to prime the pool at startup.")
except Exception as e:
    logger.error(f"Failed to initialize database connection pool at startup: {e}", exc_info=True)
    # Depending on policy, the application might not start or continue with DB features disabled.
    # For now, it will log the error and continue; tasks requiring DB will fail at runtime if pool is not available.


# --- Error Message Constants ---
# Prefixes/messages used for returning errors from core functions
ERROR_PREFIX_HARVEST_FAILED_FETCH = "Error fetching URL"
ERROR_PREFIX_HARVEST_TRAFILATURA_FAILED = "WCHA: Trafilatura failed to extract content from URL"
ERROR_PREFIX_HARVEST_TRAFILATURA_NO_CONTENT = "WCHA: Trafilatura extracted no content from URL"
ERROR_PREFIX_HARVEST_LIB_MISSING_TRAFILATURA = "WCHA Error: Trafilatura library not installed"
ERROR_PREFIX_HARVEST_LIB_MISSING_REQUESTS = "Cannot 'harvest_from_url' because requests library is missing"

ERROR_WCHA_LIB_MISSING = "WCHA: Cannot 'get_content_for_topic' due to missing libraries:"
ERROR_WCHA_SEARCH_FAILED = "Error during web search for topic"
ERROR_WCHA_NO_SEARCH_RESULTS = "WCHA: No search results found for topic"
ERROR_WCHA_HARVEST_ALL_FAILED = "WCHA: Failed to harvest usable content from any of the"

# --- New Error Type Constants for Structured Error Reporting in harvest_from_url ---
WCHA_ERROR_TYPE_LIB_MISSING = "library_missing"
WCHA_ERROR_TYPE_FETCH = "fetch_error"
WCHA_ERROR_TYPE_EXTRACTION = "extraction_error"
WCHA_ERROR_TYPE_NO_CONTENT = "no_content_extracted"
WCHA_ERROR_TYPE_CONTENT_TOO_SHORT = "content_too_short"
WCHA_ERROR_TYPE_UNSAFE_URL = "unsafe_url"
WCHA_ERROR_TYPE_SSRF_BLOCKED = "ssrf_blocked" # Added for consistency
WCHA_ERROR_TYPE_UNKNOWN = "unknown_harvest_error"


# For the test endpoint
ENDPOINT_ERROR_INVALID_PAYLOAD = "INVALID_JSON_PAYLOAD_WCHA"
ENDPOINT_ERROR_MISSING_FIELDS = "MISSING_REQUIRED_FIELDS_WCHA"
ENDPOINT_ERROR_INTERNAL_SERVER = "INTERNAL_SERVER_ERROR_WCHA"


# --- Attempt to import web scraping and search libraries ---
_IMPORTS_SUCCESSFUL_REQUESTS_BS4 = True
_MISSING_IMPORT_ERROR_REQUESTS_BS4 = None
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as e:
    _IMPORTS_SUCCESSFUL_REQUESTS_BS4 = False
    _MISSING_IMPORT_ERROR_REQUESTS_BS4 = str(e)
    if 'requests' not in globals():
        def requests_get_placeholder(*args, **kwargs): raise ImportError(f"requests library is not installed. Original error: {_MISSING_IMPORT_ERROR_REQUESTS_BS4}")
        class MockRequestsExceptions: RequestException = type('RequestException', (Exception,), {}); ConnectionError = type('ConnectionError', (RequestException,), {}); Timeout = type('Timeout', (RequestException,), {}); HTTPError = type('HTTPError', (RequestException,), {})
        class MockRequests: get = requests_get_placeholder; exceptions = MockRequestsExceptions()
        requests = MockRequests()
    if 'BeautifulSoup' not in globals():
        def BeautifulSoup_placeholder(*args, **kwargs): raise ImportError(f"BeautifulSoup (bs4) library is not installed. Original error: {_MISSING_IMPORT_ERROR_REQUESTS_BS4}")
        BeautifulSoup = BeautifulSoup_placeholder

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

IMPORTS_SUCCESSFUL_CORE = _IMPORTS_SUCCESSFUL_REQUESTS_BS4 and _IMPORTS_SUCCESSFUL_DDG
IMPORTS_SUCCESSFUL_ADVANCED_EXTRACTION = _IMPORTS_SUCCESSFUL_TRAFILATURA
IMPORTS_SUCCESSFUL = IMPORTS_SUCCESSFUL_CORE and IMPORTS_SUCCESSFUL_ADVANCED_EXTRACTION

MISSING_IMPORT_ERROR = ""
if not IMPORTS_SUCCESSFUL:
    missing_libs_list = []
    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4: missing_libs_list.append(f"requests/bs4 ({_MISSING_IMPORT_ERROR_REQUESTS_BS4})")
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
    # This function is not directly using logger, but if it were, it would need context for task_id/workflow_id
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
    """
    Checks if a URL is safe to fetch.
    - Allows only http and https schemes.
    - Resolves hostname to IP and checks if it's a public IP address.
      (Not loopback, private, or link-local).
    Returns a tuple: (is_safe: bool, reason: str)
    """
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
            # Use getaddrinfo to get all addresses (IPv4 and IPv6)
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
            ip_str = sockaddr[0] # The IP address is the first element of the sockaddr tuple
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
                    break # One unsafe IP is enough to mark the URL as unsafe
            except ValueError:
                # This can happen if the IP string format is somehow invalid, though rare from getaddrinfo
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
    """
    Celery task to fetch news articles from NewsAPI.org.
    """
    log_extra = {'task_id': request_id, 'workflow_id': 'N/A'} # workflow_id is not available here
    idempotency_key = request_id # Using request_id as idempotency key
    task_name = "wcha_fetch_news_articles_task"
    db_conn = None

    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id}): Starting task '{task_name}' for topic '{topic}'.", extra=log_extra)

    try:
        db_conn = get_db_connection()
        idempotency_check_result = check_idempotency(db_conn, idempotency_key, task_name)
        if idempotency_check_result:
            if idempotency_check_result['status'] == 'completed':
                logger.info(f"Task '{task_name}' already completed. Returning stored result.", extra=log_extra)
                return idempotency_check_result['result']
            elif idempotency_check_result['status'] == 'conflict':
                logger.warning(f"Task '{task_name}' conflict: {idempotency_check_result['message']}.", extra=log_extra)
                # Depending on desired behavior, could raise a specific error or return a status
                # For now, let Celery handle it as a retry or log and return something to indicate conflict
                return {"status": "conflict", "message": idempotency_check_result['message']}

        if not acquire_idempotency_lock(db_conn, idempotency_key, task_name, log_extra['workflow_id']):
            # This case should ideally be rare if check_idempotency allows proceeding only when lock can be acquired.
            # Or if ON CONFLICT in acquire_idempotency_lock handles the race.
            logger.error(f"Failed to acquire idempotency lock for task '{task_name}'. Aborting.", extra=log_extra)
            # Consider raising an exception here to force a retry by Celery if appropriate
            return {"status": "error", "message": "Failed to acquire idempotency lock."}

        # Core task logic starts here
        logger.info(f"Celery Task {self.request.id}: Lock acquired. Fetching news for topic '{topic}'.", extra=log_extra)
        if not wcha_config.get("USE_REAL_NEWS_API"):
            logger.info(f"Celery Task {self.request.id}: USE_REAL_NEWS_API is false. Returning mock success.", extra=log_extra)
            result = {"status": "success_mock", "articles": [], "message": "News API is not enabled; mock response."}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'completed', result_payload=result)
            return result

        if not wcha_config.get("TDA_NEWS_API_KEY"):
            logger.error(f"Celery Task {self.request.id}: TDA_NEWS_API_KEY not configured.", extra=log_extra)
            # Update idempotency record before raising error that would cause task to fail
            error_payload = {"error_type": "ConfigurationError", "message": "TDA_NEWS_API_KEY not configured."}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload)
            raise ValueError("NewsAPI key not configured.")

        base_url = wcha_config.get("TDA_NEWS_API_BASE_URL", "https://newsapi.org/v2/")
        endpoint = wcha_config.get("TDA_NEWS_API_ENDPOINT", "everything")
        api_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}" # This was the line 482 with SyntaxError

        params = {}
        query_keywords_list = [kw.strip() for kw in topic.split(',')] if topic else wcha_config.get("TDA_NEWS_DEFAULT_KEYWORDS", [])
        if query_keywords_list:
            params["q"] = " OR ".join(query_keywords_list)

        current_language = language if language else wcha_config.get("TDA_NEWS_DEFAULT_LANGUAGE", "en")
        if current_language:
            params["language"] = current_language

        params["pageSize"] = max_results if max_results else wcha_config.get("TDA_NEWS_PAGE_SIZE", 25)

        headers = {
            "X-Api-Key": wcha_config["TDA_NEWS_API_KEY"],
            "User-Agent": wcha_config.get("WCHA_USER_AGENT", "AethercastContentHarvester/0.2")
        }
        request_timeout = wcha_config.get("WCHA_REQUEST_TIMEOUT", 15)

        logger.info(f"Celery Task {self.request.id}: Calling NewsAPI: URL={api_url}, Params={params}", extra=log_extra)
        # Actual API call logic correctly indented under the main try
        response = requests.get(api_url, headers=headers, params=params, timeout=request_timeout)
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        response_json = response.json()

        if response_json.get("status") != "ok": # NewsAPI specific error status
            error_msg = f"NewsAPI returned error: {response_json.get('message', 'Unknown NewsAPI error')}"
            logger.error(f"Celery Task {self.request.id}: {error_msg}", extra=log_extra)
            error_payload = {"error_type": "NewsAPIError", "message": error_msg, "details": response_json}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload)
            # Consider if this should be retried or is a definitive failure
            raise requests.exceptions.HTTPError(error_msg, response=response) # Make task fail

        articles = response_json.get("articles", [])
        logger.info(f"Celery Task {self.request.id}: Fetched {len(articles)} articles.", extra=log_extra)
        result = {"status": "success", "articles": articles, "message": f"Fetched {len(articles)} articles."}
        update_idempotency_record(db_conn, idempotency_key, task_name, 'completed', result_payload=result)
        return result

    except requests.exceptions.RequestException as e_req: # Covers connection errors, timeouts, HTTP errors
        error_msg = f"NewsAPI request error: {e_req}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True, extra=log_extra)
        if db_conn: # Ensure db_conn is available before trying to update
            error_payload = {"error_type": type(e_req).__name__, "message": str(e_req)}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload)
        raise self.retry(exc=e_req, countdown=60, max_retries=3)
    except Exception as e_unexp: # Catch any other unexpected errors
        error_msg = f"Unexpected error fetching news: {e_unexp}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True, extra=log_extra)
        if db_conn: # Ensure db_conn is available
            error_payload = {"error_type": type(e_unexp).__name__, "message": str(e_unexp)}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload)
        raise self.retry(exc=e_unexp, countdown=60, max_retries=1)
    finally:
        if db_conn:
            release_db_connection(db_conn)


@celery_app.task(bind=True, name='harvest_url_content_task')
def harvest_url_content_task(self, request_id: str, url_to_harvest: str, min_length: int = 150):
    """
    Celery task to harvest content from a single URL.
    Note: is_url_safe check should be done *before* dispatching this task.
    """
    log_extra = {'task_id': request_id, 'workflow_id': 'N/A'}
    idempotency_key = request_id
    task_name = "wcha_harvest_url_content_task"
    db_conn = None

    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id}): Starting task '{task_name}' for URL: {url_to_harvest}", extra=log_extra)

    try:
        db_conn = get_db_connection()
        idempotency_check_result = check_idempotency(db_conn, idempotency_key, task_name)
        if idempotency_check_result:
            if idempotency_check_result['status'] == 'completed':
                logger.info(f"Task '{task_name}' already completed. Returning stored result.", extra=log_extra)
                return idempotency_check_result['result']
            elif idempotency_check_result['status'] == 'conflict':
                logger.warning(f"Task '{task_name}' conflict: {idempotency_check_result['message']}.", extra=log_extra)
                return {"status": "conflict", "url": url_to_harvest, "message": idempotency_check_result['message']}

        if not acquire_idempotency_lock(db_conn, idempotency_key, task_name, log_extra['workflow_id']):
            logger.error(f"Failed to acquire idempotency lock for task '{task_name}'. Aborting.", extra=log_extra)
            return {"status": "error", "url": url_to_harvest, "message": "Failed to acquire idempotency lock."}

        logger.info(f"Celery Task {self.request.id}: Lock acquired. Starting content harvest for URL: {url_to_harvest}", extra=log_extra)

        is_safe, reason = is_url_safe(url_to_harvest, task_id=request_id, workflow_id='N/A')
        if not is_safe:
            logger.warning(f"Celery Task {self.request.id}: URL '{url_to_harvest}' is not safe: {reason}. Skipping harvest.", extra=log_extra)
            result = {"url": url_to_harvest, "content": None, "error_type": WCHA_ERROR_TYPE_SSRF_BLOCKED, "error_message": reason}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'completed', result_payload=result)
            return result

        request_timeout = wcha_config.get('WCHA_REQUEST_TIMEOUT', 10)
        headers = {'User-Agent': wcha_config.get('WCHA_USER_AGENT', 'AethercastContentHarvester/0.2')}

        if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4:
            error_msg = f"Required library missing: requests/bs4 ({_MISSING_IMPORT_ERROR_REQUESTS_BS4})"
            logger.error(f"Celery Task {self.request.id}: {error_msg}", extra=log_extra)
            error_payload = {"error_type": "ImportError", "message": error_msg}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload)
            raise ImportError(error_msg)

        if not _IMPORTS_SUCCESSFUL_TRAFILATURA:
            error_msg = f"Required library missing: trafilatura ({_MISSING_IMPORT_ERROR_TRAFILATURA})"
            logger.error(f"Celery Task {self.request.id}: {error_msg}", extra=log_extra)
            error_payload = {"error_type": "ImportError", "message": error_msg}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload)
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

        update_idempotency_record(db_conn, idempotency_key, task_name, 'completed', result_payload=result)
        return result

    except requests.exceptions.RequestException as e_req:
        error_msg = f"RequestException ({type(e_req).__name__}) while fetching '{url_to_harvest}': {e_req}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True, extra=log_extra)
        if db_conn: # Ensure db_conn is available
            error_payload = {"error_type": type(e_req).__name__, "message": str(e_req), "url": url_to_harvest}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload)
        raise self.retry(exc=e_req, countdown=60, max_retries=3)
    except Exception as e_gen:
        error_msg = f"General error during harvest for '{url_to_harvest}': {type(e_gen).__name__} - {e_gen}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True, extra=log_extra)
        if db_conn: # Ensure db_conn is available
            error_payload = {"error_type": type(e_gen).__name__, "message": str(e_gen), "url": url_to_harvest}
            update_idempotency_record(db_conn, idempotency_key, task_name, 'failed', error_payload=error_payload)
        raise self.retry(exc=e_gen, countdown=60, max_retries=1)
    finally:
        if db_conn:
            release_db_connection(db_conn)

def harvest_from_url(url: str, min_length: int = 150, **kwargs) -> dict: # Added **kwargs
    # This function remains for synchronous use (e.g., by get_content_for_topic)
    # or as a wrapper if needed. For Celery, the core logic is in harvest_url_content_task.
    # For now, it simply calls the old logic.

    local_task_id = kwargs.pop('task_id', f"harvest_sync_{uuid.uuid4().hex[:8]}") # Use kwargs.pop
    local_workflow_id = kwargs.pop('workflow_id', "N/A") # Use kwargs.pop
    log_extra_sync = {'task_id': local_task_id, 'workflow_id': local_workflow_id}

    # First, check if the URL is safe to fetch
    safe, reason = is_url_safe(url, task_id=local_task_id, workflow_id=local_workflow_id)
    if not safe:
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_SSRF_BLOCKED, "error_message": reason}

    request_timeout = wcha_config.get('WCHA_REQUEST_TIMEOUT', 10)
    headers = {'User-Agent': wcha_config.get('WCHA_USER_AGENT', 'AethercastContentHarvester/0.2')}

    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4:
        error_msg = f"Required library missing: requests/bs4 ({_MISSING_IMPORT_ERROR_REQUESTS_BS4})"
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

def get_content_for_topic(topic: str, max_results_override: Optional[int] = None, **kwargs) -> dict: # Added **kwargs
    # This function is now primarily for orchestrating search (DDGS) and then individual URL harvesting.
    # If USE_REAL_NEWS_API is true, it will dispatch a Celery task for news fetching.
    # The harvesting of individual URLs (from DDGS or NewsAPI results) will remain synchronous within this function for now,
    # or could be refactored to dispatch harvest_url_content_task for each URL.
    # For this subtask, we focus on making the NewsAPI call async.

    request_id = f"wcha_topic_req_{uuid.uuid4().hex[:8]}" # For logging and potential task correlation
    log_extra = {'task_id': request_id, 'workflow_id': 'N/A'} # workflow_id is not available here

    if not IMPORTS_SUCCESSFUL: # For DDGS and Trafilatura primarily now
        error_msg = f"{ERROR_WCHA_LIB_MISSING} {MISSING_IMPORT_ERROR}"
        logger.error(error_msg, extra=log_extra)
        return {"status": "failure_dependency", "content": None, "source_urls": [], "message": error_msg, "task_id": None}

    if wcha_config.get("USE_REAL_NEWS_API"):
        logger.info(f"[WCHA_GET_CONTENT] Using REAL NewsAPI for topic: '{topic}'. Dispatching Celery task.", extra=log_extra)
        task = fetch_news_articles_task.delay(
            request_id=request_id, # This is passed to the Celery task and used as task_id in its logs
            topic=topic,
            language=wcha_config.get("TDA_NEWS_DEFAULT_LANGUAGE", "en"), # Assuming lang from config
            max_results=(max_results_override if max_results_override is not None
                         else wcha_config.get('WCHA_SEARCH_MAX_RESULTS', 3))
        )
        logger.info(f"[WCHA_GET_CONTENT] Dispatched NewsAPI fetch task {task.id} for topic '{topic}'.", extra=log_extra)
        # The caller of get_content_for_topic will now get a task_id for NewsAPI results.
        # The actual content harvesting from these news URLs would be a subsequent step.
        return {"status": "pending_news_api",
                "task_id": task.id,
                "message": "News article fetching initiated.",
                "source_urls": [],
                "content": None}

    # Fallback to DDGS if NewsAPI is not used (existing synchronous logic)
    if max_results_override is not None:
        actual_max_search_results = max_results_override
    else:
        actual_max_search_results = wcha_config.get('WCHA_SEARCH_MAX_RESULTS', 3)

    logger.info(f"[WCHA_SEARCH_HARVEST] Starting content search and harvest for topic: '{topic}' (max_results: {actual_max_search_results})", extra=log_extra)

    search_urls = []
    try:
        with DDGS() as ddgs:
            ddgs_results = list(ddgs.text(
                keywords=topic,
                region='wt-wt',
                safesearch='moderate',
                max_results=actual_max_search_results
            ))
            if ddgs_results:
                search_urls = [r['href'] for r in ddgs_results if r.get('href')]
        logger.info(f"[WCHA_SEARCH_HARVEST] Found {len(search_urls)} URLs for topic '{topic}': {search_urls}", extra=log_extra)
    except Exception as e:
        error_msg = f"{ERROR_WCHA_SEARCH_FAILED} '{topic}': {type(e).__name__} - {e}."
        logger.error(f"[WCHA_SEARCH_HARVEST] {error_msg}", exc_info=True, extra=log_extra)
        # Metric for search failure
        logger.info("WCHA metric", extra={'metric_name': 'wcha_search_failure_count', 'value': 1, 'tags': {'topic': topic, 'reason': type(e).__name__}, **log_extra})
        return {"status": "failure", "content": None, "source_urls": [], "message": error_msg}

    if not search_urls:
        message = f"{ERROR_WCHA_NO_SEARCH_RESULTS}: {topic}"
        logger.warning(f"[WCHA_SEARCH_HARVEST] {message}", extra=log_extra)
        # Metric for no search results
        logger.info("WCHA metric", extra={'metric_name': 'wcha_no_search_results_count', 'value': 1, 'tags': {'topic': topic}, **log_extra})
        return {"status": "failure", "content": None, "source_urls": [], "message": message}

    all_harvested_content_parts = []
    successfully_harvested_urls = []
    failed_harvest_details = []
    min_content_length_for_aggregation = wcha_config.get('WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION', 150)

    for i, url in enumerate(search_urls):
        # Note: harvest_from_url itself logs with its own context. We add log_extra for this specific loop log.
        logger.info(f"[WCHA_SEARCH_HARVEST] Attempting to harvest from URL ({i+1}/{len(search_urls)}): {url}", extra=log_extra)
        # Pass request_id (as task_id) and workflow_id to harvest_from_url
        harvest_result = harvest_from_url(url, min_length=min_content_length_for_aggregation, task_id=request_id, workflow_id=log_extra['workflow_id'])

        if harvest_result.get("content"):
            if len(harvest_result["content"]) >= min_content_length_for_aggregation:
                all_harvested_content_parts.append(f"Source: {harvest_result['url']}\n{harvest_result['content']}")
                successfully_harvested_urls.append(harvest_result['url'])
                logger.info(f"[WCHA_SEARCH_HARVEST] Successfully harvested and validated content from: {url}", extra=log_extra)
                # Metric for successful harvest of a single URL
                logger.info("WCHA metric", extra={'metric_name': 'wcha_single_harvest_success_count', 'value': 1, 'tags': {'url': url, 'topic': topic}, **log_extra})
            else:
                short_content_message = f"Content from {url} was too short ({len(harvest_result['content'])} chars, min: {min_content_length_for_aggregation}) and was not aggregated."
                logger.warning(f"[WCHA_SEARCH_HARVEST] {short_content_message}", extra=log_extra)
                failed_harvest_details.append(f"URL: {url}, Status: Skipped (too short), Message: {short_content_message}")
                # Metric for skipped harvest (too short)
                logger.info("WCHA metric", extra={'metric_name': 'wcha_harvest_skipped_short_content_count', 'value': 1, 'tags': {'url': url, 'topic': topic, 'length': len(harvest_result["content"]) }, **log_extra})
        else:
            error_type = harvest_result.get("error_type", WCHA_ERROR_TYPE_UNKNOWN)
            error_message = harvest_result.get("error_message", "Unknown error during harvest.")
            failed_harvest_details.append(f"URL: {url}, Status: Failed, Type: {error_type}, Message: {error_message}")
            logger.warning(f"[WCHA_SEARCH_HARVEST] Failed to harvest content from URL: {url}. Type: {error_type}, Reason: {error_message}", extra=log_extra)
            # Metric for failed harvest of a single URL
            logger.info("WCHA metric", extra={'metric_name': 'wcha_single_harvest_failure_count', 'value': 1, 'tags': {'url': url, 'topic': topic, 'error_type': error_type}, **log_extra})

    if not successfully_harvested_urls:
        failure_message = f"{ERROR_WCHA_HARVEST_ALL_FAILED} {len(search_urls)} search results for topic: {topic}. Failures: {'; '.join(failed_harvest_details)}"
        logger.warning(f"[WCHA_SEARCH_HARVEST] {failure_message}", extra=log_extra)
        # Metric for overall harvest failure for the topic (DDGS path)
        logger.info("WCHA metric", extra={'metric_name': 'wcha_topic_harvest_failure_count', 'value': 1, 'tags': {'topic': topic, 'search_url_count': len(search_urls)}, **log_extra})
        return {"status": "failure", "content": None, "source_urls": [], "message": failure_message}

    final_content = "\n\n---\n\n".join(all_harvested_content_parts)
    success_message = f"Successfully consolidated content from {len(successfully_harvested_urls)} out of {len(search_urls)} URLs for topic '{topic}'."
    if failed_harvest_details:
        success_message += f" Failures: {'; '.join(failed_harvest_details)}"
    
    logger.info(f"[WCHA_SEARCH_HARVEST] {success_message} Total length: {len(final_content)} chars.", extra=log_extra)
    # Metric for successful content consolidation for the topic (DDGS path)
    logger.info("WCHA metric", extra={'metric_name': 'wcha_topic_harvest_success_count', 'value': 1, 'tags': {'topic': topic, 'successful_urls': len(successfully_harvested_urls), 'total_urls_tried': len(search_urls), 'content_length': len(final_content)}, **log_extra})
    return {
        "status": "success",
        "content": final_content,
        "source_urls": successfully_harvested_urls,
        "message": success_message
    }

try:
    import flask
    app = flask.Flask(__name__) 

    @app.route("/harvest", methods=["POST"])
    def harvest_api_endpoint():
        # Generate a unique request_id for this API call, can serve as a task_id for logs related to this specific request processing
        # before it potentially calls a Celery task (which will have its own task_id).
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
            timeout_override = request_data.get("timeout") # timeout_override is not used in current logic directly here
            min_length_override = request_data.get("min_length")

            if use_search and topic:
                logger.info(f"[WCHA_API] Received API request to search and harvest for topic: '{topic}'", extra=log_extra_api)
                harvest_params_for_search = {}
                if max_results_override is not None:
                    try:
                        harvest_params_for_search["max_results_override"] = int(max_results_override)
                    except ValueError:
                        logger.warning(f"[WCHA_API] Invalid max_results value '{max_results_override}'. Using default.", extra=log_extra_api)

                # Pass api_request_id as task_id and N/A as workflow_id to get_content_for_topic
                result_dict_or_task = get_content_for_topic(topic, task_id=api_request_id, workflow_id='N/A', **harvest_params_for_search)

                if result_dict_or_task.get("status") == "pending_news_api":
                    logger.info(f"[WCHA_API] NewsAPI task {result_dict_or_task['task_id']} dispatched for topic '{topic}'.", extra=log_extra_api)
                    return flask.jsonify({
                        "task_id": result_dict_or_task['task_id'],
                        "status_url": f"/v1/tasks/{result_dict_or_task['task_id']}", # Client polls this for NewsAPI results
                    "message": "News article fetching initiated. Poll task ID for results. Then, optionally re-call /harvest with specific article URLs if needed."
                }), 202
            else: # Synchronous DDGS path result
                status_code = 500
                if result_dict_or_task["status"] == "success": status_code = 200
                elif result_dict_or_task["message"].startswith(ERROR_WCHA_LIB_MISSING): status_code = 503
                elif result_dict_or_task["message"].startswith(ERROR_WCHA_NO_SEARCH_RESULTS): status_code = 404
                elif result_dict_or_task["message"].startswith(ERROR_WCHA_SEARCH_FAILED): status_code = 502
                return flask.jsonify(result_dict_or_task), status_code

            # Start a new conditional block after handling 'use_search and topic'
            if url_to_harvest: # This part remains for direct URL async harvesting
                logger.info(f"[WCHA_API] Received API request for async direct URL harvest: '{url_to_harvest}'", extra=log_extra_api)
                # Pass api_request_id as task_id to is_url_safe
                safe, reason = is_url_safe(url_to_harvest, task_id=api_request_id, workflow_id='N/A')
                if not safe:
                    # Log the SSRF block event with API request context (already done by is_url_safe if it logs a warning/error)
                    # logger.warning(f"[WCHA_API] SSRF attempt blocked for URL: {url_to_harvest}. Reason: {reason}", extra=log_extra_api) # This would be redundant
                    return flask.jsonify({"error_code": WCHA_ERROR_TYPE_SSRF_BLOCKED, "message": reason, "url": url_to_harvest}), 400

                min_length_val = 150 # Default
                if min_length_override is not None:
                    try: min_length_val = int(min_length_override)
                    except ValueError: logger.warning(f"Invalid min_length override: {min_length_override}, using default {min_length_val}.", extra=log_extra_api)

                # The request_id passed to harvest_url_content_task.delay will be used as task_id in that Celery task's logs.
                # We use api_request_id for this specific API log entry.
                celery_task_request_id = f"wcha_harvest_direct_{uuid.uuid4().hex[:8]}"
                task = harvest_url_content_task.delay(
                    request_id=celery_task_request_id, # This becomes the task_id in the Celery task
                    url_to_harvest=url_to_harvest,
                    min_length=min_length_val
                )
                logger.info(f"[WCHA_API] Dispatched harvest task {task.id} for URL: {url_to_harvest} (Celery task request_id: {celery_task_request_id})", extra=log_extra_api)
                return flask.jsonify({"task_id": task.id, "status_url": f"/v1/tasks/{task.id}", "message": "Harvest task accepted."}), 202

            elif topic: # Mock content, synchronous
                logger.info(f"[WCHA_API] Received API request for mock topic (no use_search or url): '{topic}'", extra=log_extra_api)
                # Pass api_request_id as task_id to harvest_content
                content_result_mock_str = harvest_content(topic, task_id=api_request_id, workflow_id='N/A')
                if content_result_mock_str.startswith("No pre-defined content found"):
                    return flask.jsonify({
                        "status": "success", "content": None,
                        "source_urls": ["mock_data_source"],
                        "message": content_result_mock_str
                        }), 200
                return flask.jsonify({
                    "status": "success", "content": content_result_mock_str,
                    "source_urls": ["mock_data_source"],
                    "message": f"Mock content provided for topic: {topic}"
                    }), 200
            else: # No valid parameters for /harvest
                logger.warning("[WCHA_API] Invalid API request. 'url' or 'topic' (with use_search=true for web search, or alone for mock) must be provided.", extra=log_extra_api)
                return flask.jsonify({"error_code": "WCHA_MISSING_PARAMETERS", "message": "Invalid input", "details": "'topic' (with use_search=true) or 'url' must be provided."}), 400
        except Exception as e: # Catch-all for unexpected errors in the endpoint
            logger.error(f"Unexpected error in /harvest endpoint: {e}", exc_info=True, extra=log_extra_api)
            return flask.jsonify({"error_code": "WCHA_INTERNAL_SERVER_ERROR", "message": "Internal server error", "details": str(e)}), 500

    @app.route('/v1/tasks/<task_id>', methods=['GET'])
    def get_task_status(task_id: str):
        # For logs related to checking task status, we can use the provided task_id.
        log_extra_status = {'task_id': task_id, 'workflow_id': 'N/A'} # workflow_id might not be known here
        logger.info(f"Received request for WCHA task status: {task_id}", extra=log_extra_status)
        task_result = AsyncResult(task_id, app=celery_app)
        response_data = {"task_id": task_id, "status": task_result.status, "result": None}

        if task_result.successful():
            response_data["result"] = task_result.result
            # Potentially log successful task status retrieval if needed, e.g., for audit, using log_extra_status
            return flask.jsonify(response_data), 200
        elif task_result.failed():
            error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
            response_data["result"] = error_info
            logger.warning(f"Task {task_id} failed. Info: {task_result.info}", extra=log_extra_status)
            return flask.jsonify(response_data), 500 # Or 200, depending on how client handles it
        else: # PENDING, STARTED, RETRY
            # Potentially log pending/other status retrieval if needed
            return flask.jsonify(response_data), 202

except ImportError:
    app = None
    # This log occurs at module load time, task_id/workflow_id might not be relevant or available yet.
    # The global logger config will ensure service_name is added, and others default to N/A.
    logger.info("Flask not installed. API endpoint /harvest will not be available.")

if __name__ == "__main__":
    # Logs in __main__ are for local testing/debugging, they will also use the new JSON format.
    # task_id/workflow_id will default to "N/A" here as they are not explicitly provided.
    print("--- Testing WCHA Functionality ---")
    if not IMPORTS_SUCCESSFUL:
        # Using logger for this warning as well
        logger.warning(f"Some required libraries are missing: {MISSING_IMPORT_ERROR}. Functionality will be limited.")
        # print(f"\nWARNING: Some required libraries are missing: {MISSING_IMPORT_ERROR}")
        # print("Functionality of 'harvest_from_url' and 'get_content_for_topic' will be limited or fail.\n")

    # print("\n--- Testing harvest_content (mock data) ---") # Using logger instead of print for consistency
    logger.info("--- Testing harvest_content (mock data) ---")
    existing_topic_mock = "ai in healthcare"
    logger.info(f"Requesting mock content for topic: '{existing_topic_mock}'")
    # Pass a mock task_id for testing context
    mock_main_task_id = "main_test_task_harvest_content"
    mock_data_content_str = harvest_content(existing_topic_mock, task_id=mock_main_task_id)
    logger.info(f"Content for '{existing_topic_mock}' (first 100 chars): {mock_data_content_str[:100]}...\n")

    logger.info("--- Testing is_url_safe ---")
    mock_main_task_id_url_safe = "main_test_task_url_safe"
    safe_url_test = "https://www.google.com"
    unsafe_url_test_private = "http://192.168.1.1"
    unsafe_url_test_loopback = "http://127.0.0.1"
    unsafe_url_test_scheme = "ftp://example.com"
    unresolvable_url = "http://domain.that.does.not.exist.hopefully"

    is_safe, reason = is_url_safe(safe_url_test, task_id=mock_main_task_id_url_safe)
    is_safe, reason = is_url_safe(unsafe_url_test_private, task_id=mock_main_task_id_url_safe)
    is_safe, reason = is_url_safe(unsafe_url_test_loopback, task_id=mock_main_task_id_url_safe)
    is_safe, reason = is_url_safe(unsafe_url_test_scheme, task_id=mock_main_task_id_url_safe)
    is_safe, reason = is_url_safe(unresolvable_url, task_id=mock_main_task_id_url_safe)


    logger.info("--- Testing harvest_from_url (single URL) ---")
    mock_main_task_id_harvest_url = "main_test_task_harvest_url"
    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4 or not _IMPORTS_SUCCESSFUL_TRAFILATURA:
        missing_libs_harvest_url = []
        if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4: missing_libs_harvest_url.append("requests/bs4")
        if not _IMPORTS_SUCCESSFUL_TRAFILATURA: missing_libs_harvest_url.append("trafilatura")
        logger.warning(f"Skipping harvest_from_url test as libraries are missing: {', '.join(missing_libs_harvest_url)}\n", extra={'task_id': mock_main_task_id_harvest_url})
    else:
        python_wiki_url = "https://en.wikipedia.org/wiki/Python_(programming_language)"
        logger.info(f"Requesting content from URL: '{python_wiki_url}'", extra={'task_id': mock_main_task_id_harvest_url})
        url_harvest_result = harvest_from_url(python_wiki_url, task_id=mock_main_task_id_harvest_url)
        if url_harvest_result.get("content"):
            logger.info(f"Content from '{python_wiki_url}' (first 200 chars): {url_harvest_result['content'][:200]}...\n", extra={'task_id': mock_main_task_id_harvest_url})
        # else: # Error/warning already logged by harvest_from_url with the passed task_id
            # logger.warning(f"Error/Warning from harvest_from_url: {url_harvest_result.get('error_message')}\n", extra={'task_id': mock_main_task_id_harvest_url})
            
    logger.info("--- Testing get_content_for_topic (web search & consolidation) ---")
    mock_main_task_id_get_content = "main_test_task_get_content"
    if not IMPORTS_SUCCESSFUL:
        logger.warning(f"Skipping get_content_for_topic test as libraries are missing: {MISSING_IMPORT_ERROR}\n", extra={'task_id': mock_main_task_id_get_content})
    else:
        search_topic_exercise = "benefits of regular exercise"
        test_max_results_exercise = 2
        logger.info(f"Requesting consolidated content for topic: '{search_topic_exercise}' (max {test_max_results_exercise} results for test)", extra={'task_id': mock_main_task_id_get_content})
        
        consolidated_result_dict = get_content_for_topic(search_topic_exercise, max_results_override=test_max_results_exercise, task_id=mock_main_task_id_get_content)

        # These logs will show the final outcome as observed by the caller in __main__
        logger.info(f"Status from get_content_for_topic: {consolidated_result_dict['status']}", extra={'task_id': mock_main_task_id_get_content})
        logger.info(f"Message from get_content_for_topic: {consolidated_result_dict['message']}", extra={'task_id': mock_main_task_id_get_content})
        if consolidated_result_dict["status"] == "success" and consolidated_result_dict["content"]:
            logger.info(f"Source URLs from get_content_for_topic: {consolidated_result_dict['source_urls']}", extra={'task_id': mock_main_task_id_get_content})
            logger.info(f"Consolidated content for '{search_topic_exercise}' (first 500 chars):\n{consolidated_result_dict['content'][:500]}...\n", extra={'task_id': mock_main_task_id_get_content})
            if len(consolidated_result_dict["content"]) > 500:
                logger.info(f"... (Total length: {len(consolidated_result_dict['content'])} characters)", extra={'task_id': mock_main_task_id_get_content})
        elif consolidated_result_dict["content"]:
             logger.info(f"Content was returned but might be empty or partial. Length: {len(consolidated_result_dict['content'])}", extra={'task_id': mock_main_task_id_get_content})

    logger.info("--- WCHA functionality testing in __main__ complete ---") # Implicitly uses N/A for task_id via filter default
    
    if app:
        logger.info("--- Flask app /harvest is defined (run separately if needed) ---")
        # To run: FLASK_APP=aethercast.wcha.main flask run -p 5003
        # Example POST request (using curl or a tool like Postman):
        # curl -X POST -H "Content-Type: application/json" -d '{"topic":"ai in healthcare", "use_search":true}' http://localhost:5003/harvest
        # curl -X POST -H "Content-Type: application/json" -d '{"url":"https://en.wikipedia.org/wiki/Python_(programming_language)"}' http://localhost:5003/harvest
        # curl -X POST -H "Content-Type: application/json" -d '{"topic":"climate change"}' http://localhost:5003/harvest
