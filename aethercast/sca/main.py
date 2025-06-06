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

    logging.info("SCA Configuration Loaded:")
    for key, value in sca_config.items():
        logging.info(f"  {key}: {value}")

    if sca_config['USE_REAL_LLM_SERVICE']:
        missing_configs = []
        if not sca_config['AIMS_SERVICE_URL']: # Check AIMS URL if real service is selected
            missing_configs.append("AIMS_SERVICE_URL")
        if not sca_config['SCA_LLM_MODEL_ID']: # Still need a model to request from AIMS
            missing_configs.append("SCA_LLM_MODEL_ID")
        
        if missing_configs:
            error_message = f"CRITICAL: USE_REAL_LLM_SERVICE is true, but required configurations are missing: {', '.join(missing_configs)}."
            logging.critical(error_message)
            raise ValueError(error_message)
        else:
            logging.info("SCA is configured to use a REAL LLM service via AIMS.")
    else:
        logging.info("SCA is configured to use the SIMULATED/PLACEHOLDER LLM response (bypassing AIMS).")

load_sca_configuration()

app = flask.Flask(__name__)

AIMS_LLM_PLACEHOLDER_URL = "http://localhost:8000/v1/generate" # Kept for placeholder, though not used if USE_REAL_LLM_SERVICE=true
AIMS_LLM_HARDCODED_RESPONSE = { /* ... existing hardcoded response ... */ }

def generate_snippet_id() -> str:
    return f"snippet_{uuid.uuid4().hex[:12]}"

def call_aims_llm_placeholder(prompt: str, topic_info: dict) -> dict:
    # This function remains for USE_REAL_LLM_SERVICE=false, unchanged internally
    if sca_config['USE_REAL_LLM_SERVICE']:
        logging.warning("[SCA_AIMS_CALL] call_aims_llm_placeholder invoked while USE_REAL_LLM_SERVICE is true. This indicates a logic path needs review. Using dynamic placeholder as fallback.")
    logging.info("[SCA_AIMS_CALL] Dynamically generating SIMULATED AIMS LLM response for snippet.")
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
        "llm_response_direct": response,
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

    logging.info(f"[SCA_AIMS_CALL] Preparing to call AIMS. URL: {aims_url}, Model: {model_id_to_request}")

    if not aims_url: # Should be caught by load_sca_configuration, but as safeguard
        logging.error("[SCA_AIMS_CALL] AIMS_SERVICE_URL is not configured.")
        return {"error_code": "SCA_AIMS_CONFIG_MISSING", "message": "AIMS_SERVICE_URL not configured.", "details": "AIMS service URL is missing."}

    aims_payload = {
        "prompt": prompt,
        "model_id_override": model_id_to_request,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # "response_format": {"type": "text"} # AIMS default is text, explicit if needed
    }
    
    logging.debug(f"  AIMS Request Payload: {json.dumps(aims_payload)}")

    try:
        response = requests.post(aims_url, json=aims_payload, timeout=timeout)
        logging.info(f"[SCA_AIMS_CALL] AIMS Response Status Code: {response.status_code}")
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)

        aims_response_data = response.json()
        logging.debug(f"  Parsed AIMS JSON response: {json.dumps(aims_response_data, indent=2)}")

        if not aims_response_data.get("choices") or not aims_response_data["choices"][0].get("text"):
            logging.error(f"[SCA_AIMS_CALL] AIMS response missing 'choices[0].text'. Response: {aims_response_data}")
            return {"error_code": "SCA_AIMS_BAD_RESPONSE_STRUCTURE", "message": "AIMS response structure invalid.", "details": "Missing 'choices[0].text' in AIMS response."}

        full_generated_text = aims_response_data['choices'][0]['text'].strip()
        model_used_from_aims = aims_response_data.get('model_id', model_id_to_request) # Use model reported by AIMS

        logging.info(f"[SCA_AIMS_CALL] Extracted text (length {len(full_generated_text)}) from AIMS (model: '{model_used_from_aims}'): '{full_generated_text[:100]}...'")

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
                    logging.warning("Snippet content empty after title extraction. Using full text as content.")
                    snippet_title = f"AI-Generated Title for {topic_info.get('title_suggestion', 'Topic')}"
                    snippet_text_content = full_generated_text
            else:
                logging.warning(f"Newline found, but first line invalid as title. Using full text as content.")
        else:
            logging.warning("No newline in AIMS output to separate title. Using full text as content.")
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
        logging.error(f"[SCA_AIMS_CALL] {error_details}", exc_info=True)
        return {"error_code": "SCA_AIMS_HTTP_ERROR", "message": "AIMS request failed with HTTP error.", "details": error_details, "status_code": e_http.response.status_code}
    except requests.exceptions.Timeout:
        logging.error(f"[SCA_AIMS_CALL] Timeout error after {timeout}s for URL: {aims_url}", exc_info=True)
        return {"error_code": "SCA_AIMS_REQUEST_TIMEOUT", "message": "Request to AIMS timed out.", "details": f"Timeout after {timeout}s.", "status_code": 408}
    except requests.exceptions.RequestException as e_req:
        logging.error(f"[SCA_AIMS_CALL] AIMS request exception: {e_req}", exc_info=True)
        return {"error_code": "SCA_AIMS_REQUEST_EXCEPTION", "message": "Exception during AIMS request.", "details": str(e_req), "status_code": 500}
    except json.JSONDecodeError as e_json:
        logging.error(f"[SCA_AIMS_CALL] JSONDecodeError parsing AIMS response: {e_json}. Raw: {response.text[:500] if 'response' in locals() else 'N/A'}", exc_info=True)
        return {"error_code": "SCA_AIMS_RESPONSE_JSON_DECODE_ERROR", "message": "Failed to decode JSON from AIMS.", "details": str(e_json), "status_code": 502}
    except (KeyError, IndexError, TypeError) as e_extract:
        logging.error(f"[SCA_AIMS_CALL] Error extracting content from AIMS JSON: {e_extract}. Response: {aims_response_data if 'aims_response_data' in locals() else 'N/A'}", exc_info=True)
        return {"error_code": "SCA_AIMS_RESPONSE_STRUCTURE_ERROR", "message": "Invalid structure in AIMS response.", "details": str(e_extract)}
    except Exception as e_unexpected:
        logging.error(f"[SCA_AIMS_CALL] Unexpected error: {e_unexpected}", exc_info=True)
        return {"error_code": "SCA_AIMS_UNEXPECTED_ERROR", "message": "Unexpected error with AIMS.", "details": str(e_unexpected)}

