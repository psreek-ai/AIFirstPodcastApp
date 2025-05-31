import logging
import os
from dotenv import load_dotenv # Added
from flask import Flask, request, jsonify

# --- Load Environment Variables ---
load_dotenv() # Added

# --- PSWA Configuration ---
pswa_config = {}

def load_pswa_configuration():
    """Loads PSWA configurations from environment variables with defaults."""
    global pswa_config
    pswa_config['OPENAI_API_KEY'] = os.getenv("OPENAI_API_KEY")
    pswa_config['PSWA_LLM_MODEL'] = os.getenv("PSWA_LLM_MODEL", "gpt-3.5-turbo")
    pswa_config['PSWA_LLM_TEMPERATURE'] = float(os.getenv("PSWA_LLM_TEMPERATURE", "0.7"))
    pswa_config['PSWA_LLM_MAX_TOKENS'] = int(os.getenv("PSWA_LLM_MAX_TOKENS", "1500"))

    default_system_message = "You are a podcast scriptwriter tasked with creating well-structured podcast scripts."
    pswa_config['PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE'] = os.getenv("PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE", default_system_message)

    default_user_template = """You are an expert podcast scriptwriter. Your goal is to create an engaging and informative podcast script based on the provided topic and content.

Topic: "{topic}"

Provided Content:
---
{content}
---

Please structure your script as follows, using the exact formatting cues:
[TITLE] Your Podcast Title Here
[INTRO] A brief introduction to the topic and what the podcast will cover.
[SEGMENT_1_TITLE] Title for the first main segment.
[SEGMENT_1_CONTENT] Detailed content for the first segment, derived from the provided content.
(You can add more segments like [SEGMENT_2_TITLE], [SEGMENT_2_CONTENT] if the content warrants it, typically 1-2 main segments are enough for a short podcast unless content is very rich.)
[OUTRO] A concluding summary and call to action or final thought.

Ensure the tone is informative yet engaging for a general audience.
The script should be well-organized and flow naturally.
Only output the script itself, with no additional commentary before or after.
If the provided content is sparse or insufficient to generate a full script as described, please indicate this by starting the script with: "[ERROR] Insufficient content provided to generate a full podcast script for the topic: {topic}" and do not generate the rest of the script structure."""
    pswa_config['PSWA_DEFAULT_PROMPT_USER_TEMPLATE'] = os.getenv("PSWA_DEFAULT_PROMPT_USER_TEMPLATE", default_user_template)

    pswa_config['PSWA_HOST'] = os.getenv("PSWA_HOST", "0.0.0.0")
    pswa_config['PSWA_PORT'] = int(os.getenv("PSWA_PORT", 5004))
    pswa_config['PSWA_DEBUG'] = os.getenv("PSWA_DEBUG", "True").lower() == "true"

    logger.info("--- PSWA Configuration ---")
    for key, value in pswa_config.items():
        if "API_KEY" in key and value:
            logger.info(f"  {key}: {'*' * (len(value) - 4) + value[-4:] if len(value) > 4 else '****'}")
        elif key in ["PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE", "PSWA_DEFAULT_PROMPT_USER_TEMPLATE"]:
            logger.info(f"  {key}: Loaded (length: {len(value)})") # Log length for long strings
        else:
            logger.info(f"  {key}: {value}")
    logger.info("--- End PSWA Configuration ---")

    if not pswa_config['OPENAI_API_KEY']:
        logger.error("CRITICAL: OPENAI_API_KEY is not set. PSWA will not be able to function.")
        # Optionally raise an error here if you want to prevent startup
        # raise ValueError("OPENAI_API_KEY is required for PSWA to operate.")

# --- Attempt to import OpenAI library ---
try:
    import openai
    PSWA_IMPORTS_SUCCESSFUL = True
    PSWA_MISSING_IMPORT_ERROR = None
except ImportError as e:
    PSWA_IMPORTS_SUCCESSFUL = False
    PSWA_MISSING_IMPORT_ERROR = e
    # Define placeholder for openai.error.OpenAIError if openai itself failed to import
    # This allows the try-except block in weave_script to still reference it.
    class OpenAIErrorPlaceholder(Exception): pass
    if 'openai' not in globals(): # If openai module itself is not loaded
        # Create a dummy openai object with a dummy error attribute
        class DummyOpenAI:
            error = type('error', (object,), {'OpenAIError': OpenAIErrorPlaceholder})()
        openai = DummyOpenAI()
    elif not hasattr(openai, 'error'): # If openai is loaded but has no 'error' attribute (unlikely for real lib)
        openai.error = type('error', (object,), {'OpenAIError': OpenAIErrorPlaceholder})()
    elif not hasattr(openai.error, 'OpenAIError'): # If openai.error exists but no OpenAIError (very unlikely)
        openai.error.OpenAIError = OpenAIErrorPlaceholder


# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
# Ensure logger name is distinct if other modules also configure root logger
# Use Flask's logger if available and not the root logger to avoid duplicate messages when running with Flask.
if app.logger and app.logger.name != 'root':
    logger = app.logger
else:
    logger = logging.getLogger(__name__) # Use module-specific logger
    if not logger.hasHandlers(): # Avoid adding multiple handlers if script re-run in some contexts
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - PSWA - %(message)s')
    # Ensure load_pswa_configuration is called after logger is configured if it uses logger.
    # If logger was just configured, and pswa_config is empty, reload.
    if not pswa_config: # Check if it's empty
        load_pswa_configuration() # Call it if it wasn't called or completed due to logger

# Ensure configuration is loaded at startup if not already by the above check
if not pswa_config:
    load_pswa_configuration()


import uuid # For script_id
import re # For parsing

def parse_llm_script_output(raw_script_text: str, topic: str) -> dict:
    """
    Parses the raw text output from the LLM into a structured script dictionary.
    """
    script_id = f"pswa_script_{uuid.uuid4().hex}"

    # Default values
    parsed_script = {
        "script_id": script_id,
        "topic": topic,
        "title": f"Podcast on {topic}", # Default title
        "full_raw_script": raw_script_text,
        "segments": [],
        "llm_model_used": pswa_config.get('PSWA_LLM_MODEL', "gpt-3.5-turbo") # Get from config
    }

    # Check for critical LLM-reported error first
    if raw_script_text.startswith("[ERROR] Insufficient content"):
        logger.warning(f"[PSWA_PARSING] LLM indicated insufficient content for topic '{topic}'.")
        # No specific segments, title might not be relevant.
        # The full_raw_script already contains the error.
        # The endpoint will handle returning this as an error to CPOA.
        # For structured output, we can reflect this.
        parsed_script["title"] = f"Error: Insufficient Content for {topic}"
        parsed_script["segments"].append({
            "segment_title": "ERROR",
            "content": raw_script_text
        })
        return parsed_script # Return early with error reflected in structure

    # Regex to find all tagged sections. Dotall for multiline content.
    # Order of tags in this regex matters for segment ordering if not strictly sequential.
    # This regex finds a tag, then captures everything until the next tag or end of string.
    # It's a bit greedy, so we process sequentially.

    # Define tags we expect, including optional segment numbers
    # This version tries to capture specific known tags and then generic segments.
    # It's more robust to capture all [TAG] Content patterns and then map them.

    # Simpler approach: Split by lines and process lines that are tags
    # and lines that are content.

    # Let's use a regex to find all [TAG_NAME] occurrences and the text that follows them.
    # This regex captures the tag name (e.g., TITLE, INTRO, SEGMENT_1_TITLE)
    # and the content until the next tag or end of string.

    # Revised parsing strategy:
    # 1. Extract [TITLE]Value
    # 2. Then process the rest for [TAG]Content pairs sequentially.

    title_match = re.search(r"\[TITLE\](.*?)\n", raw_script_text, re.IGNORECASE)
    if title_match:
        parsed_script["title"] = title_match.group(1).strip()

    # Find all other tags and their content.
    # This regex looks for a tag like [ANY_TAG_NAME_HERE] and captures the tag name and the content following it.
    # The content is captured non-greedily (.*?) until a lookahead assertion finds the next tag or end of string.
    pattern = re.compile(r"\[([A-Z0-9_]+)\](.*?)(?=\n\[[A-Z0-9_]+\]|\Z)", re.IGNORECASE | re.DOTALL)

    # We skip [TITLE] as it's handled separately for the main title.
    # For segments, we need to pair up _TITLE and _CONTENT.

    current_segment_title = None

    # More direct parsing based on known tags in sequence:
    tag_sequence = ["INTRO", "SEGMENT_1_TITLE", "SEGMENT_1_CONTENT",
                    "SEGMENT_2_TITLE", "SEGMENT_2_CONTENT", # Optional
                    "SEGMENT_3_TITLE", "SEGMENT_3_CONTENT", # Optional
                    "OUTRO"]

    # Simple split-based parser, less reliant on complex regex for flow
    lines = raw_script_text.splitlines()
    current_tag_content = []
    active_tag = None

    for line in lines:
        line = line.strip()
        # Check if line is a tag
        match = re.fullmatch(r"\[([A-Z0-9_]+)\]", line, re.IGNORECASE)
        if match:
            if active_tag and current_tag_content: # Save previous tag's content
                if active_tag.upper() == "TITLE" and not parsed_script["title"]: # If title wasn't caught by initial regex
                    parsed_script["title"] = "\n".join(current_tag_content).strip()
                else:
                    parsed_script["segments"].append({
                        "segment_title": active_tag,
                        "content": "\n".join(current_tag_content).strip()
                    })
            active_tag = match.group(1).upper() # Normalize tag name
            current_tag_content = []
            if active_tag == "TITLE" and parsed_script["title"] == f"Podcast on {topic}":
                # If we already have a title from the initial regex, this [TITLE] tag in body is ignored
                # or could be treated as a segment if needed. For now, main title is prioritized.
                pass
        elif active_tag:
            current_tag_content.append(line)

    # Add the last captured segment
    if active_tag and current_tag_content:
         if active_tag.upper() == "TITLE" and parsed_script["title"] == f"Podcast on {topic}":
             parsed_script["title"] = "\n".join(current_tag_content).strip()
         else:
            parsed_script["segments"].append({
                "segment_title": active_tag,
                "content": "\n".join(current_tag_content).strip()
            })

    # Post-process to pair up SEGMENT_X_TITLE and SEGMENT_X_CONTENT
    processed_segments = []
    i = 0
    while i < len(parsed_script["segments"]):
        segment = parsed_script["segments"][i]
        title = segment["segment_title"]
        content = segment["content"]

        if title.endswith("_TITLE") and (i + 1 < len(parsed_script["segments"])):
            next_segment = parsed_script["segments"][i+1]
            if next_segment["segment_title"] == title.replace("_TITLE", "_CONTENT"):
                processed_segments.append({
                    "segment_title": content, # The content of _TITLE tag is the actual title string
                    "content": next_segment["content"]
                })
                i += 1 # Skip next segment as it's consumed
            else: # Title without matching content, treat as simple segment
                processed_segments.append({"segment_title": title, "content": content})
        elif title in ["INTRO", "OUTRO"]:
             processed_segments.append({"segment_title": title, "content": content})
        elif not title.endswith("_CONTENT"): # Other non-content tags, or content for a title already processed
            processed_segments.append({"segment_title": title, "content": content})
        i += 1
    parsed_script["segments"] = processed_segments

    # Basic validation: Ensure essential parts are present
    if not parsed_script["title"] or not any(s["segment_title"] == "INTRO" for s in parsed_script["segments"]):
        logger.warning(f"[PSWA_PARSING] Critical tags ([TITLE] or [INTRO]) missing or failed to parse for topic '{topic}'. LLM Output: '{raw_script_text[:200]}...'")
        # Decide if this is a critical parsing failure. For now, we'll return what we have.
        # Could add an error flag to parsed_script here.

    return parsed_script


