import flask
import uuid
import random
import logging
import json
import os
from dotenv import load_dotenv
import requests

# Load environment variables from .env file
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

# --- Application Configuration ---
tda_config = {
    "TDA_NEWS_API_KEY": os.getenv("TDA_NEWS_API_KEY"),
    "TDA_NEWS_API_BASE_URL": os.getenv("TDA_NEWS_API_BASE_URL", "https://newsapi.org/v2/"),
    "TDA_NEWS_API_ENDPOINT": os.getenv("TDA_NEWS_API_ENDPOINT", "everything"),
    "TDA_NEWS_DEFAULT_KEYWORDS": os.getenv("TDA_NEWS_DEFAULT_KEYWORDS", "AI,technology,science").split(','),
    "TDA_NEWS_DEFAULT_LANGUAGE": os.getenv("TDA_NEWS_DEFAULT_LANGUAGE", "en"),
    "USE_REAL_NEWS_API": os.getenv("USE_REAL_NEWS_API", "False").lower() == "true",
    "TDA_NEWS_PAGE_SIZE": int(os.getenv("TDA_NEWS_PAGE_SIZE", "25")), # Added
    "TDA_NEWS_REQUEST_TIMEOUT": int(os.getenv("TDA_NEWS_REQUEST_TIMEOUT", "15")), # Added
    "TDA_NEWS_USER_AGENT": os.getenv("TDA_NEWS_USER_AGENT", "AethercastTopicDiscovery/0.1"), # Added
}

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Log loaded configuration
logging.info("--- TDA Configuration ---")
for key, value in tda_config.items():
    if "API_KEY" in key and value:
        logging.info(f"  {key}: {'*' * (len(value) - 4) + value[-4:] if len(value) > 4 else '****'}")
    else:
        logging.info(f"  {key}: {value}")
logging.info("--- End TDA Configuration ---")

# Startup Check for API Key
if tda_config["USE_REAL_NEWS_API"] and not tda_config["TDA_NEWS_API_KEY"]:
    logging.error("CRITICAL: USE_REAL_NEWS_API is True, but TDA_NEWS_API_KEY is not set. Real News API calls will fail.")
    # Optionally, raise an error to prevent startup without API key:
    # raise ValueError("TDA_NEWS_API_KEY is required when USE_REAL_NEWS_API is True")
else:
    # This specific logging about API usage is fine here, complements the general config log.
    if tda_config["USE_REAL_NEWS_API"] and tda_config["TDA_NEWS_API_KEY"]:
        logging.info("TDA is configured to use the REAL News API.")
    elif tda_config["USE_REAL_NEWS_API"] and not tda_config["TDA_NEWS_API_KEY"]:
        # This case is already covered by the critical error log above, but good for clarity if not raising error.
        logging.warning("TDA is configured to use REAL News API but KEY IS MISSING.")
    else:
        logging.info("TDA is configured to use SIMULATED data sources.")

app = flask.Flask(__name__)

# --- Placeholder Data Sources ---
# Simulates data fetched from various news APIs, RSS feeds, etc.