def parse_llm_response_for_snippet(llm_response_text: str) -> tuple[str, str]:
    # This function is primarily for the placeholder and might be simplified if placeholder changes
    logging.debug(f"[SCA_DEPRECATED_PARSER] parse_llm_response_for_snippet called with text: '{llm_response_text[:100]}...'")
    title_part_key = "generic title: '"
    content_part_key = "generic content: '"
    title_start_index = llm_response_text.find(title_part_key)
    content_start_index = llm_response_text.find(content_part_key)
    if title_start_index != -1 and content_start_index != -1:
        title_start = title_start_index + len(title_part_key)
        title_end = llm_response_text.find("'", title_start)
        extracted_title = llm_response_text[title_start:title_end] if title_end != -1 else "Default Snippet Title (parsed)"
        content_start = content_start_index + len(content_part_key)
        content_end = llm_response_text.rfind("'")
        extracted_content = llm_response_text[content_start:content_end] if content_end > content_start else llm_response_text[content_start:]
        return extracted_title, extracted_content
    logging.warning(f"Could not parse title/content using old placeholder logic: '{llm_response_text[:100]}...'")
    return "Default Snippet Title (parse failed)", llm_response_text

@app.route("/craft_snippet", methods=["POST"])
def craft_snippet_endpoint():
    try:
        request_data = flask.request.get_json()
        if not request_data:
            return flask.jsonify({"error_code": "SCA_INVALID_PAYLOAD", "message": "Invalid JSON payload."}), 400
        topic_id = request_data.get("topic_id"); content_brief = request_data.get("content_brief")
        topic_info = request_data.get("topic_info", {}); error_trigger = request_data.get("error_trigger")
        if not topic_id or not content_brief:
            return flask.jsonify({"error_code": "SCA_MISSING_FIELDS", "message": "'topic_id' and 'content_brief' required."}), 400
        logging.info(f"[SCA_REQUEST] /craft_snippet. TopicID: '{topic_id}', Brief: '{content_brief}', Trigger: '{error_trigger}'")
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
            logging.info("Using REAL LLM service via AIMS.")
            llm_result = call_real_llm_service(prompt, topic_info)
            if "error_code" in llm_result:
                return flask.jsonify(llm_result), llm_result.get("status_code", 500)
            snippet_title = llm_result.get("title"); snippet_text_content = llm_result.get("text_content")
            llm_model_used = llm_result.get("llm_model_used", sca_config['SCA_LLM_MODEL_ID'])
            llm_prompt_used = llm_result.get("llm_prompt_sent", prompt)
        else:
            logging.info("Using SIMULATED/PLACEHOLDER LLM response.")
            placeholder_result = call_aims_llm_placeholder(prompt, topic_info)
            generated_text_full = placeholder_result.get("llm_response_direct", {}).get("choices", [{}])[0].get("text", "Error: Placeholder format issue.")
            snippet_title, snippet_text_content = parse_llm_response_for_snippet(generated_text_full)
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
        logging.info(f"[SCA_RESPONSE] Snippet crafted: {snippet_id}. Title: '{snippet_title}'")
        return flask.jsonify(snippet_data_object), 200
    except Exception as e:
        logging.error(f"Error in /craft_snippet: {e}", exc_info=True)
        return flask.jsonify({"error_code": "SCA_INTERNAL_SERVER_ERROR", "message": "Unexpected SCA error.", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(host=os.getenv("SCA_HOST", "0.0.0.0"), port=int(os.getenv("SCA_PORT", 5002)), debug=(os.getenv("FLASK_DEBUG", "True").lower()=='true'))
