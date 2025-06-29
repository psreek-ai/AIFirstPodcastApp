import flask
import uuid
import random
import logging
import json
import os
from dotenv import load_dotenv
import requests
from celery import Celery
from celery.result import AsyncResult
import psycopg2 # Added
from psycopg2.extras import RealDictCursor # Added
from datetime import datetime
import time # Added for metric logging

# Load environment variables from .env file
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

from typing import Optional, Dict, Any # For type hinting
from celery import Task # For custom Task class

# Conditional import for psycopg2 (already present, ensure it's used for idempotency)
# PSYCOPG2_AVAILABLE can be set based on successful import if needed by helper functions

# --- Idempotency Constants ---
IDEMPOTENCY_KEY_HEADER = "X-Idempotency-Key"

# --- Celery Configuration ---
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

celery_app = Celery(
    'tda_tasks',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND
)
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)
celery_app.finalize() # Explicitly finalize the app

# --- Logging Setup ---
# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="tda"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

# Initialize Flask app early so app.logger can be configured
app = flask.Flask(__name__)

# Configure JSON logging for the Flask app
def setup_json_logging(flask_app):
    # Clear existing default handlers from Flask app's logger
    flask_app.logger.handlers.clear()

    logHandler = logging.StreamHandler()

    # Add the service_name filter to the handler
    service_filter = ServiceNameFilter("tda") # Service name for TDA
    logHandler.addFilter(service_filter)

    # Use JsonFormatter
    # Ensure python-json-logger is imported
    from python_json_logger import jsonlogger
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s %(task_id)s %(workflow_id)s %(idempotency_key)s"
    )
    logHandler.setFormatter(formatter)

    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    # For the initial log message, we might not have task_id, etc.
    # The filter will add default "N/A" if these fields are missing from the LogRecord.
    # To make this specific log more structured if desired:
    flask_app.logger.info("JSON logging configured for TDA service.", extra={'task_id': 'N/A', 'workflow_id': 'N/A', 'idempotency_key': 'N/A'})

setup_json_logging(app)

# --- Celery Task-Specific Logger Setup ---
# It's good practice for Celery tasks to use a logger that is explicitly configured,
# especially if they might run in different contexts or if we want different formatting/handlers.
# However, if the app.logger is already JSON and globally accessible, tasks can use it directly.
# For consistency with other services and to ensure task-specific context can be easily added,
# we can define a way to get a task-specific logger or ensure app.logger is used with proper `extra`.

# For TDA, since app.logger is now JSON, tasks can continue to use it.
# We need to ensure context (task_id, workflow_id, idempotency_key) is passed via `extra`.

# Example of how a task-specific logger could be set up if needed:
# def get_celery_task_logger(task_name: str):
#     task_logger = logging.getLogger(f"tda.celery.{task_name}")
#     if not task_logger.hasHandlers(): # Configure only once
#         task_logger.handlers.clear()
#         logHandler = logging.StreamHandler()
#         service_filter = ServiceNameFilter("tda") # Re-use or create new
#         logHandler.addFilter(service_filter)
#         formatter = jsonlogger.JsonFormatter(
#             fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s %(task_id)s %(workflow_id)s %(idempotency_key)s"
#         )
#         logHandler.setFormatter(formatter)
#         task_logger.addHandler(logHandler)
#         task_logger.setLevel(logging.INFO) # Or from config
#         task_logger.propagate = False # Avoid double logging if root logger is also configured
#     return task_logger
# In tasks: task_specific_logger = get_celery_task_logger(self.name)
#           task_specific_logger.info("message", extra={...})
# For now, we will ensure app.logger is used with `extra` in tasks.


# --- Application Configuration ---
tda_config = {
    "TDA_NEWS_API_KEY": os.getenv("TDA_NEWS_API_KEY"),
    "TDA_NEWS_API_BASE_URL": os.getenv("TDA_NEWS_API_BASE_URL", "https://newsapi.org/v2/"),
    "TDA_NEWS_API_ENDPOINT": os.getenv("TDA_NEWS_API_ENDPOINT", "everything"),
    "TDA_NEWS_DEFAULT_KEYWORDS": os.getenv("TDA_NEWS_DEFAULT_KEYWORDS", "AI,technology,science").split(','),
    "TDA_NEWS_DEFAULT_LANGUAGE": os.getenv("TDA_NEWS_DEFAULT_LANGUAGE", "en"),
    "USE_REAL_NEWS_API": os.getenv("USE_REAL_NEWS_API", "False").lower() == "true",
    "TDA_NEWS_PAGE_SIZE": int(os.getenv("TDA_NEWS_PAGE_SIZE", "25")),
    "TDA_NEWS_REQUEST_TIMEOUT": int(os.getenv("TDA_NEWS_REQUEST_TIMEOUT", "15")),
    "TDA_NEWS_USER_AGENT": os.getenv("TDA_NEWS_USER_AGENT", "AethercastTopicDiscovery/0.1"),

    # Database Configuration
    # "DATABASE_TYPE": os.getenv("DATABASE_TYPE", "sqlite"), # Removed, TDA now uses PostgreSQL only
    # "SHARED_DATABASE_PATH": os.getenv("SHARED_DATABASE_PATH", "/app/database/aethercast_podcasts.db"), # Removed
    "POSTGRES_HOST": os.getenv("POSTGRES_HOST"),
    "POSTGRES_PORT": os.getenv("POSTGRES_PORT", "5432"),
    "POSTGRES_USER": os.getenv("POSTGRES_USER"),
    "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD"),
    "POSTGRES_DB": os.getenv("POSTGRES_DB"),

    "TDA_HOST": os.getenv("TDA_HOST", os.getenv("FLASK_RUN_HOST", "0.0.0.0")),
    "TDA_PORT": int(os.getenv("TDA_PORT", os.getenv("FLASK_RUN_PORT", "5000"))),
    # "TDA_DEBUG_MODE": os.getenv("TDA_DEBUG_MODE", "True").lower() == "true", # To be replaced by FLASK_DEBUG

    # Idempotency related configurations
    "TDA_IDEMPOTENCY_STATUS_PROCESSING": os.getenv('TDA_IDEMPOTENCY_STATUS_PROCESSING', 'processing'),
    "TDA_IDEMPOTENCY_STATUS_COMPLETED": os.getenv('TDA_IDEMPOTENCY_STATUS_COMPLETED', 'completed'),
    "TDA_IDEMPOTENCY_STATUS_FAILED": os.getenv('TDA_IDEMPOTENCY_STATUS_FAILED', 'failed'),
    "TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS": int(os.getenv('TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS', '1800')),
    # Load the consolidated DB URL for TDA
    "TDA_POSTGRES_DB_URL": os.getenv("TDA_POSTGRES_DB_URL"),
}


# --- Configuration & Logging ---
# Old basicConfig removed, app.logger is now used.