def call_real_news_api(keywords: list[str] = None, categories: list[str] = None, language: str = None, country: str = None) -> list[dict]:
    """
    Calls the NewsAPI.org to fetch articles, parses the response, and transforms articles into TopicObjects.
    Returns a list of TopicObject dictionaries or an empty list if an error occurs.
    """
    global tda_config, generate_topic_id # Ensure access to global tda_config and generate_topic_id
    if not tda_config["TDA_NEWS_API_KEY"]:
        logging.error("call_real_news_api: Missing TDA_NEWS_API_KEY. Cannot make request.")
        return []

    base_url = tda_config["TDA_NEWS_API_BASE_URL"]
    endpoint = tda_config["TDA_NEWS_API_ENDPOINT"]
    api_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    params = {}
    # query_keywords will be used for both API call and TopicObject keywords
    query_keywords_list = keywords if keywords else tda_config["TDA_NEWS_DEFAULT_KEYWORDS"]
    if query_keywords_list:
        # Ensure query_keywords_list is a list of strings
        if isinstance(query_keywords_list, str): # Should not happen if default is already a list
            query_keywords_list = [kw.strip() for kw in query_keywords_list.split(',')]
        # For NewsAPI 'q' parameter, join with " OR " for multiple keywords for broader search,
        # or " AND " for more specific. Using " OR " for discovery.
        params["q"] = " OR ".join(query_keywords_list)

    if endpoint == "top-headlines": # Specific params for 'top-headlines'
        if categories: # NewsAPI expects a single category for 'top-headlines'
            params["category"] = categories[0] if isinstance(categories, list) else categories
        if country: # Add country if provided and using top-headlines
            params["country"] = country
        # 'q' is also usable with top-headlines, but country/category are more common.
    
    current_language = language if language else tda_config["TDA_NEWS_DEFAULT_LANGUAGE"]
    if current_language:
        params["language"] = current_language
    
    params["pageSize"] = tda_config.get("TDA_NEWS_PAGE_SIZE", 25)

    headers = {
        "X-Api-Key": tda_config["TDA_NEWS_API_KEY"],
        "User-Agent": tda_config.get("TDA_NEWS_USER_AGENT", "AethercastTopicDiscovery/0.1")
    }

    request_timeout = tda_config.get("TDA_NEWS_REQUEST_TIMEOUT", 15)
    logging.info(f"Calling NewsAPI: URL={api_url}, Params={params}, Timeout={request_timeout}s")
    topic_objects = []

    try:
        response = requests.get(api_url, headers=headers, params=params, timeout=request_timeout)
        response.raise_for_status() # Raises HTTPError for bad responses (4XX or 5XX)
        
        response_json = response.json()

        if response_json.get("status") != "ok":
            logging.error(f"NewsAPI returned error status: {response_json.get('status')}. Message: {response_json.get('message')}")
            return []

        articles = response_json.get("articles", [])
        
        for article in articles:
            title = article.get('title')
            if not title or title == "[Removed]": # Skip articles with no title or removed content
                continue

            description = article.get('description')
            content = article.get('content') # NewsAPI often truncates content, description might be better
            summary_text = description if description else content if content else "No summary available."
            # Remove incomplete sentence artifacts like "[+1234 chars]"
            if summary_text.endswith(" chars]") and summary_text[-10:].startswith("[+"):
                summary_text = summary_text[:summary_text.rfind("[+")].strip()
            if not summary_text: # If still empty
                summary_text = "Content details not available."


            article_url = article.get('url')
            source_name = article.get('source', {}).get('name', 'Unknown Source')
            published_at = article.get('publishedAt')

            # Use the original list of keywords that formed the query for this TopicObject
            # This ensures the keywords reflect the search intent.
            topic_keywords = query_keywords_list

            topic_object = {
                "topic_id": generate_topic_id(),
                "source_feed_name": "news_api_org", # More specific
                "title_suggestion": title,
                "summary": summary_text,
                "keywords": topic_keywords,
                "potential_sources": [{
                    "url": article_url,
                    "title": title, # Source title is the article title itself
                    "source_name": source_name # e.g., "CNN", "BBC News"
                }],
                "relevance_score": round(random.uniform(0.6, 0.9), 2), # More varied default relevance
                "publication_date": published_at,
                "category_suggestion": "News" # Default, can be enhanced later
            }
            topic_objects.append(topic_object)
        
        logging.info(f"Transformed {len(topic_objects)} articles into TopicObjects from NewsAPI.")
        return topic_objects

    except requests.exceptions.JSONDecodeError as json_err:
        logging.error(f"Failed to decode JSON from NewsAPI: {json_err}. Response text: {response.text if 'response' in locals() else 'N/A'}")
        return []
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error occurred: {http_err} - {http_err.response.text if http_err.response else 'No response text'}")
        return []
    except requests.exceptions.ConnectionError as conn_err:
        logging.error(f"Connection error occurred: {conn_err}")
        return []
    except requests.exceptions.Timeout as timeout_err:
        logging.error(f"Timeout error occurred: {timeout_err}")
        return []
    except requests.exceptions.RequestException as req_err: # Catch any other request-related errors
        logging.error(f"An unexpected error occurred with the NewsAPI request: {req_err}")
        return []
    # Default return for any other unhandled issues within this function, though try/except should cover most.
    return []

