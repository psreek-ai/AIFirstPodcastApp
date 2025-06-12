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

# --- WCHA Configuration ---
wcha_config = {}

def load_wcha_configuration():
    """Loads WCHA configurations from environment variables with defaults."""
    global wcha_config
    wcha_config['WCHA_SEARCH_MAX_RESULTS'] = int(os.getenv('WCHA_SEARCH_MAX_RESULTS', '3'))
    wcha_config['WCHA_REQUEST_TIMEOUT'] = int(os.getenv('WCHA_REQUEST_TIMEOUT', '10'))
    wcha_config['WCHA_USER_AGENT'] = os.getenv('WCHA_USER_AGENT', 'AethercastContentHarvester/0.2')

    logger.info("--- WCHA Configuration ---")
    logger.info(f"  WCHA_SEARCH_MAX_RESULTS: {wcha_config['WCHA_SEARCH_MAX_RESULTS']}")
    logger.info(f"  WCHA_REQUEST_TIMEOUT: {wcha_config['WCHA_REQUEST_TIMEOUT']}")
    logger.info(f"  WCHA_USER_AGENT: {wcha_config['WCHA_USER_AGENT']}")
    logger.info("--- End WCHA Configuration ---")

# --- Logging Configuration ---
# Moved this block before load_wcha_configuration to ensure logger is available
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    # Explicitly configure WCHA logger to prevent interference from other module's basicConfig
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - WCHA - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False # Prevent messages from also being handled by the root logger

# Load configuration at startup
load_wcha_configuration()

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
    return f"harvest_{uuid.uuid4().hex[:10]}"

def harvest_content(topic: str) -> str:
    logger.info(f"[WCHA_LOGIC_MOCK] harvest_content (mock data) called with topic: '{topic}'")
    normalized_topic = topic.lower().strip() if topic else ""
    if normalized_topic in SIMULATED_WEB_CONTENT:
        content = SIMULATED_WEB_CONTENT[normalized_topic]
        logger.info(f"[WCHA_LOGIC_MOCK] Found mock content for topic: '{topic}'")
        return content
    else:
        logger.warning(f"[WCHA_LOGIC_MOCK] No pre-defined mock content found for topic: '{topic}'.")
        return f"No pre-defined content found for topic: {topic}"

def is_url_safe(url_string: str) -> tuple[bool, str]:
    """
    Checks if a URL is safe to fetch.
    - Allows only http and https schemes.
    - Resolves hostname to IP and checks if it's a public IP address.
      (Not loopback, private, or link-local).
    Returns a tuple: (is_safe: bool, reason: str)
    """
    logger.debug(f"[WCHA_URL_VALIDATION] Validating URL: {url_string}")
    try:
        parsed_url = urlparse(url_string)

        if parsed_url.scheme not in ('http', 'https'):
            reason = f"Invalid URL scheme: '{parsed_url.scheme}'. Only 'http' or 'https' allowed."
            logger.warning(f"[WCHA_URL_VALIDATION] {reason}")
            return False, reason

        hostname = parsed_url.hostname
        if not hostname:
            reason = "URL has no hostname."
            logger.warning(f"[WCHA_URL_VALIDATION] {reason}")
            return False, reason

        try:
            # Use getaddrinfo to get all addresses (IPv4 and IPv6)
            addr_info_list = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            reason = f"Could not resolve hostname: '{hostname}'."
            logger.warning(f"[WCHA_URL_VALIDATION] {reason}")
            return False, reason

        if not addr_info_list:
            reason = f"No address information found for hostname: '{hostname}'."
            logger.warning(f"[WCHA_URL_VALIDATION] {reason}")
            return False, reason

        all_ips_safe = True
        unsafe_ip_details = ""

        for family, socktype, proto, canonname, sockaddr in addr_info_list:
            ip_str = sockaddr[0] # The IP address is the first element of the sockaddr tuple
            try:
                ip_addr = ipaddress.ip_address(ip_str)
                logger.debug(f"[WCHA_URL_VALIDATION] URL '{url_string}' (hostname: '{hostname}') resolved to IP: {ip_str} (Family: {family})")

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
            logger.warning(f"[WCHA_URL_VALIDATION] {unsafe_ip_details}")
            return False, unsafe_ip_details

        logger.info(f"[WCHA_URL_VALIDATION] URL '{url_string}' (all resolved IPs are public) is deemed safe.")
        return True, "URL is safe."

    except ValueError as ve:
        reason = f"URL parsing error: {ve}"
        logger.warning(f"[WCHA_URL_VALIDATION] {reason}")
        return False, reason
    except Exception as e:
        reason = f"Unexpected error during URL validation: {e}"
        logger.error(f"[WCHA_URL_VALIDATION] {reason}", exc_info=True)
        return False, reason

