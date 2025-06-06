import uuid # Retain for generate_harvest_id
import datetime # Not directly used by new functions but often useful
import logging
import json # Not directly used by new functions but often useful
import os # Added
from dotenv import load_dotenv # Added
from typing import Optional # Added for type hinting

# --- Load Environment Variables ---
load_dotenv() # Added

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
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - WCHA - %(message)s')

# Load configuration at startup
load_wcha_configuration()

# --- Error Message Constants ---
# Prefixes/messages used for returning errors from core functions
ERROR_PREFIX_HARVEST_FAILED_FETCH = "Error fetching URL" # Covers timeout, http, request exceptions # Retained for now
ERROR_PREFIX_HARVEST_TRAFILATURA_FAILED = "WCHA: Trafilatura failed to extract content from URL" # Retained for now
ERROR_PREFIX_HARVEST_TRAFILATURA_NO_CONTENT = "WCHA: Trafilatura extracted no content from URL" # Retained for now
ERROR_PREFIX_HARVEST_LIB_MISSING_TRAFILATURA = "WCHA Error: Trafilatura library not installed" # Retained for now
ERROR_PREFIX_HARVEST_LIB_MISSING_REQUESTS = "Cannot 'harvest_from_url' because requests library is missing" # Retained for now

ERROR_WCHA_LIB_MISSING = "WCHA: Cannot 'get_content_for_topic' due to missing libraries:" # Note: full message includes details
ERROR_WCHA_SEARCH_FAILED = "Error during web search for topic" # Note: full message includes details
ERROR_WCHA_NO_SEARCH_RESULTS = "WCHA: No search results found for topic" # Note: full message includes details
ERROR_WCHA_HARVEST_ALL_FAILED = "WCHA: Failed to harvest usable content from any of the" # Note: full message includes details

# --- New Error Type Constants for Structured Error Reporting in harvest_from_url ---
WCHA_ERROR_TYPE_LIB_MISSING = "library_missing"
WCHA_ERROR_TYPE_FETCH = "fetch_error"
WCHA_ERROR_TYPE_EXTRACTION = "extraction_error"
WCHA_ERROR_TYPE_NO_CONTENT = "no_content_extracted"
WCHA_ERROR_TYPE_CONTENT_TOO_SHORT = "content_too_short" # For future use with min_length strictness
WCHA_ERROR_TYPE_UNKNOWN = "unknown_harvest_error"


# For the test endpoint
ENDPOINT_ERROR_INVALID_PAYLOAD = "INVALID_JSON_PAYLOAD_WCHA" # Make specific if used only here
ENDPOINT_ERROR_MISSING_FIELDS = "MISSING_REQUIRED_FIELDS_WCHA"
ENDPOINT_ERROR_INTERNAL_SERVER = "INTERNAL_SERVER_ERROR_WCHA"


# --- Attempt to import web scraping and search libraries ---
_IMPORTS_SUCCESSFUL_REQUESTS_BS4 = True
_MISSING_IMPORT_ERROR_REQUESTS_BS4 = None
try:
    import requests
    from bs4 import BeautifulSoup # Retained for now, though trafilatura might reduce its direct need
except ImportError as e:
    _IMPORTS_SUCCESSFUL_REQUESTS_BS4 = False
    _MISSING_IMPORT_ERROR_REQUESTS_BS4 = str(e)
    # Placeholders for requests and BeautifulSoup if they fail
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


# Combine import success flags and error messages
IMPORTS_SUCCESSFUL_CORE = _IMPORTS_SUCCESSFUL_REQUESTS_BS4 and _IMPORTS_SUCCESSFUL_DDG
IMPORTS_SUCCESSFUL_ADVANCED_EXTRACTION = _IMPORTS_SUCCESSFUL_TRAFILATURA

# IMPORTS_SUCCESSFUL for get_content_for_topic now depends on TRAFILATURA as well
IMPORTS_SUCCESSFUL = IMPORTS_SUCCESSFUL_CORE and IMPORTS_SUCCESSFUL_ADVANCED_EXTRACTION

