import sys
import os
from dotenv import load_dotenv # Added
from flask import Flask, jsonify, request, send_file, send_from_directory, g # Added g
import uuid 
import sqlite3
from datetime import datetime, timedelta # Added timedelta
import json # Added
import requests # Added for TDA calls
from typing import Optional, Dict, Any # Added Any
from functools import wraps # For token_required decorator
import jwt
from werkzeug.security import generate_password_hash, check_password_hash


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

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    created_at TEXT NOT NULL
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

        tables_to_check = ["podcasts", "topics_snippets", "generated_scripts", "user_sessions", "users"]
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
    prefs_json = json.dumps(preferences) if preferences else "{}"
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
        raise

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
# (CPOA import logic remains the same)
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
get_popular_categories = _cpoa_placeholder_categories

cpoa_podcast_func_imported = False
cpoa_snippet_func_imported = False
cpoa_exploration_func_imported = False
cpoa_search_func_imported = False
cpoa_landing_snippets_func_imported = False
cpoa_categories_func_imported = False
CPOA_OVERALL_IMPORT_ERROR_MESSAGE = []
_pre_init_logger = print
try:
    from aethercast.cpoa.main import orchestrate_podcast_generation as opg_real
    orchestrate_podcast_generation = opg_real
    cpoa_podcast_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_podcast_generation.")
except ImportError as e: CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_podcast_generation: {e}")
try:
    from aethercast.cpoa.main import orchestrate_snippet_generation as osg_real
    orchestrate_snippet_generation = osg_real
    cpoa_snippet_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_snippet_generation.")
except ImportError as e: CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_snippet_generation: {e}")
try:
    from aethercast.cpoa.main import orchestrate_topic_exploration as ote_real
    orchestrate_topic_exploration = ote_real
    cpoa_exploration_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_topic_exploration.")
except ImportError as e: CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_topic_exploration: {e}")
try:
    from aethercast.cpoa.main import orchestrate_search_results_generation as osrg_real
    orchestrate_search_results_generation = osrg_real
    cpoa_search_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_search_results_generation.")
except ImportError as e: CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_search_results_generation: {e}")
try:
    from aethercast.cpoa.main import orchestrate_landing_page_snippets as olps_real
    orchestrate_landing_page_snippets = olps_real
    cpoa_landing_snippets_func_imported = True
    _pre_init_logger("Successfully imported CPOA.orchestrate_landing_page_snippets.")
except ImportError as e: CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"orchestrate_landing_page_snippets: {e}")
try:
    from aethercast.cpoa.main import get_popular_categories as gpc_real
    get_popular_categories = gpc_real
    cpoa_categories_func_imported = True
    _pre_init_logger("Successfully imported CPOA.get_popular_categories.")
except ImportError as e: CPOA_OVERALL_IMPORT_ERROR_MESSAGE.append(f"get_popular_categories: {e}")
if CPOA_OVERALL_IMPORT_ERROR_MESSAGE: _pre_init_logger(f"CPOA Module Import Errors: {'; '.join(CPOA_OVERALL_IMPORT_ERROR_MESSAGE)}")

# --- Authentication Helper Functions ---
def hash_password(password: str) -> str:
    return generate_password_hash(password)

def check_password(hashed_password: str, password: str) -> bool:
    return check_password_hash(hashed_password, password)

def generate_jwt(user_id: str, secret_key: str) -> Optional[str]:
    try:
        payload = {
            'user_id': user_id,
            'exp': datetime.utcnow() + timedelta(hours=1),
            'iat': datetime.utcnow()
        }
        return jwt.encode(payload, secret_key, algorithm='HS256')
    except Exception as e:
        _pre_init_logger(f"Error generating JWT: {e}")
        return None

