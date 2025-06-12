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
import re # For email validation in /subscribe
from werkzeug.security import generate_password_hash, check_password_hash
from google.cloud import storage # Added for GCS
# from google.oauth2 import service_account # Not strictly needed if using ADC
import logging # Added for JSON logging

# --- Path Setup for CPOA Import ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
project_root = os.path.dirname(parent_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv()

# --- Logging Setup ---
# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="api-gateway"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        # Ensure workflow_id and task_id are present, defaulting to N/A if not
        record.workflow_id = getattr(record, 'workflow_id', 'N/A')
        record.task_id = getattr(record, 'task_id', 'N/A')
        return True

def setup_json_logging():
    # Configure root logger
    root_logger = logging.getLogger()
    if root_logger.hasHandlers(): # Clear existing handlers from other modules if any
        root_logger.handlers.clear()

    logHandler = logging.StreamHandler()

    # Add the service_name filter to the handler
    service_filter = ServiceNameFilter("api-gateway")
    logHandler.addFilter(service_filter)

    # Format includes common fields, service_name, and placeholders for workflow/task IDs
    # Standard formatter, fields like levelname, name, asctime are standard
    # Custom fields service_name, workflow_id, task_id are added by ServiceNameFilter
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s %(workflow_id)s %(task_id)s"
    )
    logHandler.setFormatter(formatter)

    root_logger.addHandler(logHandler)
    root_logger.setLevel(logging.INFO)

    # Test log
    initial_logger = logging.getLogger(__name__) # Use a logger for this specific module
    initial_logger.info("Standard logging configured for API Gateway.")

setup_json_logging() # Call early to configure logging

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