MISSING_IMPORT_ERROR = ""
if not IMPORTS_SUCCESSFUL:
    missing_libs_list = []
    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4: missing_libs_list.append(f"requests/bs4 ({_MISSING_IMPORT_ERROR_REQUESTS_BS4})")
    if not _IMPORTS_SUCCESSFUL_DDG: missing_libs_list.append(f"duckduckgo_search ({_MISSING_IMPORT_ERROR_DDG})")
    if not _IMPORTS_SUCCESSFUL_TRAFILATURA: missing_libs_list.append(f"trafilatura ({_MISSING_IMPORT_ERROR_TRAFILATURA})")
    MISSING_IMPORT_ERROR = f"Missing libraries: {'; '.join(missing_libs_list)}."


# --- Hardcoded Data for Simulation (Preserved) ---
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

# --- Helper Functions (Preserved) ---
def generate_harvest_id() -> str:
    return f"harvest_{uuid.uuid4().hex[:10]}"

# --- Existing harvest_content function (Preserved) ---
def harvest_content(topic: str) -> str: # This is the mock function, returns string
    logger.info(f"[WCHA_LOGIC_MOCK] harvest_content (mock data) called with topic: '{topic}'")
    normalized_topic = topic.lower().strip() if topic else ""
    if normalized_topic in SIMULATED_WEB_CONTENT:
        content = SIMULATED_WEB_CONTENT[normalized_topic]
        logger.info(f"[WCHA_LOGIC_MOCK] Found mock content for topic: '{topic}'")
        return content
    else:
        logger.warning(f"[WCHA_LOGIC_MOCK] No pre-defined mock content found for topic: '{topic}'.")
        return f"No pre-defined content found for topic: {topic}"

# --- Refactored harvest_from_url function ---
def harvest_from_url(url: str, min_length: int = 150) -> dict: # min_length default matches old constant for now
    # Using wcha_config for timeout and user_agent
    request_timeout = wcha_config.get('WCHA_REQUEST_TIMEOUT', 10)
    headers = {'User-Agent': wcha_config.get('WCHA_USER_AGENT', 'AethercastContentHarvester/0.2')}

    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4: # Check for requests
        error_msg = f"Required library missing: requests/bs4 ({_MISSING_IMPORT_ERROR_REQUESTS_BS4})"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_LIB_MISSING, "error_message": error_msg}

    if not _IMPORTS_SUCCESSFUL_TRAFILATURA:
        error_msg = f"Required library missing: trafilatura ({_MISSING_IMPORT_ERROR_TRAFILATURA})"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_LIB_MISSING, "error_message": error_msg}

    logger.info(f"[WCHA_LOGIC_WEB] Attempting to harvest content from URL: {url} using Trafilatura")

    try:
        response = requests.get(url, headers=headers, timeout=request_timeout)
        response.raise_for_status()

        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
            logger.warning(f"[WCHA_LOGIC_WEB] Content at URL '{url}' may not be HTML (Content-Type: {content_type}). Trafilatura will attempt extraction.")

        extracted_text = trafilatura.extract(response.content, url=url, output_format='txt',
                                             include_comments=False, include_tables=False,
                                             favor_precision=True)

        if extracted_text:
            if len(extracted_text) < min_length:
                # Optional: Treat as error if too short, or return content as is.
                # For now, returning content as is, but logging a warning.
                logger.warning(f"[WCHA_LOGIC_WEB] Content from {url} is shorter ({len(extracted_text)} chars) than min_length ({min_length} chars).")
                # If it should be an error:
                # return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_CONTENT_TOO_SHORT,
                #         "error_message": f"Extracted content length ({len(extracted_text)}) is less than minimum ({min_length})."}
            logger.info(f"[WCHA_LOGIC_WEB] Trafilatura successfully extracted {len(extracted_text)} characters from {url}.")
            return {"url": url, "content": extracted_text, "error_type": None, "error_message": None}
        else:
            logger.warning(f"[WCHA_LOGIC_WEB] Trafilatura extracted no content from URL: {url}.")
            return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_NO_CONTENT, "error_message": "Trafilatura extracted no content."}

    except requests.exceptions.Timeout:
        error_msg = f"Timeout after {request_timeout} seconds while fetching '{url}'."
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_FETCH, "error_message": error_msg}
    except requests.exceptions.HTTPError as e_http:
        error_msg = f"HTTP Status {e_http.response.status_code} while fetching '{url}'."
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg} Response: {e_http.response.text[:200]}")
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_FETCH, "error_message": error_msg}
    except requests.exceptions.RequestException as e_req:
        error_msg = f"RequestException ({type(e_req).__name__}) while fetching '{url}': {e_req}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_FETCH, "error_message": error_msg}
    except Exception as e_traf: # Catch potential errors from trafilatura itself
        error_msg = f"Trafilatura processing failed for '{url}': {type(e_traf).__name__} - {e_traf}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}", exc_info=True)
        return {"url": url, "content": None, "error_type": WCHA_ERROR_TYPE_EXTRACTION, "error_message": error_msg}

