import flask
import uuid
import datetime
import logging
import json
import os
from dotenv import load_dotenv
import requests # For calling AIMS (LLM)

# --- Load Environment Variables ---
load_dotenv()

# --- Logging Setup ---
import logging # Moved up
from python_json_logger import jsonlogger # Moved up

# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="sca"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

# Initialize Flask app early so app.logger can be configured
app = flask.Flask(__name__)

# Configure JSON logging for the Flask app
def setup_json_logging(flask_app):
    flask_app.logger.handlers.clear() # Clear existing default Flask handlers
    logHandler = logging.StreamHandler()
    service_filter = ServiceNameFilter("sca")
    logHandler.addFilter(service_filter)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s",
        rename_fields={"levelname": "level", "name": "logger_name", "asctime": "timestamp"}
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("JSON logging configured for SCA service.")

setup_json_logging(app)

# --- Global SCA Configuration ---
sca_config = {}

def load_sca_configuration():
    """Loads SCA configurations from environment variables with defaults."""
    global sca_config
    # Removed SCA_LLM_PROVIDER, SCA_LLM_API_KEY, SCA_LLM_BASE_URL
    sca_config['AIMS_SERVICE_URL'] = os.getenv('AIMS_SERVICE_URL', 'http://aims_service:8000/v1/generate')
    sca_config['AIMS_REQUEST_TIMEOUT_SECONDS'] = int(os.getenv('AIMS_REQUEST_TIMEOUT_SECONDS', '60'))

    sca_config['SCA_LLM_MODEL_ID'] = os.getenv('SCA_LLM_MODEL_ID', 'gpt-3.5-turbo') # Model to request from AIMS
    sca_config['SCA_LLM_MAX_TOKENS_SNIPPET'] = int(os.getenv('SCA_LLM_MAX_TOKENS_SNIPPET', '150'))
    sca_config['SCA_LLM_TEMPERATURE_SNIPPET'] = float(os.getenv('SCA_LLM_TEMPERATURE_SNIPPET', '0.7'))
    
    sca_config['USE_REAL_LLM_SERVICE'] = os.getenv('USE_REAL_LLM_SERVICE', 'false').lower() == 'true'

    app.logger.info("SCA Configuration Loaded:") # Use app.logger
    for key, value in sca_config.items():
        app.logger.info(f"  {key}: {value}") # Use app.logger

    if sca_config['USE_REAL_LLM_SERVICE']:
        missing_configs = []
        if not sca_config['AIMS_SERVICE_URL']: # Check AIMS URL if real service is selected
            missing_configs.append("AIMS_SERVICE_URL")
        if not sca_config['SCA_LLM_MODEL_ID']: # Still need a model to request from AIMS
            missing_configs.append("SCA_LLM_MODEL_ID")
        
        if missing_configs:
            error_message = f"CRITICAL: USE_REAL_LLM_SERVICE is true, but required configurations are missing: {', '.join(missing_configs)}."
            app.logger.critical(error_message) # Use app.logger
            raise ValueError(error_message)
        else:
            app.logger.info("SCA is configured to use a REAL LLM service via AIMS.") # Use app.logger
    else:
        app.logger.info("SCA is configured to use the SIMULATED/PLACEHOLDER LLM response (bypassing AIMS).") # Use app.logger

load_sca_configuration()

# Flask app initialized earlier for logging

AIMS_LLM_PLACEHOLDER_URL = "http://localhost:8000/v1/generate" # Kept for placeholder, though not used if USE_REAL_LLM_SERVICE=true
AIMS_LLM_HARDCODED_RESPONSE = {
    "request_id": "sca_placeholder_req_id",
    "model_id": "sca_placeholder_model_id",
    "choices": [
        {
            "text": "Placeholder text from AIMS_LLM_HARDCODED_RESPONSE in SCA",
            "finish_reason": "STOP"
        }
    ],
    "usage": {
        "prompt_tokens": 5, # Example value
        "completion_tokens": 10, # Example value
        "total_tokens": 15 # Example value
    }
}

def generate_snippet_id() -> str:
    return f"snippet_{uuid.uuid4().hex[:12]}"