@celery_app.task(bind=True, name='fetch_news_articles_task')
def fetch_news_articles_task(self, request_id: str, topic: str, language: Optional[str] = None, max_results: Optional[int] = None):
    """
    Celery task to fetch news articles from NewsAPI.org.
    """
    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id}): Fetching news for topic '{topic}'.")
    if not wcha_config.get("USE_REAL_NEWS_API"):
        logger.info(f"Celery Task {self.request.id}: USE_REAL_NEWS_API is false. Returning empty list for mock behavior.")
        # For consistency, the mock/placeholder logic for NewsAPI could be here if needed.
        # For now, just returning empty as this task is about "real" API call.
        return {"status": "success_mock", "articles": [], "message": "News API is not enabled; mock response."}

    if not wcha_config.get("TDA_NEWS_API_KEY"): # Corrected key based on existing call_real_news_api
        logger.error(f"Celery Task {self.request.id}: TDA_NEWS_API_KEY not configured.")
        raise ValueError("NewsAPI key not configured.") # Makes task fail

    base_url = wcha_config.get("TDA_NEWS_API_BASE_URL", "https://newsapi.org/v2/") # Get from wcha_config
    endpoint = wcha_config.get("TDA_NEWS_API_ENDPOINT", "everything")
    api_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"

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

    logger.info(f"Celery Task {self.request.id}: Calling NewsAPI: URL={api_url}, Params={params}")
    try:
        response = requests.get(api_url, headers=headers, params=params, timeout=request_timeout)
        response.raise_for_status()
        response_json = response.json()

        if response_json.get("status") != "ok": # NewsAPI specific status
            error_msg = f"NewsAPI returned error status: {response_json.get('status')}. Message: {response_json.get('message')}"
            logger.error(f"Celery Task {self.request.id}: {error_msg}")
            # Raise an exception to mark the task as FAILED
            raise requests.exceptions.HTTPError(error_msg, response=response)

        articles = response_json.get("articles", [])
        logger.info(f"Celery Task {self.request.id}: Fetched {len(articles)} articles from NewsAPI for topic '{topic}'.")
        # We are not saving to DB here anymore, that's a separate step if needed.
        return {"status": "success", "articles": articles, "message": f"Fetched {len(articles)} articles."}

    except requests.exceptions.RequestException as e_req:
        logger.error(f"Celery Task {self.request.id}: NewsAPI request error: {e_req}", exc_info=True)
        raise self.retry(exc=e_req, countdown=5, max_retries=2)
    except Exception as e_unexp:
        logger.error(f"Celery Task {self.request.id}: Unexpected error fetching news: {e_unexp}", exc_info=True)
        raise self.retry(exc=e_unexp, countdown=5, max_retries=2)


