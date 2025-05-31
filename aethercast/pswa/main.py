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
    pswa_config['PSWA_LLM_JSON_MODE'] = os.getenv("PSWA_LLM_JSON_MODE", "true").lower() == 'true' # Added

    default_system_message_json = """You are a podcast scriptwriter. Your output MUST be a single, valid JSON object.
Do not include any text outside of this JSON object, not even markdown tags like ```json.
The JSON object should conform to the following schema:
{
  "title": "string (The main title of the podcast)",
  "intro": "string (The introductory part of the podcast script, 2-3 sentences)",
  "segments": [
    {
      "segment_title": "string (Title of this segment, e.g., 'Segment 1: The Core Idea')",
      "content": "string (Content of this segment, several sentences or paragraphs)"
    }
  ],
  "outro": "string (The concluding part of the podcast script, 2-3 sentences)"
}
Ensure all script content is engaging and based on the provided topic and source content.
There should be at least an intro, one segment, and an outro.
If the provided source content is insufficient to generate a meaningful script with at least one segment,
return a JSON object with an error field:
{
  "error": "Insufficient content",
  "message": "The provided content was not sufficient to generate a full podcast script for the topic: [topic_name_here]."
}"""
    pswa_config['PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE'] = os.getenv("PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE", default_system_message_json)

    default_user_template_json = """Generate a podcast script for topic '{topic}' using the following content:
---
{content}
---
Remember, your entire response must be a single JSON object conforming to the schema provided in the system message."""
    pswa_config['PSWA_DEFAULT_PROMPT_USER_TEMPLATE'] = os.getenv("PSWA_DEFAULT_PROMPT_USER_TEMPLATE", default_user_template_json)

    pswa_config['PSWA_HOST'] = os.getenv("PSWA_HOST", "0.0.0.0")
    pswa_config['PSWA_PORT'] = int(os.getenv("PSWA_PORT", 5004))
    pswa_config['PSWA_DEBUG'] = os.getenv("PSWA_DEBUG", "True").lower() == "true"

    # Script Caching Configuration
    pswa_config['PSWA_DATABASE_PATH'] = os.getenv("PSWA_DATABASE_PATH", "../api_gateway/aethercast_podcasts.db")
    pswa_config['PSWA_SCRIPT_CACHE_ENABLED'] = os.getenv("PSWA_SCRIPT_CACHE_ENABLED", "True").lower() == 'true'
    pswa_config['PSWA_SCRIPT_CACHE_MAX_AGE_HOURS'] = int(os.getenv("PSWA_SCRIPT_CACHE_MAX_AGE_HOURS", "720")) # 30 days
    pswa_config['PSWA_TEST_MODE_ENABLED'] = os.getenv("PSWA_TEST_MODE_ENABLED", "False").lower() == 'true' # Added Test Mode

    logger.info("--- PSWA Configuration ---")
    for key, value in pswa_config.items():
        if "API_KEY" in key and value:
            logger.info(f"  {key}: {'*' * (len(value) - 4) + value[-4:] if len(value) > 4 else '****'}")
        elif key in ["PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE", "PSWA_DEFAULT_PROMPT_USER_TEMPLATE"]:
            logger.info(f"  {key}: Loaded (length: {len(value)}, first 50 chars: '{value[:50].replace('\n', ' ')}...')")
        else:
            logger.info(f"  {key}: {value}")
    logger.info(f"  PSWA_LLM_JSON_MODE: {pswa_config['PSWA_LLM_JSON_MODE']}") # Added logging for new mode
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
        load_pswa_configuration()

if not pswa_config: # Ensure configuration is loaded at startup
    load_pswa_configuration()

import uuid
import re
import sqlite3
import hashlib
from datetime import datetime, timedelta # Added timedelta for cache age
import json # Already imported if used by logger, but good to ensure for DB operations