def call_aims_llm_placeholder(prompt: str, topic_info: dict) -> dict:
    # This function remains for USE_REAL_LLM_SERVICE=false, unchanged internally
    if sca_config['USE_REAL_LLM_SERVICE']:
        app.logger.warning("[SCA_AIMS_CALL] call_aims_llm_placeholder invoked while USE_REAL_LLM_SERVICE is true. This indicates a logic path needs review. Using dynamic placeholder as fallback.")
    app.logger.info("[SCA_AIMS_CALL] Dynamically generating SIMULATED AIMS LLM response for snippet.")
    # ... (rest of existing placeholder logic remains the same) ...
    title_suggestion = topic_info.get("title_suggestion", "Interesting Developments")
    keywords = topic_info.get("keywords", [])
    dynamic_title = f"Insights on {title_suggestion}"
    if keywords:
        dynamic_content = f"Exploring {title_suggestion}, focusing on {', '.join(keywords)}. This area shows promising advancements and requires further analysis."
    else:
        dynamic_content = f"A closer look at {title_suggestion}. Several interesting developments have occurred, leading to much discussion."
    dynamic_response_text = f"This is a placeholder response from the AIMS LLM service. Based on your prompt, here's a generic title: '{dynamic_title}' and some generic content: '{dynamic_content}'"
    response = json.loads(json.dumps(AIMS_LLM_HARDCODED_RESPONSE)) 
    response["choices"][0]["text"] = dynamic_response_text
    response["request_id"] = f"aims-llm-placeholder-req-dynamic-{uuid.uuid4().hex[:6]}"
    response["model_id"] = "AetherLLM-Placeholder-DynamicSnippet-v0.2"
    response["usage"]["prompt_tokens"] = len(prompt.split()) // 4 
    response["usage"]["completion_tokens"] = len(dynamic_response_text.split()) // 4
    response["usage"]["total_tokens"] = response["usage"]["prompt_tokens"] + response["usage"]["completion_tokens"]
    return {
        "status": "success_placeholder",
        "title": dynamic_title, # Return directly
        "text_content": dynamic_content, # Return directly
        "llm_response_direct": response, # Keep for full structure if needed elsewhere
        "llm_model_used": response.get("model_id", "AetherLLM-Placeholder-DynamicSnippet-v0.2"),
        "llm_prompt_sent": prompt
    }

