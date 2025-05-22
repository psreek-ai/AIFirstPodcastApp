import flask
import uuid
import datetime
import logging
import json

app = flask.Flask(__name__)

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Hardcoded Data for Simulation ---
# This data simulates fetched and processed content from the web.
# The structure aims to match the `HarvestedContentBundle` concept.

SIMULATED_WEB_CONTENT = {
    "default": { # Fallback content if specific topic/URL not found
        "retrieved_articles": [
            {
                "source_url": "http://example.com/generic-article",
                "retrieved_text_content": "This is generic content. In a real scenario, this text would be harvested from a web page related to the requested topic. It would then be cleaned and processed.",
                "title_of_source": "Generic Placeholder Article",
                "summary_of_source": "A general article used as a fallback.",
                "original_query_topic": "unknown"
            }
        ]
    },
    "ai in renewable energy management": {
        "retrieved_articles": [
            {
                "source_url": "http://example.com/ai-energy-article1",
                "retrieved_text_content": "AI is revolutionizing how solar farms are managed. Algorithms predict energy output and optimize panel positioning, leading to significant gains in efficiency. Machine learning models analyze weather patterns and grid demand to ensure optimal energy distribution.",
                "title_of_source": "AI Boosts Solar Farm Efficiency",
                "summary_of_source": "An overview of AI in solar farm management, focusing on predictive analytics and optimization.",
                "original_query_topic": "AI in Renewable Energy Management"
            },
            {
                "source_url": "http://example.com/ai-energy-article2",
                "retrieved_text_content": "Wind turbine maintenance can be predicted using AI, reducing downtime and costs. Sensors and machine learning models play a key role in identifying potential faults before they occur, scheduling maintenance proactively.",
                "title_of_source": "Predictive Maintenance for Wind Turbines with AI",
                "summary_of_source": "Details on AI's role in wind turbine upkeep and proactive maintenance scheduling.",
                "original_query_topic": "AI in Renewable Energy Management"
            }
        ]
    },
    "quantum computing advancements": {
        "retrieved_articles": [
            {
                "source_url": "http://example.com/quantum-advances-2024",
                "retrieved_text_content": "Recent breakthroughs in qubit stability and error correction are paving the way for more powerful quantum computers. Researchers are exploring new materials and architectures to overcome existing limitations. The potential impact on fields like drug discovery and cryptography is immense.",
                "title_of_source": "Quantum Computing Leaps Forward in 2024",
                "summary_of_source": "An analysis of recent progress in quantum computing hardware and algorithms.",
                "original_query_topic": "Quantum Computing Advancements"
            }
        ]
    }
}

# --- Helper Functions ---

def generate_harvest_id() -> str:
    """Generates a unique ID for the harvest operation."""
    return f"harvest_{uuid.uuid4().hex[:10]}"