@celery_app.task(bind=True, name='harvest_url_content_task')
def harvest_url_content_task(self, request_id: str, url_to_harvest: str, min_length: int = 150):
    """
    Celery task to harvest content from a single URL.
    Note: is_url_safe check should be done *before* dispatching this task.
    """
    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id}): Starting content harvest for URL: {url_to_harvest}")

    # Configuration for the task execution (could be passed or accessed if worker has access to wcha_config)
    request_timeout = wcha_config.get('WCHA_REQUEST_TIMEOUT', 10)
    headers = {'User-Agent': wcha_config.get('WCHA_USER_AGENT', 'AethercastContentHarvester/0.2')}

    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4:
        error_msg = f"Required library missing: requests/bs4 ({_MISSING_IMPORT_ERROR_REQUESTS_BS4})"
        logger.error(f"Celery Task {self.request.id}: {error_msg}")
        # Raise an exception to mark the task as FAILED in Celery
        raise ImportError(error_msg)

    if not _IMPORTS_SUCCESSFUL_TRAFILATURA:
        error_msg = f"Required library missing: trafilatura ({_MISSING_IMPORT_ERROR_TRAFILATURA})"
        logger.error(f"Celery Task {self.request.id}: {error_msg}")
        raise ImportError(error_msg)

    logger.info(f"Celery Task {self.request.id}: Attempting to harvest content from URL: {url_to_harvest} using Trafilatura")

    try:
        response = requests.get(url_to_harvest, headers=headers, timeout=request_timeout, allow_redirects=False)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
            logger.warning(f"Celery Task {self.request.id}: Content at URL '{url_to_harvest}' may not be HTML (Content-Type: {content_type}).")

        extracted_text = trafilatura.extract(response.content, url=url_to_harvest, output_format='txt',
                                             include_comments=False, include_tables=False, favor_precision=True)

        if extracted_text:
            if len(extracted_text) < min_length:
                logger.warning(f"Celery Task {self.request.id}: Content from {url_to_harvest} is shorter ({len(extracted_text)}) than min_length ({min_length}).")
            logger.info(f"Celery Task {self.request.id}: Trafilatura successfully extracted {len(extracted_text)} characters from {url_to_harvest}.")
            return {"url": url_to_harvest, "content": extracted_text, "error_type": None, "error_message": None}
        else:
            logger.warning(f"Celery Task {self.request.id}: Trafilatura extracted no content from URL: {url_to_harvest}.")
            # This is a valid outcome, not an exception, but indicates no content found.
            return {"url": url_to_harvest, "content": None, "error_type": WCHA_ERROR_TYPE_NO_CONTENT, "error_message": "Trafilatura extracted no content."}

    except requests.exceptions.RequestException as e_req:
        error_msg = f"RequestException ({type(e_req).__name__}) while fetching '{url_to_harvest}': {e_req}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True)
        raise self.retry(exc=e_req, countdown=5, max_retries=2) # Retry for network issues
    except Exception as e_traf: # Includes Trafilatura errors or other unexpected issues
        error_msg = f"Trafilatura processing or other unexpected error for '{url_to_harvest}': {type(e_traf).__name__} - {e_traf}"
        logger.error(f"Celery Task {self.request.id}: {error_msg}", exc_info=True)
        # Do not retry Trafilatura errors by default, as they might be content-specific.
        # Could add specific retry logic if needed.
        raise # Re-raise to mark task as FAILED