def call_real_llm_service(prompt: str, topic_info: dict) -> dict:
    """
    Calls the AIMS service to get LLM-generated text for a snippet.
    """
    aims_url = sca_config.get('AIMS_SERVICE_URL')
    model_id_to_request = sca_config.get('SCA_LLM_MODEL_ID')
    max_tokens = sca_config.get('SCA_LLM_MAX_TOKENS_SNIPPET')
    temperature = sca_config.get('SCA_LLM_TEMPERATURE_SNIPPET')
    timeout = sca_config.get('AIMS_REQUEST_TIMEOUT_SECONDS')

    app.logger.info(f"[SCA_AIMS_CALL] Preparing to call AIMS. URL: {aims_url}, Model: {model_id_to_request}")

    if not aims_url: # Should be caught by load_sca_configuration, but as safeguard
        app.logger.error("[SCA_AIMS_CALL] AIMS_SERVICE_URL is not configured.")
        return {"error_code": "SCA_AIMS_CONFIG_MISSING", "message": "AIMS_SERVICE_URL not configured.", "details": "AIMS service URL is missing."}

    aims_payload = {
        "prompt": prompt,
        "model_id_override": model_id_to_request,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # "response_format": {"type": "text"} # AIMS default is text, explicit if needed
    }
    
    app.logger.debug(f"  AIMS Request Payload: {json.dumps(aims_payload)}")

    try:
        response = requests.post(aims_url, json=aims_payload, timeout=timeout)
        app.logger.info(f"[SCA_AIMS_CALL] AIMS Response Status Code: {response.status_code}")
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)

        aims_response_data = response.json()
        app.logger.debug(f"  Parsed AIMS JSON response: {json.dumps(aims_response_data, indent=2)}")

        if not aims_response_data.get("choices") or not aims_response_data["choices"][0].get("text"):
            app.logger.error(f"[SCA_AIMS_CALL] AIMS response missing 'choices[0].text'. Response: {aims_response_data}")
            return {"error_code": "SCA_AIMS_BAD_RESPONSE_STRUCTURE", "message": "AIMS response structure invalid.", "details": "Missing 'choices[0].text' in AIMS response."}

        full_generated_text = aims_response_data['choices'][0]['text'].strip()
        model_used_from_aims = aims_response_data.get('model_id', model_id_to_request) # Use model reported by AIMS

        app.logger.info(f"[SCA_AIMS_CALL] Extracted text (length {len(full_generated_text)}) from AIMS (model: '{model_used_from_aims}'): '{full_generated_text[:100]}...'")

        # Parse Title and Content (existing logic for newline separation)
        snippet_title = f"AI-Generated Title for {topic_info.get('title_suggestion', 'Topic')}"
        snippet_text_content = full_generated_text
        if '\n' in full_generated_text:
            parts = full_generated_text.split('\n', 1)
            potential_title = parts[0].strip()
            if 0 < len(potential_title) < 200:
                snippet_title = potential_title
                snippet_text_content = parts[1].strip() if len(parts) > 1 else ""
                if not snippet_text_content:
                    app.logger.warning("Snippet content empty after title extraction. Using full text as content.")
                    snippet_title = f"AI-Generated Title for {topic_info.get('title_suggestion', 'Topic')}"
                    snippet_text_content = full_generated_text
            else:
                app.logger.warning(f"Newline found, but first line invalid as title. Using full text as content.")
        else:
            app.logger.warning("No newline in AIMS output to separate title. Using full text as content.")
        if snippet_title == snippet_text_content and snippet_text_content == full_generated_text:
             snippet_title = f"AI-Generated Title for {topic_info.get('title_suggestion', 'Topic')}"
        if not snippet_text_content:
            snippet_text_content = full_generated_text
            if snippet_title == full_generated_text:
                 snippet_title = f"AI-Generated Snippet on {topic_info.get('title_suggestion', 'Topic')}"

        return {
            "status": "success", "title": snippet_title, "text_content": snippet_text_content,
            "summary": snippet_text_content, "llm_model_used": model_used_from_aims,
            "llm_prompt_sent": prompt, "llm_raw_output": full_generated_text
        }

    except requests.exceptions.HTTPError as e_http:
        error_details = f"AIMS HTTP Error {e_http.response.status_code}: {e_http.response.reason}."
        try: error_payload = e_http.response.json(); error_details += f" AIMS Service Msg: {error_payload}"
        except json.JSONDecodeError: error_details += f" Raw AIMS Service Response: {e_http.response.text[:200]}"
        app.logger.error(f"[SCA_AIMS_CALL] {error_details}", exc_info=True)
        return {"error_code": "SCA_AIMS_HTTP_ERROR", "message": "AIMS request failed with HTTP error.", "details": error_details, "status_code": e_http.response.status_code}
    except requests.exceptions.Timeout:
        app.logger.error(f"[SCA_AIMS_CALL] Timeout error after {timeout}s for URL: {aims_url}", exc_info=True)
        return {"error_code": "SCA_AIMS_REQUEST_TIMEOUT", "message": "Request to AIMS timed out.", "details": f"Timeout after {timeout}s.", "status_code": 408}
    except requests.exceptions.RequestException as e_req:
        app.logger.error(f"[SCA_AIMS_CALL] AIMS request exception: {e_req}", exc_info=True)
        return {"error_code": "SCA_AIMS_REQUEST_EXCEPTION", "message": "Exception during AIMS request.", "details": str(e_req), "status_code": 500}
    except json.JSONDecodeError as e_json:
        app.logger.error(f"[SCA_AIMS_CALL] JSONDecodeError parsing AIMS response: {e_json}. Raw: {response.text[:500] if 'response' in locals() else 'N/A'}", exc_info=True)
        return {"error_code": "SCA_AIMS_RESPONSE_JSON_DECODE_ERROR", "message": "Failed to decode JSON from AIMS.", "details": str(e_json), "status_code": 502}
    except (KeyError, IndexError, TypeError) as e_extract:
        app.logger.error(f"[SCA_AIMS_CALL] Error extracting content from AIMS JSON: {e_extract}. Response: {aims_response_data if 'aims_response_data' in locals() else 'N/A'}", exc_info=True)
        return {"error_code": "SCA_AIMS_RESPONSE_STRUCTURE_ERROR", "message": "Invalid structure in AIMS response.", "details": str(e_extract)}
    except Exception as e_unexpected:
        app.logger.error(f"[SCA_AIMS_CALL] Unexpected error: {e_unexpected}", exc_info=True)
        return {"error_code": "SCA_AIMS_UNEXPECTED_ERROR", "message": "Unexpected error with AIMS.", "details": str(e_unexpected)}