# Log loaded configuration using app.logger
app.logger.info("--- TDA Configuration ---")
for key, value in tda_config.items():
    if "API_KEY" in key and value:
        app.logger.info(f"  {key}: {'*' * (len(value) - 4) + value[-4:] if len(value) > 4 else '****'}")
    elif "PASSWORD" in key and value:
        app.logger.info(f"  {key}: ********")
    else:
        app.logger.info(f"  {key}: {value}")
app.logger.info("--- End TDA Configuration ---")

# Startup Check for API Key
if tda_config["USE_REAL_NEWS_API"] and not tda_config["TDA_NEWS_API_KEY"]:
    error_message = "CRITICAL: USE_REAL_NEWS_API is True, but TDA_NEWS_API_KEY is not set. Real News API calls will fail. Please set TDA_NEWS_API_KEY."
    app.logger.critical(error_message) # Use critical for startup failures
    raise ValueError(error_message)

# Startup check for DB config (PostgreSQL only)
required_pg_vars = ["POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]
missing_pg_vars = [var for var in required_pg_vars if not tda_config.get(var)]
if missing_pg_vars:
    error_msg = f"CRITICAL: Required PostgreSQL config is missing: {', '.join(missing_pg_vars)}"
    app.logger.critical(error_msg)
    raise ValueError(error_msg)

# --- Constants ---
MAX_SUMMARY_LENGTH = 250 # Define max summary length

DB_SCHEMA_TDA_TABLES = """
CREATE TABLE IF NOT EXISTS topics_snippets (
    id UUID PRIMARY KEY,
    type VARCHAR(50) NOT NULL CHECK(type IN ('topic', 'snippet')),
    title TEXT NOT NULL,
    summary TEXT,
    keywords JSONB,
    source_url TEXT,
    source_name TEXT,
    original_topic_details JSONB,
    llm_model_used_for_snippet VARCHAR(255),
    cover_art_prompt TEXT,
    image_url TEXT,
    generation_timestamp TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    last_accessed_timestamp TIMESTAMPTZ,
    relevance_score REAL
);
"""
DB_TYPE_TOPIC = "topic"
SOURCE_FEED_NEWS_API = "news_api_org"
ENDPOINT_ERROR_INTERNAL_SERVER_TDA = "INTERNAL_SERVER_ERROR_TDA"
NEWS_API_STATUS_OK = "ok"

# Flask app is initialized earlier now for logging setup

# --- Database Helper Functions (TDA General + Idempotency) ---
def _get_tda_db_connection():
    """Establishes a PostgreSQL database connection with RealDictCursor, prioritizing TDA_POSTGRES_DB_URL."""
    tda_db_url = tda_config.get('TDA_POSTGRES_DB_URL')

    if tda_db_url:
        try:
            conn = psycopg2.connect(dsn=tda_db_url, cursor_factory=RealDictCursor)
            app.logger.info("TDA DB: Successfully connected to PostgreSQL using TDA_POSTGRES_DB_URL.")
            return conn
        except psycopg2.Error as e:
            app.logger.error(f"TDA DB: Failed to connect using TDA_POSTGRES_DB_URL ('{tda_db_url}'): {e}. Falling back to individual components.", exc_info=True)
            # Fallback logic continues below

    # Fallback to individual components
    app.logger.info("TDA DB: TDA_POSTGRES_DB_URL not used or failed. Attempting connection with individual PostgreSQL components.")
    required_pg_vars_fallback = ["POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"] # Port is optional with default
    if not all(tda_config.get(var) for var in required_pg_vars_fallback):
        app.logger.error("TDA DB: PostgreSQL individual connection variables not fully configured for fallback.")
        raise ConnectionError("TDA DB: PostgreSQL individual environment variables not fully configured for fallback.")

    try:
        conn = psycopg2.connect(
            host=tda_config["POSTGRES_HOST"],
            port=tda_config["POSTGRES_PORT"],
            user=tda_config["POSTGRES_USER"],
            password=tda_config["POSTGRES_PASSWORD"],
            dbname=tda_config["POSTGRES_DB"],
            cursor_factory=RealDictCursor
        )
        app.logger.info("TDA DB: Successfully connected to PostgreSQL using individual components as fallback.")
        return conn
    except psycopg2.Error as e:
        app.logger.error(f"TDA DB: Error connecting to PostgreSQL database using individual components: {e}", exc_info=True)
        raise ConnectionError(f"TDA DB: PostgreSQL connection failed (individual components): {e}") from e

def _check_idempotency_key(db_conn, idempotency_key: str, task_name: str) -> Optional[Dict[str, Any]]:
    """Checks for an existing idempotency key record."""
    log_extra = {"task_id": "TDAIdempotencyCheck", "idempotency_key": idempotency_key, "task_name": task_name}
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT idempotency_key, task_name, workflow_id, created_at, locked_at, status, result_payload, error_payload FROM idempotency_keys WHERE idempotency_key = %s AND task_name = %s",
                (idempotency_key, task_name)
            )
            record = cur.fetchone()
            if record:
                app.logger.info(f"Idempotency key found. Status: '{record['status']}'.", extra=log_extra)
                if isinstance(record.get('result_payload'), str):
                    record['result_payload'] = json.loads(record['result_payload'])
                if isinstance(record.get('error_payload'), str):
                    record['error_payload'] = json.loads(record['error_payload'])
                return dict(record)
            app.logger.info("No existing idempotency key found.", extra=log_extra)
            return None
    except (psycopg2.Error, json.JSONDecodeError) as e:
        app.logger.error(f"TDA Idempotency: DB/JSON error checking key: {e}", exc_info=True, extra=log_extra)
        raise