def weave_script(content: str, topic: str) -> dict: # Return type changed to dict
    """
    Generates a podcast script using the configured LLM and parses it into a structured dict.
    Returns a dictionary, which will include an 'error' key if something went wrong,
    or the structured script data on success.
    """
    logger.info(f"[PSWA_LLM_LOGIC] weave_script called with topic: '{topic}'")

    if not PSWA_IMPORTS_SUCCESSFUL:
        error_msg = f"OpenAI library not available. {PSWA_MISSING_IMPORT_ERROR}"
        logger.error(f"[PSWA_LLM_LOGIC] {error_msg}")
        return {"error": "PSWA_IMPORT_ERROR", "details": error_msg}

    api_key = pswa_config.get("OPENAI_API_KEY")
    if not api_key:
        error_msg = "Error: OPENAI_API_KEY is not configured."
        logger.error(f"[PSWA_LLM_LOGIC] {error_msg}")
        return {"error": "PSWA_CONFIG_ERROR_API_KEY", "details": error_msg}
    openai.api_key = api_key

    current_topic = topic if topic else "an interesting subject"
    current_content = content if content else "No specific content was provided. Please generate a general script based on the topic."
        
    user_prompt_template = pswa_config.get('PSWA_DEFAULT_PROMPT_USER_TEMPLATE')
    try:
        user_prompt = user_prompt_template.format(topic=current_topic, content=current_content)
    except KeyError as e:
        logger.error(f"[PSWA_LLM_LOGIC] Error formatting user prompt template. Missing key: {e}. Using basic prompt structure.")
        user_prompt = f"Topic: {current_topic}\nContent: {current_content}\n\nPlease generate a podcast script with [TITLE], [INTRO], [SEGMENT_1_TITLE], [SEGMENT_1_CONTENT], and [OUTRO]."

    system_message = pswa_config.get('PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE')
    llm_model = pswa_config.get('PSWA_LLM_MODEL')
    temperature = pswa_config.get('PSWA_LLM_TEMPERATURE')
    max_tokens = pswa_config.get('PSWA_LLM_MAX_TOKENS')

    logger.info(f"[PSWA_LLM_LOGIC] Sending request to OpenAI API. Model: {llm_model}, Temp: {temperature}, MaxTokens: {max_tokens}")
    raw_script_text = None
    llm_model_used = llm_model # Default to configured model

    try:
        response = openai.ChatCompletion.create(
            model=llm_model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens
        )
        raw_script_text = response.choices[0].message['content'].strip()
        llm_model_used = response.model # Get actual model used from response
        logger.info(f"[PSWA_LLM_LOGIC] Successfully received script from OpenAI API (model: {llm_model_used}). Length: {len(raw_script_text)}")

        # Check for LLM-indicated error before parsing
        if raw_script_text.startswith("[ERROR] Insufficient content"):
            logger.warning(f"[PSWA_LLM_LOGIC] LLM indicated insufficient content for topic '{current_topic}'.")
            # This will be handled by the endpoint to return a 400 type error to CPOA.
            # The structured parser will also reflect this.
            # Pass it to the parser to get a consistent structure.

        parsed_script = parse_llm_script_output(raw_script_text, current_topic)
        parsed_script["llm_model_used"] = llm_model_used # Ensure this is updated
        return parsed_script

    except openai.error.OpenAIError as e:
        error_msg = f"OpenAI API Error: {type(e).__name__} - {str(e)}"
        logger.error(f"[PSWA_LLM_LOGIC] {error_msg}")
        return {"error": "PSWA_OPENAI_API_ERROR", "details": error_msg, "raw_script_text_if_any": raw_script_text}
    except Exception as e:
        error_msg = f"An unexpected error occurred during LLM call: {type(e).__name__} - {str(e)}"
        logger.error(f"[PSWA_LLM_LOGIC] {error_msg}", exc_info=True)
        return {"error": "PSWA_UNEXPECTED_LLM_ERROR", "details": error_msg, "raw_script_text_if_any": raw_script_text}

