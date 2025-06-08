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
DATABASE_FILE = os.getenv("SHARED_DATABASE_PATH", "/app/database/aethercast_podcasts.db")

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
    image_url TEXT,
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
def _cpoa_placeholder_search(*args, **kwargs): raise ImportError("CPOA's orchestrate_search_results_generation function is not available due to import failure.")
def _cpoa_placeholder_landing_snippets(*args, **kwargs): raise ImportError("CPOA's orchestrate_landing_page_snippets function is not available due to import failure.")
def _cpoa_placeholder_categories(*args, **kwargs): raise ImportError("CPOA's get_popular_categories function is not available due to import failure.")


orchestrate_podcast_generation = _cpoa_placeholder_podcast
orchestrate_snippet_generation = _cpoa_placeholder_snippet
orchestrate_topic_exploration = _cpoa_placeholder_explore
orchestrate_search_results_generation = _cpoa_placeholder_search
orchestrate_landing_page_snippets = _cpoa_placeholder_landing_snippets
get_popular_categories = _cpoa_placeholder_categories # Added

cpoa_podcast_func_imported = False
cpoa_snippet_func_imported = False
cpoa_exploration_func_imported = False
cpoa_search_func_imported = False
cpoa_landing_snippets_func_imported = False
cpoa_categories_func_imported = False # Added
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

try:
    from aethercast.cpoa.main import orchestrate_search_results_generation as osrg_real
    orchestrate_search_results_generation = osrg_real
    cpoa_search_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_search_results_generation.")
except ImportError as e:
    CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_search_results_generation: {e}")

try:
    from aethercast.cpoa.main import orchestrate_landing_page_snippets as olps_real
    orchestrate_landing_page_snippets = olps_real
    cpoa_landing_snippets_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_landing_page_snippets.")
except ImportError as e:
    CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_landing_page_snippets: {e}")

try:
    from aethercast.cpoa.main import get_popular_categories as gpc_real
    get_popular_categories = gpc_real
    cpoa_categories_func_imported = True
    _pre_init_logger("Successfully imported CPOA.get_popular_categories.")
except ImportError as e:
    CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"get_popular_categories: {e}")


if CPOA_OVERALL_IMPORT_ERROR_MESSAGE:
    _pre_init_logger(f"CPOA Module Import Errors: {'; '.join(CPOA_OVERALL_IMPORT_ERROR_MESSAGE)}")


# --- Flask App Initialization ---
app = Flask(__name__)

# Frontend Directory Path
FEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fend'))

with app.app_context():
    app.logger.info("--- API Gateway Configuration ---")
    app.logger.info(f"TDA_SERVICE_URL: {TDA_SERVICE_URL}")
    app.logger.info(f"SHARED_DATABASE_PATH: {DATABASE_FILE}")
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
    db_status = "Database connection successful."
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1 FROM podcasts LIMIT 1;") # Simple query
        conn.close()
    except Exception as e:
        db_status = f"Database connection error: {e}"
        app.logger.error(f"Health check DB error: {e}", exc_info=True)

    # Consolidate CPOA import statuses
    cpoa_import_summary = []
    if not cpoa_podcast_func_imported: cpoa_import_summary.append("podcast_generation")
    if not cpoa_snippet_func_imported: cpoa_import_summary.append("snippet_generation (legacy)")
    if not cpoa_exploration_func_imported: cpoa_import_summary.append("topic_exploration")
    if not cpoa_search_func_imported: cpoa_import_summary.append("search_generation")
    if not cpoa_landing_snippets_func_imported: cpoa_import_summary.append("landing_snippets_generation")
    if not cpoa_categories_func_imported: cpoa_import_summary.append("categories_generation") # Added

    cpoa_overall_status = "fully operational"
    if cpoa_import_summary:
        cpoa_overall_status = f"partially operational (missing: {', '.join(cpoa_import_summary)})"
        # If any CPOA function fails to import, it's a potential issue.
        # Consider if all 5 are critical for "fully operational" status.
        if len(cpoa_import_summary) > 0:
            cpoa_overall_status = f"CPOA module has import issues (missing: {', '.join(cpoa_import_summary)})"

    if CPOA_OVERALL_IMPORT_ERROR_MESSAGE and not cpoa_import_summary :
        cpoa_overall_status = "inconsistent import state (errors logged but functions flagged as imported)"


    health_data = {
        "status": "API Gateway is healthy" if db_status.startswith("Database connection successful.") and not cpoa_import_summary else "API Gateway has issues",
        "cpoa_module_status": cpoa_overall_status,
        "cpoa_podcast_function_status": "successfully imported" if cpoa_podcast_func_imported else f"failed to import (see CPOA_OVERALL_IMPORT_ERROR_MESSAGE in logs)",
        "cpoa_snippet_function_status": "successfully imported" if cpoa_snippet_func_imported else f"failed to import (see CPOA_OVERALL_IMPORT_ERROR_MESSAGE in logs)", # Kept for now
        "cpoa_exploration_function_status": "successfully imported" if cpoa_exploration_func_imported else f"failed to import (see CPOA_OVERALL_IMPORT_ERROR_MESSAGE in logs)",
        "cpoa_search_function_status": "successfully imported" if cpoa_search_func_imported else f"failed to import (see CPOA_OVERALL_IMPORT_ERROR_MESSAGE in logs)",
        "cpoa_landing_snippets_function_status": "successfully imported" if cpoa_landing_snippets_func_imported else f"failed to import (see CPOA_OVERALL_IMPORT_ERROR_MESSAGE in logs)",
        "cpoa_categories_function_status": "successfully imported" if cpoa_categories_func_imported else f"failed to import (see CPOA_OVERALL_IMPORT_ERROR_MESSAGE in logs)", # Added
        "database_status": db_status,
        "cpoa_detailed_import_errors": CPOA_OVERALL_IMPORT_ERROR_MESSAGE if CPOA_OVERALL_IMPORT_ERROR_MESSAGE else "None"
    }

    status_code = 200
    if not db_status.startswith("Database connection successful.") or not IMPORTS_SUCCESSFUL_ALL_CPOA_FUNCS():
        status_code = 503

    return jsonify(health_data), status_code

