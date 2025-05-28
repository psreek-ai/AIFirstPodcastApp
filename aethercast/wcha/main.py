import uuid # Retain for generate_harvest_id
import datetime # Not directly used by new functions but often useful
import logging
import json # Not directly used by new functions but often useful

# --- Attempt to import web scraping libraries ---
try:
    import requests
    from bs4 import BeautifulSoup
    IMPORTS_SUCCESSFUL = True
except ImportError as e:
    IMPORTS_SUCCESSFUL = False
    MISSING_IMPORT_ERROR = e
    # Define placeholder functions if imports fail, so the rest of the file can load
    # and the original harvest_content can still work.
    # These placeholders will raise an error if called, indicating the missing dependency.
    def requests_get_placeholder(*args, **kwargs):
        raise ImportError(f"requests library is not installed. Please install it to use web harvesting features. Original error: {MISSING_IMPORT_ERROR}")

    def BeautifulSoup_placeholder(*args, **kwargs):
        raise ImportError(f"BeautifulSoup (bs4) library is not installed. Please install it to use web harvesting features. Original error: {MISSING_IMPORT_ERROR}")

    # Check which import failed and assign placeholders accordingly.
    # This logic is a bit complex to ensure the script remains parsable even if one is missing.
    if 'requests' not in globals():
        # This means 'import requests' failed at the top level.
        # Create a mock 'requests' object with a 'get' method and an 'exceptions' attribute.
        class MockRequestsExceptions:
            RequestException = Exception # Base for other request exceptions
            ConnectionError = type('ConnectionError', (RequestException,), {})
            Timeout = type('Timeout', (RequestException,), {})
            HTTPError = type('HTTPError', (RequestException,), {})

        class MockRequests:
            def get(self, *args, **kwargs):
                return requests_get_placeholder(*args, **kwargs)
            exceptions = MockRequestsExceptions()
        
        requests = MockRequests()

    if 'BeautifulSoup' not in globals():
        # This means 'from bs4 import BeautifulSoup' failed.
        BeautifulSoup = BeautifulSoup_placeholder


# --- Logging Configuration ---
# Ensure logger name is distinct if other modules also configure root logger
logger = logging.getLogger(__name__) # Use module-specific logger
if not logger.hasHandlers(): # Avoid adding multiple handlers if script re-run in some contexts
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - WCHA - %(message)s')


# --- Hardcoded Data for Simulation (Preserved) ---

# --- Hardcoded Data for Simulation ---
# This data simulates fetched and processed content from the web.
# For simplicity, keys are topics and values are multi-line content strings.
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

# --- Helper Functions (Kept generate_harvest_id for now, though not strictly used by new harvest_content) ---

def generate_harvest_id() -> str:
    """Generates a unique ID for the harvest operation."""
    return f"harvest_{uuid.uuid4().hex[:10]}"

def harvest_content(topic: str) -> str:
    """
    Simulates web content harvesting based on a topic.
    Retrieves mock content from the SIMULATED_WEB_CONTENT dictionary.
    """
    # Use the module-specific logger
    logger.info(f"[WCHA_LOGIC_MOCK] harvest_content (mock data) called with topic: '{topic}'")
    normalized_topic = topic.lower().strip() if topic else ""

    if normalized_topic in SIMULATED_WEB_CONTENT:
        content = SIMULATED_WEB_CONTENT[normalized_topic]
        logger.info(f"[WCHA_LOGIC_MOCK] Found mock content for topic: '{topic}'")
        return content
    else:
        logger.warning(f"[WCHA_LOGIC_MOCK] No pre-defined mock content found for topic: '{topic}'.")
        return f"No pre-defined content found for topic: {topic}"