def decode_jwt(token: str, secret_key: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, secret_key, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        app.logger.warning("JWT expired.")
        return None
    except jwt.InvalidTokenError:
        app.logger.warning("Invalid JWT.")
        return None
    except Exception as e:
        app.logger.error(f"Error decoding JWT: {e}")
        return None

# --- Flask App Initialization ---
app = Flask(__name__)
default_secret_key = os.urandom(32).hex()
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', default_secret_key)
if app.config['SECRET_KEY'] == default_secret_key:
    _pre_init_logger("WARNING: FLASK_SECRET_KEY not set. Using temporary default. Set in .env for production.")

# Decorator for requiring JWT token
def token_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(" ")[1]
        if not token:
            app.logger.warning("Auth: Token is missing.")
            return jsonify({"error_code": "AUTH_MISSING_TOKEN", "message": "Authentication token is missing or malformed."}), 401
        try:
            payload = decode_jwt(token, app.config['SECRET_KEY'])
            if not payload or 'user_id' not in payload:
                app.logger.warning(f"Auth: Invalid token payload: {payload}")
                return jsonify({"error_code": "AUTH_INVALID_TOKEN", "message": "Token is invalid."}), 401

            conn = get_db_connection()
            # Assuming get_db_connection and its row_factory are set up for dict-like access
            user_row = conn.execute("SELECT * FROM users WHERE user_id = ?", (payload['user_id'],)).fetchone()
            conn.close() # Close connection after fetching
            if not user_row:
                app.logger.warning(f"Auth: User {payload['user_id']} from token not found in DB.")
                return jsonify({"error_code": "AUTH_USER_NOT_FOUND", "message": "User associated with token not found."}), 401
            g.current_user = dict(user_row)
        except jwt.ExpiredSignatureError:
            app.logger.warning("Auth: Token has expired.")
            return jsonify({"error_code": "AUTH_EXPIRED_TOKEN", "message": "Token has expired."}), 401
        except jwt.InvalidTokenError as e_invalid_token:
            app.logger.warning(f"Auth: Token is invalid. Error: {e_invalid_token}")
            return jsonify({"error_code": "AUTH_INVALID_TOKEN", "message": "Token is invalid."}), 401
        except Exception as e:
            app.logger.error(f"Auth: Unexpected error during token validation: {e}", exc_info=True)
            return jsonify({"error_code": "AUTH_UNEXPECTED_ERROR", "message": "Could not process token due to an internal error."}), 500
        return f(*args, **kwargs)
    return decorated_function

# Frontend Directory Path
FEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fend'))
with app.app_context():
    app.logger.info("--- API Gateway Configuration ---")
    if app.config['SECRET_KEY'] == default_secret_key:
        app.logger.warning("FLASK_SECRET_KEY is using a temporary, auto-generated default.")
    else:
        app.logger.info("FLASK_SECRET_KEY loaded from environment.")
    app.logger.info(f"TDA_SERVICE_URL: {TDA_SERVICE_URL}")
    app.logger.info(f"SHARED_DATABASE_PATH: {DATABASE_FILE}") # This will be for SQLite if DATABASE_TYPE is sqlite
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
        conn = get_db_connection() # This will use SQLite for now
        conn.execute("SELECT 1 FROM podcasts LIMIT 1;")
        conn.close()
    except Exception as e:
        db_status = f"Database connection error: {e}"
        app.logger.error(f"Health check DB error: {e}", exc_info=True)

    cpoa_import_summary = []
    if not cpoa_podcast_func_imported: cpoa_import_summary.append("podcast_generation")
    if not cpoa_snippet_func_imported: cpoa_import_summary.append("snippet_generation (legacy)")
    if not cpoa_exploration_func_imported: cpoa_import_summary.append("topic_exploration")
    if not cpoa_search_func_imported: cpoa_import_summary.append("search_generation")
    if not cpoa_landing_snippets_func_imported: cpoa_import_summary.append("landing_snippets_generation")
    if not cpoa_categories_func_imported: cpoa_import_summary.append("categories_generation")

    cpoa_overall_status = "fully operational"
    if cpoa_import_summary:
        cpoa_overall_status = f"partially operational (missing: {', '.join(cpoa_import_summary)})"
    if CPOA_OVERALL_IMPORT_ERROR_MESSAGE and not cpoa_import_summary : # Should not happen if logic is correct
        cpoa_overall_status = "inconsistent import state (errors logged but functions flagged as imported)"

    health_data = {
        "status": "API Gateway is healthy" if db_status.startswith("Database connection successful.") and not cpoa_import_summary else "API Gateway has issues",
        "cpoa_module_status": cpoa_overall_status,
        "database_status": db_status,
        "cpoa_detailed_import_errors": CPOA_OVERALL_IMPORT_ERROR_MESSAGE if CPOA_OVERALL_IMPORT_ERROR_MESSAGE else "None"
    }
    status_code = 200
    if not db_status.startswith("Database connection successful.") or not IMPORTS_SUCCESSFUL_ALL_CPOA_FUNCS(): status_code = 503
    return jsonify(health_data), status_code

def IMPORTS_SUCCESSFUL_ALL_CPOA_FUNCS():
    return cpoa_podcast_func_imported and cpoa_snippet_func_imported and cpoa_exploration_func_imported and cpoa_search_func_imported and cpoa_landing_snippets_func_imported and cpoa_categories_func_imported

# --- Session Management Endpoints ---
@app.route('/api/v1/session/init', methods=['POST'])
def session_init():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json:
        return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": f"Malformed JSON: {e_json}"}), 400
    client_id = data.get('client_id')
    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        return jsonify({"error_code": "API_GW_SESSION_CLIENT_ID_INVALID", "message": "Client ID is required."}), 400
    conn = None
    try:
        conn = get_db_connection()
        session = _get_session(conn, client_id)
        preferences = {}
        if session:
            _touch_session_last_seen(conn, client_id)
            if session["preferences_json"]: preferences = json.loads(session["preferences_json"])
        else:
            _create_session(conn, client_id)
        return jsonify({"client_id": client_id, "preferences": preferences}), 200
    except sqlite3.Error as e:
        app.logger.error(f"DB error session init for {client_id}: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_DB_ERROR_INIT", "message": "Could not initialize session."}), 500
    except json.JSONDecodeError as e:
        app.logger.error(f"Error decoding preferences for session {client_id}: {e}", exc_info=True)
        return jsonify({"client_id": client_id, "preferences": {}, "warning": "Corrupted preferences reset."}), 200
    finally:
        if conn: conn.close()