# Helper for health check to see if all CPOA functions are up
def IMPORTS_SUCCESSFUL_ALL_CPOA_FUNCS():
    return cpoa_podcast_func_imported and \
           cpoa_snippet_func_imported and \
           cpoa_exploration_func_imported and \
           cpoa_search_func_imported and \
           cpoa_landing_snippets_func_imported and \
           cpoa_categories_func_imported


# --- Session Management Endpoints ---
@app.route('/api/v1/session/init', methods=['POST'])
def session_init():
    try:
        data = request.get_json()
        if not data:
            app.logger.warning("Bad request to /api/v1/session/init: Missing or empty JSON payload.")
            return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json:
        app.logger.warning(f"Bad request to /api/v1/session/init: Malformed JSON. Error: {e_json}", exc_info=True)
        return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json)}), 400

    client_id = data.get('client_id')
    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        return jsonify({
            "error_code": "API_GW_SESSION_CLIENT_ID_INVALID",
            "message": "Client ID is required and must be a non-empty string for session initialization.",
            "details": "'client_id' must be a non-empty string."
        }), 400

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
        return jsonify({
            "error_code": "API_GW_SESSION_DB_ERROR_INIT",
            "message": "Could not initialize session due to a database issue.",
            "details": f"Failed to initialize session: {str(e)}" # Include original error string
        }), 500
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
    # Added .strip() to ensure non-whitespace only string and type check
    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        app.logger.warning(f"Bad request to /api/v1/session/preferences (GET): 'client_id' must be a non-empty string. Received: {client_id}")
        return jsonify({
            "error_code": "API_GW_SESSION_CLIENT_ID_INVALID",
            "message": "Client ID query parameter is required and must be a non-empty string to get preferences.",
            "details": "'client_id' query parameter must be a non-empty string."
        }), 400

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
            return jsonify({
                "error_code": "API_GW_SESSION_NOT_FOUND",
                "message": "User session not found. Please initialize session first.",
                "details": "Session not found. Please initialize session first."
            }), 404
    except sqlite3.Error as e:
        app.logger.error(f"Database error getting preferences for {client_id}: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_SESSION_DB_ERROR_GET_PREFS",
            "message": "Could not retrieve session preferences due to a database issue.",
            "details": f"Failed to retrieve preferences: {str(e)}" # Include original error string
        }), 500
    except json.JSONDecodeError as e:
        app.logger.error(f"Error decoding preferences for session {client_id}: {e}", exc_info=True)
        return jsonify({"client_id": client_id, "preferences": {}, "warning": "Corrupted preferences found, reset to default."}), 200 # Or 500
    finally:
        if conn:
            conn.close()

@app.route('/api/v1/session/preferences', methods=['POST'])
def update_session_preferences_endpoint():
    try:
        data = request.get_json()
        if not data:
            app.logger.warning("Bad request to /api/v1/session/preferences (POST): Missing or empty JSON payload.")
            return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json:
        app.logger.warning(f"Bad request to /api/v1/session/preferences (POST): Malformed JSON. Error: {e_json}", exc_info=True)
        return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json)}), 400

    client_id = data.get('client_id')
    preferences = data.get('preferences')

    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        return jsonify({
            "error_code": "API_GW_SESSION_CLIENT_ID_INVALID",
            "message": "Client ID is required and must be a non-empty string to update preferences.",
            "details": "'client_id' must be a non-empty string."
        }), 400
    if preferences is None or not isinstance(preferences, dict): # preferences can be an empty dict {}
        return jsonify({
            "error_code": "API_GW_SESSION_INVALID_PREFERENCES_PAYLOAD",
            "message": "Preferences payload is required and must be a dictionary.",
            "details": "'preferences' (dictionary) is required."
        }), 400

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
            return jsonify({
                "error_code": "API_GW_SESSION_NOT_FOUND",
                "message": "User session not found. Please initialize session first.",
                "details": "Session not found. Please initialize session first."
            }), 404
    except sqlite3.Error as e:
        app.logger.error(f"Database error updating preferences for {client_id}: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_SESSION_DB_ERROR_UPDATE_PREFS",
            "message": "Could not update session preferences due to a database issue.",
            "details": f"Failed to update preferences: {str(e)}" # Include original error string
        }), 500
    finally:
        if conn:
            conn.close()