-- Schema for CPOA State Management --
CREATE TABLE IF NOT EXISTS workflow_instances (
    workflow_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    trigger_event_type VARCHAR(255) NOT NULL,
    trigger_event_details_json JSONB,
    overall_status VARCHAR(50) NOT NULL,
    start_timestamp TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    end_timestamp TIMESTAMPTZ,
    last_updated_timestamp TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    context_data_json JSONB,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_workflow_user_id ON workflow_instances (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_workflow_status ON workflow_instances (overall_status);
CREATE INDEX IF NOT EXISTS idx_workflow_start_time ON workflow_instances (start_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_trigger_event_type ON workflow_instances (trigger_event_type);

CREATE TABLE IF NOT EXISTS task_instances (
    task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES workflow_instances(workflow_id) ON DELETE CASCADE,
    agent_name VARCHAR(255) NOT NULL,
    task_order INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL,
    input_params_json JSONB,
    output_result_summary_json JSONB,
    error_details_json JSONB,
    start_timestamp TIMESTAMPTZ,
    end_timestamp TIMESTAMPTZ,
    last_updated_timestamp TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    retry_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_task_workflow_id ON task_instances (workflow_id);
CREATE INDEX IF NOT EXISTS idx_task_agent_name ON task_instances (agent_name);
CREATE INDEX IF NOT EXISTS idx_task_status ON task_instances (status);
CREATE INDEX IF NOT EXISTS idx_task_order ON task_instances (workflow_id, task_order);

CREATE TABLE IF NOT EXISTS subscribers (
    email VARCHAR(255) PRIMARY KEY,
    subscribed_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
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
                cursor_factory=RealDictCursor
            )
            return conn
        except psycopg2.Error as e:
            log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
            log_func(f"Unable to connect to PostgreSQL: {e}")
            raise ConnectionError(f"PostgreSQL connection failed: {e}") from e
    else:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        current_tables_to_check = ["podcasts", "topics_snippets", "generated_scripts", "user_sessions", "users", "workflow_instances", "task_instances", "subscribers"]

        if DATABASE_TYPE == "postgres":
            for table_name in current_tables_to_check:
                cursor.execute(f"SELECT to_regclass('public.{table_name}');")
                result = cursor.fetchone()
                if not result or result[0] is None:
                    app.logger.info(f"Table '{table_name}' not found in PostgreSQL. It will be created as per DB_SCHEMA_SQL.")
                else:
                    app.logger.info(f"Table '{table_name}' already exists or was just checked in PostgreSQL.")
        else:
            for table_name in current_tables_to_check:
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
                if not cursor.fetchone():
                    app.logger.info(f"Table '{table_name}' not found in SQLite. It will be created as per DB_SCHEMA_SQL.")
                else:
                    app.logger.info(f"Table '{table_name}' already exists or was just checked in SQLite.")

        if DATABASE_TYPE == "postgres":
            cursor.execute(DB_SCHEMA_SQL)
        else:
            cursor.executescript(DB_SCHEMA_SQL)

        conn.commit()
        app.logger.info(f"Database initialization processed. Tables ensured for {DATABASE_TYPE}: {', '.join(current_tables_to_check)}.")
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
        log_func(f"Database initialization error ({DATABASE_TYPE}): {e}")
    except Exception as e_unexp:
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
        configured_bucket = os.getenv("GCS_BUCKET_NAME")
        if configured_bucket and bucket_name != configured_bucket:
            app.logger.warning(f"GCS URI bucket '{bucket_name}' does not match configured bucket '{configured_bucket}'. Proceeding with URI's bucket.")
        storage_client = storage.Client()
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

# --- Session Helper Functions ---
def _get_session(db_conn, session_id: str) -> Optional[Dict[str, Any]]:
    cursor = db_conn.cursor()
    sql = "SELECT * FROM user_sessions WHERE session_id = %s;" if DATABASE_TYPE == "postgres" else "SELECT * FROM user_sessions WHERE session_id = ?;"
    cursor.execute(sql, (session_id,))
    row = cursor.fetchone()
    return dict(row) if row else None

def _create_session(db_conn, session_id: str, preferences: Optional[dict] = None) -> None:
    now_ts = datetime.utcnow()
    prefs_data = preferences if preferences else {}
    try:
        cursor = db_conn.cursor()
        if DATABASE_TYPE == "postgres":
            sql = "INSERT INTO user_sessions (session_id, created_timestamp, last_seen_timestamp, preferences_json) VALUES (%s, %s, %s, %s) ON CONFLICT (session_id) DO NOTHING;"
            cursor.execute(sql, (session_id, now_ts, now_ts, json.dumps(prefs_data)))
        else:
            prefs_json_str = json.dumps(prefs_data)
            sql = "INSERT OR IGNORE INTO user_sessions (session_id, created_timestamp, last_seen_timestamp, preferences_json) VALUES (?, ?, ?, ?);"
            cursor.execute(sql, (session_id, now_ts.isoformat(), now_ts.isoformat(), prefs_json_str))
        db_conn.commit()
        app.logger.info(f"Session created or ignored for session_id: {session_id}")
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"Failed to create session {session_id} ({DATABASE_TYPE}): {e}")
        if DATABASE_TYPE == "postgres" and conn: conn.rollback()
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
        if DATABASE_TYPE == "postgres" and conn: conn.rollback()

def _update_session_preferences(db_conn, session_id: str, preferences: dict) -> None:
    now_ts = datetime.utcnow()
    prefs_data = preferences
    try:
        cursor = db_conn.cursor()
        if DATABASE_TYPE == "postgres":
            sql = "UPDATE user_sessions SET preferences_json = %s, last_seen_timestamp = %s WHERE session_id = %s;"
            cursor.execute(sql, (json.dumps(prefs_data), now_ts, session_id))
        else:
            prefs_json_str = json.dumps(prefs_data)
            sql = "UPDATE user_sessions SET preferences_json = ?, last_seen_timestamp = ? WHERE session_id = ?;"
            cursor.execute(sql, (prefs_json_str, now_ts.isoformat(), session_id))
        db_conn.commit()
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"Failed to update preferences for session {session_id} ({DATABASE_TYPE}): {e}")
        if DATABASE_TYPE == "postgres" and conn: conn.rollback()
        raise

# --- User Helper Functions ---
def _get_user_by_id(user_id_str: str) -> Optional[Dict[str, Any]]:
    if not user_id_str: return None
    try: uuid.UUID(user_id_str)
    except ValueError:
        app.logger.warning(f"_get_user_by_id: Invalid UUID format for user_id: {user_id_str}")
        return None
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        sql = "SELECT * FROM users WHERE user_id = %s;" if DATABASE_TYPE == "postgres" else "SELECT * FROM users WHERE user_id = ?;"
        cursor.execute(sql, (user_id_str,))
        user_row = cursor.fetchone()
        if user_row and not isinstance(user_row, dict) and hasattr(user_row, 'keys'):
             user_row = dict(zip(user_row.keys(), user_row))
        return user_row
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e:
        app.logger.error(f"Database error fetching user by ID {user_id_str} ({DATABASE_TYPE}): {e}", exc_info=True)
        return None
    finally:
        if conn: conn.close()

# --- CPOA Import ---
# ... (CPOA import logic as before) ...
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
# ... (rest of CPOA import logic)

# --- Flask App Initialization & Config ---
app = Flask(__name__)
# Ensure Flask app's logger uses the configured root logger level
app.logger.setLevel(logging.INFO)
# Note: Flask's app.logger usually propagates to the root logger by default,
# so handlers added to root_logger should apply unless explicitly overridden on app.logger.
# If Flask's default handlers were still present and not cleared from app.logger,
# you might get duplicate logs. Clearing root_logger.handlers helps prevent this.

app.logger.info("API Gateway Flask app initialized and logger configured to use root settings.")


# --- Flask App Configuration ---
app.config['SECRET_KEY'] = os.getenv('API_GATEWAY_FLASK_SECRET_KEY', 'a_default_fallback_secret_key_for_dev')
app.config['JWT_EXPIRATION_DAYS'] = int(os.getenv('API_GATEWAY_JWT_EXPIRATION_DAYS', '7'))
if app.config['SECRET_KEY'] == 'a_default_fallback_secret_key_for_dev':
    app.logger.warning("Using default Flask SECRET_KEY. Please set API_GATEWAY_FLASK_SECRET_KEY for production.")


# --- Auth Helper Functions & Decorator ---
def hash_password(password: str) -> str:
    return generate_password_hash(password)

def check_password(hashed_password: str, password_to_check: str) -> bool:
    return check_password_hash(hashed_password, password_to_check)

def generate_jwt(payload: dict, secret_key: str) -> str:
    return jwt.encode(payload, secret_key, algorithm="HS256")

def decode_jwt(token: str, secret_key: str) -> Optional[dict]:
    try:
        return jwt.decode(token, secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        app.logger.warning("JWT decode error: Token has expired.")
        return None # Or raise specific exception
    except jwt.InvalidTokenError as e:
        app.logger.warning(f"JWT decode error: Invalid token - {e}")
        return None # Or raise specific exception

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

        if not token:
            return jsonify({"error_code": "API_GW_TOKEN_MISSING", "message": "Token is missing!"}), 401

        try:
            data = decode_jwt(token, app.config['SECRET_KEY'])
            if data is None: # decode_jwt now returns None for expired/invalid instead of raising custom
                # This condition might be redundant if decode_jwt raises, but good for clarity
                # Or, if decode_jwt returns specific error objects, handle them here.
                # For now, assuming None means some kind of invalid/expired token.
                app.logger.warning("Token decoding failed (likely expired or invalid as per decode_jwt).")
                # Defaulting to generic invalid token, specific error handled by decode_jwt logging.
                return jsonify({"error_code": "API_GW_TOKEN_INVALID", "message": "Token is invalid or expired."}), 401

            # Ensure essential claims are present (e.g. user_id or session_id depending on token type)
            if 'user_id' not in data and 'session_id' not in data : # Example check
                 app.logger.warning("Token missing required claims (user_id or session_id).")
                 return jsonify({"error_code": "API_GW_TOKEN_CLAIMS_MISSING", "message": "Token missing required information."}), 401

            g.current_user = data # Store decoded JWT payload in g

        # Removed specific jwt exception handling here as decode_jwt now handles and logs them, returning None.
        # except jwt.ExpiredSignatureError:
        #     return jsonify({"error_code": "API_GW_TOKEN_EXPIRED", "message": "Token has expired!"}), 401
        # except jwt.InvalidTokenError:
        #     return jsonify({"error_code": "API_GW_TOKEN_INVALID", "message": "Token is invalid!"}), 401
        except Exception as e_token_unexpected: # Catch any other unexpected error during token processing
            app.logger.error(f"Unexpected error during token validation: {e_token_unexpected}", exc_info=True)
            return jsonify({"error_code": "API_GW_TOKEN_VALIDATION_ERROR", "message": "Error validating token."}), 500

        return f(*args, **kwargs)
    return decorated

# --- Static Frontend & Health Check ---
@app.route('/')
def serve_index():
    return send_from_directory('aethercast/fend', 'index.html')

@app.route('/style.css')
def serve_style():
    return send_from_directory('aethercast/fend', 'style.css')

@app.route('/app.js')
def serve_script():
    return send_from_directory('aethercast/fend', 'app.js')

# --- CPOA Import Check ---
cpoa_podcast_func_imported = callable(getattr(sys.modules.get('aethercast.cpoa.main'), 'orchestrate_podcast_generation', None))
cpoa_snippet_func_imported = callable(getattr(sys.modules.get('aethercast.cpoa.main'), 'orchestrate_snippet_generation', None))
cpoa_exploration_func_imported = callable(getattr(sys.modules.get('aethercast.cpoa.main'), 'orchestrate_topic_exploration', None))
cpoa_search_func_imported = callable(getattr(sys.modules.get('aethercast.cpoa.main'), 'orchestrate_search_results_generation', None))
cpoa_landing_snippets_func_imported = callable(getattr(sys.modules.get('aethercast.cpoa.main'), 'orchestrate_landing_page_snippets', None))
cpoa_categories_func_imported = callable(getattr(sys.modules.get('aethercast.cpoa.main'), 'get_popular_categories', None))

IMPORTS_SUCCESSFUL_ALL_CPOA_FUNCS = all([
    cpoa_podcast_func_imported, cpoa_snippet_func_imported, cpoa_exploration_func_imported,
    cpoa_search_func_imported, cpoa_landing_snippets_func_imported, cpoa_categories_func_imported
])

if not IMPORTS_SUCCESSFUL_ALL_CPOA_FUNCS:
    missing_cpoa_funcs_messages = [
        "CPOA 'orchestrate_podcast_generation' not available." if not cpoa_podcast_func_imported else "",
        "CPOA 'orchestrate_snippet_generation' not available." if not cpoa_snippet_func_imported else "",
        "CPOA 'orchestrate_topic_exploration' not available." if not cpoa_exploration_func_imported else "",
        "CPOA 'orchestrate_search_results_generation' not available." if not cpoa_search_func_imported else "",
        "CPOA 'orchestrate_landing_page_snippets' not available." if not cpoa_landing_snippets_func_imported else "",
        "CPOA 'get_popular_categories' not available." if not cpoa_categories_func_imported else ""
    ]
    full_cpoa_import_error_msg = "API Gateway Warning: One or more CPOA functions failed to import correctly. Related API endpoints may be affected. Details: " + " ".join(filter(None, missing_cpoa_funcs_messages))
    app.logger.error(full_cpoa_import_error_msg)
else:
    app.logger.info("All CPOA core functions imported successfully into API Gateway.")


@app.route('/health', methods=['GET'])
def health_check_endpoint():
    """Provides a simple health check endpoint."""
    # Additional checks could be added here (e.g., DB connectivity)
    return jsonify({"status": "healthy", "service": "API Gateway", "timestamp": datetime.utcnow().isoformat()}), 200

# --- Session Management Endpoints ---
@app.route('/api/v1/session/init', methods=['POST'])
def session_init():
    data = request.get_json()
    client_id_from_request = data.get('client_id') if data else None
    session_id_to_use = None
    conn = None
    try:
        conn = get_db_connection()
        if client_id_from_request:
            try:
                uuid.UUID(client_id_from_request) # Validate format
                existing_session = _get_session(conn, client_id_from_request)
                if existing_session:
                    session_id_to_use = client_id_from_request
                    _touch_session_last_seen(conn, session_id_to_use)
                    app.logger.info(f"Session init: Client provided valid session_id {session_id_to_use}, session exists and touched.")
                else:
                    session_id_to_use = client_id_from_request
                    _create_session(conn, session_id_to_use, preferences=data.get('initial_preferences'))
                    app.logger.info(f"Session init: Client provided session_id {session_id_to_use} which was not found. New session created with this ID.")
            except ValueError:
                app.logger.warning(f"Session init: Client provided invalid format client_id '{client_id_from_request}'. A new ID will be generated.")

        if not session_id_to_use:
            session_id_to_use = str(uuid.uuid4())
            _create_session(conn, session_id_to_use, preferences=data.get('initial_preferences'))
            app.logger.info(f"Session init: No valid client_id provided or found. New session created with ID {session_id_to_use}.")

        final_session_state = _get_session(conn, session_id_to_use)
        if not final_session_state:
            app.logger.error(f"Session init: Critical error - session {session_id_to_use} not found after creation attempt.")
            return jsonify({"error_code": "API_GW_SESSION_INIT_FAILURE", "message": "Failed to initialize or retrieve session."}), 500

        token_payload = {
            'session_id': session_id_to_use,
            'user_id': None,
            'exp': datetime.utcnow() + timedelta(days=app.config.get('JWT_EXPIRATION_DAYS', 7))
        }
        session_token = generate_jwt(token_payload, app.config['SECRET_KEY'])

        return jsonify({
            "message": "Session initialized successfully.",
            "client_id": session_id_to_use,
            "session_token": session_token,
            "preferences": json.loads(final_session_state["preferences_json"]) if final_session_state["preferences_json"] else {}
        }), 200

    except ConnectionError as e_conn:
        app.logger.error(f"Session init: Database connection error: {e_conn}", exc_info=True)
        return jsonify({"error_code": "API_GW_DATABASE_CONNECTION_ERROR", "message": "Database connection error."}), 503
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db:
        app.logger.error(f"Session init: Database error: {e_db}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_DB_ERROR", "message": "Database error during session initialization."}), 500
    except Exception as e:
        app.logger.error(f"Session init: Unexpected error: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_UNEXPECTED_ERROR", "message": "An unexpected error occurred."}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/v1/session/preferences', methods=['GET'])
@token_required
def get_session_preferences():
    session_id_from_token = g.current_user.get('session_id')
    if not session_id_from_token:
        app.logger.warning("get_session_preferences: No session_id found in token.")
        return jsonify({"error_code": "API_GW_INVALID_TOKEN_CLAIMS", "message": "Token does not contain session information."}), 401

    conn = None
    try:
        conn = get_db_connection()
        session_data = _get_session(conn, session_id_from_token)
        if not session_data:
            return jsonify({"error_code": "API_GW_SESSION_NOT_FOUND", "message": "Session not found."}), 404

        _touch_session_last_seen(conn, session_id_from_token)

        preferences = json.loads(session_data["preferences_json"]) if session_data["preferences_json"] else {}
        return jsonify({"client_id": session_id_from_token, "preferences": preferences}), 200
    except ConnectionError as e_conn:
        app.logger.error(f"Get session preferences: Database connection error: {e_conn}", exc_info=True)
        return jsonify({"error_code": "API_GW_DATABASE_CONNECTION_ERROR", "message": "Database connection error."}), 503
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db:
        app.logger.error(f"Get session preferences: Database error: {e_db}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_DB_ERROR", "message": "Database error retrieving session preferences."}), 500
    except Exception as e:
        app.logger.error(f"Get session preferences: Unexpected error: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_UNEXPECTED_ERROR", "message": "An unexpected error occurred."}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/v1/session/preferences', methods=['PUT'])
@token_required # Activate token requirement
def update_session_preferences_endpoint():
    data = request.get_json()
    if not data:
        return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

    target_client_id = data.get('client_id') # client_id for which preferences are being updated
    preferences = data.get('preferences')

    if not target_client_id or not isinstance(target_client_id, str):
        return jsonify({"error_code": "API_GW_CLIENT_ID_REQUIRED", "message": "'client_id' is required in payload."}), 400
    if preferences is None or not isinstance(preferences, dict):
        return jsonify({"error_code": "API_GW_PREFERENCES_REQUIRED", "message": "'preferences' (object) is required in payload."}), 400

    session_id_from_token = g.current_user.get('session_id')

    if not session_id_from_token:
        app.logger.warning("Update session preferences: No session_id claim found in JWT token.")
        return jsonify({"error_code": "API_GW_INVALID_TOKEN_CLAIMS", "message": "Token does not contain required session information."}), 401

    if session_id_from_token != target_client_id:
        app.logger.warning(f"Update session preferences: Authenticated session_id '{session_id_from_token}' "
                           f"does not match target_client_id '{target_client_id}'. Forbidden.")
        return jsonify({"error_code": "API_GW_FORBIDDEN_SESSION_UPDATE", "message": "Forbidden to update preferences for this client_id."}), 403

    conn = None
    try:
        conn = get_db_connection()
        session_exists = _get_session(conn, target_client_id)

        if not session_exists:
            app.logger.warning(f"Update session preferences: target_client_id {target_client_id} (matching token) does not exist in DB. Update failed.")
            return jsonify({"error_code": "API_GW_SESSION_NOT_FOUND", "message": "Session to update not found."}), 404
        else:
            _update_session_preferences(conn, target_client_id, preferences)
            app.logger.info(f"Update session preferences: Successfully updated for target_client_id {target_client_id}.")
            return jsonify({"message": "Preferences updated successfully.", "client_id": target_client_id, "preferences": preferences}), 200

    except ConnectionError as e_conn:
        app.logger.error(f"Update session preferences: Database connection error: {e_conn}", exc_info=True)
        return jsonify({"error_code": "API_GW_DATABASE_CONNECTION_ERROR", "message": "Database connection error."}), 503
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db:
        app.logger.error(f"Update session preferences: Database error: {e_db}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_DB_ERROR", "message": "Database error updating session preferences."}), 500
    except Exception as e:
        app.logger.error(f"Update session preferences: Unexpected error: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_SESSION_UNEXPECTED_ERROR", "message": "An unexpected error occurred."}), 500
    finally:
        if conn:
            conn.close()

