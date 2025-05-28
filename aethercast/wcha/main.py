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
    logging.info(f"[WCHA_LOGIC] harvest_content called with topic: '{topic}'")
    normalized_topic = topic.lower().strip() if topic else ""

    if normalized_topic in SIMULATED_WEB_CONTENT:
        content = SIMULATED_WEB_CONTENT[normalized_topic]
        logging.info(f"[WCHA_LOGIC] Found content for topic: '{topic}'")
        return content
    else:
        logging.warning(f"[WCHA_LOGIC] No pre-defined content found for topic: '{topic}'.")
        return f"No pre-defined content found for topic: {topic}"

# --- API Endpoint ---
@app.route("/harvest_content", methods=["POST"])
def harvest_content_endpoint():
    """
    API endpoint for CPOA to request web content harvesting.
    Accepts a JSON payload with 'topic' (string).
    """
    try:
        request_data = flask.request.get_json()
        if not request_data:
            return flask.jsonify({"error": "Invalid JSON payload"}), 400

        topic = request_data.get("topic")
        error_trigger = request_data.get("error_trigger") # Kept for potential future use or testing

        if not topic:
            return flask.jsonify({"error": "'topic' must be provided."}), 400

        logging.info(f"[WCHA_REQUEST] Received /harvest_content request. Topic: '{topic}', ErrorTrigger: '{error_trigger}'")

        if error_trigger == "wcha_error":
            logging.warning(f"[WCHA_SIMULATED_ERROR] Simulating an error for /harvest_content based on error_trigger: {error_trigger}")
            return flask.jsonify({
                "error": "Simulated WCHA Error",
                "details": "This is a controlled error triggered for testing purposes in WebContentHarvesterAgent."
            }), 500

        # Call the new harvest_content function
        content_result = harvest_content(topic)
        
        # The requirement for harvest_content is to return a string.
        # The API should ideally return JSON.
        if content_result.startswith("No pre-defined content found"):
            return flask.jsonify({"message": content_result, "content": None}), 200 # Or 404 if preferred
        else:
            return flask.jsonify({"content": content_result}), 200

    except Exception as e:
        logging.error(f"Error in /harvest_content endpoint: {e}", exc_info=True)
        return flask.jsonify({"error": f"Internal server error in WCHA: {str(e)}"}), 500

if __name__ == "__main__":
    # Example Usage for harvest_content function
    print("--- Testing harvest_content function ---")
    
    # Test with an existing topic
    existing_topic = "AI in healthcare"
    print(f"\nRequesting content for topic: '{existing_topic}'")
    content = harvest_content(existing_topic)
    print(f"--- Content for '{existing_topic}' ---\n{content}")
    
    # Test with a non-existing topic
    non_existing_topic = "Underwater Basket Weaving"
    print(f"\nRequesting content for topic: '{non_existing_topic}'")
    content_not_found = harvest_content(non_existing_topic)
    print(f"--- Content for '{non_existing_topic}' ---\n{content_not_found}")
    
    # Test with an empty/None topic
    empty_topic = ""
    print(f"\nRequesting content for topic: '{empty_topic}'")
    content_empty = harvest_content(empty_topic)
    print(f"--- Content for '{empty_topic}' ---\n{content_empty}")

    print("\n--- Starting Flask app for WCHA (on port 5003) ---")
    # Run WCHA on a different port
    # Example: python aethercast/wcha/main.py
    app.run(host="0.0.0.0", port=5003, debug=True)