def get_simulated_content(topic: str = None, source_urls: list = None) -> dict:
    """
    Retrieves simulated web content.
    If source_urls are provided, it returns mock content for those URLs.
    If a topic is provided, it tries to find matching hardcoded content.
    Otherwise, returns default content.
    """
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
    harvest_id = generate_harvest_id()
    
    # CPOA's current call_web_content_harvester_agent expects a dict with "retrieved_articles"
    # The HarvestedContentBundle in docs is slightly different. We'll align with CPOA for now.
    # HarvestedContentBundle: { "bundle_id", "topic_id_request", "query", "source_documents": [...] }
    # CPOA expectation: { "retrieved_articles": [...] } where each article has url, title.

    output_bundle = {
        "bundle_id": harvest_id, # From HarvestedContentBundle spec
        "query_topic_received": topic, # From HarvestedContentBundle spec
        "retrieval_timestamp": timestamp, # From HarvestedContentBundle spec
        "retrieved_articles": [] # This matches CPOA's current expectation for the list of sources
    }

    if source_urls:
        logging.info(f"[WCHA_LOGIC] Processing provided source_urls: {source_urls}")
        for url in source_urls:
            # Try to find if this URL matches any of our hardcoded examples for more specific content
            found_specific_article = None
            for key in SIMULATED_WEB_CONTENT:
                if key == "default": continue
                for article in SIMULATED_WEB_CONTENT[key]["retrieved_articles"]:
                    if article["source_url"] == url:
                        found_specific_article = article
                        break
                if found_specific_article: break
            
            if found_specific_article:
                 output_bundle["retrieved_articles"].append({**found_specific_article, "original_query_topic": topic or "from_url_match"})
            else: # Generic content for specified URL
                output_bundle["retrieved_articles"].append({
                    "source_url": url,
                    "retrieved_text_content": f"Simulated content harvested from {url}. This text represents the main article body extracted from the web page. It would be cleaned of HTML, ads, and navigation elements.",
                    "title_of_source": f"Simulated Title for {url}",
                    "summary_of_source": f"A brief summary of the simulated content from {url}.",
                    "original_query_topic": topic or "from_url_direct"
                })
    elif topic:
        logging.info(f"[WCHA_LOGIC] Processing topic: '{topic}'")
        normalized_topic = topic.lower().strip()
        if normalized_topic in SIMULATED_WEB_CONTENT:
            # Use a copy to avoid modifying the original SIMULATED_WEB_CONTENT
            articles_to_add = [dict(article) for article in SIMULATED_WEB_CONTENT[normalized_topic]["retrieved_articles"]]
            for article in articles_to_add: # ensure original_query_topic is set
                article["original_query_topic"] = topic
            output_bundle["retrieved_articles"].extend(articles_to_add)
        else:
            logging.warning(f"[WCHA_LOGIC] No specific simulated content for topic: '{topic}'. Using default.")
            articles_to_add = [dict(article) for article in SIMULATED_WEB_CONTENT["default"]["retrieved_articles"]]
            for article in articles_to_add:
                 article["original_query_topic"] = topic # Tag with the requested topic
            output_bundle["retrieved_articles"].extend(articles_to_add)
    else: # No topic and no URLs
        logging.info("[WCHA_LOGIC] No topic or URLs provided. Using default content.")
        articles_to_add = [dict(article) for article in SIMULATED_WEB_CONTENT["default"]["retrieved_articles"]]
        for article in articles_to_add:
             article["original_query_topic"] = "unknown_fallback"
        output_bundle["retrieved_articles"].extend(articles_to_add)

    # The CPOA `call_web_content_harvester_agent` placeholder returns a simple dict:
    # {"retrieved_articles": [{"url": "http://example.com/article1", "title": "Example Article"}]}
    # The `HarvestedContentBundle` is more structured.
    # For now, let's return the CPOA-expected structure for direct compatibility,
    # but log the full bundle structure that WCHA *could* return.
    
    cpoa_expected_output = {"retrieved_articles": []}
    for art_data in output_bundle["retrieved_articles"]:
        cpoa_expected_output["retrieved_articles"].append({
            "url": art_data.get("source_url"),
            "title": art_data.get("title_of_source"),
            # Adding text_content as PSWA might need it directly
            "text_content": art_data.get("retrieved_text_content"), 
            "summary": art_data.get("summary_of_source")
        })
    
    logging.info(f"[WCHA_LOGIC] Full HarvestedContentBundle (internal): {json.dumps(output_bundle, indent=2)}")
    logging.info(f"[WCHA_LOGIC] Returning CPOA-compatible output: {json.dumps(cpoa_expected_output, indent=2)}")
    
    return cpoa_expected_output


# --- API Endpoint ---
@app.route("/harvest_content", methods=["POST"])
def harvest_content_endpoint():
    """
    API endpoint for CPOA to request web content harvesting.
    Accepts a JSON payload with 'topic' (string) and/or 'source_urls' (list of strings).
    """
    try:
        request_data = flask.request.get_json()
        if not request_data:
            return flask.jsonify({"error": "Invalid JSON payload"}), 400

        topic = request_data.get("topic")
        source_urls = request_data.get("source_urls") # List of URLs
        error_trigger = request_data.get("error_trigger")

        if not topic and not source_urls:
            return flask.jsonify({"error": "Either 'topic' or 'source_urls' must be provided."}), 400

        logging.info(f"[WCHA_REQUEST] Received /harvest_content request. Topic: '{topic}', URLs: {source_urls}, ErrorTrigger: '{error_trigger}'")

        if error_trigger == "wcha_error":
            logging.warning(f"[WCHA_SIMULATED_ERROR] Simulating an error for /harvest_content based on error_trigger: {error_trigger}")
            return flask.jsonify({
                "error": "Simulated WCHA Error",
                "details": "This is a controlled error triggered for testing purposes in WebContentHarvesterAgent."
            }), 500

        harvested_data = get_simulated_content(topic, source_urls)

        return flask.jsonify(harvested_data), 200

    except Exception as e:
        logging.error(f"Error in /harvest_content endpoint: {e}", exc_info=True)
        return flask.jsonify({"error": f"Internal server error in WCHA: {str(e)}"}), 500

if __name__ == "__main__":
    # Run WCHA on a different port
    # Example: python aethercast/wcha/main.py
    app.run(host="0.0.0.0", port=5003, debug=True)
```