# --- Auth Endpoints ---
@app.route('/api/v1/auth/register', methods=['POST'])
def register_user():
    try:
        data = request.get_json()
        if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400

    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    if not username or not isinstance(username, str) or len(username) < 3:
        return jsonify({"error_code": "API_GW_INVALID_USERNAME", "message": "Username must be at least 3 characters."}), 400
    if not email or not isinstance(email, str) or not re.fullmatch(EMAIL_REGEX, email): # Using EMAIL_REGEX from subscribe
        return jsonify({"error_code": "API_GW_INVALID_EMAIL", "message": "Invalid email format."}), 400
    if not password or not isinstance(password, str) or len(password) < 8:
        return jsonify({"error_code": "API_GW_INVALID_PASSWORD", "message": "Password must be at least 8 characters."}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Check if username or email already exists
        check_sql = "SELECT user_id FROM users WHERE username = %s OR email = %s;" if DATABASE_TYPE == "postgres" else "SELECT user_id FROM users WHERE username = ? OR email = ?;"
        cursor.execute(check_sql, (username, email))
        if cursor.fetchone():
            return jsonify({"error_code": "API_GW_USER_EXISTS", "message": "Username or email already exists."}), 409

        user_id = str(uuid.uuid4())
        hashed_pw = hash_password(password)
        created_at_ts = datetime.utcnow()

        insert_sql = "INSERT INTO users (user_id, username, email, hashed_password, created_at) VALUES (%s, %s, %s, %s, %s);" if DATABASE_TYPE == "postgres" else "INSERT INTO users (user_id, username, email, hashed_password, created_at) VALUES (?, ?, ?, ?, ?);"
        params_insert = (user_id, username, email, hashed_pw, created_at_ts) if DATABASE_TYPE == "postgres" else (user_id, username, email, hashed_pw, created_at_ts.isoformat())

        cursor.execute(insert_sql, params_insert)
        conn.commit()
        app.logger.info(f"User registered successfully: {username} (ID: {user_id})")
        return jsonify({"message": "User registered successfully.", "user_id": user_id}), 201

    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db:
        app.logger.error(f"Register user: Database error for {username}: {e_db}", exc_info=True)
        if conn and DATABASE_TYPE == "postgres": conn.rollback()
        return jsonify({"error_code": "API_GW_REGISTER_DB_ERROR", "message": "Could not register user due to a database issue."}), 500
    except Exception as e_unexp:
        app.logger.error(f"Register user: Unexpected error for {username}: {e_unexp}", exc_info=True)
        if conn and DATABASE_TYPE == "postgres": conn.rollback()
        return jsonify({"error_code": "API_GW_REGISTER_UNEXPECTED_ERROR", "message": "An unexpected error occurred during registration."}), 500
    finally:
        if conn: conn.close()

@app.route('/api/v1/auth/login', methods=['POST'])
def login_user():
    try:
        data = request.get_json()
        if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400

    identifier = data.get('identifier') # Can be username or email
    password = data.get('password')

    if not identifier or not password:
        return jsonify({"error_code": "API_GW_LOGIN_CREDS_REQUIRED", "message": "Username/email and password required."}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Allow login with username or email
        sql = "SELECT * FROM users WHERE username = %s OR email = %s;" if DATABASE_TYPE == "postgres" else "SELECT * FROM users WHERE username = ? OR email = ?;"
        cursor.execute(sql, (identifier, identifier))
        user_row = cursor.fetchone()

        if not user_row:
            return jsonify({"error_code": "API_GW_LOGIN_INVALID_CREDS", "message": "Invalid username/email or password."}), 401

        user = dict(user_row) # Convert row to dict
        if not check_password(user['hashed_password'], password):
            return jsonify({"error_code": "API_GW_LOGIN_INVALID_CREDS", "message": "Invalid username/email or password."}), 401

        # Password is correct, generate JWT
        # IMPORTANT: For session preference updates to be authorized by this token,
        # the token MUST include the 'session_id' claim.
        # This requires that upon login, a session is either created or retrieved for the user,
        # and that session_id is then included in the JWT.
        # The current logic for session_init creates a session and a token with 'session_id'.
        # A robust login would:
        # 1. Authenticate user.
        # 2. Create/retrieve a session_id for this user (e.g. using _create_session or finding an existing one).
        # 3. Put BOTH user_id AND session_id into the JWT.

        # For now, let's assume a session_id needs to be generated or fetched here.
        # This is a simplified example; a real system might have more complex session handling at login.
        session_id_for_user = str(uuid.uuid4()) # Example: create a new session on login
        _create_session(conn, session_id_for_user) # Create a new session for this login
        app.logger.info(f"User {user['username']} logged in. New session created: {session_id_for_user}")


        token_payload = {
            'user_id': str(user['user_id']),
            'username': user['username'],
            'session_id': session_id_for_user, # CRUCIAL for session preference endpoint
            'exp': datetime.utcnow() + timedelta(days=app.config.get('JWT_EXPIRATION_DAYS', 7))
        }
        auth_token = generate_jwt(token_payload, app.config['SECRET_KEY'])

        app.logger.info(f"User {user['username']} logged in successfully.")
        return jsonify({"message": "Login successful.", "token": auth_token, "user_id": str(user['user_id']), "username": user['username'], "client_id": session_id_for_user}), 200

    except ConnectionError as e_conn:
        app.logger.error(f"Login user: Database connection error: {e_conn}", exc_info=True)
        return jsonify({"error_code": "API_GW_DATABASE_CONNECTION_ERROR", "message": "Database connection error."}), 503
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db:
        app.logger.error(f"Login user: Database error for {identifier}: {e_db}", exc_info=True)
        return jsonify({"error_code": "API_GW_LOGIN_DB_ERROR", "message": "Database error during login."}), 500
    except Exception as e_unexp:
        app.logger.error(f"Login user: Unexpected error for {identifier}: {e_unexp}", exc_info=True)
        return jsonify({"error_code": "API_GW_LOGIN_UNEXPECTED_ERROR", "message": "An unexpected error occurred during login."}), 500
    finally:
        if conn: conn.close()


# --- Helper to process snippets for signed URLs ---
def _process_snippets_for_signed_urls(snippets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    processed = []
    for snippet in snippets:
        if isinstance(snippet, dict) and snippet.get("image_url") and snippet["image_url"].startswith("gs://"):
            signed_image_url = generate_gcs_signed_url(snippet["image_url"])
            if signed_image_url:
                snippet["image_url_signed"] = signed_image_url
            else:
                app.logger.warning(f"Failed to generate signed URL for image: {snippet['image_url']}")
                snippet["image_url_signed"] = None # Or keep original, or error placeholder
        processed.append(snippet)
    return processed

# --- Snippets Endpoint ---
@app.route('/api/v1/snippets', methods=['GET'])
def get_dynamic_snippets():
    app.logger.info("Request received for /api/v1/snippets (dynamic generation)")
    if not cpoa_landing_snippets_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_SNIPPET_SERVICE_UNAVAILABLE", "message": "Snippet service unavailable."}), 503
    try:
        limit_str = request.args.get('limit', default="6")
        try: limit = int(limit_str); limit = 6 if not (1 <= limit <= 20) else limit
        except ValueError: limit = 6
        user_id_for_cpoa = None
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            if token:
                decoded_data = decode_jwt(token, app.config['SECRET_KEY'])
                if decoded_data and 'user_id' in decoded_data:
                    user_exists = _get_user_by_id(decoded_data['user_id'])
                    if user_exists:
                        user_id_for_cpoa = decoded_data['user_id']
                        app.logger.info(f"Optional user_id {user_id_for_cpoa} obtained for get_dynamic_snippets via token.")
                    else:
                        app.logger.info(f"User {decoded_data['user_id']} from optional token not found in DB for get_dynamic_snippets.")
                elif decoded_data is None:
                     app.logger.info("Optional token provided for get_dynamic_snippets was invalid or expired.")
        cpoa_response_dict = orchestrate_landing_page_snippets(limit=limit, user_id=user_id_for_cpoa)
        workflow_id_from_cpoa = cpoa_response_dict.get("workflow_id")
        if cpoa_response_dict.get("error"):
            error_code = str(cpoa_response_dict.get("error", "CPOA_ERROR")).upper()
            error_details = cpoa_response_dict.get("details", "Failed to get snippets from CPOA.")
            app.logger.error(f"CPOA error in get_dynamic_snippets: {error_code} - {error_details}. Workflow ID: {workflow_id_from_cpoa}")
            status_code = 503 if "TDA_" in error_code or "SCA_" in error_code or "WORKFLOW_CREATION_FAILED" in error_code else 500
            return jsonify({"error_code": f"API_GW_CPOA_SNIPPET_ERROR_{error_code}", "message": error_details, "workflow_id": workflow_id_from_cpoa}), status_code
        snippets_list = cpoa_response_dict.get("snippets", [])
        processed_snippets = _process_snippets_for_signed_urls(snippets_list)
        response_payload = {
            "workflow_id": workflow_id_from_cpoa,
            "snippets": processed_snippets,
            "source": cpoa_response_dict.get("source", "generation")
        }
        if "message" in cpoa_response_dict:
            response_payload["message"] = cpoa_response_dict["message"]
        return jsonify(response_payload), 200
    except ImportError:
        app.logger.error("CPOA module import error in get_dynamic_snippets.", exc_info=True)
        return jsonify({"error_code": "API_GW_CPOA_SNIPPET_MODULE_UNAVAILABLE", "message": "Snippet service module unavailable."}), 503
    except Exception as e:
        app.logger.error(f"Unexpected error in get_dynamic_snippets: {e}", exc_info=True)
        wf_id_for_error = locals().get('cpoa_response_dict', {}).get('workflow_id')
        return jsonify({"error_code": "API_GW_SNIPPETS_UNEXPECTED_ERROR", "message": "An unexpected error occurred while fetching snippets.", "workflow_id": wf_id_for_error}), 500

# --- Categories Endpoint ---
@app.route('/api/v1/categories', methods=['GET'])
def get_categories_endpoint():
    app.logger.info("Request received for /api/v1/categories")
    if not cpoa_categories_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_CATEGORIES_SERVICE_UNAVAILABLE", "message": "Categories service unavailable."}), 503
    try:
        categories_data = get_popular_categories() # This is a direct call to CPOA
        return jsonify(categories_data), 200
    except ImportError: # Should be caught by func_imported check, but as safeguard
        app.logger.error("CPOA get_popular_categories import error.", exc_info=True)
        return jsonify({"error_code": "API_GW_CPOA_CATEGORIES_MODULE_UNAVAILABLE", "message": "Categories service module unavailable."}), 503
    except Exception as e:
        app.logger.error(f"Unexpected error in /categories: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_CATEGORIES_UNEXPECTED_ERROR", "message": "An unexpected error occurred."}), 500

# --- Topic Exploration Endpoint ---
@app.route('/api/v1/topics/explore', methods=['POST'])
@token_required
def explore_topic():
    app.logger.info(f"Authenticated user for explore: {g.current_user['user_id']}. (client_id from payload: {request.get_json(silent=True).get('client_id') if request.is_json else 'N/A'})")
    if not cpoa_exploration_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_EXPLORE_SERVICE_UNAVAILABLE", "message": "Exploration service unavailable."}), 503
    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400
    current_topic_id = data.get("current_topic_id")
    keywords = data.get("keywords")
    depth_mode = data.get("depth_mode", "deeper")
    client_id = data.get("client_id") # client_id for fetching preferences, not necessarily for auth here
    if not current_topic_id and not keywords: return jsonify({"error_code": "API_GW_EXPLORE_INPUT_REQUIRED", "message": "current_topic_id or keywords required."}), 400

    user_preferences = None
    if client_id: # If client_id (session_id) is provided, try to fetch its preferences
        conn_prefs = None
        try:
            conn_prefs = get_db_connection()
            session_data = _get_session(conn_prefs, client_id)
            if session_data and session_data["preferences_json"]:
                user_preferences = json.loads(session_data["preferences_json"]) if isinstance(session_data["preferences_json"], str) else session_data["preferences_json"]
            elif not session_data: # If session doesn't exist, create it (idempotent)
                _create_session(conn_prefs, client_id)
                user_preferences = {}
            else: # Session exists but no preferences
                user_preferences = {}
            if session_data: _touch_session_last_seen(conn_prefs, client_id)
        except Exception as e_prefs:
            app.logger.error(f"DB/JSON error for client {client_id} preferences (explore): {e_prefs}"); user_preferences = {}
        finally:
            if conn_prefs: conn_prefs.close()

    try:
        current_user_id = g.current_user['user_id'] # From @token_required
        cpoa_response_dict = orchestrate_topic_exploration(
            current_topic_id=current_topic_id, keywords=keywords, depth_mode=depth_mode,
            user_preferences=user_preferences, user_id=current_user_id
        )
        workflow_id_from_cpoa = cpoa_response_dict.get("workflow_id")
        if cpoa_response_dict.get("error"):
            error_code = str(cpoa_response_dict.get("error", "CPOA_ERROR")).upper()
            error_details = cpoa_response_dict.get("details", "Exploration failed via CPOA.")
            app.logger.error(f"CPOA error in explore_topic: {error_code} - {error_details}. Workflow ID: {workflow_id_from_cpoa}")
            status_code = 503 if "TDA_" in error_code or "SCA_" in error_code or "WORKFLOW_CREATION_FAILED" in error_code else 500
            return jsonify({"error_code": f"API_GW_CPOA_EXPLORE_ERROR_{error_code}", "message": error_details, "workflow_id": workflow_id_from_cpoa}), status_code

        explored_topics_list = cpoa_response_dict.get("explored_topics", [])
        processed_explored_topics = _process_snippets_for_signed_urls(explored_topics_list)
        return jsonify({"workflow_id": workflow_id_from_cpoa, "explored_topics": processed_explored_topics}), 200
    except ImportError:
        app.logger.error("CPOA module import error in explore_topic.", exc_info=True)
        return jsonify({"error_code": "API_GW_CPOA_EXPLORE_MODULE_UNAVAILABLE_RUNTIME", "message": "Exploration module unavailable."}), 503
    except ValueError as ve: # Catch ValueErrors from CPOA's orchestrate_topic_exploration
        app.logger.warning(f"ValueError in explore_topic (likely from CPOA validation): {ve}")
        return jsonify({"error_code": "API_GW_EXPLORE_INVALID_INPUT_OR_STATE", "message": str(ve)}), 400
    except Exception as e:
        app.logger.error(f"Unexpected error in /explore: {e}", exc_info=True)
        wf_id_for_error = locals().get('cpoa_response_dict', {}).get('workflow_id') # Try to get wf_id if possible
        return jsonify({"error_code": "API_GW_EXPLORE_UNEXPECTED_ERROR", "message": "An unexpected error occurred.", "workflow_id": wf_id_for_error}), 500

# --- Search Endpoint ---
@app.route('/api/v1/search/podcasts', methods=['POST'])
@token_required
def search_podcasts_endpoint():
    app.logger.info(f"Authenticated user for search: {g.current_user['user_id']}. (client_id from payload: {request.get_json(silent=True).get('client_id') if request.is_json else 'N/A'})")
    if not cpoa_search_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_SEARCH_SERVICE_UNAVAILABLE", "message": "Search service unavailable."}), 503
    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

    query = data.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        return jsonify({"error_code": "API_GW_SEARCH_QUERY_INVALID", "message": "Query required."}), 400

    client_id = data.get("client_id") # For fetching preferences
    user_preferences = None
    if client_id:
        conn_prefs = None
        try:
            conn_prefs = get_db_connection()
            session_data = _get_session(conn_prefs, client_id)
            if session_data and session_data["preferences_json"]:
                user_preferences = json.loads(session_data["preferences_json"]) if isinstance(session_data["preferences_json"], str) else session_data["preferences_json"]
            elif not session_data: _create_session(conn_prefs, client_id); user_preferences = {}
            else: user_preferences = {}
            if session_data: _touch_session_last_seen(conn_prefs, client_id)
        except Exception as e_prefs: app.logger.error(f"DB/JSON error for client {client_id} preferences (search): {e_prefs}"); user_preferences = {}
        finally:
            if conn_prefs: conn_prefs.close()

    try:
        current_user_id = g.current_user['user_id'] # From @token_required
        cpoa_response_dict = orchestrate_search_results_generation(
            query=query, user_preferences=user_preferences, user_id=current_user_id
        )
        workflow_id_from_cpoa = cpoa_response_dict.get("workflow_id")

        if cpoa_response_dict.get("error"):
            error_code = str(cpoa_response_dict.get("error", "CPOA_SEARCH_ERROR")).upper()
            error_details = cpoa_response_dict.get("details", "Search failed via CPOA.")
            app.logger.error(f"CPOA error in search: {error_code} - {error_details}. Workflow ID: {workflow_id_from_cpoa}")
            status_code = 503 if "TDA_" in error_code or "SCA_" in error_code or "WORKFLOW_CREATION_FAILED" in error_code else 500
            return jsonify({"error_code": f"API_GW_CPOA_SEARCH_ERROR_{error_code}", "message": error_details, "workflow_id": workflow_id_from_cpoa}), status_code

        search_results_list = cpoa_response_dict.get("search_results", [])
        processed_search_results = _process_snippets_for_signed_urls(search_results_list)

        response_payload = {"workflow_id": workflow_id_from_cpoa, "search_results": processed_search_results}
        if "message" in cpoa_response_dict: response_payload["message"] = cpoa_response_dict["message"]

        return jsonify(response_payload), 200
    except ImportError:
        app.logger.error("CPOA module import error in search.", exc_info=True)
        return jsonify({"error_code": "API_GW_CPOA_SEARCH_MODULE_UNAVAILABLE", "message": "Search module unavailable."}), 503
    except Exception as e:
        app.logger.error(f"Unexpected error in /search: {e}", exc_info=True)
        wf_id_for_error = locals().get('cpoa_response_dict', {}).get('workflow_id')
        return jsonify({"error_code": "API_GW_SEARCH_UNEXPECTED_ERROR", "message": "Unexpected error during search.", "workflow_id": wf_id_for_error}), 500

# --- Podcast Generation Endpoint ---
@app.route('/api/v1/podcasts', methods=['POST'])
@token_required
def create_podcast_generation_task():
    app.logger.info(f"Authenticated user for podcast creation: {g.current_user['user_id']}. (client_id: {request.get_json(silent=True).get('client_id') if request.is_json else 'N/A'})")
    if not cpoa_podcast_func_imported:
        return jsonify({"error_code": "API_GW_CPOA_PODCAST_SERVICE_UNAVAILABLE", "message": "Podcast service unavailable."}), 503
    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "API_GW_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "API_GW_PAYLOAD_REQUIRED", "message": "Payload required."}), 400

    topic = data.get('topic')
    if not topic or not isinstance(topic, str) or not topic.strip(): return jsonify({"error_code": "API_GW_PODCAST_TOPIC_INVALID", "message": "Topic required."}), 400

    voice_params_from_request = data.get('voice_params')
    client_id_from_request = data.get('client_id') # For fetching preferences and UI updates
    test_scenarios_from_request = data.get('test_scenarios') # For testing specific error paths

    user_preferences = None
    if client_id_from_request: # If client_id (session_id) is provided, try to fetch its preferences
        conn_prefs = None
        try:
            conn_prefs = get_db_connection()
            session_data = _get_session(conn_prefs, client_id_from_request)
            if session_data and session_data["preferences_json"]:
                user_preferences = json.loads(session_data["preferences_json"]) if isinstance(session_data["preferences_json"], str) else session_data["preferences_json"]
            elif not session_data: _create_session(conn_prefs, client_id_from_request); user_preferences = {} # Create session if not found
            else: user_preferences = {} # Session exists but no preferences
            if session_data: _touch_session_last_seen(conn_prefs, client_id_from_request)
        except Exception as e_prefs: app.logger.error(f"DB/JSON error for client {client_id_from_request} preferences (podcast create): {e_prefs}"); user_preferences = {}
        finally:
            if conn_prefs: conn_prefs.close()

    try:
        podcast_id = str(uuid.uuid4())
        task_created_timestamp = datetime.utcnow()

        # Initial DB entry for the podcast task (legacy table)
        conn_main_db = None
        try:
            conn_main_db = get_db_connection()
            cursor = conn_main_db.cursor()
            sql_insert_podcast = """
                INSERT INTO podcasts (podcast_id, topic, cpoa_status, task_created_timestamp, last_updated_timestamp)
                VALUES (%s, %s, %s, %s, %s)
            """
            params_insert_podcast = (podcast_id, topic, "pending_cpoa_dispatch", task_created_timestamp, task_created_timestamp)
            if DATABASE_TYPE == "sqlite":
                sql_insert_podcast = sql_insert_podcast.replace("%s", "?")
                params_insert_podcast = (podcast_id, topic, "pending_cpoa_dispatch", task_created_timestamp.isoformat(), task_created_timestamp.isoformat())

            cursor.execute(sql_insert_podcast, params_insert_podcast)
            conn_main_db.commit()
            app.logger.info(f"Podcast task {podcast_id} for topic '{topic}' initially saved to DB.")
        except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db_init:
            app.logger.error(f"Failed to make initial DB entry for podcast task {podcast_id}: {e_db_init}", exc_info=True)
            # Proceed with CPOA call but log this DB error. CPOA will also log its own DB interactions.
        finally:
            if conn_main_db: conn_main_db.close()

        current_user_id = g.current_user['user_id'] # From @token_required

        cpoa_kwargs = {
            "topic": topic, "original_task_id": podcast_id, "user_id": current_user_id,
            "voice_params_input": voice_params_from_request, "user_preferences": user_preferences,
            "test_scenarios": test_scenarios_from_request
        }
        if client_id_from_request: cpoa_kwargs["client_id"] = client_id_from_request

        cpoa_result = orchestrate_podcast_generation(**cpoa_kwargs)

        final_cpoa_status = cpoa_result.get("status", "unknown_cpoa_status")
        workflow_id_from_cpoa = cpoa_result.get("workflow_id") # This is the new CPOA state workflow_id

        response_payload = {
            "podcast_id": podcast_id, # original_task_id
            "workflow_id": workflow_id_from_cpoa,
            "topic": topic,
            "generation_status": final_cpoa_status, # Legacy status for direct API response
            "details": cpoa_result # Full CPOA result for debugging/logging
        }

        http_status_code = 201 # Default for accepted/processing
        if final_cpoa_status.startswith("failed") or cpoa_result.get("error_message"):
            error_message = cpoa_result.get("error_message", cpoa_result.get("details", f"Podcast generation failed: {final_cpoa_status}"))
            response_payload["error_code"] = f"API_GW_CPOA_ORCHESTRATION_ERROR_{final_cpoa_status.upper()}"
            response_payload["message"] = error_message
            # Determine appropriate HTTP status code based on CPOA error
            # Example: if CPOA indicates a sub-service request failed (e.g., timeout, service unavailable)
            if "request_exception" in final_cpoa_status or "reported_error" in final_cpoa_status or "WORKFLOW_CREATION_FAILED" in final_cpoa_status.upper() or "timeout" in final_cpoa_status.lower():
                http_status_code = 502 # Bad Gateway (upstream service failure)
            else: # Other CPOA internal errors
                http_status_code = 500 # Internal Server Error
        elif final_cpoa_status == "completed_with_vfa_skipped": # A specific non-failure but not full success
             http_status_code = 200 # OK, but with details indicating skipped part

        return jsonify(response_payload), http_status_code

    except ImportError: # Should be caught by func_imported check, but as safeguard
        app.logger.error("CPOA module import error in create_podcast_generation_task.", exc_info=True)
        return jsonify({"error_code": "API_GW_CPOA_PODCAST_MODULE_UNAVAILABLE", "message": "Podcast module unavailable."}), 503
    except Exception as e:
        app.logger.error(f"Unexpected error in create_podcast_generation_task: {e}", exc_info=True)
        wf_id_for_error = locals().get('cpoa_result', {}).get('workflow_id') # Try to get wf_id if possible
        return jsonify({"error_code": "API_GW_PODCAST_CREATE_UNEXPECTED_ERROR", "message": "Unexpected error.", "workflow_id": wf_id_for_error}), 500

# --- List All Podcasts & Get Specific Podcast Details Endpoints ---
@app.route('/api/v1/podcasts', methods=['GET'])
def list_podcasts():
    # ... (implementation as before) ...
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Fetch relevant fields, including GCS URI for audio
        cursor.execute("SELECT podcast_id, topic, cpoa_status, final_audio_filepath, task_created_timestamp, last_updated_timestamp FROM podcasts ORDER BY task_created_timestamp DESC LIMIT 100;")
        podcasts_raw = cursor.fetchall()

        podcasts_list = []
        for row in podcasts_raw:
            podcast_dict = dict(row) # Convert row to dict if not already (RealDictCursor does this)
            # Generate signed URL for final_audio_filepath if it's a GCS URI
            if podcast_dict.get("final_audio_filepath") and podcast_dict["final_audio_filepath"].startswith("gs://"):
                signed_url = generate_gcs_signed_url(podcast_dict["final_audio_filepath"])
                podcast_dict["audio_url_signed"] = signed_url # Add signed URL to response
            podcasts_list.append(podcast_dict)

        return jsonify({"podcasts": podcasts_list})
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db:
        app.logger.error(f"List podcasts: Database error: {e_db}", exc_info=True)
        return jsonify({"error_code": "API_GW_DB_ERROR_LIST_PODCASTS", "message": "Database error listing podcasts."}), 500
    except Exception as e:
        app.logger.error(f"List podcasts: Unexpected error: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_UNEXPECTED_ERROR_LIST_PODCASTS", "message": "Unexpected error listing podcasts."}), 500
    finally:
        if conn: conn.close()


@app.route('/api/v1/podcasts/<uuid:podcast_id_from_path>', methods=['GET'])
def get_podcast_details(podcast_id_from_path: uuid.UUID):
    # ... (implementation as before) ...
    podcast_id_str = str(podcast_id_from_path)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        sql = "SELECT * FROM podcasts WHERE podcast_id = %s;" if DATABASE_TYPE == "postgres" else "SELECT * FROM podcasts WHERE podcast_id = ?;"
        cursor.execute(sql, (podcast_id_str,))
        podcast_data_raw = cursor.fetchone()

        if not podcast_data_raw:
            return jsonify({"error_code": "API_GW_PODCAST_NOT_FOUND", "message": "Podcast not found."}), 404

        podcast_data = dict(podcast_data_raw) # Convert row to dict

        # Generate signed URL for final_audio_filepath if it's a GCS URI
        if podcast_data.get("final_audio_filepath") and podcast_data["final_audio_filepath"].startswith("gs://"):
            signed_url = generate_gcs_signed_url(podcast_data["final_audio_filepath"])
            podcast_data["audio_url_signed"] = signed_url # Add signed URL

        # Parse JSONB log if present (PostgreSQL)
        if DATABASE_TYPE == "postgres" and 'cpoa_full_orchestration_log' in podcast_data and isinstance(podcast_data['cpoa_full_orchestration_log'], str):
            try: podcast_data['cpoa_full_orchestration_log'] = json.loads(podcast_data['cpoa_full_orchestration_log'])
            except json.JSONDecodeError: app.logger.warning(f"Failed to parse cpoa_full_orchestration_log JSON for podcast {podcast_id_str}")
        # For SQLite, if it was stored as JSON string, it might already be string. If needs parsing, add here.

        return jsonify(podcast_data)
    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db:
        app.logger.error(f"Get podcast details: Database error for {podcast_id_str}: {e_db}", exc_info=True)
        return jsonify({"error_code": "API_GW_DB_ERROR_PODCAST_DETAILS", "message": "Database error fetching podcast details."}), 500
    except Exception as e:
        app.logger.error(f"Get podcast details: Unexpected error for {podcast_id_str}: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_UNEXPECTED_ERROR_PODCAST_DETAILS", "message": "Unexpected error fetching podcast details."}), 500
    finally:
        if conn: conn.close()


