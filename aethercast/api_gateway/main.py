import sys
import os
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file, send_from_directory, g, redirect # Added redirect
import uuid
import sqlite3 # Will be replaced by psycopg2 for PG
from datetime import datetime, timedelta
import json
import requests
from typing import Optional, Dict, Any, List # Added List
from functools import wraps
import jwt
from werkzeug.security import generate_password_hash, check_password_hash
from google.cloud import storage # Added for GCS
# from google.oauth2 import service_account # Not strictly needed if using ADC

# --- Path Setup for CPOA Import ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
project_root = os.path.dirname(parent_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv()

# --- Service URLs ---
TDA_SERVICE_URL = os.getenv("TDA_SERVICE_URL", "http://localhost:5000/discover_topics")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME") # Added for signed URLs

# --- Database Configuration ---
DATABASE_TYPE = os.getenv("DATABASE_TYPE", "sqlite") # Default to sqlite
DATABASE_FILE = os.getenv("SHARED_DATABASE_PATH", "/app/database/aethercast_podcasts.db") # SQLite path

POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")

# Conditional import for psycopg2
if DATABASE_TYPE == "postgres":
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        _pre_init_logger = print # Use print before app.logger is available
        _pre_init_logger("ERROR: DATABASE_TYPE is 'postgres' but psycopg2 is not installed. Please install it.")
        # sys.exit(1) # Or handle more gracefully depending on desired behavior

# --- DB Schema (PostgreSQL compatible) ---
DB_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS podcasts (
    podcast_id UUID PRIMARY KEY,
    topic TEXT NOT NULL,
    cpoa_status TEXT,
    cpoa_error_message TEXT,
    final_audio_filepath TEXT, -- This will store GCS URI
    stream_id TEXT,
    asf_websocket_url TEXT,
    asf_notification_status TEXT,
    task_created_timestamp TIMESTAMPTZ NOT NULL,
    last_updated_timestamp TIMESTAMPTZ,
    cpoa_full_orchestration_log JSONB,
    tts_settings_used JSONB
);

CREATE TABLE IF NOT EXISTS topics_snippets (
    id UUID PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN ('topic', 'snippet')),
    title TEXT NOT NULL,
    summary TEXT,
    keywords JSONB, -- Changed from TEXT
    source_url TEXT,
    source_name TEXT,
    original_topic_details JSONB, -- Changed from TEXT
    llm_model_used_for_snippet TEXT,
    cover_art_prompt TEXT,
    image_url TEXT, -- This will store GCS URI or signed URL temporarily
    generation_timestamp TIMESTAMPTZ NOT NULL,
    last_accessed_timestamp TIMESTAMPTZ,
    relevance_score REAL
);

