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
4.  **Database Interaction:** Saves all successfully identified `TopicObject`s to a shared database (`topics_snippets` table, using the path from `SHARED_DATABASE_PATH` configuration), making them available for other services.

## Configuration

TDA is configured via environment variables, typically managed in a `.env` file within the `aethercast/tda/` directory. Create one by copying the example:

```bash
cp .env.example .env
```

Then, edit the `.env` file. The following variables are used:

-   `TDA_NEWS_API_KEY`: Your API key for the news provider (e.g., NewsAPI.org). Required if `USE_REAL_NEWS_API` is `true`.
-   `TDA_NEWS_API_BASE_URL`: Base URL for the news API. Default: `https://newsapi.org/v2/`.
-   `TDA_NEWS_API_ENDPOINT`: Specific endpoint (e.g., `everything`, `top-headlines`). Default: `everything`.
-   `TDA_NEWS_DEFAULT_KEYWORDS`: Comma-separated default keywords. Default: `AI,technology,science,innovation`.
-   `TDA_NEWS_DEFAULT_LANGUAGE`: Default language for news. Default: `en`.
-   `USE_REAL_NEWS_API`: Set to `true` for real News API; `false` for simulated data. Default: `false`.
-   `TDA_NEWS_PAGE_SIZE`: Articles to fetch per request. Default: `25`.
-   `TDA_NEWS_REQUEST_TIMEOUT`: Timeout for news API requests (seconds). Default: `15`.
-   `TDA_NEWS_USER_AGENT`: User-Agent for HTTP requests. Default: `AethercastTopicDiscovery/0.1`.
-   `SHARED_DATABASE_PATH`: Path to the shared SQLite database file. This path is typically set via this environment variable (which TDA's code uses internally as `tda_config['SHARED_DATABASE_PATH']`). In a Docker environment, this usually points to `/app/database/aethercast_podcasts.db` on a shared volume. This **must** be the same path used by API Gateway and CPOA.

**Flask Application Parameters (used when running `main.py` directly):**
-   `TDA_HOST`: Host for the TDA Flask server. Default: `0.0.0.0`. (Can also be set by `FLASK_RUN_HOST`).
-   `TDA_PORT`: Port for the TDA Flask server. Default: `5000`. (Can also be set by `FLASK_RUN_PORT`). Note: The TDA service typically runs on port 5000.
-   `TDA_DEBUG_MODE`: Enables/disables Flask debug mode (`True`/`False`). Default: `True`. (Can also be set by `FLASK_DEBUG=1` or `0`).

When using the `flask` command directly, you might set:
-   `FLASK_APP=aethercast/tda/main.py`

## Dependencies

Project dependencies are listed in `requirements.txt` (includes `Flask`, `requests`, `python-dotenv`). Install with `pip install -r requirements.txt`.

## Running the Service (Standalone)

The TDA's Flask application can be run as a standalone service:

1.  Ensure environment variables are set. If using real News API, `TDA_NEWS_API_KEY` is essential.
2.  Run the Flask development server:
    ```bash
    python aethercast/tda/main.py
    ```
    By default, this starts the service on `http://0.0.0.0:5000`.

Alternatively, using the `flask` command:
```bash
export FLASK_APP=aethercast/tda/main.py
export FLASK_DEBUG=1 # Optional
flask run --host=0.0.0.0 --port=5000
```

## API Endpoints

### Discover Topics

-   **HTTP Method:** `POST`
-   **URL Path:** `/discover_topics`
-   **Description:** Called by other services to request potential podcast topics.
-   **Request Payload Example (JSON):**
    ```json
    {
        "query": "artificial intelligence in education", // Optional
        "limit": 5, // Optional
        "error_trigger": null // Optional: for testing, e.g., "tda_error"
    }
    ```
    If `query` is not provided, default keywords are used.
-   **Success Response (200 OK) Example (JSON):**
    ```json
    {
        "discovered_topics": [
            {
                "topic_id": "topic_abc123xyz",
                "source_feed_name": "news_api_org", // Or "simulated_data"
                "title_suggestion": "AI Revolutionizes Personalized Learning Paths",
                "summary": "Educational institutions are increasingly adopting AI...",
                "keywords": ["AI", "education", "personalized learning"],
                "potential_sources": [{"url": "...", "title": "...", "source_name": "..."}],
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
    -   500 Internal Server Error (Simulated): `{"error_code": "TDA_SIMULATED_ERROR", "message": "...", "details": "..."}`
    -   500 Internal Server Error (Real API call issue): May return 200 with empty topics/message, or a generic 500 for unhandled errors: `{"error_code": "INTERNAL_SERVER_ERROR_TDA", "message": "...", "details": "..."}`.

```
**Note on Ports:** The `main.py` for TDA defaults to port 5000 in its configuration. Ensure ports are distinct for each service in a running Aethercast deployment. This README reflects the code's current default.
```
