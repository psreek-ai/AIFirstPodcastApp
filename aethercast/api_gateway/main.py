import sys
import os
from dotenv import load_dotenv # Added
from flask import Flask, jsonify, request, send_file, send_from_directory # Added send_from_directory
import uuid 
import sqlite3
from datetime import datetime, timedelta # Added timedelta
import json # Added
import requests # Added for TDA calls

# --- Path Setup for CPOA Import ---
# Add project root to sys.path to allow imports from aethercast.cpoa
# current_dir is aethercast/api_gateway/
current_dir = os.path.dirname(os.path.abspath(__file__))
# parent_dir is aethercast/
parent_dir = os.path.dirname(current_dir)
# project_root is the directory containing aethercast/
project_root = os.path.dirname(parent_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv() # Added

# --- Service URLs ---
TDA_SERVICE_URL = os.getenv("TDA_SERVICE_URL", "http://localhost:5000/discover_topics") # Assuming TDA is on 5000 as per typical setup.


# --- Database Configuration ---
DATABASE_FILE = os.getenv("DATABASE_FILE", "aethercast_podcasts.db") # Will be created in the same dir as this script (api_gateway)
                                         # For production, choose a more persistent location.

DB_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS podcasts (
    podcast_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    -- Core CPOA status fields
    cpoa_status TEXT,
    cpoa_error_message TEXT,
    -- Key details from successful generation
    final_audio_filepath TEXT,
    stream_id TEXT,
    asf_websocket_url TEXT,
    asf_notification_status TEXT,
    -- Timestamps
    task_created_timestamp TEXT NOT NULL,
    last_updated_timestamp TEXT,
    -- Full CPOA log/details
    cpoa_full_orchestration_log TEXT,
    -- Voice parameters used for TTS
    tts_settings_used TEXT
);

CREATE TABLE IF NOT EXISTS topics_snippets (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN ('topic', 'snippet')),
    title TEXT NOT NULL,
    summary TEXT,
    keywords TEXT,
    source_url TEXT,
    source_name TEXT,
    original_topic_details TEXT,
    llm_model_used_for_snippet TEXT,
    cover_art_prompt TEXT,
    generation_timestamp TEXT NOT NULL,
    last_accessed_timestamp TEXT,
    relevance_score REAL
);