# parse_llm_response_for_snippet function is removed as it's no longer needed.

@app.route("/craft_snippet", methods=["POST"])
def craft_snippet_endpoint():
    try:
        try:
            request_data = flask.request.get_json()
            if not request_data: # Handles cases where request_data is None (e.g. empty body with correct content-type)
                app.logger.warning("[SCA_REQUEST] /craft_snippet: Invalid or empty JSON payload received.")
                return flask.jsonify({"error_code": "SCA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload.", "details": "Request body must be a valid non-empty JSON object."}), 400
        except Exception as e_json_decode: # Catches Werkzeug's BadRequest for malformed JSON
            app.logger.warning(f"[SCA_REQUEST] /craft_snippet: Failed to decode JSON payload: {e_json_decode}", exc_info=True)
            return flask.jsonify({"error_code": "SCA_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json_decode)}), 400

        topic_id = request_data.get("topic_id")
        content_brief = request_data.get("content_brief")
        topic_info = request_data.get("topic_info") # Will check type later
        error_trigger = request_data.get("error_trigger")

        # Validate topic_id
        if not topic_id or not isinstance(topic_id, str) or not topic_id.strip():
            app.logger.warning(f"[SCA_REQUEST] /craft_snippet: Validation failed: 'topic_id' must be a non-empty string. Received: '{topic_id}'")
            return flask.jsonify({"error_code": "SCA_INVALID_TOPIC_ID", "message": "Validation failed: 'topic_id' must be a non-empty string."}), 400

        # Validate content_brief
        if not content_brief or not isinstance(content_brief, str) or not content_brief.strip():
            app.logger.warning(f"[SCA_REQUEST] /craft_snippet: Validation failed: 'content_brief' must be a non-empty string. Received: '{content_brief}'")
            return flask.jsonify({"error_code": "SCA_INVALID_CONTENT_BRIEF", "message": "Validation failed: 'content_brief' must be a non-empty string."}), 400
        # Optional: Max length for content_brief, e.g., 1000 chars
        CONTENT_BRIEF_MAX_LENGTH = 1000
        if len(content_brief) > CONTENT_BRIEF_MAX_LENGTH:
            app.logger.warning(f"[SCA_REQUEST] /craft_snippet: Validation failed: 'content_brief' length ({len(content_brief)}) exceeds max ({CONTENT_BRIEF_MAX_LENGTH}).")
            return flask.jsonify({"error_code": "SCA_CONTENT_BRIEF_TOO_LONG", "message": f"Validation failed: 'content_brief' exceeds maximum length of {CONTENT_BRIEF_MAX_LENGTH} characters."}), 400

        # Validate topic_info
        if topic_info is None or not isinstance(topic_info, dict): # Must be present and a dictionary
            app.logger.warning(f"[SCA_REQUEST] /craft_snippet: Validation failed: 'topic_info' must be a valid JSON object (dictionary). Received: {topic_info}")
            return flask.jsonify({"error_code": "SCA_INVALID_TOPIC_INFO", "message": "Validation failed: 'topic_info' must be a valid JSON object (dictionary)."}), 400

        # error_trigger is optional and for testing, no specific validation needed for its value

        app.logger.info(f"[SCA_REQUEST] /craft_snippet. TopicID: '{topic_id}', Brief: '{content_brief}', Trigger: '{error_trigger}'")
        if error_trigger == "sca_error":
            return flask.jsonify({"error_code": "SCA_SIMULATED_ERROR", "message": "Simulated SCA error."}), 500

        prompt_parts = [f"Generate a short, engaging podcast snippet title and content (around 2-3 sentences). Subject: '{content_brief}'."]
        if topic_info:
            summary = topic_info.get("summary"); keywords = topic_info.get("keywords"); sources = topic_info.get("potential_sources")
            if summary and summary != content_brief: prompt_parts.append(f"Context: '{summary}'.")
            if keywords and isinstance(keywords, list) and keywords:
                unique_kw = [kw for kw in keywords if kw.lower() not in content_brief.lower() and (not summary or kw.lower() not in summary.lower())]
                if unique_kw: prompt_parts.append(f"Keywords: {', '.join(unique_kw)}.")
            if sources and isinstance(sources, list) and sources:
                src_titles = [src.get("title", src.get("url", "a source")) for src in sources[:1] if isinstance(src, dict)]
                if src_titles: prompt_parts.append(f"Source inspiration: '{src_titles[0]}'.")
        prompt_parts.append("Output format: Title on its own line, then content on the next line(s).")
        prompt = " ".join(prompt_parts)
        
        llm_model_used = "unknown"; llm_prompt_used = prompt
        if sca_config['USE_REAL_LLM_SERVICE']:
            app.logger.info("Using REAL LLM service via AIMS.") # Use app.logger
            llm_result = call_real_llm_service(prompt, topic_info)
            if "error_code" in llm_result:
                return flask.jsonify(llm_result), llm_result.get("status_code", 500)
            snippet_title = llm_result.get("title"); snippet_text_content = llm_result.get("text_content")
            llm_model_used = llm_result.get("llm_model_used", sca_config['SCA_LLM_MODEL_ID'])
            llm_prompt_used = llm_result.get("llm_prompt_sent", prompt)
        else:
            app.logger.info("Using SIMULATED/PLACEHOLDER LLM response.") # Use app.logger
            placeholder_result = call_aims_llm_placeholder(prompt, topic_info)
            # Directly use title and text_content from the placeholder_result
            snippet_title = placeholder_result.get("title", "Default Placeholder Title")
            snippet_text_content = placeholder_result.get("text_content", "Default placeholder content.")
            llm_model_used = placeholder_result.get("llm_model_used", "Placeholder-v0.2")
            llm_prompt_used = placeholder_result.get("llm_prompt_sent", prompt)
        
        snippet_id = generate_snippet_id(); timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        audio_url_placeholder = f"https://aethercast.com/placeholder_audio/{snippet_id}.mp3"
        snippet_data_object = {
            "snippet_id": snippet_id, "topic_id": topic_id, "title": snippet_title,
            "summary": snippet_text_content, "audio_url": audio_url_placeholder,
            "text_content": snippet_text_content, "cover_art_prompt": f"Podcast cover: {str(snippet_title)}",
            "generation_timestamp": timestamp, "llm_prompt_used": llm_prompt_used,
            "llm_model_used": llm_model_used, "original_topic_details_from_tda": topic_info
        }
        app.logger.info(f"[SCA_RESPONSE] Snippet crafted: {snippet_id}. Title: '{snippet_title}'") # Use app.logger
        return flask.jsonify(snippet_data_object), 200
    except Exception as e:
        app.logger.error(f"Error in /craft_snippet: {e}", exc_info=True) # Use app.logger
        return flask.jsonify({"error_code": "SCA_INTERNAL_SERVER_ERROR", "message": "Unexpected SCA error.", "details": str(e)}), 500

if __name__ == "__main__":
    # Initial "JSON logging configured..." message is now part of setup_json_logging
    app.logger.info(f"--- SCA Service starting on {os.getenv('SCA_HOST', '0.0.0.0')}:{int(os.getenv('SCA_PORT', 5002))} (Debug: {(os.getenv('FLASK_DEBUG', 'True').lower()=='true')}) ---")
    app.run(host=os.getenv("SCA_HOST", "0.0.0.0"), port=int(os.getenv("SCA_PORT", 5002)), debug=(os.getenv("FLASK_DEBUG", "True").lower()=='true'))
