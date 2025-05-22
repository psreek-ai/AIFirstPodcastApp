import flask
import uuid
import datetime
import logging
import json
import requests # For calling AIMS (LLM)

app = flask.Flask(__name__)

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- AIMS (LLM) Placeholder Configuration ---
AIMS_LLM_PLACEHOLDER_URL = "http://localhost:8000/v1/generate" # Assuming AIMS LLM placeholder runs here
# This is the hardcoded response from aethercast/aims/llm_api_placeholder.md
AIMS_LLM_HARDCODED_RESPONSE = {
  "request_id": "aims-llm-placeholder-req-123",
  "model_id": "AetherLLM-Placeholder-v0.1",
  "choices": [
    {
      "text": "This is a placeholder response from the AIMS LLM service. Based on your prompt, here's a generic title: 'Interesting Developments' and some generic content: 'Several interesting developments have occurred recently, leading to much discussion and speculation within the community. Further analysis is required to fully understand the implications.'",
      "finish_reason": "length"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 50,
    "total_tokens": 60
  }
}
# For actual interaction with a running AIMS placeholder, set this to True
# For now, False means we use the hardcoded response directly to avoid dependency on a running AIMS service during this dev stage.
SIMULATE_AIMS_LLM_CALL = False


# --- Helper Functions ---
def generate_snippet_id() -> str:
    """Generates a unique snippet ID."""
    return f"snippet_{uuid.uuid4().hex[:12]}"

def call_aims_llm_placeholder(prompt: str, topic_info: dict) -> dict:
    """
    Simulates calling the AIMS LLM placeholder or calls it if SIMULATE_AIMS_LLM_CALL is True.
    """
    logging.info(f"[SCA_AIMS_CALL] Calling AIMS LLM placeholder. Prompt: '{prompt}'")
    
    if SIMULATE_AIMS_LLM_CALL:
        payload = {
            "model_id": "AetherLLM-Snippet-v1", # Placeholder model
            "prompt": prompt,
            "max_tokens": 150, # Typical for a snippet
            "temperature": 0.7,
            "context": {
                "topic_keywords": topic_info.get("keywords", []),
                "source_suggestion": topic_info.get("title_suggestion", "N/A")
            },
            "response_format": "text"
        }
        try:
            response = requests.post(AIMS_LLM_PLACEHOLDER_URL, json=payload, timeout=10)
            response.raise_for_status()
            llm_response = response.json()
            logging.info(f"[SCA_AIMS_CALL_SUCCESS] Received response from AIMS LLM: {llm_response}")
            return llm_response
        except requests.exceptions.RequestException as e:
            logging.error(f"[SCA_AIMS_CALL_ERROR] Error calling AIMS LLM placeholder: {e}. Falling back to hardcoded response.")
            return AIMS_LLM_HARDCODED_RESPONSE # Fallback
        except json.JSONDecodeError as e:
            logging.error(f"[SCA_AIMS_CALL_ERROR] Error decoding JSON from AIMS LLM: {e}. Falling back to hardcoded response.")
            return AIMS_LLM_HARDCODED_RESPONSE # Fallback
    else:
        logging.info("[SCA_AIMS_CALL] Dynamically generating AIMS LLM response for snippet (SIMULATE_AIMS_LLM_CALL is False).")
        import time
        time.sleep(0.1) # Simulate minimal processing

        # Dynamically create the response based on prompt/topic_info
        # topic_info contains 'title_suggestion' (from TDA, passed as content_brief to SCA)
        # and 'keywords'
        
        title_suggestion = topic_info.get("title_suggestion", "Interesting Developments")
        keywords = topic_info.get("keywords", [])
        
        dynamic_title = f"Insights on {title_suggestion}"
        if keywords:
            dynamic_content = f"Exploring {title_suggestion}, focusing on {', '.join(keywords)}. This area shows promising advancements and requires further analysis."
        else:
            dynamic_content = f"A closer look at {title_suggestion}. Several interesting developments have occurred, leading to much discussion."

        # Ensure the dynamic text still fits the parsing logic of parse_llm_response_for_snippet
        # The parser looks for "generic title: '" and "generic content: '"
        # So, we will embed our dynamic parts within that structure.
        
        dynamic_response_text = f"This is a placeholder response from the AIMS LLM service. Based on your prompt, here's a generic title: '{dynamic_title}' and some generic content: '{dynamic_content}'"

        # Create a new response object, copying structure from AIMS_LLM_HARDCODED_RESPONSE
        # but with the new dynamic text.
        response = json.loads(json.dumps(AIMS_LLM_HARDCODED_RESPONSE)) # Deep copy
        response["choices"][0]["text"] = dynamic_response_text
        response["request_id"] = f"aims-llm-placeholder-req-dynamic-{uuid.uuid4().hex[:6]}"
        response["model_id"] = "AetherLLM-Placeholder-DynamicSnippet-v0.2"
        
        # Update token counts very roughly based on new text length
        response["usage"]["prompt_tokens"] = len(prompt.split()) // 4 # Rough estimate
        response["usage"]["completion_tokens"] = len(dynamic_response_text.split()) // 4
        response["usage"]["total_tokens"] = response["usage"]["prompt_tokens"] + response["usage"]["completion_tokens"]
        
        return response

def parse_llm_response_for_snippet(llm_response_text: str) -> tuple[str, str]:
    """
    Parses the text from the LLM response to extract a title and content.
    This is a very basic parser for the known hardcoded response format.
    A real LLM might return structured JSON or require more sophisticated parsing.
    """
    try:
        # Example parsing logic for the hardcoded response:
        # "Based on your prompt, here's a generic title: 'Interesting Developments' and some generic content: 'Several interesting developments...'"
        title_part_key = "generic title: '"
        content_part_key = "generic content: '"

        title_start_index = llm_response_text.find(title_part_key)
        content_start_index = llm_response_text.find(content_part_key)

        if title_start_index != -1 and content_start_index != -1:
            title_start = title_start_index + len(title_part_key)
            title_end = llm_response_text.find("'", title_start)
            extracted_title = llm_response_text[title_start:title_end] if title_end != -1 else "Default Snippet Title"

            content_start = content_start_index + len(content_part_key)
            content_end = llm_response_text.rfind("'", content_start, -1) # Find last quote to handle potential quotes in content but not perfect
            if content_end == -1: # if no closing quote for content, take rest of string
                content_end = len(llm_response_text) -1 # up to the final character (often a period or quote)
            
            extracted_content = llm_response_text[content_start:content_end]
            # Remove the trailing period if it's the last character of the hardcoded response
            if extracted_content.endswith('.'):
                 extracted_content = extracted_content[:-1]


            return extracted_title, extracted_content
        else:
            logging.warning(f"Could not parse title/content from LLM response: '{llm_response_text[:100]}...' Using defaults.")
            return "Default Snippet Title", llm_response_text # Fallback
    except Exception as e:
        logging.error(f"Error parsing LLM response: {e}. Text: '{llm_response_text[:100]}...'")
        return "Error Parsing Title", "Error parsing content from LLM."


# --- API Endpoint ---
@app.route("/craft_snippet", methods=["POST"])
def craft_snippet_endpoint():
    """
    API endpoint for CPOA to request snippet generation.
    Accepts a JSON payload with 'topic_id' and 'content_brief' (which might be a topic title or summary).
    """
    try:
        request_data = flask.request.get_json()
        if not request_data:
            return flask.jsonify({"error": "Invalid JSON payload"}), 400

        topic_id = request_data.get("topic_id")
        content_brief = request_data.get("content_brief") # This is often the topic title or a short description
        # topic_info could be more structured in future, e.g. full TopicObject from TDA
        topic_info = request_data.get("topic_info", {}) # Optional full topic details
        error_trigger = request_data.get("error_trigger") # For simulating errors

        if not topic_id or not content_brief: # content_brief is essential for basic prompt
            return flask.jsonify({"error": "'topic_id' and 'content_brief' are required."}), 400

        logging.info(f"[SCA_REQUEST] Received /craft_snippet request. Topic ID: '{topic_id}', Brief: '{content_brief}', ErrorTrigger: '{error_trigger}'")
        # logging.info(f"[SCA_REQUEST] Full topic_info received: {json.dumps(topic_info, indent=2)}") # Too verbose for default log

        if error_trigger == "sca_error":
            logging.warning(f"[SCA_SIMULATED_ERROR] Simulating an error for /craft_snippet based on error_trigger: {error_trigger}")
            return flask.jsonify({
                "error": "Simulated SCA Error",
                "details": "This is a controlled error triggered for testing purposes in SnippetCraftAgent."
            }), 500

        # 1. Formulate Prompt for AIMS LLM
        prompt_parts = [
            f"Generate a short, engaging podcast snippet title and content (around 2-3 sentences) for a topic."
        ]
        if topic_info: # topic_info is the full TopicObject from TDA
            title_suggestion = topic_info.get("title_suggestion", content_brief) # Use TDA's title if available
            prompt_parts.append(f"The suggested title for this topic is: '{title_suggestion}'.")
            
            summary_from_topic = topic_info.get("summary")
            if summary_from_topic:
                prompt_parts.append(f"The topic is broadly about: '{summary_from_topic}'.")
            
            keywords = topic_info.get("keywords")
            if keywords and isinstance(keywords, list) and len(keywords) > 0:
                prompt_parts.append(f"Key aspects or keywords to focus on include: {', '.join(keywords)}.")
            
            potential_sources = topic_info.get("potential_sources")
            if potential_sources and isinstance(potential_sources, list) and len(potential_sources) > 0:
                source_titles = [src.get("title", src.get("url", "a source")) for src in potential_sources[:2] if isinstance(src, dict)]
                if source_titles:
                    prompt_parts.append(f"It draws inspiration from sources like: {'; '.join(source_titles)}.")
        else: # Fallback if topic_info is minimal
            prompt_parts.append(f"The content brief is: '{content_brief}'.")
            
        prompt_parts.append("The snippet should be catchy, concise, and suitable for a general audience.")
        prompt = " ".join(prompt_parts)
        
        # 2. Call AIMS LLM Placeholder
        llm_response = call_aims_llm_placeholder(prompt, topic_info)
        
        # 3. Parse LLM Response and Structure SnippetDataObject
        # Assuming the LLM response text is in llm_response["choices"][0]["text"]
        generated_text_full = llm_response.get("choices", [{}])[0].get("text", "Error: LLM response format unexpected.")
        
        # Basic parsing for the hardcoded LLM response.
        # A real LLM might give structured output or need more advanced parsing.
        snippet_title, snippet_text_content = parse_llm_response_for_snippet(generated_text_full)

        snippet_id = generate_snippet_id()
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"

        # Placeholder for audio_url - This is what CPOA's placeholder currently expects.
        # In a future step, SCA would call AIMS_TTS to get a real audio URL.
        # The field name `audio_url` matches CPOA's current `call_snippet_craft_agent` expectation.
        audio_url_placeholder = f"https://aethercast.com/placeholder_audio/{snippet_id}.mp3"

        # Constructing SnippetDataObject based on `docs/architecture/AI_Agents_Overview.md`
        # and matching CPOA's current `call_snippet_craft_agent` expected return structure.
        snippet_data_object = {
            "snippet_id": snippet_id,
            "topic_id": topic_id,
            "title": snippet_title, # From LLM
            "summary": snippet_text_content, # Using 'summary' field as per CPOA's current expectation for the main text
            "audio_url": audio_url_placeholder, # Placeholder, CPOA expects this
            # --- Fields from SnippetDataObject spec in docs ---
            "text_content": snippet_text_content, # Actual text content
            "cover_art_prompt": f"Podcast snippet cover art for: {snippet_title}", # Example prompt
            "generation_timestamp": timestamp,
            # --- Additional useful info ---
            "llm_prompt_used": prompt, # For traceability
            "llm_model_used": llm_response.get("model_id", "unknown"),
            "original_topic_details_from_tda": topic_info # Store for traceability/debugging
        }
        
        logging.info(f"[SCA_RESPONSE] Snippet crafted: {snippet_id} for topic {topic_id}. Title: '{snippet_title}'")
        return flask.jsonify(snippet_data_object), 200

    except Exception as e:
        logging.error(f"Error in /craft_snippet endpoint: {e}", exc_info=True)
        return flask.jsonify({"error": f"Internal server error in SCA: {str(e)}"}), 500

if __name__ == "__main__":
    # Run SCA on a different port than CPOA and TDA
    # Example: python aethercast/sca/main.py
    # For development, SIMULATE_AIMS_LLM_CALL = False uses hardcoded LLM response.
    # To test with a live AIMS LLM placeholder, set SIMULATE_AIMS_LLM_CALL = True
    # and ensure the AIMS LLM placeholder service is running on AIMS_LLM_PLACEHOLDER_URL.
    app.run(host="0.0.0.0", port=5002, debug=True)
```