# --- Snippets Endpoint ---
@app.route('/api/v1/snippets', methods=['GET'])
def get_dynamic_snippets():
    app.logger.info("Request received for /api/v1/snippets (dynamic generation using orchestrate_landing_page_snippets)")
    if not cpoa_landing_snippets_func_imported:
        app.logger.error("CPOA landing page snippets function not available.")
        return jsonify({
            "error_code": "API_GW_CPOA_SNIPPET_SERVICE_UNAVAILABLE",
            "message": "Snippet generation service is currently unavailable.",
            "details": "CPOA landing page snippets function not available."
        }), 503

    try:
        limit_str = request.args.get('limit', default="6") # Get as string first
        try:
            limit = int(limit_str)
            if not (1 <= limit <= 20):
                app.logger.warning(f"Invalid limit value '{limit_str}' for /snippets. Must be 1-20. Defaulting to 6.")
                # For GET requests, defaulting is often preferred over erroring for optional, simple params.
                # If strictness is required by product decision, uncomment the return below:
                # return jsonify({"error_code": "API_GW_INVALID_LIMIT_RANGE", "message": "Validation failed: 'limit' must be an integer between 1 and 20."}), 400
                limit = 6 # Defaulting behavior
        except ValueError:
            app.logger.warning(f"Invalid limit type '{limit_str}' for /snippets. Must be integer. Defaulting to 6.")
            # If strictness is required, uncomment the return below:
            # return jsonify({"error_code": "API_GW_INVALID_LIMIT_TYPE", "message": "Validation failed: 'limit' must be a valid integer."}), 400
            limit = 6 # Defaulting behavior

        # User preferences are not yet passed for general landing page snippets.
        # client_id = request.args.get('client_id')
        # user_preferences = None
        # if client_id: ... fetch preferences ... (can be added later)

        app.logger.info(f"Calling CPOA orchestrate_landing_page_snippets with limit: {limit}")
        cpoa_response = orchestrate_landing_page_snippets(limit=limit) # user_preferences can be added later

        if "error" in cpoa_response:
            app.logger.error(f"CPOA returned an error for landing page snippets: {cpoa_response}")
            error_type = cpoa_response.get("error", "UNKNOWN_CPOA_ERROR")
            # Determine status code based on cpoa_response["error"]
            status_code = 500 # Default to general server error
            if "TDA_" in error_type or "SCA_" in error_type or "IGA_" in error_type or "CPOA_CONFIG_ERROR" in error_type:
                status_code = 503 # Service Unavailable for downstream or config issues

            return jsonify({
                "error_code": f"API_GW_CPOA_SNIPPET_ERROR_{error_type.replace('.', '_').upper()}", # Sanitize error_type for code
                "message": "Failed to generate landing page snippets.",
                "details": cpoa_response.get("details", "An internal error occurred during snippet orchestration.")
            }), status_code

        # Expected successful response from CPOA: {"snippets": [...], "source": "generation"}
        # The API Gateway now directly returns what CPOA provides.
        return jsonify(cpoa_response), 200

    except ImportError:
        app.logger.critical("CPOA landing snippets function became unavailable after initial check.", exc_info=True)
        return jsonify({
            "error_code": "API_GW_CPOA_SNIPPET_MODULE_UNAVAILABLE",
            "message": "Snippet generation module is critically unavailable.",
            "details": "CPOA landing snippets function became unavailable after initial check."
        }), 503
    except Exception as e:
        app.logger.error(f"Unexpected error in get_dynamic_snippets: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_SNIPPETS_UNEXPECTED_ERROR",
            "message": "An unexpected error occurred while fetching snippets.",
            "details": str(e)
        }), 500

# --- Categories Endpoint ---
@app.route('/api/v1/categories', methods=['GET'])
def get_categories_endpoint():
    app.logger.info("Request received for /api/v1/categories")
    if not cpoa_categories_func_imported:
        app.logger.error("CPOA get_popular_categories function not available.")
        return jsonify({
            "error_code": "API_GW_CPOA_CATEGORY_SERVICE_UNAVAILABLE",
            "message": "Category service is currently unavailable.",
            "details": "CPOA get_popular_categories function not available."
        }), 503

    try:
        app.logger.info("Calling CPOA get_popular_categories...")
        cpoa_response = get_popular_categories() # This function in CPOA returns {"categories": [...]}

        if "error" in cpoa_response: # Should not happen if CPOA's func is just returning a list
            app.logger.error(f"CPOA returned an error for categories: {cpoa_response}")
            error_code_from_cpoa = cpoa_response.get("error", "CPOA_ERROR")
            return jsonify({
                "error_code": f"API_GW_CPOA_CATEGORY_ERROR_{error_code_from_cpoa.replace('.', '_').upper()}",
                "message": "Failed to get categories due to an internal CPOA error.",
                "details": cpoa_response.get("details", "An internal error occurred during category retrieval.")
            }), 500

        # Expected CPOA response: {"categories": ["Tech", "Science", ...]}
        return jsonify(cpoa_response), 200

    except ImportError: # Safeguard, though caught by flag
        app.logger.critical("CPOA get_popular_categories function became unavailable after initial check.", exc_info=True)
        return jsonify({
            "error_code": "API_GW_CPOA_CATEGORY_MODULE_UNAVAILABLE",
            "message": "Category module component is critically unavailable.",
            "details": "CPOA get_popular_categories function became unavailable after initial check."
        }), 503
    except Exception as e:
        app.logger.error(f"Unexpected error in /api/v1/categories endpoint: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_CATEGORIES_UNEXPECTED_ERROR",
            "message": "An unexpected error occurred while fetching categories.",
            "details": str(e)
        }), 500

