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
def get_content_for_topic(topic: str, max_results_override: Optional[int] = None) -> str: # Return type remains string (aggregated content or error message)
    """
    Performs a web search for the given topic, harvests content from multiple URLs,
    and consolidates the text.
    Uses WCHA_SEARCH_MAX_RESULTS from config, but can be overridden by max_results_override.
    """
    if not IMPORTS_SUCCESSFUL: # This checks all required libs including Trafilatura now
        error_msg = f"{ERROR_WCHA_LIB_MISSING} {MISSING_IMPORT_ERROR}"
        logger.error(error_msg)
        return error_msg

    # Determine max_search_results: use override if provided, else use config, else default to 3.
    if max_results_override is not None:
        actual_max_search_results = max_results_override
    else:
        actual_max_search_results = wcha_config.get('WCHA_SEARCH_MAX_RESULTS', 3)

    logger.info(f"[WCHA_SEARCH_HARVEST] Starting content search and harvest for topic: '{topic}' (max_results: {actual_max_search_results})")
    
    urls = []
    try:
        with DDGS() as ddgs:
            ddgs_results = list(ddgs.text(
                keywords=topic,
                region='wt-wt',
                safesearch='moderate',
                max_results=actual_max_search_results
            ))
            if ddgs_results:
                urls = [r['href'] for r in ddgs_results if r.get('href')]
        logger.info(f"[WCHA_SEARCH_HARVEST] Found {len(urls)} URLs for topic '{topic}': {urls}")
    except Exception as e:
        logger.error(f"[WCHA_SEARCH_HARVEST] Error during duckduckgo_search for '{topic}': {type(e).__name__} - {e}")
        return f"{ERROR_WCHA_SEARCH_FAILED} '{topic}': {type(e).__name__}." # Appended topic for clarity

    if not urls:
        logger.warning(f"[WCHA_SEARCH_HARVEST] No search results found for topic: {topic}")
        return f"{ERROR_WCHA_NO_SEARCH_RESULTS}: {topic}"

    all_harvested_text = []
    successful_harvest_count = 0
    last_harvest_error_details = "No specific harvest error recorded." # For overall error reporting
    min_content_length_for_aggregation = 150 # Define or get from config if needed

    for i, url in enumerate(urls):
        logger.info(f"[WCHA_SEARCH_HARVEST] Attempting to harvest from URL ({i+1}/{len(urls)}): {url}")
        # Pass min_length to harvest_from_url; it will log if content is too short but still return it.
        # Here, we can decide if short content should be part of aggregation.
        harvest_result = harvest_from_url(url, min_length=min_content_length_for_aggregation)
        
        if harvest_result.get("content"):
            # Optional: Add a check here if truly short content should be skipped for aggregation
            # if len(harvest_result["content"]) < min_content_length_for_aggregation:
            #     logger.warning(f"[WCHA_SEARCH_HARVEST] Content from {url} was too short ({len(harvest_result['content'])} chars) and will be skipped for aggregation.")
            #     last_harvest_error_details = f"URL: {url}, Type: {WCHA_ERROR_TYPE_CONTENT_TOO_SHORT}, Message: Content too short."
            # else:
            all_harvested_text.append(f"Source: {harvest_result['url']}\n{harvest_result['content']}")
            successful_harvest_count += 1
            logger.info(f"[WCHA_SEARCH_HARVEST] Successfully harvested and validated content from: {url}")
        else:
            error_type = harvest_result.get("error_type", WCHA_ERROR_TYPE_UNKNOWN)
            error_message = harvest_result.get("error_message", "Unknown error during harvest.")
            last_harvest_error_details = f"URL: {url}, Type: {error_type}, Message: {error_message}"
            logger.warning(f"[WCHA_SEARCH_HARVEST] Failed to harvest valid content from URL: {url}. Type: {error_type}, Reason: {error_message}")

    if successful_harvest_count == 0:
        message = f"{ERROR_WCHA_HARVEST_ALL_FAILED} {len(urls)} search results for topic: {topic}. Last error: {last_harvest_error_details}"
        logger.warning(f"[WCHA_SEARCH_HARVEST] {message}")
        return message # Return the detailed error message
    
    final_content = "\n\n---\n\n".join(all_harvested_text)
    logger.info(f"[WCHA_SEARCH_HARVEST] Consolidated content from {successful_harvest_count} sources for topic '{topic}'. Total length: {len(final_content)} chars.")
    return final_content


