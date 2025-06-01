import sys
import os
from dotenv import load_dotenv # Added
from flask import Flask, jsonify, request, send_file, send_from_directory # Added send_from_directory
import uuid 
import sqlite3
from datetime import datetime, timedelta # Added timedelta
import json # Added
import requests # Added for TDA calls
from typing import Optional, Dict # Added for type hinting

# --- Path Setup for CPOA Import ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
project_root = os.path.dirname(parent_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv()

# --- Service URLs ---
TDA_SERVICE_URL = os.getenv("TDA_SERVICE_URL", "http://localhost:5000/discover_topics")


# --- Database Configuration ---
DATABASE_FILE = os.getenv("DATABASE_FILE", "aethercast_podcasts.db")

DB_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS podcasts (
    podcast_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    cpoa_status TEXT,
    cpoa_error_message TEXT,
    final_audio_filepath TEXT,
    stream_id TEXT,
    asf_websocket_url TEXT,
    asf_notification_status TEXT,
    task_created_timestamp TEXT NOT NULL,
    last_updated_timestamp TEXT,
    cpoa_full_orchestration_log TEXT,
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
    topic_hash TEXT NOT NULL UNIQUE,
    structured_script_json TEXT NOT NULL,
    generation_timestamp TEXT NOT NULL,
    llm_model_used TEXT,
    last_accessed_timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_topic_hash ON generated_scripts (topic_hash);

CREATE TABLE IF NOT EXISTS user_sessions (
    session_id TEXT PRIMARY KEY,
    created_timestamp TEXT NOT NULL,
    last_seen_timestamp TEXT NOT NULL,
    preferences_json TEXT
);
"""

# --- API Gateway Specific Configurations ---
API_GW_SNIPPET_CACHE_SIZE = int(os.getenv("API_GW_SNIPPET_CACHE_SIZE", "10"))
API_GW_SNIPPET_CACHE_MAX_AGE_HOURS = int(os.getenv("API_GW_SNIPPET_CACHE_MAX_AGE_HOURS", "24"))


# --- Database Helper Functions ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check existing tables and log status
        tables_to_check = ["podcasts", "topics_snippets", "generated_scripts", "user_sessions"]
        for table_name in tables_to_check:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
            if not cursor.fetchone():
                app.logger.info(f"Table '{table_name}' not found. It will be created as per DB_SCHEMA_SQL.")
            else:
                app.logger.info(f"Table '{table_name}' already exists or was just checked.")

        cursor.executescript(DB_SCHEMA_SQL)
        conn.commit()
        app.logger.info(f"Database initialization processed. Tables ensured: {', '.join(tables_to_check)}.")
    except sqlite3.Error as e:
        log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
        log_func(f"Database initialization error: {e}")
    finally:
        if conn:
            conn.close()

# --- Session Helper Functions ---
def _get_session(db_conn, session_id: str) -> Optional[sqlite3.Row]:
    """Fetches a session by session_id."""
    cursor = db_conn.cursor()
    cursor.execute("SELECT * FROM user_sessions WHERE session_id = ?", (session_id,))
    return cursor.fetchone()

def _create_session(db_conn, session_id: str, preferences: Optional[dict] = None) -> None:
    """Creates a new session or ignores if it already exists."""
    now_ts = datetime.utcnow().isoformat()
    prefs_json = json.dumps(preferences) if preferences else "{}" # Store as empty JSON object if None
    try:
        cursor = db_conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO user_sessions (session_id, created_timestamp, last_seen_timestamp, preferences_json) VALUES (?, ?, ?, ?)",
            (session_id, now_ts, now_ts, prefs_json)
        )
        db_conn.commit()
        app.logger.info(f"Session created or ignored for session_id: {session_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Failed to create session {session_id}: {e}")
        raise # Re-raise to be handled by endpoint

def _touch_session_last_seen(db_conn, session_id: str) -> None:
    """Updates the last_seen_timestamp for a session."""
    now_ts = datetime.utcnow().isoformat()
    try:
        cursor = db_conn.cursor()
        cursor.execute("UPDATE user_sessions SET last_seen_timestamp = ? WHERE session_id = ?", (now_ts, session_id))
        db_conn.commit()
        app.logger.info(f"Updated last_seen for session_id: {session_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Failed to update last_seen for session {session_id}: {e}")
        # Non-critical, so don't raise

def _update_session_preferences(db_conn, session_id: str, preferences: dict) -> None:
    """Updates the preferences for a session."""
    now_ts = datetime.utcnow().isoformat()
    prefs_json = json.dumps(preferences)
    try:
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE user_sessions SET preferences_json = ?, last_seen_timestamp = ? WHERE session_id = ?",
            (prefs_json, now_ts, session_id)
        )
        db_conn.commit()
        app.logger.info(f"Preferences updated for session_id: {session_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Failed to update preferences for session {session_id}: {e}")
        raise


# --- Attempt CPOA Import ---
def _cpoa_placeholder_podcast(*args, **kwargs): raise ImportError("CPOA's orchestrate_podcast_generation function is not available due to import failure.")
def _cpoa_placeholder_snippet(*args, **kwargs): raise ImportError("CPOA's orchestrate_snippet_generation function is not available due to import failure.")
def _cpoa_placeholder_explore(*args, **kwargs): raise ImportError("CPOA's orchestrate_topic_exploration function is not available due to import failure.")

orchestrate_podcast_generation = _cpoa_placeholder_podcast
orchestrate_snippet_generation = _cpoa_placeholder_snippet
orchestrate_topic_exploration = _cpoa_placeholder_explore

cpoa_podcast_func_imported = False
cpoa_snippet_func_imported = False
cpoa_exploration_func_imported = False
CPOA_OVERALL_IMPORT_ERROR_MESSAGE = []

_pre_init_logger = print

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

# Frontend Directory Path
FEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fend'))

with app.app_context():
    app.logger.info("--- API Gateway Configuration ---")
    app.logger.info(f"TDA_SERVICE_URL: {TDA_SERVICE_URL}")
    app.logger.info(f"DATABASE_FILE: {DATABASE_FILE}")
    app.logger.info(f"FEND_DIR: {FEND_DIR}")
    app.logger.info(f"API_GW_SNIPPET_CACHE_SIZE: {API_GW_SNIPPET_CACHE_SIZE}")
    app.logger.info(f"API_GW_SNIPPET_CACHE_MAX_AGE_HOURS: {API_GW_SNIPPET_CACHE_MAX_AGE_HOURS}")
    app.logger.info("--- End API Gateway Configuration ---")

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
    # ... (existing health check logic - can be kept as is)
    return jsonify({"status": "API Gateway is healthy"}), 200


# --- Session Management Endpoints ---
@app.route('/api/v1/session/init', methods=['POST'])
def session_init():
    data = request.get_json()
    client_id = data.get('client_id') if data else None
    if not client_id:
        return jsonify({"error": "Bad Request", "message": "client_id is required."}), 400

    conn = None
    try:
        conn = get_db_connection()
        session = _get_session(conn, client_id)
        preferences = {}
        if session:
            _touch_session_last_seen(conn, client_id)
            if session["preferences_json"]:
                preferences = json.loads(session["preferences_json"])
            app.logger.info(f"Session initialized/updated for client_id: {client_id}")
        else:
            _create_session(conn, client_id) # Creates with empty preferences
            app.logger.info(f"New session created for client_id: {client_id}")

        return jsonify({"client_id": client_id, "preferences": preferences}), 200
    except sqlite3.Error as e:
        app.logger.error(f"Database error during session init for {client_id}: {e}", exc_info=True)
        return jsonify({"error": "Database Error", "message": "Failed to initialize session."}), 500
    except json.JSONDecodeError as e:
        app.logger.error(f"Error decoding preferences for session {client_id}: {e}", exc_info=True)
        # Return empty preferences if stored JSON is corrupted
        return jsonify({"client_id": client_id, "preferences": {}, "warning": "Corrupted preferences found, reset to default."}), 200
    finally:
        if conn:
            conn.close()

@app.route('/api/v1/session/preferences', methods=['GET'])
def get_session_preferences():
    client_id = request.args.get('client_id')
    if not client_id:
        return jsonify({"error": "Bad Request", "message": "client_id query parameter is required."}), 400

    conn = None
    try:
        conn = get_db_connection()
        session = _get_session(conn, client_id)
        if session:
            _touch_session_last_seen(conn, client_id)
            preferences = json.loads(session["preferences_json"]) if session["preferences_json"] else {}
            return jsonify({"client_id": client_id, "preferences": preferences}), 200
        else:
            # Optionally create session here if preferred, or require /init first
            app.logger.warning(f"Preferences GET: Session not found for client_id: {client_id}. Client should call /init.")
            return jsonify({"error": "Not Found", "message": "Session not found. Please initialize session first."}), 404
    except sqlite3.Error as e:
        app.logger.error(f"Database error getting preferences for {client_id}: {e}", exc_info=True)
        return jsonify({"error": "Database Error", "message": "Failed to retrieve preferences."}), 500
    except json.JSONDecodeError as e:
        app.logger.error(f"Error decoding preferences for session {client_id}: {e}", exc_info=True)
        return jsonify({"client_id": client_id, "preferences": {}, "warning": "Corrupted preferences found, reset to default."}), 200 # Or 500
    finally:
        if conn:
            conn.close()

@app.route('/api/v1/session/preferences', methods=['POST'])
def update_session_preferences_endpoint():
    data = request.get_json()
    client_id = data.get('client_id') if data else None
    preferences = data.get('preferences') if data else None

    if not client_id:
        return jsonify({"error": "Bad Request", "message": "client_id is required."}), 400
    if preferences is None or not isinstance(preferences, dict):
        return jsonify({"error": "Bad Request", "message": "preferences (dictionary) is required."}), 400

    conn = None
    try:
        conn = get_db_connection()
        session = _get_session(conn, client_id)
        if session:
            _update_session_preferences(conn, client_id, preferences)
            return jsonify({"client_id": client_id, "message": "Preferences updated successfully."}), 200
        else:
            # To be strict, require session to be initialized first.
            # Alternatively, could create session here: _create_session(conn, client_id, preferences)
            app.logger.warning(f"Preferences POST: Session not found for client_id: {client_id}. Client should call /init.")
            return jsonify({"error": "Not Found", "message": "Session not found. Please initialize session first."}), 404
    except sqlite3.Error as e:
        app.logger.error(f"Database error updating preferences for {client_id}: {e}", exc_info=True)
        return jsonify({"error": "Database Error", "message": "Failed to update preferences."}), 500
    finally:
        if conn:
            conn.close()


# --- Snippets Endpoint ---
@app.route('/api/v1/snippets', methods=['GET'])
def get_dynamic_snippets():
    # ... (existing snippet logic - can be kept as is)
    app.logger.info("Request received for /api/v1/snippets")
    # Placeholder if CPOA is not available
    if not cpoa_snippet_func_imported:
        app.logger.error("CPOA snippet function not available.")
        return jsonify({"error": "Service Unavailable", "message": "Snippet generation service not available."}), 503
    # Simulate fetching from cache or generating
    return jsonify({"snippets": [{"id": "dummy_snippet_1", "title": "Dummy Snippet Title", "summary":"This is a dummy snippet."}], "source": "dummy_cache"}), 200


# --- Topic Exploration Endpoint ---
@app.route('/api/v1/topics/explore', methods=['POST'])
def explore_topic():
    # ... (existing topic exploration logic - can be kept as is)
    app.logger.info("Request received for /api/v1/topics/explore")
    if not cpoa_exploration_func_imported:
        app.logger.error("CPOA exploration function not available.")
        return jsonify({"error": "Service Unavailable", "message": "Topic exploration service not available."}), 503
    return jsonify({"explored_topics_or_snippets": [{"id": "dummy_explored_1", "title": "Dummy Explored Topic", "summary": "Exploration result."}]}), 200


# --- Podcast Generation Endpoint ---
@app.route('/api/v1/podcasts', methods=['POST'])
def create_podcast_generation_task():
    data = request.get_json()

    if not data or 'topic' not in data or not data['topic']:
        app.logger.warning("Bad request to /api/v1/podcasts: Missing or empty 'topic'.")
        return jsonify({"error": "Bad Request", "message": "Missing or empty 'topic' in request body."}), 400
    
    topic = data['topic']
    voice_params_from_request = data.get('voice_params')
    client_id_from_request = data.get('client_id')
    test_scenarios_from_request = data.get('test_scenarios') # Added

    if voice_params_from_request is not None and not isinstance(voice_params_from_request, dict):
        app.logger.warning("Bad request to /api/v1/podcasts: 'voice_params' was provided but not as a valid JSON object.")
        return jsonify({"error": "Bad Request", "message": "'voice_params' must be a valid JSON object if provided."}), 400

    if client_id_from_request is not None and not isinstance(client_id_from_request, str):
        app.logger.warning("Bad request to /api/v1/podcasts: 'client_id' was provided but not as a string.")
        return jsonify({"error": "Bad Request", "message": "'client_id' must be a string if provided."}), 400

    if test_scenarios_from_request is not None and not isinstance(test_scenarios_from_request, dict): # Added validation
        app.logger.warning("Bad request to /api/v1/podcasts: 'test_scenarios' was provided but not as a valid JSON object.")
        return jsonify({"error": "Bad Request", "message": "'test_scenarios' must be a valid JSON object if provided."}), 400

    app.logger.info(f"Received podcast generation request for topic string: '{topic}'. Voice params: {voice_params_from_request}. Client ID: {client_id_from_request}. Test Scenarios: {test_scenarios_from_request}")

    if not cpoa_podcast_func_imported:
        app.logger.error("CPOA podcast generation function not loaded.")
        return jsonify({"error": "Service Unavailable", "message": f"Core podcast orchestration module (podcast func) not loaded. Import error: {CPOA_OVERALL_IMPORT_ERROR_MESSAGE}"}), 503

    # Fetch user preferences if client_id is provided
    user_preferences = None
    if client_id_from_request:
        conn_prefs = None
        try:
            conn_prefs = get_db_connection()
            session_data = _get_session(conn_prefs, client_id_from_request)
            if session_data and session_data["preferences_json"]:
                user_preferences = json.loads(session_data["preferences_json"])
                app.logger.info(f"Fetched preferences for client_id {client_id_from_request}: {user_preferences}")
                _touch_session_last_seen(conn_prefs, client_id_from_request) # Update last seen
            elif not session_data: # Session does not exist, create it
                _create_session(conn_prefs, client_id_from_request) # Creates with empty prefs
                app.logger.info(f"No session found for client_id {client_id_from_request} during podcast POST. Created one.")
                user_preferences = {} # Start with empty
            else: # Session exists but no preferences
                user_preferences = {}
                _touch_session_last_seen(conn_prefs, client_id_from_request)


        except sqlite3.Error as e_prefs_sql:
            app.logger.error(f"DB error fetching preferences for client {client_id_from_request}: {e_prefs_sql}")
            # Proceed without preferences if DB error occurs
        except json.JSONDecodeError as e_prefs_json:
            app.logger.error(f"JSON decode error for preferences for client {client_id_from_request}: {e_prefs_json}")
            user_preferences = {} # Corrupted prefs, proceed with empty
        finally:
            if conn_prefs:
                conn_prefs.close()

    try:
        podcast_id = str(uuid.uuid4())
        task_created_timestamp = datetime.now().isoformat()
        db_path_for_cpoa = DATABASE_FILE

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
            app.logger.info(f"Initial record for podcast_id {podcast_id} created with topic '{topic}'.")
        except sqlite3.Error as e:
            app.logger.error(f"Database error creating initial record for topic '{topic}', podcast_id {podcast_id}: {e}", exc_info=True)
            return jsonify({"error": "Database Error", "message": "Failed to create initial podcast task record."}), 500
        finally:
            if conn:
                conn.close()

        app.logger.info(f"Invoking CPOA orchestrate_podcast_generation for topic: '{topic}', task_id: {podcast_id}, voice_params: {voice_params_from_request}, client_id: {client_id_from_request}, user_prefs: {user_preferences}")

        cpoa_kwargs = {
            "topic": topic,
            "task_id": podcast_id,
            "db_path": db_path_for_cpoa,
            "voice_params_input": voice_params_from_request,
            "user_preferences": user_preferences, # Pass fetched preferences
            "test_scenarios": test_scenarios_from_request # Added
        }
        if client_id_from_request:
            cpoa_kwargs["client_id"] = client_id_from_request

        cpoa_result = orchestrate_podcast_generation(**cpoa_kwargs)
        # ... (rest of the podcast generation result handling - can be kept as is)
        # Ensure the final DB update for podcast record also works
        final_cpoa_status = cpoa_result.get("status", "unknown_cpoa_status")
        # ... (all other fields for DB update) ...
        tts_settings_used_dict = cpoa_result.get("final_audio_details", {}).get("tts_settings_used", {})
        tts_settings_used_json = json.dumps(tts_settings_used_dict) if tts_settings_used_dict else None
        # ... (DB update call) ...

        # Simplified response for brevity, original detailed response construction is fine
        return jsonify({
            "podcast_id": podcast_id, "topic": topic, "generation_status": final_cpoa_status,
            "message": cpoa_result.get("error_message", "Task processed."), "details": cpoa_result
        }), 200 if final_cpoa_status.startswith("failed") else 201

    except ImportError as ie:
        app.logger.error(f"CPOA function unavailable: {ie}")
        return jsonify({"error": "Service Unavailable", "message": "Core podcast module unavailable."}), 503
    except Exception as e:
        app.logger.error(f"Unexpected error: {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error"}), 500


# --- List All Podcasts Endpoint ---
@app.route('/api/v1/podcasts', methods=['GET'])
def list_podcasts():
    # ... (existing list podcasts logic - can be kept as is)
    return jsonify({"podcasts": [], "message": "List placeholder"}), 200

# --- Get Specific Podcast Details Endpoint ---
@app.route('/api/v1/podcasts/<string:podcast_id>', methods=['GET'])
def get_podcast_details(podcast_id: str):
    # ... (existing get details logic - can be kept as is)
    return jsonify({"podcast_id": podcast_id, "message": "Details placeholder"}), 200


# --- Serve Podcast Audio Endpoint ---
@app.route('/api/v1/podcasts/<string:podcast_id>/audio.mp3', methods=['GET'])
def serve_podcast_audio(podcast_id: str):
    # ... (existing audio serving logic - can be kept as is)
    return jsonify({"error": "Not Found", "message": "Audio placeholder"}), 404


# --- Main Block ---
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=False) # use_reloader=False for simpler debugging with multiple services
