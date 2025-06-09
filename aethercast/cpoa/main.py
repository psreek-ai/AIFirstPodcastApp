import logging
import sys
import os
from dotenv import load_dotenv # Added
import json
import sqlite3 # Will be conditionally used or removed later
from datetime import datetime
import uuid
from typing import Optional, Dict, Any, List
import requests # Added for service calls
import time # Added for retry logic
import psycopg2 # Added for PostgreSQL
from psycopg2.extras import RealDictCursor # Added for PostgreSQL
import random # Added for landing page snippet keyword randomization

# Ensure the 'aethercast' directory is in the Python path.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_script_dir)
repo_root_dir = os.path.dirname(parent_dir)
if repo_root_dir not in sys.path:
    sys.path.insert(0, repo_root_dir)

load_dotenv() # Added

# --- CPOA Status Constants ---
CPOA_STATUS_PENDING = "pending"
CPOA_STATUS_INIT_FAILURE = "initialization_failure" # Used in log_step, implies error
CPOA_STATUS_FAILED_WCHA_MODULE_ERROR = "failed_wcha_module_error"
CPOA_STATUS_WCHA_CONTENT_RETRIEVAL = "wcha_content_retrieval" # Intermediate status
CPOA_STATUS_FAILED_WCHA_CONTENT_HARVEST = "failed_wcha_content_harvest"
CPOA_STATUS_PSWA_SCRIPT_GENERATION = "pswa_script_generation" # Intermediate status
CPOA_STATUS_FAILED_PSWA_BAD_SCRIPT_STRUCTURE = "failed_pswa_bad_script_structure"
CPOA_STATUS_FAILED_PSWA_REQUEST_EXCEPTION = "failed_pswa_request_exception"
CPOA_STATUS_FAILED_PSWA_JSON_DECODE = "failed_pswa_json_decode"
CPOA_STATUS_VFA_AUDIO_GENERATION = "vfa_audio_generation" # Intermediate status
CPOA_STATUS_FAILED_VFA_REQUEST_EXCEPTION = "failed_vfa_request_exception"
CPOA_STATUS_FAILED_VFA_JSON_DECODE = "failed_vfa_json_decode"
CPOA_STATUS_COMPLETED = "completed"
CPOA_STATUS_ASF_NOTIFICATION = "asf_notification" # Intermediate status
CPOA_STATUS_COMPLETED_WITH_ASF_NOTIFICATION_FAILURE = "completed_with_asf_notification_failure"
CPOA_STATUS_COMPLETED_WITH_ASF_NOTIFICATION_FAILURE_JSON_DECODE = "completed_with_asf_notification_failure_json_decode"
CPOA_STATUS_COMPLETED_WITH_VFA_DATA_MISSING = "completed_with_vfa_data_missing"
CPOA_STATUS_COMPLETED_WITH_VFA_SKIPPED = "completed_with_vfa_skipped"
CPOA_STATUS_FAILED_VFA_REPORTED_ERROR = "failed_vfa_reported_error"
CPOA_STATUS_FAILED_VFA_UNKNOWN_STATUS = "failed_vfa_unknown_status"
CPOA_STATUS_FAILED_WCHA_EXCEPTION = "failed_wcha_exception"
CPOA_STATUS_FAILED_PSWA_EXCEPTION = "failed_pswa_exception"
CPOA_STATUS_FAILED_VFA_EXCEPTION = "failed_vfa_exception"
CPOA_STATUS_FAILED_ASF_NOTIFICATION_EXCEPTION = "failed_asf_notification_exception"
CPOA_STATUS_FAILED_UNKNOWN_STAGE_EXCEPTION = "failed_unknown_stage_exception"

# --- Orchestration Stage Constants ---
ORCHESTRATION_STAGE_INITIALIZATION = "initialization"
ORCHESTRATION_STAGE_INITIALIZATION_FAILURE = "initialization_failure"
ORCHESTRATION_STAGE_WCHA = "wcha_content_retrieval"
ORCHESTRATION_STAGE_PSWA = "pswa_script_generation"
ORCHESTRATION_STAGE_VFA = "vfa_audio_generation"
ORCHESTRATION_STAGE_ASF_NOTIFICATION = "asf_notification"
ORCHESTRATION_STAGE_FINALIZATION = "finalization" # For logging the end

# --- UI Event Name Constants ---
UI_EVENT_TASK_ERROR = "task_error"
UI_EVENT_GENERATION_STATUS = "generation_status"

# --- Preference Keys (example, define actual keys used) ---
PREF_KEY_VFA_VOICE_NAME = "preferred_vfa_voice_name"
PREF_KEY_VFA_LANGUAGE_CODE = "preferred_vfa_language_code" # Example
PREF_KEY_VFA_SPEAKING_RATE = "preferred_vfa_speaking_rate" # Example
PREF_KEY_VFA_PITCH = "preferred_vfa_pitch" # Example
PREF_KEY_NEWS_CATEGORY = "preferred_news_category" # Example for TDA

# --- VFA Result Status Constants ---
VFA_STATUS_NOT_RUN = "not_run"
VFA_STATUS_SUCCESS = "success"
VFA_STATUS_SKIPPED = "skipped"
VFA_STATUS_ERROR = "error"

# --- SCA/Snippet Orchestration Status Constants ---
SCA_STATUS_REQUEST_INVALID = "SCA_REQUEST_INVALID"
SCA_STATUS_CALL_FAILED_AFTER_RETRIES = "SCA_CALL_FAILED_AFTER_RETRIES"
SCA_STATUS_RESPONSE_INVALID_JSON = "SCA_RESPONSE_INVALID_JSON"
SCA_STATUS_CALL_UNEXPECTED_ERROR = "SCA_CALL_UNEXPECTED_ERROR"

# --- DB Model Type Constants ---
DB_TYPE_TOPIC = "topic"
DB_TYPE_SNIPPET = "snippet"


# --- Service URLs ---
PSWA_SERVICE_URL = os.getenv("PSWA_SERVICE_URL", "http://localhost:5004/weave_script")
VFA_SERVICE_URL = os.getenv("VFA_SERVICE_URL", "http://localhost:5005/forge_voice")
ASF_NOTIFICATION_URL = os.getenv("ASF_NOTIFICATION_URL", "http://localhost:5006/asf/internal/notify_new_audio")
ASF_WEBSOCKET_BASE_URL = os.getenv("ASF_WEBSOCKET_BASE_URL", "ws://localhost:5006/api/v1/podcasts/stream")
SCA_SERVICE_URL = os.getenv("SCA_SERVICE_URL", "http://localhost:5002/craft_snippet")
CPOA_ASF_SEND_UI_UPDATE_URL = os.getenv("CPOA_ASF_SEND_UI_UPDATE_URL", "http://localhost:5006/asf/internal/send_ui_update")
IGA_SERVICE_URL = os.getenv("IGA_SERVICE_URL", "http://localhost:5007")
TDA_SERVICE_URL = os.getenv("TDA_SERVICE_URL", "http://localhost:5000/discover_topics")

# Retry Configuration
CPOA_SERVICE_RETRY_COUNT = int(os.getenv("CPOA_SERVICE_RETRY_COUNT", "3"))
CPOA_SERVICE_RETRY_BACKOFF_FACTOR = float(os.getenv("CPOA_SERVICE_RETRY_BACKOFF_FACTOR", "0.5"))

# Database Configuration
DATABASE_TYPE = os.getenv("DATABASE_TYPE", "sqlite") # Default to sqlite
CPOA_DATABASE_PATH = os.getenv("SHARED_DATABASE_PATH", "/app/database/aethercast_podcasts.db") # SQLite path

POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")


# --- Logging Configuration ---
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    # New format to include workflow_id and task_id, which will be added via LoggerAdapter
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - CPOA - %(workflow_id)s - %(task_id)s - %(message)s')

# Log loaded configuration (initial logging without workflow/task IDs)
# Create a temporary adapter for these initial logs if needed, or log directly.
initial_log_extra = {'workflow_id': 'N/A', 'task_id': 'N/A'}
logger.info("--- CPOA Configuration ---", extra=initial_log_extra)
logger.info(f"DATABASE_TYPE: {DATABASE_TYPE}")
if DATABASE_TYPE == "sqlite":
    logger.info(f"SHARED_DATABASE_PATH (SQLite): {CPOA_DATABASE_PATH}")
elif DATABASE_TYPE == "postgres":
    logger.info(f"POSTGRES_HOST: {POSTGRES_HOST}")
    logger.info(f"POSTGRES_PORT: {POSTGRES_PORT}")
    logger.info(f"POSTGRES_USER: {POSTGRES_USER}")
    # Do not log password
    logger.info(f"POSTGRES_DB: {POSTGRES_DB}")

logger.info(f"TDA_SERVICE_URL: {TDA_SERVICE_URL}")
logger.info(f"PSWA_SERVICE_URL: {PSWA_SERVICE_URL}")
logger.info(f"VFA_SERVICE_URL: {VFA_SERVICE_URL}")
logger.info(f"ASF_NOTIFICATION_URL: {ASF_NOTIFICATION_URL}")
logger.info(f"ASF_WEBSOCKET_BASE_URL: {ASF_WEBSOCKET_BASE_URL}")
logger.info(f"SCA_SERVICE_URL: {SCA_SERVICE_URL}")
logger.info(f"IGA_SERVICE_URL: {IGA_SERVICE_URL}")
logger.info(f"CPOA_ASF_SEND_UI_UPDATE_URL: {CPOA_ASF_SEND_UI_UPDATE_URL}")
logger.info(f"CPOA_SERVICE_RETRY_COUNT: {CPOA_SERVICE_RETRY_COUNT}")
logger.info(f"CPOA_SERVICE_RETRY_BACKOFF_FACTOR: {CPOA_SERVICE_RETRY_BACKOFF_FACTOR}")
logger.info("--- End CPOA Configuration ---")


# --- Database Connection Helper (New for PostgreSQL, adaptable) ---
def _get_cpoa_db_connection():
    db_type = DATABASE_TYPE # Use the global config
    if db_type == "postgres":
        required_pg_vars = {"POSTGRES_HOST": POSTGRES_HOST,
                            "POSTGRES_USER": POSTGRES_USER,
                            "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
                            "POSTGRES_DB": POSTGRES_DB}
        missing_vars = [key for key, value in required_pg_vars.items() if not value]
        if missing_vars:
            logger.error(f"CPOA: PostgreSQL connection variables not fully set: Missing {', '.join(missing_vars)}")
            raise ConnectionError(f"CPOA: PostgreSQL environment variables not fully configured: Missing {', '.join(missing_vars)}")
        try:
            conn = psycopg2.connect(
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                dbname=POSTGRES_DB,
                cursor_factory=RealDictCursor
            )
            logger.info("CPOA successfully connected to PostgreSQL.")
            return conn
        except psycopg2.Error as e:
            logger.error(f"CPOA: Unable to connect to PostgreSQL: {e}")
            raise ConnectionError(f"CPOA: PostgreSQL connection failed: {e}") from e
    elif db_type == "sqlite":
        # This function is now primarily for PG. SQLite connections will be handled by existing logic
        # that uses CPOA_DATABASE_PATH directly. Or, refactor that logic to call a similar helper.
        # For now, returning None indicates to use the old SQLite path.
        logger.debug("CPOA DB type is SQLite. Connection handled by individual functions using CPOA_DATABASE_PATH.")
        return None
    else:
        logger.error(f"CPOA: Unsupported DATABASE_TYPE: {db_type}", extra=initial_log_extra)
        raise ValueError(f"CPOA: Unsupported DATABASE_TYPE: {db_type}")

# --- CPOA State Management DB Status Constants (New) ---
WORKFLOW_STATUS_PENDING = "pending"
WORKFLOW_STATUS_IN_PROGRESS = "in_progress"
WORKFLOW_STATUS_COMPLETED = "completed"
WORKFLOW_STATUS_FAILED = "failed"
WORKFLOW_STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"

TASK_STATUS_PENDING = "pending"
TASK_STATUS_DISPATCHED = "dispatched"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_SKIPPED = "skipped"

# --- CPOA State Management DB Helpers (New) ---
def _create_workflow_instance(trigger_event_type: str, trigger_event_details: Optional[dict] = None, user_id: Optional[str] = None) -> Optional[str]:
    conn = None
    workflow_id = None
    log_extra = {'workflow_id': None, 'task_id': None}
    try:
        conn = _get_cpoa_db_connection()
        if not conn or DATABASE_TYPE != "postgres":
            logger.error(f"CPOA State: Failed to get PostgreSQL connection for creating workflow. DB Type: {DATABASE_TYPE}", extra=log_extra)
            return None

        with conn.cursor() as cur:
            sql = """
                INSERT INTO workflow_instances
                    (user_id, trigger_event_type, trigger_event_details_json, overall_status, start_timestamp, last_updated_timestamp)
                VALUES (%s, %s, %s, %s, current_timestamp, current_timestamp)
                RETURNING workflow_id;
            """
            trigger_details_json = json.dumps(trigger_event_details) if trigger_event_details else None
            cur.execute(sql, (user_id, trigger_event_type, trigger_details_json, WORKFLOW_STATUS_PENDING))
            result = cur.fetchone()
            if result and 'workflow_id' in result: # Check if 'workflow_id' key exists
                workflow_id = str(result['workflow_id'])
            conn.commit()
            log_extra['workflow_id'] = workflow_id # Update log_extra with the new workflow_id
            logger.info(f"Workflow instance created. Type: {trigger_event_type}", extra=log_extra)
            return workflow_id
    except psycopg2.Error as e:
        logger.error(f"CPOA State: DB error creating workflow. Type: {trigger_event_type}. Error: {e}", exc_info=True, extra=log_extra)
        if conn: conn.rollback()
        return None
    except Exception as e_unexp:
        logger.error(f"CPOA State: Unexpected error creating workflow. Type: {trigger_event_type}. Error: {e_unexp}", exc_info=True, extra=log_extra)
        if conn: conn.rollback()
        return None
    finally:
        if conn: conn.close()