# --- API Endpoint (Preserved) ---
try:
    import flask
    app = flask.Flask(__name__) 

    @app.route("/harvest_content_endpoint", methods=["POST"])
    def harvest_content_api_endpoint():
        try:
            request_data = flask.request.get_json()
            if not request_data:
                return flask.jsonify({"error": ENDPOINT_ERROR_INVALID_PAYLOAD, "details": "Invalid or missing JSON payload."}), 400

            topic = request_data.get("topic")
            url_to_harvest = request_data.get("url")
            use_search = request_data.get("use_search", False) # For get_content_for_topic
            # Optional: allow overriding timeout and min_length via request for direct URL harvest
            timeout_override = request_data.get("timeout")
            min_length_override = request_data.get("min_length")


            if use_search and topic:
                logger.info(f"[WCHA_API] Received API request to search and harvest for topic: '{topic}'")
                # get_content_for_topic still returns a string (content or error message)
                content_result_str = get_content_for_topic(topic)
                # Check if the returned string indicates an error based on its prefixes
                # (This part remains similar as get_content_for_topic's return signature wasn't changed to dict for this subtask)
                if any(content_result_str.startswith(prefix) for prefix in [ERROR_WCHA_LIB_MISSING, ERROR_WCHA_SEARCH_FAILED, ERROR_WCHA_NO_SEARCH_RESULTS, ERROR_WCHA_HARVEST_ALL_FAILED]):
                    return flask.jsonify({"topic": topic, "error": content_result_str, "content": None}), 400 # Or 500 depending on error
                return flask.jsonify({"topic": topic, "source": "web_search_ddg", "content": content_result_str}), 200

            elif url_to_harvest:
                logger.info(f"[WCHA_API] Received API request for direct URL harvest: '{url_to_harvest}'")
                harvest_params = {}
                if timeout_override is not None: harvest_params["timeout"] = timeout_override
                if min_length_override is not None: harvest_params["min_length"] = min_length_override

                harvest_result_dict = harvest_from_url(url_to_harvest, **harvest_params)

                if harvest_result_dict.get("content"):
                    return flask.jsonify({
                        "url": url_to_harvest,
                        "source": "direct_url",
                        "content": harvest_result_dict["content"]
                    }), 200
                else:
                    error_type = harvest_result_dict.get("error_type", WCHA_ERROR_TYPE_UNKNOWN)
                    error_message = harvest_result_dict.get("error_message", "Unknown error during harvest.")
                    status_code = 500 # Default
                    if error_type == WCHA_ERROR_TYPE_LIB_MISSING: status_code = 503
                    elif error_type == WCHA_ERROR_TYPE_FETCH: status_code = 502
                    elif error_type == WCHA_ERROR_TYPE_NO_CONTENT: status_code = 404
                    # WCHA_ERROR_TYPE_EXTRACTION or WCHA_ERROR_TYPE_UNKNOWN remains 500

                    return flask.jsonify({
                        "url": url_to_harvest,
                        "error": error_message,
                        "error_type": error_type,
                        "content": None
                    }), status_code

            elif topic: # Fallback to mock data if no use_search and no url, but topic is present
                logger.info(f"[WCHA_API] Received API request for mock topic (no use_search or url): '{topic}'")
                content_result_mock = harvest_content(topic) # Mock function
                if content_result_mock.startswith("No pre-defined content found"):
                     return flask.jsonify({"topic": topic, "message": content_result_mock, "content": None}), 200 # Or 404
                return flask.jsonify({"topic": topic, "source": "mock_data", "content": content_result_mock}), 200

            else:
                logger.warning("[WCHA_API] Invalid API request. 'url' or 'topic' (with use_search=true for web search, or alone for mock) must be provided.")
                return flask.jsonify({"error": ENDPOINT_ERROR_MISSING_FIELDS, "details": "'topic' (with use_search=true) or 'url' must be provided."}), 400

        except Exception as e:
            logger.error(f"Unexpected error in /harvest_content_endpoint: {e}", exc_info=True)
            return flask.jsonify({"error": ENDPOINT_ERROR_INTERNAL_SERVER, "details": str(e)}), 500

