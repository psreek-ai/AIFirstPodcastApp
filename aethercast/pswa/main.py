import logging
import os
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import uuid
import re
import sqlite3
import hashlib
from datetime import datetime, timedelta
import json
import requests # Added for AIMS service call
import time # Added for retry logic with AIMS

# --- Load Environment Variables ---
load_dotenv()

# --- PSWA Configuration ---
pswa_config = {}

def load_pswa_configuration():
    """Loads PSWA configurations from environment variables with defaults."""
    global pswa_config
    # OPENAI_API_KEY is removed, PSWA will call AIMS
    pswa_config['AIMS_SERVICE_URL'] = os.getenv("AIMS_SERVICE_URL", "http://aims_service:8000/v1/generate")
    pswa_config['AIMS_REQUEST_TIMEOUT_SECONDS'] = int(os.getenv("AIMS_REQUEST_TIMEOUT_SECONDS", "180"))

    pswa_config['PSWA_LLM_MODEL'] = os.getenv("PSWA_LLM_MODEL", "gpt-3.5-turbo") # This is now a request to AIMS
    pswa_config['PSWA_LLM_TEMPERATURE'] = float(os.getenv("PSWA_LLM_TEMPERATURE", "0.7"))
    pswa_config['PSWA_LLM_MAX_TOKENS'] = int(os.getenv("PSWA_LLM_MAX_TOKENS", "1500"))
    pswa_config['PSWA_LLM_JSON_MODE'] = os.getenv("PSWA_LLM_JSON_MODE", "true").lower() == 'true'

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
    pswa_config['PSWA_DEBUG_MODE'] = os.getenv("PSWA_DEBUG_MODE", "True").lower() == "true"

    pswa_config['SHARED_DATABASE_PATH'] = os.getenv("SHARED_DATABASE_PATH", "/app/database/aethercast_podcasts.db")
    pswa_config['PSWA_SCRIPT_CACHE_ENABLED'] = os.getenv("PSWA_SCRIPT_CACHE_ENABLED", "True").lower() == 'true'
    pswa_config['PSWA_SCRIPT_CACHE_MAX_AGE_HOURS'] = int(os.getenv("PSWA_SCRIPT_CACHE_MAX_AGE_HOURS", "720"))
    pswa_config['PSWA_TEST_MODE_ENABLED'] = os.getenv("PSWA_TEST_MODE_ENABLED", "False").lower() == 'true'

    logger.info("--- PSWA Configuration ---")
    logger.info(f"  AIMS_SERVICE_URL: {pswa_config['AIMS_SERVICE_URL']}")
    logger.info(f"  AIMS_REQUEST_TIMEOUT_SECONDS: {pswa_config['AIMS_REQUEST_TIMEOUT_SECONDS']}")
    logger.info(f"  PSWA_LLM_MODEL (request to AIMS): {pswa_config['PSWA_LLM_MODEL']}")
    logger.info(f"  PSWA_LLM_TEMPERATURE (request to AIMS): {pswa_config['PSWA_LLM_TEMPERATURE']}")
    logger.info(f"  PSWA_LLM_MAX_TOKENS (request to AIMS): {pswa_config['PSWA_LLM_MAX_TOKENS']}")
    logger.info(f"  PSWA_LLM_JSON_MODE (request to AIMS): {pswa_config['PSWA_LLM_JSON_MODE']}")
    # Log other configs as before, API_KEY related logging is removed
    for key, value in pswa_config.items():
        if key not in ["AIMS_SERVICE_URL", "AIMS_REQUEST_TIMEOUT_SECONDS", "PSWA_LLM_MODEL", "PSWA_LLM_TEMPERATURE", "PSWA_LLM_MAX_TOKENS", "PSWA_LLM_JSON_MODE"]:
            if key in ["PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE", "PSWA_DEFAULT_PROMPT_USER_TEMPLATE"]:
                logger.info(f"  {key}: Loaded (length: {len(value)}, first 50 chars: '{value[:50].replace('\n', ' ')}...')")
            else:
                logger.info(f"  {key}: {value}")
    logger.info("--- End PSWA Configuration ---")

    if not pswa_config.get('AIMS_SERVICE_URL'):
        error_msg = "CRITICAL: AIMS_SERVICE_URL is not set. PSWA cannot function."
        logger.error(error_msg)
        raise ValueError(error_msg)

