import flask
import uuid
import random
import logging
import json

app = flask.Flask(__name__)

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Placeholder Data Sources ---
# Simulates data fetched from various news APIs, RSS feeds, etc.
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

        discovered_topics = identify_topics_from_sources(query=query, limit=limit)

        if not discovered_topics:
            return flask.jsonify({"message": "No topics discovered for the given query.", "topics": []}), 200

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
```