# --- Topic Exploration Endpoint ---
@app.route('/api/v1/topics/explore', methods=['POST'])
def explore_topic():
    app.logger.info("Request received for /api/v1/topics/explore")

    if not cpoa_exploration_func_imported:
        app.logger.error("CPOA topic exploration function (cpoa_exploration_func_imported) is not available.")
        return jsonify({
            "error_code": "API_GW_CPOA_EXPLORE_SERVICE_UNAVAILABLE",
            "message": "Topic exploration service is currently unavailable.",
            "details": "CPOA topic exploration function (orchestrate_topic_exploration) is not available."
        }), 503

    try:
        data = request.get_json()
        if not data:
            app.logger.warning("Bad request to /api/v1/topics/explore: Missing or empty JSON payload.")
            return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json:
        app.logger.warning(f"Bad request to /api/v1/topics/explore: Malformed JSON. Error: {e_json}", exc_info=True)
        return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json)}), 400

    current_topic_id = data.get("current_topic_id")
    keywords = data.get("keywords") # Expected to be a list of strings
    depth_mode = data.get("depth_mode", "deeper") # Default to "deeper"
    client_id = data.get("client_id")

    # Validate input: current_topic_id or keywords must be provided
    if not current_topic_id and not keywords:
        app.logger.warning("Bad request to /api/v1/topics/explore: Neither 'current_topic_id' nor 'keywords' provided.")
        return jsonify({
            "error_code": "API_GW_EXPLORE_INPUT_REQUIRED",
            "message": "Either 'current_topic_id' or 'keywords' must be provided for topic exploration.",
            "details": "Missing 'current_topic_id' and 'keywords'. One or both are required."
        }), 400

    if keywords is not None: # If keywords key is present
        if not isinstance(keywords, list):
            app.logger.warning(f"Bad request to /api/v1/topics/explore: 'keywords' provided but not as a list. Received: {keywords}")
            return jsonify({
                "error_code": "API_GW_EXPLORE_INVALID_KEYWORDS_TYPE",
                "message": "'keywords' must be a list of strings.",
                "details": f"Invalid type for 'keywords': expected list, got {type(keywords).__name__}."
            }), 400
        for i, kw in enumerate(keywords): # Check each item in the list
            if not isinstance(kw, str) or not kw.strip(): # Each keyword must be a non-empty string
                app.logger.warning(f"Bad request to /api/v1/topics/explore: keyword at index {i} is not a non-empty string. Received: '{kw}'")
                return jsonify({
                    "error_code": "API_GW_EXPLORE_INVALID_KEYWORD_ITEM",
                    "message": "All items in 'keywords' list must be non-empty strings.",
                    "details": f"Invalid keyword at index {i}."
                }), 400

    # Validate other optional fields if present
    if current_topic_id is not None and (not isinstance(current_topic_id, str) or not current_topic_id.strip()):
        app.logger.warning(f"Bad request to /api/v1/topics/explore: 'current_topic_id' must be a non-empty string if provided. Received: '{current_topic_id}'")
        return jsonify({"error_code": "API_GW_EXPLORE_INVALID_TOPIC_ID", "message": "'current_topic_id' must be a non-empty string if provided."}), 400

    if depth_mode is not None and (not isinstance(depth_mode, str) or not depth_mode.strip()):
        app.logger.warning(f"Bad request to /api/v1/topics/explore: 'depth_mode' must be a non-empty string if provided. Received: '{depth_mode}'")
        return jsonify({"error_code": "API_GW_EXPLORE_INVALID_DEPTH_MODE", "message": "'depth_mode' must be a non-empty string if provided."}), 400

    if client_id is not None and (not isinstance(client_id, str) or not client_id.strip()): # Check if client_id is non-empty string
        app.logger.warning(f"Bad request to /api/v1/topics/explore: 'client_id' must be a non-empty string if provided. Received: '{client_id}'")
        return jsonify({"error_code": "API_GW_CLIENT_ID_INVALID", "message": "If 'client_id' is provided, it must be a non-empty string."}), 400

    user_preferences = None
    if client_id:
        conn_prefs = None
        try:
            conn_prefs = get_db_connection()
            session_data = _get_session(conn_prefs, client_id)
            if session_data and session_data["preferences_json"]:
                user_preferences = json.loads(session_data["preferences_json"])
                app.logger.info(f"Fetched preferences for client_id {client_id} for topic exploration: {user_preferences}")
                _touch_session_last_seen(conn_prefs, client_id)
            elif not session_data: # Session does not exist, create it with empty preferences
                _create_session(conn_prefs, client_id) # Will create with "{}"
                app.logger.info(f"No session found for client_id {client_id} during topic exploration. Created one.")
                user_preferences = {} # Initialize as empty dict
            else: # Session exists but no preferences JSON (or it's null)
                user_preferences = {}
                _touch_session_last_seen(conn_prefs, client_id)

        except sqlite3.Error as e_prefs_sql:
            app.logger.error(f"DB error fetching/creating preferences for client {client_id} during topic exploration: {e_prefs_sql}")
            # Proceed without preferences if DB error occurs, CPOA can handle None
        except json.JSONDecodeError as e_prefs_json:
            app.logger.error(f"JSON decode error for preferences for client {client_id} during topic exploration: {e_prefs_json}")
            user_preferences = {} # Corrupted prefs, treat as empty
        finally:
            if conn_prefs:
                conn_prefs.close()

    try:
        app.logger.info(f"Calling CPOA orchestrate_topic_exploration with topic_id: '{current_topic_id}', keywords: {keywords}, mode: '{depth_mode}', user_prefs: {user_preferences}")
        # CPOA function expects: current_topic_id, keywords, depth_mode, user_preferences
        cpoa_response = orchestrate_topic_exploration(
            current_topic_id=current_topic_id,
            keywords=keywords,
            depth_mode=depth_mode,
            user_preferences=user_preferences
        )

        # CPOA's orchestrate_topic_exploration is expected to return a list of snippet objects
        # or an error structure (though the spec implies direct list or raises exception handled by CPOA)
        # For robustness, check if it's a list. If CPOA returns an error dict, it should be handled here.
        if isinstance(cpoa_response, dict) and "error" in cpoa_response: # Check if CPOA itself returned an error dict
            app.logger.error(f"CPOA orchestrate_topic_exploration returned an error: {cpoa_response}")
            # Try to map CPOA error to a client-friendly message and appropriate status code
            error_details = cpoa_response.get("details", "Internal error during topic exploration.")
            error_code_from_cpoa = cpoa_response.get("error", "CPOA_EXPLORATION_ERROR")
            status_code = 500 # Default internal server error
            if "TDA_" in error_code_from_cpoa or "SCA_" in error_code_from_cpoa or "CPOA_CONFIG_ERROR" in error_code_from_cpoa:
                 status_code = 503 # Service unavailable for downstream issues

            return jsonify({
                "error_code": f"API_GW_CPOA_EXPLORE_ERROR_{error_code_from_cpoa.replace('.', '_').upper()}",
                "message": "Topic exploration failed.",
                "details": error_details
            }), status_code

        # If CPOA returns a list (expected success case)
        if isinstance(cpoa_response, list):
            app.logger.info(f"CPOA orchestrate_topic_exploration returned {len(cpoa_response)} explored topics/snippets.")
            return jsonify({"explored_topics": cpoa_response}), 200
        else:
            # Should not happen if CPOA adheres to its contract (list of dicts or raises exception)
            app.logger.error(f"CPOA orchestrate_topic_exploration returned an unexpected response type: {type(cpoa_response)}. Response: {cpoa_response}")
            return jsonify({
                "error_code": "API_GW_CPOA_EXPLORE_UNEXPECTED_RESPONSE",
                "message": "Topic exploration service returned an unexpected response.",
                "details": "The format of the response from the exploration service was not as expected."
            }), 500

    except ImportError: # Safeguard if the function became unavailable after the initial check
        app.logger.critical("CPOA orchestrate_topic_exploration function became unavailable after initial check.", exc_info=True)
        return jsonify({
            "error_code": "API_GW_CPOA_EXPLORE_MODULE_UNAVAILABLE_RUNTIME",
            "message": "Topic exploration module component is critically unavailable (runtime).",
            "details": "CPOA orchestrate_topic_exploration function became unavailable after initial check."
        }), 503
    except ValueError as ve: # Catch specific ValueErrors, e.g. from CPOA if it raises one for bad inputs not caught here
        app.logger.warning(f"ValueError during topic exploration: {ve}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_EXPLORE_INVALID_INPUT_OR_STATE",
            "message": "Invalid input or internal state issue during topic exploration.",
            "details": str(ve)
        }), 400 # Or 500 if it's more of an internal CPOA state issue from the ValueError
    except Exception as e:
        app.logger.error(f"Unexpected error in /api/v1/topics/explore endpoint: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_EXPLORE_UNEXPECTED_ERROR",
            "message": "An unexpected error occurred during topic exploration.",
            "details": str(e)
        }), 500