# --- Database Schema for Cache ---
DB_SCHEMA_PSWA_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS generated_scripts (
    script_id TEXT PRIMARY KEY,
    topic_hash TEXT NOT NULL UNIQUE,
    structured_script_json TEXT NOT NULL,
    generation_timestamp TEXT NOT NULL,
    llm_model_used TEXT,
    last_accessed_timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_topic_hash ON generated_scripts (topic_hash);
"""

# --- Constants ---
KEY_TITLE = "title"
KEY_INTRO = "intro"
KEY_SEGMENTS = "segments"
KEY_SEGMENT_TITLE = "segment_title"
KEY_CONTENT = "content"
KEY_OUTRO = "outro"
KEY_ERROR = "error" # Used for LLM error structure within JSON
KEY_MESSAGE = "message"

SEGMENT_TITLE_INTRO = "INTRO"
SEGMENT_TITLE_OUTRO = "OUTRO"
SEGMENT_TITLE_ERROR = "ERROR"
TAG_TITLE = "TITLE"

# --- Test Mode Scenario Constants (remain largely the same) ---
SCENARIO_DEFAULT_SCRIPT_CONTENT = { KEY_TITLE: "Test Mode Default Title", KEY_INTRO: "This is the default intro for test mode.", KEY_SEGMENTS: [{KEY_SEGMENT_TITLE: "Test Segment 1", KEY_CONTENT: "Content of test segment 1."},{KEY_SEGMENT_TITLE: "Test Segment 2", KEY_CONTENT: "Content of test segment 2."}], KEY_OUTRO: "This is the default outro for test mode."}
SCENARIO_INSUFFICIENT_CONTENT_SCRIPT_CONTENT = { KEY_TITLE: "Error: Test Scenario Insufficient Content", KEY_SEGMENTS: [{KEY_SEGMENT_TITLE: SEGMENT_TITLE_ERROR, KEY_CONTENT: "[ERROR] Insufficient content for test topic."}],}
SCENARIO_EMPTY_SEGMENTS_SCRIPT_CONTENT = { KEY_TITLE: "Test Mode Title - Empty Segments", KEY_INTRO: "This intro leads to no actual content segments.", KEY_SEGMENTS: [], KEY_OUTRO: "This outro follows no actual content segments."}


# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
if app.logger and app.logger.name != 'root':
    logger = app.logger
else:
    logger = logging.getLogger(__name__)
    if not logger.hasHandlers():
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - PSWA - %(message)s')

if not pswa_config:
    load_pswa_configuration()

# --- Database Initialization ---
def init_pswa_db(db_path: str):
    """Initializes the PSWA script cache database table and index if they don't exist."""
    logger.info(f"[PSWA_DB_INIT] Ensuring PSWA database schema exists at {db_path}...")
    conn = None
    try:
        conn = _get_db_connection(db_path) # Uses existing helper that raises error on connection fail
        cursor = conn.cursor()
        cursor.executescript(DB_SCHEMA_PSWA_CACHE_TABLE)
        conn.commit()
        logger.info("[PSWA_DB_INIT] PSWA: Database table 'generated_scripts' and index 'idx_topic_hash' ensured.")
    except sqlite3.Error as e:
        logger.error(f"[PSWA_DB_INIT] PSWA: Database error during schema initialization: {e}", exc_info=True)
        # Depending on policy, might re-raise to halt startup if DB is critical even for non-cached operations.
        # For now, logging the error and allowing service to continue (caching might fail).
    except Exception as e_unexp:
        logger.error(f"[PSWA_DB_INIT] PSWA: Unexpected error during schema initialization: {e_unexp}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --- Database Helper Functions for Script Caching (remain the same) ---
def _get_db_connection(db_path: str):
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"[PSWA_CACHE_DB] Error connecting to database at {db_path}: {e}")
        raise

def _calculate_content_hash(topic: str, content: str) -> str:
    normalized_topic = topic.lower().strip()
    normalized_content_summary = content.lower().strip()[:1000]
    input_string = f"topic:{normalized_topic}|content_summary:{normalized_content_summary}"
    return hashlib.sha256(input_string.encode('utf-8')).hexdigest()

def _get_cached_script(db_path: str, topic_hash: str, max_age_hours: int) -> Optional[dict]:
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
            try:
                cursor.execute("UPDATE generated_scripts SET last_accessed_timestamp = ? WHERE script_id = ?",
                               (datetime.utcnow().isoformat(), row['script_id']))
                conn.commit()
            except sqlite3.Error as e_update:
                logger.warning(f"[PSWA_CACHE_DB] Failed to update last_accessed_timestamp for script {row['script_id']}: {e_update}")
            if 'source' not in structured_script:
                 structured_script['source'] = "cache"
            structured_script['script_id'] = row['script_id']
            structured_script['llm_model_used'] = row['llm_model_used']
            structured_script['generation_timestamp_from_cache'] = row['generation_timestamp']
            return structured_script
        else:
            logger.info(f"[PSWA_CACHE_DB] Cache miss or stale for hash {topic_hash} (max_age_hours: {max_age_hours})")
            return None
    except (sqlite3.Error, json.JSONDecodeError) as e:
        logger.error(f"[PSWA_CACHE_DB] Error accessing/decoding cache for hash {topic_hash}: {e}", exc_info=True)
        return None
    finally:
        if conn:
            conn.close()

def _save_script_to_cache(db_path: str, script_id: str, topic_hash: str, structured_script: dict, llm_model_used: str):
    if not pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        return
    logger.info(f"[PSWA_CACHE_DB] Saving script {script_id} to cache with hash: {topic_hash}")
    conn = None
    try:
        script_to_save = structured_script.copy()
        if 'source' in script_to_save : del script_to_save['source']
        if 'generation_timestamp_from_cache' in script_to_save: del script_to_save['generation_timestamp_from_cache']
        structured_script_json = json.dumps(script_to_save)
        generation_timestamp = datetime.utcnow().isoformat()
        conn = _get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO generated_scripts (script_id, topic_hash, structured_script_json, generation_timestamp, llm_model_used, last_accessed_timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (script_id, topic_hash, structured_script_json, generation_timestamp, llm_model_used, generation_timestamp)
        )
        conn.commit()
        logger.info(f"[PSWA_CACHE_DB] Successfully saved script {script_id} to cache.")
    except (sqlite3.Error, json.JSONEncodeError) as e:
        logger.error(f"[PSWA_CACHE_DB] Error saving script {script_id} to cache: {e}", exc_info=True)
    except Exception as e_unexp:
        logger.error(f"[PSWA_CACHE_DB] Unexpected error saving script {script_id} to cache: {e_unexp}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --- LLM Output Parsing (parse_llm_script_output - remains largely the same) ---
def parse_llm_script_output(raw_script_text: str, topic: str) -> dict:
    script_id = f"pswa_script_{uuid.uuid4().hex}"
    parsed_script = {
        "script_id": script_id, "topic": topic, "title": f"Podcast on {topic}",
        "full_raw_script": raw_script_text, "segments": [],
        "llm_model_used": pswa_config.get('PSWA_LLM_MODEL', "gpt-3.5-turbo") # Default, will be updated by actual model from AIMS
    }
    try:
        llm_json_data = json.loads(raw_script_text)
        logger.info(f"[PSWA_PARSING] Successfully parsed LLM output as JSON for topic '{topic}'.")
        if KEY_ERROR in llm_json_data and llm_json_data[KEY_ERROR] == "Insufficient content":
            logger.warning(f"[PSWA_PARSING] LLM returned 'Insufficient content' error in JSON for topic '{topic}'.")
            parsed_script[KEY_TITLE] = llm_json_data.get(KEY_MESSAGE, f"Error: Insufficient Content for {topic}")
            parsed_script[KEY_SEGMENTS] = [{KEY_SEGMENT_TITLE: SEGMENT_TITLE_ERROR, KEY_CONTENT: llm_json_data.get(KEY_MESSAGE, raw_script_text)}]
            return parsed_script
        parsed_script[KEY_TITLE] = llm_json_data.get(KEY_TITLE, f"Podcast on {topic}")
        intro_content = llm_json_data.get(KEY_INTRO)
        if intro_content is not None: parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: SEGMENT_TITLE_INTRO, KEY_CONTENT: str(intro_content)})
        else: logger.warning(f"[PSWA_PARSING] JSON from LLM missing '{KEY_INTRO}' for topic '{topic}'.")
        llm_segments = llm_json_data.get(KEY_SEGMENTS, [])
        if isinstance(llm_segments, list):
            for seg in llm_segments:
                if isinstance(seg, dict) and KEY_SEGMENT_TITLE in seg and KEY_CONTENT in seg:
                    parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: str(seg[KEY_SEGMENT_TITLE]), KEY_CONTENT: str(seg[KEY_CONTENT])})
                else: logger.warning(f"[PSWA_PARSING] Invalid segment structure in JSON from LLM for topic '{topic}': {seg}")
        else: logger.warning(f"[PSWA_PARSING] JSON from LLM '{KEY_SEGMENTS}' is not a list for topic '{topic}'.")
        outro_content = llm_json_data.get(KEY_OUTRO)
        if outro_content is not None: parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: SEGMENT_TITLE_OUTRO, KEY_CONTENT: str(outro_content)})
        else: logger.warning(f"[PSWA_PARSING] JSON from LLM missing '{KEY_OUTRO}' for topic '{topic}'.")
        if not parsed_script[KEY_SEGMENTS]: logger.warning(f"[PSWA_PARSING] No valid segments found in JSON for topic '{topic}'.")
        return parsed_script
    except json.JSONDecodeError:
        logger.warning(f"[PSWA_PARSING] LLM output was not valid JSON for topic '{topic}'. Raw output: '{raw_script_text[:200]}...' Attempting fallback.")
        parsed_script[KEY_TITLE] = f"Podcast on {topic}"; parsed_script[KEY_SEGMENTS] = [] # Reset for fallback
    if raw_script_text.startswith("[ERROR] Insufficient content"):
        logger.warning(f"[PSWA_PARSING_FALLBACK] LLM indicated insufficient content for topic '{topic}'.")
        parsed_script[KEY_TITLE] = f"Error: Insufficient Content for {topic}"
        parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: SEGMENT_TITLE_ERROR, KEY_CONTENT: raw_script_text})
        return parsed_script
    title_match = re.search(r"\[TITLE\](.*?)\n", raw_script_text, re.IGNORECASE)
    if title_match: parsed_script[KEY_TITLE] = title_match.group(1).strip()
    lines = raw_script_text.splitlines(); current_tag_content = []; active_tag = None
    for line in lines:
        line = line.strip(); match = re.fullmatch(r"\[([A-Z0-9_]+)\]", line, re.IGNORECASE)
        if match:
            if active_tag and current_tag_content:
                if active_tag.upper() == TAG_TITLE and parsed_script[KEY_TITLE] == f"Podcast on {topic}": parsed_script[KEY_TITLE] = "\n".join(current_tag_content).strip()
                else: parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: active_tag, KEY_CONTENT: "\n".join(current_tag_content).strip()})
            active_tag = match.group(1).upper(); current_tag_content = []
            if active_tag == TAG_TITLE and parsed_script[KEY_TITLE] != f"Podcast on {topic}": active_tag = None
        elif active_tag: current_tag_content.append(line)
    if active_tag and current_tag_content:
        if active_tag.upper() == TAG_TITLE and parsed_script[KEY_TITLE] == f"Podcast on {topic}": parsed_script[KEY_TITLE] = "\n".join(current_tag_content).strip()
        else: parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: active_tag, KEY_CONTENT: "\n".join(current_tag_content).strip()})
    processed_segments = []; i = 0; temp_segments_for_processing = parsed_script[KEY_SEGMENTS]; parsed_script[KEY_SEGMENTS] = []
    while i < len(temp_segments_for_processing):
        segment = temp_segments_for_processing[i]; title_tag = segment[KEY_SEGMENT_TITLE]; text_content = segment[KEY_CONTENT]
        if title_tag.endswith("_TITLE") and (i + 1 < len(temp_segments_for_processing)):
            next_segment = temp_segments_for_processing[i+1]
            if next_segment[KEY_SEGMENT_TITLE] == title_tag.replace("_TITLE", "_CONTENT"):
                processed_segments.append({KEY_SEGMENT_TITLE: text_content, KEY_CONTENT: next_segment[KEY_CONTENT]}); i += 1
            else: processed_segments.append({KEY_SEGMENT_TITLE: title_tag, KEY_CONTENT: text_content})
        elif title_tag in [SEGMENT_TITLE_INTRO, SEGMENT_TITLE_OUTRO]: processed_segments.append({KEY_SEGMENT_TITLE: title_tag, KEY_CONTENT: text_content})
        elif not title_tag.endswith("_CONTENT"): processed_segments.append({KEY_SEGMENT_TITLE: title_tag, KEY_CONTENT: text_content})
        i += 1
    parsed_script[KEY_SEGMENTS] = processed_segments
    if (not parsed_script[KEY_TITLE] or parsed_script[KEY_TITLE] == f"Podcast on {topic}") and not any(s[KEY_SEGMENT_TITLE] == SEGMENT_TITLE_INTRO for s in parsed_script[KEY_SEGMENTS]):
        logger.warning(f"[PSWA_PARSING_FALLBACK] Critical tags missing after fallback for topic '{topic}'. Output: '{raw_script_text[:200]}...'")
    return parsed_script