@app.route('/api/v1/session/preferences', methods=['GET'])
def get_session_preferences():
    client_id = request.args.get('client_id')
    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        return jsonify({"error_code": "API_GW_SESSION_CLIENT_ID_INVALID", "message": "Client ID query param required."}), 400
    conn = None
    try:
        conn = get_db_connection()
        session = _get_session(conn, client_id)
        if session:
            _touch_session_last_seen(conn, client_id)
            preferences = json.loads(session["preferences_json"]) if session["preferences_json"] else {}
            return jsonify({"client_id": client_id, "preferences": preferences}), 200
        else:
            return jsonify({"error_code": "API_GW_SESSION_NOT_FOUND", "message": "User session not found."}), 404
    except sqlite3.Error as e:
        app.logger.error(f"DB error get_session_preferences for {client_id}: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_DB_ERROR_GET_PREFS", "message": "Could not retrieve preferences."}), 500
    except json.JSONDecodeError as e:
        app.logger.error(f"Error decoding preferences for session {client_id}: {e}", exc_info=True)
        return jsonify({"client_id": client_id, "preferences": {}, "warning": "Corrupted preferences reset."}), 200
    finally:
        if conn: conn.close()

@app.route('/api/v1/session/preferences', methods=['POST'])
@token_required
def update_session_preferences_endpoint():
    app.logger.info(f"User {g.current_user['user_id']} accessing update_session_preferences.")
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json:
        return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": f"Malformed JSON: {e_json}"}), 400
    client_id = data.get('client_id')
    preferences = data.get('preferences')
    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        return jsonify({"error_code": "API_GW_SESSION_CLIENT_ID_INVALID", "message": "Client ID required."}), 400
    if preferences is None or not isinstance(preferences, dict):
        return jsonify({"error_code": "API_GW_SESSION_INVALID_PREFERENCES_PAYLOAD", "message": "Preferences dict required."}), 400
    conn = None
    try:
        conn = get_db_connection()
        session = _get_session(conn, client_id)
        if session:
            # Potential: Check if g.current_user['user_id'] has rights to modify session for 'client_id'
            _update_session_preferences(conn, client_id, preferences)
            return jsonify({"client_id": client_id, "message": "Preferences updated successfully."}), 200
        else:
            return jsonify({"error_code": "API_GW_SESSION_NOT_FOUND", "message": "Session not found."}), 404
    except sqlite3.Error as e:
        app.logger.error(f"DB error update_session_preferences for {client_id}: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_DB_ERROR_UPDATE_PREFS", "message": "Could not update preferences."}), 500
    finally:
        if conn: conn.close()

# --- Auth Endpoints ---
@app.route('/auth/register', methods=['POST'])
def register_user():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json:
        return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": f"Malformed JSON: {e_json}"}), 400
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    if not username or not isinstance(username, str) or not username.strip():
        return jsonify({"error_code": "API_GW_AUTH_INVALID_USERNAME", "message": "Username required."}), 400
    if not email or not isinstance(email, str) or not email.strip(): # Add more robust email validation if needed
        return jsonify({"error_code": "API_GW_AUTH_INVALID_EMAIL", "message": "Email required."}), 400
    if not password or not isinstance(password, str) or len(password) < 8:
        return jsonify({"error_code": "API_GW_AUTH_INVALID_PASSWORD", "message": "Password (min 8 chars) required."}), 400
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE username = ? OR email = ?", (username, email))
        if cursor.fetchone():
            return jsonify({"error_code": "API_GW_AUTH_USER_EXISTS", "message": "Username or email already exists."}), 409
        user_id = str(uuid.uuid4())
        hashed_pwd = hash_password(password)
        created_at_ts = datetime.utcnow().isoformat()
        cursor.execute("INSERT INTO users (user_id, username, email, hashed_password, created_at) VALUES (?, ?, ?, ?, ?)",
                       (user_id, username, email, hashed_pwd, created_at_ts))
        conn.commit()
        app.logger.info(f"New user registered: {username}, ID: {user_id}")
        return jsonify({"message": "User registered successfully.", "user_id": user_id}), 201
    except sqlite3.Error as e:
        app.logger.error(f"DB error user registration for {username}: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_AUTH_DB_ERROR_REGISTER", "message": "Could not register user."}), 500
    finally:
        if conn: conn.close()