# --- New harvest_from_url function ---
def harvest_from_url(url: str) -> str:
    """
    Fetches content from the given URL, parses HTML, and extracts text from paragraph tags.
    """
    if not IMPORTS_SUCCESSFUL:
        error_msg = f"Cannot 'harvest_from_url' because required libraries are missing: {MISSING_IMPORT_ERROR}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg

    logger.info(f"[WCHA_LOGIC_WEB] Attempting to harvest content from URL: {url}")
    headers = {'User-Agent': 'AethercastFetcher/0.1'}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        logger.debug(f"[WCHA_LOGIC_WEB] Response status code for {url}: {response.status_code}")
        # Check for non-200 status codes first
        if response.status_code != 200:
            error_msg = f"Failed to fetch URL '{url}'. Status code: {response.status_code}"
            logger.warning(f"[WCHA_LOGIC_WEB] {error_msg}")
            return error_msg

        # Proceed to parse content if status is OK
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
            
        # Join text content of paragraphs, ensuring spaces between them.
        # Using strip() on each paragraph's text to remove leading/trailing whitespace from that paragraph.
        extracted_text = "\n\n".join([para.get_text().strip() for para in paragraphs])
        logger.info(f"[WCHA_LOGIC_WEB] Successfully extracted {len(extracted_text)} characters from paragraphs at {url}.")
        return extracted_text

    except requests.exceptions.Timeout:
        error_msg = f"Error fetching URL '{url}': Timeout after 10 seconds."
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg
    except requests.exceptions.ConnectionError as e:
        error_msg = f"Error fetching URL '{url}': ConnectionError - {e}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg
    except requests.exceptions.HTTPError as e: 
        # This is typically for 4xx/5xx errors if response.raise_for_status() was used.
        # requests.get doesn't raise for status by default, so this might not be hit unless raise_for_status() is added.
        # However, having it is good practice if that changes.
        error_msg = f"Error fetching URL '{url}': HTTPError - {e}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg
    except requests.exceptions.RequestException as e: # Catch-all for other requests issues
        error_msg = f"Error fetching URL '{url}': RequestException - {type(e).__name__} - {e}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}")
        return error_msg
    except Exception as e: # Catch other potential errors (e.g., during parsing with BeautifulSoup)
        error_msg = f"An unexpected error occurred while processing URL '{url}': {type(e).__name__} - {e}"
        logger.error(f"[WCHA_LOGIC_WEB] {error_msg}", exc_info=True) # exc_info=True logs stack trace
        return error_msg

# --- API Endpoint (Preserved - Not changed for this subtask) ---
# This part would require Flask and related setup if it were to be run.
# For now, it's just illustrating that it's preserved.
try:
    import flask
    app = flask.Flask(__name__) # Keep app variable for potential future use or if run in Flask context

    @app.route("/harvest_content_endpoint", methods=["POST"]) # Changed endpoint name for clarity
    def harvest_content_api_endpoint(): # Renamed function
        request_data = flask.request.get_json()
        if not request_data:
            return flask.jsonify({"error": "Invalid JSON payload"}), 400

        topic = request_data.get("topic")
        # Example extension: allow API to call harvest_from_url
        # url_to_harvest = request_data.get("url")

        if topic:
            logger.info(f"[WCHA_API] Received API request for topic: '{topic}'")
            content_result = harvest_content(topic) # Original mock data function
            if content_result.startswith("No pre-defined content found"):
                 return flask.jsonify({"topic": topic, "message": content_result, "content": None}), 200
            return flask.jsonify({"topic": topic, "content": content_result}), 200
        # elif url_to_harvest:
        #     logger.info(f"[WCHA_API] Received API request for URL: '{url_to_harvest}'")
        #     content_result = harvest_from_url(url_to_harvest)
        #     # Based on harvest_from_url's return, decide if it's an error or success
        #     if "Error fetching URL" in content_result or "Failed to fetch URL" in content_result or "No paragraph text found" in content_result or "not HTML" in content_result:
        #         return flask.jsonify({"url": url_to_harvest, "error": content_result}), 400 # Or 500 depending on error type
        #     return flask.jsonify({"url": url_to_harvest, "content": content_result}), 200
        else:
            logger.warning("[WCHA_API] Invalid API request. 'topic' or 'url' must be provided.")
            return flask.jsonify({"error": "Invalid request, 'topic' (for mock) or 'url' (for web) must be provided."}), 400