SIMULATED_DATA_SOURCES = [
    {
        "source_name": "Tech Chronicle",
        "articles": [
            {"title": "The Future of AI in Personalized Medicine", "url": "http://example.com/ai-medicine", "keywords": ["AI", "Healthcare", "Personalized Medicine"], "publish_date": "2024-03-10"},
            {"title": "Quantum Computing: Beyond the Hype", "url": "http://example.com/quantum-beyond-hype", "keywords": ["Quantum Computing", "Technology", "Innovation"], "publish_date": "2024-03-11"},
            {"title": "Sustainable Tech: Innovations for a Greener Planet", "url": "http://example.com/sustainable-tech", "keywords": ["Sustainability", "Green Tech", "Environment"], "publish_date": "2024-03-08"},
        ]
    },
    {
        "source_name": "Global News Network",
        "articles": [
            {"title": "Geopolitical Shifts and Their Economic Impact", "url": "http://example.com/geopolitics-economy", "keywords": ["Geopolitics", "Economy", "Global Affairs"], "publish_date": "2024-03-12"},
            {"title": "Advances in Space Exploration: The Artemis Program", "url": "http://example.com/artemis-program", "keywords": ["Space Exploration", "NASA", "Moon Mission"], "publish_date": "2024-03-09"},
        ]
    },
    {
        "source_name": "Science Today Journal",
        "articles": [
            {"title": "Breakthrough in Fusion Energy Research", "url": "http://example.com/fusion-breakthrough", "keywords": ["Fusion Energy", "Physics", "Clean Energy"], "publish_date": "2024-03-11"},
            {"title": "Understanding Dark Matter: New Theories Emerge", "url": "http://example.com/dark-matter-theories", "keywords": ["Cosmology", "Dark Matter", "Astrophysics"], "publish_date": "2024-03-07"},
        ]
    }
]

# --- Topic Identification & Ranking Logic ---

def generate_topic_id() -> str:
    """Generates a unique topic ID."""
    return f"topic_{uuid.uuid4().hex[:10]}"

def generate_summary_from_title(title: str) -> str:
    """Generates a placeholder summary based on the title."""
    return f"This topic explores {title.lower()}, focusing on its recent developments and potential impact."

def calculate_relevance_score(article: dict, query: str = None) -> float:
    """
    Calculates a basic relevance score.
    If a query is provided, boosts score if query keywords are in article keywords.
    Otherwise, scores based on recency or other heuristics.
    """
    score = random.uniform(0.5, 0.9) # Base random score

    # Boost score for keywords (simple matching)
    if query:
        query_keywords = [q.strip().lower() for q in query.split()]
        for qk in query_keywords:
            if qk in [kw.lower() for kw in article.get("keywords", [])]:
                score = min(1.0, score + 0.2) # Boost score
            if qk in article.get("title", "").lower():
                score = min(1.0, score + 0.1)


    # Placeholder: Add a small boost for more recent articles (days_old < 7)
    # In a real system, `publish_date` would be parsed properly.
    # For now, we assume all are recent enough.
    # Example: if "2024-03-12" in article.get("publish_date", ""): score = min(1.0, score + 0.05)

    return round(score, 2)


def identify_topics_from_sources(query: str = None, limit: int = 5) -> list:
    """
    Processes simulated data sources to identify and rank potential topics.
    """
    identified_topics = []
    all_articles = []

    logging.info(f"[TDA_LOGIC] Scanning simulated data sources. Query: '{query}', Limit: {limit}")

    for data_source in SIMULATED_DATA_SOURCES:
        for article in data_source["articles"]:
            all_articles.append({
                "title": article["title"],
                "url": article["url"],
                "source_name": data_source["source_name"],
                "keywords": article.get("keywords", []),
                "publish_date": article.get("publish_date", "")
            })

    # Score and select topics
    for article in all_articles:
        relevance = calculate_relevance_score(article, query)
        topic_object = {
            "topic_id": generate_topic_id(),
            "title_suggestion": article["title"],
            "summary": generate_summary_from_title(article["title"]),
            "keywords": article.get("keywords", []) + [kw.strip() for kw in query.split()] if query else article.get("keywords", []),
            "potential_sources": [{"url": article["url"], "title": article["title"], "source_name": article["source_name"]}],
            "relevance_score": relevance,
            "category_suggestion": "General" # Placeholder, could be derived from keywords
        }
        identified_topics.append(topic_object)

    # Sort by relevance score (descending) and return top 'limit'
    identified_topics.sort(key=lambda x: x["relevance_score"], reverse=True)
    
    logging.info(f"[TDA_LOGIC] Identified {len(identified_topics)} potential topics. Returning top {min(limit, len(identified_topics))}.")
    return identified_topics[:limit]