@app.route('/auth/login', methods=['POST'])
def login_user():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json:
        return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": f"Malformed JSON: {e_json}"}), 400
    login_identifier = data.get('login_identifier')
    password = data.get('password')
    if not login_identifier or not isinstance(login_identifier, str) or not login_identifier.strip():
        return jsonify({"error_code": "API_GW_AUTH_INVALID_LOGIN_ID", "message": "Login identifier required."}), 400
    if not password or not isinstance(password, str):
        return jsonify({"error_code": "API_GW_AUTH_INVALID_PASSWORD_LOGIN", "message": "Password required."}), 400
    conn = None
    try:
        conn = get_db_connection()
        # Assuming RealDictRow or similar from get_db_connection if PG, or sqlite3.Row
        user_record = conn.execute("SELECT user_id, username, hashed_password FROM users WHERE username = ? OR email = ?",
                                   (login_identifier, login_identifier)).fetchone()
        if not user_record or not check_password(user_record["hashed_password"], password):
            app.logger.warning(f"Failed login attempt for: {login_identifier}")
            return jsonify({"error_code": "API_GW_AUTH_INVALID_CREDENTIALS", "message": "Invalid credentials."}), 401

        user_id = user_record["user_id"] # Works for both sqlite3.Row and RealDictRow
        username = user_record["username"]

        access_token = generate_jwt(user_id, app.config['SECRET_KEY'])
        if not access_token:
            app.logger.error(f"JWT generation failed for user: {user_id}")
            return jsonify({"error_code": "API_GW_AUTH_JWT_GENERATION_FAILED", "message": "Could not issue token."}), 500
        app.logger.info(f"User '{username}' logged in.")
        return jsonify({"access_token": access_token, "user_id": user_id, "username": username}), 200
    except sqlite3.Error as e: # Will change to psycopg2.Error
        app.logger.error(f"Database error during login for {login_identifier}: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_AUTH_DB_ERROR_LOGIN", "message": "Login failed due to database issue."}), 500
    finally:
        if conn: conn.close()