def _store_idempotency_record(db_conn, idempotency_key: str, task_name: str, status: str, workflow_id: Optional[str] = None, result_payload: Optional[dict] = None, error_payload: Optional[dict] = None, is_new_key: bool = True):
    """Stores or updates an idempotency record."""
    log_extra = {"task_id": "TDAIdempotencyStore", "idempotency_key": idempotency_key, "task_name": task_name, "new_status": status}
    current_ts_utc = datetime.now(timezone.utc) # Use timezone-aware datetime
    locked_at_val = current_ts_utc if status == tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'] else None

    try:
        with db_conn.cursor() as cur:
            if is_new_key:
                app.logger.info("Storing new idempotency key.", extra=log_extra)
                cur.execute(
                    """
                    INSERT INTO idempotency_keys (idempotency_key, task_name, workflow_id, locked_at, status, result_payload, error_payload, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO UPDATE SET
                        task_name = EXCLUDED.task_name, workflow_id = EXCLUDED.workflow_id,
                        locked_at = EXCLUDED.locked_at, status = EXCLUDED.status,
                        result_payload = EXCLUDED.result_payload, error_payload = EXCLUDED.error_payload,
                        created_at = idempotency_keys.created_at;
                    """,
                    (idempotency_key, task_name, workflow_id, locked_at_val, status,
                     json.dumps(result_payload) if result_payload else None,
                     json.dumps(error_payload) if error_payload else None, current_ts_utc)
                )
            else: # Update existing key
                app.logger.info("Updating existing idempotency key.", extra=log_extra)
                set_clauses = ["status = %s", "result_payload = %s", "error_payload = %s"]
                params_update = [status, json.dumps(result_payload) if result_payload else None, json.dumps(error_payload) if error_payload else None]

                if status == tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING']:
                    set_clauses.append("locked_at = %s")
                    params_update.append(current_ts_utc)
                elif status in [tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED'], tda_config['TDA_IDEMPOTENCY_STATUS_FAILED']]:
                    set_clauses.append("locked_at = NULL")

                params_update.extend([idempotency_key, task_name])
                cur.execute(
                    f"UPDATE idempotency_keys SET {', '.join(set_clauses)} WHERE idempotency_key = %s AND task_name = %s;",
                    tuple(params_update)
                )
            app.logger.info("Successfully stored/updated TDA idempotency key.", extra=log_extra)
    except (psycopg2.Error, json.JSONDecodeError) as e:
        app.logger.error(f"TDA Idempotency: DB/JSON error storing key: {e}", exc_info=True, extra=log_extra)
        raise

def init_tda_db():
    """Initializes the TDA database table if it doesn't exist (PostgreSQL only)."""
    app.logger.info("[TDA_DB_INIT] Ensuring TDA database schema exists (PostgreSQL)...")
    conn = None
    cursor = None
    try:
        conn = _get_tda_db_connection() # Use renamed function
        cursor = conn.cursor()
        # Check if table exists for PostgreSQL (topics_snippets)
        cursor.execute("""
            SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                AND table_name = 'topics_snippets'
            );
        """)
        table_exists = cursor.fetchone()['exists']
        if not table_exists:
            app.logger.info("Table 'topics_snippets' not found in PostgreSQL. Creating now...")
            cursor.execute(DB_SCHEMA_TDA_TABLES)
            conn.commit()
            app.logger.info("[TDA_DB_INIT] PostgreSQL: Table 'topics_snippets' created.")
        else:
            app.logger.info("[TDA_DB_INIT] PostgreSQL: Table 'topics_snippets' already exists.")

    except psycopg2.Error as e:
        app.logger.error(f"[TDA_DB_INIT] PostgreSQL error during schema initialization: {e}", exc_info=True)
    except Exception as e_unexp:
        app.logger.error(f"[TDA_DB_INIT] Unexpected PostgreSQL error during schema initialization: {e_unexp}", exc_info=True)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# --- Database Interaction for Topics ---
def _save_topic_to_db(topic_object: dict): # Removed db_path argument
    """Saves a single topic object to the configured database."""
    conn = None
    cursor = None
    try:
        conn = _get_tda_db_connection() # Use renamed function
        cursor = conn.cursor()

        keywords_data = topic_object.get("keywords", [])
        keywords_to_save = json.dumps(keywords_data) if keywords_data else None

        potential_sources = topic_object.get("potential_sources", [])
        source_url = potential_sources[0].get("url") if potential_sources and len(potential_sources) > 0 else None
        source_name = potential_sources[0].get("source_name") if potential_sources and len(potential_sources) > 0 else None

        current_ts_iso = datetime.utcnow().isoformat()
        publication_date_to_save = topic_object.get("publication_date", current_ts_iso)
        last_accessed_ts_to_save = current_ts_iso

        topic_id_str = topic_object.get("topic_id")
        try:
            uuid.UUID(topic_id_str)
        except (ValueError, TypeError, AttributeError):
            topic_id_str = str(uuid.uuid4())

        summary_to_save = topic_object.get("summary")
        if summary_to_save and len(summary_to_save) > MAX_SUMMARY_LENGTH:
            cutoff_point = summary_to_save.rfind(' ', 0, MAX_SUMMARY_LENGTH - 3)
            if cutoff_point == -1: # No space found, hard cut
                summary_to_save = summary_to_save[:MAX_SUMMARY_LENGTH - 3] + "..."
            else:
                summary_to_save = summary_to_save[:cutoff_point] + "..."
            app.logger.info(f"Truncated summary for topic {topic_id_str} as it exceeded {MAX_SUMMARY_LENGTH} chars.")


        sql_insert = """
            INSERT INTO topics_snippets (
                id, type, title, summary, keywords,
                source_url, source_name, original_topic_details,
                llm_model_used_for_snippet, cover_art_prompt, image_url,
                generation_timestamp, last_accessed_timestamp, relevance_score
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                type = EXCLUDED.type,
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                keywords = EXCLUDED.keywords,
                source_url = EXCLUDED.source_url,
                source_name = EXCLUDED.source_name,
                original_topic_details = EXCLUDED.original_topic_details,
                llm_model_used_for_snippet = EXCLUDED.llm_model_used_for_snippet,
                cover_art_prompt = EXCLUDED.cover_art_prompt,
                image_url = EXCLUDED.image_url,
                generation_timestamp = EXCLUDED.generation_timestamp,
                last_accessed_timestamp = EXCLUDED.last_accessed_timestamp,
                relevance_score = EXCLUDED.relevance_score;
        """
        # Using %s for psycopg2, original_topic_details is expected to be JSONB compatible (dict/list)
        params = (
            topic_id_str,
            DB_TYPE_TOPIC,
            topic_object.get("title_suggestion"),
            summary_to_save, # Use the potentially truncated summary
            keywords_to_save,
            source_url,
            source_name,
            json.dumps(topic_object.get("original_topic_details")) if topic_object.get("original_topic_details") else None,
            None,
            None,
            topic_object.get("image_url"),
            publication_date_to_save,
            last_accessed_ts_to_save,
            topic_object.get("relevance_score")
        )
        cursor.execute(sql_insert, params)
        conn.commit()
        app.logger.info(f"Saved/Replaced topic {topic_id_str} to PostgreSQL DB: {topic_object.get('title_suggestion')}")

    except psycopg2.Error as e:
        app.logger.error(f"PostgreSQL error saving topic {topic_id_str}: {e}", exc_info=True) # Use topic_id_str for logging
        if conn: conn.rollback()
    except Exception as e_unexp:
        app.logger.error(f"Unexpected error saving topic {topic_id_str} to PostgreSQL DB: {e_unexp}", exc_info=True) # Use topic_id_str for logging
        if conn: conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def call_real_news_api(keywords: list[str] = None, categories: list[str] = None, language: str = None, country: str = None) -> list[dict]:
    """
    Calls the NewsAPI.org to fetch articles, parses them, transforms into TopicObjects,
    saves them to the database, and returns the list of TopicObjects.
    """
    if not tda_config.get("USE_REAL_NEWS_API"):
        return []
    if not tda_config.get("TDA_NEWS_API_KEY"):
        app.logger.error("call_real_news_api: TDA_NEWS_API_KEY not configured. Cannot make request.")
        return []

    base_url = tda_config["TDA_NEWS_API_BASE_URL"]
    endpoint = tda_config["TDA_NEWS_API_ENDPOINT"]
    api_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    params = {}
    query_keywords_list = keywords if keywords else tda_config["TDA_NEWS_DEFAULT_KEYWORDS"]
    if query_keywords_list:
        if isinstance(query_keywords_list, str):
            query_keywords_list = [kw.strip() for kw in query_keywords_list.split(',')]
        params["q"] = " OR ".join(query_keywords_list)
    if endpoint == "top-headlines":
        if categories:
            params["category"] = categories[0] if isinstance(categories, list) else categories
        if country:
            params["country"] = country
    current_language = language if language else tda_config["TDA_NEWS_DEFAULT_LANGUAGE"]
    if current_language:
        params["language"] = current_language
    params["pageSize"] = tda_config.get("TDA_NEWS_PAGE_SIZE", 25)
    headers = {
        "X-Api-Key": tda_config["TDA_NEWS_API_KEY"],
        "User-Agent": tda_config.get("TDA_NEWS_USER_AGENT", "AethercastTopicDiscovery/0.1")
    }
    request_timeout = tda_config.get("TDA_NEWS_REQUEST_TIMEOUT", 15)
    app.logger.info(f"Calling NewsAPI: URL={api_url}, Params={params}, Timeout={request_timeout}s")
    topic_objects = []
    try:
        response = requests.get(api_url, headers=headers, params=params, timeout=request_timeout)
        response.raise_for_status()
        response_json = response.json()
        if response_json.get("status") != NEWS_API_STATUS_OK:
            app.logger.error(f"NewsAPI returned error status: {response_json.get('status')}. Message: {response_json.get('message')}")
            return [] # Return empty list on API error status
        articles = response_json.get("articles", [])

        # DB saving logic is now centralized in _save_topic_to_db which uses configured DB type
        # No need for db_path here directly.
        
        for article in articles:
            title = article.get('title')
            if not title or title == "[Removed]":
                continue
            description = article.get('description')
            content = article.get('content')
            summary_text = description if description else content if content else "No summary available."
            if summary_text.endswith(" chars]") and summary_text[-10:].startswith("[+"):
                summary_text = summary_text[:summary_text.rfind("[+")].strip()
            if not summary_text: summary_text = "Content details not available."
            article_url = article.get('url')
            source_name = article.get('source', {}).get('name', 'Unknown Source')
            published_at = article.get('publishedAt')
            topic_object = {
                "topic_id": str(uuid.uuid4()), # Generate UUID for new topics
                "source_feed_name": SOURCE_FEED_NEWS_API,
                "title_suggestion": title,
                "summary": summary_text,
                "keywords": query_keywords_list, # Use the search keywords
                "potential_sources": [{"url": article_url, "title": title, "source_name": source_name}],
                "relevance_score": round(random.uniform(0.6, 0.9), 2), # Placeholder score
                "publication_date": published_at,
                "category_suggestion": "News", # Default category for news items
                "original_topic_details": article # Store the full article for potential future use
            }
            topic_objects.append(topic_object)
            _save_topic_to_db(topic_object) # Call updated save function
        
        app.logger.info(f"Transformed {len(topic_objects)} articles into TopicObjects from NewsAPI.")
        return topic_objects
    except requests.exceptions.RequestException as req_err:
        app.logger.error(f"NewsAPI request error: {req_err}", exc_info=True)
        # For Celery task, we should raise the error so it can be retried or marked as failed
        raise # Re-raise the exception
    except Exception as e_unexp: # Catch any other unexpected error
        app.logger.error(f"Unexpected error in call_real_news_api: {e_unexp}", exc_info=True)
        raise # Re-raise


@celery_app.task(bind=True, base=TdaNewsApiCeleryTask, name='fetch_news_from_newsapi_task')
def fetch_news_from_newsapi_task(self, request_id_celery: str, keywords: list[str] = None, categories: list[str] = None, language: str = None, country: str = None, idempotency_key: Optional[str] = None, workflow_id: Optional[str] = None):
    """
    Celery task to fetch articles from NewsAPI.org with idempotency.
    request_id_celery is for logging correlation with the dispatching request.
    """
    task_log_id = self.request.id # This is the Celery task's own unique ID
    task_name_for_idempotency = self.name # "fetch_news_from_newsapi_task"
    # Ensure keys in log_extra_base match the formatter fields for direct inclusion
    log_extra_base = {
        "orig_req_id": request_id_celery,
        "task_id": task_log_id,
        "idempotency_key": idempotency_key,
        "workflow_id": workflow_id,
        "keywords": keywords
    }
    app.logger.info(f"TDA NewsAPI Task {task_log_id}: Starting. Keywords: {keywords}", extra=log_extra_base)

    if not idempotency_key:
        app.logger.error(f"TDA NewsAPI Task {task_log_id}: Idempotency key not provided. This is required.", extra=log_extra_base)
        # Not raising ValueError here to allow on_failure to handle if it's invoked by Celery due to other reasons
        # before this check. Instead, return an error payload.
        # The task will be marked as FAILED by Celery if it raises an exception not caught by self.retry.
        # If we want to ensure on_failure is called, we'd raise an unhandled exception.
        # For now, let's assume the caller (discover_topics_task) ensures it's passed.
        # If it's absolutely critical to stop and record failure via on_failure, raise unhandled error.
        raise ValueError("Idempotency key is required for fetch_news_from_newsapi_task.")


    db_conn = None
    try:
        db_conn = _get_tda_db_connection()
        db_conn.autocommit = False

        existing_record = _check_idempotency_key(db_conn, idempotency_key, task_name_for_idempotency)
        if existing_record:
            status = existing_record['status']
            locked_at = existing_record.get('locked_at')
            if status == tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED']:
                app.logger.info(f"TDA NewsAPI Task {task_log_id}: Idempotency key '{idempotency_key}' already COMPLETED. Returning stored result.", extra=log_extra_base)
                db_conn.rollback() # Release transaction
                return existing_record['result_payload']
            elif status == tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING']:
                timeout_seconds = tda_config['TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']
                # Check if locked_at is timezone-aware; if not, make it so for comparison
                if locked_at and locked_at.tzinfo is None:
                    locked_at = locked_at.replace(tzinfo=datetime.now(timezone.utc).tzinfo)

                if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < timeout_seconds:
                    app.logger.warning(f"TDA NewsAPI Task {task_log_id}: Idempotency key '{idempotency_key}' is already PROCESSING. Conflict.", extra=log_extra_base)
                    db_conn.rollback() # Release transaction
                    # This specific return structure indicates to the parent task that it's a conflict.
                    return {"status": "PROCESSING_CONFLICT", "message": "Sub-task with this idempotency key is already processing.", "idempotency_key": idempotency_key}
                else: # Lock expired
                    app.logger.warning(f"TDA NewsAPI Task {task_log_id}: Idempotency key '{idempotency_key}' was PROCESSING but lock timed out. Re-processing.", extra=log_extra_base)
                    _store_idempotency_record(db_conn, idempotency_key, task_name_for_idempotency, tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
            elif status == tda_config['TDA_IDEMPOTENCY_STATUS_FAILED']:
                app.logger.info(f"TDA NewsAPI Task {task_log_id}: Idempotency key '{idempotency_key}' previously FAILED. Retrying.", extra=log_extra_base)
                _store_idempotency_record(db_conn, idempotency_key, task_name_for_idempotency, tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
        else: # No existing record
            app.logger.info(f"TDA NewsAPI Task {task_log_id}: New idempotency key '{idempotency_key}'. Storing as PROCESSING.", extra=log_extra_base)
            _store_idempotency_record(db_conn, idempotency_key, task_name_for_idempotency, tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=True)

        db_conn.commit() # Commit the PROCESSING status update

        # --- Main Task Logic ---
        app.logger.info(f"TDA NewsAPI Task {task_log_id}: Proceeding with NewsAPI call for key '{idempotency_key}'.", extra=log_extra_base)
        articles = call_real_news_api(keywords=keywords, categories=categories, language=language, country=country)

        task_result_payload_to_store = {"status": "success", "discovered_topics": articles, "message": f"Fetched {len(articles)} topics."}

        _store_idempotency_record(db_conn, idempotency_key, task_name_for_idempotency, tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED'], workflow_id=workflow_id, result_payload=task_result_payload_to_store, is_new_key=False)
        db_conn.commit() # Commit the COMPLETED status and result

        app.logger.info(f"TDA NewsAPI Task {task_log_id}: Successfully processed and stored COMPLETED status for key '{idempotency_key}'.", extra=log_extra_base)
        return task_result_payload_to_store

    except requests.exceptions.RequestException as e_req:
        app.logger.error(f"TDA NewsAPI Task {task_log_id}: NewsAPI request error for key '{idempotency_key}': {e_req}", exc_info=True, extra=log_extra_base)
        # self.retry will re-raise the exception if retries are exhausted, then on_failure in TdaNewsApiCeleryTask handles idempotency.
        # No need to update idempotency record to FAILED here, as on_failure will do it.
        if db_conn: db_conn.rollback() # Rollback any pending DB changes before retry/failure
        raise self.retry(exc=e_req, countdown=10, max_retries=2, idempotency_key=idempotency_key, workflow_id=workflow_id) # Pass keys for on_failure
    except Exception as e_task:
        app.logger.error(f"TDA NewsAPI Task {task_log_id}: Unexpected error for key '{idempotency_key}': {e_task}", exc_info=True, extra=log_extra_base)
        if db_conn: db_conn.rollback()
        # For other errors, on_failure will also be triggered if this re-raises.
        # Ensure idempotency_key and workflow_id are available to on_failure via kwargs.
        # Celery's self.retry automatically passes original kwargs. If raising a new exception, ensure they are passed.
        # For now, assume self.retry handles passing kwargs correctly or the exception is directly raised.
        raise # Re-raise to trigger on_failure, which will mark idempotency as FAILED.
    finally:
        if db_conn and not db_conn.closed:
            db_conn.close()
            app.logger.debug(f"TDA NewsAPI Task {task_log_id}: Closed DB connection for key '{idempotency_key}'.", extra=log_extra_base)


SIMULATED_DATA_SOURCES = [
    {
        "source_name": "Tech Chronicle",
        "articles": [
            {"title": "The Future of AI in Personalized Medicine", "url": "http://example.com/ai-medicine", "keywords": ["AI", "Healthcare", "Personalized Medicine"], "publish_date": "2024-03-10T10:00:00Z"},
            {"title": "Quantum Computing: Beyond the Hype", "url": "http://example.com/quantum-beyond-hype", "keywords": ["Quantum Computing", "Technology", "Innovation"], "publish_date": "2024-03-11T11:00:00Z"},
        ]
    },
]

def identify_topics_from_sources(query: str = None, limit: int = 5) -> list:
    identified_topics = []
    all_articles = []
    app.logger.info(f"[TDA_LOGIC] Scanning simulated data sources. Query: '{query}', Limit: {limit}")
    for data_source in SIMULATED_DATA_SOURCES:
        for article in data_source["articles"]:
            all_articles.append({
                "title": article["title"], "url": article["url"],
                "source_name": data_source["source_name"], "keywords": article.get("keywords", []),
                "publish_date": article.get("publish_date", datetime.utcnow().isoformat())
            })
    for article in all_articles:
        relevance = calculate_relevance_score(article, query)
        combined_keywords = list(set(article.get("keywords", []) + ([kw.strip() for kw in query.split(',')] if query else [])))
        topic_object = {
            "topic_id": str(uuid.uuid4()), "title_suggestion": article["title"],
            "summary": generate_summary_from_title(article["title"]), "keywords": combined_keywords,
            "potential_sources": [{"url": article["url"], "title": article["title"], "source_name": article["source_name"]}],
            "relevance_score": relevance, "publication_date": article.get("publish_date"),
            "category_suggestion": "General", "original_topic_details": article
        }
        identified_topics.append(topic_object)
        _save_topic_to_db(topic_object)
    identified_topics.sort(key=lambda x: x["relevance_score"], reverse=True)
    app.logger.info(f"[TDA_LOGIC] Identified {len(identified_topics)} potential topics from simulated sources. Returning top {min(limit, len(identified_topics))}.")
    return identified_topics[:limit]

# --- Other helper functions (generate_topic_id, generate_summary_from_title, calculate_relevance_score) ---
# generate_topic_id is now just str(uuid.uuid4()) inline or within topic creation.
# generate_summary_from_title and calculate_relevance_score are specific to simulated data.


# --- Custom Celery Task Class for NewsAPI Task with Idempotency ---
class TdaNewsApiCeleryTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        app.logger.error(f'Celery Task {task_id} (TDA NewsAPI Fetch) failed: {exc}', exc_info=einfo)
        idempotency_key = kwargs.get('idempotency_key')
        # task_name_for_idempotency should be passed in kwargs or use self.name if appropriate
        # For this specific task, self.name (e.g., 'fetch_news_from_newsapi_task') is suitable
        task_name = self.name # or kwargs.get('task_name_for_idempotency')
        workflow_id = kwargs.get('workflow_id')

        if idempotency_key:
            db_conn_fail = None
            try:
                db_conn_fail = _get_tda_db_connection()
                if db_conn_fail:
                    db_conn_fail.autocommit = False
                    error_payload = {"error_type": type(exc).__name__, "error_message": str(exc), "traceback": str(einfo).strip()}
                    _store_idempotency_record(db_conn_fail, idempotency_key, task_name,
                                              tda_config['TDA_IDEMPOTENCY_STATUS_FAILED'],
                                              workflow_id=workflow_id, # Pass workflow_id
                                              error_payload=error_payload, is_new_key=False)
                    db_conn_fail.commit()
                    app.logger.info(f"Idempotency record for key {idempotency_key} (Task: {task_name}) marked as FAILED.")
            except Exception as db_err:
                app.logger.error(f"Failed to update idempotency record to FAILED for key {idempotency_key} (Task: {task_name}) after task failure: {db_err}", exc_info=True)
                if db_conn_fail: db_conn_fail.rollback()
            finally:
                if db_conn_fail and not db_conn_fail.closed:
                    try: db_conn_fail.close()
                    except Exception: pass

def generate_summary_from_title(title: str) -> str:
    return f"This topic explores {title.lower()}, focusing on its recent developments and potential impact."

def calculate_relevance_score(article: dict, query: str = None) -> float:
    score = random.uniform(0.5, 0.9)
    if query:
        query_keywords = [q.strip().lower() for q in query.split()]
        for qk in query_keywords:
            if qk in [kw.lower() for kw in article.get("keywords", [])]: score = min(1.0, score + 0.2)
            if qk in article.get("title", "").lower(): score = min(1.0, score + 0.1)
    return round(score, 2)

# --- Custom Celery Task Class for TDA with Idempotency ---
class TdaCeleryTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        app.logger.error(f'Celery Task {task_id} (TDA DiscoverTopics) failed: {exc}', exc_info=einfo)
        idempotency_key = kwargs.get('idempotency_key')
        task_name = self.name

        # Check if PSYCOPG2_AVAILABLE before attempting DB operations
        # This check should ideally be at a higher level or ensure config reflects availability
        if idempotency_key: # and PSYCOPG2_AVAILABLE: (Assume check is handled by _get_tda_db_connection)
            db_conn_fail = None
            try:
                db_conn_fail = _get_tda_db_connection() # This will raise if not configured/available
                if db_conn_fail:
                    db_conn_fail.autocommit = False
                    error_payload = {"error_type": type(exc).__name__, "error_message": str(exc), "traceback": str(einfo)}
                    _store_idempotency_record(db_conn_fail, idempotency_key, task_name,
                                              tda_config['TDA_IDEMPOTENCY_STATUS_FAILED'],
                                              error_payload=error_payload, is_new_key=False)
                    db_conn_fail.commit()
                    app.logger.info(f"Idempotency record for key {idempotency_key} marked as FAILED for TDA task.")
            except Exception as db_err: # Catch broad exceptions for DB operations during failure handling
                app.logger.error(f"Failed to update idempotency record to FAILED for key {idempotency_key} (TDA task) after task failure: {db_err}", exc_info=True)
                if db_conn_fail: db_conn_fail.rollback()
            finally:
                if db_conn_fail and not db_conn_fail.closed:
                    try: db_conn_fail.close()
                    except Exception: pass


# Re-define discover_topics_task with the new base class
@celery_app.task(bind=True, base=TdaCeleryTask, name='discover_topics_task') # Name matches original
def discover_topics_task(self, request_id_main: str, query: Optional[str], limit: int, use_real_news_api_flag: bool, error_trigger: Optional[str] = None, idempotency_key: Optional[str] = None, workflow_id: Optional[str] = None):
    # Task logic from above, refactored into this new definition
    # (The entire logic of the previous discover_topics_task function body goes here)
    # For brevity, I'll just show the call to the actual logic, assuming it's moved or called.
    # This is a simplified representation. The actual implementation would move the previous function's body here.
    # The core logic of discovering topics (simulated or real)
    task_log_id = self.request.id # Celery's own unique ID for this task execution
    # Ensure keys in log_extra_base match the formatter fields for direct inclusion
    log_extra_base = {
        "orig_req_id": request_id_main,
        "task_id": task_log_id,
        "idempotency_key": idempotency_key,
        "workflow_id": workflow_id, # Added workflow_id
        "query": query
    }
    app.logger.info(f"TDA Celery Task {task_log_id}: Starting. Limit: {limit}, UseNewsAPI: {use_real_news_api_flag}", extra=log_extra_base)

    if not idempotency_key:
        app.logger.error(f"TDA Task {task_log_id}: Idempotency key not provided. This is required.", extra=log_extra_base)
        raise ValueError("Idempotency key is required for TDA task execution.")

    # Idempotency DB connection and checks
    db_conn = None
    try:
        db_conn = _get_tda_db_connection()
        db_conn.autocommit = False

        existing_record = _check_idempotency_key(db_conn, idempotency_key, self.name)
        if existing_record:
            status = existing_record['status']
            locked_at = existing_record.get('locked_at')
            if status == tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED']:
                app.logger.info(f"TDA Task {task_log_id}: Idempotency key '{idempotency_key}' already COMPLETED. Returning stored result.", extra=log_extra_base)
                db_conn.rollback()
                return existing_record['result_payload']
            elif status == tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING']:
                timeout_seconds = tda_config['TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']
                if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < timeout_seconds:
                    app.logger.warning(f"TDA Task {task_log_id}: Idempotency key '{idempotency_key}' is already PROCESSING. Conflict.", extra=log_extra_base)
                    db_conn.rollback()
                    return {"status": "PROCESSING_CONFLICT", "message": "Task with this idempotency key is already processing.", "idempotency_key": idempotency_key}
                else:
                    app.logger.warning(f"TDA Task {task_log_id}: Idempotency key '{idempotency_key}' was PROCESSING but lock timed out. Re-processing.", extra=log_extra_base)
                    _store_idempotency_record(db_conn, idempotency_key, self.name, tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
            elif status == tda_config['TDA_IDEMPOTENCY_STATUS_FAILED']:
                app.logger.info(f"TDA Task {task_log_id}: Idempotency key '{idempotency_key}' previously FAILED. Retrying.", extra=log_extra_base)
                _store_idempotency_record(db_conn, idempotency_key, self.name, tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
        else:
            app.logger.info(f"TDA Task {task_log_id}: New idempotency key '{idempotency_key}'. Storing as PROCESSING.", extra=log_extra_base)
            _store_idempotency_record(db_conn, idempotency_key, self.name, tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=True)
        db_conn.commit()

        if error_trigger == "tda_error":
            app.logger.info(f"TDA Task {task_log_id}: Simulated TDA error triggered for key '{idempotency_key}'.", extra=log_extra_base)
            raise Exception("Simulated TDA error in Celery task.")

        # --- Main Task Logic (copied from original discover_topics_task) ---
        discovered_topics = []
        if use_real_news_api_flag:
            request_keywords = [k.strip() for k in query.split(',')] if query else None
            app.logger.info(f"Celery Task {self.request.id}: Dispatching NewsAPI sub-task. Keywords: {request_keywords}", extra=log_extra_base)

            derived_news_key = f"{idempotency_key}_newsapi_fetch"
            app.logger.info(f"TDA Task {task_log_id}: Derived idempotency key for NewsAPI sub-task: {derived_news_key}", extra=log_extra_base)

            news_task = fetch_news_from_newsapi_task.delay(
                request_id_celery=self.request.id, # Correlation ID for logging
                keywords=request_keywords,
                language=tda_config.get("TDA_NEWS_DEFAULT_LANGUAGE"),
                idempotency_key=derived_news_key,
                workflow_id=workflow_id # Pass down the parent's workflow_id
            )
            app.logger.info(f"Celery Task {self.request.id}: NewsAPI sub-task {news_task.id} dispatched with idempotency key {derived_news_key}. Polling...", extra=log_extra_base)

            polling_start_time = time.time()
            news_polling_interval = int(os.getenv("TDA_NEWSAPI_POLLING_INTERVAL_SECONDS", "5"))
            news_polling_timeout = int(os.getenv("TDA_NEWSAPI_POLLING_TIMEOUT_SECONDS", "120"))

            while True:
                if time.time() - polling_start_time > news_polling_timeout:
                    app.logger.error(f"Celery Task {self.request.id}: Polling NewsAPI sub-task {news_task.id} timed out.", extra=log_extra_base)
                    raise Exception(f"Polling NewsAPI sub-task {news_task.id} timed out.")
                news_task_result = AsyncResult(news_task.id, app=celery_app)
                app.logger.info(f"Celery Task {self.request.id}: NewsAPI sub-task {news_task.id} status: {news_task_result.status}", extra=log_extra_base)
                if news_task_result.successful():
                    sub_task_output = news_task_result.result
                    # Handle PROCESSING_CONFLICT from sub-task
                    if isinstance(sub_task_output, dict) and sub_task_output.get("status") == "PROCESSING_CONFLICT":
                        app.logger.warning(f"TDA Task {task_log_id}: NewsAPI sub-task {news_task.id} for key '{derived_news_key}' is already processing. Will retry polling.", extra=log_extra_base)
                        # Continue polling, do not break yet.
                        # Add a small delay before next poll to avoid busy-waiting on conflict
                        time.sleep(news_polling_interval) # Or a specific conflict_retry_interval
                        continue # Skip to next iteration of while loop

                    if sub_task_output.get("status") == "success":
                        discovered_topics = sub_task_output.get("discovered_topics", [])
                        app.logger.info(f"Celery Task {self.request.id}: NewsAPI sub-task successful. Found {len(discovered_topics)} topics.", extra=log_extra_base)
                    else: # Other non-success states from sub-task that are not PROCESSING_CONFLICT
                        app.logger.error(f"Celery Task {self.request.id}: NewsAPI sub-task {news_task.id} reported business logic failure: {sub_task_output.get('message')}", extra=log_extra_base)
                        raise Exception(f"NewsAPI sub-task failed: {sub_task_output.get('message', 'Unknown error')}")
                    break # Break on actual success or non-conflict failure
                elif news_task_result.failed():
                    app.logger.error(f"Celery Task {self.request.id}: NewsAPI sub-task {news_task.id} failed with Celery status FAILED. Info: {news_task_result.info}", extra=log_extra_base)
                    raise Exception(f"NewsAPI sub-task failed: {str(news_task_result.info)}")
                time.sleep(news_polling_interval)
        else:
            app.logger.info(f"Celery Task {self.request.id}: Using simulated data sources. Query: '{query}', Limit: {limit}", extra=log_extra_base)
            discovered_topics = identify_topics_from_sources(query=query, limit=limit)
            app.logger.info(f"Celery Task {self.request.id}: Simulated data discovery found {len(discovered_topics)} topics.", extra=log_extra_base)

        task_result_payload = {}
        if not discovered_topics:
            task_result_payload = {"status": "success_no_topics", "message": "No topics discovered.", "topics": []}
        else:
            task_result_payload = {"status": "success", "discovered_topics": discovered_topics, "message": f"Successfully discovered {len(discovered_topics)} topics."}

        _store_idempotency_record(db_conn, idempotency_key, self.name, tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED'], workflow_id=workflow_id, result_payload=task_result_payload, is_new_key=False)
        db_conn.commit()
        app.logger.info(f"TDA Task {task_log_id}: Successfully processed and stored COMPLETED status for key '{idempotency_key}'.", extra=log_extra_base)
        return task_result_payload

    except Exception as e:
        app.logger.error(f"TDA Task {task_log_id}: Error for key '{idempotency_key}': {e}", exc_info=True, extra=log_extra_base)
        raise
    finally:
        if db_conn:
            if not db_conn.closed:
                db_conn.close()
                app.logger.debug(f"TDA Task {task_log_id}: Closed DB connection for key '{idempotency_key}'.", extra=log_extra_base)


# --- API Endpoint ---
@app.route("/discover_topics", methods=["POST"])
def discover_topics_async_endpoint():
    request_start_time = time.time()
    request_id_main = f"tda_req_{uuid.uuid4().hex[:8]}"
    app.logger.info(f"Request {request_id_main}: Received async /discover_topics request.") # Use app.logger

    idempotency_key = flask.request.headers.get(IDEMPOTENCY_KEY_HEADER)
    workflow_id = flask.request.headers.get("X-Workflow-ID")

    if not idempotency_key:
        app.logger.warning(f"Request {request_id_main}: Missing X-Idempotency-Key header.")
        return flask.jsonify({"error_code": "TDA_MISSING_IDEMPOTENCY_KEY", "message": "X-Idempotency-Key header is required."}), 400

    # --- Idempotency Pre-check at Endpoint Level ---
    idem_task_name_for_db = 'discover_topics_task' # Matches Celery task name
    db_conn_http = None
    # Assuming PSYCOPG2_AVAILABLE is defined and True if psycopg2 is imported
    # For TDA, psycopg2 is imported directly, so we can assume it's available if no import error.
    # A more robust check would be `if 'psycopg2' in sys.modules:` or the PSYCOPG2_AVAILABLE flag if set.
    # For now, let's assume it's available if the service starts.
    try:
        db_conn_http = _get_tda_db_connection()
        db_conn_http.autocommit = False # Manage transaction for pre-check

        existing_record = _check_idempotency_key(db_conn_http, idempotency_key, idem_task_name_for_db)
        if existing_record:
            status = existing_record['status']
            locked_at = existing_record.get('locked_at')
            lock_timeout = tda_config['TDA_IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']

            if status == tda_config['TDA_IDEMPOTENCY_STATUS_COMPLETED']:
                app.logger.info(f"TDA Request {request_id_main}: Idempotency key '{idempotency_key}' already COMPLETED. Returning stored result.", extra={'workflow_id': workflow_id})
                db_conn_http.rollback()
                return flask.jsonify(existing_record['result_payload']), 200
            elif status == tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING']:
                if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < lock_timeout:
                    app.logger.warning(f"TDA Request {request_id_main}: Idempotency key '{idempotency_key}' is PROCESSING. Returning conflict.", extra={'workflow_id': workflow_id})
                    db_conn_http.rollback()
                    return flask.jsonify({"error_code": "TDA_IDEMPOTENCY_CONFLICT", "message": "Request with this idempotency key is currently processing."}), 409
                else: # Lock expired
                    app.logger.info(f"TDA Request {request_id_main}: Idempotency key '{idempotency_key}' was PROCESSING but lock expired. Re-processing.", extra={'workflow_id': workflow_id})
                    _store_idempotency_record(db_conn_http, idempotency_key, idem_task_name_for_db, tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
                    db_conn_http.commit()
            elif status == tda_config['TDA_IDEMPOTENCY_STATUS_FAILED']:
                app.logger.info(f"TDA Request {request_id_main}: Idempotency key '{idempotency_key}' previously FAILED. Re-processing.", extra={'workflow_id': workflow_id})
                _store_idempotency_record(db_conn_http, idempotency_key, idem_task_name_for_db, tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=False)
                db_conn_http.commit()
        else: # No existing record
            app.logger.info(f"TDA Request {request_id_main}: New idempotency key '{idempotency_key}'. Storing as PROCESSING.", extra={'workflow_id': workflow_id})
            _store_idempotency_record(db_conn_http, idempotency_key, idem_task_name_for_db, tda_config['TDA_IDEMPOTENCY_STATUS_PROCESSING'], workflow_id=workflow_id, is_new_key=True)
            db_conn_http.commit()
    except psycopg2.Error as db_err_http:
        app.logger.error(f"TDA Request {request_id_main}: Database error during HTTP idempotency pre-check: {db_err_http}", exc_info=True, extra={'workflow_id': workflow_id})
        if db_conn_http: db_conn_http.rollback()
        app.logger.warning(f"TDA Request {request_id_main}: Proceeding to Celery dispatch despite DB error in pre-check.")
    except Exception as e_idem_http: # Catch any other error during pre-check
        app.logger.error(f"TDA Request {request_id_main}: Unexpected error during HTTP idempotency pre-check: {e_idem_http}", exc_info=True, extra={'workflow_id': workflow_id})
        if db_conn_http: db_conn_http.rollback()
        app.logger.warning(f"TDA Request {request_id_main}: Proceeding to Celery dispatch despite unexpected error in pre-check.")
    finally:
        if db_conn_http and not db_conn_http.closed:
            db_conn_http.close()
    # Continue to payload validation and task dispatch even if DB pre-check had issues (logged warning)

    try:
        request_data = flask.request.get_json() if flask.request.content_length else {}
    except Exception as e_json_decode:
        logger.warning(f"Request {request_id_main}: Failed to decode JSON: {e_json_decode}", exc_info=True)
        return flask.jsonify({"error_code": "TDA_MALFORMED_JSON", "message": "Malformed JSON."}), 400

    query = request_data.get("query")
    limit_raw = request_data.get("limit")
    error_trigger = request_data.get("error_trigger") # For testing endpoint itself

    if query is not None and (not isinstance(query, str) or not query.strip()):
        return flask.jsonify({"error_code": "TDA_INVALID_QUERY", "message": "query must be non-empty string."}), 400

    limit = 5
    if limit_raw is not None:
        try:
            limit = int(limit_raw)
            if not (1 <= limit <= 50):
                return flask.jsonify({"error_code": "TDA_INVALID_LIMIT_RANGE", "message": "limit must be 1-50."}), 400
        except ValueError:
            return flask.jsonify({"error_code": "TDA_INVALID_LIMIT_TYPE", "message": "limit must be integer."}), 400

    if error_trigger == "tda_endpoint_error": # Test endpoint error before dispatch
        logger.info(f"Request {request_id_main}: Simulated TDA endpoint error triggered.")
        return flask.jsonify({"error_code": "TDA_SIMULATED_ENDPOINT_ERROR", "message": "Simulated TDA endpoint error."}), 500

    use_real_news_api_flag = tda_config["USE_REAL_NEWS_API"]

    app.logger.info(f"Request {request_id_main}: Dispatching topic discovery to Celery task. Query: '{query}', Limit: {limit}, UseNewsAPI: {use_real_news_api_flag}, Idempotency-Key: {idempotency_key}") # Use app.logger

    task = discover_topics_task.delay(
        request_id_main=request_id_main,
        query=query,
        limit=limit,
        use_real_news_api_flag=use_real_news_api_flag,
        error_trigger=error_trigger,
        idempotency_key=idempotency_key,
        workflow_id=workflow_id
    )

    app.logger.info(f"Request {request_id_main}: Topic discovery task {task.id} dispatched.") # Use app.logger
    return flask.jsonify({
        "task_id": task.id,
        "status_url": f"/v1/tasks/{task.id}",
        "message": "Topic discovery task initiated. Poll task ID for results.",
        "idempotency_key_processed": idempotency_key
    }), 202


@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_tda_task_status(task_id: str):
    app.logger.info(f"Received request for TDA task status: {task_id}") # Use app.logger
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}

    if task_result.successful():
        task_output = task_result.result # This is the dict from fetch_news_from_newsapi_task
        response_data["result"] = task_output
        http_status = 200
        # Example: check if the task's internal logic indicated an issue
        if isinstance(task_output, dict) and task_output.get("status") != "success" and task_output.get("status") != "success_no_topics":
             http_status = 500
        # Idempotency conflict reported by the task
        if isinstance(task_output, dict) and task_output.get("status") == "PROCESSING_CONFLICT":
            return flask.jsonify(response_data), 409 # Conflict
        return flask.jsonify(response_data), http_status
    elif task_result.failed():
        error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
        response_data["result"] = error_info
        return flask.jsonify(response_data), 500
    else: # PENDING, STARTED, RETRY
        return flask.jsonify(response_data), 202

if __name__ == "__main__":
    # Startup checks for psycopg2 and PostgreSQL config
    try:
        # Attempt a basic check for psycopg2 presence if not done elsewhere,
        # though PSYCOPG2_AVAILABLE flag based on import is better.
        # This is more about ensuring config is present if the lib is there.
        if not psycopg2: # This will cause NameError if psycopg2 failed to import
            app.logger.warning("TDA Warning: psycopg2 library is not available. Idempotency features will be disabled.")
    except NameError:
        app.logger.warning("TDA Warning: psycopg2 library failed to import. Idempotency features will be disabled.")

    if not all(tda_config.get(k) for k in required_pg_vars): # required_pg_vars defined globally
        app.logger.warning("TDA Warning: PostgreSQL connection details not fully configured. Idempotency and DB operations may fail.")

    init_tda_db()

    host = tda_config.get("TDA_HOST")
    port = tda_config.get("TDA_PORT")
    # Read FLASK_DEBUG directly for running the app
    flask_debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == 'true'
    # The initial "JSON logging configured..." message is now part of setup_json_logging
    app.logger.info(f"--- TDA Service starting on {host}:{port} (Debug: {flask_debug_mode}, DB: PostgreSQL) ---")
    app.run(host=host, port=port, debug=flask_debug_mode)
