import logging
import os
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import uuid
import re
# import sqlite3 # Removed
import hashlib
from datetime import datetime, timedelta
import json
import requests
import time
import psycopg2 # Added
from psycopg2.extras import RealDictCursor # Added
from typing import Optional, Dict, Any # Added for type hinting
from python_json_logger import jsonlogger # Added for JSON logging

# --- Load Environment Variables ---
load_dotenv()

# --- Logging Setup ---
# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="pswa"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

# Initialize Flask app early so app.logger can be configured
app = Flask(__name__)

# Configure JSON logging for the Flask app
def setup_json_logging(flask_app):
    flask_app.logger.handlers.clear() # Clear existing default Flask handlers
    logHandler = logging.StreamHandler()
    service_filter = ServiceNameFilter("pswa")
    logHandler.addFilter(service_filter)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s",
        rename_fields={"levelname": "level", "name": "logger_name", "asctime": "timestamp"}
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("JSON logging configured for PSWA service.")

setup_json_logging(app)

# Make the global logger use the configured app.logger
logger = app.logger

# --- PSWA Configuration ---
pswa_config = {}

def load_pswa_configuration():
    """Loads PSWA configurations from environment variables with defaults."""
    global pswa_config
    pswa_config['AIMS_SERVICE_URL'] = os.getenv("AIMS_SERVICE_URL", "http://aims_service:8000/v1/generate")
    pswa_config['AIMS_REQUEST_TIMEOUT_SECONDS'] = int(os.getenv("AIMS_REQUEST_TIMEOUT_SECONDS", "180"))

    pswa_config['PSWA_LLM_MODEL'] = os.getenv("PSWA_LLM_MODEL", "gpt-3.5-turbo")
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
    # Old system message removed
    # pswa_config['PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE'] = os.getenv("PSWA_DEFAULT_PROMPT_SYSTEM_MESSAGE", default_system_message_json)

    # New prompt configurations
    pswa_config['PSWA_DEFAULT_PROMPT_USER_TEMPLATE'] = os.getenv("PSWA_DEFAULT_PROMPT_USER_TEMPLATE",
        """Generate a podcast script for topic '{topic}' using the following content:
---
{content}
---
{narrative_guidance}
Remember, your entire response must be a single JSON object conforming to the schema provided in the system message.""")

    pswa_config['PSWA_DEFAULT_PERSONA'] = os.getenv("PSWA_DEFAULT_PERSONA", "InformativeHost")

    persona_prompts_json_str = os.getenv("PSWA_PERSONA_PROMPTS_JSON", '{}')
    try:
        pswa_config['PSWA_PERSONA_PROMPTS_MAP_PARSED'] = json.loads(persona_prompts_json_str)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse PSWA_PERSONA_PROMPTS_JSON: {e}. Using empty map. JSON string was: {persona_prompts_json_str}")
        pswa_config['PSWA_PERSONA_PROMPTS_MAP_PARSED'] = {}

    pswa_config['PSWA_BASE_SYSTEM_MESSAGE_JSON_SCHEMA_INSTRUCTION'] = os.getenv("PSWA_BASE_SYSTEM_MESSAGE_JSON_SCHEMA_INSTRUCTION",
        """Your output MUST be a single, valid JSON object. Do not include any text outside of this JSON object, not even markdown tags like ```json. The JSON object should conform to the following schema: {"title": "string (The main title of the podcast)", "intro": "string (The introductory part of the podcast script, 2-3 sentences)", "segments": [{"segment_title": "string (Title of this segment, e.g., 'Segment 1: The Core Idea')", "content": "string (Content of this segment, several sentences or paragraphs)"}], "outro": "string (The concluding part of the podcast script, 2-3 sentences)"}. Ensure all script content is engaging and based on the provided topic and source content. There should be at least an intro, one segment, and an outro. If the provided source content is insufficient to generate a meaningful script with at least one segment, return a JSON object with an error field: {"error": "Insufficient content", "message": "The provided content was not sufficient to generate a full podcast script for the topic: [topic_name_here]."}""")

    pswa_config['PSWA_NARRATIVE_GUIDANCE_USER_PROMPT_ADDITION'] = os.getenv("PSWA_NARRATIVE_GUIDANCE_USER_PROMPT_ADDITION",
        "When writing the segments, ensure you start with a compelling hook in the intro, develop key points logically with smooth transitions, and end with a satisfying conclusion in the outro. Vary sentence structure for engagement. Ensure the overall script is coherent and engaging for the listener.")

    pswa_config['PSWA_HOST'] = os.getenv("PSWA_HOST", "0.0.0.0")
    pswa_config['PSWA_PORT'] = int(os.getenv("PSWA_PORT", 5004))
    pswa_config['PSWA_DEBUG_MODE'] = os.getenv("PSWA_DEBUG_MODE", "True").lower() == "true"

    # Database Configuration
    pswa_config["DATABASE_TYPE"] = os.getenv("DATABASE_TYPE", "sqlite") # Default to sqlite
    pswa_config['SHARED_DATABASE_PATH'] = os.getenv("SHARED_DATABASE_PATH", "/app/database/aethercast_podcasts.db") # SQLite path
    pswa_config["POSTGRES_HOST"] = os.getenv("POSTGRES_HOST")
    pswa_config["POSTGRES_PORT"] = os.getenv("POSTGRES_PORT", "5432")
    pswa_config["POSTGRES_USER"] = os.getenv("POSTGRES_USER")
    pswa_config["POSTGRES_PASSWORD"] = os.getenv("POSTGRES_PASSWORD")
    pswa_config["POSTGRES_DB"] = os.getenv("POSTGRES_DB")

    pswa_config['PSWA_SCRIPT_CACHE_ENABLED'] = os.getenv("PSWA_SCRIPT_CACHE_ENABLED", "True").lower() == 'true'
    pswa_config['PSWA_SCRIPT_CACHE_MAX_AGE_HOURS'] = int(os.getenv("PSWA_SCRIPT_CACHE_MAX_AGE_HOURS", "720"))
    pswa_config['PSWA_TEST_MODE_ENABLED'] = os.getenv("PSWA_TEST_MODE_ENABLED", "False").lower() == 'true'

    logger.info("--- PSWA Configuration ---")
    for key, value in pswa_config.items():
        if "PASSWORD" in key and value:
            logger.info(f"  {key}: ********")
        elif key == "PSWA_PERSONA_PROMPTS_MAP_PARSED":
            logger.info(f"  {key}: Loaded {len(value)} personas: {list(value.keys())}")
        elif key in ["PSWA_DEFAULT_PROMPT_USER_TEMPLATE", "PSWA_BASE_SYSTEM_MESSAGE_JSON_SCHEMA_INSTRUCTION", "PSWA_NARRATIVE_GUIDANCE_USER_PROMPT_ADDITION"]:
            log_value_snippet = str(value)[:50].replace('\n', ' ')
            logger.info(f"  {key}: Loaded (length: {len(value)}, first 50 chars: '{log_value_snippet}...')")
        else:
            logger.info(f"  {key}: {value}")
    logger.info("--- End PSWA Configuration ---")

    if not pswa_config.get('AIMS_SERVICE_URL'):
        error_msg = "CRITICAL: AIMS_SERVICE_URL is not set. PSWA cannot function."
        logger.critical(error_msg)
        raise ValueError(error_msg)

    if pswa_config["DATABASE_TYPE"] == "postgres":
        required_pg_vars = ["POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]
        missing_pg_vars = [var for var in required_pg_vars if not pswa_config.get(var)]
        if missing_pg_vars:
            error_msg = f"CRITICAL: DATABASE_TYPE is 'postgres' but required PostgreSQL config is missing: {', '.join(missing_pg_vars)}"
            logger.critical(error_msg)
            raise ValueError(error_msg)
    elif pswa_config["DATABASE_TYPE"] == "sqlite" and pswa_config['PSWA_SCRIPT_CACHE_ENABLED']:
        if not pswa_config.get("SHARED_DATABASE_PATH"):
            error_msg = "CRITICAL: DATABASE_TYPE is 'sqlite' and cache is enabled, but SHARED_DATABASE_PATH is not set."
            logger.critical(error_msg)
            raise ValueError(error_msg)


# --- Database Schema for Cache (PostgreSQL compatible) ---
DB_SCHEMA_PSWA_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS generated_scripts (
    script_id UUID PRIMARY KEY,
    topic_hash VARCHAR(64) NOT NULL UNIQUE,
    structured_script_json JSONB NOT NULL,
    generation_timestamp TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    llm_model_used VARCHAR(255),
    last_accessed_timestamp TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_topic_hash ON generated_scripts (topic_hash);
"""

# --- Constants ---
KEY_TITLE = "title"; KEY_INTRO = "intro"; KEY_SEGMENTS = "segments"; KEY_SEGMENT_TITLE = "segment_title"
KEY_CONTENT = "content"; KEY_OUTRO = "outro"; KEY_ERROR = "error"; KEY_MESSAGE = "message"
SEGMENT_TITLE_INTRO = "INTRO"; SEGMENT_TITLE_OUTRO = "OUTRO"; SEGMENT_TITLE_ERROR = "ERROR"; TAG_TITLE = "TITLE"

SCENARIO_DEFAULT_SCRIPT_CONTENT = { KEY_TITLE: "Test Mode Default Title", KEY_INTRO: "This is the default intro for test mode.", KEY_SEGMENTS: [{KEY_SEGMENT_TITLE: "Test Segment 1", KEY_CONTENT: "Content of test segment 1."},{KEY_SEGMENT_TITLE: "Test Segment 2", KEY_CONTENT: "Content of test segment 2."}], KEY_OUTRO: "This is the default outro for test mode."}
SCENARIO_INSUFFICIENT_CONTENT_SCRIPT_CONTENT = { KEY_TITLE: "Error: Test Scenario Insufficient Content", KEY_SEGMENTS: [{KEY_SEGMENT_TITLE: SEGMENT_TITLE_ERROR, KEY_CONTENT: "[ERROR] Insufficient content for test topic."}],}
SCENARIO_EMPTY_SEGMENTS_SCRIPT_CONTENT = { KEY_TITLE: "Test Mode Title - Empty Segments", KEY_INTRO: "This intro leads to no actual content segments.", KEY_SEGMENTS: [], KEY_OUTRO: "This outro follows no actual content segments."}

# Flask app is initialized earlier for logging setup.
# Global logger is now an alias to app.logger.
# The basicConfig call is removed as setup_json_logging handles root/app logger.
if not pswa_config: load_pswa_configuration() # load_pswa_configuration will now use the aliased app.logger

# --- Database Helper Functions for Script Caching ---
def _get_db_connection():
    db_type = pswa_config.get("DATABASE_TYPE")
    if db_type == "postgres":
        try:
            conn = psycopg2.connect(
                host=pswa_config["POSTGRES_HOST"], port=pswa_config["POSTGRES_PORT"],
                user=pswa_config["POSTGRES_USER"], password=pswa_config["POSTGRES_PASSWORD"],
                dbname=pswa_config["POSTGRES_DB"], cursor_factory=RealDictCursor
            )
            return conn
        except psycopg2.Error as e:
            logger.error(f"[PSWA_CACHE_DB] Error connecting to PostgreSQL: {e}")
            raise
    elif db_type == "sqlite":
        db_path = pswa_config['SHARED_DATABASE_PATH']
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    else:
        raise ValueError(f"Unsupported DATABASE_TYPE: {db_type}")

def init_pswa_db():
    if not pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        logger.info("[PSWA_DB_INIT] Script caching is disabled. Skipping DB initialization.")
        return
    db_type = pswa_config.get("DATABASE_TYPE")
    logger.info(f"[PSWA_DB_INIT] Ensuring PSWA cache schema exists (DB Type: {db_type})...")
    conn = None; cursor = None
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        if db_type == "postgres":
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'generated_scripts'
                );
            """)
            table_exists = cursor.fetchone()['exists']
            if not table_exists:
                cursor.execute(DB_SCHEMA_PSWA_CACHE_TABLE)
                conn.commit()
                logger.info("[PSWA_DB_INIT] PostgreSQL: Table 'generated_scripts' and index created.")
            else:
                logger.info("[PSWA_DB_INIT] PostgreSQL: Table 'generated_scripts' already exists.")
        elif db_type == "sqlite":
            cursor.executescript(DB_SCHEMA_PSWA_CACHE_TABLE) # executescript for SQLite
            conn.commit()
            logger.info("[PSWA_DB_INIT] SQLite: Table 'generated_scripts' and index ensured.")
    except (psycopg2.Error, sqlite3.Error) as e:
        logger.error(f"[PSWA_DB_INIT] Database error: {e}", exc_info=True)
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

def _calculate_content_hash(topic: str, content: str) -> str:
    # (Function remains the same)
    normalized_topic = topic.lower().strip()
    normalized_content_summary = content.lower().strip()[:1000]
    input_string = f"topic:{normalized_topic}|content_summary:{normalized_content_summary}"
    return hashlib.sha256(input_string.encode('utf-8')).hexdigest()

def _get_cached_script(topic_hash: str, max_age_hours: int) -> Optional[Dict[str, Any]]:
    if not pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'): return None
    logger.info(f"[PSWA_CACHE_DB] Fetching script from cache for hash: {topic_hash}")
    conn = None; cursor = None
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cutoff_timestamp = (datetime.utcnow() - timedelta(hours=max_age_hours))

        sql_query = """
            SELECT script_id, structured_script_json, llm_model_used, generation_timestamp
            FROM generated_scripts
            WHERE topic_hash = %s AND generation_timestamp >= %s;
        """
        params = (topic_hash, cutoff_timestamp)

        if pswa_config.get("DATABASE_TYPE") == "sqlite":
            sql_query = sql_query.replace("%s", "?")
            params = (topic_hash, cutoff_timestamp.isoformat())

        cursor.execute(sql_query, params)
        row = cursor.fetchone()

        if row:
            logger.info(f"[PSWA_CACHE_DB] Cache hit for hash {topic_hash}. Script ID: {row['script_id']}")
            # structured_script_json is already a dict due to RealDictCursor or json.loads for sqlite
            structured_script = row['structured_script_json'] if isinstance(row['structured_script_json'], dict) else json.loads(row['structured_script_json'])

            update_access_sql = "UPDATE generated_scripts SET last_accessed_timestamp = %s WHERE script_id = %s;"
            update_params = (datetime.utcnow(), row['script_id'])
            if pswa_config.get("DATABASE_TYPE") == "sqlite":
                update_access_sql = update_access_sql.replace("%s", "?")
                update_params = (datetime.utcnow().isoformat(), row['script_id'])

            # Secondary cursor for update to not interfere with fetchone if needed, though not strictly necessary here
            update_cursor = conn.cursor()
            update_cursor.execute(update_access_sql, update_params)
            conn.commit()
            update_cursor.close()

            structured_script['source'] = "cache"
            # script_id and llm_model_used are already part of structured_script_json if saved correctly.
            # If not, ensure they are added:
            if 'script_id' not in structured_script: structured_script['script_id'] = row['script_id']
            if 'llm_model_used' not in structured_script: structured_script['llm_model_used'] = row['llm_model_used']
            structured_script['generation_timestamp_from_cache'] = row['generation_timestamp'].isoformat() if isinstance(row['generation_timestamp'], datetime) else str(row['generation_timestamp'])
            return structured_script
        else:
            logger.info(f"[PSWA_CACHE_DB] Cache miss or stale for hash {topic_hash}")
            return None
    except (psycopg2.Error, sqlite3.Error, json.JSONDecodeError) as e:
        logger.error(f"[PSWA_CACHE_DB] Error accessing/decoding cache for {topic_hash}: {e}", exc_info=True)
        if conn and pswa_config.get("DATABASE_TYPE") == "postgres": conn.rollback()
        return None
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

def _save_script_to_cache(script_id: str, topic_hash: str, structured_script: Dict[str, Any], llm_model_used: str):
    if not pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'): return
    logger.info(f"[PSWA_CACHE_DB] Saving script {script_id} to cache with hash: {topic_hash}")
    conn = None; cursor = None
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()

        script_to_save_db = structured_script.copy()
        # Remove fields not part of the core stored JSON if they were added for runtime
        script_to_save_db.pop('source', None)
        script_to_save_db.pop('generation_timestamp_from_cache', None)

        # For PostgreSQL JSONB, pass dict directly. For SQLite TEXT, dump to string.
        script_json_for_db = script_to_save_db if pswa_config.get("DATABASE_TYPE") == "postgres" else json.dumps(script_to_save_db)

        current_ts = datetime.utcnow()

        sql_insert = """
            INSERT INTO generated_scripts
                (script_id, topic_hash, structured_script_json, generation_timestamp, llm_model_used, last_accessed_timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (topic_hash) DO UPDATE SET
                script_id = EXCLUDED.script_id,
                structured_script_json = EXCLUDED.structured_script_json,
                generation_timestamp = EXCLUDED.generation_timestamp,
                llm_model_used = EXCLUDED.llm_model_used,
                last_accessed_timestamp = EXCLUDED.last_accessed_timestamp;
        """
        # Note: SQLite's ON CONFLICT syntax is different for specific column updates.
        # PostgreSQL's ON CONFLICT (topic_hash) DO UPDATE is more robust for this.
        # For SQLite, it would typically be INSERT OR REPLACE, or separate INSERT and UPDATE.
        # Given the schema has topic_hash UNIQUE, INSERT OR REPLACE is simpler for SQLite.
        params = (script_id, topic_hash, script_json_for_db, current_ts, llm_model_used, current_ts)

        if pswa_config.get("DATABASE_TYPE") == "sqlite":
            sql_insert = """
                INSERT OR REPLACE INTO generated_scripts
                    (script_id, topic_hash, structured_script_json, generation_timestamp, llm_model_used, last_accessed_timestamp)
                VALUES (?, ?, ?, ?, ?, ?);
            """ # Using ? placeholders and ensuring JSON is string
            params = (script_id, topic_hash, json.dumps(script_json_for_db) if isinstance(script_json_for_db, dict) else script_json_for_db, current_ts.isoformat(), llm_model_used, current_ts.isoformat())

        cursor.execute(sql_insert, params)
        conn.commit()
        logger.info(f"[PSWA_CACHE_DB] Successfully saved script {script_id} to cache.")
    except (psycopg2.Error, sqlite3.Error, json.JSONEncodeError) as e:
        logger.error(f"[PSWA_CACHE_DB] Error saving script {script_id} to cache: {e}", exc_info=True)
        if conn and pswa_config.get("DATABASE_TYPE") == "postgres": conn.rollback()
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# --- LLM Output Parsing (parse_llm_script_output - remains the same) ---
def parse_llm_script_output(raw_script_text: str, topic: str) -> dict:
    # (Function content remains the same)
    script_id = f"pswa_script_{uuid.uuid4().hex}"
    parsed_script = {
        "script_id": script_id, "topic": topic, "title": f"Podcast on {topic}",
        "full_raw_script": raw_script_text, "segments": [],
        "llm_model_used": pswa_config.get('PSWA_LLM_MODEL', "gpt-3.5-turbo")
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
        parsed_script[KEY_TITLE] = f"Podcast on {topic}"; parsed_script[KEY_SEGMENTS] = []
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
    script_id_base = f"pswa_script_{str(uuid.uuid4())}"
    status_for_metric = "unknown_error" # Default status for metrics
    aims_call_latency_ms = None # Initialize

    if pswa_config.get('PSWA_TEST_MODE_ENABLED'):
        scenario = request.headers.get('X-Test-Scenario', 'default')
        logger.info(f"[PSWA_MAIN_LOGIC] Test mode enabled. Scenario: '{scenario}' for topic '{topic}'.")
        status_for_metric = f"test_mode_scenario_{scenario}"

        script_content_to_return = SCENARIO_DEFAULT_SCRIPT_CONTENT.copy()
        source_info = f"test_mode_scenario_{scenario}"
        if scenario == 'insufficient_content':
            script_content_to_return = SCENARIO_INSUFFICIENT_CONTENT_SCRIPT_CONTENT.copy()
            script_content_to_return[KEY_TITLE] = f"Error: Test Scenario Insufficient Content for topic: {topic}"
            script_content_to_return[KEY_SEGMENTS] = [script_content_to_return[KEY_SEGMENTS][0].copy()]
            script_content_to_return[KEY_SEGMENTS][0][KEY_CONTENT] = f"[ERROR] Insufficient content for test topic: {topic}"
        elif scenario == 'empty_segments': script_content_to_return = SCENARIO_EMPTY_SEGMENTS_SCRIPT_CONTENT.copy()
        else: script_content_to_return[KEY_TITLE] = f"Test Mode: {topic}"; script_content_to_return.get(KEY_INTRO, f"This is the intro for test topic: {topic}.")

        final_test_script = {"script_id": f"{script_id_base}_test_{scenario}", "topic": topic, "llm_model_used": "test-mode-model", "source": source_info}
        final_test_script.update(script_content_to_return)
        if KEY_SEGMENTS not in final_test_script: final_test_script[KEY_SEGMENTS] = []
        raw_script_output = {"error": "Insufficient content", "message": f"The provided content was not sufficient... topic: {topic}"} if scenario == 'insufficient_content' else script_content_to_return
        final_test_script["full_raw_script"] = json.dumps(raw_script_output)
        return {"script_data": final_test_script, "status_for_metric": status_for_metric}

    topic_hash = _calculate_content_hash(topic, content)
    if pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        cached_script = _get_cached_script(topic_hash, pswa_config['PSWA_SCRIPT_CACHE_MAX_AGE_HOURS'])
        if cached_script:
            logger.info(f"[PSWA_MAIN_LOGIC] Returning cached script for topic '{topic}', hash {topic_hash}")
            logger.info("PSWA cache hit", extra=dict(metric_name="pswa_cache_hit_count", value=1, tags={"topic_hash": topic_hash}))
            status_for_metric = "success_cache_hit"
            # Ensure the 'source' key is present as expected by downstream if it's from cache
            if 'source' not in cached_script: cached_script['source'] = "cache"
            return {"script_data": cached_script, "status_for_metric": status_for_metric}
        else:
            logger.info("PSWA cache miss", extra=dict(metric_name="pswa_cache_miss_count", value=1, tags={"topic_hash": topic_hash}))

    logger.info(f"[PSWA_MAIN_LOGIC] No cache for topic '{topic}'. Calling AIMS service.")
    current_topic = topic or "an interesting subject"
    current_content = content or "No specific content provided. Generate general script based on topic."

    # Determine Persona and Construct Prompts
    default_persona_id = pswa_config.get('PSWA_DEFAULT_PERSONA', 'InformativeHost')
    # Allow persona_id to be passed in the request in the future, for now, use default from config
    # For this implementation, we'll assume persona_id comes from config or defaults to InformativeHost.
    # If CPOA were to send it, it would be like: persona_id_from_request = data.get("persona_id", default_persona_id)
    current_persona_id = default_persona_id # Placeholder for potential future request-driven persona

    persona_prompts_map = pswa_config.get('PSWA_PERSONA_PROMPTS_MAP_PARSED', {})

    persona_specific_system_message = persona_prompts_map.get(current_persona_id)
    if not persona_specific_system_message:
        logger.warning(f"Persona ID '{current_persona_id}' not found in PSWA_PERSONA_PROMPTS_MAP_PARSED. Falling back to 'InformativeHost'.")
        persona_specific_system_message = persona_prompts_map.get('InformativeHost', "You are a helpful AI podcast scriptwriter.") # Final fallback

    base_schema_instruction = pswa_config.get('PSWA_BASE_SYSTEM_MESSAGE_JSON_SCHEMA_INSTRUCTION', '')
    final_system_message = f"{persona_specific_system_message.strip()} {base_schema_instruction.strip()}".strip()

    narrative_guidance = pswa_config.get('PSWA_NARRATIVE_GUIDANCE_USER_PROMPT_ADDITION', '')
    user_prompt_template = pswa_config.get('PSWA_DEFAULT_PROMPT_USER_TEMPLATE')

    try:
        user_prompt = user_prompt_template.format(
            topic=current_topic,
            content=current_content,
            narrative_guidance=narrative_guidance
        )
    except KeyError as e:
        logger.error(f"Error formatting user prompt template: {e}. Using basic prompt. Template was: '{user_prompt_template}'")
        user_prompt = f"Topic: {current_topic}\nContent: {current_content}\nNarrative Guidance: {narrative_guidance}\nPlease generate script."

    # The AIMS service expects a single "prompt" field that combines system and user messages.
    # This might need adjustment if AIMS later supports separate system/user prompt fields.
    full_prompt_for_aims = f"{final_system_message}\n\nUser Request:\n{user_prompt}"

    aims_payload = {
        "prompt": full_prompt_for_aims, # This now contains the combined system (persona + schema) and user (topic + content + narrative guidance) prompts
        "model_id_override": pswa_config.get('PSWA_LLM_MODEL'),
        "max_tokens": pswa_config.get('PSWA_LLM_MAX_TOKENS'),
        "temperature": pswa_config.get('PSWA_LLM_TEMPERATURE'),
    }
    if pswa_config.get('PSWA_LLM_JSON_MODE'): aims_payload["response_format"] = {"type": "json_object"}
    aims_url = pswa_config.get('AIMS_SERVICE_URL'); aims_timeout = pswa_config.get('AIMS_REQUEST_TIMEOUT_SECONDS')
    logger.info(f"[PSWA_MAIN_LOGIC] Sending request to AIMS. URL: {aims_url}, Payload: {json.dumps(aims_payload)}")

    aims_call_start_time = time.time()
    try:
        response = requests.post(aims_url, json=aims_payload, timeout=aims_timeout)
        response.raise_for_status()
        aims_response_data = response.json()
        aims_call_latency_ms = (time.time() - aims_call_start_time) * 1000
        logger.info("PSWA AIMS call processed", extra=dict(metric_name="pswa_aims_call_latency_ms", value=round(aims_call_latency_ms, 2)))

        if not aims_response_data.get("choices") or not aims_response_data["choices"][0].get("text"):
            raise ValueError("AIMS response missing 'choices[0].text'.")
        raw_script_text_from_aims = aims_response_data["choices"][0]["text"].strip()
        llm_model_reported_by_aims = aims_response_data.get("model_id", pswa_config.get('PSWA_LLM_MODEL'))
        if "usage" in aims_response_data: logger.info(f"AIMS usage: {aims_response_data['usage']}")
        logger.info(f"Received script from AIMS (model: {llm_model_reported_by_aims}). Length: {len(raw_script_text_from_aims)}")

        parsed_script = parse_llm_script_output(raw_script_text_from_aims, current_topic)
        parsed_script["llm_model_used"] = llm_model_reported_by_aims
        parsed_script["source"] = "generation_via_aims"
        parsed_script["script_id"] = str(uuid.UUID(parsed_script.get("script_id", uuid.uuid4())))

        if pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED') and not (parsed_script.get("segments") and parsed_script["segments"][0].get("segment_title") == "ERROR"):
            _save_script_to_cache(parsed_script["script_id"], topic_hash, parsed_script, llm_model_reported_by_aims)

        status_for_metric = "success_generation"
        return {"script_data": parsed_script, "status_for_metric": status_for_metric, "aims_call_latency_ms": aims_call_latency_ms}

    except requests.exceptions.Timeout as e_timeout:
        aims_call_latency_ms = (time.time() - aims_call_start_time) * 1000 # Log latency even on timeout
        logger.info("PSWA AIMS call processed (timeout)", extra=dict(metric_name="pswa_aims_call_latency_ms", value=round(aims_call_latency_ms, 2)))
        logger.error(f"AIMS request timed out: {e_timeout}")
        logger.error("PSWA AIMS call failure", extra=dict(metric_name="pswa_aims_call_failure_count", value=1, tags={"error_type": "timeout"}))
        status_for_metric = "aims_timeout_error"
        return {"error_data": {"error_code": "PSWA_AIMS_TIMEOUT", "message": "AIMS request timed out.", "details": str(e_timeout), "source": "error"}, "status_for_metric": status_for_metric}
    except requests.exceptions.HTTPError as e_http:
        aims_call_latency_ms = (time.time() - aims_call_start_time) * 1000
        logger.info("PSWA AIMS call processed (http_error)", extra=dict(metric_name="pswa_aims_call_latency_ms", value=round(aims_call_latency_ms, 2)))
        logger.error(f"AIMS HTTP error {e_http.response.status_code}: {e_http.response.text}")
        logger.error("PSWA AIMS call failure", extra=dict(metric_name="pswa_aims_call_failure_count", value=1, tags={"error_type": f"http_error_{e_http.response.status_code}"}))
        status_for_metric = f"aims_http_error_{e_http.response.status_code}"
        return {"error_data": {"error_code": "PSWA_AIMS_HTTP_ERROR", "message": f"AIMS HTTP error {e_http.response.status_code}.", "details": e_http.response.text, "source": "error"}, "status_for_metric": status_for_metric}
    except requests.exceptions.RequestException as e_req:
        aims_call_latency_ms = (time.time() - aims_call_start_time) * 1000
        logger.info("PSWA AIMS call processed (request_exception)", extra=dict(metric_name="pswa_aims_call_latency_ms", value=round(aims_call_latency_ms, 2)))
        logger.error(f"AIMS request error: {e_req}")
        logger.error("PSWA AIMS call failure", extra=dict(metric_name="pswa_aims_call_failure_count", value=1, tags={"error_type": "request_exception"}))
        status_for_metric = "aims_request_error"
        return {"error_data": {"error_code": "PSWA_AIMS_REQUEST_ERROR", "message": "AIMS communication failed.", "details": str(e_req), "source": "error"}, "status_for_metric": status_for_metric}
    except (json.JSONDecodeError, ValueError) as e_parse:
        # Latency might not be fully accurate if error is in parsing response, but log what we have
        if aims_call_start_time: aims_call_latency_ms = (time.time() - aims_call_start_time) * 1000
        if aims_call_latency_ms: logger.info("PSWA AIMS call processed (parse_error)", extra=dict(metric_name="pswa_aims_call_latency_ms", value=round(aims_call_latency_ms, 2)))
        logger.error(f"Could not decode/parse AIMS response: {e_parse}.")
        logger.error("PSWA AIMS call failure", extra=dict(metric_name="pswa_aims_call_failure_count", value=1, tags={"error_type": "parse_error"}))
        status_for_metric = "aims_bad_response"
        return {"error_data": {"error_code": "PSWA_AIMS_BAD_RESPONSE", "message": "AIMS response invalid.", "details": str(e_parse), "source": "error"}, "status_for_metric": status_for_metric}
    except Exception as e:
        if aims_call_start_time: aims_call_latency_ms = (time.time() - aims_call_start_time) * 1000
        if aims_call_latency_ms: logger.info("PSWA AIMS call processed (unknown_error)", extra=dict(metric_name="pswa_aims_call_latency_ms", value=round(aims_call_latency_ms, 2)))
        logger.error(f"Unexpected error with AIMS: {e}", exc_info=True)
        logger.error("PSWA AIMS call failure", extra=dict(metric_name="pswa_aims_call_failure_count", value=1, tags={"error_type": "unknown_aims_error"}))
        status_for_metric = "aims_unknown_error"
        return {"error_data": {"error_code": "PSWA_AIMS_UNEXPECTED_ERROR", "message": "Unexpected AIMS error.", "details": str(e), "source": "error"}, "status_for_metric": status_for_metric}

# --- Flask Endpoint ---
@app.route('/weave_script', methods=['POST'])
def handle_weave_script():
    request_start_time = time.time()
    final_status_str = "unknown_error" # For request_count metric

    logger.info("[PSWA_FLASK_ENDPOINT] Received request for /weave_script")
    try:
        data = request.get_json()
        if not data:
            final_status_str = "validation_error_payload"
            logger.info("PSWA request completed", extra=dict(metric_name="pswa_weave_script_request_count", value=1, tags={"status": final_status_str}))
            return jsonify({"error_code": "PSWA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json_decode:
        final_status_str = "validation_error_bad_json"
        logger.info("PSWA request completed", extra=dict(metric_name="pswa_weave_script_request_count", value=1, tags={"status": final_status_str}))
        return jsonify({"error_code": "PSWA_MALFORMED_JSON", "message": f"Malformed JSON: {e_json_decode}"}), 400

    content = data.get(KEY_CONTENT); topic = data.get(KEY_TOPIC)
    if not content or not isinstance(content, str) or not content.strip():
        final_status_str = "validation_error_content"
        logger.info("PSWA request completed", extra=dict(metric_name="pswa_weave_script_request_count", value=1, tags={"status": final_status_str}))
        return jsonify({"error_code": "PSWA_INVALID_CONTENT", "message": f"'{KEY_CONTENT}' must be non-empty string."}), 400

    CONTENT_MIN_LENGTH = 50; CONTENT_MAX_LENGTH = 50000
    if len(content) < CONTENT_MIN_LENGTH:
        logger.warning(f"Content length ({len(content)}) < min ({CONTENT_MIN_LENGTH}).")
        # Not treating as a validation error for status, but good to know.
    if len(content) > CONTENT_MAX_LENGTH:
        final_status_str = "validation_error_content_length"
        logger.info("PSWA request completed", extra=dict(metric_name="pswa_weave_script_request_count", value=1, tags={"status": final_status_str}))
        return jsonify({"error_code": "PSWA_CONTENT_TOO_LONG", "message": f"Content exceeds max length {CONTENT_MAX_LENGTH}."}), 400

    if not topic or not isinstance(topic, str) or not topic.strip():
        final_status_str = "validation_error_topic"
        logger.info("PSWA request completed", extra=dict(metric_name="pswa_weave_script_request_count", value=1, tags={"status": final_status_str}))
        return jsonify({"error_code": "PSWA_INVALID_TOPIC", "message": f"'{KEY_TOPIC}' must be non-empty string."}), 400

    logger.info(f"[PSWA_FLASK_ENDPOINT] Calling weave_script for topic: '{topic}'")
    weave_result = weave_script(content, topic)

    result_data = weave_result.get("script_data", weave_result.get("error_data"))
    final_status_str = weave_result.get("status_for_metric", "unknown_weave_script_status")

    overall_latency_ms = (time.time() - request_start_time) * 1000
    logger.info("PSWA weave_script latency", extra=dict(metric_name="pswa_weave_script_latency_ms", value=round(overall_latency_ms, 2)))
    logger.info("PSWA request completed", extra=dict(metric_name="pswa_weave_script_request_count", value=1, tags={"status": final_status_str}))

    if "error_code" in result_data:
        error_code = result_data["error_code"]; message = result_data.get("message", "Error processing script.")
        http_status = 500
        if error_code == "PSWA_AIMS_TIMEOUT": http_status = 504
        elif error_code in ["PSWA_AIMS_HTTP_ERROR", "PSWA_AIMS_BAD_RESPONSE", "PSWA_AIMS_BAD_RESPONSE_JSON"]: http_status = 502
        return jsonify({"error_code": error_code, "message": message, "details": result_data.get("details")}), http_status

    if result_data.get(KEY_SEGMENTS) and result_data[KEY_SEGMENTS][0].get(KEY_SEGMENT_TITLE) == SEGMENT_TITLE_ERROR:
        # This indicates insufficient content as per LLM's structured error
        return jsonify({"error_code": "PSWA_INSUFFICIENT_CONTENT", "message": "Content insufficient (LLM reported).", "details": result_data[KEY_SEGMENTS][0].get(KEY_CONTENT) }), 400

    if not result_data.get(KEY_TITLE) or not any(s.get(KEY_SEGMENT_TITLE) == SEGMENT_TITLE_INTRO for s in result_data.get(KEY_SEGMENTS,[])):
         # This indicates a failure in parsing or LLM not adhering to structure
         return jsonify({ "error_code": "PSWA_SCRIPT_PARSING_FAILURE", "message": "Failed to parse script structure from AIMS."}), 500

    return jsonify(result_data)

if __name__ == "__main__":
    # Logging calls here will use the configured app.logger via the global logger alias
    if pswa_config.get("DATABASE_TYPE") == "sqlite" and not pswa_config.get("SHARED_DATABASE_PATH") and pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        logger.warning("SHARED_DATABASE_PATH not configured for PSWA SQLite mode with caching. Caching may fail.")
    elif pswa_config.get("DATABASE_TYPE") == "postgres" and not all(pswa_config.get(k) for k in ["POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]) and pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        logger.warning("PostgreSQL is cache DB_TYPE, but connection vars missing. Caching may fail.")

    init_pswa_db() # Call init_db based on configured DB_TYPE

    host = pswa_config.get("PSWA_HOST", "0.0.0.0")
    port = pswa_config.get("PSWA_PORT", 5004)
    debug_mode = pswa_config.get("PSWA_DEBUG_MODE", True)
    logger.info(f"--- PSWA Service (AIMS Client) starting on {host}:{port} (Debug: {debug_mode}, DB: {pswa_config.get('DATABASE_TYPE')}) ---")
    app.run(host=host, port=port, debug=debug_mode)