# --- Database Helper Functions for Script Caching ---
def _get_db_connection(db_path: str):
    """Establishes a SQLite database connection."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"[PSWA_CACHE_DB] Error connecting to database at {db_path}: {e}")
        raise # Re-raise to be handled by caller, or return None if preferred to not halt all operations

def _calculate_content_hash(topic: str, content: str) -> str:
    """Calculates a SHA256 hash for the given topic and content."""
    # Normalize: lowercase and strip whitespace
    normalized_topic = topic.lower().strip()
    # Use a summary of content to avoid hashing very large strings, e.g., first 1000 chars
    normalized_content_summary = content.lower().strip()[:1000]

    input_string = f"topic:{normalized_topic}|content_summary:{normalized_content_summary}"
    return hashlib.sha256(input_string.encode('utf-8')).hexdigest()

def _get_cached_script(db_path: str, topic_hash: str, max_age_hours: int) -> Optional[dict]:
    """Retrieves a script from cache if it exists and is not stale."""
    if not pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        return None

    logger.info(f"[PSWA_CACHE_DB] Attempting to fetch script from cache for hash: {topic_hash}")
    conn = None
    try:
        conn = _get_db_connection(db_path)
        cursor = conn.cursor()

        cutoff_timestamp = (datetime.utcnow() - timedelta(hours=max_age_hours)).isoformat()

        cursor.execute(
            "SELECT script_id, structured_script_json, llm_model_used, generation_timestamp FROM generated_scripts WHERE topic_hash = ? AND generation_timestamp >= ?",
            (topic_hash, cutoff_timestamp)
        )
        row = cursor.fetchone()

        if row:
            logger.info(f"[PSWA_CACHE_DB] Cache hit for hash {topic_hash}. Script ID: {row['script_id']}")
            structured_script = json.loads(row['structured_script_json'])

            # Update last_accessed_timestamp (best effort)
            try:
                cursor.execute("UPDATE generated_scripts SET last_accessed_timestamp = ? WHERE script_id = ?",
                               (datetime.utcnow().isoformat(), row['script_id']))
                conn.commit()
            except sqlite3.Error as e_update:
                logger.warning(f"[PSWA_CACHE_DB] Failed to update last_accessed_timestamp for script {row['script_id']}: {e_update}")

            # Add source field for clarity if it's not already part of the stored script
            if 'source' not in structured_script:
                 structured_script['source'] = "cache"
            structured_script['script_id'] = row['script_id'] # Ensure script_id from DB is used
            structured_script['llm_model_used'] = row['llm_model_used'] # Ensure model from DB is used
            structured_script['generation_timestamp_from_cache'] = row['generation_timestamp'] # For context
            return structured_script
        else:
            logger.info(f"[PSWA_CACHE_DB] Cache miss or stale for hash {topic_hash} (max_age_hours: {max_age_hours})")
            return None
    except sqlite3.Error as e:
        logger.error(f"[PSWA_CACHE_DB] Database error getting cached script for hash {topic_hash}: {e}")
        return None # On DB error, proceed as if cache miss
    except json.JSONDecodeError as e_json:
        logger.error(f"[PSWA_CACHE_DB] Error decoding cached script JSON for hash {topic_hash}: {e_json}. Treating as cache miss.")
        # Optionally, consider deleting the malformed cache entry here.
        return None
    finally:
        if conn:
            conn.close()

def _save_script_to_cache(db_path: str, script_id: str, topic_hash: str, structured_script: dict, llm_model_used: str):
    """Saves a generated script to the cache."""
    if not pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        return

    logger.info(f"[PSWA_CACHE_DB] Saving script {script_id} to cache with hash: {topic_hash}")
    conn = None
    try:
        # Ensure the script itself doesn't have the temporary 'source' field before saving
        script_to_save = structured_script.copy()
        if 'source' in script_to_save : del script_to_save['source']
        if 'generation_timestamp_from_cache' in script_to_save: del script_to_save['generation_timestamp_from_cache']

        structured_script_json = json.dumps(script_to_save)
        generation_timestamp = datetime.utcnow().isoformat()

        conn = _get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO generated_scripts
            (script_id, topic_hash, structured_script_json, generation_timestamp, llm_model_used, last_accessed_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (script_id, topic_hash, structured_script_json, generation_timestamp, llm_model_used, generation_timestamp)
        )
        conn.commit()
        logger.info(f"[PSWA_CACHE_DB] Successfully saved script {script_id} to cache.")
    except sqlite3.Error as e:
        logger.error(f"[PSWA_CACHE_DB] Database error saving script {script_id} to cache: {e}")
    except json.JSONEncodeError as e_json:
        logger.error(f"[PSWA_CACHE_DB] Error encoding script {script_id} to JSON for cache: {e_json}")
    except Exception as e_unexp: # Catch any other unexpected error
        logger.error(f"[PSWA_CACHE_DB] Unexpected error saving script {script_id} to cache: {e_unexp}", exc_info=True)
    finally:
        if conn:
            conn.close()


def parse_llm_script_output(raw_script_text: str, topic: str) -> dict:
    """
    Parses the raw text output from the LLM into a structured script dictionary.
    """
    script_id = f"pswa_script_{uuid.uuid4().hex}"

    # Default values
    parsed_script = {
        "script_id": script_id,
        "topic": topic,
        "title": f"Podcast on {topic}",
        "full_raw_script": raw_script_text, # Store the original LLM output
        "segments": [],
        "llm_model_used": pswa_config.get('PSWA_LLM_MODEL', "gpt-3.5-turbo")
    }

    # Attempt to parse as JSON first
    try:
        llm_json_data = json.loads(raw_script_text)
        logger.info(f"[PSWA_PARSING] Successfully parsed LLM output as JSON for topic '{topic}'.")

        # Check for LLM-reported error within the JSON
        if "error" in llm_json_data and llm_json_data["error"] == "Insufficient content":
            logger.warning(f"[PSWA_PARSING] LLM returned 'Insufficient content' error in JSON for topic '{topic}'.")
            parsed_script["title"] = llm_json_data.get("message", f"Error: Insufficient Content for {topic}")
            parsed_script["segments"] = [{"segment_title": "ERROR", "content": llm_json_data.get("message", raw_script_text)}]
            return parsed_script

        # Validate and map JSON fields to our structure
        parsed_script["title"] = llm_json_data.get("title", f"Podcast on {topic}")

        # Intro
        intro_content = llm_json_data.get("intro")
        if intro_content is not None: # Allow empty string for intro
             parsed_script["segments"].append({"segment_title": "INTRO", "content": str(intro_content)})
        else:
            logger.warning(f"[PSWA_PARSING] JSON from LLM missing 'intro' for topic '{topic}'.")


        # Segments
        llm_segments = llm_json_data.get("segments", [])
        if isinstance(llm_segments, list):
            for seg in llm_segments:
                if isinstance(seg, dict) and "segment_title" in seg and "content" in seg:
                    parsed_script["segments"].append({
                        "segment_title": str(seg["segment_title"]),
                        "content": str(seg["content"])
                    })
                else:
                    logger.warning(f"[PSWA_PARSING] Invalid segment structure in JSON from LLM for topic '{topic}': {seg}")
        else:
            logger.warning(f"[PSWA_PARSING] JSON from LLM 'segments' is not a list for topic '{topic}'.")

        # Outro
        outro_content = llm_json_data.get("outro")
        if outro_content is not None: # Allow empty string for outro
            parsed_script["segments"].append({"segment_title": "OUTRO", "content": str(outro_content)})
        else:
            logger.warning(f"[PSWA_PARSING] JSON from LLM missing 'outro' for topic '{topic}'.")

        if not parsed_script["segments"]: # If after all this, segments are empty
             logger.warning(f"[PSWA_PARSING] No valid segments (intro, content segments, outro) found in JSON for topic '{topic}'.")
             # This might be an issue, could lead to an empty podcast.

        return parsed_script

    except json.JSONDecodeError:
        logger.warning(f"[PSWA_PARSING] LLM output was not valid JSON for topic '{topic}'. Raw output preview: '{raw_script_text[:200]}...' Attempting fallback tag-based parsing.")
        # Fallback to tag-based parsing (existing logic)
        # The existing tag-based parser is already here, so we just let the code flow into it.
        # Reset title to default as it might have been partially set by failed JSON attempt.
        parsed_script["title"] = f"Podcast on {topic}" # Reset for tag parser
        parsed_script["segments"] = [] # Reset for tag parser

    # Fallback Tag-based parsing (adapted from previous version)
    if raw_script_text.startswith("[ERROR] Insufficient content"): # Check again for fallback
        logger.warning(f"[PSWA_PARSING_FALLBACK] LLM indicated insufficient content for topic '{topic}'.")
        parsed_script["title"] = f"Error: Insufficient Content for {topic}"
        parsed_script["segments"].append({"segment_title": "ERROR", "content": raw_script_text})
        return parsed_script

    title_match = re.search(r"\[TITLE\](.*?)\n", raw_script_text, re.IGNORECASE)
    if title_match:
        parsed_script["title"] = title_match.group(1).strip()

    lines = raw_script_text.splitlines()
    current_tag_content = []
    active_tag = None

    for line in lines:
        line = line.strip()
        match = re.fullmatch(r"\[([A-Z0-9_]+)\]", line, re.IGNORECASE)
        if match:
            if active_tag and current_tag_content:
                # Special handling for title if it wasn't caught by initial regex and is default
                if active_tag.upper() == "TITLE" and parsed_script["title"] == f"Podcast on {topic}":
                    parsed_script["title"] = "\n".join(current_tag_content).strip()
                else:
                    parsed_script["segments"].append({
                        "segment_title": active_tag,
                        "content": "\n".join(current_tag_content).strip()
                    })
            active_tag = match.group(1).upper()
            current_tag_content = []
            # Avoid re-processing TITLE if already extracted by initial regex search
            if active_tag == "TITLE" and parsed_script["title"] != f"Podcast on {topic}":
                active_tag = None # Effectively ignore this [TITLE] content as main title is already set
        elif active_tag:
            current_tag_content.append(line)

    if active_tag and current_tag_content: # Add the last segment
        if active_tag.upper() == "TITLE" and parsed_script["title"] == f"Podcast on {topic}":
             parsed_script["title"] = "\n".join(current_tag_content).strip()
        else:
            parsed_script["segments"].append({"segment_title": active_tag, "content": "\n".join(current_tag_content).strip()})

    processed_segments = []
    i = 0
    temp_segments_for_processing = parsed_script["segments"] # Use the segments populated by tag parser
    parsed_script["segments"] = [] # Clear it to repopulate with processed ones

    while i < len(temp_segments_for_processing):
        segment = temp_segments_for_processing[i]
        title_tag = segment["segment_title"]
        text_content = segment["content"]

        if title_tag.endswith("_TITLE") and (i + 1 < len(temp_segments_for_processing)):
            next_segment = temp_segments_for_processing[i+1]
            if next_segment["segment_title"] == title_tag.replace("_TITLE", "_CONTENT"):
                processed_segments.append({
                    "segment_title": text_content,
                    "content": next_segment["content"]
                })
                i += 1
            else:
                processed_segments.append({"segment_title": title_tag, "content": text_content})
        elif title_tag in ["INTRO", "OUTRO"]:
             processed_segments.append({"segment_title": title_tag, "content": text_content})
        elif not title_tag.endswith("_CONTENT"):
            processed_segments.append({"segment_title": title_tag, "content": text_content})
        i += 1
    parsed_script["segments"] = processed_segments

    if (not parsed_script["title"] or parsed_script["title"] == f"Podcast on {topic}") and \
       not any(s["segment_title"] == "INTRO" for s in parsed_script["segments"]):
        logger.warning(f"[PSWA_PARSING_FALLBACK] Critical tags missing after fallback for topic '{topic}'. Output: '{raw_script_text[:200]}...'")

    return parsed_script


def weave_script(content: str, topic: str) -> dict: # Return type changed to dict
    """
    Generates a podcast script using the configured LLM and parses it into a structured dict.
    Implements caching logic to avoid redundant LLM calls.
    Returns a dictionary, which will include an 'error' key if something went wrong,
    or the structured script data on success (with a 'source' field indicating 'cache' or 'generation').
    """
    logger.info(f"[PSWA_MAIN_LOGIC] weave_script called for topic: '{topic}'")

    if pswa_config.get('PSWA_TEST_MODE_ENABLED'):
        logger.info(f"[PSWA_MAIN_LOGIC] Test mode enabled. Returning predefined script for topic '{topic}'.")
        script_id = f"pswa_script_testmode_{uuid.uuid4().hex[:6]}"
        dummy_segments = [
            {"segment_title": "INTRO", "content": f"This is a test mode intro for {topic}."},
            {"segment_title": "Segment 1: Test Details", "content": "Content of segment 1 for test mode."},
            {"segment_title": "OUTRO", "content": "This concludes the test mode script."}
        ]
        dummy_script = {
            "script_id": script_id,
            "topic": topic,
            "title": f"Test Mode: {topic}",
            "segments": dummy_segments,
            "full_raw_script": json.dumps({ # Simulating what a JSON response from LLM might look like
                "title": f"Test Mode: {topic}",
                "intro": dummy_segments[0]["content"],
                "segments": [{"segment_title": s["segment_title"], "content": s["content"]} for s in dummy_segments[1:-1]], # Exclude intro/outro for this part
                "outro": dummy_segments[-1]["content"]
            }),
            "llm_model_used": "test-mode-model",
            "source": "test_mode"
        }
        return dummy_script

    # Calculate hash for caching
    topic_hash = _calculate_content_hash(topic, content)
    logger.info(f"[PSWA_MAIN_LOGIC] Topic hash for caching: {topic_hash}")

    # Cache lookup if enabled
    if pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        cached_script = _get_cached_script(
            pswa_config['PSWA_DATABASE_PATH'],
            topic_hash,
            pswa_config['PSWA_SCRIPT_CACHE_MAX_AGE_HOURS']
        )
        if cached_script:
            logger.info(f"[PSWA_MAIN_LOGIC] Returning cached script for topic '{topic}', hash {topic_hash}")
            # Ensure 'source' field is set, default to 'cache' if not present from DB (older entries)
            if 'source' not in cached_script:
                cached_script['source'] = "cache"
            return cached_script

    # Proceed with LLM generation if no cache hit or caching disabled
    logger.info(f"[PSWA_MAIN_LOGIC] No suitable cache entry found or caching disabled for topic '{topic}'. Proceeding with LLM generation.")

    if not PSWA_IMPORTS_SUCCESSFUL:
        error_msg = f"OpenAI library not available. Import error: {PSWA_MISSING_IMPORT_ERROR}"
        logger.error(f"[PSWA_MAIN_LOGIC] {error_msg}")
        return {"error": "PSWA_IMPORT_ERROR", "details": error_msg, "source": "error"}

    api_key = pswa_config.get("OPENAI_API_KEY")
    if not api_key:
        error_msg = "Error: OPENAI_API_KEY is not configured."
        logger.error(f"[PSWA_MAIN_LOGIC] {error_msg}")
        return {"error": "PSWA_CONFIG_ERROR_API_KEY", "details": error_msg, "source": "error"}
    openai.api_key = api_key

    current_topic = topic if topic else "an interesting subject"
    current_content = content if content else "No specific content was provided. Please generate a general script based on the topic."
        
    user_prompt_template = pswa_config.get('PSWA_DEFAULT_PROMPT_USER_TEMPLATE')
    try:
        user_prompt = user_prompt_template.format(topic=current_topic, content=current_content)
    except KeyError as e:
        logger.error(f"[PSWA_MAIN_LOGIC] Error formatting user prompt template. Missing key: {e}. Using basic prompt structure.")
        user_prompt = f"Topic: {current_topic}\nContent: {current_content}\n\nPlease generate a podcast script with [TITLE], [INTRO], [SEGMENT_1_TITLE], [SEGMENT_1_CONTENT], and [OUTRO]."

    system_message = pswa_config.get('PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE')
    llm_model = pswa_config.get('PSWA_LLM_MODEL')
    temperature = pswa_config.get('PSWA_LLM_TEMPERATURE')
    max_tokens = pswa_config.get('PSWA_LLM_MAX_TOKENS')
    use_json_mode = pswa_config.get('PSWA_LLM_JSON_MODE', False) # Get from config

    openai_call_params = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    if use_json_mode:
        # Basic check for models known to support JSON mode.
        # More sophisticated checks or library version checks might be needed for robustness.
        if "1106" in llm_model or "gpt-4" in llm_model or "turbo" in llm_model or "gpt-3.5-turbo-0125" in llm_model:
            openai_call_params["response_format"] = {"type": "json_object"}
            logger.info(f"[PSWA_MAIN_LOGIC] Attempting to use JSON mode with OpenAI model {llm_model}.")
        else:
            logger.warning(f"[PSWA_MAIN_LOGIC] PSWA_LLM_JSON_MODE is true, but model {llm_model} might not explicitly support it via API flag. Will rely on prompt for JSON structure.")

    logger.info(f"[PSWA_MAIN_LOGIC] Sending request to OpenAI API. Params: {json.dumps(openai_call_params)}")
    raw_script_text = None
    llm_model_used = llm_model

    try:
        response = openai.ChatCompletion.create(**openai_call_params)
        raw_script_text = response.choices[0].message['content'].strip()
        llm_model_used = response.model
        logger.info(f"[PSWA_MAIN_LOGIC] Successfully received script from OpenAI API (model: {llm_model_used}). Length: {len(raw_script_text)}")

        parsed_script = parse_llm_script_output(raw_script_text, current_topic) # This populates script_id
        parsed_script["llm_model_used"] = llm_model_used
        parsed_script["source"] = "generation"

        # Save to cache if enabled and parsing was successful (not an error structure)
        if pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED') and not (parsed_script.get("segments") and parsed_script["segments"][0]["segment_title"] == "ERROR"):
            _save_script_to_cache(
                pswa_config['PSWA_DATABASE_PATH'],
                parsed_script["script_id"], # Use the ID generated by parse_llm_script_output
                topic_hash,
                parsed_script, # Pass the full structured script
                llm_model_used
            )
        return parsed_script

    except openai.error.OpenAIError as e:
        error_msg = f"OpenAI API Error: {type(e).__name__} - {str(e)}"
        logger.error(f"[PSWA_MAIN_LOGIC] {error_msg}")
        return {"error": "PSWA_OPENAI_API_ERROR", "details": error_msg, "raw_script_text_if_any": raw_script_text, "source": "error"}
    except Exception as e:
        error_msg = f"An unexpected error occurred during LLM call: {type(e).__name__} - {str(e)}"
        logger.error(f"[PSWA_MAIN_LOGIC] {error_msg}", exc_info=True)
        return {"error": "PSWA_UNEXPECTED_LLM_ERROR", "details": error_msg, "raw_script_text_if_any": raw_script_text, "source": "error"}

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