# --- Serve Podcast Audio Endpoint ---
@app.route('/api/v1/podcasts/<uuid:podcast_id_from_path>/audio', methods=['GET'])
def serve_podcast_audio(podcast_id_from_path: uuid.UUID):
    # ... (implementation as before, but use signed URL logic) ...
    podcast_id_str = str(podcast_id_from_path)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        sql = "SELECT final_audio_filepath FROM podcasts WHERE podcast_id = %s;" if DATABASE_TYPE == "postgres" else "SELECT final_audio_filepath FROM podcasts WHERE podcast_id = ?;"
        cursor.execute(sql, (podcast_id_str,))
        row = cursor.fetchone()

        if not row or not row.get("final_audio_filepath"):
            return jsonify({"error_code": "API_GW_AUDIO_NOT_FOUND_OR_NO_PATH", "message": "Audio file path not found for this podcast."}), 404

        audio_path = row["final_audio_filepath"]

        if audio_path.startswith("gs://"):
            signed_url = generate_gcs_signed_url(audio_path, expiration_minutes=5) # Short expiration for direct streaming
            if signed_url:
                app.logger.info(f"Redirecting to GCS signed URL for podcast {podcast_id_str} audio.")
                return redirect(signed_url, code=302)
            else:
                app.logger.error(f"Failed to generate signed URL for GCS audio path: {audio_path}")
                return jsonify({"error_code": "API_GW_GCS_SIGNED_URL_FAILURE", "message": "Could not generate secure audio link."}), 500
        else: # Fallback for local file paths (legacy or testing)
            app.logger.warning(f"Serving audio for podcast {podcast_id_str} from local path: {audio_path}. This is not recommended for production.")
            if not os.path.isabs(audio_path): # Ensure path is absolute if local
                # This might need adjustment based on where VFA/ASF actually store files relative to API GW's perspective
                # For Docker, this usually means paths within a shared volume.
                # Assuming audio_path is an absolute path in the container if local.
                app.logger.error(f"Local audio path is not absolute: {audio_path}")
                return jsonify({"error_code": "API_GW_LOCAL_AUDIO_PATH_INVALID", "message": "Invalid local audio path configured."}), 500
            if not os.path.exists(audio_path):
                app.logger.error(f"Local audio file not found at: {audio_path}")
                return jsonify({"error_code": "API_GW_LOCAL_AUDIO_FILE_MISSING", "message": "Audio file not found locally."}), 404

            # Determine mimetype (simple version)
            mimetype = "audio/mpeg" # Default
            if audio_path.lower().endswith(".wav"): mimetype = "audio/wav"
            elif audio_path.lower().endswith(".ogg"): mimetype = "audio/ogg"

            return send_file(audio_path, mimetype=mimetype, as_attachment=False)

    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db:
        app.logger.error(f"Serve audio: Database error for {podcast_id_str}: {e_db}", exc_info=True)
        return jsonify({"error_code": "API_GW_DB_ERROR_SERVE_AUDIO", "message": "Database error serving audio."}), 500
    except Exception as e:
        app.logger.error(f"Serve audio: Unexpected error for {podcast_id_str}: {e}", exc_info=True)
        return jsonify({"error_code": "API_GW_UNEXPECTED_ERROR_SERVE_AUDIO", "message": "Unexpected error serving audio."}), 500
    finally:
        if conn: conn.close()