# --- Search Endpoint ---
@app.route('/api/v1/search/podcasts', methods=['POST'])
def search_podcasts_endpoint():
    app.logger.info("Request received for /api/v1/search/podcasts")
    if not cpoa_search_func_imported:
        app.logger.error("CPOA search function not available.")
        return jsonify({
            "error_code": "API_GW_CPOA_SEARCH_SERVICE_UNAVAILABLE",
            "message": "Search orchestration service is currently unavailable.",
            "details": "CPOA search function not available."
        }), 503

    try:
        data = request.get_json()
        if not data:
            app.logger.warning("Bad request to /api/v1/search/podcasts: Missing or empty JSON payload.")
            return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json:
        app.logger.warning(f"Bad request to /api/v1/search/podcasts: Malformed JSON. Error: {e_json}", exc_info=True)
        return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json)}), 400

    query = data.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        app.logger.warning(f"Bad request to /api/v1/search/podcasts: 'query' must be a non-empty string. Received: {query}")
        return jsonify({
            "error_code": "API_GW_SEARCH_QUERY_INVALID",
            "message": "A non-empty search query string is required.",
            "details": "'query' must be a non-empty string."
        }), 400

    client_id = data.get("client_id")
    if client_id is not None and (not isinstance(client_id, str) or not client_id.strip()):
        app.logger.warning(f"Bad request to /api/v1/search/podcasts: 'client_id' must be a non-empty string if provided. Received: {client_id}")
        return jsonify({
            "error_code": "API_GW_CLIENT_ID_INVALID",
            "message": "If 'client_id' is provided, it must be a non-empty string.",
        }), 400
    user_preferences = None

    if client_id:
        conn_prefs = None
        try:
            conn_prefs = get_db_connection()
            session_data = _get_session(conn_prefs, client_id)
            if session_data and session_data["preferences_json"]:
                user_preferences = json.loads(session_data["preferences_json"])
                app.logger.info(f"Fetched preferences for client_id {client_id} for search: {user_preferences}")
                _touch_session_last_seen(conn_prefs, client_id)
            elif not session_data:
                _create_session(conn_prefs, client_id)
                app.logger.info(f"No session found for client_id {client_id} during search. Created one.")
            else: # Session exists but no preferences
                 _touch_session_last_seen(conn_prefs, client_id)
        except sqlite3.Error as e_prefs_sql:
            app.logger.error(f"DB error fetching preferences for client {client_id} during search: {e_prefs_sql}")
        except json.JSONDecodeError as e_prefs_json:
            app.logger.error(f"JSON decode error for preferences for client {client_id} during search: {e_prefs_json}")
        finally:
            if conn_prefs:
                conn_prefs.close()

    try:
        app.logger.info(f"Calling CPOA orchestrate_search_results_generation with query: '{query}'")
        cpoa_search_response = orchestrate_search_results_generation(query=query, user_preferences=user_preferences)

        if "error" in cpoa_search_response: # CPOA still uses "error" for its own error structure
            app.logger.error(f"CPOA returned an error during search: {cpoa_search_response}")
            error_code_from_cpoa = cpoa_search_response.get("error", "UNKNOWN_CPOA_SEARCH_ERROR")
            status_code = 500 # Default to general server error
            if "TDA_" in error_code_from_cpoa or "SCA_" in error_code_from_cpoa or "CPOA_CONFIG_ERROR" in error_code_from_cpoa:
                status_code = 503 # Service Unavailable for downstream or config issues

            return jsonify({
                "error_code": f"API_GW_CPOA_SEARCH_ERROR_{error_code_from_cpoa.replace('.', '_').upper()}",
                "message": "Search processing failed internally.", # User-friendly message
                "details": cpoa_search_response.get("details", "An internal error occurred during search orchestration.")
            }), status_code

        # Expecting {"search_results": [...]} from CPOA on success
        return jsonify(cpoa_search_response), 200

    except ImportError:
        app.logger.critical("CPOA search function became unavailable after initial check.", exc_info=True)
        return jsonify({
            "error_code": "API_GW_CPOA_SEARCH_MODULE_UNAVAILABLE",
            "message": "Search module component is critically unavailable.",
            "details": "CPOA search function became unavailable after initial check."
        }), 503
    except Exception as e:
        app.logger.error(f"Unexpected error in search_podcasts_endpoint: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_SEARCH_UNEXPECTED_ERROR",
            "message": "An unexpected error occurred during search.",
            "details": str(e)
        }), 500