# --- Snippets Endpoint ---
@app.route('/api/v1/snippets', methods=['GET'])
def get_dynamic_snippets():
    app.logger.info("Request received for /api/v1/snippets (dynamic generation using orchestrate_landing_page_snippets)")
    if not cpoa_landing_snippets_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_SNIPPET_SERVICE_UNAVAILABLE", "message": "Snippet service unavailable."}), 503
    try:
        limit_str = request.args.get('limit', default="6")
        try:
            limit = int(limit_str)
            if not (1 <= limit <= 20): limit = 6
        except ValueError: limit = 6
        cpoa_response = orchestrate_landing_page_snippets(limit=limit)
        if "error" in cpoa_response:
            error_type = cpoa_response.get("error", "CPOA_ERROR")
            status_code = 503 if "TDA_" in error_type or "SCA_" in error_type or "IGA_" in error_type else 500
            return jsonify({"error_code": f"API_GW_CPOA_SNIPPET_ERROR_{error_type.upper()}", "message": "Failed to get snippets."}), status_code
        return jsonify(cpoa_response), 200
    except ImportError: return jsonify({"error_code": "API_GW_CPOA_SNIPPET_MODULE_UNAVAILABLE", "message": "Snippet module unavailable."}), 503
    except Exception as e: app.logger.error(f"Unexpected error in get_dynamic_snippets: {e}", exc_info=True); return jsonify({"error_code": "API_GW_SNIPPETS_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500

# --- Categories Endpoint ---
@app.route('/api/v1/categories', methods=['GET'])
def get_categories_endpoint():
    app.logger.info("Request received for /api/v1/categories")
    if not cpoa_categories_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_CATEGORY_SERVICE_UNAVAILABLE", "message": "Category service unavailable."}), 503
    try:
        cpoa_response = get_popular_categories()
        if "error" in cpoa_response:
            return jsonify({"error_code": f"API_GW_CPOA_CATEGORY_ERROR_{cpoa_response.get('error', 'CPOA_ERROR').upper()}", "message": "Failed to get categories."}), 500
        return jsonify(cpoa_response), 200
    except ImportError: return jsonify({"error_code": "API_GW_CPOA_CATEGORY_MODULE_UNAVAILABLE", "message": "Category module unavailable."}), 503
    except Exception as e: app.logger.error(f"Unexpected error in /categories: {e}", exc_info=True); return jsonify({"error_code": "API_GW_CATEGORIES_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500

# --- Topic Exploration Endpoint ---
@app.route('/api/v1/topics/explore', methods=['POST'])
@token_required
def explore_topic():
    app.logger.info(f"Authenticated user for explore: {g.current_user['user_id']}. (client_id from payload: {request.get_json(silent=True).get('client_id') if request.is_json else 'N/A'})")
    if not cpoa_exploration_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_EXPLORE_SERVICE_UNAVAILABLE", "message": "Exploration service unavailable."}), 503
    try:
        data = request.get_json()
        if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid JSON payload."}), 400
    except Exception as e_json: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": f"Malformed JSON: {e_json}"}), 400
    current_topic_id = data.get("current_topic_id")
    keywords = data.get("keywords")
    depth_mode = data.get("depth_mode", "deeper")
    client_id = data.get("client_id")
    if not current_topic_id and not keywords:
        return jsonify({"error_code": "API_GW_EXPLORE_INPUT_REQUIRED", "message": "current_topic_id or keywords required."}), 400
    if keywords is not None and (not isinstance(keywords, list) or not all(isinstance(kw, str) and kw.strip() for kw in keywords)):
        return jsonify({"error_code": "API_GW_EXPLORE_INVALID_KEYWORDS_TYPE", "message": "keywords must be a list of non-empty strings."}), 400
    if current_topic_id is not None and (not isinstance(current_topic_id, str) or not current_topic_id.strip()):
        return jsonify({"error_code": "API_GW_EXPLORE_INVALID_TOPIC_ID", "message": "current_topic_id must be non-empty string."}), 400
    if depth_mode is not None and (not isinstance(depth_mode, str) or not depth_mode.strip()):
        return jsonify({"error_code": "API_GW_EXPLORE_INVALID_DEPTH_MODE", "message": "depth_mode must be non-empty string."}), 400
    if client_id is not None and (not isinstance(client_id, str) or not client_id.strip()):
        return jsonify({"error_code": "API_GW_CLIENT_ID_INVALID", "message": "client_id must be non-empty string."}), 400
    user_preferences = None
    if client_id:
        conn_prefs = None
        try:
            conn_prefs = get_db_connection()
            session_data = _get_session(conn_prefs, client_id)
            if session_data and session_data["preferences_json"]: user_preferences = json.loads(session_data["preferences_json"])
            elif not session_data: _create_session(conn_prefs, client_id); user_preferences = {}
            else: user_preferences = {}
            if session_data: _touch_session_last_seen(conn_prefs, client_id)
        except Exception as e_prefs: app.logger.error(f"DB/JSON error for client {client_id} preferences: {e_prefs}"); user_preferences = {}
        finally:
            if conn_prefs: conn_prefs.close()
    try:
        cpoa_response = orchestrate_topic_exploration(current_topic_id=current_topic_id, keywords=keywords, depth_mode=depth_mode, user_preferences=user_preferences)
        if isinstance(cpoa_response, dict) and "error" in cpoa_response:
            error_type = cpoa_response.get("error", "CPOA_ERROR")
            status_code = 503 if "TDA_" in error_type or "SCA_" in error_type else 500
            return jsonify({"error_code": f"API_GW_CPOA_EXPLORE_ERROR_{error_type.upper()}", "message": "Exploration failed."}), status_code
        if isinstance(cpoa_response, list): return jsonify({"explored_topics": cpoa_response}), 200
        else: return jsonify({"error_code": "API_GW_CPOA_EXPLORE_UNEXPECTED_RESPONSE", "message": "Unexpected response from exploration."}), 500
    except ImportError: return jsonify({"error_code": "API_GW_CPOA_EXPLORE_MODULE_UNAVAILABLE_RUNTIME", "message": "Exploration module unavailable."}), 503
    except ValueError as ve: return jsonify({"error_code": "API_GW_EXPLORE_INVALID_INPUT_OR_STATE", "message": str(ve)}), 400
    except Exception as e: app.logger.error(f"Unexpected error in /explore: {e}", exc_info=True); return jsonify({"error_code": "API_GW_EXPLORE_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500

# --- Search Endpoint ---
@app.route('/api/v1/search/podcasts', methods=['POST'])
@token_required
def search_podcasts_endpoint():
    app.logger.info(f"Authenticated user for search: {g.current_user['user_id']}. (client_id from payload: {request.get_json(silent=True).get('client_id') if request.is_json else 'N/A'})")
    if not cpoa_search_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_SEARCH_SERVICE_UNAVAILABLE", "message": "Search service unavailable."}), 503
    try:
        data = request.get_json()
        if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid JSON payload."}), 400
    except Exception as e_json: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": f"Malformed JSON: {e_json}"}), 400
    query = data.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        return jsonify({"error_code": "API_GW_SEARCH_QUERY_INVALID", "message": "Query required."}), 400
    client_id = data.get("client_id")
    if client_id is not None and (not isinstance(client_id, str) or not client_id.strip()):
        return jsonify({"error_code": "API_GW_CLIENT_ID_INVALID", "message": "client_id must be non-empty string."}), 400
    user_preferences = None
    if client_id:
        conn_prefs = None
        try:
            conn_prefs = get_db_connection()
            session_data = _get_session(conn_prefs, client_id)
            if session_data and session_data["preferences_json"]: user_preferences = json.loads(session_data["preferences_json"])
            elif not session_data: _create_session(conn_prefs, client_id)
            if session_data: _touch_session_last_seen(conn_prefs, client_id)
        except Exception as e_prefs: app.logger.error(f"DB/JSON error for client {client_id} preferences (search): {e_prefs}")
        finally:
            if conn_prefs: conn_prefs.close()
    try:
        cpoa_search_response = orchestrate_search_results_generation(query=query, user_preferences=user_preferences)
        if "error" in cpoa_search_response:
            error_type = cpoa_search_response.get("error", "CPOA_SEARCH_ERROR")
            status_code = 503 if "TDA_" in error_type or "SCA_" in error_type else 500
            return jsonify({"error_code": f"API_GW_CPOA_SEARCH_ERROR_{error_type.upper()}", "message": "Search failed."}), status_code
        return jsonify(cpoa_search_response), 200
    except ImportError: return jsonify({"error_code": "API_GW_CPOA_SEARCH_MODULE_UNAVAILABLE", "message": "Search module unavailable."}), 503
    except Exception as e: app.logger.error(f"Unexpected error in /search: {e}", exc_info=True); return jsonify({"error_code": "API_GW_SEARCH_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500

# --- Podcast Generation Endpoint ---
@app.route('/api/v1/podcasts', methods=['POST'])
@token_required
def create_podcast_generation_task():
    app.logger.info(f"Authenticated user for podcast creation: {g.current_user['user_id']}. (client_id from payload: {request.get_json(silent=True).get('client_id') if request.is_json else 'N/A'})")
    if not cpoa_podcast_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_PODCAST_SERVICE_UNAVAILABLE", "message": "Podcast service unavailable."}), 503
    try:
        data = request.get_json()
        if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Invalid JSON payload."}), 400
    except Exception as e_json: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": f"Malformed JSON: {e_json}"}), 400
    topic = data.get('topic')
    if not topic or not isinstance(topic, str) or not topic.strip():
        return jsonify({"error_code": "API_GW_PODCAST_TOPIC_INVALID", "message": "Topic required."}), 400
    voice_params_from_request = data.get('voice_params')
    if voice_params_from_request is not None and not isinstance(voice_params_from_request, dict):
        return jsonify({"error_code": "API_GW_PODCAST_INVALID_VOICE_PARAMS_TYPE", "message": "voice_params must be object."}), 400
    client_id_from_request = data.get('client_id')
    if client_id_from_request is not None and (not isinstance(client_id_from_request, str) or not client_id_from_request.strip()):
        return jsonify({"error_code": "API_GW_PODCAST_INVALID_CLIENT_ID", "message": "client_id must be non-empty string."}), 400
    test_scenarios_from_request = data.get('test_scenarios')
    if test_scenarios_from_request is not None and not isinstance(test_scenarios_from_request, dict):
        return jsonify({"error_code": "API_GW_PODCAST_INVALID_TEST_SCENARIOS_TYPE", "message": "test_scenarios must be object."}), 400
    user_preferences = None
    if client_id_from_request:
        conn_prefs = None
        try:
            conn_prefs = get_db_connection()
            session_data = _get_session(conn_prefs, client_id_from_request)
            if session_data and session_data["preferences_json"]: user_preferences = json.loads(session_data["preferences_json"])
            elif not session_data: _create_session(conn_prefs, client_id_from_request); user_preferences = {}
            else: user_preferences = {}
            if session_data: _touch_session_last_seen(conn_prefs, client_id_from_request)
        except Exception as e_prefs: app.logger.error(f"DB/JSON error for client {client_id_from_request} preferences (podcast): {e_prefs}")
        finally:
            if conn_prefs: conn_prefs.close()
    try:
        podcast_id = str(uuid.uuid4())
        task_created_timestamp = datetime.utcnow().isoformat()
        conn_task = None
        try:
            conn_task = get_db_connection()
            conn_task.execute("INSERT INTO podcasts (podcast_id, topic, cpoa_status, task_created_timestamp, last_updated_timestamp, tts_settings_used) VALUES (?, ?, ?, ?, ?, ?)",
                              (podcast_id, topic, "pending_api_gateway", task_created_timestamp, task_created_timestamp, json.dumps(voice_params_from_request) if voice_params_from_request else None))
            conn_task.commit()
        except sqlite3.Error as e_task_db:
            app.logger.error(f"DB error creating podcast task {podcast_id}: {e_task_db}", exc_info=True)
            return jsonify({"error_code": "API_GW_PODCAST_DB_ERROR_CREATE_TASK", "message": "Failed to create podcast task record."}), 500
        finally:
            if conn_task: conn_task.close()
        cpoa_kwargs = {"topic": topic, "task_id": podcast_id, "db_path": DATABASE_FILE, "voice_params_input": voice_params_from_request, "user_preferences": user_preferences, "test_scenarios": test_scenarios_from_request}
        if client_id_from_request: cpoa_kwargs["client_id"] = client_id_from_request
        cpoa_result = orchestrate_podcast_generation(**cpoa_kwargs)
        final_cpoa_status = cpoa_result.get("status", "unknown_cpoa_status")
        response_payload = {"podcast_id": podcast_id, "topic": topic, "generation_status": final_cpoa_status, "details": cpoa_result}
        http_status_code = 201
        if final_cpoa_status.startswith("failed"):
            error_message = cpoa_result.get("error_message", f"Podcast generation failed: {final_cpoa_status}")
            response_payload = {"error_code": f"API_GW_CPOA_ORCHESTRATION_FAILED_{final_cpoa_status.upper()}", "message": error_message, "details": cpoa_result.get("details", error_message), "podcast_id": podcast_id, "topic": topic, "generation_status": final_cpoa_status}
            http_status_code = 502 if "request_exception" in final_cpoa_status or "reported_error" in final_cpoa_status else 500
        elif final_cpoa_status.startswith("completed_with_"):
            response_payload["message"] = cpoa_result.get("error_message", f"Task completed: {final_cpoa_status}")
            http_status_code = 200
        else: # Success
            if cpoa_result.get("final_audio_details", {}).get("audio_filepath"):
                response_payload["audio_url"] = f"/api/v1/podcasts/{podcast_id}/audio.mp3"
        return jsonify(response_payload), http_status_code
    except ImportError: return jsonify({"error_code": "API_GW_CPOA_PODCAST_MODULE_UNAVAILABLE", "message": "Podcast module unavailable."}), 503
    except Exception as e: app.logger.error(f"Unexpected error in create_podcast_generation_task: {e}", exc_info=True); return jsonify({"error_code": "API_GW_PODCAST_CREATE_UNEXPECTED_ERROR", "message": "Unexpected error creating podcast."}), 500

# --- List All Podcasts Endpoint ---
# ... (route remains the same) ...
@app.route('/api/v1/podcasts', methods=['GET'])
def list_podcasts():
    try:
        page_str = request.args.get('page', default="1")
        per_page_str = request.args.get('per_page', default="10")
        try: page = int(page_str); page = 1 if page < 1 else page
        except ValueError: page = 1
        try: per_page = int(per_page_str); per_page = 10 if not (1 <= per_page <= 100) else per_page
        except ValueError: per_page = 10
        offset = (page - 1) * per_page
        conn = get_db_connection()
        total_podcasts = conn.execute("SELECT COUNT(*) FROM podcasts").fetchone()[0]
        podcasts_rows = conn.execute("SELECT podcast_id, topic, task_created_timestamp, cpoa_status, final_audio_filepath FROM podcasts ORDER BY task_created_timestamp DESC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
        conn.close()
        podcasts_list = [{"podcast_id": r["podcast_id"], "topic": r["topic"], "task_created_timestamp": r["task_created_timestamp"], "status": r["cpoa_status"], "audio_url": f"/api/v1/podcasts/{r['podcast_id']}/audio.mp3" if r["final_audio_filepath"] else None} for r in podcasts_rows]
        total_pages = (total_podcasts + per_page - 1) // per_page if total_podcasts > 0 else 0
        return jsonify({"podcasts": podcasts_list, "page": page, "per_page": per_page, "total_podcasts": total_podcasts, "total_pages": total_pages}), 200
    except sqlite3.Error as e: app.logger.error(f"DB error listing podcasts: {e}", exc_info=True); return jsonify({"error_code": "API_GW_PODCAST_DB_ERROR_LIST", "message": "Could not list podcasts."}), 500
    except Exception as e: app.logger.error(f"Unexpected error listing podcasts: {e}", exc_info=True); return jsonify({"error_code": "API_GW_PODCAST_LIST_UNEXPECTED_ERROR", "message": "Unexpected error listing podcasts."}), 500

# --- Get Specific Podcast Details Endpoint ---
# ... (route remains the same) ...
@app.route('/api/v1/podcasts/<string:podcast_id>', methods=['GET'])
def get_podcast_details(podcast_id: str):
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM podcasts WHERE podcast_id = ?", (podcast_id,)).fetchone()
        conn.close()
        if not row: return jsonify({"error_code": "API_GW_PODCAST_NOT_FOUND", "message": "Podcast not found."}), 404
        podcast_details = dict(row)
        try: podcast_details["cpoa_full_orchestration_log"] = json.loads(row["cpoa_full_orchestration_log"]) if row["cpoa_full_orchestration_log"] else []
        except json.JSONDecodeError: podcast_details["cpoa_full_orchestration_log"] = [{"error": "log parsing failed"}]
        try: podcast_details["tts_settings_used"] = json.loads(row["tts_settings_used"]) if row["tts_settings_used"] else {}
        except json.JSONDecodeError: podcast_details["tts_settings_used"] = {"error": "tts settings parsing failed"}
        if podcast_details.get("final_audio_filepath"): podcast_details["audio_url"] = f"/api/v1/podcasts/{podcast_id}/audio.mp3"
        else: podcast_details["audio_url"] = None
        return jsonify(podcast_details), 200
    except sqlite3.Error as e: app.logger.error(f"DB error get_podcast_details for {podcast_id}: {e}", exc_info=True); return jsonify({"error_code": "API_GW_PODCAST_DB_ERROR_DETAILS", "message": "Could not retrieve podcast details."}), 500
    except Exception as e: app.logger.error(f"Unexpected error get_podcast_details for {podcast_id}: {e}", exc_info=True); return jsonify({"error_code": "API_GW_PODCAST_DETAILS_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500

# --- Serve Podcast Audio Endpoint ---
# ... (route remains the same) ...
@app.route('/api/v1/podcasts/<string:podcast_id>/audio.mp3', methods=['GET'])
def serve_podcast_audio(podcast_id: str):
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT final_audio_filepath FROM podcasts WHERE podcast_id = ?", (podcast_id,)).fetchone()
        conn.close()
        if not row or not row["final_audio_filepath"]:
            return jsonify({"error_code": "API_GW_AUDIO_NOT_FOUND_DB", "message": "Audio not found or not generated."}), 404
        audio_filepath = row["final_audio_filepath"]
        if not os.path.exists(audio_filepath):
            app.logger.error(f"Audio file missing for {podcast_id}: {audio_filepath}")
            return jsonify({"error_code": "API_GW_AUDIO_NOT_FOUND_DISK", "message": "Audio file missing."}), 404
        mimetype = "audio/mpeg"
        if audio_filepath.lower().endswith(".wav"): mimetype = "audio/wav"
        elif audio_filepath.lower().endswith(".ogg"): mimetype = "audio/ogg"
        return send_file(audio_filepath, mimetype=mimetype)
    except sqlite3.Error as e: app.logger.error(f"DB error serve_podcast_audio for {podcast_id}: {e}", exc_info=True); return jsonify({"error_code": "API_GW_AUDIO_DB_ERROR", "message": "Could not serve audio."}), 500
    except Exception as e: app.logger.error(f"Unexpected error serve_podcast_audio for {podcast_id}: {e}", exc_info=True); return jsonify({"error_code": "API_GW_AUDIO_UNEXPECTED_ERROR", "message": "Unexpected error serving audio."}), 500

# --- Main Block ---
if __name__ == '__main__':
    init_db()
    host = os.getenv("API_GW_HOST", "0.0.0.0")
    port = int(os.getenv("API_GW_PORT", "5001"))
    debug_mode = os.getenv("API_GW_DEBUG_MODE", "True").lower() == "true"
    app.logger.info(f"Starting API Gateway: Host={host}, Port={port}, DebugMode={debug_mode}")
    app.run(host=host, port=port, debug=debug_mode, use_reloader=False)

[end of aethercast/api_gateway/main.py]