# --- Main Script Weaving Logic ---
def weave_script(content: str, topic: str) -> dict:
    logger.info(f"[PSWA_MAIN_LOGIC] weave_script called for topic: '{topic}'")
    script_id_base = f"pswa_script_{uuid.uuid4().hex[:8]}"

    if pswa_config.get('PSWA_TEST_MODE_ENABLED'):
        # Test mode logic remains the same as it bypasses LLM calls
        scenario = request.headers.get('X-Test-Scenario', 'default')
        logger.info(f"[PSWA_MAIN_LOGIC] Test mode enabled. Scenario: '{scenario}' for topic '{topic}'.")
        script_content_to_return = SCENARIO_DEFAULT_SCRIPT_CONTENT.copy()
        source_info = f"test_mode_scenario_{scenario}"
        if scenario == 'insufficient_content':
            script_content_to_return = SCENARIO_INSUFFICIENT_CONTENT_SCRIPT_CONTENT.copy()
            script_content_to_return[KEY_TITLE] = f"Error: Test Scenario Insufficient Content for topic: {topic}"
            script_content_to_return[KEY_SEGMENTS] = [script_content_to_return[KEY_SEGMENTS][0].copy()]
            script_content_to_return[KEY_SEGMENTS][0][KEY_CONTENT] = f"[ERROR] Insufficient content for test topic: {topic}"
        elif scenario == 'empty_segments':
            script_content_to_return = SCENARIO_EMPTY_SEGMENTS_SCRIPT_CONTENT.copy()
        else: # Default
            script_content_to_return[KEY_TITLE] = f"Test Mode: {topic}"
            if script_content_to_return.get(KEY_INTRO):
                 script_content_to_return[KEY_INTRO] = f"This is the intro for the test mode topic: {topic}."

        final_test_script = {"script_id": f"{script_id_base}_test_{scenario}", "topic": topic, "llm_model_used": "test-mode-model", "source": source_info}
        final_test_script.update(script_content_to_return)
        if KEY_SEGMENTS not in final_test_script: final_test_script[KEY_SEGMENTS] = []
        if scenario == 'insufficient_content':
            raw_llm_error_sim = {"error": "Insufficient content", "message": f"The provided content was not sufficient... topic: {topic}"}
            final_test_script["full_raw_script"] = json.dumps(raw_llm_error_sim)
        else:
            final_test_script["full_raw_script"] = json.dumps(script_content_to_return)
        return final_test_script

    topic_hash = _calculate_content_hash(topic, content)
    logger.info(f"[PSWA_MAIN_LOGIC] Topic hash for caching: {topic_hash}")
    if pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        cached_script = _get_cached_script(pswa_config['SHARED_DATABASE_PATH'], topic_hash, pswa_config['PSWA_SCRIPT_CACHE_MAX_AGE_HOURS'])
        if cached_script:
            logger.info(f"[PSWA_MAIN_LOGIC] Returning cached script for topic '{topic}', hash {topic_hash}")
            if 'source' not in cached_script: cached_script['source'] = "cache"
            return cached_script
    logger.info(f"[PSWA_MAIN_LOGIC] No suitable cache entry or caching disabled for topic '{topic}'. Calling AIMS service.")

    current_topic = topic if topic else "an interesting subject"
    current_content = content if content else "No specific content was provided. Please generate a general script based on the topic."
    user_prompt_template = pswa_config.get('PSWA_DEFAULT_PROMPT_USER_TEMPLATE')
    try:
        user_prompt = user_prompt_template.format(topic=current_topic, content=current_content)
    except KeyError as e:
        logger.error(f"[PSWA_MAIN_LOGIC] Error formatting user prompt template. Missing key: {e}. Using basic prompt.")
        user_prompt = f"Topic: {current_topic}\nContent: {current_content}\nPlease generate a podcast script."

    system_message = pswa_config.get('PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE')
    full_prompt_for_aims = f"{system_message}\n\nUser Request:\n{user_prompt}" # Combine for AIMS

    aims_payload = {
        "prompt": full_prompt_for_aims,
        "model_id_override": pswa_config.get('PSWA_LLM_MODEL'),
        "max_tokens": pswa_config.get('PSWA_LLM_MAX_TOKENS'),
        "temperature": pswa_config.get('PSWA_LLM_TEMPERATURE'),
    }
    if pswa_config.get('PSWA_LLM_JSON_MODE'):
        aims_payload["response_format"] = {"type": "json_object"}

    aims_url = pswa_config.get('AIMS_SERVICE_URL')
    aims_timeout = pswa_config.get('AIMS_REQUEST_TIMEOUT_SECONDS')

    logger.info(f"[PSWA_MAIN_LOGIC] Sending request to AIMS Service. URL: {aims_url}, Payload: {json.dumps(aims_payload)}")
    raw_script_text_from_aims = None
    llm_model_reported_by_aims = pswa_config.get('PSWA_LLM_MODEL') # Default, will be updated

    try:
        response = requests.post(aims_url, json=aims_payload, timeout=aims_timeout)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        aims_response_data = response.json()

        if not aims_response_data.get("choices") or not aims_response_data["choices"][0].get("text"):
            error_msg = "AIMS service response missing expected 'choices[0].text' field."
            logger.error(f"[PSWA_MAIN_LOGIC] {error_msg} Response: {aims_response_data}")
            return {"error_code": "PSWA_AIMS_BAD_RESPONSE", "message": error_msg, "details": aims_response_data, "source": "error"}

        raw_script_text_from_aims = aims_response_data["choices"][0]["text"].strip()
        llm_model_reported_by_aims = aims_response_data.get("model_id", llm_model_reported_by_aims)
        # Log usage if AIMS provides it, though PSWA doesn't directly use it now
        if "usage" in aims_response_data:
            logger.info(f"[PSWA_MAIN_LOGIC] AIMS reported usage: {aims_response_data['usage']}")

        logger.info(f"[PSWA_MAIN_LOGIC] Successfully received script from AIMS (model: {llm_model_reported_by_aims}). Length: {len(raw_script_text_from_aims)}")

        parsed_script = parse_llm_script_output(raw_script_text_from_aims, current_topic)
        parsed_script["llm_model_used"] = llm_model_reported_by_aims # Store model reported by AIMS
        parsed_script["source"] = "generation_via_aims"

        if pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED') and not (parsed_script.get("segments") and parsed_script["segments"][0]["segment_title"] == "ERROR"):
            _save_script_to_cache(pswa_config['SHARED_DATABASE_PATH'], parsed_script["script_id"], topic_hash, parsed_script, llm_model_reported_by_aims)
        return parsed_script

    except requests.exceptions.Timeout as e_timeout:
        error_msg = f"AIMS service request timed out after {aims_timeout}s: {str(e_timeout)}"
        logger.error(f"[PSWA_MAIN_LOGIC] {error_msg}")
        return {"error_code": "PSWA_AIMS_TIMEOUT", "message": "Request to AIMS service timed out.", "details": error_msg, "source": "error"}
    except requests.exceptions.HTTPError as e_http:
        error_msg = f"AIMS service returned HTTP error {e_http.response.status_code}: {e_http.response.text}"
        logger.error(f"[PSWA_MAIN_LOGIC] {error_msg}")
        aims_error_details = e_http.response.text
        try: aims_error_details = e_http.response.json() # If AIMS returns JSON error
        except ValueError: pass
        return {"error_code": "PSWA_AIMS_HTTP_ERROR", "message": f"AIMS service returned HTTP {e_http.response.status_code}.", "details": aims_error_details, "source": "error"}
    except requests.exceptions.RequestException as e_req:
        error_msg = f"Error calling AIMS service: {type(e_req).__name__} - {str(e_req)}"
        logger.error(f"[PSWA_MAIN_LOGIC] {error_msg}")
        return {"error_code": "PSWA_AIMS_REQUEST_ERROR", "message": "Failed to communicate with AIMS service.", "details": error_msg, "source": "error"}
    except json.JSONDecodeError as e_json: # If AIMS response is not JSON
        error_msg = f"Could not decode JSON response from AIMS: {str(e_json)}. Response text: {response.text[:200] if 'response' in locals() else 'N/A'}"
        logger.error(f"[PSWA_MAIN_LOGIC] {error_msg}")
        return {"error_code": "PSWA_AIMS_BAD_RESPONSE_JSON", "message": "AIMS service response was not valid JSON.", "details": error_msg, "source": "error"}
    except Exception as e:
        error_msg = f"An unexpected error occurred while interacting with AIMS: {type(e).__name__} - {str(e)}"
        logger.error(f"[PSWA_MAIN_LOGIC] {error_msg}", exc_info=True)
        return {"error_code": "PSWA_AIMS_UNEXPECTED_ERROR", "message": "An unexpected error occurred while processing via AIMS.", "details": error_msg, "source": "error"}