def harvest_from_url(url: str, min_length: int = 150) -> dict:
    # This function remains for synchronous use (e.g., by get_content_for_topic)
    # or as a wrapper if needed. For Celery, the core logic is in harvest_url_content_task.
    # For now, it simply calls the old logic.
    # If get_content_for_topic is also to be made async, this would need more refactoring.

    # First, check if the URL is safe to fetch
    safe, reason = is_url_safe(url)
    if not safe:
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_SSRF_BLOCKED, "error_message": reason}

    request_timeout = wcha_config.get('WCHA_REQUEST_TIMEOUT', 10)
    headers = {'User-Agent': wcha_config.get('WCHA_USER_AGENT', 'AethercastContentHarvester/0.2')}

    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4:
        error_msg = f"Required library missing: requests/bs4 ({_MISSING_IMPORT_ERROR_REQUESTS_BS4})"
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}")
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_LIB_MISSING, "error_message": error_msg}

    if not _IMPORTS_SUCCESSFUL_TRAFILATURA:
        error_msg = f"Required library missing: trafilatura ({_MISSING_IMPORT_ERROR_TRAFILATURA})"
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}")
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_LIB_MISSING, "error_message": error_msg}

    logger.info(f"[WCHA_LOGIC_WEB_SYNC] Attempting to harvest content from URL: {url} using Trafilatura")

    try:
        response = requests.get(url, headers=headers, timeout=request_timeout, allow_redirects=False)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
            logger.warning(f"[WCHA_LOGIC_WEB_SYNC] Content at URL '{url}' may not be HTML (Content-Type: {content_type}). Trafilatura will attempt extraction.")
        extracted_text = trafilatura.extract(response.content, url=url, output_format='txt',
                                             include_comments=False, include_tables=False, favor_precision=True)
        if extracted_text:
            if len(extracted_text) < min_length:
                logger.warning(f"[WCHA_LOGIC_WEB_SYNC] Content from {url} is shorter ({len(extracted_text)} chars) than min_length ({min_length} chars).")
            logger.info(f"[WCHA_LOGIC_WEB_SYNC] Trafilatura successfully extracted {len(extracted_text)} characters from {url}.")
            return {"url": url, "content": extracted_text, "error_type": None, "error_message": None}
        else:
            logger.warning(f"[WCHA_LOGIC_WEB_SYNC] Trafilatura extracted no content from URL: {url}.")
            return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_NO_CONTENT, "error_message": "Trafilatura extracted no content."}
    except requests.exceptions.Timeout as e_timeout:
        error_msg = f"Timeout after {request_timeout} seconds while fetching '{url}'."
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}", exc_info=True)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_FETCH, "error_message": error_msg}
    except requests.exceptions.HTTPError as e_http:
        error_msg = f"HTTP Status {e_http.response.status_code} while fetching '{url}'."
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg} Response: {e_http.response.text[:200]}", exc_info=True)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_FETCH, "error_message": error_msg}
    except requests.exceptions.RequestException as e_req:
        error_msg = f"RequestException ({type(e_req).__name__}) while fetching '{url}': {e_req}"
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}", exc_info=True)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_FETCH, "error_message": error_msg}
    except Exception as e_traf:
        error_msg = f"Trafilatura processing or other unexpected error for '{url}': {type(e_traf).__name__} - {e_traf}"
        logger.error(f"[WCHA_LOGIC_WEB_SYNC] {error_msg}", exc_info=True)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_EXTRACTION, "error_message": error_msg}