# --- Refactored get_content_for_topic function ---
def get_content_for_topic(topic: str, max_results_override: Optional[int] = None) -> dict:
    """
    Performs a web search for the given topic, harvests content from multiple URLs,
    and consolidates the text. Returns a dictionary with status, content, source_urls, and message.
    Uses WCHA_SEARCH_MAX_RESULTS from config, but can be overridden by max_results_override.
    """
    if not IMPORTS_SUCCESSFUL:
        error_msg = f"{ERROR_WCHA_LIB_MISSING} {MISSING_IMPORT_ERROR}"
        logger.error(error_msg)
        return {"status": "failure", "content": None, "source_urls": [], "message": error_msg}

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
        logger.error(f"[WCHA_SEARCH_HARVEST] {error_msg}")
        return {"status": "failure", "content": None, "source_urls": [], "message": error_msg}

    if not search_urls:
        message = f"{ERROR_WCHA_NO_SEARCH_RESULTS}: {topic}"
        logger.warning(f"[WCHA_SEARCH_HARVEST] {message}")
        return {"status": "failure", "content": None, "source_urls": [], "message": message}

    all_harvested_content_parts = []
    successfully_harvested_urls = []
    failed_harvest_details = []
    min_content_length_for_aggregation = wcha_config.get('WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION', 150) # Example config

    for i, url in enumerate(search_urls):
        logger.info(f"[WCHA_SEARCH_HARVEST] Attempting to harvest from URL ({i+1}/{len(search_urls)}): {url}")
        harvest_result = harvest_from_url(url, min_length=min_content_length_for_aggregation)

        if harvest_result.get("content"):
            # Content is returned even if short, decision to aggregate is here
            if len(harvest_result["content"]) >= min_content_length_for_aggregation:
                all_harvested_content_parts.append(f"Source: {harvest_result['url']}\n{harvest_result['content']}")
                successfully_harvested_urls.append(harvest_result['url'])
                logger.info(f"[WCHA_SEARCH_HARVEST] Successfully harvested and validated content from: {url}")
            else:
                # Content was harvested but deemed too short for aggregation based on WCHA_MIN_CONTENT_LENGTH_FOR_AGGREGATION
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