# --- Flask Endpoint (remains largely the same, but error handling might adapt based on weave_script's new error_codes) ---
@app.route('/weave_script', methods=['POST'])
def handle_weave_script():
    logger.info("[PSWA_FLASK_ENDPOINT] Received request for /weave_script")
    data = request.get_json()
    try:
        data = request.get_json()
        if not data:
            logger.error("[PSWA_FLASK_ENDPOINT] Invalid or empty JSON payload received.")
            return jsonify({"error_code": "PSWA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload.", "details": "Request body must be a valid non-empty JSON object."}), 400
    except Exception as e_json_decode:
        logger.error(f"[PSWA_FLASK_ENDPOINT] Failed to decode JSON payload: {e_json_decode}", exc_info=True)
        return jsonify({"error_code": "PSWA_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json_decode)}), 400

    content = data.get(KEY_CONTENT)
    topic = data.get(KEY_TOPIC)

    # Validate content
    if not content or not isinstance(content, str) or not content.strip():
        logger.warning(f"[PSWA_FLASK_ENDPOINT] Validation failed: '{KEY_CONTENT}' must be a non-empty string. Received: '{content}'")
        return jsonify({"error_code": "PSWA_INVALID_CONTENT", "message": f"Validation failed: '{KEY_CONTENT}' must be a non-empty string."}), 400

    # Optional: Consider a min/max length for content if it makes sense for script generation quality
    CONTENT_MIN_LENGTH = 50 # Example minimum
    CONTENT_MAX_LENGTH = 50000 # Example maximum (very generous)
    if len(content) < CONTENT_MIN_LENGTH:
        logger.warning(f"[PSWA_FLASK_ENDPOINT] Validation warning: '{KEY_CONTENT}' length ({len(content)}) is less than recommended minimum ({CONTENT_MIN_LENGTH}).")
        # Not returning error, but logging. Could return 400 if strict.
    if len(content) > CONTENT_MAX_LENGTH:
        logger.warning(f"[PSWA_FLASK_ENDPOINT] Validation failed: '{KEY_CONTENT}' length ({len(content)}) exceeds maximum ({CONTENT_MAX_LENGTH}).")
        return jsonify({"error_code": "PSWA_CONTENT_TOO_LONG", "message": f"Validation failed: '{KEY_CONTENT}' exceeds maximum length of {CONTENT_MAX_LENGTH} characters."}), 400

    # Validate topic
    if not topic or not isinstance(topic, str) or not topic.strip():
        logger.warning(f"[PSWA_FLASK_ENDPOINT] Validation failed: '{KEY_TOPIC}' must be a non-empty string. Received: '{topic}'")
        return jsonify({"error_code": "PSWA_INVALID_TOPIC", "message": f"Validation failed: '{KEY_TOPIC}' must be a non-empty string."}), 400

    logger.info(f"[PSWA_FLASK_ENDPOINT] Calling weave_script with topic: '{topic}' (Content length: {len(content)})")
    result_data = weave_script(content, topic)

    if "error_code" in result_data:
        error_code = result_data.get("error_code")
        error_message = result_data.get("message", f"Error processing script for {topic}.")
        error_details = result_data.get("details", "No additional details provided.")
        logger.error(f"[PSWA_FLASK_ENDPOINT] Error from weave_script: {error_code} - {error_message} - {error_details}")
        http_status_code = 500 # Default for internal/AIMS errors
        if error_code == "PSWA_AIMS_TIMEOUT": http_status_code = 504 # Gateway Timeout
        elif error_code == "PSWA_AIMS_HTTP_ERROR": # Could refine based on AIMS's actual status if available in details
            if isinstance(error_details, dict) and error_details.get("status_code"): # Hypothetical if AIMS error includes original status
                 pass # http_status_code = error_details.get("status_code")
            else: # Generic for now
                 http_status_code = 502 # Bad Gateway
        elif error_code == "PSWA_AIMS_BAD_RESPONSE" or error_code == "PSWA_AIMS_BAD_RESPONSE_JSON":
             http_status_code = 502 # Bad Gateway
        return jsonify({"error_code": error_code, "message": error_message, "details": error_details}), http_status_code

    if result_data.get(KEY_SEGMENTS) and result_data[KEY_SEGMENTS][0][KEY_SEGMENT_TITLE] == SEGMENT_TITLE_ERROR:
        error_detail_from_script = result_data[KEY_SEGMENTS][0][KEY_CONTENT]
        logger.warning(f"[PSWA_FLASK_ENDPOINT] Insufficient content indicated by LLM (via AIMS) for topic '{topic}'. Details: {error_detail_from_script}")
        return jsonify({"error_code": "PSWA_INSUFFICIENT_CONTENT", "message": "Content provided was insufficient for script generation (reported by LLM).", "details": error_detail_from_script }), 400

    if not result_data.get(KEY_TITLE) or not any(s[KEY_SEGMENT_TITLE] == SEGMENT_TITLE_INTRO for s in result_data.get(KEY_SEGMENTS,[])):
         logger.error(f"[PSWA_FLASK_ENDPOINT] Failed to parse essential script structure from AIMS output for topic '{topic}'.")
         return jsonify({ "error_code": "PSWA_SCRIPT_PARSING_FAILURE", "message": "Failed to parse essential script structure from AIMS output.", "details": "The AIMS output did not conform to the expected script structure.", "raw_output_preview": result_data.get("full_raw_script","")[:200] + "..."}), 500

    logger.info("[PSWA_FLASK_ENDPOINT] Successfully generated and structured script via AIMS.")
    return jsonify(result_data)

if __name__ == "__main__":
    host = pswa_config.get("PSWA_HOST", "0.0.0.0")
    port = pswa_config.get("PSWA_PORT", 5004)
    debug_mode = pswa_config.get("PSWA_DEBUG_MODE", True)
    print(f"\n--- PSWA Service (AIMS Client) starting on {host}:{port} (Debug: {debug_mode}) ---")
    if not pswa_config.get("AIMS_SERVICE_URL"):
        # This case should ideally be prevented by load_pswa_configuration raising an error
        print("CRITICAL ERROR: AIMS_SERVICE_URL is not set. PSWA will not function.")

    # Initialize the database for PSWA script caching
    if pswa_config.get('SHARED_DATABASE_PATH'):
        init_pswa_db(pswa_config['SHARED_DATABASE_PATH'])
    else:
        print("WARNING: SHARED_DATABASE_PATH not configured for PSWA. Script caching will be disabled or use a default path if any.")

    app.run(host=host, port=port, debug=debug_mode)