def get_content_for_topic(topic: str, max_results_override: Optional[int] = None) -> dict:
    # This function is now primarily for orchestrating search (DDGS) and then individual URL harvesting.
    # If USE_REAL_NEWS_API is true, it will dispatch a Celery task for news fetching.
    # The harvesting of individual URLs (from DDGS or NewsAPI results) will remain synchronous within this function for now,
    # or could be refactored to dispatch harvest_url_content_task for each URL.
    # For this subtask, we focus on making the NewsAPI call async.

    if not IMPORTS_SUCCESSFUL: # For DDGS and Trafilatura primarily now
        error_msg = f"{ERROR_WCHA_LIB_MISSING} {MISSING_IMPORT_ERROR}"
        logger.error(error_msg)
        return {"status": "failure_dependency", "content": None, "source_urls": [], "message": error_msg, "task_id": None}

    request_id = f"wcha_topic_req_{uuid.uuid4().hex[:8]}" # For logging and potential task correlation

    if wcha_config.get("USE_REAL_NEWS_API"):
        logger.info(f"[WCHA_GET_CONTENT] Using REAL NewsAPI for topic: '{topic}'. Dispatching Celery task.")
        task = fetch_news_articles_task.delay(
            request_id=request_id,
            topic=topic,
            language=wcha_config.get("TDA_NEWS_DEFAULT_LANGUAGE", "en"), # Assuming lang from config
            max_results=(max_results_override if max_results_override is not None
                         else wcha_config.get('WCHA_SEARCH_MAX_RESULTS', 3))
        )
        logger.info(f"[WCHA_GET_CONTENT] Dispatched NewsAPI fetch task {task.id} for topic '{topic}'.")
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

    logger.info(f"[WCHA_SEARCH_HARVEST] Starting content search and harvest for topic: '{topic}' (max_results: {actual_max_search_results})")

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
        logger.info(f"[WCHA_SEARCH_HARVEST] Found {len(search_urls)} URLs for topic '{topic}': {search_urls}")
    except Exception as e:
        error_msg = f"{ERROR_WCHA_SEARCH_FAILED} '{topic}': {type(e).__name__} - {e}."
        logger.error(f"[WCHA_SEARCH_HARVEST] {error_msg}", exc_info=True)
        return {"status": "failure", "content": None, "source_urls": [], "message": error_msg}

    if not search_urls:
        message = f"{ERROR_WCHA_NO_SEARCH_RESULTS}: {topic}"
        logger.warning(f"[WCHA_SEARCH_HARVEST] {message}")
        return {"status": "failure", "content": None, "source_urls": [], "message": message}

    all_harvested_content_parts = []
    successfully_harvested_urls = []
    failed_harvest_details = []
    min_content_length_for_aggregation = wcha_config.get('WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION', 150)

    for i, url in enumerate(search_urls):
        logger.info(f"[WCHA_SEARCH_HARVEST] Attempting to harvest from URL ({i+1}/{len(search_urls)}): {url}")
        harvest_result = harvest_from_url(url, min_length=min_content_length_for_aggregation)

        if harvest_result.get("content"):
            if len(harvest_result["content"]) >= min_content_length_for_aggregation:
                all_harvested_content_parts.append(f"Source: {harvest_result['url']}\n{harvest_result['content']}")
                successfully_harvested_urls.append(harvest_result['url'])
                logger.info(f"[WCHA_SEARCH_HARVEST] Successfully harvested and validated content from: {url}")
            else:
                short_content_message = f"Content from {url} was too short ({len(harvest_result['content'])} chars, min: {min_content_length_for_aggregation}) and was not aggregated."
                logger.warning(f"[WCHA_SEARCH_HARVEST] {short_content_message}")
                failed_harvest_details.append(f"URL: {url}, Status: Skipped (too short), Message: {short_content_message}")
        else:
            error_type = harvest_result.get("error_type", WCHA_ERROR_TYPE_UNKNOWN)
            error_message = harvest_result.get("error_message", "Unknown error during harvest.")
            failed_harvest_details.append(f"URL: {url}, Status: Failed, Type: {error_type}, Message: {error_message}")
            logger.warning(f"[WCHA_SEARCH_HARVEST] Failed to harvest content from URL: {url}. Type: {error_type}, Reason: {error_message}")

    if not successfully_harvested_urls:
        failure_message = f"{ERROR_WCHA_HARVEST_ALL_FAILED} {len(search_urls)} search results for topic: {topic}. Failures: {'; '.join(failed_harvest_details)}"
        logger.warning(f"[WCHA_SEARCH_HARVEST] {failure_message}")
        return {"status": "failure", "content": None, "source_urls": [], "message": failure_message}

    final_content = "\n\n---\n\n".join(all_harvested_content_parts)
    success_message = f"Successfully consolidated content from {len(successfully_harvested_urls)} out of {len(search_urls)} URLs for topic '{topic}'."
    if failed_harvest_details:
        success_message += f" Failures: {'; '.join(failed_harvest_details)}"
    
    logger.info(f"[WCHA_SEARCH_HARVEST] {success_message} Total length: {len(final_content)} chars.")
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
        try:
            try:
                request_data = flask.request.get_json()
                if not request_data:
                    logger.warning("[WCHA_API] Received empty or non-JSON payload for /harvest.")
                    return flask.jsonify({"error_code": "WCHA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload.", "details": "Request body must be a valid non-empty JSON object."}), 400
            except Exception as e_json_decode:
                logger.warning(f"[WCHA_API] Failed to decode JSON payload for /harvest: {e_json_decode}", exc_info=True)
                return flask.jsonify({"error_code": "WCHA_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json_decode)}), 400

            topic = request_data.get("topic")
            url_to_harvest = request_data.get("url")
            use_search = request_data.get("use_search", False)
            max_results_override = request_data.get("max_results")
            timeout_override = request_data.get("timeout")
            min_length_override = request_data.get("min_length")

            if use_search and topic:
                logger.info(f"[WCHA_API] Received API request to search and harvest for topic: '{topic}'")
                harvest_params_for_search = {}
                if max_results_override is not None:
                    try:
                        harvest_params_for_search["max_results_override"] = int(max_results_override)
                    except ValueError:
                        logger.warning(f"[WCHA_API] Invalid max_results value '{max_results_override}'. Using default.")

            # Call to get_content_for_topic which now might return a task_id for NewsAPI
            result_dict_or_task = get_content_for_topic(topic, **harvest_params_for_search)

            if result_dict_or_task.get("status") == "pending_news_api":
                logger.info(f"[WCHA_API] NewsAPI task {result_dict_or_task['task_id']} dispatched for topic '{topic}'.")
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

            elif url_to_harvest: # This part remains for direct URL async harvesting
                logger.info(f"[WCHA_API] Received API request for async direct URL harvest: '{url_to_harvest}'")
                safe, reason = is_url_safe(url_to_harvest)
                if not safe:
                    return flask.jsonify({"error_code": WCHA_ERROR_TYPE_SSRF_BLOCKED, "message": reason, "url": url_to_harvest}), 400

                min_length_val = 150 # Default
                if min_length_override is not None:
                    try: min_length_val = int(min_length_override)
                    except ValueError: logger.warning(f"Invalid min_length override: {min_length_override}, using default {min_length_val}.")

                request_id = f"wcha_harvest_{uuid.uuid4().hex[:8]}" # Unique ID for this request/task
                task = harvest_url_content_task.delay(
                    request_id=request_id,
                    url_to_harvest=url_to_harvest,
                    min_length=min_length_val
                )
                logger.info(f"[WCHA_API] Dispatched harvest task {task.id} for URL: {url_to_harvest}")
                return flask.jsonify({"task_id": task.id, "status_url": f"/v1/tasks/{task.id}", "message": "Harvest task accepted."}), 202

            elif topic: # Mock content, synchronous
                logger.info(f"[WCHA_API] Received API request for mock topic (no use_search or url): '{topic}'")
                content_result_mock_str = harvest_content(topic)
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
                logger.warning("[WCHA_API] Invalid API request. 'url' or 'topic' (with use_search=true for web search, or alone for mock) must be provided.")
                return flask.jsonify({"error_code": "WCHA_MISSING_PARAMETERS", "message": "Invalid input", "details": "'topic' (with use_search=true) or 'url' must be provided."}), 400
        except Exception as e: # Catch-all for unexpected errors in the endpoint
            logger.error(f"Unexpected error in /harvest endpoint: {e}", exc_info=True)
            return flask.jsonify({"error_code": "WCHA_INTERNAL_SERVER_ERROR", "message": "Internal server error", "details": str(e)}), 500

    @app.route('/v1/tasks/<task_id>', methods=['GET'])
    def get_task_status(task_id: str):
        logger.info(f"Received request for WCHA task status: {task_id}")
        task_result = AsyncResult(task_id, app=celery_app)
        response_data = {"task_id": task_id, "status": task_result.status, "result": None}

        if task_result.successful():
            response_data["result"] = task_result.result
            return flask.jsonify(response_data), 200
        elif task_result.failed():
            error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
            response_data["result"] = error_info
            return flask.jsonify(response_data), 500 # Or 200
        else: # PENDING, STARTED, RETRY
            return flask.jsonify(response_data), 202

except ImportError:
    app = None 
    logger.info("Flask not installed. API endpoint /harvest will not be available.")

if __name__ == "__main__":
    print("--- Testing WCHA Functionality ---")
    if not IMPORTS_SUCCESSFUL:
        print(f"\nWARNING: Some required libraries are missing: {MISSING_IMPORT_ERROR}")
        print("Functionality of 'harvest_from_url' and 'get_content_for_topic' will be limited or fail.\n")

    print("\n--- Testing harvest_content (mock data) ---")
    existing_topic_mock = "ai in healthcare"
    print(f"Requesting mock content for topic: '{existing_topic_mock}'")
    mock_data_content_str = harvest_content(existing_topic_mock)
    print(f"Content for '{existing_topic_mock}' (first 100 chars): {mock_data_content_str[:100]}...\n")

    print("\n--- Testing is_url_safe ---")
    safe_url_test = "https://www.google.com"
    unsafe_url_test_private = "http://192.168.1.1"
    unsafe_url_test_loopback = "http://127.0.0.1"
    unsafe_url_test_scheme = "ftp://example.com"
    unresolvable_url = "http://domain.that.does.not.exist.hopefully"

    is_safe, reason = is_url_safe(safe_url_test)
    print(f"Checked '{safe_url_test}': Safe={is_safe}, Reason: {reason}")
    is_safe, reason = is_url_safe(unsafe_url_test_private)
    print(f"Checked '{unsafe_url_test_private}': Safe={is_safe}, Reason: {reason}")
    is_safe, reason = is_url_safe(unsafe_url_test_loopback)
    print(f"Checked '{unsafe_url_test_loopback}': Safe={is_safe}, Reason: {reason}")
    is_safe, reason = is_url_safe(unsafe_url_test_scheme)
    print(f"Checked '{unsafe_url_test_scheme}': Safe={is_safe}, Reason: {reason}")
    is_safe, reason = is_url_safe(unresolvable_url)
    print(f"Checked '{unresolvable_url}': Safe={is_safe}, Reason: {reason}")


    print("\n--- Testing harvest_from_url (single URL) ---")
    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4 or not _IMPORTS_SUCCESSFUL_TRAFILATURA:
        missing_libs_harvest_url = []
        if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4: missing_libs_harvest_url.append("requests/bs4")
        if not _IMPORTS_SUCCESSFUL_TRAFILATURA: missing_libs_harvest_url.append("trafilatura")
        print(f"Skipping harvest_from_url test as libraries are missing: {', '.join(missing_libs_harvest_url)}\n")
    else:
        python_wiki_url = "https://en.wikipedia.org/wiki/Python_(programming_language)"
        print(f"Requesting content from URL: '{python_wiki_url}'")
        url_harvest_result = harvest_from_url(python_wiki_url)
        if url_harvest_result.get("content"):
            print(f"Content from '{python_wiki_url}' (first 200 chars): {url_harvest_result['content'][:200]}...\n")
        else:
            print(f"Error/Warning from harvest_from_url: {url_harvest_result.get('error_message')}\n")
            
    print("\n--- Testing get_content_for_topic (web search & consolidation) ---")
    if not IMPORTS_SUCCESSFUL:
        print(f"Skipping get_content_for_topic test as libraries are missing: {MISSING_IMPORT_ERROR}\n")
    else:
        search_topic_exercise = "benefits of regular exercise"
        test_max_results_exercise = 2
        print(f"Requesting consolidated content for topic: '{search_topic_exercise}' (max {test_max_results_exercise} results for test)")
        
        consolidated_result_dict = get_content_for_topic(search_topic_exercise, max_results_override=test_max_results_exercise)

        print(f"Status: {consolidated_result_dict['status']}")
        print(f"Message: {consolidated_result_dict['message']}")
        if consolidated_result_dict["status"] == "success" and consolidated_result_dict["content"]:
            print(f"Source URLs: {consolidated_result_dict['source_urls']}")
            print(f"Consolidated content for '{search_topic_exercise}' (first 500 chars):\n{consolidated_result_dict['content'][:500]}...\n")
            if len(consolidated_result_dict["content"]) > 500:
                print(f"... (Total length: {len(consolidated_result_dict['content'])} characters)")
        elif consolidated_result_dict["content"]:
             print(f"Content was returned but might be empty or partial. Length: {len(consolidated_result_dict['content'])}")

    print("\n--- WCHA functionality testing in __main__ complete ---")
    
    if app:
        print("\n--- Flask app /harvest is defined (run separately if needed) ---")
        # To run: FLASK_APP=aethercast.wcha.main flask run -p 5003
        # Example POST request (using curl or a tool like Postman):
        # curl -X POST -H "Content-Type: application/json" -d '{"topic":"ai in healthcare", "use_search":true}' http://localhost:5003/harvest
        # curl -X POST -H "Content-Type: application/json" -d '{"url":"https://en.wikipedia.org/wiki/Python_(programming_language)"}' http://localhost:5003/harvest
        # curl -X POST -H "Content-Type: application/json" -d '{"topic":"climate change"}' http://localhost:5003/harvest