# --- Flask Endpoint ---
@app.route('/weave_script', methods=['POST'])
def handle_weave_script():
    logger.info("[PSWA_FLASK_ENDPOINT] Received request for /weave_script")
    data = request.get_json()

    if not data:
        logger.error("[PSWA_FLASK_ENDPOINT] No JSON payload received.")
        return jsonify({"error": "No JSON payload received"}), 400

    content = data.get('content')
    topic = data.get('topic')

    if not content or not topic:
        missing_params = []
        if not content:
            missing_params.append('content')
        if not topic:
            missing_params.append('topic')
        logger.error(f"[PSWA_FLASK_ENDPOINT] Missing parameters: {', '.join(missing_params)}")
        return jsonify({"error": f"Missing required parameters: {', '.join(missing_params)}"}), 400

    logger.info(f"[PSWA_FLASK_ENDPOINT] Calling weave_script with topic: '{topic}'")
    result_data = weave_script(content, topic) # This now returns a dictionary

    if "error" in result_data:
        error_type = result_data.get("error")
        error_details = result_data.get("details")
        logger.error(f"[PSWA_FLASK_ENDPOINT] Error from weave_script: {error_type} - {error_details}")

        # Determine appropriate HTTP status code based on error type
        if error_type in ["PSWA_IMPORT_ERROR", "PSWA_CONFIG_ERROR_API_KEY", "PSWA_OPENAI_API_ERROR", "PSWA_UNEXPECTED_LLM_ERROR"]:
            return jsonify({"error": error_type, "message": error_details}), 500 # Internal server type errors
        # Add other specific error mappings if needed
        else: # Generic internal error
            return jsonify({"error": "PSWA_PROCESSING_ERROR", "message": error_details}), 500

    # Special handling for LLM-indicated insufficient content error
    # The parser puts the error message into the first segment's content.
    if result_data.get("segments") and result_data["segments"][0]["segment_title"] == "ERROR" and \
       result_data["segments"][0]["content"].startswith("[ERROR] Insufficient content"):
        logger.warning(f"[PSWA_FLASK_ENDPOINT] Insufficient content indicated by LLM for topic '{topic}'.")
        # Return the raw error message from LLM as 'error' field, with 400 status
        return jsonify({"error": result_data["segments"][0]["content"], "details": "LLM indicated content was insufficient."}), 400

    # Check if parsing itself failed to find critical elements, even if LLM call succeeded
    if not result_data.get("title") or not any(s["segment_title"] == "INTRO" for s in result_data.get("segments",[])):
         logger.error(f"[PSWA_FLASK_ENDPOINT] Failed to parse essential script structure (TITLE/INTRO) from LLM output for topic '{topic}'.")
         return jsonify({"error": "PSWA_SCRIPT_PARSING_FAILURE",
                         "message": "Failed to parse essential script structure from LLM output.",
                         "raw_output_preview": result_data.get("full_raw_script","")[:200] + "..."}), 500


    logger.info("[PSWA_FLASK_ENDPOINT] Successfully generated and structured script.")
    # The main 'script_text' key is still useful for CPOA if it expects the full raw script.
    # The structured version is now also available under 'structured_script'.
    # For now, let's return the structured script as the primary payload.
    # CPOA will need to be updated to expect this.
    # For now, to maintain compatibility with CPOA expecting "script_text", we send that.
    # The structured data can be added alongside.
    # Decision: Send the raw script text in "script_text" and the new structure in "structured_script_details"

    # Per requirements, the endpoint should return the structured script.
    # CPOA will be updated to handle this structured response.
    return jsonify(result_data)