def _update_workflow_instance_status(workflow_id: str, overall_status: str, context_data: Optional[dict] = None, error_message: Optional[str] = None):
    conn = None
    log_extra = {'workflow_id': workflow_id, 'task_id': None}
    try:
        conn = _get_cpoa_db_connection()
        if not conn or DATABASE_TYPE != "postgres":
            logger.error(f"CPOA State: Failed to get PostgreSQL connection for updating workflow.", extra=log_extra)
            return False

        with conn.cursor() as cur:
            end_ts_sql_part = ", end_timestamp = current_timestamp" if overall_status in [WORKFLOW_STATUS_COMPLETED, WORKFLOW_STATUS_FAILED, WORKFLOW_STATUS_COMPLETED_WITH_ERRORS] else ""

            current_context_data = {}
            if context_data is None:
                cur.execute("SELECT context_data_json FROM workflow_instances WHERE workflow_id = %s;", (workflow_id,))
                current_row = cur.fetchone()
                if current_row and current_row.get('context_data_json'): # Check key existence
                    current_context_data = current_row['context_data_json']
                context_data_to_save = current_context_data
            else:
                cur.execute("SELECT context_data_json FROM workflow_instances WHERE workflow_id = %s;", (workflow_id,))
                current_row = cur.fetchone()
                if current_row and current_row.get('context_data_json'):
                    current_context_data = current_row['context_data_json']
                current_context_data.update(context_data)
                context_data_to_save = current_context_data

            sql = f"""
                UPDATE workflow_instances
                SET overall_status = %s, context_data_json = %s, error_message = %s, last_updated_timestamp = current_timestamp {end_ts_sql_part}
                WHERE workflow_id = %s;
            """
            cur.execute(sql, (overall_status, json.dumps(context_data_to_save) if context_data_to_save else None, error_message, workflow_id))
            conn.commit()
            logger.info(f"Workflow instance status updated to {overall_status}.", extra=log_extra)
            return True
    except psycopg2.Error as e:
        logger.error(f"CPOA State: DB error updating workflow to status {overall_status}. Error: {e}", exc_info=True, extra=log_extra)
        if conn: conn.rollback()
        return False
    except Exception as e_unexp:
        logger.error(f"CPOA State: Unexpected error updating workflow. Error: {e_unexp}", exc_info=True, extra=log_extra)
        if conn: conn.rollback()
        return False
    finally:
        if conn: conn.close()

def _create_task_instance(workflow_id: str, agent_name: str, task_order: int, input_params: Optional[dict] = None, initial_status: str = TASK_STATUS_PENDING) -> Optional[str]:
    conn = None
    task_id = None
    log_extra = {'workflow_id': workflow_id, 'task_id': None}
    try:
        conn = _get_cpoa_db_connection()
        if not conn or DATABASE_TYPE != "postgres":
            logger.error(f"CPOA State: Failed to get PostgreSQL connection for creating task.", extra=log_extra)
            return None

        with conn.cursor() as cur:
            sql = """
                INSERT INTO task_instances
                    (workflow_id, agent_name, task_order, status, input_params_json, start_timestamp, last_updated_timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, current_timestamp)
                RETURNING task_id;
            """
            start_ts = datetime.now() if initial_status not in [TASK_STATUS_PENDING] else None # Use datetime.now() for PG

            cur.execute(sql, (workflow_id, agent_name, task_order, initial_status, json.dumps(input_params) if input_params else None, start_ts))
            result = cur.fetchone()
            if result and 'task_id' in result: # Check key existence
                task_id = str(result['task_id'])
            conn.commit()
            log_extra['task_id'] = task_id # Update log_extra with new task_id
            logger.info(f"Task instance created. Agent: {agent_name}, Order: {task_order}, Status: {initial_status}", extra=log_extra)
            return task_id
    except psycopg2.Error as e:
        logger.error(f"CPOA State: DB error creating task for agent {agent_name}. Error: {e}", exc_info=True, extra=log_extra)
        if conn: conn.rollback()
        return None
    except Exception as e_unexp:
        logger.error(f"CPOA State: Unexpected error creating task for agent {agent_name}. Error: {e_unexp}", exc_info=True, extra=log_extra)
        if conn: conn.rollback()
        return None
    finally:
        if conn: conn.close()

def _update_task_instance_status(task_id: str, status: str, output_summary: Optional[dict] = None, error_details: Optional[dict] = None, retry_count: Optional[int] = None, workflow_id_for_log: Optional[str] = None):
    conn = None
    log_extra = {'workflow_id': workflow_id_for_log, 'task_id': task_id}
    try:
        conn = _get_cpoa_db_connection()
        if not conn or DATABASE_TYPE != "postgres":
            logger.error(f"CPOA State: Failed to get PostgreSQL connection for updating task.", extra=log_extra)
            return False

        with conn.cursor() as cur:
            set_clauses = ["status = %s", "last_updated_timestamp = current_timestamp"]
            params = [status]

            if output_summary is not None:
                set_clauses.append("output_result_summary_json = %s")
                params.append(json.dumps(output_summary))
            if error_details is not None:
                set_clauses.append("error_details_json = %s")
                params.append(json.dumps(error_details))
            if retry_count is not None:
                set_clauses.append("retry_count = %s")
                params.append(retry_count)

            if status in [TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_SKIPPED]:
                set_clauses.append("end_timestamp = current_timestamp")

            if status in [TASK_STATUS_DISPATCHED, TASK_STATUS_IN_PROGRESS] and status != TASK_STATUS_PENDING : # Ensure it's not TASK_STATUS_PENDING
                 set_clauses.append("start_timestamp = COALESCE(start_timestamp, current_timestamp)")


            sql = f"UPDATE task_instances SET {', '.join(set_clauses)} WHERE task_id = %s;"
            params.append(task_id)

            cur.execute(sql, tuple(params))
            conn.commit()
            logger.info(f"Task instance status updated to {status}.", extra=log_extra)
            return True
    except psycopg2.Error as e:
        logger.error(f"CPOA State: DB error updating task to status {status}. Error: {e}", exc_info=True, extra=log_extra)
        if conn: conn.rollback()
        return False
    except Exception as e_unexp:
        logger.error(f"CPOA State: Unexpected error updating task. Error: {e_unexp}", exc_info=True, extra=log_extra)
        if conn: conn.rollback()
        return False
    finally:
        if conn: conn.close()

# Try to import WCHA. PSWA and VFA are now services.
try:
    from aethercast.wcha.main import get_content_for_topic
    WCHA_IMPORT_SUCCESSFUL = True
    WCHA_MISSING_IMPORT_ERROR = None
except ImportError as e:
    WCHA_IMPORT_SUCCESSFUL = False
    WCHA_MISSING_IMPORT_ERROR = f"CPOA: WCHA module import error: {e}. WCHA functionality will be unavailable."
    # Define placeholder function for WCHA if import fails
    def get_content_for_topic(topic: str, max_sources: int = 3) -> str: # Placeholder
        return f"Error: WCHA module not loaded. Cannot get content for topic '{topic}'."

# CPOA_IMPORTS_SUCCESSFUL now only depends on WCHA for this module's direct operation.
# If WCHA was also a service, this would be True unless requests was missing.
CPOA_IMPORTS_SUCCESSFUL = WCHA_IMPORT_SUCCESSFUL
CPOA_MISSING_IMPORT_ERROR = WCHA_MISSING_IMPORT_ERROR


# Global Error Indicator Constants
WCHA_ERROR_INDICATORS = (
    "Error: WCHA module not loaded",
    "WCHA Error: Necessary web scraping libraries not installed.",
    "Error during web search",
    "WCHA: No search results",
    "WCHA: Failed to harvest usable content",
    "Error fetching URL",
    "Failed to fetch URL",
    "No paragraph text found",
    "Content at URL",
    "Cannot 'harvest_from_url'",
    "No pre-defined content found"
)
# PSWA errors are now primarily handled by HTTP status codes from its service.
# This list can be kept as a fallback for checking 200 OK responses with error messages in payload.
PSWA_PAYLOAD_ERROR_PREFIXES = (
    "OpenAI library not available", # Should ideally be a 500 from PSWA service
    "Error: OPENAI_API_KEY",        # Should ideally be a 500 from PSWA service
    "OpenAI API Error:",            # Should ideally be a 500 from PSWA service
    "[ERROR] Insufficient content", # Should ideally be a 400 from PSWA service
    "An unexpected error occurred"  # Should ideally be a 500 from PSWA service
)
# VFA errors are handled by its service's JSON response structure ('status' and 'message' keys).

# TOPIC_TO_URL_MAP has been removed.

