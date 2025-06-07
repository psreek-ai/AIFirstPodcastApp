# Web Content Harvester Agent (WCHA)

## Purpose

The Web Content Harvester Agent (WCHA) is a component of the Aethercast system responsible for fetching and extracting textual content from web pages. Given a topic, it uses web search (via DuckDuckGo) to find relevant URLs and then employs content extraction (via Trafilatura) to get the main text from those pages.

Key Responsibilities:

1.  **Topic-Based Search:** Uses `duckduckgo_search` to find relevant web pages for a given topic string.
2.  **Content Fetching:** Makes HTTP GET requests to fetch the content of identified URLs.
3.  **Text Extraction:** Utilizes the `trafilatura` library to extract the main textual content from the fetched HTML, aiming to remove boilerplate like ads, navigation, and footers.
4.  **Content Consolidation:** Combines text extracted from multiple sources into a single string, usually demarcated by source URL.
5.  **Error Handling:** Manages errors related to web requests (timeouts, HTTP errors), search failures, and content extraction issues.

WCHA is primarily used as a library module by CPOA (`get_content_for_topic` function) but also includes a simple Flask endpoint for direct testing of its harvesting capabilities.

## Configuration

WCHA is configured via environment variables, typically managed in a `.env` file within the `aethercast/wcha/` directory. Create one by copying the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file. The following variables are used:

-   `WCHA_SEARCH_MAX_RESULTS`: The maximum number of search results to fetch and process from DuckDuckGo for a given topic.
    -   *Default:* `3`
-   `WCHA_REQUEST_TIMEOUT`: Timeout in seconds for HTTP GET requests when fetching content from a URL.
    -   *Default:* `10`
-   `WCHA_USER_AGENT`: The User-Agent string to be used for HTTP requests made by WCHA.
    -   *Default:* `AethercastContentHarvester/0.2`

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask` (for the test endpoint), `python-dotenv`, `requests`, `beautifulsoup4` (though direct use is reduced), `duckduckgo_search`, and `trafilatura`.

## Running and Testing

**As a Library:**
WCHA's core functionality (`get_content_for_topic` and `harvest_from_url`) is designed to be imported and used by other Python modules, primarily CPOA.

**Standalone Testing (via `if __name__ == "__main__":`)**
The `main.py` script includes a `if __name__ == "__main__":` block that allows for direct testing of its functions:
1.  Ensure dependencies are installed.
2.  Set up environment variables if you want to override defaults (e.g., in a `.env` file).
3.  Execute the script directly:
    ```bash
    python aethercast/wcha/main.py
    ```
This will run tests for `harvest_content` (mocked data), `harvest_from_url` (against a live Wikipedia page), and `get_content_for_topic` (performing live web searches and harvesting).

**Test Flask Endpoint (Optional):**
WCHA also contains a simple Flask app with an endpoint for testing harvesting. If Flask is installed and you wish to run this:
1.  Set environment variables.
2.  Run the Flask development server (it defaults to port 5003 if not specified by Flask's own environment variables like `FLASK_RUN_PORT`):
    ```bash
    # Ensure FLASK_APP is set if using 'flask run'
    export FLASK_APP=aethercast/wcha/main.py
    flask run --port=5003
    # Or, more simply, run the script directly if its __main__ block starts the app:
    # python aethercast/wcha/main.py (if its __main__ is updated to run app)
    ```
    *Note: The `if __name__ == "__main__":` block in the current `wcha/main.py` primarily runs test functions, not the Flask app directly. To run the Flask app, you would typically use `flask run` as shown above or modify the `__main__` block to call `app.run()`.*

## API Endpoint

WCHA provides a single Flask endpoint primarily for testing its harvesting functions.

### Harvest Content

-   **HTTP Method:** `POST`
-   **URL Path:** `/harvest`
-   **Description:** Allows testing of content harvesting. Can either use `get_content_for_topic` based on a topic string (uses web search) or `harvest_from_url` for a specific URL. It can also return mock data for specific topics (legacy).
-   **Request Payload Example (JSON) - For Search & Harvest:**
    ```json
    {
        "topic": "benefits of regular exercise",
        "use_search": true
    }
    ```
-   **Request Payload Example (JSON) - For Direct URL Harvest:**
    ```json
    {
        "url": "https://en.wikipedia.org/wiki/Python_(programming_language)"
    }
    ```
-   **Request Payload Example (JSON) - For Mock Data (Legacy):**
    ```json
    {
        "topic": "ai in healthcare"
    }
    ```
-   **Success Response (200 OK) Example (JSON - Search & Harvest, `use_search=true`):**
    ```json
    {
        "status": "success",
        "content": "Source: http://example.com/article1\nText from article 1...\n\n---\n\nSource: http://example.com/article2\nText from article 2...",
        "source_urls": ["http://example.com/article1", "http://example.com/article2"],
        "message": "Successfully consolidated content from 2 out of 2 URLs for topic 'benefits of regular exercise'."
    }
    ```
-   **Success Response (200 OK) Example (JSON - Direct URL Harvest):**
    ```json
    {
        "status": "success",
        "content": "Text from the harvested URL...",
        "source_urls": ["http://example.com/specific_page"],
        "message": "Successfully harvested content from URL: http://example.com/specific_page"
    }
    ```
-   **Error Response Examples (JSON):**
    -   **400 Bad Request (Invalid Payload - e.g. missing `topic` and `url`):**
        ```json
        {
            "error_code": "WCHA_MISSING_PARAMETERS",
            "message": "Invalid input",
            "details": "'topic' (with use_search=true) or 'url' must be provided."
        }
        ```
    -   **404 Not Found (Search yields no results for `use_search=true`):**
        ```json
        {
            "status": "failure",
            "content": null,
            "source_urls": [],
            "message": "WCHA: No search results found for topic: some very obscure topic"
        }
        ```
    -   **500 Internal Server Error (All harvest attempts fail for `use_search=true`):**
        ```json
        {
            "status": "failure",
            "content": null,
            "source_urls": [],
            "message": "WCHA: Failed to harvest usable content from any of the 2 search results for topic: some topic. Failures: URL: http://badurl1..., Status: Failed, Type: fetch_error, Message: HTTP Status 404..."
        }
        ```
    -   **502 Bad Gateway (Direct URL harvest fails due to fetch error):**
        ```json
        {
            "status": "failure",
            "content": null,
            "source_urls": [],
            "message": "Failed to harvest content from URL: http://nonexistenturl123.invalid/. Reason: Error fetching URL 'http://nonexistenturl123.invalid/': RequestException - ConnectionError - ..."
        }
        ```