except ImportError:
    app = None 
    logger.info("Flask not installed. API endpoint /harvest_content_endpoint will not be available.")


# --- Example Usage (Updated) ---
if __name__ == "__main__":
    print("--- Testing WCHA Functionality ---")

    # Check if all imports were successful for full functionality
    if not IMPORTS_SUCCESSFUL:
        print(f"\nWARNING: Some required libraries are missing: {MISSING_IMPORT_ERROR}")
        print("Functionality of 'harvest_from_url' and 'get_content_for_topic' will be limited or fail.\n")

    # 1. Test harvest_content (mock data)
    print("\n--- Testing harvest_content (mock data) ---")
    existing_topic = "ai in healthcare"
    print(f"Requesting mock content for topic: '{existing_topic}'")
    mock_data_content = harvest_content(existing_topic)
    print(f"Content for '{existing_topic}' (first 100 chars): {mock_data_content[:100]}...\n")

    # 2. Test harvest_from_url (if imports allow)
    print("\n--- Testing harvest_from_url (single URL) ---")
    if not _IMPORTS_SUCCESSFUL_BASE: # Specifically requests/bs4
        print(f"Skipping harvest_from_url test as requests/bs4 are missing: {_MISSING_IMPORT_ERROR_BASE}\n")
    else:
        # Test with a known URL, e.g., Wikipedia page on "Python (programming language)"
        python_wiki_url = "https://en.wikipedia.org/wiki/Python_(programming_language)"
        print(f"Requesting content from URL: '{python_wiki_url}'")
        url_content = harvest_from_url(python_wiki_url)
        if any(url_content.startswith(prefix) for prefix in ("Error fetching URL", "Failed to fetch URL", "No paragraph text found", "Content at URL")):
            print(f"Error/Warning from harvest_from_url: {url_content}\n")
        else:
            print(f"Content from '{python_wiki_url}' (first 200 chars): {url_content[:200]}...\n")
            
    # 3. Test get_content_for_topic (if all imports allow)
    print("\n--- Testing get_content_for_topic (web search & consolidation) ---")
    if not IMPORTS_SUCCESSFUL: # Checks all: requests, bs4, duckduckgo_search
        print(f"Skipping get_content_for_topic test as libraries are missing: {MISSING_IMPORT_ERROR}\n")
    else:
        search_topic = "benefits of regular exercise"
        # Test with override for max_results
        test_max_results = 2
        print(f"Requesting consolidated content for topic: '{search_topic}' (max {test_max_results} results for test)")
        consolidated_content = get_content_for_topic(search_topic, max_results_override=test_max_results)
        
        # Check if the result is an error message from the function itself
        if any(consolidated_content.startswith(prefix) for prefix in ("WCHA: Cannot 'get_content_for_topic'", "Error during web search", "WCHA: No search results", "WCHA: Failed to harvest usable content")):
            print(f"Error/Warning from get_content_for_topic: {consolidated_content}\n")
        else:
            print(f"Consolidated content for '{search_topic}' (first 500 chars):\n{consolidated_content[:500]}...\n")
            if len(consolidated_content) > 500:
                print(f"... (Total length: {len(consolidated_content)} characters)")

    print("\n--- WCHA functionality testing in __main__ complete ---")
    
    if app:
        print("\n--- Flask app /harvest_content_endpoint is defined (run separately if needed) ---")
        # To run: FLASK_APP=aethercast.wcha.main flask run -p 5003
        # Example POST request (using curl or a tool like Postman):
        # curl -X POST -H "Content-Type: application/json" -d '{"topic":"ai in healthcare", "use_search":true}' http://localhost:5003/harvest_content_endpoint
        # curl -X POST -H "Content-Type: application/json" -d '{"url":"https://en.wikipedia.org/wiki/Python_(programming_language)"}' http://localhost:5003/harvest_content_endpoint
        # curl -X POST -H "Content-Type: application/json" -d '{"topic":"climate change"}' http://localhost:5003/harvest_content_endpoint