# --- Podcast Generation Endpoint ---
@app.route('/api/v1/podcasts', methods=['POST'])
def create_podcast_generation_task():
    try:
        data = request.get_json()
        if not data:
            app.logger.warning("Bad request to /api/v1/podcasts (POST): Missing or empty JSON payload.")
            return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json:
        app.logger.warning(f"Bad request to /api/v1/podcasts (POST): Malformed JSON. Error: {e_json}", exc_info=True)
        return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON payload.", "details": str(e_json)}), 400

    topic = data.get('topic')
    if not topic or not isinstance(topic, str) or not topic.strip():
        app.logger.warning(f"Bad request to /api/v1/podcasts: 'topic' must be a non-empty string. Received: {topic}")
        return jsonify({
            "error_code": "API_GW_PODCAST_TOPIC_INVALID",
            "message": "A non-empty topic string is required to generate a podcast.",
            "details": "'topic' must be a non-empty string."
        }), 400
    
    voice_params_from_request = data.get('voice_params')
    if voice_params_from_request is not None and not isinstance(voice_params_from_request, dict):
        app.logger.warning(f"Bad request to /api/v1/podcasts: 'voice_params' must be an object if provided. Received: {voice_params_from_request}")
        return jsonify({
            "error_code": "API_GW_PODCAST_INVALID_VOICE_PARAMS_TYPE",
            "message": "Provided voice parameters are invalid.",
            "details": "'voice_params' must be a valid JSON object if provided."
        }), 400

    client_id_from_request = data.get('client_id')
    if client_id_from_request is not None and (not isinstance(client_id_from_request, str) or not client_id_from_request.strip()):
        app.logger.warning(f"Bad request to /api/v1/podcasts: 'client_id' must be a non-empty string if provided. Received: {client_id_from_request}")
        return jsonify({
            "error_code": "API_GW_PODCAST_INVALID_CLIENT_ID",
            "message": "Provided client ID is invalid.",
            "details": "'client_id' must be a non-empty string if provided."
        }), 400

    test_scenarios_from_request = data.get('test_scenarios')
    if test_scenarios_from_request is not None and not isinstance(test_scenarios_from_request, dict):
        app.logger.warning(f"Bad request to /api/v1/podcasts: 'test_scenarios' must be an object if provided. Received: {test_scenarios_from_request}")
        return jsonify({
            "error_code": "API_GW_PODCAST_INVALID_TEST_SCENARIOS_TYPE",
            "message": "Provided test_scenarios are invalid.",
            "details": "'test_scenarios' must be a valid JSON object if provided."
        }), 400

    app.logger.info(f"Received podcast generation request for topic string: '{topic}'. Voice params: {voice_params_from_request}. Client ID: {client_id_from_request}. Test Scenarios: {test_scenarios_from_request}")

    if not cpoa_podcast_func_imported:
        app.logger.error("CPOA podcast generation function not loaded.")
        return jsonify({
            "error_code": "API_GW_CPOA_PODCAST_SERVICE_UNAVAILABLE",
            "message": "Core podcast orchestration module is currently unavailable.",
            "details": f"Core podcast orchestration module (podcast func) not loaded. Import error: {CPOA_OVERALL_IMPORT_ERROR_MESSAGE}"
        }), 503

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
            return jsonify({
                "error_code": "API_GW_PODCAST_DB_ERROR_CREATE_TASK",
                "message": "Failed to create initial podcast task record due to a database issue.",
                "details": f"Failed to create initial podcast task record: {str(e)}"
            }), 500
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

        response_payload = {
            "podcast_id": podcast_id,
            "topic": topic,
            "generation_status": final_cpoa_status,
            "details": cpoa_result  # Keep full details for now
        }

        http_status_code = 201 # Default for success

        if final_cpoa_status.startswith("failed"):
            # Prioritize specific error message from CPOA
            error_message = cpoa_result.get("error_message")
            if not error_message:
                error_message = f"Podcast generation failed with status: {final_cpoa_status}"
            # Standardize response_payload for error case
            response_payload = {
                "error_code": f"API_GW_CPOA_ORCHESTRATION_FAILED_{final_cpoa_status.upper().replace('FAILED_', '')}",
                "message": error_message, # This is usually user-friendly from CPOA
                "details": cpoa_result.get("details", error_message), # Use CPOA details or error_message as details
                "podcast_id": podcast_id, # Still include podcast_id for tracking
                "topic": topic,
                "generation_status": final_cpoa_status # Keep original status for context
            }
            # Determine appropriate HTTP status code
            if "request_exception" in final_cpoa_status or                "reported_error" in final_cpoa_status or                "bad_script_structure" in final_cpoa_status or                "json_decode" in final_cpoa_status: # Errors related to downstream services
                http_status_code = 502 # Bad Gateway
            else: # More general CPOA failures
                http_status_code = 500 # Internal Server Error
        elif final_cpoa_status.startswith("completed_with_vfa_skipped") or              final_cpoa_status.startswith("completed_with_asf_notification_failure") or              final_cpoa_status.startswith("completed_with_vfa_data_missing"):
            # Task completed but with issues, still a form of success but message is important
            response_payload["message"] = cpoa_result.get("error_message", f"Task completed with status: {final_cpoa_status}")
            # Add context to details if not already there from CPOA
            if "details" not in response_payload: response_payload["details"] = cpoa_result
            http_status_code = 200 # OK, as it did complete, but with caveats
        else: # Successful completion
            response_payload["message"] = cpoa_result.get("message", "Podcast generation task initiated and completed successfully.")
            if "details" not in response_payload: response_payload["details"] = cpoa_result # Ensure details is populated
            # Add audio_url if available from CPOA result, for successful completion
            if cpoa_result.get("final_audio_details", {}).get("audio_filepath"):
                response_payload["audio_url"] = f"/api/v1/podcasts/{podcast_id}/audio.mp3"

        return jsonify(response_payload), http_status_code

    except ImportError as ie:
        app.logger.error(f"CPOA function unavailable: {ie}")
        return jsonify({
            "error_code": "API_GW_CPOA_PODCAST_MODULE_UNAVAILABLE", # More specific than generic service unavailable
            "message": "Core podcast orchestration module is critically unavailable.",
            "details": f"CPOA orchestrate_podcast_generation function import failed: {str(ie)}"
        }), 503
    except Exception as e:
        app.logger.error(f"Unexpected error: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_PODCAST_CREATE_UNEXPECTED_ERROR",
            "message": "An unexpected error occurred while creating podcast task.",
            "details": str(e)
        }), 500