except ImportError:
    # If Flask is not installed, the app object won't be created.
    # The script can still be used for its functions if run directly.
    app = None 
    logger.info("Flask not installed. API endpoint /harvest_content_endpoint will not be available.")


# --- Example Usage (Updated) ---
if __name__ == "__main__":
    print("--- Testing WCHA: harvest_content (mock data) ---")
    
    # Test with an existing topic
    existing_topic = "ai in healthcare"
    print(f"\nRequesting mock content for topic: '{existing_topic}'")
    mock_content = harvest_content(existing_topic) # Renamed variable to avoid conflict
    print(f"--- Content for '{existing_topic}' ---\n{mock_content[:300]}...\n") # Print snippet
    
    # Test with a non-existing topic
    non_existing_topic = "Underwater Basket Weaving"
    print(f"\nRequesting mock content for topic: '{non_existing_topic}'")
    content_not_found = harvest_content(non_existing_topic)
    print(f"--- Content for '{non_existing_topic}' ---\n{content_not_found}\n")

    print("\n--- Testing WCHA: harvest_from_url (live web fetching) ---")
    if not IMPORTS_SUCCESSFUL:
        print(f"Skipping harvest_from_url tests as required libraries are missing: {MISSING_IMPORT_ERROR}\n")
    else:
        # Example 1: Known public URL (Wikipedia)
        # Note: Live web requests can be slow and might fail due to network issues or website changes.
        # For robust CI/CD, these would typically be mocked.
        wiki_url = "https://en.wikipedia.org/wiki/Artificial_intelligence"
        print(f"\nRequesting content from URL: '{wiki_url}'")
        wiki_content = harvest_from_url(wiki_url)
        print(f"--- Content from '{wiki_url}' (first 500 chars) ---\n{wiki_content[:500]}...\n")

        # Example 2: Non-existent URL
        non_existent_url = "http://thisurldoesnotexist.aethercast.internal"
        print(f"\nRequesting content from non-existent URL: '{non_existent_url}'")
        error_content = harvest_from_url(non_existent_url)
        print(f"--- Result from '{non_existent_url}' ---\n{error_content}\n")

        # Example 3: URL that is not HTML (e.g., a direct image link or a JSON endpoint)
        json_url = "https://jsonplaceholder.typicode.com/todos/1" # Returns JSON
        print(f"\nRequesting content from URL (expected non-HTML): '{json_url}'")
        non_html_content = harvest_from_url(json_url)
        print(f"--- Result from '{json_url}' ---\n{non_html_content}\n")

        # Example 4: URL that is HTML but might have no <p> tags
        # A more reliable way would be to set up a local test server or use a data URI if supported.
        # For now, using a site that is known to be simple. Example.com usually has <p> tags.
        # Let's try a page that might not.
        # This is still a bit unpredictable for a unit test.
        no_para_url = "https://www.google.com/robots.txt" # This is plain text, should be caught by content-type check
        print(f"\nRequesting content from URL (robots.txt, plain text): '{no_para_url}'")
        no_para_content = harvest_from_url(no_para_url)
        print(f"--- Result from '{no_para_url}' ---\n{no_para_content}\n")


    print("\n--- WCHA functionality testing complete ---")
    
    # The Flask app part is conditional on Flask being installed.
    # If app is not None, and this script is run directly, it will start the Flask dev server.
    # This is not strictly part of the subtask's requirements for function implementation testing.
    if app:
        print("\n--- Starting Flask app for WCHA (if Flask is available, on port 5003) ---")
        # Note: The Flask app uses the original `harvest_content` for the `/harvest_content_endpoint`
        # and does not yet expose `harvest_from_url` via an API route in this version.
        try:
            app.run(host="0.0.0.0", port=5003, debug=False) # debug=True can sometimes cause issues with reloader and imports
        except Exception as e:
            logger.error(f"Failed to start Flask app: {e}")
            print(f"Could not start Flask app (may be already running or port conflict): {e}")