# --- Internal Endpoints ---
@app.route('/api/v1/internal/media_access_url', methods=['GET'])
@token_required # Protect this internal endpoint as well
def get_internal_media_access_url():
    # This endpoint is intended to be called by other internal services (like ASF)
    # to get a publicly accessible URL (e.g., a GCS signed URL) for a media file.
    # It requires authentication to ensure only trusted internal services can use it.
    # The requesting service's JWT might need specific claims if we want to verify which service it is.

    # For now, g.current_user will contain whatever claims the internal service's token has.
    # We might add a specific 'service_role' or 'service_name' claim to internal tokens.
    app.logger.info(f"Internal media access URL request received. Authenticated entity: {g.current_user}")

    gcs_uri = request.args.get('gcs_uri')
    if not gcs_uri or not gcs_uri.startswith("gs://"):
        return jsonify({"error_code": "API_GW_INVALID_GCS_URI_PARAM", "message": "Valid 'gcs_uri' parameter starting with 'gs://' is required."}), 400

    signed_url = generate_gcs_signed_url(gcs_uri, expiration_minutes=5) # Short-lived for immediate use
    if signed_url:
        app.logger.info(f"Generated signed URL for internal request: {gcs_uri}")
        return jsonify({"gcs_uri": gcs_uri, "signed_url": signed_url}), 200
    else:
        app.logger.error(f"Failed to generate signed URL for internal request: {gcs_uri}")
        return jsonify({"error_code": "API_GW_INTERNAL_SIGNED_URL_FAILURE", "message": "Could not generate secure URL for the GCS resource."}), 500