# --- List All Podcasts Endpoint ---
@app.route('/api/v1/podcasts', methods=['GET'])
def list_podcasts():
    try:
        page_str = request.args.get('page', default="1")
        per_page_str = request.args.get('per_page', default="10")

        try:
            page = int(page_str)
            if page < 1:
                app.logger.warning(f"Invalid page number '{page_str}' for /podcasts GET. Must be >= 1. Defaulting to 1.")
                # Optionally return 400 if strict validation is preferred
                # return jsonify({"error_code": "API_GW_INVALID_PAGE_NUM", "message": "Validation failed: 'page' must be a positive integer."}), 400
                page = 1 # Defaulting behavior
        except ValueError:
            app.logger.warning(f"Invalid page type '{page_str}' for /podcasts GET. Must be integer. Defaulting to 1.")
            # return jsonify({"error_code": "API_GW_INVALID_PAGE_TYPE", "message": "Validation failed: 'page' must be a valid integer."}), 400
            page = 1 # Defaulting behavior

        try:
            per_page = int(per_page_str)
            if not (1 <= per_page <= 100): # Max 100 per page
                app.logger.warning(f"Invalid per_page value '{per_page_str}' for /podcasts GET. Must be 1-100. Defaulting to 10.")
                # return jsonify({"error_code": "API_GW_INVALID_PER_PAGE_RANGE", "message": "Validation failed: 'per_page' must be an integer between 1 and 100."}), 400
                per_page = 10 # Defaulting behavior
        except ValueError:
            app.logger.warning(f"Invalid per_page type '{per_page_str}' for /podcasts GET. Must be integer. Defaulting to 10.")
            # return jsonify({"error_code": "API_GW_INVALID_PER_PAGE_TYPE", "message": "Validation failed: 'per_page' must be a valid integer."}), 400
            per_page = 10 # Defaulting behavior

        offset = (page - 1) * per_page

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM podcasts")
        total_podcasts_row = cursor.fetchone()
        total_podcasts = total_podcasts_row[0] if total_podcasts_row else 0

        cursor.execute(
            "SELECT podcast_id, topic, task_created_timestamp, cpoa_status, final_audio_filepath FROM podcasts ORDER BY task_created_timestamp DESC LIMIT ? OFFSET ?",
            (per_page, offset)
        )
        podcasts_rows = cursor.fetchall()
        conn.close()

        podcasts_list = []
        for row in podcasts_rows:
            audio_url = f"/api/v1/podcasts/{row['podcast_id']}/audio.mp3" if row["final_audio_filepath"] else None
            podcasts_list.append({
                "podcast_id": row["podcast_id"],
                "topic": row["topic"],
                "task_created_timestamp": row["task_created_timestamp"],
                "status": row["cpoa_status"],
                "audio_url": audio_url
            })

        total_pages = (total_podcasts + per_page - 1) // per_page if total_podcasts > 0 else 0

        return jsonify({
            "podcasts": podcasts_list,
            "page": page,
            "per_page": per_page,
            "total_podcasts": total_podcasts,
            "total_pages": total_pages
        }), 200
    except sqlite3.Error as e:
        app.logger.error(f"Database error listing podcasts: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_PODCAST_DB_ERROR_LIST",
            "message": "Could not list podcasts due to a database issue.",
            "details": str(e)
        }), 500
    except Exception as e:
        app.logger.error(f"Unexpected error listing podcasts: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_PODCAST_LIST_UNEXPECTED_ERROR",
            "message": "An unexpected error occurred while listing podcasts.",
            "details": str(e)
        }), 500