# --- API Endpoint (Preserved) ---
try:
    import flask
    app = flask.Flask(__name__) 

    @app.route("/harvest", methods=["POST"]) # Renamed endpoint
    def harvest_api_endpoint():
        try:
            request_data = flask.request.get_json()
            if not request_data:
                return flask.jsonify({"error_code": "WCHA_INVALID_PAYLOAD", "message": "Invalid or missing JSON payload."}), 400 # Standardized error

            topic = request_data.get("topic")
            url_to_harvest = request_data.get("url")
            use_search = request_data.get("use_search", False)
            max_results_override = request_data.get("max_results") # For get_content_for_topic

            # Optional: allow overriding timeout and min_length via request for direct URL harvest
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

                result_dict = get_content_for_topic(topic, **harvest_params_for_search)

                status_code = 500 # Default for failure
                if result_dict["status"] == "success":
                    status_code = 200
                elif result_dict["message"].startswith(ERROR_WCHA_LIB_MISSING):
                    status_code = 503 # Service unavailable
                elif result_dict["message"].startswith(ERROR_WCHA_NO_SEARCH_RESULTS):
                    status_code = 404 # Not found (for the topic)
                elif result_dict["message"].startswith(ERROR_WCHA_SEARCH_FAILED):
                    status_code = 502 # Bad Gateway (search provider failed)
                # ERROR_WCHA_HARVEST_ALL_FAILED can remain 500 or be more specific e.g. 207 Multi-Status if some info is there

                return flask.jsonify(result_dict), status_code

            elif url_to_harvest:
                logger.info(f"[WCHA_API] Received API request for direct URL harvest: '{url_to_harvest}'")
                harvest_params = {}
                if timeout_override is not None:
                    try: harvest_params["timeout"] = int(timeout_override)
                    except ValueError: logger.warning(f"Invalid timeout override: {timeout_override}")
                if min_length_override is not None:
                    try: harvest_params["min_length"] = int(min_length_override)
                    except ValueError: logger.warning(f"Invalid min_length override: {min_length_override}")

                # harvest_from_url already returns a dict, but we need to ensure it aligns with the new overall structure.
                # For now, we'll adapt its output slightly to fit the status/content/source_urls/message pattern.
                # Ideally, harvest_from_url would also be refactored to this standard.
                direct_harvest_result = harvest_from_url(url_to_harvest, **harvest_params)

                if direct_harvest_result.get("content"):
                    # Successfully got content from the single URL
                    api_response = {
                        "status": "success",
                        "content": direct_harvest_result["content"],
                        "source_urls": [url_to_harvest], # Only one URL in this case
                        "message": f"Successfully harvested content from URL: {url_to_harvest}"
                    }
                    return flask.jsonify(api_response), 200
                else:
                    # Failed to get content from the single URL
                    error_message_detail = direct_harvest_result.get("error_message", "Unknown error during direct URL harvest.")
                    api_response = {
                        "status": "failure",
                        "content": None,
                        "source_urls": [],
                        "message": f"Failed to harvest content from URL: {url_to_harvest}. Reason: {error_message_detail}"
                    }
                    # Determine status code based on error_type from harvest_from_url
                    error_type = direct_harvest_result.get("error_type")
                    status_code = 500 # Default
                    if error_type == WCHA_ERROR_TYPE_LIB_MISSING: status_code = 503
                    elif error_type == WCHA_ERROR_TYPE_FETCH: status_code = 502 # Or 404 if specific like "Not Found"
                    elif error_type == WCHA_ERROR_TYPE_NO_CONTENT: status_code = 404 # Or 200 with empty content if preferred
                    return flask.jsonify(api_response), status_code

            elif topic: # Fallback to mock data if no use_search and no url, but topic is present
                logger.info(f"[WCHA_API] Received API request for mock topic (no use_search or url): '{topic}'")
                content_result_mock_str = harvest_content(topic) # Mock function returns string
                if content_result_mock_str.startswith("No pre-defined content found"):
                    return flask.jsonify({
                        "status": "success", # Or "failure" if no mock data is considered an error
                        "content": None,
                        "source_urls": ["mock_data_source"], # Placeholder source
                        "message": content_result_mock_str
                        }), 200 # Or 404 if "not found"
                return flask.jsonify({
                    "status": "success",
                    "content": content_result_mock_str,
                    "source_urls": ["mock_data_source"],
                    "message": f"Mock content provided for topic: {topic}"
                    }), 200
            else:
                logger.warning("[WCHA_API] Invalid API request. 'url' or 'topic' (with use_search=true for web search, or alone for mock) must be provided.")
                return flask.jsonify({"error_code": "WCHA_MISSING_PARAMETERS", "message": "Invalid input", "details": "'topic' (with use_search=true) or 'url' must be provided."}), 400

        except Exception as e:
            logger.error(f"Unexpected error in /harvest: {e}", exc_info=True) # Updated endpoint name in log
            return flask.jsonify({"error_code": "WCHA_INTERNAL_SERVER_ERROR", "message": "Internal server error", "details": str(e)}), 500

except ImportError:
    app = None 
    logger.info("Flask not installed. API endpoint /harvest will not be available.") # Updated endpoint name