CREATE TABLE IF NOT EXISTS generated_scripts (
    script_id TEXT PRIMARY KEY,
    topic_hash TEXT NOT NULL UNIQUE, -- Hash of topic + input content summary/key elements
    structured_script_json TEXT NOT NULL,
    generation_timestamp TEXT NOT NULL,
    llm_model_used TEXT,
    last_accessed_timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_topic_hash ON generated_scripts (topic_hash);
"""

# --- API Gateway Specific Configurations ---
API_GW_SNIPPET_CACHE_SIZE = int(os.getenv("API_GW_SNIPPET_CACHE_SIZE", "10"))
API_GW_SNIPPET_CACHE_MAX_AGE_HOURS = int(os.getenv("API_GW_SNIPPET_CACHE_MAX_AGE_HOURS", "24"))


# --- Database Helper Functions ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row # Allows accessing columns by name
    return conn

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Check if the table exists and if it has the old 'audio_filepath' column to suggest deletion for dev
        cursor.execute("PRAGMA table_info(podcasts)")
        columns = [col[1] for col in cursor.fetchall()]
        if "audio_filepath" in columns and "final_audio_filepath" not in columns : # A simple check for old schema
             app.logger.warning("Old 'podcasts' table schema detected (missing 'final_audio_filepath' or 'tts_settings_used'). For development, please delete the database file 'aethercast_podcasts.db' to apply the new schema. This is a destructive operation for dev only.")
        elif "tts_settings_used" not in columns: # Check specifically for the new column from this subtask
            app.logger.warning("The 'podcasts' table is missing the 'tts_settings_used' column. Consider DB deletion for schema update in dev.")

        # Check for the existence of the new topics_snippets table.
        # The `IF NOT EXISTS` in its DDL handles initial creation.
        # This log is mainly for awareness during development if the schema *within* topics_snippets needs future changes.
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='topics_snippets';")
        if not cursor.fetchone():
            app.logger.info("Table 'topics_snippets' not found. It will be created as per DB_SCHEMA_SQL.")
        else:
            app.logger.info("Table 'topics_snippets' already exists.")

        # Check for generated_scripts table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='generated_scripts';")
        if not cursor.fetchone():
            app.logger.info("Table 'generated_scripts' not found. It will be created as per DB_SCHEMA_SQL.")
        else:
            app.logger.info("Table 'generated_scripts' already exists.")
            # Check for index (less critical to log, but good for completeness if debugging)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_topic_hash';")
            if not cursor.fetchone():
                 app.logger.info("Index 'idx_topic_hash' for 'generated_scripts' not found. It will be created.")
            else:
                 app.logger.info("Index 'idx_topic_hash' for 'generated_scripts' already exists.")

        cursor.executescript(DB_SCHEMA_SQL) # Use executescript for multi-statement SQL
        conn.commit()
        app.logger.info("Database initialization processed. 'podcasts', 'topics_snippets', and 'generated_scripts' tables (and indexes) ensured.")
    except sqlite3.Error as e:
        # Use app.logger if available, otherwise print
        log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
        log_func(f"Database initialization error: {e}")
    finally:
        if conn:
            conn.close()


# --- Attempt CPOA Import ---
# Define placeholders that raise ImportError if called, indicating the real function wasn't imported.
def _cpoa_placeholder_podcast(*args, **kwargs): raise ImportError("CPOA's orchestrate_podcast_generation function is not available due to import failure.")
def _cpoa_placeholder_snippet(*args, **kwargs): raise ImportError("CPOA's orchestrate_snippet_generation function is not available due to import failure.")
def _cpoa_placeholder_explore(*args, **kwargs): raise ImportError("CPOA's orchestrate_topic_exploration function is not available due to import failure.")

# Initialize with placeholders
orchestrate_podcast_generation = _cpoa_placeholder_podcast
orchestrate_snippet_generation = _cpoa_placeholder_snippet
orchestrate_topic_exploration = _cpoa_placeholder_explore

cpoa_podcast_func_imported = False
cpoa_snippet_func_imported = False
cpoa_exploration_func_imported = False
CPOA_OVERALL_IMPORT_ERROR_MESSAGE = [] # Store multiple error messages if needed

_pre_init_logger = print # Fallback logger before Flask app context is ready

try:
    from aethercast.cpoa.main import orchestrate_podcast_generation as opg_real
    orchestrate_podcast_generation = opg_real
    cpoa_podcast_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_podcast_generation.")
except ImportError as e:
    CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_podcast_generation: {e}")

try:
    from aethercast.cpoa.main import orchestrate_snippet_generation as osg_real
    orchestrate_snippet_generation = osg_real
    cpoa_snippet_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_snippet_generation.")
except ImportError as e:
    CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_snippet_generation: {e}")

try:
    from aethercast.cpoa.main import orchestrate_topic_exploration as ote_real
    orchestrate_topic_exploration = ote_real
    cpoa_exploration_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_topic_exploration.")
except ImportError as e:
    CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_topic_exploration: {e}")

if CPOA_OVERALL_IMPORT_ERROR_MESSAGE:
    _pre_init_logger(f"CPOA Module Import Errors: {'; '.join(CPOA_OVERALL_IMPORT_ERROR_MESSAGE)}")


# --- Flask App Initialization ---
app = Flask(__name__)

# Configure basic logging for Flask app if not already configured by Flask/debug mode
# This logging setup is fine, or Flask's default logger can be used via app.logger
if not app.debug and not app.logger.handlers: # Check if handlers are already added
    import logging
    # Ensure logging is configured before init_db might try to use app.logger
    # For simplicity, if flask's app.logger is not yet fully configured, print will be used in init_db
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - API_GW - %(message)s')

# Log loaded configuration after app is initialized, so app.logger is available
with app.app_context():
    app.logger.info("--- API Gateway Configuration ---")
    app.logger.info(f"TDA_SERVICE_URL: {TDA_SERVICE_URL}")
    app.logger.info(f"DATABASE_FILE: {DATABASE_FILE}")
    app.logger.info(f"FEND_DIR: {FEND_DIR}") # FEND_DIR is derived below, but logged here after app context is up.
    app.logger.info(f"API_GW_SNIPPET_CACHE_SIZE: {API_GW_SNIPPET_CACHE_SIZE}")
    app.logger.info(f"API_GW_SNIPPET_CACHE_MAX_AGE_HOURS: {API_GW_SNIPPET_CACHE_MAX_AGE_HOURS}")
    app.logger.info("--- End API Gateway Configuration ---")

# --- Frontend Directory Path ---
# Assuming this main.py is in aethercast/api_gateway/
# FEND_DIR should point to aethercast/fend/
FEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fend'))


# --- Remove Global Storage PODCAST_FILE_MAP ---
# PODCAST_FILE_MAP = {} # This line is now removed.

# --- Static Frontend File Serving ---
@app.route('/')
def serve_index():
    app.logger.info(f"Serving index.html from: {FEND_DIR}")
    return send_from_directory(FEND_DIR, 'index.html')

@app.route('/style.css')
def serve_style():
    app.logger.info(f"Serving style.css from: {FEND_DIR}")
    return send_from_directory(FEND_DIR, 'style.css')

@app.route('/app.js')
def serve_script():
    app.logger.info(f"Serving app.js from: {FEND_DIR}")
    return send_from_directory(FEND_DIR, 'app.js')

# --- Health Check Endpoint ---
@app.route('/health', methods=['GET'])
def health_check():
    podcast_func_status = "successfully imported" if cpoa_podcast_func_imported else f"failed to import ({'; '.join(CPOA_OVERALL_IMPORT_ERROR_MESSAGE)})"
    snippet_func_status = "successfully imported" if cpoa_snippet_func_imported else f"failed to import ({'; '.join(CPOA_OVERALL_IMPORT_ERROR_MESSAGE)})"
    exploration_func_status = "successfully imported" if cpoa_exploration_func_imported else f"failed to import ({'; '.join(CPOA_OVERALL_IMPORT_ERROR_MESSAGE)})" # Added
    db_status_message = "Database connection successful."
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1 FROM podcasts LIMIT 1;") # Try a simple query
        conn.close()
    except sqlite3.Error as e:
        db_status_message = f"Database connection error: {e}"
        app.logger.error(f"Health check DB error: {e}")

    return jsonify({
        "status": "API Gateway is healthy",
        "cpoa_functions_import_status": {
            "orchestrate_podcast_generation": {"imported": cpoa_podcast_func_imported, "status": podcast_func_status},
            "orchestrate_snippet_generation": {"imported": cpoa_snippet_func_imported, "status": snippet_func_status},
            "orchestrate_topic_exploration": {"imported": cpoa_exploration_func_imported, "status": exploration_func_status} # Added
        },
        "database_status": db_status_message,
        "cpoa_import_error_details": CPOA_OVERALL_IMPORT_ERROR_MESSAGE if CPOA_OVERALL_IMPORT_ERROR_MESSAGE else "None"
    }), 200

# --- Snippets Endpoint ---
@app.route('/api/v1/snippets', methods=['GET'])
def get_dynamic_snippets():
    app.logger.info("Request received for /api/v1/snippets")

    if not cpoa_snippet_func_imported:
        app.logger.error("CPOA snippet generation function not loaded. Cannot process snippet generation.")
        return jsonify({"error": "Service Unavailable", "message": f"Core snippet orchestration module not loaded. Import error: {CPOA_IMPORT_ERROR_MESSAGE}"}), 503

    # --- Try fetching from DB cache first ---
    conn = None
    cached_snippets = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Calculate the oldest acceptable generation timestamp
        # Using datetime directly without strptime for ISO format
        max_age_dt = datetime.utcnow() - timedelta(hours=API_GW_SNIPPET_CACHE_MAX_AGE_HOURS)
        oldest_acceptable_timestamp = max_age_dt.isoformat()

        app.logger.info(f"Fetching snippets from DB, limit: {API_GW_SNIPPET_CACHE_SIZE}, newer than: {oldest_acceptable_timestamp}")
        cursor.execute(
            """
            SELECT * FROM topics_snippets
            WHERE type = 'snippet' AND generation_timestamp >= ?
            ORDER BY generation_timestamp DESC
            LIMIT ?
            """,
            (oldest_acceptable_timestamp, API_GW_SNIPPET_CACHE_SIZE)
        )
        rows = cursor.fetchall()

        current_time_iso = datetime.now().isoformat()
        snippet_ids_to_update_access_time = []

        for row in rows:
            snippet_dict = dict(row) # Convert sqlite3.Row to dict
            # Deserialize JSON fields
            if snippet_dict.get("keywords"):
                try:
                    snippet_dict["keywords"] = json.loads(snippet_dict["keywords"])
                except json.JSONDecodeError:
                    app.logger.warning(f"Failed to decode keywords JSON for snippet {snippet_dict['id']}: {snippet_dict['keywords']}")
                    snippet_dict["keywords"] = [] # Default to empty list on error

            if snippet_dict.get("original_topic_details"):
                try:
                    snippet_dict["original_topic_details"] = json.loads(snippet_dict["original_topic_details"])
                except json.JSONDecodeError:
                    app.logger.warning(f"Failed to decode original_topic_details JSON for snippet {snippet_dict['id']}: {snippet_dict['original_topic_details']}")
                    snippet_dict["original_topic_details"] = None # Default to None

            cached_snippets.append(snippet_dict)
            snippet_ids_to_update_access_time.append(snippet_dict["id"])

        if snippet_ids_to_update_access_time:
            # Batch update last_accessed_timestamp
            # Ensure each ID is a tuple for executemany
            update_params = [(current_time_iso, snippet_id) for snippet_id in snippet_ids_to_update_access_time]
            cursor.executemany(
                "UPDATE topics_snippets SET last_accessed_timestamp = ? WHERE id = ?",
                update_params
            )
            conn.commit()
            app.logger.info(f"Updated last_accessed_timestamp for {len(snippet_ids_to_update_access_time)} cached snippets.")

    except sqlite3.Error as e_sql:
        app.logger.error(f"Database error while fetching/updating cached snippets: {e_sql}", exc_info=True)
        # Do not fail the request here; proceed to generate new snippets.
    except Exception as e_gen_cache: # Catch other potential errors during cache read
        app.logger.error(f"Unexpected error during snippet cache retrieval: {e_gen_cache}", exc_info=True)
    finally:
        if conn:
            conn.close()

    # Cache decision logic: Use cached snippets if we have a reasonable number.
    # Heuristic: if we have at least half the desired cache size.
    if len(cached_snippets) >= API_GW_SNIPPET_CACHE_SIZE / 2:
        app.logger.info(f"Serving {len(cached_snippets)} snippets from DB cache.")
        return jsonify({"snippets": cached_snippets, "source": "cache"}), 200
    else:
        app.logger.info(f"Cache miss or insufficient fresh snippets ({len(cached_snippets)} found). Proceeding to generate new snippets.")

    # --- Fallback to TDA/CPOA if cache is insufficient ---
    topics_from_tda = []
    try:
        app.logger.info(f"Calling TDA service at {TDA_SERVICE_URL} to discover topics.")
        tda_payload = {"limit": request.args.get('limit', 5, type=int)}
        tda_response = requests.post(TDA_SERVICE_URL, json=tda_payload, timeout=30)
        tda_response.raise_for_status()
        tda_data = tda_response.json()
        # TDA now returns "topics" directly in the root of the JSON response as per recent updates.
        # Before it was {"discovered_topics": [...]}, now it's {"topics": [...] }
        topics_from_tda = tda_data.get("topics", tda_data.get("discovered_topics", []))


        if not topics_from_tda:
            app.logger.warning("TDA service returned no topics.")
            return jsonify({"message": "No topics available from TDA to generate new snippets.", "snippets": []}), 200
        app.logger.info(f"Received {len(topics_from_tda)} topics from TDA for new snippet generation.")

    except requests.exceptions.HTTPError as e_http:
        error_details = str(e_http)
        if e_http.response is not None:
            try:
                error_payload = e_http.response.json()
                error_details = error_payload.get("error", error_details) if isinstance(error_payload, dict) else error_details
            except json.JSONDecodeError:
                error_details = e_http.response.text[:200]
        app.logger.error(f"TDA service call failed (HTTP error): {error_details}", exc_info=True)
        return jsonify({"error": "Service Unavailable", "message": f"Failed to connect to Topic Discovery Agent: {error_details}"}), 503
    except requests.exceptions.RequestException as e_req:
        app.logger.error(f"TDA service call failed (network/request error): {e_req}", exc_info=True)
        return jsonify({"error": "Service Unavailable", "message": f"Topic Discovery Agent is unreachable: {e_req}"}), 503
    except json.JSONDecodeError as e_json:
        app.logger.error(f"TDA service response was not valid JSON: {e_json}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "Invalid response from Topic Discovery Agent."}), 500
    except Exception as e_gen:
        app.logger.error(f"Unexpected error calling TDA service: {e_gen}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": f"An unexpected error occurred while fetching topics: {e_gen}"}), 500

    generated_snippets = []
    for topic_obj in topics_from_tda:
        topic_id_val = topic_obj.get("id") or topic_obj.get("topic_id")
        title_sug_val = topic_obj.get("title") or topic_obj.get("title_suggestion")

        topic_info_for_cpoa = {
            "topic_id": topic_id_val,
            "title_suggestion": title_sug_val,
            "summary": topic_obj.get("summary"),
            "keywords": topic_obj.get("keywords", []),
            # Pass the full original TDA topic object to CPOA, so it can store it in DB for snippets
            "original_topic_details_from_tda": topic_obj
        }
        if not topic_info_for_cpoa["title_suggestion"]:
            app.logger.warning(f"Skipping snippet generation for topic from TDA due to missing title: {topic_obj}")
            continue

        app.logger.info(f"Requesting snippet generation from CPOA for topic: {title_sug_val}")
        try:
            # CPOA's orchestrate_snippet_generation will now save to DB itself.
            snippet_result = orchestrate_snippet_generation(topic_info=topic_info_for_cpoa)
            if snippet_result and "error" not in snippet_result:
                generated_snippets.append(snippet_result)
                app.logger.info(f"Snippet generated successfully by CPOA for topic: {title_sug_val}")
            else:
                app.logger.error(f"Snippet generation by CPOA failed for topic '{title_sug_val}': {snippet_result.get('details', 'Unknown CPOA error')}")
        except Exception as e_cpoa_snip:
            app.logger.error(f"Unexpected error calling CPOA orchestrate_snippet_generation for topic '{title_sug_val}': {e_cpoa_snip}", exc_info=True)

    if not generated_snippets:
        app.logger.info("No snippets were successfully generated by CPOA for the discovered topics.")
        return jsonify({"message": "No new snippets generated for the available topics.", "snippets": []}), 200

    app.logger.info(f"Successfully generated {len(generated_snippets)} new snippets via TDA/CPOA.")
    return jsonify({"snippets": generated_snippets, "source": "generation"}), 200

# --- Topic Exploration Endpoint ---
@app.route('/api/v1/topics/explore', methods=['POST'])
def explore_topic():
    app.logger.info("Request received for /api/v1/topics/explore")
    data = request.get_json()

    if not data:
        app.logger.warning("Bad request to /api/v1/topics/explore: No JSON payload.")
        return jsonify({"error": "Bad Request", "message": "Missing JSON payload."}), 400

    current_topic_id = data.get('current_topic_id')
    keywords = data.get('keywords')
    depth_mode = data.get('depth', 'deeper') # Default to 'deeper'

    if not current_topic_id and not keywords:
        app.logger.warning("Bad request to /api/v1/topics/explore: Missing 'current_topic_id' or 'keywords'.")
        return jsonify({"error": "Bad Request", "message": "Either 'current_topic_id' or 'keywords' must be provided."}), 400

    # Ensure CPOA has the new exploration function imported
    # Check if the specific CPOA function for exploration is available
    if not cpoa_exploration_func_imported:
        app.logger.error(f"CPOA's orchestrate_topic_exploration function is not available due to import error(s): {CPOA_OVERALL_IMPORT_ERROR_MESSAGE}")
        return jsonify({"error": "Service Unavailable", "message": "Core topic exploration module is not available."}), 503

    try:
        app.logger.info(f"Calling CPOA to explore topic. Topic ID: {current_topic_id}, Keywords: {keywords}, Depth: {depth_mode}")

        # Call the (potentially placeholder or real) orchestrate_topic_exploration function
        exploration_results = orchestrate_topic_exploration(
            current_topic_id=current_topic_id,
            keywords=keywords,
            depth_mode=depth_mode
            # client_id is not passed here, as this is an internal generation loop,
            # not directly tied to a single client's immediate podcast request UI.
        )

        app.logger.info(f"CPOA returned {len(exploration_results)} items for topic exploration.")
        return jsonify({"explored_topics_or_snippets": exploration_results}), 200

    except ValueError as ve: # Catch specific errors raised by CPOA for bad inputs not caught by initial check
        app.logger.error(f"ValueError during topic exploration: {ve}")
        return jsonify({"error": "Bad Request", "message": str(ve)}), 400
    except ImportError as ie: # If the dynamic import above failed (should be caught by initial check ideally)
        app.logger.error(f"CPOA function 'orchestrate_topic_exploration' unavailable: {ie}")
        return jsonify({"error": "Service Unavailable", "message": "Core topic exploration module is not available."}), 503
    except Exception as e:
        app.logger.error(f"Unexpected error during topic exploration: {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred during topic exploration."}), 500


# --- Podcast Generation Endpoint ---
@app.route('/api/v1/podcasts', methods=['POST'])
def create_podcast_generation_task():
    data = request.get_json()

    if not data or 'topic' not in data or not data['topic']:
        app.logger.warning("Bad request to /api/v1/podcasts: Missing or empty 'topic'.")
        return jsonify({"error": "Bad Request", "message": "Missing or empty 'topic' in request body."}), 400
    
    topic = data['topic']
    voice_params_from_request = data.get('voice_params') # Optional
    client_id_from_request = data.get('client_id') # Optional client_id for UI updates

    if voice_params_from_request is not None and not isinstance(voice_params_from_request, dict):
        app.logger.warning("Bad request to /api/v1/podcasts: 'voice_params' was provided but not as a valid JSON object.")
        return jsonify({"error": "Bad Request", "message": "'voice_params' must be a valid JSON object if provided."}), 400

    if client_id_from_request is not None and not isinstance(client_id_from_request, str):
        app.logger.warning("Bad request to /api/v1/podcasts: 'client_id' was provided but not as a string.")
        return jsonify({"error": "Bad Request", "message": "'client_id' must be a string if provided."}), 400


    app.logger.info(f"Received podcast generation request for topic string: '{topic}'. Voice params: {voice_params_from_request}. Client ID: {client_id_from_request}")

    if not cpoa_podcast_func_imported:
        app.logger.error("CPOA podcast generation function not loaded. Cannot process podcast generation.")
        return jsonify({"error": "Service Unavailable", "message": f"Core podcast orchestration module (podcast func) not loaded. Import error: {CPOA_IMPORT_ERROR_MESSAGE}"}), 503

    try:
        podcast_id = str(uuid.uuid4())
        task_created_timestamp = datetime.now().isoformat()
        db_path_for_cpoa = DATABASE_FILE # Use the configured DB file

        # Initial record creation with "pending" status
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO podcasts
                (podcast_id, topic, cpoa_status, task_created_timestamp, last_updated_timestamp, tts_settings_used)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (podcast_id, topic, "pending_api_gateway", task_created_timestamp, task_created_timestamp, json.dumps(voice_params_from_request) if voice_params_from_request else None)
            )
            conn.commit()
            app.logger.info(f"Initial record for podcast_id {podcast_id} created with topic '{topic}'. Voice params (if any) stored.")
        except sqlite3.Error as e:
            app.logger.error(f"Database error creating initial record for topic '{topic}', podcast_id {podcast_id}: {e}", exc_info=True)
            return jsonify({"error": "Database Error", "message": "Failed to create initial podcast task record."}), 500
        finally:
            if conn:
                conn.close()

        app.logger.info(f"Invoking CPOA orchestrate_podcast_generation for topic: '{topic}', task_id: {podcast_id}, voice_params: {voice_params_from_request}, client_id: {client_id_from_request}")

        # Pass client_id to CPOA if available
        cpoa_kwargs = {
            "topic": topic,
            "task_id": podcast_id,
            "db_path": db_path_for_cpoa,
            "voice_params_input": voice_params_from_request
        }
        if client_id_from_request:
            cpoa_kwargs["client_id"] = client_id_from_request

        cpoa_result = orchestrate_podcast_generation(**cpoa_kwargs)
        app.logger.info(f"CPOA returned for task_id '{podcast_id}'. Status: {cpoa_result.get('status')}")

        # Extract details from CPOA result for final update
        final_cpoa_status = cpoa_result.get("status", "unknown_cpoa_status")
        final_cpoa_error_message = cpoa_result.get("error_message")
        final_audio_details = cpoa_result.get("final_audio_details", {})
        final_audio_filepath = final_audio_details.get("audio_filepath")
        final_stream_id = final_audio_details.get("stream_id")
        final_asf_websocket_url = cpoa_result.get("asf_websocket_url")
        final_asf_notification_status = cpoa_result.get("asf_notification_status")
        cpoa_log_json = json.dumps(cpoa_result.get("orchestration_log", []))

        # Get tts_settings_used from CPOA's result (from final_audio_details)
        tts_settings_used_dict = final_audio_details.get("tts_settings_used", {})
        tts_settings_used_json = json.dumps(tts_settings_used_dict) if tts_settings_used_dict else None

        last_updated_ts = datetime.now().isoformat()

        conn_update = None
        try:
            conn_update = get_db_connection()
            cursor_update = conn_update.cursor()
            cursor_update.execute(
                """
                UPDATE podcasts
                SET cpoa_status = ?, cpoa_error_message = ?, final_audio_filepath = ?,
                    stream_id = ?, asf_websocket_url = ?, asf_notification_status = ?,
                    cpoa_full_orchestration_log = ?, tts_settings_used = ?, last_updated_timestamp = ?
                WHERE podcast_id = ?
                """,
                (final_cpoa_status, final_cpoa_error_message, final_audio_filepath,
                 final_stream_id, final_asf_websocket_url, final_asf_notification_status,
                 cpoa_log_json, tts_settings_used_json, last_updated_ts, podcast_id)
            )
            conn_update.commit()
            app.logger.info(f"Final details for podcast {podcast_id} updated in DB.")
        except sqlite3.Error as e:
            app.logger.error(f"Database error updating final details for podcast {podcast_id}: {e}", exc_info=True)
            # Even if this update fails, the CPOA process ran. The response should reflect CPOA's outcome.
            # The record will be in the DB from the initial insert, but might lack final details.
        finally:
            if conn_update:
                conn_update.close()

        # Construct response based on CPOA's outcome
        if final_cpoa_status == "completed" and final_audio_filepath:
            return jsonify({
                "podcast_id": podcast_id,
                "topic": topic,
                "generation_status": final_cpoa_status,
                "audio_url": f"/api/v1/podcasts/{podcast_id}/audio.mp3",
                "message": "Podcast generation task processed. Final status: completed.",
                "details": cpoa_result # Return full CPOA result for now
            }), 201 # 201 for successful creation and processing leading to resource
        elif final_cpoa_status in ["completed_with_warnings", "completed_with_errors", "completed_with_asf_notification_failure"] or \
             (final_cpoa_status == "completed" and not final_audio_filepath): # Completed but something is off
            return jsonify({
                "podcast_id": podcast_id, # Still provide podcast_id
                "topic": topic,
                "generation_status": final_cpoa_status,
                "message": final_cpoa_error_message or "Podcast generation task processed with warnings/issues.",
                "details": cpoa_result
            }), 200 # 200 OK as the task was accepted and processed, but outcome has issues.
        else: # Failed states from CPOA
             return jsonify({
                "podcast_id": podcast_id, # Still provide podcast_id
                "topic": topic,
                "generation_status": final_cpoa_status,
                "message": final_cpoa_error_message or "Podcast generation failed.",
                "details": cpoa_result
            }), 200 # 200 OK because the API gateway handled the request, CPOA processed it (and failed)
            # Alternative for hard failures: return 500 if CPOA reports a critical internal failure not due to user input.
            # For now, 200 with error in payload seems fine as CPOA itself didn't crash.

    except ImportError as ie: # CPOA module itself not found
        app.logger.error(f"CPOA function unavailable during request: {ie}")
        # Update the initially created DB record to reflect this failure if possible
        # This error happens before CPOA is called, so the 'pending_api_gateway' status is still relevant.
        # We can update it to 'failed_cpoa_module_unavailable'.
        conn_fail = None
        try:
            conn_fail = get_db_connection()
            cursor_fail = conn_fail.cursor()
            cursor_fail.execute("UPDATE podcasts SET cpoa_status = ?, cpoa_error_message = ?, last_updated_timestamp = ? WHERE podcast_id = ?",
                                ("failed_cpoa_module_unavailable", str(ie), datetime.now().isoformat(), podcast_id if 'podcast_id' in locals() else "unknown_id"))
            conn_fail.commit()
        except Exception as db_e:
            app.logger.error(f"DB error updating status for CPOA module import failure: {db_e}")
        finally:
            if conn_fail: conn_fail.close()
        return jsonify({"error": "Service Unavailable", "message": "Core podcast orchestration module is not available."}), 503

    except Exception as e: # Other unexpected errors in API Gateway before or after CPOA call
        app.logger.error(f"Unexpected error during podcast generation task for topic '{topic}': {e}", exc_info=True)
        conn_fail_unexp = None
        try: # Try to update DB if podcast_id was generated
            if 'podcast_id' in locals():
                conn_fail_unexp = get_db_connection()
                cursor_fail_unexp = conn_fail_unexp.cursor()
                cursor_fail_unexp.execute("UPDATE podcasts SET cpoa_status = ?, cpoa_error_message = ?, last_updated_timestamp = ? WHERE podcast_id = ?",
                                    ("failed_api_gateway_error", str(e), datetime.now().isoformat(), podcast_id))
                conn_fail_unexp.commit()
        except Exception as db_e:
            app.logger.error(f"DB error updating status for unexpected API Gateway error: {db_e}")
        finally:
            if conn_fail_unexp: conn_fail_unexp.close()
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred."}), 500


# --- List All Podcasts Endpoint ---
@app.route('/api/v1/podcasts', methods=['GET'])
def list_podcasts():
    app.logger.info("Request received for /api/v1/podcasts (list all)")
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        if page < 1: page = 1
        if per_page < 1: per_page = 10
        if per_page > 100: per_page = 100 # Max limit
    except ValueError:
        app.logger.warning("Invalid pagination parameters received.")
        return jsonify({"error": "Bad Request", "message": "Invalid page or per_page parameters."}), 400

    offset = (page - 1) * per_page

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM podcasts")
        total_podcasts_row = cursor.fetchone()
        total_podcasts = total_podcasts_row[0] if total_podcasts_row else 0

        total_pages = (total_podcasts + per_page - 1) // per_page if total_podcasts > 0 else 1

        cursor.execute(
            """SELECT podcast_id, topic, task_created_timestamp, cpoa_status, final_audio_filepath
               FROM podcasts
               ORDER BY task_created_timestamp DESC
               LIMIT ? OFFSET ?""",
            (per_page, offset)
        )
        podcasts_rows = cursor.fetchall()

        podcasts_list = []
        for row in podcasts_rows:
            podcasts_list.append({
                "podcast_id": row["podcast_id"],
                "topic": row["topic"],
                "task_created_timestamp": row["task_created_timestamp"],
                "status": row["cpoa_status"],
                "audio_url": f"/api/v1/podcasts/{row['podcast_id']}/audio.mp3" if row["final_audio_filepath"] else None,
            })

        app.logger.info(f"Returning {len(podcasts_list)} podcasts for page {page}.")
        return jsonify({
            "podcasts": podcasts_list,
            "page": page,
            "per_page": per_page,
            "total_podcasts": total_podcasts,
            "total_pages": total_pages
        }), 200

    except sqlite3.Error as e:
        app.logger.error(f"Database error listing podcasts: {e}", exc_info=True)
        return jsonify({"error": "Database Error", "message": "Error retrieving podcast list."}), 500
    except Exception as e: # Catch any other unexpected errors
        app.logger.error(f"Unexpected error listing podcasts: {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred."}), 500
    finally:
        if conn:
            conn.close()

# --- Get Specific Podcast Details Endpoint ---
@app.route('/api/v1/podcasts/<string:podcast_id>', methods=['GET'])
def get_podcast_details(podcast_id: str):
    app.logger.info(f"Request received for /api/v1/podcasts/{podcast_id}")
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Fetch all new columns, including tts_settings_used
        cursor.execute(
            """SELECT podcast_id, topic, cpoa_status, cpoa_error_message,
                      final_audio_filepath, stream_id, asf_websocket_url,
                      asf_notification_status, task_created_timestamp,
                      last_updated_timestamp, cpoa_full_orchestration_log, tts_settings_used
               FROM podcasts WHERE podcast_id = ?""", (podcast_id,))
        row = cursor.fetchone()

        if row:
            orchestration_log_data = []
            if row["cpoa_full_orchestration_log"]:
                try:
                    orchestration_log_data = json.loads(row["cpoa_full_orchestration_log"])
                except json.JSONDecodeError as e:
                    app.logger.error(f"Error parsing cpoa_full_orchestration_log JSON for podcast {podcast_id}: {e}")
                    orchestration_log_data = [{"error": "Failed to parse orchestration log"}] # Provide error in log field

            tts_settings_data = None
            if row["tts_settings_used"]: # This field might be NULL if not set during creation/update
                try:
                    tts_settings_data = json.loads(row["tts_settings_used"])
                except json.JSONDecodeError as e:
                    app.logger.error(f"Error parsing tts_settings_used JSON for podcast {podcast_id}: {e}")
                    tts_settings_data = {"error": "Failed to parse TTS settings"}


            response_data = {
                "podcast_id": row["podcast_id"],
                "topic": row["topic"],
                "status": row["cpoa_status"],
                "error_message": row["cpoa_error_message"],
                "audio_url": f"/api/v1/podcasts/{row['podcast_id']}/audio.mp3" if row["final_audio_filepath"] else None,
                "final_audio_filepath": row["final_audio_filepath"],
                "stream_id": row["stream_id"],
                "asf_websocket_url": row["asf_websocket_url"],
                "asf_notification_status": row["asf_notification_status"],
                "task_created_timestamp": row["task_created_timestamp"],
                "last_updated_timestamp": row["last_updated_timestamp"],
                "orchestration_log": orchestration_log_data,
                "tts_settings_used": tts_settings_data
            }
            app.logger.info(f"Returning details for podcast {podcast_id}.")
            return jsonify(response_data), 200
        else:
            app.logger.warning(f"Podcast ID '{podcast_id}' not found in database.")
            return jsonify({"error": "Not Found", "message": "Podcast not found."}), 404

    except sqlite3.Error as e:
        app.logger.error(f"Database error retrieving podcast {podcast_id}: {e}", exc_info=True)
        return jsonify({"error": "Database Error", "message": "Error retrieving podcast details."}), 500
    except Exception as e: # Catch any other unexpected errors
        app.logger.error(f"Unexpected error retrieving podcast {podcast_id}: {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred."}), 500
    finally:
        if conn:
            conn.close()


# --- Serve Podcast Audio Endpoint ---
@app.route('/api/v1/podcasts/<string:podcast_id>/audio.mp3', methods=['GET'])
def serve_podcast_audio(podcast_id: str):
    app.logger.info(f"Request received to serve audio for podcast_id: {podcast_id}")
    
    audio_filepath = None # Will be final_audio_filepath
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Updated to select final_audio_filepath
        cursor.execute("SELECT final_audio_filepath FROM podcasts WHERE podcast_id = ?", (podcast_id,))
        db_record = cursor.fetchone()
        
        if db_record and db_record["final_audio_filepath"]:
            audio_filepath = db_record["final_audio_filepath"]
        else:
            app.logger.warning(f"Podcast ID '{podcast_id}' not found in database or final_audio_filepath is null.")
            return jsonify({"error": "Not Found", "message": "Audio not found for this podcast or generation is pending/failed."}), 404 # More specific message
            
    except sqlite3.Error as e:
        app.logger.error(f"Database error retrieving final_audio_filepath for podcast_id '{podcast_id}': {e}", exc_info=True)
        return jsonify({"error": "Database Error", "message": "Error retrieving audio metadata."}), 500
    finally:
        if conn:
            conn.close()

    # audio_filepath should be set if we reach here from the try block successfully
    if not os.path.exists(audio_filepath): # This check is crucial
        app.logger.error(f"Audio file for podcast_id '{podcast_id}' not found at expected path: {audio_filepath}")
        # Note: No PODCAST_FILE_MAP.pop here as we are using DB.
        # A more advanced system might flag this record in the DB as having a missing file.
        return jsonify({"error": "Not Found", "message": "Audio file missing or no longer available."}), 404

    # Security: Validate that the resolved path is within an expected directory.
    # For now, SHARED_AUDIO_DIR is in VFA, not directly known here without import/config.
    # Assuming audio_filepath from DB is trusted to be from the correct shared location.
    # A better approach would be to store relative paths or IDs and resolve them against a configured base path here.

    try:
        app.logger.info(f"Attempting to send file: {audio_filepath} for podcast_id: {podcast_id}")
        # send_file needs absolute path. Assume audio_filepath from DB is absolute.
        # If it's relative, it needs to be resolved to an absolute path.
        # Example: audio_filepath = os.path.join(app.config['SHARED_AUDIO_BASE_PATH'], audio_filepath)
        return send_file(audio_filepath, mimetype='audio/mpeg')
    except Exception as e:
        app.logger.error(f"Error sending file for podcast_id '{podcast_id}': {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An error occurred while trying to serve the audio file."}), 500


# --- Main Block ---
if __name__ == '__main__':
    init_db() # Initialize the database and create tables if they don't exist

    # Set a default TDA port if necessary for local testing.
    # This is just for the print statement, actual TDA_SERVICE_URL is used by the endpoint.
    tda_port_for_message = TDA_SERVICE_URL.split(':')[-1].split('/')[0] if "://" in TDA_SERVICE_URL else "5000 (default)"

    print(f"Starting Aethercast API Gateway on http://0.0.0.0:5001")
    print(f"Ensure other Aethercast services are running:")
    print(f" - CPOA (used internally, no direct URL needed from Gateway's perspective for startup)")
    print(f" - TDA at {TDA_SERVICE_URL} (typically on port {tda_port_for_message})")
    print(f" - SCA at {os.getenv('SCA_SERVICE_URL', 'http://localhost:5002/craft_snippet')}")
    print(f" - PSWA at {os.getenv('PSWA_SERVICE_URL', 'http://localhost:5004/weave_script')}")
    print(f" - VFA at {os.getenv('VFA_SERVICE_URL', 'http://localhost:5005/forge_voice')}")
    print(f" - ASF at {os.getenv('ASF_WEBSOCKET_BASE_URL', 'ws://localhost:5006/api/v1/podcasts/stream')} and its notification endpoint")

    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=True)

[end of aethercast/api_gateway/main.py]