# --- Subscribe Endpoint (New) ---
EMAIL_REGEX = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"

@app.route('/api/v1/subscribe', methods=['POST'])
def handle_subscribe():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error_code": "SUBSCRIBE_PAYLOAD_REQUIRED", "message": "JSON payload is required."}), 400
    except Exception as e_json:
        app.logger.warning(f"Malformed JSON in /subscribe: {e_json}")
        return jsonify({"error_code": "SUBSCRIBE_MALFORMED_JSON", "message": f"Malformed JSON: {e_json}"}), 400

    email = data.get('email')

    if not email or not isinstance(email, str) or not email.strip():
        return jsonify({"error_code": "SUBSCRIBE_EMAIL_REQUIRED", "message": "Email is required."}), 400

    if not re.fullmatch(EMAIL_REGEX, email):
        return jsonify({"error_code": "SUBSCRIBE_INVALID_EMAIL_FORMAT", "message": "Invalid email format."}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if email exists
        check_sql = "SELECT email FROM subscribers WHERE email = %s;" if DATABASE_TYPE == "postgres" else "SELECT email FROM subscribers WHERE email = ?;"
        cursor.execute(check_sql, (email,))
        if cursor.fetchone():
            app.logger.info(f"Email already subscribed: {email}")
            return jsonify({"error_code": "SUBSCRIBE_EMAIL_EXISTS", "message": "This email is already subscribed."}), 409 # Conflict

        # Insert new subscriber
        insert_sql = "INSERT INTO subscribers (email, subscribed_at) VALUES (%s, current_timestamp);" if DATABASE_TYPE == "postgres" else "INSERT INTO subscribers (email, subscribed_at) VALUES (?, ?);"
        params_insert = (email,) if DATABASE_TYPE == "postgres" else (email, datetime.utcnow().isoformat())

        cursor.execute(insert_sql, params_insert)
        conn.commit()

        app.logger.info(f"New email subscribed: {email}")
        return jsonify({"message": "Successfully subscribed! Thank you."}), 201 # Created

    except (sqlite3.Error, psycopg2.Error if DATABASE_TYPE == "postgres" else sqlite3.Error) as e_db:
        app.logger.error(f"Database error during subscription for {email} ({DATABASE_TYPE}): {e_db}", exc_info=True)
        if conn and DATABASE_TYPE == "postgres": conn.rollback()
        return jsonify({"error_code": "SUBSCRIBE_DB_ERROR", "message": "Could not process subscription due to a database issue."}), 500
    except Exception as e_unexp:
        app.logger.error(f"Unexpected error during subscription for {email}: {e_unexp}", exc_info=True)
        if conn and DATABASE_TYPE == "postgres": conn.rollback()
        return jsonify({"error_code": "SUBSCRIBE_UNEXPECTED_ERROR", "message": "An unexpected error occurred."}), 500
    finally:
        if conn: conn.close()

# --- Main Block ---
if __name__ == '__main__':
    app.logger.info("Starting API Gateway service directly for development.")
    init_db()
    app.run(debug=True, host=os.getenv("API_GATEWAY_HOST", "0.0.0.0"), port=int(os.getenv("API_GATEWAY_PORT", "5001")))