# --- Retry Helper ---
def requests_with_retry(method: str, url: str, max_retries: int, backoff_factor: float, **kwargs) -> requests.Response:
    """
    Makes an HTTP request with retries and exponential backoff.
    """
    for attempt in range(max_retries):
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            return response
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            logger.warning(f"Request to {url} failed (attempt {attempt + 1}/{max_retries}): {type(e).__name__} - {e}")
            if attempt + 1 == max_retries:
                logger.error(f"Max retries reached for {url}. Giving up.")
                raise # Re-raise the last exception
            sleep_time = backoff_factor * (2 ** attempt)
            logger.info(f"Retrying in {sleep_time:.2f} seconds...")
            time.sleep(sleep_time)
        except requests.exceptions.HTTPError as e: # HTTP errors (4xx/5xx) are usually not retried unless idempotent
            # For this project, we will retry on 5xx errors as they might be transient service issues.
            # 4xx errors typically indicate client errors and should not be retried directly without change.
            if e.response.status_code >= 500:
                logger.warning(f"Request to {url} failed with HTTP {e.response.status_code} (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt + 1 == max_retries:
                    logger.error(f"Max retries reached for {url} after HTTP {e.response.status_code}. Giving up.")
                    raise
                sleep_time = backoff_factor * (2 ** attempt)
                logger.info(f"Retrying in {sleep_time:.2f} seconds...")
                time.sleep(sleep_time)
            else: # For 4xx errors, raise immediately
                logger.error(f"Request to {url} failed with client error HTTP {e.response.status_code}: {e}. Not retrying.")
                raise
    # Should not be reached if max_retries > 0, but as a fallback:
    raise Exception(f"Requests with retry failed for {url} after {max_retries} attempts without specific exception.")


def _update_task_status_in_db(task_id: str, new_cpoa_status: str, error_msg: Optional[str] = None, db_path_sqlite: Optional[str] = None) -> None:
    """
    Updates the cpoa_status, cpoa_error_message, and last_updated_timestamp for a task in the database.
    Uses PostgreSQL if configured, otherwise falls back to SQLite with db_path_sqlite.
    """
    logger.info(f"Task {task_id}: Updating CPOA status to '{new_cpoa_status}'. Error: '{error_msg or 'None'}'")
    timestamp = datetime.now() # Use datetime object for PG, format for SQLite

    conn = None
    cursor = None
    db_type_used = DATABASE_TYPE

    try:
        conn = _get_cpoa_db_connection() # This will return PG conn or None (for SQLite)

        if conn: # PostgreSQL path
            cursor = conn.cursor()
            # Ensure task_id is a string for UUID compatibility if it isn't already
            task_id_str = str(task_id) if not isinstance(task_id, str) else task_id

            sql = """
                UPDATE podcasts
                SET cpoa_status = %s, cpoa_error_message = %s, last_updated_timestamp = %s
                WHERE podcast_id = %s;
            """
            cursor.execute(sql, (new_cpoa_status, error_msg, timestamp, task_id_str))
            conn.commit()
            logger.info(f"Task {task_id}: Successfully updated CPOA status in PostgreSQL DB to '{new_cpoa_status}'.")

        else: # SQLite path (using db_path_sqlite which defaults to CPOA_DATABASE_PATH)
            db_type_used = "sqlite" # For logging
            sqlite_path = db_path_sqlite or CPOA_DATABASE_PATH
            if not sqlite_path:
                logger.error(f"Task {task_id}: SQLite DB path not available for status update.")
                return

            conn_sqlite = sqlite3.connect(sqlite_path)
            cursor_sqlite = conn_sqlite.cursor()
            cursor_sqlite.execute(
                """
                UPDATE podcasts
                SET cpoa_status = ?, cpoa_error_message = ?, last_updated_timestamp = ?
                WHERE podcast_id = ?
                """,
                (new_cpoa_status, error_msg, timestamp.isoformat(), task_id)
            )
            conn_sqlite.commit()
            conn_sqlite.close() # Close SQLite connection
            logger.info(f"Task {task_id}: Successfully updated CPOA status in SQLite DB to '{new_cpoa_status}'.")

    except (psycopg2.Error, sqlite3.Error) as e:
        logger.error(f"CPOA: DB error for task {task_id} (DB: {db_type_used}, Status: {new_cpoa_status}): {e}", exc_info=True)
        if conn and db_type_used == "postgres": conn.rollback() # Rollback PG transaction
    except Exception as e_unexp:
        logger.error(f"CPOA: Unexpected error in _update_task_status_in_db for task {task_id} (DB: {db_type_used}, Status: {new_cpoa_status}): {e_unexp}", exc_info=True)
        if conn and db_type_used == "postgres": conn.rollback()
    finally:
        if cursor and db_type_used == "postgres": # Only close PG cursor if it was used
            cursor.close()
        if conn and db_type_used == "postgres": # Only close PG conn if it was used
            conn.close()

# --- Helper function to send UI updates to ASF ---
def _send_ui_update(client_id: Optional[str], event_name: str, data: Dict[str, Any], workflow_id_for_log: Optional[str] = None):
    """
    Sends a UI update message to ASF's internal endpoint.
    This is a non-critical operation; failures are logged but do not halt orchestration.
    """
    log_extra = {'workflow_id': workflow_id_for_log, 'task_id': None}
    if not client_id:
        logger.info("No client_id provided, skipping UI update.", extra=log_extra)
        return

    if not CPOA_ASF_SEND_UI_UPDATE_URL:
        logger.warning("CPOA_ASF_SEND_UI_UPDATE_URL not configured. Cannot send UI update.", extra=log_extra)
        return

    payload = {
        "client_id": client_id,
        "event_name": event_name,
        "data": data
    }
    try:
        response = requests_with_retry(
            "post",
            CPOA_ASF_SEND_UI_UPDATE_URL,
            max_retries=1,
            backoff_factor=0.1,
            json=payload,
            timeout=5,
            workflow_id_for_log=workflow_id_for_log, # Pass context for retry logger
            task_id_for_log=None
        )
        if response.status_code == 200:
            logger.info(f"Successfully sent UI update '{event_name}' for client_id '{client_id}'.", extra=log_extra)
        else:
            logger.warning(f"Failed to send UI update '{event_name}' for client_id '{client_id}'. ASF responded with {response.status_code}: {response.text}", extra=log_extra)
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending UI update '{event_name}' for client_id '{client_id}' to ASF: {e}", exc_info=True, extra=log_extra)
    except Exception as e_unexp:
        logger.error(f"Unexpected error in _send_ui_update for client_id '{client_id}': {e_unexp}", exc_info=True, extra=log_extra)


def orchestrate_podcast_generation(
    topic: str,
    original_task_id: str, # ID from API Gateway, used for logging & correlation
    user_id: Optional[str] = None,
    voice_params_input: Optional[dict] = None,
    client_id: Optional[str] = None,
    user_preferences: Optional[dict] = None,
    test_scenarios: Optional[dict] = None
) -> Dict[str, Any]:

    workflow_id = _create_workflow_instance(
        trigger_event_type="podcast_generation",
        trigger_event_details={
            "topic": topic, "original_task_id": original_task_id,
            "voice_params_input": voice_params_input, "client_id": client_id,
            "user_preferences": user_preferences, "test_scenarios": test_scenarios
        },
        user_id=user_id
    )

    if not workflow_id:
        # Log with initial_log_extra as workflow_id is None
        logger.error(f"Failed to create workflow instance for podcast_generation (topic: {topic}). Aborting.", extra=initial_log_extra)
        return {"task_id": original_task_id, "workflow_id": None, "status": CPOA_STATUS_INIT_FAILURE,
                "error_message": "Workflow creation failed in CPOA state manager.",
                "orchestration_log": [], "final_audio_details": {}}

    # Use a logging adapter for this workflow, injecting workflow_id into all logs
    wf_logger = logging.LoggerAdapter(logger, {'workflow_id': workflow_id, 'task_id': None})

    _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_IN_PROGRESS)

    # This is the CPOA internal step log, distinct from the DB task_instances table
    orchestration_log_cpoa: List[Dict[str, Any]] = []

    vfa_result_dict: Dict[str, Any] = {"status": VFA_STATUS_NOT_RUN, "message": "VFA not reached."}
    current_orchestration_stage_legacy: str = ORCHESTRATION_STAGE_INITIALIZATION # For legacy log_step

    # final_cpoa_status is the legacy overall status for the 'podcasts' table entry
    final_cpoa_status_legacy: str = CPOA_STATUS_PENDING
    final_workflow_status: str = WORKFLOW_STATUS_IN_PROGRESS # For the new 'workflow_instances' table

    final_error_message: Optional[str] = None
    asf_notification_status_message: Optional[str] = None
    current_task_order = 0
    context_data_for_workflow = {} # Store key results like GCS URIs, script title etc.

    # Legacy log_step function (can be refactored or removed if orchestration_log_cpoa is not primary)
    def log_step_cpoa(message: str, data: Optional[Dict[str, Any]] = None, is_error_payload: bool = False) -> None:
        timestamp = datetime.now().isoformat()
        log_entry: Dict[str, Any] = {"timestamp": timestamp, "stage": current_orchestration_stage_legacy, "message": message}
        # ... (rest of log_step_cpoa implementation as before) ...
        orchestration_log_cpoa.append(log_entry)
        # wf_logger.info(f"Legacy log_step: {message}") # Avoid duplicate wf_logger here if it's just for orchestration_log_cpoa

    wf_logger.info(f"Podcast generation workflow started for topic: {topic}. Original Task ID: {original_task_id}")
    log_step_cpoa("Orchestration process started.", data={"original_task_id": original_task_id, "topic": topic, "voice_params_input": voice_params_input, "client_id": client_id, "user_preferences": user_preferences, "test_scenarios": test_scenarios})

    # Update legacy 'podcasts' table (this should eventually be phased out or integrated with workflow status)
    _update_task_status_in_db(original_task_id, CPOA_STATUS_WCHA_CONTENT_RETRIEVAL, workflow_id_for_log=workflow_id)

    try:
        # --- WCHA Stage ---
        current_orchestration_stage_legacy = ORCHESTRATION_STAGE_WCHA
        current_task_order += 1
        wcha_task_id = _create_task_instance(workflow_id, "WCHA", current_task_order, {"topic": topic}, initial_status=TASK_STATUS_DISPATCHED)
        
        _send_ui_update(client_id, UI_EVENT_GENERATION_STATUS, {"message": "Fetching web content...", "stage": current_orchestration_stage_legacy}, workflow_id_for_log=workflow_id)
        log_step_cpoa("Calling WCHA (get_content_for_topic)...", data={"topic": topic})
        
        wcha_content = None
        wcha_error_details = None
        wcha_output_summary = {}
        try:
            if not WCHA_IMPORT_SUCCESSFUL:
                raise ImportError(WCHA_MISSING_IMPORT_ERROR or "WCHA module not available.")

            if wcha_task_id: _update_task_instance_status(wcha_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            wcha_result_dict = get_content_for_topic(topic=topic) # Direct function call

            if isinstance(wcha_result_dict, dict) and wcha_result_dict.get("status") == "success":
                wcha_content = wcha_result_dict.get("content")
                source_urls = wcha_result_dict.get("source_urls", [])
                context_data_for_workflow["wcha_source_urls"] = source_urls
                wcha_output_summary = {"content_length": len(wcha_content) if wcha_content else 0, "source_urls": source_urls, "message": wcha_result_dict.get("message", "WCHA success.")}
                log_step_cpoa("WCHA finished successfully.", data=wcha_output_summary)
                if not wcha_content: wcha_error_details = {"message": wcha_output_summary.get("message") or "WCHA success but no content."}
            else:
                wcha_error_details = {"message": wcha_result_dict.get("message", "WCHA failed or unexpected structure."), "wcha_raw_output": wcha_result_dict}
                log_step_cpoa(f"WCHA content retrieval failure.", data=wcha_error_details, is_error_payload=True)
        except Exception as e_wcha:
            wcha_error_details = {"message": f"WCHA execution error: {str(e_wcha)}", "exception_type": type(e_wcha).__name__}
            wf_logger.error(f"WCHA stage error: {wcha_error_details['message']}", exc_info=True, extra={'task_id': wcha_task_id})

        if wcha_task_id:
            _update_task_instance_status(wcha_task_id, TASK_STATUS_COMPLETED if not wcha_error_details else TASK_STATUS_FAILED,
                                         output_summary=wcha_output_summary, error_details=wcha_error_details, workflow_id_for_log=workflow_id)
        if wcha_error_details or not wcha_content:
            final_error_message = (wcha_error_details.get("message") if wcha_error_details else None) or "WCHA critical failure: No content."
            final_cpoa_status_legacy = CPOA_STATUS_FAILED_WCHA_CONTENT_HARVEST
            raise Exception(final_error_message)

        # --- PSWA Stage ---
        current_orchestration_stage_legacy = ORCHESTRATION_STAGE_PSWA
        current_task_order += 1
        pswa_input_params = {"topic": topic, "content_input_length": len(wcha_content)}
        pswa_task_id = _create_task_instance(workflow_id, "PSWA", current_task_order, pswa_input_params, initial_status=TASK_STATUS_DISPATCHED)

        _update_task_status_in_db(original_task_id, CPOA_STATUS_PSWA_SCRIPT_GENERATION, workflow_id_for_log=workflow_id) # Legacy
        _send_ui_update(client_id, UI_EVENT_GENERATION_STATUS, {"message": "Crafting script...", "stage": current_orchestration_stage_legacy}, workflow_id_for_log=workflow_id)
        log_step_cpoa("Calling PSWA Service...", data={"url": PSWA_SERVICE_URL, **pswa_input_params})

        structured_script_from_pswa = None
        pswa_error_details = None
        pswa_output_summary = {}
        try:
            if pswa_task_id: _update_task_instance_status(pswa_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            pswa_payload = {"content": wcha_content, "topic": topic}
            pswa_headers = {'X-Test-Scenario': test_scenarios["pswa"]} if test_scenarios and test_scenarios.get("pswa") else {}

            response_pswa = requests_with_retry("post", PSWA_SERVICE_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                            json=pswa_payload, timeout=180, headers=pswa_headers,
                                            workflow_id_for_log=workflow_id, task_id_for_log=pswa_task_id)
            structured_script_from_pswa = response_pswa.json()

            if not (isinstance(structured_script_from_pswa, dict) and structured_script_from_pswa.get("script_id") and structured_script_from_pswa.get("title")):
                pswa_error_details = {"message": "PSWA service returned invalid or malformed structured script.", "received_script_preview": structured_script_from_pswa}
                log_step_cpoa(pswa_error_details["message"], data=pswa_error_details, is_error_payload=True)
            else:
                pswa_output_summary = {"script_id": structured_script_from_pswa.get("script_id"), "title": structured_script_from_pswa.get("title"), "segment_count": len(structured_script_from_pswa.get("segments", []))}
                log_step_cpoa("PSWA Service finished successfully.", data=pswa_output_summary)
        except requests.exceptions.RequestException as e_req_pswa:
            status_code = e_req_pswa.response.status_code if e_req_pswa.response is not None else "N/A"
            pswa_error_details = {"message": f"PSWA service call failed (HTTP status: {status_code}, type: {type(e_req_pswa).__name__}): {str(e_req_pswa)}." , "response_payload_preview": e_req_pswa.response.text[:200] if e_req_pswa.response is not None else "N/A"}
            log_step_cpoa("PSWA service request exception.", data=pswa_error_details, is_error_payload=True)
        except json.JSONDecodeError as e_json_pswa:
            pswa_error_details = {"message": f"PSWA service response was not valid JSON: {str(e_json_pswa)}", "response_text_preview": response_pswa.text[:200] if 'response_pswa' in locals() else "N/A"}
            log_step_cpoa("PSWA service JSON decode error.", data=pswa_error_details, is_error_payload=True)
        except Exception as e_pswa_unexp: # Catch any other unexpected error
            pswa_error_details = {"message": f"PSWA unexpected error: {str(e_pswa_unexp)}", "exception_type": type(e_pswa_unexp).__name__}
            wf_logger.error(f"PSWA stage unexpected error: {pswa_error_details['message']}", exc_info=True, extra={'task_id': pswa_task_id})

        if pswa_task_id:
            _update_task_instance_status(pswa_task_id, TASK_STATUS_COMPLETED if not pswa_error_details else TASK_STATUS_FAILED,
                                         output_summary=pswa_output_summary, error_details=pswa_error_details, workflow_id_for_log=workflow_id)
        if pswa_error_details or not structured_script_from_pswa:
            final_error_message = (pswa_error_details.get("message") if pswa_error_details else None) or "PSWA critical failure: No script."
            final_cpoa_status_legacy = CPOA_STATUS_FAILED_PSWA_REQUEST_EXCEPTION # Or appropriate legacy status
            raise Exception(final_error_message)

        context_data_for_workflow["script_title"] = structured_script_from_pswa.get("title")
        context_data_for_workflow["script_id"] = structured_script_from_pswa.get("script_id")


        # --- VFA Stage ---
        current_orchestration_stage_legacy = ORCHESTRATION_STAGE_VFA
        current_task_order += 1
        effective_voice_params = voice_params_input.copy() if voice_params_input else {}
        if user_preferences: # Apply preferences
            # ... (preference application logic as before) ...
            pass
        vfa_input_params = {"script_id": context_data_for_workflow["script_id"], "title": context_data_for_workflow["script_title"], "voice_params_input": effective_voice_params}
        vfa_task_id = _create_task_instance(workflow_id, "VFA", current_task_order, vfa_input_params, initial_status=TASK_STATUS_DISPATCHED)

        _update_task_status_in_db(original_task_id, CPOA_STATUS_VFA_AUDIO_GENERATION, workflow_id_for_log=workflow_id) # Legacy
        _send_ui_update(client_id, UI_EVENT_GENERATION_STATUS, {"message": "Synthesizing audio...", "stage": current_orchestration_stage_legacy}, workflow_id_for_log=workflow_id)
        log_step_cpoa("Calling VFA Service (forge_voice)...", data=vfa_input_params)

        vfa_error_details = None
        vfa_output_summary = {}
        try:
            if vfa_task_id: _update_task_instance_status(vfa_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            vfa_payload = {"script": structured_script_from_pswa, "voice_params": effective_voice_params}
            vfa_headers = {'X-Test-Scenario': test_scenarios["vfa"]} if test_scenarios and test_scenarios.get("vfa") else {}
            
            response_vfa = requests_with_retry("post", VFA_SERVICE_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                           json=vfa_payload, timeout=90, headers=vfa_headers,
                                           workflow_id_for_log=workflow_id, task_id_for_log=vfa_task_id)
            vfa_result_dict = response_vfa.json() # Overwrites initial placeholder

            vfa_output_summary = {"status": vfa_result_dict.get("status"), "audio_gcs_uri": vfa_result_dict.get("audio_filepath"), "stream_id": vfa_result_dict.get("stream_id"), "tts_settings_used": vfa_result_dict.get("tts_settings_used")}
            log_step_cpoa("VFA Service finished.", data=vfa_output_summary)

            if vfa_result_dict.get("status") != VFA_STATUS_SUCCESS:
                vfa_error_details = {"message": vfa_result_dict.get("message", "VFA reported non-success status."), "vfa_response": vfa_result_dict}
        except requests.exceptions.RequestException as e_req_vfa:
            status_code = e_req_vfa.response.status_code if e_req_vfa.response is not None else "N/A"
            vfa_error_details = {"message": f"VFA service call failed (HTTP status: {status_code}, type: {type(e_req_vfa).__name__}): {str(e_req_vfa)}.", "response_payload_preview": e_req_vfa.response.text[:200] if e_req_vfa.response is not None else "N/A"}
            vfa_result_dict = {"status": VFA_STATUS_ERROR, "message": vfa_error_details["message"]} # Ensure vfa_result_dict is set
        except json.JSONDecodeError as e_json_vfa:
            vfa_error_details = {"message": f"VFA service response was not valid JSON: {str(e_json_vfa)}", "response_text_preview": response_vfa.text[:200] if 'response_vfa' in locals() else "N/A"}
            vfa_result_dict = {"status": VFA_STATUS_ERROR, "message": vfa_error_details["message"]}
        except Exception as e_vfa_unexp:
            vfa_error_details = {"message": f"VFA unexpected error: {str(e_vfa_unexp)}", "exception_type": type(e_vfa_unexp).__name__}
            vfa_result_dict = {"status": VFA_STATUS_ERROR, "message": vfa_error_details["message"]}
            wf_logger.error(f"VFA stage unexpected error: {vfa_error_details['message']}", exc_info=True, extra={'task_id': vfa_task_id})

        if vfa_task_id:
             _update_task_instance_status(vfa_task_id, TASK_STATUS_COMPLETED if not vfa_error_details else TASK_STATUS_FAILED,
                                         output_summary=vfa_output_summary, error_details=vfa_error_details, workflow_id_for_log=workflow_id)

        if vfa_error_details or vfa_result_dict.get("status") != VFA_STATUS_SUCCESS:
            final_error_message = (vfa_error_details.get("message") if vfa_error_details else None) or "VFA critical failure."
            if vfa_result_dict.get("status") == VFA_STATUS_SKIPPED: final_cpoa_status_legacy = CPOA_STATUS_COMPLETED_WITH_VFA_SKIPPED
            else: final_cpoa_status_legacy = CPOA_STATUS_FAILED_VFA_REPORTED_ERROR
            raise Exception(final_error_message)

        context_data_for_workflow["final_audio_gcs_uri"] = vfa_result_dict.get("audio_filepath")
        context_data_for_workflow["stream_id"] = vfa_result_dict.get("stream_id")
        context_data_for_workflow["tts_settings_used"] = vfa_result_dict.get("tts_settings_used")
        final_cpoa_status_legacy = CPOA_STATUS_COMPLETED


        # --- ASF Notification Stage ---
        if context_data_for_workflow.get("final_audio_gcs_uri") and context_data_for_workflow.get("stream_id"):
            current_orchestration_stage_legacy = ORCHESTRATION_STAGE_ASF_NOTIFICATION
            current_task_order += 1
            asf_input_params = {"stream_id": context_data_for_workflow["stream_id"], "gcs_uri": context_data_for_workflow["final_audio_gcs_uri"]}
            asf_task_id = _create_task_instance(workflow_id, "ASF_NOTIFY", current_task_order, asf_input_params, initial_status=TASK_STATUS_DISPATCHED)

            _update_task_status_in_db(original_task_id, CPOA_STATUS_ASF_NOTIFICATION, workflow_id_for_log=workflow_id) # Legacy
            _send_ui_update(client_id, UI_EVENT_GENERATION_STATUS, {"message": "Preparing audio stream...", "stage": current_orchestration_stage_legacy}, workflow_id_for_log=workflow_id)
            log_step_cpoa("Notifying ASF about new audio...", data=asf_input_params)

            asf_error_details = None
            asf_output_summary = {}
            try:
                if asf_task_id: _update_task_instance_status(asf_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
                asf_payload = {"stream_id": context_data_for_workflow["stream_id"], "filepath": context_data_for_workflow["final_audio_gcs_uri"]}
                response_asf = requests_with_retry("post", ASF_NOTIFICATION_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                               json=asf_payload, timeout=10,
                                               workflow_id_for_log=workflow_id, task_id_for_log=asf_task_id)
                asf_notification_status_message = f"ASF notified successfully for stream {context_data_for_workflow['stream_id']}."
                asf_output_summary = {"message": asf_notification_status_message, "response_status": response_asf.status_code}
                log_step_cpoa(asf_notification_status_message, data=asf_output_summary)
            except Exception as e_asf:
                asf_error_details = {"message": f"ASF notification failed: {str(e_asf)}", "exception_type": type(e_asf).__name__}
                asf_notification_status_message = asf_error_details["message"] # For legacy field
                final_error_message = asf_error_details["message"]
                final_cpoa_status_legacy = CPOA_STATUS_COMPLETED_WITH_ASF_NOTIFICATION_FAILURE
                wf_logger.error(f"ASF Notification stage error: {asf_error_details['message']}", exc_info=True, extra={'task_id': asf_task_id})

            if asf_task_id:
                _update_task_instance_status(asf_task_id, TASK_STATUS_COMPLETED if not asf_error_details else TASK_STATUS_FAILED,
                                             output_summary=asf_output_summary, error_details=asf_error_details, workflow_id_for_log=workflow_id)
        else: # audio_filepath or stream_id missing
            asf_notification_status_message = "ASF notification skipped: audio_filepath or stream_id missing from VFA success response."
            log_step_cpoa(asf_notification_status_message, data={"vfa_result": vfa_result_dict}, is_error_payload=True)
            final_error_message = asf_notification_status_message
            final_cpoa_status_legacy = CPOA_STATUS_COMPLETED_WITH_VFA_DATA_MISSING
            wf_logger.warning(asf_notification_status_message, extra={'workflow_id': workflow_id, 'task_id': None})

        # Determine final workflow status based on legacy CPOA status
        if final_cpoa_status_legacy == CPOA_STATUS_COMPLETED:
            final_workflow_status = WORKFLOW_STATUS_COMPLETED
        elif final_cpoa_status_legacy.startswith("completed_with_"):
            final_workflow_status = WORKFLOW_STATUS_COMPLETED_WITH_ERRORS
        else: # Should have been caught by exceptions leading to WORKFLOW_STATUS_FAILED
            final_workflow_status = WORKFLOW_STATUS_FAILED


    except Exception as e_main_workflow:
        wf_logger.error(f"Podcast generation workflow critically failed at stage '{current_orchestration_stage_legacy}': {e_main_workflow}", exc_info=True)
        final_error_message = final_error_message or str(e_main_workflow)
        final_workflow_status = WORKFLOW_STATUS_FAILED

        # Ensure legacy status reflects a failure if not already specific from a caught block
        if not final_cpoa_status_legacy.startswith("failed_") and not final_cpoa_status_legacy.startswith("completed_with_"):
            final_cpoa_status_legacy = CPOA_STATUS_FAILED_UNKNOWN_STAGE_EXCEPTION

        _send_ui_update(client_id, UI_EVENT_TASK_ERROR, {"message": final_error_message, "stage": current_orchestration_stage_legacy, "final_status": final_cpoa_status_legacy}, workflow_id_for_log=workflow_id)

    # Final updates to persistent stores
    _update_task_status_in_db(original_task_id, final_cpoa_status_legacy, error_msg=final_error_message, workflow_id_for_log=workflow_id) # Legacy DB update
    _update_workflow_instance_status(workflow_id, final_workflow_status, context_data=context_data_for_workflow, error_message=final_error_message)

    current_orchestration_stage_legacy = ORCHESTRATION_STAGE_FINALIZATION
    log_step_cpoa(f"Orchestration process ended with status {final_workflow_status}.", data={"final_cpoa_status_legacy": final_cpoa_status_legacy, "final_error_message": final_error_message})
    wf_logger.info(f"Podcast generation workflow ended. Final status: {final_workflow_status}. Legacy CPOA status: {final_cpoa_status_legacy}.")

    # Send final UI update based on the new workflow_status
    if final_workflow_status == WORKFLOW_STATUS_FAILED or final_workflow_status == WORKFLOW_STATUS_COMPLETED_WITH_ERRORS :
        _send_ui_update(client_id, UI_EVENT_TASK_ERROR, {"message": final_error_message or f"Task ended with status: {final_workflow_status}", "final_status": final_workflow_status, "is_terminal": True}, workflow_id_for_log=workflow_id)
    elif final_workflow_status == WORKFLOW_STATUS_COMPLETED:
         _send_ui_update(client_id, UI_EVENT_GENERATION_STATUS, {"message": "Podcast generation complete!", "final_status": final_workflow_status, "is_terminal": True}, workflow_id_for_log=workflow_id)

    asf_ws_url = f"{ASF_WEBSOCKET_BASE_URL}?stream_id={context_data_for_workflow['stream_id']}" if context_data_for_workflow.get("stream_id") else None

    cpoa_final_result = {
        "task_id": original_task_id,
        "workflow_id": workflow_id,
        "topic": topic,
        "status": final_cpoa_status_legacy, # Return legacy status for now for API GW compatibility
        "error_message": final_error_message,
        "asf_notification_status": asf_notification_status_message,
        "asf_websocket_url": asf_ws_url,
        "final_audio_details": vfa_result_dict,
        "orchestration_log": orchestration_log_cpoa
    }
    if "tts_settings_used" not in vfa_result_dict and "tts_settings_used" in context_data_for_workflow: # Ensure tts_settings are in final_audio_details
         vfa_result_dict["tts_settings_used"] = context_data_for_workflow["tts_settings_used"]

    return cpoa_final_result


# --- Snippet DB Interaction ---
def _save_snippet_to_db(snippet_object: dict, db_path_sqlite: Optional[str] = None): # db_path_sqlite for SQLite fallback
    """Saves a single snippet object to the topics_snippets table."""
    conn = None
    cursor = None
    db_type_used = DATABASE_TYPE
    # Ensure snippet_id is a UUID string for PG, generate if missing
    snippet_id = str(snippet_object.get("snippet_id") or uuid.uuid4())


    try:
        conn = _get_cpoa_db_connection()

        keywords_data = snippet_object.get("keywords", [])
        original_topic_details_data = snippet_object.get("original_topic_details_from_tda")

        current_ts = datetime.now() # Use datetime obj for PG
        generation_timestamp_input = snippet_object.get("generation_timestamp", current_ts.isoformat())

        # Ensure generation_timestamp is a datetime object for PG
        if isinstance(generation_timestamp_input, str):
            try:
                generation_timestamp_to_save = datetime.fromisoformat(generation_timestamp_input.replace("Z", "+00:00"))
            except ValueError:
                logger.warning(f"Could not parse generation_timestamp string '{generation_timestamp_input}', using current time.")
                generation_timestamp_to_save = current_ts
        elif isinstance(generation_timestamp_input, datetime):
            generation_timestamp_to_save = generation_timestamp_input
        else: # Fallback for unexpected types
            logger.warning(f"Unexpected type for generation_timestamp '{type(generation_timestamp_input)}', using current time.")
            generation_timestamp_to_save = current_ts


        if conn: # PostgreSQL Path
            cursor = conn.cursor()

            sql = """
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
            params = (
                snippet_id, DB_TYPE_SNIPPET, snippet_object.get("title"),
                snippet_object.get("summary"), keywords_data, # Pass list/dict directly for JSONB
                snippet_object.get("source_url"), snippet_object.get("source_name"),
                original_topic_details_data, # Pass dict/list directly for JSONB
                snippet_object.get("llm_model_used"), snippet_object.get("cover_art_prompt"),
                snippet_object.get("image_url"), generation_timestamp_to_save, # datetime object
                current_ts, # datetime object for last_accessed
                snippet_object.get("relevance_score", 0.5)
            )
            cursor.execute(sql, params)
            conn.commit()
            logger.info(f"Saved/Replaced snippet {snippet_id} to PostgreSQL DB: {snippet_object.get('title')}")

        else: # SQLite Path
            db_type_used = "sqlite"
            sqlite_path = db_path_sqlite or CPOA_DATABASE_PATH
            if not sqlite_path:
                logger.error(f"SQLite DB path not available for saving snippet {snippet_id}.")
                return

            keywords_json = json.dumps(keywords_data)
            original_topic_details_json = json.dumps(original_topic_details_data) if original_topic_details_data else None
            gen_ts_iso = generation_timestamp_to_save.isoformat()
            last_acc_ts_iso = current_ts.isoformat()

            conn_sqlite = sqlite3.connect(sqlite_path)
            cursor_sqlite = conn_sqlite.cursor()
            cursor_sqlite.execute(
                """
                INSERT OR REPLACE INTO topics_snippets (
                    id, type, title, summary, keywords,
                    source_url, source_name, original_topic_details,
                    llm_model_used_for_snippet, cover_art_prompt, image_url,
                    generation_timestamp, last_accessed_timestamp, relevance_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snippet_id, DB_TYPE_SNIPPET, snippet_object.get("title"),
                    snippet_object.get("summary"), keywords_json,
                    snippet_object.get("source_url"), snippet_object.get("source_name"),
                    original_topic_details_json, snippet_object.get("llm_model_used"),
                    snippet_object.get("cover_art_prompt"), snippet_object.get("image_url"),
                    gen_ts_iso, last_acc_ts_iso, snippet_object.get("relevance_score", 0.5)
                )
            )
            conn_sqlite.commit()
            conn_sqlite.close()
            logger.info(f"Saved/Replaced snippet {snippet_id} to SQLite DB: {snippet_object.get('title')}")

    except (psycopg2.Error, sqlite3.Error) as e:
        logger.error(f"Database error saving snippet {snippet_id} (DB: {db_type_used}): {e}", exc_info=True)
        if conn and db_type_used == "postgres": conn.rollback()
    except Exception as e_unexp:
        logger.error(f"Unexpected error saving snippet {snippet_id} (DB: {db_type_used}): {e_unexp}", exc_info=True)
        if conn and db_type_used == "postgres": conn.rollback()
    finally:
        if cursor and db_type_used == "postgres": cursor.close()
        if conn and db_type_used == "postgres": conn.close()

# --- Topic Exploration DB Helper ---
def _get_topic_details_from_db(topic_id: str, db_path_sqlite: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches details for a specific topic_id from the topics_snippets table."""
    conn = None
    cursor = None
    db_type_used = DATABASE_TYPE
    # Ensure topic_id is a string for UUID compatibility if it isn't already
    topic_id_str = str(topic_id) if not isinstance(topic_id, str) else topic_id

    try:
        conn = _get_cpoa_db_connection()

        if conn: # PostgreSQL Path
            cursor = conn.cursor()
            sql = "SELECT * FROM topics_snippets WHERE id = %s AND type = %s;"
            cursor.execute(sql, (topic_id_str, DB_TYPE_TOPIC))
            row = cursor.fetchone()
            if row:
                # RealDictCursor already returns a dict-like object
                # Keywords are already JSONB, psycopg2 handles them as dict/list
                return dict(row)
            return None

        else: # SQLite Path
            db_type_used = "sqlite"
            sqlite_path = db_path_sqlite or CPOA_DATABASE_PATH
            if not sqlite_path:
                logger.error(f"SQLite DB path not available for fetching topic {topic_id_str}.")
                return None

            conn_sqlite = sqlite3.connect(sqlite_path)
            conn_sqlite.row_factory = sqlite3.Row
            cursor_sqlite = conn_sqlite.cursor()
            cursor_sqlite.execute("SELECT * FROM topics_snippets WHERE id = ? AND type = ?", (topic_id_str, DB_TYPE_TOPIC))
            row_sqlite = cursor_sqlite.fetchone()
            conn_sqlite.close()

            if row_sqlite:
                topic_details = dict(row_sqlite)
                if isinstance(topic_details.get('keywords'), str): # SQLite stores JSON as TEXT
                    try:
                        topic_details['keywords'] = json.loads(topic_details['keywords'])
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to decode keywords JSON (SQLite) for topic {topic_id_str}")
                        topic_details['keywords'] = []
                return topic_details
            return None

    except (psycopg2.Error, sqlite3.Error) as e:
        logger.error(f"Database error fetching topic {topic_id_str} (DB: {db_type_used}): {e}", exc_info=True)
        return None
    except Exception as e_unexp:
        logger.error(f"Unexpected error fetching topic {topic_id_str} (DB: {db_type_used}): {e_unexp}", exc_info=True)
        return None
    finally:
        if cursor and db_type_used == "postgres": cursor.close()
        if conn and db_type_used == "postgres": conn.close()


def orchestrate_snippet_generation(topic_info: dict) -> Dict[str, Any]:
    """
    Orchestrates snippet generation by calling the SnippetCraftAgent (SCA) service.
    """
    function_name = "orchestrate_snippet_generation"
    # --- Input Validation ---
    if not isinstance(topic_info, dict):
        logger.error(f"CPOA: {function_name} - Input 'topic_info' must be a dictionary. Received: {type(topic_info)}")
        return {"error": SCA_STATUS_REQUEST_INVALID, "details": "Input 'topic_info' must be a dictionary."}

    logger.info(f"CPOA: {function_name} called for topic_info: {topic_info.get('title_suggestion', 'N/A')}")

    topic_id = topic_info.get("topic_id")
    if topic_id and not isinstance(topic_id, str):
        logger.error(f"CPOA: {function_name} - If provided, 'topic_id' must be a string. Received: {type(topic_id)}")
        return {"error": SCA_STATUS_REQUEST_INVALID, "details": "If provided, 'topic_id' must be a string."}
    if not topic_id:
        topic_id = f"topic_adhoc_{uuid.uuid4().hex[:6]}" # Generate if not provided or if invalid type was ignored
        logger.warning(f"CPOA: {function_name} - 'topic_id' missing or invalid, generated adhoc topic_id: {topic_id}")
    
    content_brief = topic_info.get("title_suggestion") # Using title_suggestion as the content_brief
    if not content_brief or not isinstance(content_brief, str) or not content_brief.strip():
        logger.error(f"CPOA: {function_name} - 'title_suggestion' (for content_brief) must be a non-empty string. Received: '{content_brief}' for topic_id: {topic_id}.")
        return {"error": SCA_STATUS_REQUEST_INVALID, "details": "Missing or invalid 'title_suggestion' (must be a non-empty string)."}

    # Optional fields validation
    if "summary" in topic_info and not isinstance(topic_info["summary"], str):
        logger.warning(f"CPOA: {function_name} - 'summary' provided but not a string. Will be ignored or might cause issues downstream if SCA expects string. Topic_id: {topic_id}")
        # Decide if this is a hard error or just a warning. For now, warning.
    if "keywords" in topic_info and not (isinstance(topic_info["keywords"], list) and all(isinstance(kw, str) for kw in topic_info["keywords"])):
        logger.warning(f"CPOA: {function_name} - 'keywords' provided but not a list of strings. Will be ignored or might cause issues downstream. Topic_id: {topic_id}")
        # Decide if this is a hard error or just a warning. For now, warning.


    sca_payload = {
        "topic_id": topic_id,
        "content_brief": content_brief,
        "topic_info": topic_info # Pass the whole topic_info dict as it might contain other useful fields for SCA
    }

    logger.info(f"CPOA: {function_name} - Calling SCA Service for topic_id {topic_id}...")
    try:
        response = requests_with_retry("post", SCA_SERVICE_URL,
                                       max_retries=CPOA_SERVICE_RETRY_COUNT,
                                       backoff_factor=CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                       json=sca_payload, timeout=60)

        snippet_data = response.json() # This is the SnippetDataObject from SCA
        logger.info(f"CPOA: {function_name} - SCA Service call successful for topic_id {topic_id}. Snippet data received: {snippet_data.get('snippet_id')}")

        # Save the generated snippet to the database
        # db_path argument is removed from _save_snippet_to_db
        # It will use CPOA_DATABASE_PATH for SQLite if DATABASE_TYPE is sqlite
        # or _get_cpoa_db_connection for PostgreSQL.
        # No explicit db_path needed here anymore.

        # --- IGA Call for Cover Art ---
        cover_art_prompt = snippet_data.get("cover_art_prompt")
        if cover_art_prompt and IGA_SERVICE_URL:
            logger.info(f"CPOA: Orchestrating image generation for snippet '{snippet_data.get('snippet_id')}' with prompt: '{cover_art_prompt}'")
            iga_payload = {"prompt": cover_art_prompt}
            iga_endpoint = f"{IGA_SERVICE_URL.rstrip('/')}/generate_image"

            try:
                iga_response = requests_with_retry("post", iga_endpoint,
                                                   max_retries=CPOA_SERVICE_RETRY_COUNT,
                                                   backoff_factor=CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                                   json=iga_payload,
                                                   timeout=20) # IGA specific timeout

                iga_response_data = iga_response.json()
                if iga_response.status_code == 200 and iga_response_data.get("image_url"):
                    snippet_data["image_url"] = iga_response_data["image_url"]
                    logger.info(f"CPOA: Successfully received image_url '{snippet_data['image_url']}' from IGA for snippet '{snippet_data.get('snippet_id')}'.")
                else:
                    logger.warning(f"CPOA: IGA service responded with status {iga_response.status_code} or missing image_url for snippet '{snippet_data.get('snippet_id')}'. Response: {iga_response_data}")
                    snippet_data["image_url"] = None
            except requests.exceptions.RequestException as e_iga_req:
                logger.warning(f"CPOA: IGA service call failed for snippet '{snippet_data.get('snippet_id')}': {e_iga_req}", exc_info=True)
                snippet_data["image_url"] = None
            except json.JSONDecodeError as e_iga_json:
                logger.warning(f"CPOA: Failed to decode IGA response for snippet '{snippet_data.get('snippet_id')}': {e_iga_json}. Response text: {iga_response.text[:200] if 'iga_response' in locals() else 'N/A'}", exc_info=True)
                snippet_data["image_url"] = None
            except Exception as e_iga_unexpected:
                logger.error(f"CPOA: Unexpected error during IGA call for snippet '{snippet_data.get('snippet_id')}': {e_iga_unexpected}", exc_info=True)
                snippet_data["image_url"] = None
        elif not IGA_SERVICE_URL:
            logger.warning("CPOA: IGA_SERVICE_URL not configured. Skipping image generation for snippets.")
            snippet_data["image_url"] = None
        else: # No cover_art_prompt
             snippet_data["image_url"] = None

        # Now save to DB. _save_snippet_to_db will handle DB type.
        # If DATABASE_TYPE is "sqlite", it will use CPOA_DATABASE_PATH.
        _save_snippet_to_db(snippet_data)


        return snippet_data

    except requests.exceptions.RequestException as e_req:
        sca_err_payload_str = "N/A"
        status_code_str = "N/A"
        if hasattr(e_req, 'response') and e_req.response is not None:
            status_code_str = str(e_req.response.status_code)
            try:
                # SCA errors might be in "detail" or directly in the response body
                error_payload = e_req.response.json()
                sca_err_payload_str = error_payload.get("detail", json.dumps(error_payload))
            except json.JSONDecodeError:
                sca_err_payload_str = e_req.response.text[:200]

        error_message = f"SCA service call failed for topic_id {topic_id} after retries (HTTP status: {status_code_str}, type: {type(e_req).__name__}): {str(e_req)}. Response: {sca_err_payload_str}"
        logger.error(f"CPOA: {function_name} - {error_message}", exc_info=True)
        return {"error": SCA_STATUS_CALL_FAILED_AFTER_RETRIES, "details": error_message}

    except json.JSONDecodeError as e_json:
        error_message = f"SCA service response was not valid JSON for topic_id {topic_id}: {str(e_json)}"
        logger.error(f"CPOA: {function_name} - {error_message}", exc_info=True)
        return {"error": SCA_STATUS_RESPONSE_INVALID_JSON, "details": error_message, "raw_response": response.text[:500] if 'response' in locals() and response is not None else "N/A"}

    except Exception as e: # Catch-all for other unexpected errors
        error_message = f"Unexpected error during SCA call for topic_id {topic_id}: {str(e)}"
        logger.error(f"CPOA: {function_name} - {error_message}", exc_info=True)
        return {"error": SCA_STATUS_CALL_UNEXPECTED_ERROR, "details": error_message}


def pretty_print_orchestration_result(result: dict):
    """Helper to pretty print the orchestration result, parsing log data."""
    parsed_log = []
    if result and "orchestration_log" in result:
        for entry in result["orchestration_log"]:
            parsed_entry = entry.copy()
            if "data" in parsed_entry and isinstance(parsed_entry["data"], str):
                try:
                    # Attempt to parse if it's a JSON string, otherwise keep as string
                    data_content = parsed_entry["data"]
                    if data_content.startswith("{") and data_content.endswith("}") or \
                       data_content.startswith("[") and data_content.endswith("]"):
                        parsed_entry["data"] = json.loads(data_content)
                    # else it's likely a simple string message, keep as is
                except json.JSONDecodeError:
                    pass # Keep as string if not valid JSON
            parsed_log.append(parsed_entry)
        # Create a mutable copy of result to update the log
        result_copy = result.copy()
        result_copy["orchestration_log"] = parsed_log
        print(json.dumps(result_copy, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    print("--- CPOA: Testing Enhanced Podcast Orchestration ---")
    
    # The main function for testing will require PSWA and VFA services to be running.
    # For local testing, you would run pswa/main.py and vfa/main.py in separate terminals.
    
    # Use configured database path (relevant if testing SQLite path for __main__)
    # db_path_main = CPOA_DATABASE_PATH
    # logger.info(f"Using database path from environment for __main__: {db_path_main}")

    # __main__ block testing with SQLite directly might become less relevant
    # as primary DB moves to PostgreSQL. For now, keeping it minimal.
    # If DATABASE_TYPE is postgres, these direct SQLite connection in __main__ will not work
    # unless specifically handled or if CPOA functions are called which then use the new DB logic.

    # To test orchestrate_podcast_generation, it no longer takes db_path directly.
    # It uses the globally configured DATABASE_TYPE and associated paths/credentials.
    # So, ensure your .env for CPOA points to a test DB (SQLite or PG) for this block.

    # Example for testing (assuming DATABASE_TYPE and related vars are set in .env for CPOA):
    if DATABASE_TYPE == "sqlite" and CPOA_DATABASE_PATH:
        logger.info(f"__main__ testing with SQLite DB: {CPOA_DATABASE_PATH}")
        try:
            conn = sqlite3.connect(CPOA_DATABASE_PATH)
            cursor = conn.cursor()
            # Minimal schema for testing orchestrate_podcast_generation's _update_task_status_in_db
            cursor.execute("CREATE TABLE IF NOT EXISTS podcasts (podcast_id TEXT PRIMARY KEY, topic TEXT, cpoa_status TEXT, cpoa_error_message TEXT, last_updated_timestamp TEXT)")
            # Minimal schema for _save_snippet_to_db and _get_topic_details_from_db
            cursor.execute("CREATE TABLE IF NOT EXISTS topics_snippets (id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT, keywords TEXT, original_topic_details TEXT, llm_model_used_for_snippet TEXT, cover_art_prompt TEXT, image_url TEXT, generation_timestamp TEXT, last_accessed_timestamp TEXT, relevance_score REAL, source_url TEXT, source_name TEXT)")

            conn.commit()
        except sqlite3.Error as e: logger.error(f"Error creating test DB tables: {e}")
        finally:
            if conn: conn.close()

    sample_topic_1 = "AI in Healthcare"
    sample_task_id_1 = str(uuid.uuid4()) # Ensure UUID for PG path
    print(f"\nTest 1: Orchestrating for topic '{sample_topic_1}' (Task ID: {sample_task_id_1})")
    # Simulate initial record creation if needed for testing _update_task_status_in_db
    # This would now also depend on DATABASE_TYPE
    # For simplicity, this step is omitted here; assume task_id exists or test the creation path.

    # orchestrate_podcast_generation no longer takes db_path
    result1 = orchestrate_podcast_generation(topic=sample_topic_1, task_id=sample_task_id_1)
    print(f"\n--- Result for '{sample_topic_1}' ---")
    pretty_print_orchestration_result(result1)

    # sample_topic_2 = "The Future of Space Travel"
    # sample_task_id_2 = str(uuid.uuid4())
    # print(f"\nTest 2: Orchestrating for topic '{sample_topic_2}' (Task ID: {sample_task_id_2})")
    # result2 = orchestrate_podcast_generation(topic=sample_topic_2, task_id=sample_task_id_2)
    # print(f"\n--- Result for '{sample_topic_2}' ---")
    # pretty_print_orchestration_result(result2)

    # try:
    #     if DATABASE_TYPE == "sqlite" and CPOA_DATABASE_PATH and os.path.exists(CPOA_DATABASE_PATH):
    #         os.remove(CPOA_DATABASE_PATH) # Clean up SQLite test DB
    #         logger.info(f"Cleaned up SQLite test database: {CPOA_DATABASE_PATH}")
    # except OSError as e:
    #     logger.error(f"Error removing SQLite test database {CPOA_DATABASE_PATH}: {e}")

    print("\n--- CPOA orchestration testing with service calls complete ---")
    print(f"NOTE: Ensure PSWA, VFA, ASF, SCA services are running. DB used: {DATABASE_TYPE}")


    print("\n--- CPOA: Testing Snippet Generation ---")
    sample_topic_info_for_snippet = {
        "topic_id": "topic_snippet_test_001",
        "title_suggestion": "The Future of Renewable Energy",
        "summary": "A brief look into advancements in solar, wind, and other renewable energy sources.",
        "keywords": ["renewable energy", "solar power", "wind energy", "sustainability"]
    }
    snippet_result = orchestrate_snippet_generation(sample_topic_info_for_snippet)
    print("\n--- Result for Snippet Generation ---")
    # Using pretty_print_orchestration_result for consistency, though it's a simple dict here
    pretty_print_orchestration_result(snippet_result)

    sample_topic_info_no_id = {
        "title_suggestion": "Exploring Mars: The Next Frontier",
        "summary": "What challenges and opportunities await humans on the Red Planet?",
        "keywords": ["mars exploration", "space travel", "colonization", "nasa"]
    }
    snippet_result_no_id = orchestrate_snippet_generation(sample_topic_info_no_id)
    print("\n--- Result for Snippet Generation (No Topic ID initially) ---")
    pretty_print_orchestration_result(snippet_result_no_id)

    sample_topic_info_no_brief = {
        "topic_id": "topic_snippet_test_002",
        "summary": "This will fail due to missing title_suggestion for content_brief.",
        "keywords": ["error case", "missing field"]
    }
    snippet_result_no_brief = orchestrate_snippet_generation(sample_topic_info_no_brief)
    print("\n--- Result for Snippet Generation (Missing Content Brief) ---")
    pretty_print_orchestration_result(snippet_result_no_brief)

    print("\n--- CPOA All Tests Complete ---")


# --- Topic Exploration Orchestration ---
def orchestrate_topic_exploration(
    current_topic_id: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    depth_mode: str = "deeper", # Depth mode currently illustrative, not deeply implemented
    user_preferences: Optional[dict] = None # Added user_preferences
) -> List[Dict[str, Any]]:
    """
    Orchestrates topic exploration by getting related topics from TDA
    and then generating snippets for them via SCA.
    Returns a list of generated snippet objects or an error dictionary if validation fails early.
    """
    function_name = "orchestrate_topic_exploration"
    logger.info(f"CPOA: {function_name} called. Mode: {depth_mode}, Topic ID: {current_topic_id}, Keywords: {keywords}, UserPrefs: {user_preferences}")

    # --- Input Validation ---
    if current_topic_id and (not isinstance(current_topic_id, str) or not current_topic_id.strip()):
        logger.error(f"CPOA: {function_name} - 'current_topic_id' must be a non-empty string if provided. Received: '{current_topic_id}'")
        # Consistent with API Gateway, this function is internal but let's return a structure that can be propagated.
        # However, the original code raises ValueError, which might be fine for internal logic.
        # For now, let's align with raising ValueError as per existing pattern for this func.
        raise ValueError("'current_topic_id' must be a non-empty string if provided.")

    if keywords:
        if not isinstance(keywords, list) or not all(isinstance(kw, str) and kw.strip() for kw in keywords):
            logger.error(f"CPOA: {function_name} - 'keywords' must be a list of non-empty strings if provided. Received: {keywords}")
            raise ValueError("'keywords' must be a list of non-empty strings if provided.")

    if not isinstance(depth_mode, str) or not depth_mode.strip(): # Basic check for depth_mode
        logger.error(f"CPOA: {function_name} - 'depth_mode' must be a non-empty string. Received: '{depth_mode}'")
        raise ValueError("'depth_mode' must be a non-empty string.")
        # Could add validation for specific allowed values for depth_mode if they are defined.

    if user_preferences and not isinstance(user_preferences, dict):
        logger.error(f"CPOA: {function_name} - 'user_preferences' must be a dictionary if provided. Received: {type(user_preferences)}")
        raise ValueError("'user_preferences' must be a dictionary if provided.")

    # Log user preferences if available (moved after validation)
    if user_preferences and PREF_KEY_NEWS_CATEGORY in user_preferences:
        logger.info(f"CPOA: {function_name} - User preference for news category found: {user_preferences[PREF_KEY_NEWS_CATEGORY]}. This could influence TDA query if no specific keywords provided by user for this exploration call.")

    explored_snippets: List[Dict[str, Any]] = []
    query_for_tda = None
    original_topic_title = "original topic"

    if current_topic_id:
        # _get_topic_details_from_db no longer takes db_path directly
        original_topic = _get_topic_details_from_db(current_topic_id)
        if original_topic:
            original_topic_title = original_topic.get('title', current_topic_id)
            # For "deeper", let's refine the query. Could be more sophisticated.
            # Simple approach: use original keywords, or title if keywords are sparse.
            # TDA's own logic might be better at handling "deeper" if it had such a mode.
            # For now, we'll just re-query with existing keywords or title.
            if original_topic.get('keywords') and isinstance(original_topic.get('keywords'), list) and len(original_topic['keywords']) > 0 :
                 query_for_tda = " ".join(original_topic['keywords'])
            if not query_for_tda: # Fallback to title if no keywords
                 query_for_tda = original_topic_title
            logger.info(f"Exploring based on existing topic '{original_topic_title}'. Using query for TDA: '{query_for_tda}'")
        else:
            logger.warning(f"Could not find details for current_topic_id: {current_topic_id}. Proceeding with keywords if available.")
            # Fall through to use keywords if provided, otherwise error
            if not keywords:
                 logger.error(f"No details for topic_id {current_topic_id} and no keywords provided for exploration.")
                 return []


    if keywords: # If keywords are directly provided (or if topic_id lookup failed but keywords were also given)
        direct_keywords_query = " ".join(keywords)
        if query_for_tda and direct_keywords_query != query_for_tda : # If topic_id also yielded a query
            # Simple strategy: prioritize direct keywords if they differ significantly, or combine.
            # For now, let's say direct keywords override if they exist.
            query_for_tda = direct_keywords_query
            original_topic_title = f"keywords: {direct_keywords_query}" # Update context for logging
            logger.info(f"Exploring based on provided keywords. Overriding/using query for TDA: '{query_for_tda}'")
        elif not query_for_tda:
            query_for_tda = direct_keywords_query
            original_topic_title = f"keywords: {direct_keywords_query}"
            logger.info(f"Exploring based on provided keywords. Using query for TDA: '{query_for_tda}'")


    if not query_for_tda:
        logger.error("No valid query could be constructed for TDA (neither topic_id yielded info nor keywords provided).")
        raise ValueError("Cannot explore topic without a valid current_topic_id or keywords.")

    # Call TDA Service
    tda_topics = []
    try:
        logger.info(f"Calling TDA service for exploration. Query: '{query_for_tda}'")
        # Example: Request fewer, more focused topics for "deeper" exploration.
        tda_payload = {"query": query_for_tda, "limit": 3} # Adjust limit as needed

        # Ensure TDA_SERVICE_URL is available
        if not TDA_SERVICE_URL: # Accessing the global TDA_SERVICE_URL
            logger.error("TDA_SERVICE_URL is not configured. Cannot perform topic exploration.")
            return [] # Or raise an error

        response = requests_with_retry("post", TDA_SERVICE_URL, # Using global TDA_SERVICE_URL
                                       max_retries=CPOA_SERVICE_RETRY_COUNT,
                                       backoff_factor=CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                       json=tda_payload, timeout=30)
        tda_data = response.json()
        # TDA returns list of topics under "discovered_topics" or "topics" key
        tda_topics = tda_data.get("topics", tda_data.get("discovered_topics", []))
        logger.info(f"TDA returned {len(tda_topics)} topics for exploration based on '{original_topic_title}'.")

    except requests.exceptions.RequestException as e_req:
        logger.error(f"TDA service call failed during exploration: {e_req}")
        return [] # Or re-raise as a CPOA internal error
    except json.JSONDecodeError as e_json:
        logger.error(f"TDA service response was not valid JSON during exploration: {e_json}")
        return []

    if not tda_topics:
        logger.info(f"No further topics discovered by TDA for '{original_topic_title}'. Exploration ends.")
        return []

    # Generate snippets for these new/refined topics
    for topic_obj in tda_topics:
        # Adapt TDA's TopicObject to CPOA's expected topic_info structure for orchestrate_snippet_generation
        # Main thing is that orchestrate_snippet_generation expects "title_suggestion"
        # and a "topic_id" if available (TDA provides "topic_id" or "id").
        topic_info_for_sca = {
            "topic_id": topic_obj.get("topic_id") or topic_obj.get("id"),
            "title_suggestion": topic_obj.get("title_suggestion") or topic_obj.get("title"),
            "summary": topic_obj.get("summary"),
            "keywords": topic_obj.get("keywords", []),
            "original_topic_details_from_tda": topic_obj # Good to keep original TDA output if SCA needs more
        }

        if not topic_info_for_sca["title_suggestion"]:
            logger.warning(f"Skipping snippet generation for explored topic due to missing title: {topic_obj}")
            continue

        logger.info(f"Generating exploration snippet for: {topic_info_for_sca['title_suggestion']}")
        try:
            # client_id is not passed here, as these are "background" generations for exploration results
            snippet_result = orchestrate_snippet_generation(topic_info=topic_info_for_sca)
            if snippet_result and "error" not in snippet_result:
                explored_snippets.append(snippet_result)
            else:
                logger.error(f"Snippet generation failed for explored topic '{topic_info_for_sca['title_suggestion']}': {snippet_result.get('details', 'Unknown error')}")
        except Exception as e_snip:
            logger.error(f"Unexpected error calling orchestrate_snippet_generation for explored topic '{topic_info_for_sca['title_suggestion']}': {e_snip}", exc_info=True)

    logger.info(f"Topic exploration for '{original_topic_title}' yielded {len(explored_snippets)} new snippets.")
    return {"workflow_id": workflow_id, "explored_topics": []} # Placeholder


def orchestrate_search_results_generation(query: str, user_preferences: Optional[dict] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    workflow_id = _create_workflow_instance(
        trigger_event_type="search_results_generation",
        trigger_event_details={"query": query, "user_preferences": user_preferences},
        user_id=user_id
    )
    log_extra_wf = {'workflow_id': workflow_id, 'task_id': None}

    if not workflow_id:
        logger.error(f"Failed to create workflow instance for search_results_generation. Query: {query}", extra=log_extra_wf)
        return {"error": "WORKFLOW_CREATION_FAILED", "details": "Workflow creation failed.", "search_results": [], "workflow_id": None}

    wf_logger = logging.LoggerAdapter(logger, log_extra_wf)
    _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_IN_PROGRESS)
    wf_logger.info(f"Search results workflow started. Query: '{query}'")

    # --- Input Validation ---
    if not query or not isinstance(query, str) or not query.strip():
        error_msg = f"'query' must be a non-empty string. Received: '{query}'"
        wf_logger.error(f"Input validation failed: {error_msg}")
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=error_msg)
        return {"error": "CPOA_REQUEST_INVALID", "details": error_msg, "search_results": [], "workflow_id": workflow_id}

    if user_preferences and not isinstance(user_preferences, dict):
        error_msg = f"'user_preferences' must be a dictionary if provided. Received: {type(user_preferences)}"
        wf_logger.error(f"Input validation failed: {error_msg}")
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=error_msg)
        return {"error": "CPOA_REQUEST_INVALID", "details": error_msg, "search_results": [], "workflow_id": workflow_id}

    # --- TDA Task ---
    current_task_order = 1
    tda_input_params = {"query": query, "limit": 7} # Example limit for search
    tda_task_id = _create_task_instance(workflow_id, "TDA_Search", current_task_order, tda_input_params, initial_status=TASK_STATUS_DISPATCHED)

    tda_topics = []
    tda_error_details = None
    tda_output_summary = {}

    if not TDA_SERVICE_URL:
        tda_error_details = {"message": "TDA_SERVICE_URL is not configured."}
        wf_logger.error(tda_error_details["message"], extra={'task_id': tda_task_id})
    elif tda_task_id:
        try:
            _update_task_instance_status(tda_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            response = requests_with_retry("post", TDA_SERVICE_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                           json=tda_input_params, timeout=30,
                                           workflow_id_for_log=workflow_id, task_id_for_log=tda_task_id)
            tda_data = response.json()
            tda_topics = tda_data.get("topics", tda_data.get("discovered_topics", []))
            tda_output_summary = {"topic_count": len(tda_topics), "query_used": query}
            wf_logger.info(f"TDA returned {len(tda_topics)} topics for search query '{query}'.", extra={'task_id': tda_task_id})
            if not tda_topics:
                 tda_error_details = {"message": "TDA returned no topics for search query.", "tda_response": tda_data}
        except requests.exceptions.RequestException as e_req:
            tda_error_details = {"message": f"TDA service call failed for search: {str(e_req)}", "exception_type": type(e_req).__name__}
        except json.JSONDecodeError as e_json:
            tda_error_details = {"message": f"Failed to decode TDA response for search: {str(e_json)}", "response_preview": response.text[:200] if 'response' in locals() else "N/A"}
        except Exception as e_gen_tda:
            tda_error_details = {"message": f"Unexpected error during TDA call for search: {str(e_gen_tda)}", "exception_type": type(e_gen_tda).__name__}

        if tda_error_details:
            wf_logger.error(f"TDA_Search task failed: {tda_error_details['message']}", exc_info=True if "exception_type" in tda_error_details else False, extra={'task_id': tda_task_id})
        _update_task_instance_status(tda_task_id, TASK_STATUS_COMPLETED if not tda_error_details else TASK_STATUS_FAILED,
                                     output_summary=tda_output_summary, error_details=tda_error_details, workflow_id_for_log=workflow_id)

    if tda_error_details or not tda_topics:
        final_error_msg = (tda_error_details.get("message") if tda_error_details else None) or "TDA returned no topics for search."
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=final_error_msg, context_data={"tda_query": query})
        return {"error": "TDA_FAILURE", "details": final_error_msg, "search_results": [], "workflow_id": workflow_id}

    # --- Snippet Generation Loop ---
    search_results_snippets: List[Dict[str, Any]] = []
    final_workflow_status = WORKFLOW_STATUS_COMPLETED
    any_snippet_errors = False
    snippet_gen_task_base_order = current_task_order

    for i, topic_obj in enumerate(tda_topics):
        current_task_order = snippet_gen_task_base_order + i + 1
        if not isinstance(topic_obj, dict):
            wf_logger.warning(f"Skipping non-dictionary topic object from TDA (search): {topic_obj}")
            continue

        topic_info_for_sca = {
            "topic_id": topic_obj.get("topic_id") or topic_obj.get("id") or f"tda_topic_search_{i}",
            "title_suggestion": topic_obj.get("title_suggestion") or topic_obj.get("title"),
            "summary": topic_obj.get("summary"), "keywords": topic_obj.get("keywords", []),
            "original_topic_details_from_tda": topic_obj
        }
        if not topic_info_for_sca["title_suggestion"]:
            wf_logger.warning(f"Skipping TDA topic (search) due to missing title: {topic_obj}")
            continue

        sg_task_id = _create_task_instance(workflow_id, "SnippetGenerationSearchItem", current_task_order,
                                           {"topic_title": topic_info_for_sca["title_suggestion"]}, initial_status=TASK_STATUS_DISPATCHED)

        snippet_error_details = None
        snippet_result = None
        snippet_output_summary = {}
        try:
            if sg_task_id: _update_task_instance_status(sg_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            snippet_result = orchestrate_snippet_generation(topic_info=topic_info_for_sca, workflow_id_for_log=workflow_id)

            if snippet_result and "error" not in snippet_result:
                if "snippet_id" not in snippet_result and "id" in snippet_result: snippet_result["snippet_id"] = snippet_result["id"]
                elif "snippet_id" not in snippet_result: snippet_result["snippet_id"] = f"search_snippet_{uuid.uuid4().hex[:8]}"
                search_results_snippets.append(snippet_result)
                snippet_output_summary = {"snippet_id": snippet_result.get("snippet_id"), "image_url_present": bool(snippet_result.get("image_url"))}
                wf_logger.info(f"Successfully generated search snippet for topic: '{topic_info_for_sca['title_suggestion']}'", extra={'task_id': sg_task_id})
            else:
                snippet_error_details = {"message": snippet_result.get("details", "Search snippet generation returned error structure."), "sca_response": snippet_result}
                any_snippet_errors = True
        except Exception as e_snippet_gen:
            snippet_error_details = {"message": f"Error in orchestrate_snippet_generation call (search): {str(e_snippet_gen)}", "exception_type": type(e_snippet_gen).__name__}
            any_snippet_errors = True

        if snippet_error_details:
            wf_logger.warning(f"SnippetGenerationSearchItem task failed: {snippet_error_details['message']}", exc_info=True if "exception_type" in snippet_error_details else False, extra={'task_id': sg_task_id})

        if sg_task_id:
            _update_task_instance_status(sg_task_id, TASK_STATUS_COMPLETED if not snippet_error_details else TASK_STATUS_FAILED,
                                         output_summary=snippet_output_summary, error_details=snippet_error_details, workflow_id_for_log=workflow_id)

    final_error_message_wf = None
    if not search_results_snippets:
        final_workflow_status = WORKFLOW_STATUS_FAILED if any_snippet_errors else WORKFLOW_STATUS_COMPLETED
        final_error_message_wf = "No search snippets generated" + (" due to errors." if any_snippet_errors else " (TDA might have returned too few topics).")
        wf_logger.info(final_error_message_wf)
    elif any_snippet_errors:
        final_workflow_status = WORKFLOW_STATUS_COMPLETED_WITH_ERRORS
        final_error_message_wf = "Some search snippets could not be generated."
        wf_logger.warning(final_error_message_wf)

    _update_workflow_instance_status(workflow_id, final_workflow_status,
                                     context_data={"generated_snippet_count": len(search_results_snippets), "search_query": query},
                                     error_message=final_error_message_wf)

    if not search_results_snippets and (final_workflow_status == WORKFLOW_STATUS_FAILED or not tda_topics): # If nothing from TDA or all snippet gens failed
         return {"workflow_id": workflow_id, "search_results": [], "error": final_workflow_status, "details": final_error_message_wf or "No results found or error in processing."}

    wf_logger.info(f"Search results workflow generated {len(search_results_snippets)} snippets for query '{query}'.")
    return {"workflow_id": workflow_id, "search_results": search_results_snippets}


# --- Topic Exploration Orchestration ---
def orchestrate_topic_exploration(
    current_topic_id: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    depth_mode: str = "deeper", # Depth mode currently illustrative, not deeply implemented
    user_preferences: Optional[dict] = None, # Added user_preferences
    user_id: Optional[str] = None # New parameter
) -> Dict[str, Any]: # Return type changed to Dict to include workflow_id
    workflow_id = _create_workflow_instance(
        trigger_event_type="topic_exploration",
        trigger_event_details={"current_topic_id": current_topic_id, "keywords": keywords, "depth_mode": depth_mode, "user_preferences": user_preferences},
        user_id=user_id
    )
    log_extra_wf = {'workflow_id': workflow_id, 'task_id': None}

    if not workflow_id:
        logger.error(f"Failed to create workflow instance for topic_exploration. Inputs: current_topic_id={current_topic_id}, keywords={keywords}", extra=log_extra_wf)
        # Previous version raised ValueError, now returning a dict for consistency from top-level orchestrators
        return {"error": "WORKFLOW_CREATION_FAILED", "details": "Workflow creation failed.", "explored_topics": [], "workflow_id": None}

    wf_logger = logging.LoggerAdapter(logger, log_extra_wf)
    _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_IN_PROGRESS)
    wf_logger.info(f"Topic exploration workflow started. Mode: {depth_mode}, Topic ID: {current_topic_id}, Keywords: {keywords}")

    # --- Input Validation (as before, but use wf_logger and update workflow on error) ---
    if current_topic_id and (not isinstance(current_topic_id, str) or not current_topic_id.strip()):
        error_msg = f"'current_topic_id' must be a non-empty string if provided. Received: '{current_topic_id}'"
        wf_logger.error(f"Input validation failed: {error_msg}")
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=error_msg)
        return {"error": "CPOA_REQUEST_INVALID", "details": error_msg, "explored_topics": [], "workflow_id": workflow_id}
    # ... (other input validations for keywords, depth_mode, user_preferences - similar error handling) ...

    # --- Query Construction for TDA (as before) ---
    query_for_tda = None
    original_topic_title = "original topic for exploration" # More descriptive default
    # ... (logic to determine query_for_tda based on current_topic_id or keywords, logging with wf_logger) ...
    if not query_for_tda: # If after all logic, no query is formed
        error_msg = "No valid query could be constructed for TDA (topic_id lookup failed and no keywords provided)."
        wf_logger.error(error_msg)
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=error_msg)
        return {"error": "TDA_QUERY_CONSTRUCTION_FAILED", "details": error_msg, "explored_topics": [], "workflow_id": workflow_id}


    # --- TDA Task ---
    current_task_order = 1
    tda_input_params = {"query": query_for_tda, "limit": 3} # Example limit for exploration
    tda_task_id = _create_task_instance(workflow_id, "TDA_Exploration", current_task_order, tda_input_params, initial_status=TASK_STATUS_DISPATCHED)

    tda_topics = []
    tda_error_details = None
    tda_output_summary = {}
    if not TDA_SERVICE_URL:
        tda_error_details = {"message": "TDA_SERVICE_URL is not configured."}
        wf_logger.error(tda_error_details["message"], extra={'task_id': tda_task_id})
    elif tda_task_id:
        try:
            _update_task_instance_status(tda_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            response = requests_with_retry("post", TDA_SERVICE_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                           json=tda_input_params, timeout=30,
                                           workflow_id_for_log=workflow_id, task_id_for_log=tda_task_id)
            tda_data = response.json()
            tda_topics = tda_data.get("topics", tda_data.get("discovered_topics", []))
            tda_output_summary = {"topic_count": len(tda_topics), "query_used": query_for_tda}
            wf_logger.info(f"TDA returned {len(tda_topics)} topics for exploration based on '{original_topic_title}'.", extra={'task_id': tda_task_id})
            if not tda_topics:
                tda_error_details = {"message": "TDA returned no topics for exploration.", "tda_response": tda_data}
        except requests.exceptions.RequestException as e_req:
            tda_error_details = {"message": f"TDA service call failed during exploration: {str(e_req)}", "exception_type": type(e_req).__name__}
        except json.JSONDecodeError as e_json:
             tda_error_details = {"message": f"Failed to decode TDA response for exploration: {str(e_json)}", "response_preview": response.text[:200] if 'response' in locals() else "N/A"}
        except Exception as e_gen_tda:
            tda_error_details = {"message": f"Unexpected error during TDA call for exploration: {str(e_gen_tda)}", "exception_type": type(e_gen_tda).__name__}

        if tda_error_details:
            wf_logger.error(f"TDA_Exploration task failed: {tda_error_details['message']}", exc_info=True if "exception_type" in tda_error_details else False, extra={'task_id': tda_task_id})
        _update_task_instance_status(tda_task_id, TASK_STATUS_COMPLETED if not tda_error_details else TASK_STATUS_FAILED,
                                     output_summary=tda_output_summary, error_details=tda_error_details, workflow_id_for_log=workflow_id)

    if tda_error_details or not tda_topics:
        final_error_msg = (tda_error_details.get("message") if tda_error_details else None) or "TDA returned no topics for exploration."
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=final_error_msg, context_data={"tda_query": query_for_tda})
        return {"error": "TDA_FAILURE", "details": final_error_msg, "explored_topics": [], "workflow_id": workflow_id}

    # --- Snippet Generation Loop for Explored Topics ---
    explored_snippets: List[Dict[str, Any]] = []
    final_workflow_status = WORKFLOW_STATUS_COMPLETED
    any_snippet_errors = False
    snippet_gen_task_base_order = current_task_order

    for i, topic_obj in enumerate(tda_topics):
        current_task_order = snippet_gen_task_base_order + i + 1
        if not isinstance(topic_obj, dict):
            wf_logger.warning(f"Skipping non-dictionary topic object from TDA (exploration): {topic_obj}")
            continue

        topic_info_for_sca = { # Adapt TDA object
            "topic_id": topic_obj.get("topic_id") or topic_obj.get("id") or f"tda_topic_explore_{i}",
            "title_suggestion": topic_obj.get("title_suggestion") or topic_obj.get("title"),
            "summary": topic_obj.get("summary"), "keywords": topic_obj.get("keywords", []),
            "original_topic_details_from_tda": topic_obj
        }
        if not topic_info_for_sca["title_suggestion"]:
            wf_logger.warning(f"Skipping TDA topic (exploration) due to missing title: {topic_obj}")
            continue

        sg_task_id = _create_task_instance(workflow_id, "SnippetGenerationExplorationItem", current_task_order,
                                           {"topic_title": topic_info_for_sca["title_suggestion"]}, initial_status=TASK_STATUS_DISPATCHED)

        snippet_error_details = None
        snippet_result = None
        snippet_output_summary = {}
        try:
            if sg_task_id: _update_task_instance_status(sg_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            snippet_result = orchestrate_snippet_generation(topic_info=topic_info_for_sca, workflow_id_for_log=workflow_id)

            if snippet_result and "error" not in snippet_result:
                explored_snippets.append(snippet_result)
                snippet_output_summary = {"snippet_id": snippet_result.get("snippet_id"), "image_url_present": bool(snippet_result.get("image_url"))}
                wf_logger.info(f"Successfully generated exploration snippet for topic: '{topic_info_for_sca['title_suggestion']}'", extra={'task_id': sg_task_id})
            else:
                snippet_error_details = {"message": snippet_result.get("details", "Exploration snippet generation returned error structure."), "sca_response": snippet_result}
                any_snippet_errors = True
        except Exception as e_snippet_gen:
            snippet_error_details = {"message": f"Error in orchestrate_snippet_generation call (exploration): {str(e_snippet_gen)}", "exception_type": type(e_snippet_gen).__name__}
            any_snippet_errors = True

        if snippet_error_details:
            wf_logger.warning(f"SnippetGenerationExplorationItem task failed: {snippet_error_details['message']}", exc_info=True if "exception_type" in snippet_error_details else False, extra={'task_id': sg_task_id})

        if sg_task_id:
            _update_task_instance_status(sg_task_id, TASK_STATUS_COMPLETED if not snippet_error_details else TASK_STATUS_FAILED,
                                         output_summary=snippet_output_summary, error_details=snippet_error_details, workflow_id_for_log=workflow_id)

    final_error_message_wf = None
    if not explored_snippets:
        final_workflow_status = WORKFLOW_STATUS_FAILED if any_snippet_errors else WORKFLOW_STATUS_COMPLETED
        final_error_message_wf = "No explored snippets generated" + (" due to errors." if any_snippet_errors else " (TDA might have returned too few topics).")
        wf_logger.info(final_error_message_wf)
    elif any_snippet_errors:
        final_workflow_status = WORKFLOW_STATUS_COMPLETED_WITH_ERRORS
        final_error_message_wf = "Some explored snippets could not be generated."
        wf_logger.warning(final_error_message_wf)

    _update_workflow_instance_status(workflow_id, final_workflow_status,
                                     context_data={"generated_snippet_count": len(explored_snippets), "tda_query": query_for_tda},
                                     error_message=final_error_message_wf)

    if not explored_snippets and (final_workflow_status == WORKFLOW_STATUS_FAILED or not tda_topics):
         return {"workflow_id": workflow_id, "explored_topics": [], "error": final_workflow_status, "details": final_error_message_wf or "No explored topics found or error in processing."}

    wf_logger.info(f"Topic exploration workflow generated {len(explored_snippets)} explored snippets.")
    return {"workflow_id": workflow_id, "explored_topics": explored_snippets}


def orchestrate_landing_page_snippets(limit: int = 5, user_preferences: Optional[dict] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    workflow_id = _create_workflow_instance(
        trigger_event_type="landing_page_snippets",
        trigger_event_details={"limit": limit, "user_preferences": user_preferences},
        user_id=user_id
    )
    log_extra_wf = {'workflow_id': workflow_id, 'task_id': None} # For logs before wf_logger is set

    if not workflow_id:
        logger.error(f"Failed to create workflow instance for landing_page_snippets. Aborting.", extra=log_extra_wf)
        return {"error": "WORKFLOW_CREATION_FAILED", "details": "Workflow creation failed.", "snippets": [], "workflow_id": None}

    wf_logger = logging.LoggerAdapter(logger, log_extra_wf)
    _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_IN_PROGRESS)
    wf_logger.info(f"Landing page snippets workflow started. Limit: {limit}")

    # --- Input Validation ---
    if not isinstance(limit, int) or not (1 <= limit <= 20):
        error_msg = f"'limit' must be an integer between 1 and 20. Received: {limit}"
        wf_logger.error(f"Input validation failed: {error_msg}")
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=error_msg)
        return {"error": "CPOA_REQUEST_INVALID", "details": error_msg, "snippets": [], "workflow_id": workflow_id}

    if user_preferences and not isinstance(user_preferences, dict):
        error_msg = f"'user_preferences' must be a dictionary if provided. Received: {type(user_preferences)}"
        wf_logger.error(f"Input validation failed: {error_msg}")
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=error_msg)
        return {"error": "CPOA_REQUEST_INVALID", "details": error_msg, "snippets": [], "workflow_id": workflow_id}

    # --- Query Construction for TDA ---
    default_keywords = ["technology", "science", "lifestyle", "business", "arts", "global news", "innovation", "culture"]
    query_for_tda = None
    if user_preferences and isinstance(user_preferences.get("preferred_categories"), list) and user_preferences["preferred_categories"]:
        query_for_tda = " ".join(user_preferences["preferred_categories"])
    elif user_preferences and isinstance(user_preferences.get(PREF_KEY_NEWS_CATEGORY), str) and user_preferences[PREF_KEY_NEWS_CATEGORY]:
        query_for_tda = user_preferences[PREF_KEY_NEWS_CATEGORY]
    else:
        query_for_tda = " ".join(random.sample(default_keywords, min(len(default_keywords), 3)))
    wf_logger.info(f"Using TDA query: '{query_for_tda}'")

    if not query_for_tda: # Should be rare with defaults
        error_msg = "Failed to construct a query for TDA."
        wf_logger.error(error_msg)
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=error_msg)
        return {"error": "TDA_QUERY_EMPTY", "details": error_msg, "snippets": [], "workflow_id": workflow_id}

    # --- TDA Task ---
    current_task_order = 1
    tda_input_params = {"query": query_for_tda, "limit": limit * 2} # Fetch more to have a buffer
    tda_task_id = _create_task_instance(workflow_id, "TDA", current_task_order, tda_input_params, initial_status=TASK_STATUS_DISPATCHED)

    tda_topics = []
    tda_error_details = None
    tda_output_summary = {}

    if not TDA_SERVICE_URL:
        tda_error_details = {"message": "TDA_SERVICE_URL is not configured."}
        wf_logger.error(tda_error_details["message"], extra={'task_id': tda_task_id})
    elif tda_task_id:
        try:
            _update_task_instance_status(tda_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            response = requests_with_retry("post", TDA_SERVICE_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                           json=tda_input_params, timeout=30,
                                           workflow_id_for_log=workflow_id, task_id_for_log=tda_task_id)
            tda_data = response.json()
            tda_topics = tda_data.get("topics", tda_data.get("discovered_topics", []))
            tda_output_summary = {"topic_count": len(tda_topics), "query_used": query_for_tda}
            wf_logger.info(f"TDA returned {len(tda_topics)} topics.", extra={'task_id': tda_task_id})
            if not tda_topics:
                tda_error_details = {"message": "TDA returned no topics.", "tda_response": tda_data}
        except requests.exceptions.RequestException as e_req:
            tda_error_details = {"message": f"TDA service call failed: {str(e_req)}", "exception_type": type(e_req).__name__}
        except json.JSONDecodeError as e_json:
            tda_error_details = {"message": f"Failed to decode TDA response: {str(e_json)}", "response_preview": response.text[:200] if 'response' in locals() else "N/A"}
        except Exception as e_gen_tda:
            tda_error_details = {"message": f"Unexpected error during TDA call: {str(e_gen_tda)}", "exception_type": type(e_gen_tda).__name__}

        if tda_error_details:
             wf_logger.error(f"TDA task failed: {tda_error_details['message']}", exc_info=True if "exception_type" in tda_error_details else False, extra={'task_id': tda_task_id})
        _update_task_instance_status(tda_task_id, TASK_STATUS_COMPLETED if not tda_error_details else TASK_STATUS_FAILED,
                                     output_summary=tda_output_summary, error_details=tda_error_details, workflow_id_for_log=workflow_id)

    if tda_error_details or not tda_topics:
        final_error_msg = (tda_error_details.get("message") if tda_error_details else None) or "TDA returned no topics for landing page."
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=final_error_msg, context_data={"tda_query": query_for_tda})
        return {"error": "TDA_FAILURE", "details": final_error_msg, "snippets": [], "workflow_id": workflow_id}

    # --- Snippet Generation Loop ---
    generated_snippets: List[Dict[str, Any]] = []
    final_workflow_status = WORKFLOW_STATUS_COMPLETED
    any_snippet_errors = False
    snippet_gen_task_base_order = current_task_order # To increment from here

    for i, topic_obj in enumerate(tda_topics):
        if len(generated_snippets) >= limit: break
        current_task_order = snippet_gen_task_base_order + i + 1

        if not isinstance(topic_obj, dict):
            wf_logger.warning(f"Skipping non-dictionary topic object from TDA: {topic_obj}")
            continue

        topic_info_for_sca = {
            "topic_id": topic_obj.get("topic_id") or topic_obj.get("id") or f"tda_topic_lp_{i}",
            "title_suggestion": topic_obj.get("title_suggestion") or topic_obj.get("title"),
            "summary": topic_obj.get("summary"), "keywords": topic_obj.get("keywords", []),
            "original_topic_details_from_tda": topic_obj
        }
        if not topic_info_for_sca["title_suggestion"]:
            wf_logger.warning(f"Skipping TDA topic due to missing title: {topic_obj}")
            continue

        sg_task_id = _create_task_instance(workflow_id, "SnippetGenerationLoopItem", current_task_order,
                                           {"topic_title": topic_info_for_sca["title_suggestion"]}, initial_status=TASK_STATUS_DISPATCHED)

        snippet_error_details = None
        snippet_result = None
        snippet_output_summary = {}
        try:
            if sg_task_id: _update_task_instance_status(sg_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            # Pass workflow_id for logging context within orchestrate_snippet_generation
            snippet_result = orchestrate_snippet_generation(topic_info=topic_info_for_sca, workflow_id_for_log=workflow_id)

            if snippet_result and "error" not in snippet_result:
                generated_snippets.append(snippet_result)
                snippet_output_summary = {"snippet_id": snippet_result.get("snippet_id"), "image_url_present": bool(snippet_result.get("image_url"))}
                wf_logger.info(f"Successfully generated snippet for topic: '{topic_info_for_sca['title_suggestion']}'", extra={'task_id': sg_task_id})
            else:
                snippet_error_details = {"message": snippet_result.get("details", "Snippet generation returned error structure."), "sca_response": snippet_result}
                any_snippet_errors = True
        except Exception as e_snippet_gen:
            snippet_error_details = {"message": f"Error in orchestrate_snippet_generation call: {str(e_snippet_gen)}", "exception_type": type(e_snippet_gen).__name__}
            any_snippet_errors = True

        if snippet_error_details:
             wf_logger.warning(f"SnippetGenerationLoopItem task failed: {snippet_error_details['message']}", exc_info=True if "exception_type" in snippet_error_details else False, extra={'task_id': sg_task_id})

        if sg_task_id:
            _update_task_instance_status(sg_task_id, TASK_STATUS_COMPLETED if not snippet_error_details else TASK_STATUS_FAILED,
                                         output_summary=snippet_output_summary, error_details=snippet_error_details, workflow_id_for_log=workflow_id)

    final_error_message_wf = None
    if not generated_snippets:
        final_workflow_status = WORKFLOW_STATUS_FAILED if any_snippet_errors else WORKFLOW_STATUS_COMPLETED # Completed if TDA gave no topics but no errors occurred
        final_error_message_wf = "No snippets generated" + (" due to errors." if any_snippet_errors else " (TDA might have returned too few topics).")
        wf_logger.info(final_error_message_wf)
    elif any_snippet_errors:
        final_workflow_status = WORKFLOW_STATUS_COMPLETED_WITH_ERRORS
        final_error_message_wf = "Some snippets could not be generated for landing page."
        wf_logger.warning(final_error_message_wf)

    _update_workflow_instance_status(workflow_id, final_workflow_status,
                                     context_data={"generated_snippet_count": len(generated_snippets), "requested_limit": limit, "tda_query": query_for_tda},
                                     error_message=final_error_message_wf)

    if not generated_snippets:
         return {"message": "NO_SNIPPETS_GENERATED", "details": final_error_message_wf or "Failed to generate any snippets.", "snippets": [], "workflow_id": workflow_id}

    wf_logger.info(f"Landing page snippets workflow successfully generated {len(generated_snippets)} snippets.")
    return {"workflow_id": workflow_id, "snippets": generated_snippets, "source": "generation"}


def get_popular_categories() -> Dict[str, Any]:
    """
    Returns a predefined list of popular podcast categories.
    In the future, this could be made dynamic, e.g., by analyzing TDA output
    or other metrics.
    """
    logger.info("CPOA: get_popular_categories called. Returning predefined list.")
    # This list should match the one intended for the frontend UI design.
    predefined_categories = [
        "Business",
        "Technology",
        "Lifestyle",
        "Entertainment",
        "Health",
        "Science",
        "Education",
        "Arts"
    ]
    # The API Gateway expects a dictionary like {"categories": [...]}
    return {"categories": predefined_categories}