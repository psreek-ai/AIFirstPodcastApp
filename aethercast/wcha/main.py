import uuid # Retain for generate_harvest_id
import datetime # Not directly used by new functions but often useful
import logging
import json # Not directly used by new functions but often useful
import os # Added
from dotenv import load_dotenv # Added

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

# Load configuration at startup
load_wcha_configuration()

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


# --- Logging Configuration ---
logger = logging.getLogger(__name__) 
if not logger.hasHandlers(): # Ensure logger is configured before load_wcha_configuration tries to use it.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - WCHA - %(message)s')
    # Re-run load_wcha_configuration if logger was just configured, to ensure initial logs are captured.
    # This is a bit of a workaround for scripts where config loading might happen before logger setup.
    # A better pattern is to ensure logger is configured very first.
    if not wcha_config: # If config is empty because logger wasn't ready
        load_wcha_configuration()


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

# --- Existing harvest_from_url function (Modified to use Trafilatura) ---
def harvest_from_url(url: str) -> str:
    if not _IMPORTS_SUCCESSFUL_REQUESTS_BS4: # Still need requests
        error_msg = f"Cannot 'harvest_from_url' because requests library is missing: {_MISSING_IMPORT_ERROR_REQUESTS_BS4}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg

    if not _IMPORTS_SUCCESSFUL_TRAFILATURA:
        error_msg = f"WCHA Error: Trafilatura library not installed. Cannot perform advanced content extraction. ({_MISSING_IMPORT_ERROR_TRAFILATURA})"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg

    logger.info(f"[WCHA_LOGIC_WEB] Attempting to harvest content from URL: {url} using Trafilatura")
    headers = {'User-Agent': wcha_config.get('WCHA_USER_AGENT', 'AethercastContentHarvester/0.2')}
    request_timeout = wcha_config.get('WCHA_REQUEST_TIMEOUT', 10)

    try:
        response = requests.get(url, headers=headers, timeout=request_timeout)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        # Check content type - Trafilatura might handle non-HTML better, but good to be aware.
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
            logger.warning(f"[WCHA_LOGIC_WEB] Content at URL '{url}' may not be HTML (Content-Type: {content_type}). Trafilatura will attempt extraction.")

        # Use Trafilatura for extraction
        # The `url` parameter helps trafilatura resolve relative links if it were fetching, but here we pass raw content.
        # It still can be useful for some internal heuristics of trafilatura.
        extracted_text = trafilatura.extract(response.content, url=url, output_format='txt',
                                             include_comments=False, include_tables=False,
                                             favor_precision=True) # Favor quality over quantity

        if extracted_text:
            logger.info(f"[WCHA_LOGIC_WEB] Trafilatura successfully extracted {len(extracted_text)} characters from {url}.")
            return extracted_text
        else:
            # This case means trafilatura ran but decided there was no main content.
            logger.warning(f"[WCHA_LOGIC_WEB] Trafilatura extracted no content from URL: {url}. This might be a non-article page or content is not extractable.")
            return f"WCHA: Trafilatura extracted no content from URL: {url}"

    except requests.exceptions.Timeout:
        error_msg = f"Error fetching URL '{url}': Timeout after {request_timeout} seconds."
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg
    except requests.exceptions.HTTPError as e_http: # Handles 4xx/5xx errors
        error_msg = f"Failed to fetch URL '{url}'. HTTP Status code: {e_http.response.status_code}."
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg} Response: {e_http.response.text[:200]}")
        return error_msg
    except requests.exceptions.RequestException as e_req: # Other requests issues (ConnectionError etc.)
        error_msg = f"Error fetching URL '{url}': RequestException - {type(e_req).__name__} - {e_req}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg
    except Exception as e_traf: # Catch potential errors from trafilatura itself, though it's usually robust
        error_msg = f"WCHA: Trafilatura failed to extract content from URL '{url}': {type(e_traf).__name__} - {e_traf}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}", exc_info=True)
        return error_msg