# --- Get Specific Podcast Details Endpoint ---
@app.route('/api/v1/podcasts/<string:podcast_id>', methods=['GET'])
def get_podcast_details(podcast_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM podcasts WHERE podcast_id = ?", (podcast_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({
                "error_code": "API_GW_PODCAST_NOT_FOUND",
                "message": "Podcast task not found.",
                "details": f"Podcast with ID {podcast_id} not found."
            }), 404

        podcast_details = dict(row)
        # Parse JSON fields
        try:
            podcast_details["cpoa_full_orchestration_log"] = json.loads(row["cpoa_full_orchestration_log"]) if row["cpoa_full_orchestration_log"] else []
        except json.JSONDecodeError:
            app.logger.warning(f"Could not parse orchestration_log for podcast {podcast_id}")
            podcast_details["cpoa_full_orchestration_log"] = [{"error": "log parsing failed"}] # Or provide raw string
        try:
            podcast_details["tts_settings_used"] = json.loads(row["tts_settings_used"]) if row["tts_settings_used"] else {}
        except json.JSONDecodeError:
            app.logger.warning(f"Could not parse tts_settings_used for podcast {podcast_id}")
            podcast_details["tts_settings_used"] = {"error": "tts settings parsing failed"}

        # Add audio_url if applicable
        if podcast_details.get("final_audio_filepath"):
            podcast_details["audio_url"] = f"/api/v1/podcasts/{podcast_id}/audio.mp3"
        else:
            podcast_details["audio_url"] = None

        return jsonify(podcast_details), 200
    except sqlite3.Error as e:
        app.logger.error(f"Database error getting details for podcast {podcast_id}: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_PODCAST_DB_ERROR_DETAILS",
            "message": "Could not retrieve podcast details due to a database issue.",
            "details": str(e)
        }), 500
    except Exception as e:
        app.logger.error(f"Unexpected error getting details for podcast {podcast_id}: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_PODCAST_DETAILS_UNEXPECTED_ERROR",
            "message": "An unexpected error occurred while retrieving podcast details.",
            "details": str(e)
        }), 500


# --- Serve Podcast Audio Endpoint ---
@app.route('/api/v1/podcasts/<string:podcast_id>/audio.mp3', methods=['GET'])
def serve_podcast_audio(podcast_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT final_audio_filepath FROM podcasts WHERE podcast_id = ?", (podcast_id,))
        row = cursor.fetchone()
        conn.close()

        if not row or not row["final_audio_filepath"]:
            return jsonify({
                "error_code": "API_GW_AUDIO_NOT_FOUND_DB",
                "message": "Audio not found or not generated for this podcast.",
                "details": f"Audio record not found or filepath missing for podcast ID {podcast_id}."
            }), 404

        audio_filepath = row["final_audio_filepath"]
        if not os.path.exists(audio_filepath):
            app.logger.error(f"Audio file missing on disk for podcast {podcast_id}: {audio_filepath}")
            return jsonify({
                "error_code": "API_GW_AUDIO_NOT_FOUND_DISK",
                "message": "Audio file is missing.",
                "details": f"Audio file not found on disk at path: {audio_filepath}"
            }), 404

        # Determine mimetype based on extension, default to mpeg for .mp3
        mimetype = "audio/mpeg"
        if audio_filepath.lower().endswith(".wav"):
            mimetype = "audio/wav"
        elif audio_filepath.lower().endswith(".ogg"): # Assuming ogg opus
            mimetype = "audio/ogg"

        return send_file(audio_filepath, mimetype=mimetype)
    except sqlite3.Error as e:
        app.logger.error(f"Database error serving audio for podcast {podcast_id}: {e}", exc_info=True)
        return jsonify({
            "error_code": "API_GW_AUDIO_DB_ERROR",
            "message": "Could not serve audio due to a database issue.",
            "details": str(e)
        }), 500
    except Exception as e:
        app.logger.error(f"Unexpected error serving audio for podcast {podcast_id}: {e}", exc_info=True)
        # Avoid sending full exception details for file serving issues if they might leak path info not already logged
        return jsonify({
            "error_code": "API_GW_AUDIO_UNEXPECTED_ERROR",
            "message": "An unexpected error occurred while serving audio.",
            "details": "Unexpected server error."
        }), 500


# --- Main Block ---
if __name__ == '__main__':
    init_db()
    host = os.getenv("API_GW_HOST", "0.0.0.0")
    port = int(os.getenv("API_GW_PORT", "5001"))
    debug_mode = os.getenv("API_GW_DEBUG_MODE", "True").lower() == "true"
    # Add logging for these specific run parameters
    app.logger.info(f"Starting API Gateway: Host={host}, Port={port}, DebugMode={debug_mode}")
    app.run(host=host, port=port, debug=debug_mode, use_reloader=False)

[end of aethercast/api_gateway/main.py]

[end of aethercast/api_gateway/main.py]
