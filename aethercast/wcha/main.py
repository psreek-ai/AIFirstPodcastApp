import uuid # Retain for generate_harvest_id
import datetime # Not directly used by new functions but often useful
import logging
import json # Not directly used by new functions but often useful

# --- Attempt to import web scraping and search libraries ---
# Store original IMPORTS_SUCCESSFUL and MISSING_IMPORT_ERROR for requests/bs4
_IMPORTS_SUCCESSFUL_BASE = True
_MISSING_IMPORT_ERROR_BASE = None
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as e:
    _IMPORTS_SUCCESSFUL_BASE = False
    _MISSING_IMPORT_ERROR_BASE = e
    # Define placeholder functions if imports fail
    def requests_get_placeholder(*args, **kwargs):
        raise ImportError(f"requests library is not installed. Original error: {_MISSING_IMPORT_ERROR_BASE}")
    def BeautifulSoup_placeholder(*args, **kwargs):
        raise ImportError(f"BeautifulSoup (bs4) library is not installed. Original error: {_MISSING_IMPORT_ERROR_BASE}")
    if 'requests' not in globals():
        class MockRequestsExceptions: RequestException = Exception; ConnectionError = type('ConnectionError', (RequestException,), {}); Timeout = type('Timeout', (RequestException,), {}); HTTPError = type('HTTPError', (RequestException,), {})
        class MockRequests: get = requests_get_placeholder; exceptions = MockRequestsExceptions()
        requests = MockRequests()
    if 'BeautifulSoup' not in globals(): BeautifulSoup = BeautifulSoup_placeholder

_IMPORTS_SUCCESSFUL_DDG = True
_MISSING_IMPORT_ERROR_DDG = None
try:
    from duckduckgo_search import DDGS
except ImportError as e:
    _IMPORTS_SUCCESSFUL_DDG = False
    _MISSING_IMPORT_ERROR_DDG = e
    def DDGS_placeholder(*args, **kwargs): # Placeholder for DDGS context manager
        class DummyDDGS:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_val, exc_tb): pass
            def text(self, *args, **kwargs):
                 raise ImportError(f"duckduckgo_search library is not installed. Original error: {_MISSING_IMPORT_ERROR_DDG}")
        return DummyDDGS()

# Combine import success flags and error messages
IMPORTS_SUCCESSFUL = _IMPORTS_SUCCESSFUL_BASE and _IMPORTS_SUCCESSFUL_DDG
if not IMPORTS_SUCCESSFUL:
    missing_libs = []
    if not _IMPORTS_SUCCESSFUL_BASE: missing_libs.append(str(_MISSING_IMPORT_ERROR_BASE).split("'")[1] if "'" in str(_MISSING_IMPORT_ERROR_BASE) else "requests/bs4")
    if not _IMPORTS_SUCCESSFUL_DDG: missing_libs.append(str(_MISSING_IMPORT_ERROR_DDG).split("'")[1] if "'" in str(_MISSING_IMPORT_ERROR_DDG) else "duckduckgo_search")
    MISSING_IMPORT_ERROR = f"Missing libraries: {', '.join(missing_libs)}."
else:
    MISSING_IMPORT_ERROR = None


# --- Logging Configuration ---
logger = logging.getLogger(__name__) 
if not logger.hasHandlers(): 
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - WCHA - %(message)s')


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

# --- Existing harvest_from_url function (Preserved) ---
def harvest_from_url(url: str) -> str:
    if not _IMPORTS_SUCCESSFUL_BASE: # Specifically check for requests/bs4
        error_msg = f"Cannot 'harvest_from_url' because requests/bs4 libraries are missing: {_MISSING_IMPORT_ERROR_BASE}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg

    logger.info(f"[WCHA_LOGIC_WEB] Attempting to harvest content from URL: {url}")
    headers = {'User-Agent': 'AethercastFetcher/0.1'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            error_msg = f"Failed to fetch URL '{url}'. Status code: {response.status_code}"
            logger.warning(f"[WCHA_LOGIC_WEB] {error_msg}")
            return error_msg
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type:
            error_msg = f"Content at URL '{url}' is not HTML (Content-Type: {response.headers.get('Content-Type', 'N/A')}). Skipping parsing."
            logger.warning(f"[WCHA_LOGIC_WEB] {error_msg}")
            return error_msg
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        if not paragraphs:
            message = f"No paragraph text found at URL: {url}"
            logger.info(f"[WCHA_LOGIC_WEB] {message}")
            return message
        extracted_text = "\n\n".join([para.get_text().strip() for para in paragraphs])
        logger.info(f"[WCHA_LOGIC_WEB] Successfully extracted {len(extracted_text)} characters from paragraphs at {url}.")
        return extracted_text
    except requests.exceptions.Timeout:
        error_msg = f"Error fetching URL '{url}': Timeout after 10 seconds."
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg
    except requests.exceptions.RequestException as e: # Catch other requests issues (ConnectionError, HTTPError etc.)
        error_msg = f"Error fetching URL '{url}': RequestException - {type(e).__name__} - {e}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg
    except Exception as e: 
        error_msg = f"An unexpected error occurred while processing URL '{url}': {type(e).__name__} - {e}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}", exc_info=True)
        return error_msg

# --- New get_content_for_topic function ---
def get_content_for_topic(topic: str, max_search_results: int = 3) -> str:
    """
    Performs a web search for the given topic, harvests content from multiple URLs,
    and consolidates the text.
    """
    if not IMPORTS_SUCCESSFUL:
        error_msg = f"WCHA: Cannot 'get_content_for_topic' due to missing libraries: {MISSING_IMPORT_ERROR}"
        logger.error(error_msg)
        return error_msg

    logger.info(f"[WCHA_SEARCH_HARVEST] Starting content search and harvest for topic: '{topic}' (max_results: {max_search_results})")
    
    urls = []
    try:
        with DDGS() as ddgs:
            ddgs_results = list(ddgs.text(
                keywords=topic,
                region='wt-wt',
                safesearch='moderate',
                max_results=max_search_results 
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
    harvest_error_prefixes = ("Error fetching URL", "Failed to fetch URL", "No paragraph text found", "Content at URL", "Cannot 'harvest_from_url'")

    for i, url in enumerate(urls): # Iterate through found URLs
        if i >= max_search_results: # Ensure we don't exceed max_search_results, though ddgs.text should handle it
            break
        logger.info(f"[WCHA_SEARCH_HARVEST] Attempting to harvest from URL ({i+1}/{len(urls)}): {url}")
        content_from_single_url = harvest_from_url(url)
        
        if content_from_single_url and not any(content_from_single_url.startswith(prefix) for prefix in harvest_error_prefixes):
            all_harvested_text.append(f"Source: {url}\n{content_from_single_url}")
            successful_harvest_count += 1
            logger.info(f"[WCHA_SEARCH_HARVEST] Successfully harvested and validated content from: {url}")
        else:
            logger.warning(f"[WCHA_SEARCH_HARVEST] Failed to harvest valid content or error returned from URL: {url}. Response: '{content_from_single_url[:100]}...'")

    if successful_harvest_count == 0:
        message = f"WCHA: Failed to harvest usable content from any of the {len(urls)} search results for topic: {topic}"
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
        print(f"Requesting consolidated content for topic: '{search_topic}' (max 2 results for test)")
        consolidated_content = get_content_for_topic(search_topic, max_search_results=2)
        
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