# --- New get_content_for_topic function (Updated error prefixes) ---
def get_content_for_topic(topic: str, max_results_override: Optional[int] = None) -> str:
    """
    Performs a web search for the given topic, harvests content from multiple URLs,
    and consolidates the text.
    Uses WCHA_SEARCH_MAX_RESULTS from config, but can be overridden by max_results_override.
    """
    if not IMPORTS_SUCCESSFUL:
        error_msg = f"WCHA: Cannot 'get_content_for_topic' due to missing libraries: {MISSING_IMPORT_ERROR}"
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
        return f"Error during web search for topic '{topic}': {type(e).__name__}."

    if not urls:
        logger.warning(f"[WCHA_SEARCH_HARVEST] No search results found for topic: {topic}")
        return f"WCHA: No search results found for topic: {topic}"

    all_harvested_text = []
    successful_harvest_count = 0
    
    # Define error prefixes from harvest_from_url to check against
    # Note: "No paragraph text found" might become less relevant if Trafilatura is the sole method.
    harvest_error_prefixes = (
        "Error fetching URL", "Failed to fetch URL",
        # "No paragraph text found", # Original BeautifulSoup-specific, less relevant now
        "Content at URL", # Original error, might indicate non-HTML or issues before extraction
        "Cannot 'harvest_from_url'", # Original error from import failures
        "WCHA Error: Trafilatura library not installed", # New
        "WCHA: Trafilatura failed to extract content from URL", # New
        "WCHA: Trafilatura extracted no content from URL" # New
    )

    for i, url in enumerate(urls): # Iterate through found URLs
        # The ddgs.text(max_results=...) should already limit the number of URLs.
        # This loop just processes what was returned.
        logger.info(f"[WCHA_SEARCH_HARVEST] Attempting to harvest from URL ({i+1}/{len(urls)}): {url}")
        content_from_single_url = harvest_from_url(url)
        
        # Check if harvesting was successful
        # content_from_single_url should not be None and not start with any of the error prefixes
        if content_from_single_url and not any(content_from_single_url.startswith(prefix) for prefix in harvest_error_prefixes):
            all_harvested_text.append(f"Source: {url}\n{content_from_single_url}")
            successful_harvest_count += 1
            logger.info(f"[WCHA_SEARCH_HARVEST] Successfully harvested and validated content from: {url}")
        else:
            # Log the failure reason more clearly. content_from_single_url itself is the error message here.
            logger.warning(f"[WCHA_SEARCH_HARVEST] Failed to harvest valid content from URL: {url}. Reason/Response: '{content_from_single_url}'")

    if successful_harvest_count == 0:
        message = f"WCHA: Failed to harvest usable content from any of the {len(urls)} search results for topic: {topic}. Last URL error (if any): {content_from_single_url if urls else 'No URLs found'}"
        logger.warning(f"[WCHA_SEARCH_HARVEST] {message}")
        return message
    
    final_content = "\n\n---\n\n".join(all_harvested_text)
    logger.info(f"[WCHA_SEARCH_HARVEST] Consolidated content from {successful_harvest_count} sources for topic '{topic}'. Total length: {len(final_content)} chars.")
    return final_content


# --- API Endpoint (Preserved) ---
try:
    import flask
    app = flask.Flask(__name__) 

    @app.route("/harvest_content_endpoint", methods=["POST"])
    def harvest_content_api_endpoint(): 
        request_data = flask.request.get_json()
        if not request_data:
            return flask.jsonify({"error": "Invalid JSON payload"}), 400
        topic = request_data.get("topic")
        url_to_harvest = request_data.get("url") # Allow direct URL harvest via API
        use_search = request_data.get("use_search", False) # New flag for API to use get_content_for_topic

        if use_search and topic:
            logger.info(f"[WCHA_API] Received API request to search and harvest for topic: '{topic}'")
            content_result = get_content_for_topic(topic)
            # Check if search/harvest failed and return appropriate message
            if content_result.startswith("Error during web search") or \
               content_result.startswith("WCHA: No search results") or \
               content_result.startswith("WCHA: Failed to harvest usable content"):
                return flask.jsonify({"topic": topic, "error": content_result, "content": None}), 400 # Or 500 for internal errors
            return flask.jsonify({"topic": topic, "source": "web_search_ddg", "content": content_result}), 200
        elif topic: # Original mock data path
            logger.info(f"[WCHA_API] Received API request for mock topic: '{topic}'")
            content_result = harvest_content(topic) 
            if content_result.startswith("No pre-defined content found"):
                 return flask.jsonify({"topic": topic, "message": content_result, "content": None}), 200
            return flask.jsonify({"topic": topic, "source": "mock_data", "content": content_result}), 200
        elif url_to_harvest: # Direct URL harvest path
            logger.info(f"[WCHA_API] Received API request for URL: '{url_to_harvest}'")
            content_result = harvest_from_url(url_to_harvest)
            if any(content_result.startswith(prefix) for prefix in ("Error fetching URL", "Failed to fetch URL", "No paragraph text found", "Content at URL", "Cannot 'harvest_from_url'")):
                return flask.jsonify({"url": url_to_harvest, "error": content_result, "content":None}), 400
            return flask.jsonify({"url": url_to_harvest, "source": "direct_url", "content": content_result}), 200
        else:
            logger.warning("[WCHA_API] Invalid API request. 'topic' (for mock/search) or 'url' (for direct) must be provided.")
            return flask.jsonify({"error": "Invalid request, 'topic' or 'url' must be provided."}), 400
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