CREATE TABLE IF NOT EXISTS generated_scripts (
    script_id UUID PRIMARY KEY,
    topic_hash TEXT NOT NULL UNIQUE, -- Keep as TEXT, it's a hash
    structured_script_json JSONB NOT NULL, -- Changed from TEXT
    generation_timestamp TIMESTAMPTZ NOT NULL,
    llm_model_used TEXT,
    last_accessed_timestamp TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_topic_hash ON generated_scripts (topic_hash);

CREATE TABLE IF NOT EXISTS user_sessions (
    session_id UUID PRIMARY KEY,
    created_timestamp TIMESTAMPTZ NOT NULL,
    last_seen_timestamp TIMESTAMPTZ NOT NULL,
    preferences_json JSONB -- Changed from TEXT
);

CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
"""

# --- API Gateway Specific Configurations ---
API_GW_SNIPPET_CACHE_SIZE = int(os.getenv("API_GW_SNIPPET_CACHE_SIZE", "10"))
API_GW_SNIPPET_CACHE_MAX_AGE_HOURS = int(os.getenv("API_GW_SNIPPET_CACHE_MAX_AGE_HOURS", "24"))

# --- Database Helper Functions (Updated for PostgreSQL) ---
def get_db_connection():
    if DATABASE_TYPE == "postgres":
        if not all([POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB]):
            log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
            log_func("PostgreSQL connection variables not fully set.")
            raise ConnectionError("PostgreSQL environment variables not fully configured.")
        try:
            conn = psycopg2.connect(
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                dbname=POSTGRES_DB,
                cursor_factory=RealDictCursor  # Use RealDictCursor for dict-like row access
            )
            return conn
        except psycopg2.Error as e:
            log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
            log_func(f"Unable to connect to PostgreSQL: {e}")
            raise ConnectionError(f"PostgreSQL connection failed: {e}") from e
    else: # Fallback to SQLite
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if DATABASE_TYPE == "postgres":
            # For PostgreSQL, check for table existence using information_schema
            tables_to_check = ["podcasts", "topics_snippets", "generated_scripts", "user_sessions", "users"]
            for table_name in tables_to_check:
                cursor.execute(f"SELECT to_regclass('public.{table_name}');")
                if not cursor.fetchone()[0]: # to_regclass returns NULL if table doesn't exist
                    app.logger.info(f"Table '{table_name}' not found in PostgreSQL. It will be created.")
                else:
                    app.logger.info(f"Table '{table_name}' already exists in PostgreSQL.")
        else: # SQLite check (original logic)
            tables_to_check = ["podcasts", "topics_snippets", "generated_scripts", "user_sessions", "users"]
            for table_name in tables_to_check:
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
                if not cursor.fetchone():
                    app.logger.info(f"Table '{table_name}' not found in SQLite. It will be created.")
                else:
                    app.logger.info(f"Table '{table_name}' already exists in SQLite.")

        cursor.executescript(DB_SCHEMA_SQL) # This might need adjustment if complex PG features are used not compatible with executescript
        conn.commit()
        app.logger.info(f"Database initialization processed. Tables ensured for {DATABASE_TYPE}.")
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
        log_func(f"Database initialization error ({DATABASE_TYPE}): {e}")
    except Exception as e_unexp: # Catch other potential errors like ConnectionError
        log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
        log_func(f"Unexpected error during DB initialization ({DATABASE_TYPE}): {e_unexp}")
    finally:
        if conn:
            conn.close()

# --- GCS Signed URL Helper ---
def generate_gcs_signed_url(gcs_uri: str, expiration_minutes: int = 15) -> Optional[str]:
    try:
        if not gcs_uri or not gcs_uri.startswith("gs://"):
            app.logger.error(f"Invalid GCS URI provided for signed URL: {gcs_uri}")
            return None

        parts = gcs_uri.replace("gs://", "").split("/", 1)
        bucket_name = parts[0]
        object_name = parts[1] if len(parts) > 1 else None

        if not object_name:
            app.logger.error(f"Could not parse object name from GCS URI: {gcs_uri}")
            return None

        # Optional: Validate bucket_name against configured GCS_BUCKET_NAME
        configured_bucket = os.getenv("GCS_BUCKET_NAME")
        if configured_bucket and bucket_name != configured_bucket:
            app.logger.warning(f"GCS URI bucket '{bucket_name}' does not match configured bucket '{configured_bucket}'. Proceeding with URI's bucket.")
            # Depending on policy, you might choose to return None here.
            # For now, allow signing for any bucket if GOOGLE_APPLICATION_CREDENTIALS has permission.

        storage_client = storage.Client() # Assumes GOOGLE_APPLICATION_CREDENTIALS is set
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(object_name)

        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=expiration_minutes),
            method="GET"
        )
        app.logger.info(f"Generated signed URL for {gcs_uri} expiring in {expiration_minutes} minutes.")
        return signed_url
    except Exception as e:
        app.logger.error(f"Error generating signed URL for {gcs_uri}: {e}", exc_info=True)
        return None

# --- Session Helper Functions (adapt for PG if needed, for now assume compatible) ---
def _get_session(db_conn, session_id: str) -> Optional[Dict[str, Any]]:
    cursor = db_conn.cursor()
    sql = "SELECT * FROM user_sessions WHERE session_id = %s;" if DATABASE_TYPE == "postgres" else "SELECT * FROM user_sessions WHERE session_id = ?;"
    cursor.execute(sql, (session_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def _create_session(db_conn, session_id: str, preferences: Optional[dict] = None) -> None:
    now_ts = datetime.utcnow() # Use datetime object for PG
    prefs_data = preferences if preferences else {} # Ensure it's a dict for JSONB

    try:
        cursor = db_conn.cursor()
        if DATABASE_TYPE == "postgres":
            sql = "INSERT INTO user_sessions (session_id, created_timestamp, last_seen_timestamp, preferences_json) VALUES (%s, %s, %s, %s) ON CONFLICT (session_id) DO NOTHING;"
            # psycopg2 expects json.dumps for JSONB if you pass a string, or it can handle dicts directly
            cursor.execute(sql, (session_id, now_ts, now_ts, json.dumps(prefs_data)))
        else: # SQLite
            prefs_json_str = json.dumps(prefs_data)
            sql = "INSERT OR IGNORE INTO user_sessions (session_id, created_timestamp, last_seen_timestamp, preferences_json) VALUES (?, ?, ?, ?);"
            cursor.execute(sql, (session_id, now_ts.isoformat(), now_ts.isoformat(), prefs_json_str))
        db_conn.commit()
        app.logger.info(f"Session created or ignored for session_id: {session_id}")
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"Failed to create session {session_id} ({DATABASE_TYPE}): {e}")
        db_conn.rollback() if DATABASE_TYPE == "postgres" else None
        raise

def _touch_session_last_seen(db_conn, session_id: str) -> None:
    now_ts = datetime.utcnow()
    try:
        cursor = db_conn.cursor()
        sql = "UPDATE user_sessions SET last_seen_timestamp = %s WHERE session_id = %s;" if DATABASE_TYPE == "postgres" else "UPDATE user_sessions SET last_seen_timestamp = ? WHERE session_id = ?;"
        params = (now_ts, session_id) if DATABASE_TYPE == "postgres" else (now_ts.isoformat(), session_id)
        cursor.execute(sql, params)
        db_conn.commit()
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"Failed to update last_seen for session {session_id} ({DATABASE_TYPE}): {e}")
        db_conn.rollback() if DATABASE_TYPE == "postgres" else None


def _update_session_preferences(db_conn, session_id: str, preferences: dict) -> None:
    now_ts = datetime.utcnow()
    prefs_data = preferences # dict for JSONB
    try:
        cursor = db_conn.cursor()
        if DATABASE_TYPE == "postgres":
            sql = "UPDATE user_sessions SET preferences_json = %s, last_seen_timestamp = %s WHERE session_id = %s;"
            cursor.execute(sql, (json.dumps(prefs_data), now_ts, session_id))
        else: # SQLite
            prefs_json_str = json.dumps(prefs_data)
            sql = "UPDATE user_sessions SET preferences_json = ?, last_seen_timestamp = ? WHERE session_id = ?;"
            cursor.execute(sql, (prefs_json_str, now_ts.isoformat(), session_id))
        db_conn.commit()
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"Failed to update preferences for session {session_id} ({DATABASE_TYPE}): {e}")
        db_conn.rollback() if DATABASE_TYPE == "postgres" else None
        raise

# --- CPOA Import (Remains largely the same, logging adapted) ---
_pre_init_logger = print # Use print before app.logger is available
# ... (CPOA import logic as before, ensure _pre_init_logger is used if app.logger not ready)
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


# --- Flask App Initialization ---
app = Flask(__name__)
default_secret_key = os.urandom(32).hex()
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', default_secret_key)

# --- Logging Setup (after app init) ---
if app.config['SECRET_KEY'] == default_secret_key and not os.getenv('FLASK_SECRET_KEY'):
    app.logger.warning("FLASK_SECRET_KEY not set. Using temporary default. Set in .env for production.")

# --- Auth Helper Functions (PG compatible) ---
def hash_password(password: str) -> str:
    return generate_password_hash(password)

def check_password(hashed_password: str, password: str) -> bool:
    return check_password_hash(hashed_password, password)

def generate_jwt(user_id: str, secret_key: str) -> Optional[str]:
    try:
        payload = {
            'user_id': user_id,
            'exp': datetime.utcnow() + timedelta(hours=int(os.getenv("JWT_EXPIRATION_HOURS", "1"))), # Configurable expiration
            'iat': datetime.utcnow()
        }
        return jwt.encode(payload, secret_key, algorithm='HS256')
    except Exception as e:
        app.logger.error(f"Error generating JWT: {e}", exc_info=True)
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
    except Exception as e: # Catch any other JWT related error
        app.logger.error(f"Error decoding JWT: {e}", exc_info=True)
        return None

# Decorator for requiring JWT token (PG compatible)
def token_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
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
            cursor = conn.cursor()
            sql = "SELECT * FROM users WHERE user_id = %s;" if DATABASE_TYPE == "postgres" else "SELECT * FROM users WHERE user_id = ?;"
            cursor.execute(sql, (payload['user_id'],))
            user_row = cursor.fetchone()
            conn.close()

            if not user_row:
                app.logger.warning(f"Auth: User {payload['user_id']} from token not found in DB.")
                return jsonify({"error_code": "AUTH_USER_NOT_FOUND", "message": "User associated with token not found."}), 401
            g.current_user = dict(user_row) # Works for RealDictRow and sqlite3.Row

        except jwt.ExpiredSignatureError:
            app.logger.warning("Auth: Token has expired.")
            return jsonify({"error_code": "AUTH_EXPIRED_TOKEN", "message": "Token has expired."}), 401
        except jwt.InvalidTokenError as e_invalid_token:
            app.logger.warning(f"Auth: Token is invalid. Error: {e_invalid_token}")
            return jsonify({"error_code": "AUTH_INVALID_TOKEN", "message": "Token is invalid."}), 401
        except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as db_err:
            app.logger.error(f"Auth: DB error during token validation: {db_err}", exc_info=True)
            return jsonify({"error_code": "AUTH_DB_ERROR", "message": "Could not validate token due to a database error."}), 500
        except Exception as e:
            app.logger.error(f"Auth: Unexpected error during token validation: {e}", exc_info=True)
            return jsonify({"error_code": "AUTH_UNEXPECTED_ERROR", "message": "Could not process token due to an internal error."}), 500
        return f(*args, **kwargs)
    return decorated_function


# Frontend Directory Path
FEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fend'))

with app.app_context():
    app.logger.info("--- API Gateway Configuration ---")
    if app.config['SECRET_KEY'] == default_secret_key and not os.getenv('FLASK_SECRET_KEY'):
        app.logger.warning("FLASK_SECRET_KEY is using a temporary, auto-generated default.")
    else:
        app.logger.info("FLASK_SECRET_KEY loaded.")
    app.logger.info(f"DATABASE_TYPE: {DATABASE_TYPE}")
    if DATABASE_TYPE == "postgres":
        app.logger.info(f"POSTGRES_HOST: {POSTGRES_HOST}, POSTGRES_DB: {POSTGRES_DB}")
    else:
        app.logger.info(f"SHARED_DATABASE_PATH (SQLite): {DATABASE_FILE}")
    app.logger.info(f"TDA_SERVICE_URL: {TDA_SERVICE_URL}")
    app.logger.info(f"GCS_BUCKET_NAME: {GCS_BUCKET_NAME if GCS_BUCKET_NAME else 'Not Set'}")
    app.logger.info(f"FEND_DIR: {FEND_DIR}")
    app.logger.info(f"API_GW_SNIPPET_CACHE_SIZE: {API_GW_SNIPPET_CACHE_SIZE}")
    app.logger.info(f"API_GW_SNIPPET_CACHE_MAX_AGE_HOURS: {API_GW_SNIPPET_CACHE_MAX_AGE_HOURS}")
    app.logger.info("--- End API Gateway Configuration ---")


# --- Static Frontend File Serving ---
@app.route('/')
def serve_index():
    return send_from_directory(FEND_DIR, 'index.html')

@app.route('/style.css')
def serve_style():
    return send_from_directory(FEND_DIR, 'style.css')

@app.route('/app.js')
def serve_script():
    return send_from_directory(FEND_DIR, 'app.js')

# --- Health Check Endpoint ---
@app.route('/health', methods=['GET'])
def health_check():
    db_status = "Database connection successful."
    db_type_for_health = "unknown"
    try:
        conn = get_db_connection()
        db_type_for_health = DATABASE_TYPE
        # Simple query for PG and SQLite
        conn.execute("SELECT 1 LIMIT 1;") if DATABASE_TYPE == "postgres" else conn.execute("SELECT 1 FROM podcasts LIMIT 1;")
        conn.close()
    except Exception as e:
        db_status = f"Database connection error ({db_type_for_health}): {e}"
        app.logger.error(f"Health check DB error: {e}", exc_info=True)

    cpoa_import_summary = [name for name, imported in {
        "podcast_generation": cpoa_podcast_func_imported,
        "snippet_generation": cpoa_snippet_func_imported,
        "topic_exploration": cpoa_exploration_func_imported,
        "search_generation": cpoa_search_func_imported,
        "landing_snippets": cpoa_landing_snippets_func_imported,
        "categories": cpoa_categories_func_imported
    }.items() if not imported]

    cpoa_overall_status = "fully operational"
    if cpoa_import_summary:
        cpoa_overall_status = f"partially operational (missing CPOA functions: {', '.join(cpoa_import_summary)})"

    gcs_signed_url_status = "GCS Signed URL generation operational (requires GOOGLE_APPLICATION_CREDENTIALS and bucket access)."
    if not GCS_BUCKET_NAME: # Bucket name is not strictly required by generate_gcs_signed_url if URI has it, but good to check if it's expected to be configured.
        gcs_signed_url_status = "GCS_BUCKET_NAME not configured, signed URLs might rely on bucket from URI."
    try:
        # Test GCS client initialization (doesn't make a call yet)
        storage.Client()
    except Exception as e_gcs:
        gcs_signed_url_status = f"GCS client initialization failed: {e_gcs}. GOOGLE_APPLICATION_CREDENTIALS might be missing or invalid."
        app.logger.error(f"Health check GCS client init error: {e_gcs}", exc_info=True)


    health_data = {
        "status": "API Gateway is healthy" if db_status.startswith("Database connection successful.") and not cpoa_import_summary else "API Gateway has issues",
        "cpoa_module_status": cpoa_overall_status,
        "database_status": db_status,
        "gcs_signed_url_status": gcs_signed_url_status,
        "cpoa_detailed_import_errors": CPOA_OVERALL_IMPORT_ERROR_MESSAGE if CPOA_OVERALL_IMPORT_ERROR_MESSAGE else "None"
    }
    status_code = 200
    if not db_status.startswith("Database connection successful.") or cpoa_import_summary or "GCS client initialization failed" in gcs_signed_url_status : status_code = 503
    return jsonify(health_data), status_code

def IMPORTS_SUCCESSFUL_ALL_CPOA_FUNCS(): # Helper for health check logic
    return all([cpoa_podcast_func_imported, cpoa_snippet_func_imported, cpoa_exploration_func_imported, cpoa_search_func_imported, cpoa_landing_snippets_func_imported, cpoa_categories_func_imported])

# --- Session Management Endpoints (PG compatible) ---
@app.route('/api/v1/session/init', methods=['POST'])
def session_init():
    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

    client_id = data.get('client_id')
    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        return jsonify({"error_code": "API_GW_SESSION_CLIENT_ID_INVALID", "message": "Client ID required."}), 400

    conn = None
    try:
        conn = get_db_connection()
        session = _get_session(conn, client_id)
        preferences = {}
        if session:
            _touch_session_last_seen(conn, client_id)
            if session.get("preferences_json"): # Check if key exists
                prefs_data = session["preferences_json"]
                # For PG with RealDictCursor, JSONB might be returned as dict already or string
                if isinstance(prefs_data, str): preferences = json.loads(prefs_data)
                elif isinstance(prefs_data, dict): preferences = prefs_data
                else: preferences = {} # Fallback
        else:
            _create_session(conn, client_id) # Creates with empty prefs if none provided
        return jsonify({"client_id": client_id, "preferences": preferences}), 200
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"DB error session init for {client_id} ({DATABASE_TYPE}): {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_DB_ERROR_INIT", "message": "Could not initialize session."}), 500
    except json.JSONDecodeError as e_json: # Should be less common with PG handling JSONB
        app.logger.error(f"Error decoding preferences for session {client_id}: {e_json}", exc_info=True)
        return jsonify({"client_id": client_id, "preferences": {}, "warning": "Corrupted preferences, reset."}), 200
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
            preferences = {}
            if session.get("preferences_json"):
                prefs_data = session["preferences_json"]
                if isinstance(prefs_data, str): preferences = json.loads(prefs_data)
                elif isinstance(prefs_data, dict): preferences = prefs_data
                else: preferences = {} # Fallback
            return jsonify({"client_id": client_id, "preferences": preferences}), 200
        else:
            return jsonify({"error_code": "API_GW_SESSION_NOT_FOUND", "message": "User session not found."}), 404
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"DB error get_session_preferences for {client_id} ({DATABASE_TYPE}): {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_DB_ERROR_GET_PREFS", "message": "Could not retrieve preferences."}), 500
    except json.JSONDecodeError as e_json:
        app.logger.error(f"Error decoding preferences for session {client_id}: {e_json}", exc_info=True)
        return jsonify({"client_id": client_id, "preferences": {}, "warning": "Corrupted preferences, reset."}), 200
    finally:
        if conn: conn.close()

@app.route('/api/v1/session/preferences', methods=['POST'])
@token_required # Ensure this decorator is PG compatible
def update_session_preferences_endpoint():
    app.logger.info(f"User {g.current_user['user_id']} accessing update_session_preferences.")
    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

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
            _update_session_preferences(conn, client_id, preferences)
            return jsonify({"client_id": client_id, "message": "Preferences updated successfully."}), 200
        else:
            return jsonify({"error_code": "API_GW_SESSION_NOT_FOUND", "message": "Session not found."}), 404
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"DB error update_session_preferences for {client_id} ({DATABASE_TYPE}): {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_DB_ERROR_UPDATE_PREFS", "message": "Could not update preferences."}), 500
    finally:
        if conn: conn.close()


# --- Auth Endpoints (PG compatible) ---
@app.route('/auth/register', methods=['POST'])
def register_user():
    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    # Basic validation (can be enhanced)
    if not username or not isinstance(username, str) or not username.strip(): return jsonify({"error_code": "API_GW_AUTH_INVALID_USERNAME", "message": "Username required."}), 400
    if not email or not isinstance(email, str) or not email.strip(): return jsonify({"error_code": "API_GW_AUTH_INVALID_EMAIL", "message": "Email required."}), 400 # Add regex for email
    if not password or not isinstance(password, str) or len(password) < 8: return jsonify({"error_code": "API_GW_AUTH_INVALID_PASSWORD", "message": "Password (min 8 chars) required."}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Check if user exists
        sql_check = "SELECT user_id FROM users WHERE username = %s OR email = %s;" if DATABASE_TYPE == "postgres" else "SELECT user_id FROM users WHERE username = ? OR email = ?;"
        cursor.execute(sql_check, (username, email))
        if cursor.fetchone():
            return jsonify({"error_code": "API_GW_AUTH_USER_EXISTS", "message": "Username or email already exists."}), 409

        user_id = str(uuid.uuid4()) # Ensure UUID is string for PG if column is UUID type
        hashed_pwd = hash_password(password)
        created_at_ts = datetime.utcnow() # datetime obj for PG

        sql_insert = "INSERT INTO users (user_id, username, email, hashed_password, created_at) VALUES (%s, %s, %s, %s, %s);" if DATABASE_TYPE == "postgres" else "INSERT INTO users (user_id, username, email, hashed_password, created_at) VALUES (?, ?, ?, ?, ?);"
        params_insert = (user_id, username, email, hashed_pwd, created_at_ts if DATABASE_TYPE == "postgres" else created_at_ts.isoformat())
        cursor.execute(sql_insert, params_insert)
        conn.commit()
        app.logger.info(f"New user registered: {username}, ID: {user_id}")
        return jsonify({"message": "User registered successfully.", "user_id": user_id}), 201
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"DB error user registration for {username} ({DATABASE_TYPE}): {e}", exc_info=True)
        if conn and DATABASE_TYPE == "postgres": conn.rollback()
        return jsonify({"error_code": "API_GW_AUTH_DB_ERROR_REGISTER", "message": "Could not register user."}), 500
    finally:
        if conn: conn.close()

@app.route('/auth/login', methods=['POST'])
def login_user():
    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

    login_identifier = data.get('login_identifier') # Can be username or email
    password = data.get('password')

    if not login_identifier or not isinstance(login_identifier, str) or not login_identifier.strip(): return jsonify({"error_code": "API_GW_AUTH_INVALID_LOGIN_ID", "message": "Login identifier required."}), 400
    if not password or not isinstance(password, str): return jsonify({"error_code": "API_GW_AUTH_INVALID_PASSWORD_LOGIN", "message": "Password required."}), 400

    conn = None
    try:
        conn = get_db_connection()
        sql = "SELECT user_id, username, hashed_password FROM users WHERE username = %s OR email = %s;" if DATABASE_TYPE == "postgres" else "SELECT user_id, username, hashed_password FROM users WHERE username = ? OR email = ?;"
        cursor = conn.cursor()
        cursor.execute(sql, (login_identifier, login_identifier))
        user_record = cursor.fetchone() # Already a dict if RealDictCursor, or sqlite3.Row

        if not user_record or not check_password(user_record["hashed_password"], password):
            app.logger.warning(f"Failed login attempt for: {login_identifier}")
            return jsonify({"error_code": "API_GW_AUTH_INVALID_CREDENTIALS", "message": "Invalid credentials."}), 401

        user_id = str(user_record["user_id"]) # Ensure UUID is string if needed by JWT
        username = user_record["username"]
        access_token = generate_jwt(user_id, app.config['SECRET_KEY'])
        if not access_token:
            app.logger.error(f"JWT generation failed for user: {user_id}")
            return jsonify({"error_code": "API_GW_AUTH_JWT_GENERATION_FAILED", "message": "Could not issue token."}), 500

        app.logger.info(f"User '{username}' (ID: {user_id}) logged in.")
        return jsonify({"access_token": access_token, "user_id": user_id, "username": username}), 200
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"Database error during login for {login_identifier} ({DATABASE_TYPE}): {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_AUTH_DB_ERROR_LOGIN", "message": "Login failed due to database issue."}), 500
    finally:
        if conn: conn.close()

# --- Helper to process snippets for signed URLs ---
def _process_snippets_for_signed_urls(snippets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(snippets, list):
        return snippets # Or raise error, or log

    processed_snippets = []
    for snippet in snippets:
        if isinstance(snippet, dict) and snippet.get("image_url") and snippet["image_url"].startswith("gs://"):
            signed_image_url = generate_gcs_signed_url(snippet['image_url'])
            if signed_image_url:
                snippet['image_url'] = signed_image_url
            else:
                app.logger.warning(f"Failed to generate signed URL for image: {snippet['image_url']}. Leaving gs:// URI.")
        processed_snippets.append(snippet)
    return processed_snippets

# --- Snippets Endpoint (Updated for Signed URLs) ---
@app.route('/api/v1/snippets', methods=['GET'])
def get_dynamic_snippets():
    app.logger.info("Request received for /api/v1/snippets (dynamic generation)")
    if not cpoa_landing_snippets_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_SNIPPET_SERVICE_UNAVAILABLE", "message": "Snippet service unavailable."}), 503
    try:
        limit_str = request.args.get('limit', default="6")
        try: limit = int(limit_str); limit = 6 if not (1 <= limit <= 20) else limit
        except ValueError: limit = 6

        cpoa_response = orchestrate_landing_page_snippets(limit=limit) # Assuming CPOA returns list of dicts

        if "error" in cpoa_response:
            # ... (error handling as before)
            return jsonify({"error_code": f"API_GW_CPOA_SNIPPET_ERROR", "message": "Failed to get snippets."}), 500 # Simplified

        snippets_list = cpoa_response.get("snippets", [])
        cpoa_response["snippets"] = _process_snippets_for_signed_urls(snippets_list)

        return jsonify(cpoa_response), 200
    except ImportError: return jsonify({"error_code": "API_GW_CPOA_SNIPPET_MODULE_UNAVAILABLE", "message": "Snippet module unavailable."}), 503
    except Exception as e: app.logger.error(f"Unexpected error in get_dynamic_snippets: {e}", exc_info=True); return jsonify({"error_code": "API_GW_SNIPPETS_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500

# --- Categories Endpoint ---
@app.route('/api/v1/categories', methods=['GET'])
def get_categories_endpoint():
    # ... (no changes needed for GCS signed URLs here)
    app.logger.info("Request received for /api/v1/categories")
    if not cpoa_categories_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_CATEGORY_SERVICE_UNAVAILABLE", "message": "Category service unavailable."}), 503
    try:
        cpoa_response = get_popular_categories()
        if "error" in cpoa_response: # Assuming CPOA might return an error structure
            return jsonify({"error_code": f"API_GW_CPOA_CATEGORY_ERROR_{cpoa_response.get('error', 'CPOA_ERROR').upper()}", "message": "Failed to get categories."}), 500
        return jsonify(cpoa_response), 200
    except ImportError: return jsonify({"error_code": "API_GW_CPOA_CATEGORY_MODULE_UNAVAILABLE", "message": "Category module unavailable."}), 503
    except Exception as e: app.logger.error(f"Unexpected error in /categories: {e}", exc_info=True); return jsonify({"error_code": "API_GW_CATEGORIES_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500


# --- Topic Exploration Endpoint (Updated for Signed URLs) ---
@app.route('/api/v1/topics/explore', methods=['POST'])
@token_required
def explore_topic():
    # ... (request parsing and user_preferences logic as before)
    app.logger.info(f"Authenticated user for explore: {g.current_user['user_id']}. (client_id from payload: {request.get_json(silent=True).get('client_id') if request.is_json else 'N/A'})")
    if not cpoa_exploration_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_EXPLORE_SERVICE_UNAVAILABLE", "message": "Exploration service unavailable."}), 503

    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

    current_topic_id = data.get("current_topic_id")
    keywords = data.get("keywords")
    depth_mode = data.get("depth_mode", "deeper") # Default value
    client_id = data.get("client_id") # Optional client_id

    # Input validation
    if not current_topic_id and not keywords: return jsonify({"error_code": "API_GW_EXPLORE_INPUT_REQUIRED", "message": "current_topic_id or keywords required."}), 400
    # ... (other validations for keywords, topic_id, depth_mode, client_id as before)

    user_preferences = None
    if client_id:
        # ... (fetch user_preferences as before) ...
        pass

    try:
        cpoa_response_list = orchestrate_topic_exploration(current_topic_id=current_topic_id, keywords=keywords, depth_mode=depth_mode, user_preferences=user_preferences)

        # Assuming cpoa_response_list is a list of snippet dicts or an error dict from CPOA
        if isinstance(cpoa_response_list, dict) and "error" in cpoa_response_list:
            # ... (error handling as before)
            return jsonify({"error_code": "API_GW_CPOA_EXPLORE_ERROR", "message": "Exploration failed."}), 500

        processed_response = _process_snippets_for_signed_urls(cpoa_response_list)
        return jsonify({"explored_topics": processed_response}), 200

    except ImportError: return jsonify({"error_code": "API_GW_CPOA_EXPLORE_MODULE_UNAVAILABLE_RUNTIME", "message": "Exploration module unavailable."}), 503
    except ValueError as ve: return jsonify({"error_code": "API_GW_EXPLORE_INVALID_INPUT_OR_STATE", "message": str(ve)}), 400 # CPOA might raise ValueError
    except Exception as e: app.logger.error(f"Unexpected error in /explore: {e}", exc_info=True); return jsonify({"error_code": "API_GW_EXPLORE_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500

# --- Search Endpoint (Updated for Signed URLs) ---
@app.route('/api/v1/search/podcasts', methods=['POST'])
@token_required
def search_podcasts_endpoint():
    # ... (request parsing and user_preferences logic as before)
    app.logger.info(f"Authenticated user for search: {g.current_user['user_id']}. (client_id from payload: {request.get_json(silent=True).get('client_id') if request.is_json else 'N/A'})")
    if not cpoa_search_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_SEARCH_SERVICE_UNAVAILABLE", "message": "Search service unavailable."}), 503

    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

    query = data.get("query")
    if not query or not isinstance(query, str) or not query.strip(): return jsonify({"error_code": "API_GW_SEARCH_QUERY_INVALID", "message": "Query required."}), 400

    client_id = data.get("client_id") # Optional
    # ... (client_id validation and user_preferences fetching as before) ...
    user_preferences = None # Placeholder for brevity

    try:
        cpoa_search_response_dict = orchestrate_search_results_generation(query=query, user_preferences=user_preferences)

        if "error" in cpoa_search_response_dict:
            # ... (error handling as before)
            return jsonify({"error_code": "API_GW_CPOA_SEARCH_ERROR", "message": "Search failed."}), 500

        search_results_list = cpoa_search_response_dict.get("search_results", [])
        cpoa_search_response_dict["search_results"] = _process_snippets_for_signed_urls(search_results_list)

        return jsonify(cpoa_search_response_dict), 200
    except ImportError: return jsonify({"error_code": "API_GW_CPOA_SEARCH_MODULE_UNAVAILABLE", "message": "Search module unavailable."}), 503
    except Exception as e: app.logger.error(f"Unexpected error in /search: {e}", exc_info=True); return jsonify({"error_code": "API_GW_SEARCH_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500


# --- Podcast Generation Endpoint (No direct changes for GCS signed URLs here, CPOA handles GCS URIs) ---
@app.route('/api/v1/podcasts', methods=['POST'])
@token_required
def create_podcast_generation_task():
    # ... (logic remains the same, as CPOA now returns GCS URIs in final_audio_filepath)
    # The audio_url construction `f"/api/v1/podcasts/{podcast_id}/audio.mp3"` is correct,
    # as the GET endpoint for that URL will handle the GCS redirection.
    app.logger.info(f"Authenticated user for podcast creation: {g.current_user['user_id']}. (client_id: {request.get_json(silent=True).get('client_id') if request.is_json else 'N/A'})")
    if not cpoa_podcast_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_PODCAST_SERVICE_UNAVAILABLE", "message": "Podcast service unavailable."}), 503

    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

    topic = data.get('topic')
    # ... (rest of input validation and user_preferences fetching logic as before) ...
    if not topic or not isinstance(topic, str) or not topic.strip(): return jsonify({"error_code": "API_GW_PODCAST_TOPIC_INVALID", "message": "Topic required."}), 400
    voice_params_from_request = data.get('voice_params') # Optional
    client_id_from_request = data.get('client_id') # Optional
    test_scenarios_from_request = data.get('test_scenarios') # Optional
    user_preferences = None # Placeholder for user prefs logic

    try:
        podcast_id = str(uuid.uuid4())
        task_created_timestamp = datetime.utcnow()
        conn_task = None
        try:
            conn_task = get_db_connection()
            cursor = conn_task.cursor()
            # Prepare tts_settings_used for JSONB or TEXT
            tts_settings_to_save = None
            if voice_params_from_request:
                tts_settings_to_save = json.dumps(voice_params_from_request) if DATABASE_TYPE == "sqlite" else voice_params_from_request

            sql_insert_task = """
                INSERT INTO podcasts (podcast_id, topic, cpoa_status, task_created_timestamp, last_updated_timestamp, tts_settings_used)
                VALUES (%s, %s, %s, %s, %s, %s);
            """ if DATABASE_TYPE == "postgres" else """
                INSERT INTO podcasts (podcast_id, topic, cpoa_status, task_created_timestamp, last_updated_timestamp, tts_settings_used)
                VALUES (?, ?, ?, ?, ?, ?);
            """
            params_insert_task = (
                podcast_id, topic, "pending_api_gateway",
                task_created_timestamp if DATABASE_TYPE == "postgres" else task_created_timestamp.isoformat(),
                task_created_timestamp if DATABASE_TYPE == "postgres" else task_created_timestamp.isoformat(),
                tts_settings_to_save
            )
            cursor.execute(sql_insert_task, params_insert_task)
            conn_task.commit()
        except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_task_db:
            app.logger.error(f"DB error creating podcast task {podcast_id} ({DATABASE_TYPE}): {e_task_db}", exc_info=True)
            if conn_task and DATABASE_TYPE == "postgres": conn_task.rollback()
            return jsonify({"error_code": "API_GW_PODCAST_DB_ERROR_CREATE_TASK", "message": "Failed to create podcast task record."}), 500
        finally:
            if conn_task: conn_task.close()

        # CPOA call - db_path is now handled by CPOA itself based on its env config
        cpoa_kwargs = {"topic": topic, "task_id": podcast_id,
                       "voice_params_input": voice_params_from_request,
                       "user_preferences": user_preferences,
                       "test_scenarios": test_scenarios_from_request}
        if client_id_from_request: cpoa_kwargs["client_id"] = client_id_from_request

        cpoa_result = orchestrate_podcast_generation(**cpoa_kwargs)

        # Update DB with CPOA result (status, error, GCS path, etc.) - This part might be complex
        # For now, assume CPOA's own DB update is sufficient, or API Gateway does a final update here.
        # The response payload construction below relies on CPOA result.

        final_cpoa_status = cpoa_result.get("status", "unknown_cpoa_status")
        response_payload = {"podcast_id": podcast_id, "topic": topic, "generation_status": final_cpoa_status, "details": cpoa_result}
        http_status_code = 201 # Accepted or Created

        if final_cpoa_status.startswith("failed"):
            # ... (error response formatting as before)
            http_status_code = 502 # Or 500
        elif final_cpoa_status.startswith("completed_with_"):
            # ... (warning/partial success response formatting as before)
            http_status_code = 200 # OK
        else: # Success
            if cpoa_result.get("final_audio_details", {}).get("audio_filepath"): # This should be GCS URI
                response_payload["audio_url"] = f"/api/v1/podcasts/{podcast_id}/audio.mp3" # This URL will handle redirection

        return jsonify(response_payload), http_status_code
    except ImportError: return jsonify({"error_code": "API_GW_CPOA_PODCAST_MODULE_UNAVAILABLE", "message": "Podcast module unavailable."}), 503
    except Exception as e: app.logger.error(f"Unexpected error in create_podcast_generation_task: {e}", exc_info=True); return jsonify({"error_code": "API_GW_PODCAST_CREATE_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500


# --- List All Podcasts Endpoint (No direct changes for GCS) ---
@app.route('/api/v1/podcasts', methods=['GET'])
def list_podcasts():
    # ... (logic remains the same, audio_url construction is fine)
    try:
        # Pagination
        page_str = request.args.get('page', default="1")
        per_page_str = request.args.get('per_page', default="10")
        try: page = int(page_str); page = max(1, page)
        except ValueError: page = 1
        try: per_page = int(per_page_str); per_page = max(1, min(100, per_page)) # Limit per_page
        except ValueError: per_page = 10
        offset = (page - 1) * per_page

        conn = get_db_connection()
        cursor = conn.cursor()

        # Get total count
        sql_count = "SELECT COUNT(*) AS total FROM podcasts;"
        cursor.execute(sql_count)
        total_podcasts = cursor.fetchone()['total']

        # Get paginated results
        sql_select = """
            SELECT podcast_id, topic, task_created_timestamp, cpoa_status, final_audio_filepath
            FROM podcasts ORDER BY task_created_timestamp DESC
            LIMIT %s OFFSET %s;
        """ if DATABASE_TYPE == "postgres" else """
            SELECT podcast_id, topic, task_created_timestamp, cpoa_status, final_audio_filepath
            FROM podcasts ORDER BY task_created_timestamp DESC
            LIMIT ? OFFSET ?;
        """
        cursor.execute(sql_select, (per_page, offset))
        podcasts_rows = cursor.fetchall() # List of dicts (RealDictCursor) or sqlite3.Row
        conn.close()

        podcasts_list = [
            {
                "podcast_id": str(r["podcast_id"]), # Ensure UUID is string
                "topic": r["topic"],
                "task_created_timestamp": r["task_created_timestamp"].isoformat() if isinstance(r["task_created_timestamp"], datetime) else r["task_created_timestamp"],
                "status": r["cpoa_status"],
                "audio_url": f"/api/v1/podcasts/{str(r['podcast_id'])}/audio.mp3" if r["final_audio_filepath"] else None
            } for r in podcasts_rows
        ]
        total_pages = (total_podcasts + per_page - 1) // per_page if total_podcasts > 0 else 0
        return jsonify({"podcasts": podcasts_list, "page": page, "per_page": per_page, "total_podcasts": total_podcasts, "total_pages": total_pages}), 200
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"DB error listing podcasts ({DATABASE_TYPE}): {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_PODCAST_DB_ERROR_LIST", "message": "Could not list podcasts."}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error listing podcasts: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_PODCAST_LIST_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500

# --- Get Specific Podcast Details Endpoint (No direct changes for GCS) ---
@app.route('/api/v1/podcasts/<string:podcast_id>', methods=['GET'])
def get_podcast_details(podcast_id: str):
    # ... (logic remains the same, audio_url construction is fine)
    # Ensure podcast_id is validated as UUID if using PG with UUID type
    try: uuid.UUID(podcast_id) # Validate if it's a UUID string
    except ValueError: return jsonify({"error_code": "API_GW_PODCAST_INVALID_ID_FORMAT", "message": "Invalid podcast ID format."}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        sql = "SELECT * FROM podcasts WHERE podcast_id = %s;" if DATABASE_TYPE == "postgres" else "SELECT * FROM podcasts WHERE podcast_id = ?;"
        cursor.execute(sql, (podcast_id,))
        row = cursor.fetchone() # dict or sqlite3.Row
        conn.close()

        if not row: return jsonify({"error_code": "API_GW_PODCAST_NOT_FOUND", "message": "Podcast not found."}), 404

        podcast_details = dict(row) # Convert sqlite3.Row to dict if necessary

        # Process JSONB fields (PG already returns them as dict/list with RealDictCursor)
        if DATABASE_TYPE == "sqlite": # Manual JSON parsing for SQLite
            try: podcast_details["cpoa_full_orchestration_log"] = json.loads(row["cpoa_full_orchestration_log"]) if row["cpoa_full_orchestration_log"] else []
            except (json.JSONDecodeError, TypeError): podcast_details["cpoa_full_orchestration_log"] = [{"error": "log parsing failed"}]
            try: podcast_details["tts_settings_used"] = json.loads(row["tts_settings_used"]) if row["tts_settings_used"] else {}
            except (json.JSONDecodeError, TypeError): podcast_details["tts_settings_used"] = {"error": "tts settings parsing failed"}

        # Ensure timestamps are ISO format strings
        for ts_key in ["task_created_timestamp", "last_updated_timestamp"]:
            if ts_key in podcast_details and isinstance(podcast_details[ts_key], datetime):
                podcast_details[ts_key] = podcast_details[ts_key].isoformat()

        podcast_details["podcast_id"] = str(podcast_details["podcast_id"]) # Ensure UUID is string

        if podcast_details.get("final_audio_filepath"):
            podcast_details["audio_url"] = f"/api/v1/podcasts/{podcast_id}/audio.mp3"
        else: podcast_details["audio_url"] = None

        return jsonify(podcast_details), 200
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"DB error get_podcast_details for {podcast_id} ({DATABASE_TYPE}): {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_PODCAST_DB_ERROR_DETAILS", "message": "Could not retrieve podcast details."}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error get_podcast_details for {podcast_id}: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_PODCAST_DETAILS_UNEXPECTED_ERROR", "message": "Unexpected error."}), 500

# --- Serve Podcast Audio Endpoint (Updated for GCS Signed URL Redirect) ---
@app.route('/api/v1/podcasts/<string:podcast_id>/audio.mp3', methods=['GET'])
def serve_podcast_audio(podcast_id: str):
    try: uuid.UUID(podcast_id) # Validate format
    except ValueError: return jsonify({"error_code": "API_GW_AUDIO_INVALID_ID_FORMAT", "message": "Invalid podcast ID format for audio."}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        sql = "SELECT final_audio_filepath FROM podcasts WHERE podcast_id = %s;" if DATABASE_TYPE == "postgres" else "SELECT final_audio_filepath FROM podcasts WHERE podcast_id = ?;"
        cursor.execute(sql, (podcast_id,))
        row = cursor.fetchone()
        conn.close()

        if not row or not row.get("final_audio_filepath"): # .get for RealDictRow
            return jsonify({"error_code": "API_GW_AUDIO_NOT_FOUND_DB", "message": "Audio not found or not generated."}), 404

        gcs_audio_uri = row["final_audio_filepath"]

        if not gcs_audio_uri.startswith("gs://"):
            app.logger.error(f"Audio path for {podcast_id} is not a GCS URI: {gcs_audio_uri}. Cannot generate signed URL.")
            # Fallback to old send_file logic if it's a local path and exists (for backward compatibility or local dev)
            if os.path.exists(gcs_audio_uri): # Check if it's an existing local file
                 app.logger.warning(f"Serving audio for {podcast_id} from local path: {gcs_audio_uri} (legacy mode).")
                 mimetype = "audio/mpeg"
                 if gcs_audio_uri.lower().endswith(".wav"): mimetype = "audio/wav"
                 elif gcs_audio_uri.lower().endswith(".ogg"): mimetype = "audio/ogg"
                 return send_file(gcs_audio_uri, mimetype=mimetype)
            return jsonify({"error_code": "API_GW_AUDIO_INVALID_PATH", "message": "Audio path is not a GCS URI and local file not found."}), 500

        signed_audio_url = generate_gcs_signed_url(gcs_audio_uri, expiration_minutes=60) # Longer expiry for audio

        if signed_audio_url:
            app.logger.info(f"Redirecting to signed GCS URL for audio of podcast {podcast_id}.")
            return redirect(signed_audio_url, code=302)
        else:
            app.logger.error(f"Failed to generate signed URL for GCS audio URI: {gcs_audio_uri} for podcast {podcast_id}")
            return jsonify({"error_code": "API_GW_AUDIO_SIGNED_URL_FAILURE", "message": "Could not generate secure access URL for audio."}), 500

    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"DB error serve_podcast_audio for {podcast_id} ({DATABASE_TYPE}): {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_AUDIO_DB_ERROR", "message": "Could not serve audio due to DB error."}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error serve_podcast_audio for {podcast_id}: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_AUDIO_UNEXPECTED_ERROR", "message": "Unexpected error serving audio."}), 500

# --- Internal Endpoints ---
@app.route('/api/v1/internal/media_access_url', methods=['GET'])
def get_internal_media_access_url():
    """
    Internal endpoint for services like ASF to obtain a signed URL for a GCS resource.
    Query parameter: gcs_uri (e.g., gs://bucket/object)
    """
    gcs_uri = request.args.get('gcs_uri')
    app.logger.info(f"Internal request for media_access_url. GCS URI: {gcs_uri}")

    if not gcs_uri:
        app.logger.warning("Missing gcs_uri query parameter for internal media_access_url.")
        return jsonify({"error_code": "MISSING_GCS_URI", "message": "gcs_uri query parameter is required."}), 400

    if not isinstance(gcs_uri, str) or not gcs_uri.startswith("gs://"):
        app.logger.warning(f"Invalid GCS URI format received: {gcs_uri}")
        return jsonify({"error_code": "INVALID_GCS_URI_FORMAT", "message": "gcs_uri must be a non-empty string starting with gs://"}), 400

    try:
        # Using a shorter expiration for URLs intended for immediate internal use by another service
        signed_url = generate_gcs_signed_url(gcs_uri, expiration_minutes=10)

        if signed_url:
            app.logger.info(f"Successfully generated signed URL for GCS URI: {gcs_uri}")
            return jsonify({"signed_url": signed_url}), 200
        else:
            # generate_gcs_signed_url already logs detailed errors
            app.logger.error(f"Failed to generate signed URL for GCS URI: {gcs_uri} (helper returned None).")
            return jsonify({"error_code": "SIGNED_URL_GENERATION_FAILED", "message": "Failed to generate signed URL for the GCS resource."}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error in get_internal_media_access_url for GCS URI {gcs_uri}: {e}", exc_info=True)
        return jsonify({"error_code": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred while processing the request."}), 500

# --- Main Block ---
if __name__ == '__main__':
    init_db() # Initialize DB on startup
    host = os.getenv("API_GW_HOST", "0.0.0.0")
    port = int(os.getenv("API_GW_PORT", "5001"))
    debug_mode = os.getenv("API_GW_DEBUG_MODE", "True").lower() == "true" # FLASK_DEBUG can also be used

    app.logger.info(f"Starting API Gateway: Host={host}, Port={port}, DebugMode={debug_mode}")
    app.run(host=host, port=port, debug=debug_mode, use_reloader=False) # use_reloader=False for stability with background tasks if any
