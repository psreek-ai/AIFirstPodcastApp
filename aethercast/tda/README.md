# Topic Discovery Agent (TDA)

## Purpose

The Topic Discovery Agent (TDA) is a component of the Aethercast system designed to identify and suggest potential podcast topics. It can scan various data sources (currently focusing on NewsAPI.org if configured) or use simulated data to find relevant and timely subjects.

Key Responsibilities:

1.  **Data Source Interaction:**
    *   If configured for real API use, it fetches articles from a news API (e.g., NewsAPI.org) based on keywords, categories, or other criteria.
    *   If not using a real API, it falls back to a set of simulated articles.
2.  **Topic Identification & Transformation:**
    *   Processes fetched articles (or simulated data) to extract key information.
    *   Transforms these articles into a structured `TopicObject` format, including a generated `topic_id`, title suggestion, summary, keywords, potential sources, relevance score, publication date, and category suggestion.
3.  **Output:** Provides a list of these `TopicObject` dictionaries to the calling service, typically the API Gateway or CPOA.

## Configuration

TDA is configured via environment variables, typically managed in a `.env` file within the `aethercast/tda/` directory. Create one by copying the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file. The following variables are used:

-   `TDA_NEWS_API_KEY`: Your API key for the news provider (e.g., NewsAPI.org). This is required if `USE_REAL_NEWS_API` is set to `true`.
    -   *Example:* `your_news_api_key_here`
-   `TDA_NEWS_API_BASE_URL`: Base URL for the news API.
    -   *Default:* `https://newsapi.org/v2/`
-   `TDA_NEWS_API_ENDPOINT`: Specific endpoint to use (e.g., `everything`, `top-headlines`).
    -   *Default:* `everything`
-   `TDA_NEWS_DEFAULT_KEYWORDS`: Comma-separated list of default keywords for topic discovery if no specific query is provided.
    -   *Default:* `AI,technology,science,innovation`
-   `TDA_NEWS_DEFAULT_LANGUAGE`: Default language for news articles (e.g., `en`, `de`).
    -   *Default:* `en`
-   `USE_REAL_NEWS_API`: Set to `true` to use the real News API; `false` for simulated data.
    -   *Default:* `false`
-   `TDA_NEWS_PAGE_SIZE`: Number of articles to fetch from the news API per request.
    -   *Default:* `25`
-   `TDA_NEWS_REQUEST_TIMEOUT`: Timeout in seconds for requests to the news API.
    -   *Default:* `15`
-   `TDA_NEWS_USER_AGENT`: User-Agent string for HTTP requests made by TDA.
    -   *Default:* `AethercastTopicDiscovery/0.1`

**Flask Application Parameters:**
The following are standard Flask environment variables used by `main.py` if you run it directly:
-   `FLASK_APP=aethercast/tda/main.py`
-   `FLASK_RUN_HOST`: Host for the Flask development server.
    -   *Default in `main.py` if run directly:* `0.0.0.0` (was 5001, but TDA typically runs on 5000 or other distinct port)
-   `FLASK_RUN_PORT`: Port for the Flask development server.
    -   *Default in `main.py` if run directly:* `5001` (Note: The provided `main.py` runs on 5001, but TDA is often conceptually on 5000. Ensure this is clear or consistently set.)
-   `FLASK_DEBUG`: To run Flask in debug mode.
    -   *Default in `main.py` if run directly:* `True`

## Dependencies

Project dependencies are listed in `requirements.txt`. Install them using pip:

```bash
pip install -r requirements.txt
```
This includes `Flask`, `requests`, and `python-dotenv`.

## Running the Service (Standalone)

The TDA's Flask application can be run as a standalone service:

1.  Ensure environment variables are set (e.g., in a `.env` file or system environment). If using the real News API, `TDA_NEWS_API_KEY` is essential.
2.  Run the Flask development server:
    ```bash
    python aethercast/tda/main.py
    ```
    By default, this starts the service on `http://0.0.0.0:5001` (as per the current `main.py`'s `if __name__ == "__main__":` block).

Alternatively, using the `flask` command:
```bash
export FLASK_APP=aethercast/tda/main.py
export FLASK_DEBUG=1 # Optional
flask run --host=0.0.0.0 --port=5001
```
*(Adjust port if necessary to avoid conflicts, e.g., to 5000 if API Gateway is on 5001).*

## API Endpoints

### Discover Topics

-   **HTTP Method:** `POST`
-   **URL Path:** `/discover_topics`
-   **Description:** Called by other services (like API Gateway or CPOA) to request a list of potential podcast topics.
-   **Request Payload Example (JSON):**
    ```json
    {
        "query": "artificial intelligence in education", // Optional: specific keywords for search
        "limit": 5, // Optional: number of topics to return
        "error_trigger": null // Optional: for testing error states, e.g., "tda_error"
    }
    ```
    If the payload is empty or `query` is not provided, default keywords from the configuration will be used.
-   **Success Response (200 OK) Example (JSON):**
    ```json
    {
        "discovered_topics": [
            {
                "topic_id": "topic_abc123xyz",
                "source_feed_name": "news_api_org", // Or "simulated_data"
                "title_suggestion": "AI Revolutionizes Personalized Learning Paths",
                "summary": "Educational institutions are increasingly adopting AI to tailor learning experiences...",
                "keywords": ["AI", "education", "personalized learning"],
                "potential_sources": [
                    {
                        "url": "http://example.com/news/ai-education-revolution",
                        "title": "AI Revolutionizes Personalized Learning Paths",
                        "source_name": "Tech News Today"
                    }
                ],
                "relevance_score": 0.85,
                "publication_date": "2024-03-14T10:00:00Z",
                "category_suggestion": "Technology"
            }
            // ... more TopicObjects
        ]
    }
    ```
-   **Success Response (200 OK - No Topics Found) Example (JSON):**
    ```json
    {
        "message": "No topics discovered from NewsAPI for the given query.", // Or from simulated sources
        "topics": []
    }
    ```
-   **Error Response Examples (JSON):**
    -   **500 Internal Server Error (Simulated for testing):**
        ```json
        {
            "error": "Simulated TDA Error",
            "details": "This is a controlled error triggered for testing purposes in TopicDiscoveryAgent."
        }
        ```
    -   **500 Internal Server Error (Real API call issue):**
        If `USE_REAL_NEWS_API` is true and the NewsAPI call fails due to an invalid key, network issue after retries, or unexpected API response, the service will log the error and typically return an empty list of topics or a generic server error. For example, if NewsAPI key is missing:
        ```json
        {
            "message": "No topics discovered from NewsAPI for the given query.",
            "topics": []
        }
        ```
        (The endpoint aims to return 200 with empty topics if the API call fails gracefully internally, but critical unhandled errors in `requests` might lead to a 500 if not caught by the endpoint's main try-except.)

```
**Note on Ports:** The `main.py` for TDA currently defaults to port 5001 in its `if __name__ == "__main__":` block. Standard documentation often places the API Gateway on 5001. Ensure ports are distinct for each service in a running Aethercast deployment. For example, TDA could be on 5000, API Gateway on 5001, SCA on 5002, etc. This README reflects the code's current default but advises users to manage ports.
```
