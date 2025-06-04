import os
import logging
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - IGA - %(message)s')

iga_config = {}

def load_iga_configuration():
    global iga_config
    iga_config['IGA_HOST'] = os.getenv("IGA_HOST", "0.0.0.0")
    iga_config['IGA_PORT'] = int(os.getenv("IGA_PORT", 5007))
    iga_config['IGA_DEBUG_MODE'] = os.getenv("IGA_DEBUG_MODE", "True").lower() == "true"
    # Add logging of loaded config here
    logging.info("--- IGA Configuration ---")
    for key, value in iga_config.items():
        logging.info(f"  {key}: {value}")
    logging.info("--- End IGA Configuration ---")

load_iga_configuration()

IGA_MODEL_VERSION = "iga-placeholder-v0.1"

@app.route("/generate_image", methods=["POST"])
def generate_image_endpoint():
    try:
        data = request.get_json()
        if not data or "prompt" not in data or not data["prompt"]:
            logging.warning("IGA: Bad request to /generate_image: Missing or empty 'prompt'.")
            return jsonify({"error": "BAD_REQUEST", "message": "Missing 'prompt' in request body."}), 400

        prompt = data["prompt"]
        logging.info(f"IGA: Received prompt for image generation: '{prompt}'")

        # Simulate image generation by returning a dynamic Unsplash URL based on keywords from prompt
        # For simplicity, take the first few words of the prompt as keywords
        keywords = "+".join(prompt.split()[:3]) # e.g., "A+futuristic+podcast"

        # Sanitize keywords: replace non-alphanumeric with '+' (except '+') and remove trailing/leading '+'
        sanitized_keywords = "".join(c if c.isalnum() or c == '+' else '+' for c in keywords)
        sanitized_keywords = "+".join(filter(None, sanitized_keywords.split('+'))) # Remove multiple/empty '+'

        image_url = f"https://source.unsplash.com/random/400x225/?{sanitized_keywords},podcast,abstract"

        # Fallback if keywords are empty after sanitization
        if not sanitized_keywords:
            image_url = "https://source.unsplash.com/random/400x225/?podcast,abstract"


        logging.info(f"IGA: Returning placeholder image URL: {image_url} for prompt: '{prompt}'")

        response_data = {
            "image_url": image_url,
            "prompt_used": prompt,
            "model_version": IGA_MODEL_VERSION
        }
        return jsonify(response_data), 200

    except Exception as e:
        logging.error(f"IGA: Error in /generate_image endpoint: {e}", exc_info=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR", "message": "IGA placeholder encountered an unexpected error."}), 500

if __name__ == "__main__":
    host = iga_config.get("IGA_HOST")
    port = iga_config.get("IGA_PORT")
    is_debug_mode = iga_config.get("IGA_DEBUG_MODE")
    app.run(host=host, port=port, debug=is_debug_mode)