# --- Example Usage (Updated) ---
if __name__ == "__main__":
    print("--- Testing WCHA Functionality ---")

    # Check if all imports were successful for full functionality
    if not IMPORTS_SUCCESSFUL:
        print(f"\nWARNING: Some required libraries are missing: {MISSING_IMPORT_ERROR}")
        print("Functionality of 'harvest_from_url' and 'get_content_for_topic' will be limited or fail.\n")

    # 1. Test harvest_content (mock data) - this is the old simple mock, kept for basic check
    print("\n--- Testing harvest_content (mock data) ---")
    existing_topic_mock = "ai in healthcare"
    print(f"Requesting mock content for topic: '{existing_topic_mock}'")
    mock_data_content_str = harvest_content(existing_topic_mock) # Original mock function
    print(f"Content for '{existing_topic_mock}' (first 100 chars): {mock_data_content_str[:100]}...\n")

    # 2. Test harvest_from_url (if imports allow) - this returns a dict now
    print("\n--- Testing harvest_from_url (single URL) ---")
    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4 or not _IMPORTS_SUCCESSFUL_TRAFILATURA:
        missing_libs_harvest_url = []
        if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4: missing_libs_harvest_url.append("requests/bs4")
        if not _IMPORTS_SUCCESSFUL_TRAFILATURA: missing_libs_harvest_url.append("trafilatura")
        print(f"Skipping harvest_from_url test as libraries are missing: {', '.join(missing_libs_harvest_url)}\n")
    else:
        python_wiki_url = "https://en.wikipedia.org/wiki/Python_(programming_language)"
        print(f"Requesting content from URL: '{python_wiki_url}'")
        url_harvest_result = harvest_from_url(python_wiki_url) # Returns dict
        if url_harvest_result.get("content"):
            print(f"Content from '{python_wiki_url}' (first 200 chars): {url_harvest_result['content'][:200]}...\n")
        else:
            print(f"Error/Warning from harvest_from_url: {url_harvest_result.get('error_message')}\n")
            
    # 3. Test get_content_for_topic (if all imports allow) - this now returns a dict
    print("\n--- Testing get_content_for_topic (web search & consolidation) ---")
    if not IMPORTS_SUCCESSFUL: # Checks all: requests, bs4, duckduckgo_search, trafilatura
        print(f"Skipping get_content_for_topic test as libraries are missing: {MISSING_IMPORT_ERROR}\n")
    else:
        search_topic_exercise = "benefits of regular exercise"
        test_max_results_exercise = 2 # Test with override for max_results
        print(f"Requesting consolidated content for topic: '{search_topic_exercise}' (max {test_max_results_exercise} results for test)")
        
        consolidated_result_dict = get_content_for_topic(search_topic_exercise, max_results_override=test_max_results_exercise)

        print(f"Status: {consolidated_result_dict['status']}")
        print(f"Message: {consolidated_result_dict['message']}")
        if consolidated_result_dict["status"] == "success" and consolidated_result_dict["content"]:
            print(f"Source URLs: {consolidated_result_dict['source_urls']}")
            print(f"Consolidated content for '{search_topic_exercise}' (first 500 chars):\n{consolidated_result_dict['content'][:500]}...\n")
            if len(consolidated_result_dict["content"]) > 500:
                print(f"... (Total length: {len(consolidated_result_dict['content'])} characters)")
        elif consolidated_result_dict["content"]: # Might be success but empty content if all sources were too short
             print(f"Content was returned but might be empty or partial. Length: {len(consolidated_result_dict['content'])}")
        # Error or no content already covered by message

    print("\n--- WCHA functionality testing in __main__ complete ---")
    
    if app:
        print("\n--- Flask app /harvest is defined (run separately if needed) ---") # Updated endpoint name
        # To run: FLASK_APP=aethercast.wcha.main flask run -p 5003
        # Example POST request (using curl or a tool like Postman):
        # curl -X POST -H "Content-Type: application/json" -d '{"topic":"ai in healthcare", "use_search":true}' http://localhost:5003/harvest
        # curl -X POST -H "Content-Type: application/json" -d '{"url":"https://en.wikipedia.org/wiki/Python_(programming_language)"}' http://localhost:5003/harvest
        # curl -X POST -H "Content-Type: application/json" -d '{"topic":"climate change"}' http://localhost:5003/harvest