if __name__ == "__main__":
    # The original CLI test logic can be kept for direct script testing if needed,
    # but the primary execution mode will now be the Flask app.

    # Start Flask app using configured values
    host = pswa_config.get("PSWA_HOST", "0.0.0.0")
    port = pswa_config.get("PSWA_PORT", 5004)
    debug_mode = pswa_config.get("PSWA_DEBUG", True)

    print(f"\n--- PSWA LLM Service starting on {host}:{port} (Debug: {debug_mode}) ---")
    # Check if API key is present before trying to run, as it's critical
    if not pswa_config.get("OPENAI_API_KEY"):
        print("CRITICAL ERROR: OPENAI_API_KEY is not set. The application will not function correctly.")
        print("Please set the OPENAI_API_KEY environment variable.")
        # Depending on desired behavior, could exit here:
        # import sys
        # sys.exit(1)

    app.run(host=host, port=port, debug=debug_mode)

    # Original CLI test (can be commented out or removed if Flask is the sole interface)
    # print("\n--- PSWA LLM Test (CLI - for direct script testing) ---")
    # sample_topic = "The Impact of AI on Daily Life"
    # sample_content = (
    #     "Artificial intelligence is increasingly prevalent. From voice assistants like Siri and Alexa "
    #     "to recommendation algorithms on Netflix and Spotify, AI shapes our interactions with technology. "
    #     "It's also making inroads in healthcare for diagnostics and in transportation with self-driving car development."
    # )
    # print(f"Attempting to weave script for topic: '{sample_topic}'")

    # # Check for import success
    # if not PSWA_IMPORTS_SUCCESSFUL:
    #      print(f"Cannot run weave_script: OpenAI library not available. {PSWA_MISSING_IMPORT_ERROR}")
    # else:
    #     # Check for API key to give user context if it will run
    #     if os.getenv("OPENAI_API_KEY"):
    #         print("OPENAI_API_KEY found, will attempt real API call.")
    #     else:
    #         print("OPENAI_API_KEY not found or empty. Expecting error message from weave_script.")
        
    #     generated_script = weave_script(sample_content, sample_topic)
    #     print("\nGenerated Script or Error Message:")
    #     print(generated_script)
    
    # # Test with empty content to see if LLM follows instruction
    # print("\n--- PSWA LLM Test (Empty Content) ---")
    # sample_topic_empty_content = "The Mysteries of the Deep Sea"
    # sample_content_empty = "" # Or very minimal like "Not much is known."
    
    # print(f"Attempting to weave script for topic: '{sample_topic_empty_content}' with empty content.")
    # if not PSWA_IMPORTS_SUCCESSFUL:
    #      print(f"Cannot run weave_script: OpenAI library not available. {PSWA_MISSING_IMPORT_ERROR}")
    # else:
    #     if os.getenv("OPENAI_API_KEY"):
    #         print("OPENAI_API_KEY found, will attempt real API call.")
    #     else:
    #         print("OPENAI_API_KEY not found or empty. Expecting error message from weave_script.")
    #     generated_script_empty = weave_script(sample_content_empty, sample_topic_empty_content)
    #     print("\nGenerated Script or Error Message (for empty content):")
    #     print(generated_script_empty)
        
    # print("\n--- End PSWA LLM Test (CLI) ---")