# --- API Endpoint ---

@app.route("/discover_topics", methods=["POST"])
def discover_topics_endpoint():
    """
    API endpoint for CPOA to request topic discovery.
    Accepts a JSON payload with an optional 'query' and 'limit'.
    """
    try:
        request_data = flask.request.get_json()
        if not request_data:
            request_data = {} # Allow empty payload for general discovery

        query = request_data.get("query") # Optional query from CPOA
        limit = request_data.get("limit", 5) # Default limit
        error_trigger = request_data.get("error_trigger") # For simulating errors

        logging.info(f"Received POST /discover_topics request. Query: '{query}', Limit: {limit}, ErrorTrigger: '{error_trigger}'")

        if error_trigger == "tda_error":
            logging.warning(f"[TDA_SIMULATED_ERROR] Simulating an error for /discover_topics based on error_trigger: {error_trigger}")
            return flask.jsonify({
                "error": "Simulated TDA Error",
                "details": "This is a controlled error triggered for testing purposes in TopicDiscoveryAgent."
            }), 500

        # global tda_config # tda_config is already globally accessible
        
        discovered_topics = []
        if tda_config["USE_REAL_NEWS_API"]:
            logging.info(f"Using REAL News API for /discover_topics. Query: '{query}'")
            request_keywords = [k.strip() for k in query.split(',')] if query else None
            
            # call_real_news_api expects a list of keywords.
            # It does not currently use 'limit' directly; NewsAPI handles result size.
            # We will slice the result if a limit is needed post-fetch.
            raw_topics_from_api = call_real_news_api(
                keywords=request_keywords, 
                language=tda_config.get("TDA_NEWS_DEFAULT_LANGUAGE")
                # categories and country are not passed here, call_real_news_api uses defaults or endpoint specific logic
            )
            
            if raw_topics_from_api:
                # Sort by relevance_score if available, assuming higher is better.
                # The current call_real_news_api sets a static 0.8, so sorting won't do much unless changed.
                # For now, we'll just take the list as is.
                # raw_topics_from_api.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
                
                # Apply limit if specified
                if limit > 0 and isinstance(limit, int):
                    discovered_topics = raw_topics_from_api[:limit]
                else:
                    discovered_topics = raw_topics_from_api
            else:
                discovered_topics = [] # Ensure it's an empty list if API returns nothing or error
        
        else:
            logging.info(f"Using SIMULATED data for /discover_topics. Query: '{query}', Limit: {limit}")
            discovered_topics = identify_topics_from_sources(query=query, limit=limit)

        if not discovered_topics:
            # Provide a more specific message if using the real API and no topics were found
            message = "No topics discovered."
            if tda_config["USE_REAL_NEWS_API"]:
                 message = "No topics discovered from NewsAPI for the given query."
            else:
                 message = "No topics discovered from simulated sources for the given query."
            return flask.jsonify({"message": message, "topics": []}), 200

        # This is the structure CPOA's `call_topic_discovery_agent` expects
        response_data = {"discovered_topics": discovered_topics}
        
        return flask.jsonify(response_data), 200

    except Exception as e:
        logging.error(f"Error in /discover_topics endpoint: {e}")
        return flask.jsonify({"error": "Internal server error during topic discovery"}), 500

if __name__ == "__main__":
    # In a real deployment, use a proper WSGI server like Gunicorn or uWSGI
    # For development, Flask's built-in server is fine.
    # The CPOA will call this service, so it needs to be running.
    # Example: python aethercast/tda/main.py
    app.run(host="0.0.0.0", port=5001, debug=True) # Running on a different port than CPOA
