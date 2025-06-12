import logging
import sys
import os
from dotenv import load_dotenv # Added
import json
from datetime import datetime
import uuid
from typing import Optional, Dict, Any, List
import requests # Added for service calls
import time # Added for retry logic
import psycopg2 # Added for PostgreSQL
from psycopg2.extras import RealDictCursor # Added for PostgreSQL
import random # Added for landing page snippet keyword randomization
from celery import Celery
from celery.result import AsyncResult
from flask import Flask, jsonify as flask_jsonify # To avoid conflict if jsonify is used elsewhere

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


# --- Celery Configuration ---
CPOA_CELERY_BROKER_URL = os.getenv('CPOA_CELERY_BROKER_URL', 'redis://redis:6379/0')
CPOA_CELERY_RESULT_BACKEND = os.getenv('CPOA_CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

celery_app = Celery(
    'cpoa_tasks',
    broker=CPOA_CELERY_BROKER_URL,
    backend=CPOA_CELERY_RESULT_BACKEND,
    include=['aethercast.cpoa.main'] # Ensure tasks are discoverable
)
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

# --- Service URLs ---
PSWA_SERVICE_URL = os.getenv("PSWA_SERVICE_URL", "http://localhost:5004/weave_script")
VFA_SERVICE_URL = os.getenv("VFA_SERVICE_URL", "http://localhost:5005/forge_voice")
ASF_NOTIFICATION_URL = os.getenv("ASF_NOTIFICATION_URL", "http://localhost:5006/asf/internal/notify_new_audio")
ASF_WEBSOCKET_BASE_URL = os.getenv("ASF_WEBSOCKET_BASE_URL", "ws://localhost:5006/api/v1/podcasts/stream")
SCA_SERVICE_URL = os.getenv("SCA_SERVICE_URL", "http://localhost:5002/craft_snippet")
CPOA_ASF_SEND_UI_UPDATE_URL = os.getenv("CPOA_ASF_SEND_UI_UPDATE_URL", "http://localhost:5006/asf/internal/send_ui_update")
IGA_SERVICE_URL = os.getenv("IGA_SERVICE_URL", "http://localhost:5007")
TDA_SERVICE_URL = os.getenv("TDA_SERVICE_URL", "http://localhost:5000/discover_topics")
# WCHA_ASYNC_CONTENT_URL = os.getenv("WCHA_ASYNC_CONTENT_URL", "http://wcha:5003/v1/async_get_content_for_topic") # Old, to be removed
WCHA_SERVICE_BASE_URL = os.getenv("WCHA_SERVICE_BASE_URL", "http://wcha:5003") # New base URL for WCHA

# Retry Configuration
CPOA_SERVICE_RETRY_COUNT = int(os.getenv("CPOA_SERVICE_RETRY_COUNT", "3"))
CPOA_SERVICE_RETRY_BACKOFF_FACTOR = float(os.getenv("CPOA_SERVICE_RETRY_BACKOFF_FACTOR", "0.5"))

# Polling Configuration for PSWA (and potentially others later)
CPOA_PSWA_POLLING_INTERVAL_SECONDS = int(os.getenv("CPOA_PSWA_POLLING_INTERVAL_SECONDS", "5"))
CPOA_PSWA_POLLING_TIMEOUT_SECONDS = int(os.getenv("CPOA_PSWA_POLLING_TIMEOUT_SECONDS", "600")) # 10 minutes

# Polling Configuration for SCA
CPOA_SCA_POLLING_INTERVAL_SECONDS = int(os.getenv("CPOA_SCA_POLLING_INTERVAL_SECONDS", "3"))
CPOA_SCA_POLLING_TIMEOUT_SECONDS = int(os.getenv("CPOA_SCA_POLLING_TIMEOUT_SECONDS", "180")) # 3 minutes for snippet crafting

# Polling Configuration for VFA
CPOA_VFA_POLLING_INTERVAL_SECONDS = int(os.getenv("CPOA_VFA_POLLING_INTERVAL_SECONDS", "5"))
CPOA_VFA_POLLING_TIMEOUT_SECONDS = int(os.getenv("CPOA_VFA_POLLING_TIMEOUT_SECONDS", "300")) # 5 minutes for voice forging

# Polling Configuration for IGA
CPOA_IGA_POLLING_INTERVAL_SECONDS = int(os.getenv("CPOA_IGA_POLLING_INTERVAL_SECONDS", "5"))
CPOA_IGA_POLLING_TIMEOUT_SECONDS = int(os.getenv("CPOA_IGA_POLLING_TIMEOUT_SECONDS", "240")) # 4 minutes for image generation

# Polling Configuration for TDA
CPOA_TDA_POLLING_INTERVAL_SECONDS = int(os.getenv("CPOA_TDA_POLLING_INTERVAL_SECONDS", "5"))
CPOA_TDA_POLLING_TIMEOUT_SECONDS = int(os.getenv("CPOA_TDA_POLLING_TIMEOUT_SECONDS", "180")) # 3 minutes for topic discovery

# Polling Configuration for WCHA
CPOA_WCHA_POLLING_INTERVAL_SECONDS = int(os.getenv("CPOA_WCHA_POLLING_INTERVAL_SECONDS", "10"))
CPOA_WCHA_POLLING_TIMEOUT_SECONDS = int(os.getenv("CPOA_WCHA_POLLING_TIMEOUT_SECONDS", "300")) # 5 minutes for content harvesting

# Database Configuration (PostgreSQL only)
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")


# --- Logging Configuration ---
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    # New format to include workflow_id and task_id, which will be added via LoggerAdapter
    # Added default "N/A" for workflow_id and task_id if not provided in the log record.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - CPOA - %(workflow_id:-N/A)s - %(task_id:-N/A)s - %(message)s')

# Log loaded configuration (initial logging without workflow/task IDs)
# Create a temporary adapter for these initial logs if needed, or log directly.
initial_log_extra = {'workflow_id': 'N/A', 'task_id': 'N/A'}
logger.info("--- CPOA Configuration ---", extra=initial_log_extra)
logger.info("DATABASE_TYPE: postgres (hardcoded)", extra=initial_log_extra) # Hardcoded to postgres
logger.info(f"POSTGRES_HOST: {POSTGRES_HOST}", extra=initial_log_extra)
logger.info(f"POSTGRES_PORT: {POSTGRES_PORT}", extra=initial_log_extra)
logger.info(f"POSTGRES_USER: {POSTGRES_USER}", extra=initial_log_extra)
# Do not log password
logger.info(f"POSTGRES_DB: {POSTGRES_DB}", extra=initial_log_extra)

logger.info(f"TDA_SERVICE_URL: {TDA_SERVICE_URL}", extra=initial_log_extra)
logger.info(f"PSWA_SERVICE_URL: {PSWA_SERVICE_URL}", extra=initial_log_extra)
logger.info(f"VFA_SERVICE_URL: {VFA_SERVICE_URL}", extra=initial_log_extra)
logger.info(f"ASF_NOTIFICATION_URL: {ASF_NOTIFICATION_URL}", extra=initial_log_extra)
logger.info(f"ASF_WEBSOCKET_BASE_URL: {ASF_WEBSOCKET_BASE_URL}", extra=initial_log_extra)
logger.info(f"SCA_SERVICE_URL: {SCA_SERVICE_URL}", extra=initial_log_extra)
logger.info(f"IGA_SERVICE_URL: {IGA_SERVICE_URL}", extra=initial_log_extra)
logger.info(f"CPOA_ASF_SEND_UI_UPDATE_URL: {CPOA_ASF_SEND_UI_UPDATE_URL}", extra=initial_log_extra)
logger.info(f"CPOA_SERVICE_RETRY_COUNT: {CPOA_SERVICE_RETRY_COUNT}", extra=initial_log_extra)
logger.info(f"CPOA_SERVICE_RETRY_BACKOFF_FACTOR: {CPOA_SERVICE_RETRY_BACKOFF_FACTOR}", extra=initial_log_extra)
logger.info("--- End CPOA Configuration ---", extra=initial_log_extra)


# --- Database Connection Helper (PostgreSQL only) ---
def _get_cpoa_db_connection():
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
# Now expects db_conn to be passed
def _create_workflow_instance(db_conn, trigger_event_type: str, trigger_event_details: Optional[dict] = None, user_id: Optional[str] = None) -> Optional[str]:
    workflow_id = None
    log_extra = {'workflow_id': None, 'task_id': None}
    # Connection is managed by the caller
    try:
        if not db_conn:
            logger.error(f"CPOA State: DB connection not provided for creating workflow.", extra=log_extra)
            raise ConnectionError("DB connection not provided to _create_workflow_instance")

        with db_conn.cursor() as cur:
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
            # conn.commit() # Commit handled by caller
            log_extra['workflow_id'] = workflow_id # Update log_extra with the new workflow_id
            logger.info(f"Workflow instance created. Type: {trigger_event_type}", extra=log_extra)
            return workflow_id
    except psycopg2.Error as e:
        logger.error(f"CPOA State: DB error creating workflow. Type: {trigger_event_type}. Error: {e}", exc_info=True, extra=log_extra)
        # if conn: conn.rollback() # Rollback handled by caller
        raise # Re-raise to be handled by caller's transaction management
    except Exception as e_unexp:
        logger.error(f"CPOA State: Unexpected error creating workflow. Type: {trigger_event_type}. Error: {e_unexp}", exc_info=True, extra=log_extra)
        # if conn: conn.rollback() # Rollback handled by caller
        raise # Re-raise
    finally:
        pass # Connection managed by the caller, cursor closed by 'with'

def _update_workflow_instance_status(db_conn, workflow_id: str, overall_status: str, context_data: Optional[dict] = None, error_message: Optional[str] = None):
    log_extra = {'workflow_id': workflow_id, 'task_id': None}
    # Connection is managed by the caller
    try:
        if not db_conn:
            logger.error(f"CPOA State: DB connection not provided for updating workflow.", extra=log_extra)
            raise ConnectionError("DB connection not provided to _update_workflow_instance_status")

        with db_conn.cursor() as cur:
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
            # conn.commit() # Commit handled by caller
            logger.info(f"Workflow instance status updated to {overall_status}.", extra=log_extra)
            return True # Indicates SQL execution was successful
    except psycopg2.Error as e:
        logger.error(f"CPOA State: DB error updating workflow to status {overall_status}. Error: {e}", exc_info=True, extra=log_extra)
        # if conn: conn.rollback() # Rollback handled by caller
        raise # Re-raise
    except Exception as e_unexp:
        logger.error(f"CPOA State: Unexpected error updating workflow. Error: {e_unexp}", exc_info=True, extra=log_extra)
        # if conn: conn.rollback() # Rollback handled by caller
        raise # Re-raise
    finally:
        pass # Connection managed by the caller, cursor closed by 'with'

def _create_task_instance(db_conn, workflow_id: str, agent_name: str, task_order: int, input_params: Optional[dict] = None, initial_status: str = TASK_STATUS_PENDING) -> Optional[str]:
    task_id = None
    log_extra = {'workflow_id': workflow_id, 'task_id': None}
    # Connection is managed by the caller
    try:
        if not db_conn:
            logger.error(f"CPOA State: DB connection not provided for creating task.", extra=log_extra)
            raise ConnectionError("DB connection not provided to _create_task_instance")

        with db_conn.cursor() as cur:
            sql = """
                INSERT INTO task_instances
                    (workflow_id, agent_name, task_order, status, input_params_json, start_timestamp, last_updated_timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, current_timestamp)
                RETURNING task_id;
            """
            start_ts = datetime.now() if initial_status not in [TASK_STATUS_PENDING] else None # Use datetime.now() for PG

            cur.execute(sql, (workflow_id, agent_name, task_order, initial_status, json.dumps(input_params) if input_params else None, start_ts))
            result = cur.fetchone()
            if result and 'task_id' in result: # Check if 'task_id' key exists, was workflow_id before
                task_id = str(result['task_id'])
            # conn.commit() # Commit handled by caller
            log_extra['task_id'] = task_id # Update log_extra with new task_id
            logger.info(f"Task instance created. Agent: {agent_name}, Order: {task_order}, Status: {initial_status}", extra=log_extra)
            return task_id
    except psycopg2.Error as e:
        logger.error(f"CPOA State: DB error creating task for agent {agent_name}. Error: {e}", exc_info=True, extra=log_extra)
        # if conn: conn.rollback() # Rollback handled by caller
        raise # Re-raise
    except Exception as e_unexp:
        logger.error(f"CPOA State: Unexpected error creating task for agent {agent_name}. Error: {e_unexp}", exc_info=True, extra=log_extra)
        # if conn: conn.rollback() # Rollback handled by caller
        raise # Re-raise
    finally:
        pass # Connection managed by the caller, cursor closed by 'with'

def _update_task_instance_status(db_conn, task_id: str, status: str, output_summary: Optional[dict] = None, error_details: Optional[dict] = None, retry_count: Optional[int] = None, workflow_id_for_log: Optional[str] = None):
    log_extra = {'workflow_id': workflow_id_for_log, 'task_id': task_id}
    # Connection is managed by the caller
    try:
        if not db_conn:
            logger.error(f"CPOA State: DB connection not provided for updating task.", extra=log_extra)
            raise ConnectionError("DB connection not provided to _update_task_instance_status")

        with db_conn.cursor() as cur:
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
            # conn.commit() # Commit handled by caller
            logger.info(f"Task instance status updated to {status}.", extra=log_extra)
            return True # Indicates SQL execution was successful
    except psycopg2.Error as e:
        logger.error(f"CPOA State: DB error updating task to status {status}. Error: {e}", exc_info=True, extra=log_extra)
        # if conn: conn.rollback() # Rollback handled by caller
        raise # Re-raise
    except Exception as e_unexp:
        logger.error(f"CPOA State: Unexpected error updating task. Error: {e_unexp}", exc_info=True, extra=log_extra)
        # if conn: conn.rollback() # Rollback handled by caller
        raise # Re-raise
    finally:
        pass # Connection managed by the caller, cursor closed by 'with'

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


def _update_task_status_in_db(db_conn, task_id: str, new_cpoa_status: str, error_msg: Optional[str] = None, workflow_id_for_log: Optional[str] = None) -> None: # Added workflow_id_for_log for consistency
    """
    Updates the cpoa_status, cpoa_error_message, and last_updated_timestamp for a task in the 'podcasts' table (PostgreSQL only).
    Accepts an active db_conn.
    """
    log_extra = {'workflow_id': workflow_id_for_log, 'task_id': task_id}
    logger.info(f"Updating legacy podcast status to '{new_cpoa_status}'. Error: '{error_msg or 'None'}'", extra=log_extra)
    timestamp = datetime.now()
    cursor = None

    try:
        if not db_conn:
             logger.error(f"DB connection not provided for legacy status update.", extra=log_extra)
             raise ConnectionError("DB connection not provided for _update_task_status_in_db")

        cursor = db_conn.cursor()
        task_id_str = str(task_id) # Ensure UUID is string for query

        sql = """
            UPDATE podcasts
            SET cpoa_status = %s, cpoa_error_message = %s, last_updated_timestamp = %s
            WHERE podcast_id = %s;
        """
        cursor.execute(sql, (new_cpoa_status, error_msg, timestamp, task_id_str))
        # Commit is handled by the calling task
        logger.info(f"Task {task_id}: Successfully prepared update for CPOA status in PostgreSQL DB to '{new_cpoa_status}'.")

    except psycopg2.Error as e:
        logger.error(f"CPOA: DB error for task {task_id} (PostgreSQL, Status: {new_cpoa_status}): {e}", exc_info=True)
        raise # Re-raise to be caught by task's transaction handler
    except Exception as e_unexp:
        logger.error(f"CPOA: Unexpected error in _update_task_status_in_db for task {task_id} (PostgreSQL, Status: {new_cpoa_status}): {e_unexp}", exc_info=True)
        raise # Re-raise
    finally:
        if cursor:
            cursor.close()
        # Connection closing and commit/rollback are handled by the calling task for transactions

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
    test_scenarios: Optional[dict] = None,
    # Add Celery task context if needed, e.g., self for bind=True
) -> Dict[str, Any]:
    # This is the core logic, to be run by the Celery task.
    # The 'original_task_id' here is the Celery task's own ID if bind=True, or a passed-in ID.
    # For simplicity, let's assume original_task_id is passed in if this function is called directly,
    # or can be derived from self.request.id if this becomes the task body itself.
    # For now, keeping it as a parameter.

    # If this function is directly decorated as a task, 'original_task_id' might be self.request.id
    # For now, let's assume it's a distinct ID for the conceptual podcast operation.

    workflow_id = _create_workflow_instance(
        trigger_event_type="podcast_generation_celery_task", # Modified trigger type
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
            if wcha_task_id: _update_task_instance_status(wcha_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)

            # Construct WCHA /harvest URL and payload
            wcha_harvest_url = f"{WCHA_SERVICE_BASE_URL.rstrip('/')}/harvest"
            wcha_payload = {"topic": topic, "use_search": True}
            # min_length could be added if CPOA needs to specify it, e.g. from config
            # wcha_payload["min_length"] = int(os.getenv("CPOA_WCHA_MIN_CONTENT_LENGTH", "150"))


            initial_wcha_response = requests_with_retry("post", wcha_harvest_url,
                                                        CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                                        json=wcha_payload, timeout=30,
                                                        workflow_id_for_log=workflow_id, task_id_for_log=wcha_task_id)

            if initial_wcha_response.status_code != 202:
                # Handle cases where WCHA might return content synchronously (if not using NewsAPI and DDGS path is sync)
                if initial_wcha_response.status_code == 200:
                    wf_logger.info(f"WCHA returned synchronous response for topic '{topic}'. Processing directly.", extra={'task_id': wcha_task_id})
                    wcha_sync_result = initial_wcha_response.json()
                    if wcha_sync_result.get("status") == "success" and wcha_sync_result.get("content"):
                        wcha_content = wcha_sync_result.get("content")
                        source_urls = wcha_sync_result.get("source_urls", [])
                        context_data_for_workflow["wcha_source_urls"] = source_urls
                        wcha_output_summary = {"content_length": len(wcha_content) if wcha_content else 0, "source_urls": source_urls, "message": wcha_sync_result.get("message", "WCHA synchronous success.")}
                        log_step_cpoa("WCHA synchronous success, content received.", data=wcha_output_summary)
                        # Skip polling logic by breaking the outer loop effectively or setting wcha_internal_task_id to None
                        wcha_internal_task_id = None # This will prevent polling loop
                    else:
                        wcha_error_details = {"message": "WCHA synchronous response indicates failure or no content.", "wcha_response": wcha_sync_result}
                        raise Exception(wcha_error_details["message"])
                else:
                    wcha_error_details = {"message": f"WCHA service did not accept task. Status: {initial_wcha_response.status_code}", "response_text": initial_wcha_response.text[:200]}
                    raise Exception(wcha_error_details["message"])
            else: # Status is 202, proceed with polling
                wcha_task_init_data = initial_wcha_response.json()
                wcha_internal_task_id = wcha_task_init_data.get("task_id")
                wcha_status_url_suffix = wcha_task_init_data.get("status_url")

                if not wcha_internal_task_id or not wcha_status_url_suffix:
                    wcha_error_details = {"message": "WCHA task submission response missing task_id or status_url.", "response_data": wcha_task_init_data}
                    raise Exception(wcha_error_details["message"])

                # Construct poll URL based on WCHA_SERVICE_BASE_URL and the suffix from WCHA
                wcha_poll_url = f"{WCHA_SERVICE_BASE_URL.rstrip('/')}{wcha_status_url_suffix}"
                log_step_cpoa(f"WCHA task {wcha_internal_task_id} submitted. Polling at {wcha_poll_url}", data=wcha_task_init_data)
                wf_logger.info(f"WCHA task {wcha_internal_task_id} submitted. Polling status at {wcha_poll_url}", extra={'task_id': wcha_task_id})

            # Polling Loop - only if wcha_internal_task_id is set (i.e. got 202)
            if wcha_internal_task_id:
                polling_start_time = time.time()
                while True:
                    if time.time() - polling_start_time > CPOA_WCHA_POLLING_TIMEOUT_SECONDS:
                        wcha_error_details = {"message": f"Polling WCHA task {wcha_internal_task_id} timed out."}
                        raise Exception(wcha_error_details["message"])
                    try:
                        poll_response_wcha = requests.get(wcha_poll_url, timeout=15)
                        poll_response_wcha.raise_for_status()
                        wcha_task_status_data = poll_response_wcha.json()
                        wcha_task_state = wcha_task_status_data.get("status")
                        log_step_cpoa(f"WCHA task {wcha_internal_task_id} status: {wcha_task_state}", data=wcha_task_status_data)
                        wf_logger.info(f"WCHA task {wcha_internal_task_id} status: {wcha_task_state}", extra={'task_id': wcha_task_id})

                        if wcha_task_state == "SUCCESS":
                            wcha_result_dict = wcha_task_status_data.get("result")
                            # WCHA's harvest_url_content_task returns: {"url": url, "content": extracted_text, "error_type": None, "error_message": None}
                            # WCHA's fetch_news_articles_task returns: {"status": "success", "articles": articles, "message": ...}
                            # The new conceptual 'get_content_for_topic_task' (if WCHA were updated) should return something like:
                            # {"status": "success", "content": "...", "source_urls": [...]}
                            # For now, let's assume the result from WCHA task (if successful) will have a 'content' field.
                            # This might need adjustment based on what task WCHA's /harvest actually triggers for topics.
                            if wcha_result_dict and wcha_result_dict.get("content"): # Assuming direct content
                                wcha_content = wcha_result_dict.get("content")
                                source_urls = wcha_result_dict.get("source_urls", []) # If WCHA returns this
                                context_data_for_workflow["wcha_source_urls"] = source_urls
                                wcha_output_summary = {"content_length": len(wcha_content), "source_urls": source_urls, "message": "WCHA task content received."}
                                log_step_cpoa("WCHA task polling successful, content received.", data=wcha_output_summary)
                            # If WCHA returned articles (from NewsAPI path), CPOA would need to harvest each one.
                            # This part is simplified for now, assuming direct content or a single content blob.
                            elif wcha_result_dict and wcha_result_dict.get("articles"): # NewsAPI path
                                 wcha_error_details = {"message": "WCHA returned articles, but CPOA expected aggregated content. Further processing needed.", "wcha_response": wcha_result_dict}
                                 # For now, this is an error. A more robust CPOA would handle this by dispatching individual URL harvests.
                            else:
                                 wcha_error_details = {"message": "WCHA task succeeded but result format unexpected or content missing.", "wcha_response": wcha_result_dict}
                            break # Exit polling loop on SUCCESS
                        elif wcha_task_state == "FAILURE":
                            wcha_error_details = {"message": "WCHA task execution failed.", "wcha_celery_response": wcha_task_status_data.get("result")}
                            log_step_cpoa(wcha_error_details["message"], data=wcha_error_details, is_error_payload=True)
                            break # Exit polling loop on FAILURE
                        time.sleep(CPOA_WCHA_POLLING_INTERVAL_SECONDS)
                    except requests.exceptions.RequestException as e_poll_wcha:
                        log_step_cpoa(f"Polling WCHA task {wcha_internal_task_id} failed: {e_poll_wcha}. Retrying.", is_error_payload=True)
                        wf_logger.warning(f"Polling WCHA task {wcha_internal_task_id} failed: {e_poll_wcha}. Retrying.", extra={'task_id': wcha_task_id})
                        time.sleep(CPOA_WCHA_POLLING_INTERVAL_SECONDS)
            # End of polling loop / synchronous handling block
        except Exception as e_wcha: # Catch errors from initial dispatch or polling logic
            wcha_error_details = wcha_error_details or {"message": f"WCHA stage error: {str(e_wcha)}", "exception_type": type(e_wcha).__name__}
            wf_logger.error(f"WCHA stage error: {wcha_error_details['message']}", exc_info=True, extra={'task_id': wcha_task_id})

        if wcha_task_id:
            _update_task_instance_status(wcha_task_id, TASK_STATUS_COMPLETED if not wcha_error_details and wcha_content else TASK_STATUS_FAILED,
                                         output_summary=wcha_output_summary, error_details=wcha_error_details, workflow_id_for_log=workflow_id)

        if wcha_error_details or not wcha_content: # If any error occurred or content is still None
            final_error_message = (wcha_error_details.get("message") if wcha_error_details else None) or "WCHA critical failure: No content after polling."
            final_cpoa_status_legacy = CPOA_STATUS_FAILED_WCHA_CONTENT_HARVEST
            raise Exception(final_error_message)


        # --- PSWA Stage ---
        current_orchestration_stage_legacy = ORCHESTRATION_STAGE_PSWA
                raise Exception(wcha_error_details["message"])

            # This block was part of the original polling logic, keep it for when wcha_internal_task_id is valid (got 202)
            # The SEARCH block above duplicated it, so this is the correct placement.
            # if wcha_internal_task_id: # This check is now done before starting the loop.
            #    polling_start_time = time.time()
            #    while True:
            #        if time.time() - polling_start_time > CPOA_WCHA_POLLING_TIMEOUT_SECONDS:
            #            wcha_error_details = {"message": f"Polling WCHA task {wcha_internal_task_id} timed out."}
            #            raise Exception(wcha_error_details["message"])
            #        try:
            #            poll_response_wcha = requests.get(wcha_poll_url, timeout=15)
            #            poll_response_wcha.raise_for_status()
            #            wcha_task_status_data = poll_response_wcha.json()
            #            wcha_task_state = wcha_task_status_data.get("status")
            #            log_step_cpoa(f"WCHA task {wcha_internal_task_id} status: {wcha_task_state}", data=wcha_task_status_data)
            #            wf_logger.info(f"WCHA task {wcha_internal_task_id} status: {wcha_task_state}", extra={'task_id': wcha_task_id})

            #            if wcha_task_state == "SUCCESS":
            #                wcha_result_dict = wcha_task_status_data.get("result")
            #                if not wcha_result_dict or wcha_result_dict.get("status") != "success":
            #                     wcha_error_details = {"message": "WCHA task succeeded but reported internal failure or invalid result.", "wcha_response": wcha_result_dict}
            #                else: # Success
            #                    wcha_content = wcha_result_dict.get("content")
            #                    source_urls = wcha_result_dict.get("source_urls", [])
            #                    context_data_for_workflow["wcha_source_urls"] = source_urls
            #                    wcha_output_summary = {"content_length": len(wcha_content) if wcha_content else 0, "source_urls": source_urls, "message": wcha_result_dict.get("message", "WCHA success.")}
            #                    log_step_cpoa("WCHA task polling successful, content received.", data=wcha_output_summary)
            #                    if not wcha_content: wcha_error_details = {"message": wcha_output_summary.get("message") or "WCHA success but no content."}
            #                break
            #            elif wcha_task_state == "FAILURE":
            #                wcha_error_details = {"message": "WCHA task execution failed.", "wcha_celery_response": wcha_task_status_data.get("result")}
            #                log_step_cpoa(wcha_error_details["message"], data=wcha_error_details, is_error_payload=True)
            #                break
            #            time.sleep(CPOA_WCHA_POLLING_INTERVAL_SECONDS)
            #        except requests.exceptions.RequestException as e_poll_wcha:
            #            log_step_cpoa(f"Polling WCHA task {wcha_internal_task_id} failed: {e_poll_wcha}. Retrying.", is_error_payload=True)
            #            wf_logger.warning(f"Polling WCHA task {wcha_internal_task_id} failed: {e_poll_wcha}. Retrying.", extra={'task_id': wcha_task_id})
            #            time.sleep(CPOA_WCHA_POLLING_INTERVAL_SECONDS)

        except Exception as e_wcha: # Catch errors from initial dispatch or polling logic
            wcha_error_details = wcha_error_details or {"message": f"WCHA stage error: {str(e_wcha)}", "exception_type": type(e_wcha).__name__}
            wf_logger.error(f"WCHA stage error: {wcha_error_details['message']}", exc_info=True, extra={'task_id': wcha_task_id})

        if wcha_task_id:
            _update_task_instance_status(wcha_task_id, TASK_STATUS_COMPLETED if not wcha_error_details and wcha_content else TASK_STATUS_FAILED,
                                         output_summary=wcha_output_summary, error_details=wcha_error_details, workflow_id_for_log=workflow_id)

        if wcha_error_details or not wcha_content: # If any error occurred or content is still None
            final_error_message = (wcha_error_details.get("message") if wcha_error_details else None) or "WCHA critical failure: No content after polling."
            final_cpoa_status_legacy = CPOA_STATUS_FAILED_WCHA_CONTENT_HARVEST
            raise Exception(final_error_message)


        # --- PSWA Stage ---
        current_orchestration_stage_legacy = ORCHESTRATION_STAGE_PSWA
                    raise Exception(wcha_error_details["message"])
                try:
                    poll_response_wcha = requests.get(wcha_poll_url, timeout=15)
                    poll_response_wcha.raise_for_status()
                    wcha_task_status_data = poll_response_wcha.json()
                    wcha_task_state = wcha_task_status_data.get("status")
                    log_step_cpoa(f"WCHA task {wcha_internal_task_id} status: {wcha_task_state}", data=wcha_task_status_data)
                    wf_logger.info(f"WCHA task {wcha_internal_task_id} status: {wcha_task_state}", extra={'task_id': wcha_task_id})

                    if wcha_task_state == "SUCCESS":
                        wcha_result_dict = wcha_task_status_data.get("result")
                        if not wcha_result_dict or wcha_result_dict.get("status") != "success":
                             wcha_error_details = {"message": "WCHA task succeeded but reported internal failure or invalid result.", "wcha_response": wcha_result_dict}
                        else: # Success
                            wcha_content = wcha_result_dict.get("content")
                            source_urls = wcha_result_dict.get("source_urls", [])
                            context_data_for_workflow["wcha_source_urls"] = source_urls
                            wcha_output_summary = {"content_length": len(wcha_content) if wcha_content else 0, "source_urls": source_urls, "message": wcha_result_dict.get("message", "WCHA success.")}
                            log_step_cpoa("WCHA task polling successful, content received.", data=wcha_output_summary)
                            if not wcha_content: wcha_error_details = {"message": wcha_output_summary.get("message") or "WCHA success but no content."}
                        break
                    elif wcha_task_state == "FAILURE":
                        wcha_error_details = {"message": "WCHA task execution failed.", "wcha_celery_response": wcha_task_status_data.get("result")}
                        log_step_cpoa(wcha_error_details["message"], data=wcha_error_details, is_error_payload=True)
                        break
                    time.sleep(CPOA_WCHA_POLLING_INTERVAL_SECONDS)
                except requests.exceptions.RequestException as e_poll_wcha:
                    log_step_cpoa(f"Polling WCHA task {wcha_internal_task_id} failed: {e_poll_wcha}. Retrying.", is_error_payload=True)
                    wf_logger.warning(f"Polling WCHA task {wcha_internal_task_id} failed: {e_poll_wcha}. Retrying.", extra={'task_id': wcha_task_id})
                    time.sleep(CPOA_WCHA_POLLING_INTERVAL_SECONDS)

        except Exception as e_wcha: # Catch errors from initial dispatch or polling logic
            wcha_error_details = wcha_error_details or {"message": f"WCHA stage error: {str(e_wcha)}", "exception_type": type(e_wcha).__name__}
            wf_logger.error(f"WCHA stage error: {wcha_error_details['message']}", exc_info=True, extra={'task_id': wcha_task_id})

        if wcha_task_id:
            _update_task_instance_status(wcha_task_id, TASK_STATUS_COMPLETED if not wcha_error_details and wcha_content else TASK_STATUS_FAILED,
                                         output_summary=wcha_output_summary, error_details=wcha_error_details, workflow_id_for_log=workflow_id)

        if wcha_error_details or not wcha_content: # If any error occurred or content is still None
            final_error_message = (wcha_error_details.get("message") if wcha_error_details else None) or "WCHA critical failure: No content after polling."
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

            pswa_payload = {"content": wcha_content, "topic": topic}
            pswa_headers = {'X-Test-Scenario': test_scenarios["pswa"]} if test_scenarios and test_scenarios.get("pswa") else {}

            # Initial call to PSWA to dispatch task
            response_pswa_initial = requests_with_retry("post", PSWA_SERVICE_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                                      json=pswa_payload, timeout=30, headers=pswa_headers, # Shorter timeout for task dispatch
                                                      workflow_id_for_log=workflow_id, task_id_for_log=pswa_task_id)

            if response_pswa_initial.status_code != 202:
                pswa_error_details = {"message": f"PSWA service did not accept the task. Status: {response_pswa_initial.status_code}", "response_text": response_pswa_initial.text[:200]}
                raise Exception(pswa_error_details["message"])

            pswa_task_init_data = response_pswa_initial.json()
            pswa_internal_task_id = pswa_task_init_data.get("task_id")
            pswa_status_url_suffix = pswa_task_init_data.get("status_url")

            if not pswa_internal_task_id or not pswa_status_url_suffix:
                pswa_error_details = {"message": "PSWA task submission response missing task_id or status_url.", "response_data": pswa_task_init_data}
                raise Exception(pswa_error_details["message"])

            pswa_base_url = '/'.join(PSWA_SERVICE_URL.split('/')[:-1]) # e.g., http://pswa:5004
            pswa_poll_url = f"{pswa_base_url}{pswa_status_url_suffix}"
            log_step_cpoa(f"PSWA task {pswa_internal_task_id} submitted. Polling at {pswa_poll_url}", data=pswa_task_init_data)
            wf_logger.info(f"PSWA task {pswa_internal_task_id} submitted. Polling status at {pswa_poll_url}", extra={'task_id': pswa_task_id})


            polling_start_time = time.time()
            while True:
                if time.time() - polling_start_time > CPOA_PSWA_POLLING_TIMEOUT_SECONDS:
                    pswa_error_details = {"message": f"Polling PSWA task {pswa_internal_task_id} timed out after {CPOA_PSWA_POLLING_TIMEOUT_SECONDS}s."}
                    raise Exception(pswa_error_details["message"])

                try:
                    poll_response = requests.get(pswa_poll_url, timeout=15)
                    poll_response.raise_for_status()
                    pswa_task_status_data = poll_response.json()
                    pswa_task_state = pswa_task_status_data.get("status")
                    log_step_cpoa(f"PSWA task {pswa_internal_task_id} status: {pswa_task_state}", data=pswa_task_status_data)
                    wf_logger.info(f"PSWA task {pswa_internal_task_id} status: {pswa_task_state}", extra={'task_id': pswa_task_id})

                    if pswa_task_state == "SUCCESS":
                        structured_script_from_pswa = pswa_task_status_data.get("result", {}).get("script_data") # PSWA task returns dict with script_data or error_data
                        if not structured_script_from_pswa: # Check if script_data is present
                             pswa_error_details = {"message": "PSWA task succeeded but script_data missing.", "pswa_response": pswa_task_status_data}
                             log_step_cpoa(pswa_error_details["message"], data=pswa_error_details, is_error_payload=True)
                        elif not (isinstance(structured_script_from_pswa, dict) and structured_script_from_pswa.get("script_id") and structured_script_from_pswa.get("title")):
                            pswa_error_details = {"message": "PSWA task result is invalid or malformed.", "received_script_preview": structured_script_from_pswa}
                            log_step_cpoa(pswa_error_details["message"], data=pswa_error_details, is_error_payload=True)
                        else:
                            pswa_output_summary = {"script_id": structured_script_from_pswa.get("script_id"), "title": structured_script_from_pswa.get("title"), "segment_count": len(structured_script_from_pswa.get("segments", []))}
                            log_step_cpoa("PSWA Task polling successful, script received.", data=pswa_output_summary)
                        break
                    elif pswa_task_state == "FAILURE":
                        pswa_error_details = {"message": "PSWA task execution failed.", "pswa_response": pswa_task_status_data.get("result")}
                        log_step_cpoa(pswa_error_details["message"], data=pswa_error_details, is_error_payload=True)
                        break

                    time.sleep(CPOA_PSWA_POLLING_INTERVAL_SECONDS)
                except requests.exceptions.RequestException as e_poll_pswa:
                    log_step_cpoa(f"Polling PSWA task {pswa_internal_task_id} failed: {e_poll_pswa}. Retrying.", is_error_payload=True)
                    wf_logger.warning(f"Polling PSWA task {pswa_internal_task_id} failed: {e_poll_pswa}. Retrying.", extra={'task_id': pswa_task_id})
                    time.sleep(CPOA_PSWA_POLLING_INTERVAL_SECONDS)

        except requests.exceptions.RequestException as e_req_pswa: # For initial PSWA call
            status_code = e_req_pswa.response.status_code if e_req_pswa.response is not None else "N/A"
            pswa_error_details = {"message": f"PSWA service initial call failed (HTTP status: {status_code}, type: {type(e_req_pswa).__name__}): {str(e_req_pswa)}." , "response_payload_preview": e_req_pswa.response.text[:200] if e_req_pswa.response is not None else "N/A"}
            log_step_cpoa("PSWA service request exception.", data=pswa_error_details, is_error_payload=True)
        except json.JSONDecodeError as e_json_pswa: # For initial PSWA call
            pswa_error_details = {"message": f"PSWA service initial response was not valid JSON: {str(e_json_pswa)}", "response_text_preview": response_pswa_initial.text[:200] if 'response_pswa_initial' in locals() else "N/A"}
            log_step_cpoa("PSWA service JSON decode error on initial call.", data=pswa_error_details, is_error_payload=True)
        except Exception as e_pswa_unexp:
            pswa_error_details = pswa_error_details or {"message": f"PSWA stage unexpected error: {str(e_pswa_unexp)}", "exception_type": type(e_pswa_unexp).__name__}
            wf_logger.error(f"PSWA stage unexpected error: {pswa_error_details['message']}", exc_info=True, extra={'task_id': pswa_task_id})

        if pswa_task_id:
            _update_task_instance_status(pswa_task_id, TASK_STATUS_COMPLETED if not pswa_error_details else TASK_STATUS_FAILED,
                                         output_summary=pswa_output_summary, error_details=pswa_error_details, workflow_id_for_log=workflow_id)

        if pswa_error_details or not structured_script_from_pswa: # Check if error occurred or script is still None
            final_error_message = (pswa_error_details.get("message") if pswa_error_details else None) or "PSWA critical failure: No script after polling."
            final_cpoa_status_legacy = CPOA_STATUS_FAILED_PSWA_REQUEST_EXCEPTION
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
            
            # Initial call to VFA to dispatch task
            response_vfa_initial = requests_with_retry("post", VFA_SERVICE_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                                       json=vfa_payload, timeout=30, headers=vfa_headers, # Shorter timeout for task dispatch
                                                       workflow_id_for_log=workflow_id, task_id_for_log=vfa_task_id)

            if response_vfa_initial.status_code != 202: # VFA should return 202 Accepted
                vfa_error_details = {"message": f"VFA service did not accept the task. Status: {response_vfa_initial.status_code}", "response_text": response_vfa_initial.text[:200]}
                raise Exception(vfa_error_details["message"])

            vfa_task_init_data = response_vfa_initial.json()
            vfa_internal_task_id = vfa_task_init_data.get("task_id")
            vfa_status_url_suffix = vfa_task_init_data.get("status_url")

            if not vfa_internal_task_id or not vfa_status_url_suffix:
                vfa_error_details = {"message": "VFA task submission response missing task_id or status_url.", "response_data": vfa_task_init_data}
                raise Exception(vfa_error_details["message"])

            vfa_base_url = '/'.join(VFA_SERVICE_URL.split('/')[:-1]) # e.g., http://vfa:5005
            vfa_poll_url = f"{vfa_base_url}{vfa_status_url_suffix}"
            log_step_cpoa(f"VFA task {vfa_internal_task_id} submitted. Polling at {vfa_poll_url}", data=vfa_task_init_data)
            wf_logger.info(f"VFA task {vfa_internal_task_id} submitted. Polling status at {vfa_poll_url}", extra={'task_id': vfa_task_id})

            polling_start_time = time.time()
            while True:
                if time.time() - polling_start_time > CPOA_VFA_POLLING_TIMEOUT_SECONDS:
                    vfa_error_details = {"message": f"Polling VFA task {vfa_internal_task_id} timed out after {CPOA_VFA_POLLING_TIMEOUT_SECONDS}s."}
                    raise Exception(vfa_error_details["message"])

                try:
                    poll_response_vfa = requests.get(vfa_poll_url, timeout=15)
                    poll_response_vfa.raise_for_status()
                    vfa_task_status_data = poll_response_vfa.json()
                    vfa_task_state = vfa_task_status_data.get("status")
                    log_step_cpoa(f"VFA task {vfa_internal_task_id} status: {vfa_task_state}", data=vfa_task_status_data)
                    wf_logger.info(f"VFA task {vfa_internal_task_id} status: {vfa_task_state}", extra={'task_id': vfa_task_id})

                    if vfa_task_state == "SUCCESS":
                        vfa_result_dict = vfa_task_status_data.get("result") # This is the original VFA success/error dict
                        if not vfa_result_dict or not isinstance(vfa_result_dict, dict):
                            vfa_error_details = {"message": "VFA task succeeded but result is missing or invalid.", "vfa_response": vfa_task_status_data}
                        elif vfa_result_dict.get("status") != VFA_STATUS_SUCCESS: # VFA task's own internal status
                             vfa_error_details = {"message": vfa_result_dict.get("message", "VFA task reported non-success status in result."), "vfa_response": vfa_result_dict}
                        else: # VFA task logic succeeded
                            vfa_output_summary = {"status": vfa_result_dict.get("status"), "audio_gcs_uri": vfa_result_dict.get("audio_filepath"), "stream_id": vfa_result_dict.get("stream_id"), "tts_settings_used": vfa_result_dict.get("tts_settings_used")}
                            log_step_cpoa("VFA Task polling successful, audio metadata received.", data=vfa_output_summary)
                        break
                    elif vfa_task_state == "FAILURE":
                        vfa_error_details = {"message": "VFA Celery task execution failed.", "vfa_celery_response": vfa_task_status_data.get("result")}
                        log_step_cpoa(vfa_error_details["message"], data=vfa_error_details, is_error_payload=True)
                        break

                    time.sleep(CPOA_VFA_POLLING_INTERVAL_SECONDS)
                except requests.exceptions.RequestException as e_poll_vfa:
                    log_step_cpoa(f"Polling VFA task {vfa_internal_task_id} failed: {e_poll_vfa}. Retrying.", is_error_payload=True)
                    wf_logger.warning(f"Polling VFA task {vfa_internal_task_id} failed: {e_poll_vfa}. Retrying.", extra={'task_id': vfa_task_id})
                    time.sleep(CPOA_VFA_POLLING_INTERVAL_SECONDS)

        except requests.exceptions.RequestException as e_req_vfa: # For initial VFA call
            status_code = e_req_vfa.response.status_code if e_req_vfa.response is not None else "N/A"
            vfa_error_details = {"message": f"VFA service initial call failed (HTTP status: {status_code}, type: {type(e_req_vfa).__name__}): {str(e_req_vfa)}.", "response_payload_preview": e_req_vfa.response.text[:200] if e_req_vfa.response is not None else "N/A"}
            vfa_result_dict = {"status": VFA_STATUS_ERROR, "message": vfa_error_details["message"]}
        except json.JSONDecodeError as e_json_vfa: # For initial VFA call
            vfa_error_details = {"message": f"VFA service initial response was not valid JSON: {str(e_json_vfa)}", "response_text_preview": response_vfa_initial.text[:200] if 'response_vfa_initial' in locals() else "N/A"}
            vfa_result_dict = {"status": VFA_STATUS_ERROR, "message": vfa_error_details["message"]}
        except Exception as e_vfa_unexp:
            vfa_error_details = vfa_error_details or {"message": f"VFA stage unexpected error: {str(e_vfa_unexp)}", "exception_type": type(e_vfa_unexp).__name__}
            vfa_result_dict = {"status": VFA_STATUS_ERROR, "message": vfa_error_details["message"]}
            wf_logger.error(f"VFA stage unexpected error: {vfa_error_details['message']}", exc_info=True, extra={'task_id': vfa_task_id})

        if vfa_task_id:
             _update_task_instance_status(vfa_task_id, TASK_STATUS_COMPLETED if not vfa_error_details and vfa_result_dict.get("status") == VFA_STATUS_SUCCESS else TASK_STATUS_FAILED,
                                         output_summary=vfa_output_summary, error_details=vfa_error_details, workflow_id_for_log=workflow_id)

        if vfa_error_details or vfa_result_dict.get("status") != VFA_STATUS_SUCCESS:
            final_error_message = (vfa_error_details.get("message") if vfa_error_details else None) or vfa_result_dict.get("message", "VFA critical failure.")
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

@celery_app.task(bind=True, name='cpoa.orchestrate_podcast_task')
def cpoa_orchestrate_podcast_task(self,
                                 topic: str,
                                 original_task_id_from_caller: str, # ID from API Gateway or initial call
                                 user_id: Optional[str] = None,
                                 voice_params_input: Optional[dict] = None,
                                 client_id: Optional[str] = None,
                                 user_preferences: Optional[dict] = None,
                                 test_scenarios: Optional[dict] = None) -> Dict[str, Any]:
    """
    Celery task wrapper for orchestrate_podcast_generation.
    'self.request.id' will be the Celery task_id for this orchestration.
    'original_task_id_from_caller' is the ID API Gateway might have created for its tracking.
    We can use self.request.id as the primary 'original_task_id' for the internal logic if desired,
    or pass it along. For now, let's use self.request.id as the main identifier for this run.
    """
    logger.info(f"CPOA Celery Task {self.request.id} started for topic: '{topic}'. Original caller ID: {original_task_id_from_caller}")

    # Update workflow instance if it was created by the calling function, using self.request.id
    # This assumes the calling function (new orchestrate_podcast_generation_entrypoint)
    # might create a placeholder workflow_instance or task_instance tied to original_task_id_from_caller.
    # Or, the Celery task itself is responsible for the definitive workflow instance.
    # For this refactor, let orchestrate_podcast_generation handle the workflow instance creation,
    # passing self.request.id as the 'original_task_id' to it.

    result = orchestrate_podcast_generation(
        topic=topic,
        original_task_id=self.request.id, # Use Celery's task ID for internal tracking
        user_id=user_id,
        voice_params_input=voice_params_input,
        client_id=client_id,
        user_preferences=user_preferences,
        test_scenarios=test_scenarios
    )
    logger.info(f"CPOA Celery Task {self.request.id} completed. Final status: {result.get('status')}")
    return result # This result will be stored in the Celery backend


def trigger_podcast_orchestration(
    topic: str,
    user_id: Optional[str] = None,
    voice_params_input: Optional[dict] = None,
    client_id: Optional[str] = None,
    user_preferences: Optional[dict] = None,
    test_scenarios: Optional[dict] = None
) -> Dict[str, Any]:
    """
    This is the new entry point that API Gateway will call.
    It dispatches the Celery task and returns task information.
    """
    # Minimal validation before dispatching
    if not topic or not isinstance(topic, str):
        return {"error": "INVALID_TOPIC", "message": "Topic must be a non-empty string.", "task_id": None, "status_url": None}

    # This ID is just for the initial request before Celery task_id is known.
    # The Celery task (cpoa_orchestrate_podcast_task) will have its own unique ID (self.request.id).
    initial_request_id = str(uuid.uuid4())
    logger.info(f"CPOA: Received trigger for podcast orchestration. Topic: '{topic}'. Initial Req ID: {initial_request_id}")

    task = cpoa_orchestrate_podcast_task.delay(
        topic=topic,
        original_task_id_from_caller=initial_request_id, # Pass the initial ID for logging/correlation
        user_id=user_id,
        voice_params_input=voice_params_input,
        client_id=client_id,
        user_preferences=user_preferences,
        test_scenarios=test_scenarios
    )

    status_url = f"/v1/cpoa_tasks/{task.id}" # Conceptual URL, actual endpoint on API GW or CPOA if run as service
    logger.info(f"CPOA: Dispatched podcast orchestration task {task.id} for topic '{topic}'. Status URL: {status_url}")

    return {
        "message": "Podcast orchestration task accepted.",
        "cpoa_task_id": task.id,
        "status_url": status_url # This URL would be relative to CPOA if it's a service, or API GW
    }


# --- Snippet DB Interaction ---
def _save_snippet_to_db(db_conn, snippet_object: dict):
    """Saves a single snippet object to the topics_snippets table.
    Accepts an active db_conn.
    """
    cursor = None
    snippet_id = str(snippet_object.get("snippet_id") or uuid.uuid4())
    log_extra = {'workflow_id': None, 'task_id': snippet_id} # Assuming snippet_id can serve as task_id for logging context

    try:
        if not db_conn:
            logger.error(f"DB connection not provided for saving snippet {snippet_id}.", extra=log_extra)
            raise ConnectionError(f"DB connection not provided to _save_snippet_to_db for snippet {snippet_id}")

        cursor = db_conn.cursor()
        keywords_data = snippet_object.get("keywords", [])
        original_topic_details_data = snippet_object.get("original_topic_details_from_tda")
        current_ts = datetime.now()
        generation_timestamp_input = snippet_object.get("generation_timestamp", current_ts.isoformat())

        if isinstance(generation_timestamp_input, str):
            try:
                generation_timestamp_to_save = datetime.fromisoformat(generation_timestamp_input.replace("Z", "+00:00"))
            except ValueError:
                logger.warning(f"Could not parse generation_timestamp string '{generation_timestamp_input}' for snippet {snippet_id}, using current time.")
                generation_timestamp_to_save = current_ts
        elif isinstance(generation_timestamp_input, datetime):
            generation_timestamp_to_save = generation_timestamp_input
        else:
            logger.warning(f"Unexpected type for generation_timestamp '{type(generation_timestamp_input)}' for snippet {snippet_id}, using current time.")
            generation_timestamp_to_save = current_ts

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
            snippet_object.get("summary"), json.dumps(keywords_data) if keywords_data else None, # Ensure JSON string for PG JSONB
            snippet_object.get("source_url"), snippet_object.get("source_name"),
            json.dumps(original_topic_details_data) if original_topic_details_data else None, # Ensure JSON string for PG JSONB
            snippet_object.get("llm_model_used"), snippet_object.get("cover_art_prompt"),
            snippet_object.get("image_url"), generation_timestamp_to_save,
            current_ts,
            snippet_object.get("relevance_score", 0.5)
        )
        cursor.execute(sql, params)
        # Commit is handled by the calling task
        logger.info(f"Prepared save/replace for snippet {snippet_id} to PostgreSQL DB: {snippet_object.get('title')}")

    except psycopg2.Error as e:
        logger.error(f"Database error saving snippet {snippet_id} (PostgreSQL): {e}", exc_info=True)
        raise # Re-raise
    except Exception as e_unexp:
        logger.error(f"Unexpected error saving snippet {snippet_id} (PostgreSQL): {e_unexp}", exc_info=True)
        raise # Re-raise
    finally:
        if cursor: cursor.close()
        # Connection closing and commit/rollback are handled by the calling task

# --- Topic Exploration DB Helper ---
def _get_topic_details_from_db(db_conn, topic_id: str) -> Optional[Dict[str, Any]]: # Added db_conn
    """Fetches details for a specific topic_id from the topics_snippets table (PostgreSQL only).
    Expects db_conn to be passed.
    """
    cursor = None
    topic_id_str = str(topic_id)
    log_extra = {'workflow_id': None, 'task_id': topic_id_str} # Assuming topic_id can serve as task_id for logging

    try:
        if not db_conn:
            logger.error(f"DB connection not provided for fetching topic {topic_id_str}.", extra=log_extra)
            raise ConnectionError(f"DB connection not provided to _get_topic_details_from_db for topic {topic_id_str}")

        cursor = db_conn.cursor()
        sql = "SELECT * FROM topics_snippets WHERE id = %s AND type = %s;"
        cursor.execute(sql, (topic_id_str, DB_TYPE_TOPIC))
        row = cursor.fetchone()
        if row:
            # RealDictCursor returns a dict. Keywords and original_topic_details are JSONB,
            # psycopg2 should handle their conversion to Python dict/list automatically.
            return dict(row)
        return None

    except psycopg2.Error as e:
        logger.error(f"Database error fetching topic {topic_id_str} (PostgreSQL): {e}", exc_info=True)
        return None
    except Exception as e_unexp:
        logger.error(f"Unexpected error fetching topic {topic_id_str} (PostgreSQL): {e_unexp}", exc_info=True)
        return None
    finally:
        if cursor: cursor.close()
        # Connection managed by the caller


def orchestrate_snippet_generation(topic_info: dict, db_conn_param = None) -> Dict[str, Any]:
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

    logger.info(f"CPOA: {function_name} - Calling SCA Service for topic_id {topic_id} (async)...")
    sca_task_id = None
    sca_status_url = None
    snippet_data = None

    try:
        # 1. Initiate SCA Task
        initial_sca_response = requests_with_retry(
            "post", SCA_SERVICE_URL,
            max_retries=CPOA_SERVICE_RETRY_COUNT,
            backoff_factor=CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
            json=sca_payload, timeout=30 # Shorter timeout for task dispatch
        )

        if initial_sca_response.status_code != 202:
            logger.error(f"CPOA: {function_name} - SCA service did not accept task for topic_id {topic_id}. Status: {initial_sca_response.status_code}, Response: {initial_sca_response.text[:200]}")
            return {"error": "SCA_TASK_REJECTED", "details": f"SCA rejected task: {initial_sca_response.status_code} - {initial_sca_response.text[:200]}"}

        sca_task_init_data = initial_sca_response.json()
        sca_task_id = sca_task_init_data.get("task_id")
        sca_status_url_suffix = sca_task_init_data.get("status_url")

        if not sca_task_id or not sca_status_url_suffix:
            logger.error(f"CPOA: {function_name} - SCA task submission response missing task_id or status_url for topic_id {topic_id}. Response: {sca_task_init_data}")
            return {"error": "SCA_BAD_TASK_RESPONSE", "details": f"SCA task submission response invalid: {sca_task_init_data}"}

        sca_base_url = '/'.join(SCA_SERVICE_URL.split('/')[:-1]) # e.g., http://sca:5002
        sca_poll_url = f"{sca_base_url}{sca_status_url_suffix}"
        logger.info(f"CPOA: {function_name} - SCA task {sca_task_id} submitted for topic_id {topic_id}. Polling at {sca_poll_url}")

        # 2. Poll SCA Task
        polling_start_time = time.time()
        polling_interval = os.getenv("CPOA_SCA_POLLING_INTERVAL_SECONDS", "3") # Get from env or default
        polling_timeout = os.getenv("CPOA_SCA_POLLING_TIMEOUT_SECONDS", "180")

        while True:
            if time.time() - polling_start_time > polling_timeout:
                logger.error(f"CPOA: {function_name} - Polling SCA task {sca_task_id} for topic_id {topic_id} timed out after {polling_timeout}s.")
                return {"error": "SCA_POLLING_TIMEOUT", "details": f"Polling SCA task {sca_task_id} timed out."}

            try:
                poll_response = requests.get(sca_poll_url, timeout=10)
                poll_response.raise_for_status()
                sca_task_status_data = poll_response.json()
                sca_task_state = sca_task_status_data.get("status")
                logger.info(f"CPOA: {function_name} - SCA task {sca_task_id} for topic_id {topic_id} status: {sca_task_state}")

                if sca_task_state == "SUCCESS":
                    snippet_data_from_task = sca_task_status_data.get("result")
                    if not snippet_data_from_task or isinstance(snippet_data_from_task, dict) and snippet_data_from_task.get("error_code"): # SCA task itself might return an error structure in result
                        logger.error(f"CPOA: {function_name} - SCA task {sca_task_id} succeeded but returned an error or no valid data: {snippet_data_from_task}")
                        return {"error": "SCA_TASK_LOGICAL_ERROR", "details": snippet_data_from_task}
                    snippet_data = snippet_data_from_task # This is the final SnippetDataObject
                    break
                elif sca_task_state == "FAILURE":
                    logger.error(f"CPOA: {function_name} - SCA task {sca_task_id} for topic_id {topic_id} failed. Data: {sca_task_status_data}")
                    return {"error": "SCA_TASK_FAILED", "details": sca_task_status_data.get("result", {}).get("error", "Unknown SCA task failure")}

                time.sleep(polling_interval)
            except requests.exceptions.RequestException as e_poll_sca:
                logger.warning(f"CPOA: {function_name} - Polling SCA task {sca_task_id} for topic_id {topic_id} failed: {e_poll_sca}. Retrying.")
                time.sleep(polling_interval)

        # At this point, snippet_data should be populated from successful SCA task
        logger.info(f"CPOA: {function_name} - SCA task {sca_task_id} successful for topic_id {topic_id}. Snippet data received: {snippet_data.get('snippet_id')}")

        # --- IGA Call for Cover Art (Remains synchronous within this flow for now) ---
        if snippet_data: # Ensure we have snippet_data before proceeding
            cover_art_prompt = snippet_data.get("cover_art_prompt")
            if cover_art_prompt and IGA_SERVICE_URL:
                logger.info(f"CPOA: Orchestrating image generation for snippet '{snippet_data.get('snippet_id')}' with prompt: '{cover_art_prompt}' (async IGA call)")
                iga_payload = {"prompt": cover_art_prompt}
                iga_submit_url = f"{IGA_SERVICE_URL.rstrip('/')}/generate_image" # IGA's async endpoint

                try:
                    initial_iga_response = requests_with_retry("post", iga_submit_url, max_retries=CPOA_SERVICE_RETRY_COUNT,
                                                              backoff_factor=CPOA_SERVICE_RETRY_BACKOFF_FACTOR, json=iga_payload, timeout=30) # Timeout for task submission

                    if initial_iga_response.status_code != 202:
                        logger.warning(f"CPOA: IGA service did not accept task for snippet '{snippet_data.get('snippet_id')}'. Status: {initial_iga_response.status_code}, Response: {initial_iga_response.text[:200]}")
                        snippet_data["image_url"] = None
                    else:
                        iga_task_init_data = initial_iga_response.json()
                        iga_task_id = iga_task_init_data.get("task_id")
                        iga_status_url_suffix = iga_task_init_data.get("status_url")

                        if not iga_task_id or not iga_status_url_suffix:
                            logger.warning(f"CPOA: IGA task submission response missing task_id or status_url for snippet '{snippet_data.get('snippet_id')}'. Response: {iga_task_init_data}")
                            snippet_data["image_url"] = None
                        else:
                            iga_base_url = '/'.join(IGA_SERVICE_URL.rstrip('/').split('/')[:-1]) # if IGA_SERVICE_URL includes /v1 part
                            if not IGA_SERVICE_URL.startswith("http"): iga_base_url = IGA_SERVICE_URL.rstrip('/') # if it's just base
                            else: # Try to infer base if IGA_SERVICE_URL is like http://iga:5007 (no /v1)
                                 parsed_iga_url = urlparse(IGA_SERVICE_URL)
                                 iga_base_url = f"{parsed_iga_url.scheme}://{parsed_iga_url.netloc}"

                            iga_poll_url = f"{iga_base_url}{iga_status_url_suffix}"
                            logger.info(f"CPOA: IGA task {iga_task_id} submitted for snippet '{snippet_data.get('snippet_id')}'. Polling at {iga_poll_url}")

                            polling_start_time_iga = time.time()
                            while True:
                                if time.time() - polling_start_time_iga > CPOA_IGA_POLLING_TIMEOUT_SECONDS:
                                    logger.error(f"CPOA: Polling IGA task {iga_task_id} for snippet '{snippet_data.get('snippet_id')}' timed out.")
                                    snippet_data["image_url"] = None
                                    break
                                try:
                                    poll_response_iga = requests.get(iga_poll_url, timeout=10)
                                    poll_response_iga.raise_for_status()
                                    iga_task_status_data = poll_response_iga.json()
                                    iga_task_state = iga_task_status_data.get("status")
                                    logger.info(f"CPOA: IGA task {iga_task_id} status: {iga_task_state} for snippet '{snippet_data.get('snippet_id')}'")

                                    if iga_task_state == "SUCCESS":
                                        iga_result = iga_task_status_data.get("result")
                                        if iga_result and iga_result.get("image_url"):
                                            snippet_data["image_url"] = iga_result["image_url"]
                                            logger.info(f"CPOA: Successfully received image_url '{snippet_data['image_url']}' from IGA task for snippet '{snippet_data.get('snippet_id')}'.")
                                        else:
                                            logger.warning(f"CPOA: IGA task {iga_task_id} succeeded but result or image_url missing for snippet '{snippet_data.get('snippet_id')}'. Data: {iga_task_status_data}")
                                            snippet_data["image_url"] = None
                                        break
                                    elif iga_task_state == "FAILURE":
                                        logger.error(f"CPOA: IGA task {iga_task_id} failed for snippet '{snippet_data.get('snippet_id')}'. Data: {iga_task_status_data}")
                                        snippet_data["image_url"] = None
                                        break
                                    time.sleep(CPOA_IGA_POLLING_INTERVAL_SECONDS)
                                except requests.exceptions.RequestException as e_poll_iga:
                                    logger.warning(f"CPOA: Polling IGA task {iga_task_id} for snippet '{snippet_data.get('snippet_id')}' failed: {e_poll_iga}. Retrying.")
                                    time.sleep(CPOA_IGA_POLLING_INTERVAL_SECONDS)
                except requests.exceptions.RequestException as e_iga_req_initial:
                    logger.warning(f"CPOA: IGA service initial call failed for snippet '{snippet_data.get('snippet_id')}': {e_iga_req_initial}", exc_info=True)
                    snippet_data["image_url"] = None
                except Exception as e_iga_unexp: # Catch other unexpected errors during IGA interaction
                    logger.error(f"CPOA: Unexpected error during IGA interaction for snippet '{snippet_data.get('snippet_id')}': {e_iga_unexp}", exc_info=True)
                    snippet_data["image_url"] = None
            elif not IGA_SERVICE_URL:
                logger.warning("CPOA: IGA_SERVICE_URL not configured. Skipping image generation for snippets.")
                snippet_data["image_url"] = None
            else: # No cover_art_prompt
                 snippet_data["image_url"] = None

            _save_snippet_to_db(snippet_data) # Save snippet with or without image_url
            return snippet_data
        else:
            logger.error(f"CPOA: {function_name} - Snippet data is None after successful polling for SCA task {sca_task_id}. This indicates an issue.")
            return {"error": "SCA_POLLING_LOGIC_ERROR", "details": "Snippet data missing after SCA task success."}

    except requests.exceptions.RequestException as e_req: # For initial SCA call
        error_message = f"SCA service initial call failed for topic_id {topic_id}: {str(e_req)}"
        logger.error(f"CPOA: {function_name} - {error_message}", exc_info=True)
        return {"error": SCA_STATUS_CALL_FAILED_AFTER_RETRIES, "details": error_message}
    except json.JSONDecodeError as e_json: # For initial SCA call response
        error_message = f"SCA service initial response was not valid JSON for topic_id {topic_id}: {str(e_json)}"
        logger.error(f"CPOA: {function_name} - {error_message}", exc_info=True)
        return {"error": SCA_STATUS_RESPONSE_INVALID_JSON, "details": error_message, "raw_response": initial_sca_response.text[:500] if 'initial_sca_response' in locals() else "N/A"}
    except Exception as e:
        error_message = f"Unexpected error during SCA interaction for topic_id {topic_id}: {str(e)}"
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

    # Example for testing (assuming PostgreSQL related env vars are set):
    # Ensure your .env for CPOA points to a test PG DB for this block.
    # Local schema creation for testing is removed as it should target a running PG instance.
    logger.info(f"__main__ testing assumes PostgreSQL DB is available and configured via environment variables.")

    sample_topic_1 = "AI in Healthcare"
    # The 'task_id' for orchestrate_podcast_generation is now expected to be the Celery task_id.
    # For direct testing of the core logic, we can simulate one.
    sample_celery_task_id_1 = str(uuid.uuid4())
    print(f"\nTest 1: Orchestrating for topic '{sample_topic_1}' (Simulated Celery Task ID: {sample_celery_task_id_1})")

    # If testing the trigger function:
    # result1_trigger = trigger_podcast_orchestration(topic=sample_topic_1)
    # print(f"\n--- Result for '{sample_topic_1}' (Trigger) ---")
    # pretty_print_orchestration_result(result1_trigger)
    # print(f"To check status, poll: CPOA_HOST{result1_trigger['status_url']}")

    # Direct call to core logic for testing (simulating what Celery worker would do)
    # Note: original_task_id is now the Celery task's ID.
    result1 = orchestrate_podcast_generation(topic=sample_topic_1, original_task_id=sample_celery_task_id_1)
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

# --- Flask App for Task Status (Minimal) ---
# This makes CPOA potentially runnable as a service for status checks.
cpoa_flask_app = Flask(__name__)

@cpoa_flask_app.route('/v1/cpoa_tasks/<task_id>', methods=['GET'])
def get_cpoa_task_status(task_id: str):
    logger.info(f"CPOA Flask: Received request for CPOA task status: {task_id}")
    # Use the globally defined celery_app instance
    task_result = AsyncResult(task_id, app=celery_app)

    response_data = {
        "task_id": task_id,
        "status": task_result.status,
        "result": None
    }
    http_status_code = 200

    if task_result.successful():
        response_data["result"] = task_result.result
    elif task_result.failed():
        response_data["result"] = {
            "error": {"type": "task_failed", "message": str(task_result.info)}
        }
        # If the result itself contains a CPOA error structure, propagate that
        if isinstance(task_result.info, dict) and task_result.info.get("error_message"):
             response_data["result"]["cpoa_error"] = task_result.info # task_result.info is the actual result dict from failed task
        http_status_code = 200 # Or 500 if preferred for task failure
    else: # PENDING, STARTED, RETRY
        http_status_code = 202

    return flask_jsonify(response_data), http_status_code

@cpoa_flask_app.route('/cpoa/health', methods=['GET'])
def cpoa_health_check():
    return flask_jsonify({"status": "CPOA Flask health check OK"}), 200


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

    # Call TDA Service (now asynchronous)
    tda_topics = []
    tda_task_id_from_service = None
    if not TDA_SERVICE_URL:
        tda_error_details = {"message": "TDA_SERVICE_URL is not configured."}
        wf_logger.error(tda_error_details["message"], extra={'task_id': tda_task_id})
    elif tda_task_id: # If CPOA task instance was created
        try:
            _update_task_instance_status(tda_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            tda_payload = {"query": query_for_tda, "limit": 3} # Adjust limit as needed

            initial_tda_response = requests_with_retry("post", TDA_SERVICE_URL,
                                                       max_retries=CPOA_SERVICE_RETRY_COUNT,
                                                       backoff_factor=CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                                       json=tda_payload, timeout=30) # Timeout for task dispatch

            if initial_tda_response.status_code != 202:
                tda_error_details = {"message": f"TDA service did not accept task. Status: {initial_tda_response.status_code}", "response_text": initial_tda_response.text[:200]}
                raise Exception(tda_error_details["message"])

            tda_task_init_data = initial_tda_response.json()
            tda_task_id_from_service = tda_task_init_data.get("task_id")
            tda_status_url_suffix = tda_task_init_data.get("status_url")

            if not tda_task_id_from_service or not tda_status_url_suffix:
                tda_error_details = {"message": "TDA task submission response missing task_id or status_url.", "response_data": tda_task_init_data}
                raise Exception(tda_error_details["message"])

            tda_base_url = '/'.join(TDA_SERVICE_URL.split('/')[:-1]) # e.g., http://tda:5000
            tda_poll_url = f"{tda_base_url}{tda_status_url_suffix}"
            wf_logger.info(f"TDA task {tda_task_id_from_service} submitted for exploration. Polling at {tda_poll_url}", extra={'task_id': tda_task_id})

            polling_start_time = time.time()
            while True:
                if time.time() - polling_start_time > CPOA_TDA_POLLING_TIMEOUT_SECONDS:
                    tda_error_details = {"message": f"Polling TDA task {tda_task_id_from_service} timed out."}
                    raise Exception(tda_error_details["message"])
                try:
                    poll_response_tda = requests.get(tda_poll_url, timeout=10)
                    poll_response_tda.raise_for_status()
                    tda_task_status_data = poll_response_tda.json()
                    tda_task_state = tda_task_status_data.get("status")
                    wf_logger.info(f"TDA task {tda_task_id_from_service} status: {tda_task_state}", extra={'task_id': tda_task_id})

                    if tda_task_state == "SUCCESS":
                        tda_result = tda_task_status_data.get("result", {})
                        if tda_result.get("status") == "success": # Check internal status from TDA task
                            tda_topics = tda_result.get("discovered_topics", [])
                            tda_output_summary = {"topic_count": len(tda_topics), "query_used": query_for_tda}
                            wf_logger.info(f"TDA task {tda_task_id_from_service} successful. Found {len(tda_topics)} topics.", extra={'task_id': tda_task_id})
                        else:
                            tda_error_details = {"message": "TDA task succeeded but reported internal failure.", "tda_response": tda_result}
                        break
                    elif tda_task_state == "FAILURE":
                        tda_error_details = {"message": "TDA task execution failed.", "tda_celery_response": tda_task_status_data.get("result")}
                        break
                    time.sleep(CPOA_TDA_POLLING_INTERVAL_SECONDS)
                except requests.exceptions.RequestException as e_poll_tda:
                    wf_logger.warning(f"Polling TDA task {tda_task_id_from_service} failed: {e_poll_tda}. Retrying.", extra={'task_id': tda_task_id})
                    time.sleep(CPOA_TDA_POLLING_INTERVAL_SECONDS)

        except requests.exceptions.RequestException as e_req:
            tda_error_details = {"message": f"TDA service initial call failed for exploration: {str(e_req)}", "exception_type": type(e_req).__name__}
        except json.JSONDecodeError as e_json:
             tda_error_details = {"message": f"Failed to decode TDA initial response for exploration: {str(e_json)}", "response_preview": initial_tda_response.text[:200] if 'initial_tda_response' in locals() else "N/A"}
        except Exception as e_gen_tda: # Catch other errors during dispatch or polling setup
            tda_error_details = tda_error_details or {"message": f"Unexpected error during TDA interaction for exploration: {str(e_gen_tda)}", "exception_type": type(e_gen_tda).__name__}

    if tda_error_details or not tda_topics: # If any error occurred or still no topics
        final_error_msg = (tda_error_details.get("message") if tda_error_details else None) or "TDA returned no topics for exploration."
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=final_error_msg, context_data={"tda_query": query_for_tda})
        # Ensure the CPOA task instance is also updated
        if tda_task_id: _update_task_instance_status(tda_task_id, TASK_STATUS_FAILED, output_summary=tda_output_summary, error_details=tda_error_details or {"message":final_error_msg}, workflow_id_for_log=workflow_id)
        return {"error": "TDA_FAILURE", "details": final_error_msg, "explored_topics": [], "workflow_id": workflow_id}

    # Update CPOA task instance for TDA successfully
    if tda_task_id: _update_task_instance_status(tda_task_id, TASK_STATUS_COMPLETED, output_summary=tda_output_summary, workflow_id_for_log=workflow_id)

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

    tda_topics = [] # This will be populated by the polling result if successful
    tda_task_id_from_service = None # To store the task_id from TDA
    tda_error_details = None
    tda_output_summary = {}

    if not TDA_SERVICE_URL:
        tda_error_details = {"message": "TDA_SERVICE_URL is not configured."}
        wf_logger.error(tda_error_details["message"], extra={'task_id': tda_task_id})
    elif tda_task_id: # If CPOA task instance was created
        try:
            _update_task_instance_status(tda_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)

            initial_tda_response = requests_with_retry("post", TDA_SERVICE_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                                       json=tda_input_params, timeout=30, # Shorter timeout for task dispatch
                                                       workflow_id_for_log=workflow_id, task_id_for_log=tda_task_id)

            if initial_tda_response.status_code != 202:
                tda_error_details = {"message": f"TDA service did not accept task. Status: {initial_tda_response.status_code}", "response_text": initial_tda_response.text[:200]}
                raise Exception(tda_error_details["message"])

            tda_task_init_data = initial_tda_response.json()
            tda_task_id_from_service = tda_task_init_data.get("task_id")
            tda_status_url_suffix = tda_task_init_data.get("status_url")

            if not tda_task_id_from_service or not tda_status_url_suffix:
                tda_error_details = {"message": "TDA task submission response missing task_id or status_url.", "response_data": tda_task_init_data}
                raise Exception(tda_error_details["message"])

            tda_base_url = '/'.join(TDA_SERVICE_URL.split('/')[:-1])
            tda_poll_url = f"{tda_base_url}{tda_status_url_suffix}"
            wf_logger.info(f"TDA task {tda_task_id_from_service} submitted for search. Polling at {tda_poll_url}", extra={'task_id': tda_task_id})

            polling_start_time = time.time()
            while True:
                if time.time() - polling_start_time > CPOA_TDA_POLLING_TIMEOUT_SECONDS:
                    tda_error_details = {"message": f"Polling TDA task {tda_task_id_from_service} timed out."}
                    raise Exception(tda_error_details["message"])
                try:
                    poll_response_tda = requests.get(tda_poll_url, timeout=10)
                    poll_response_tda.raise_for_status()
                    tda_task_status_data = poll_response_tda.json()
                    tda_task_state = tda_task_status_data.get("status")
                    wf_logger.info(f"TDA task {tda_task_id_from_service} status: {tda_task_state}", extra={'task_id': tda_task_id})

                    if tda_task_state == "SUCCESS":
                        tda_result = tda_task_status_data.get("result", {})
                        if tda_result.get("status") == "success":
                            tda_topics = tda_result.get("discovered_topics", [])
                            tda_output_summary = {"topic_count": len(tda_topics), "query_used": query}
                            wf_logger.info(f"TDA task {tda_task_id_from_service} successful. Found {len(tda_topics)} topics.", extra={'task_id': tda_task_id})
                        else:
                            tda_error_details = {"message": "TDA task succeeded but reported internal failure.", "tda_response": tda_result}
                        break
                    elif tda_task_state == "FAILURE":
                        tda_error_details = {"message": "TDA task execution failed.", "tda_celery_response": tda_task_status_data.get("result")}
                        break
                    time.sleep(CPOA_TDA_POLLING_INTERVAL_SECONDS)
                except requests.exceptions.RequestException as e_poll_tda:
                    wf_logger.warning(f"Polling TDA task {tda_task_id_from_service} failed: {e_poll_tda}. Retrying.", extra={'task_id': tda_task_id})
                    time.sleep(CPOA_TDA_POLLING_INTERVAL_SECONDS)

        except requests.exceptions.RequestException as e_req:
            tda_error_details = {"message": f"TDA service initial call failed for search: {str(e_req)}", "exception_type": type(e_req).__name__}
        except json.JSONDecodeError as e_json:
             tda_error_details = {"message": f"Failed to decode TDA initial response for search: {str(e_json)}", "response_preview": initial_tda_response.text[:200] if 'initial_tda_response' in locals() else "N/A"}
        except Exception as e_gen_tda:
            tda_error_details = tda_error_details or {"message": f"Unexpected error during TDA interaction for search: {str(e_gen_tda)}", "exception_type": type(e_gen_tda).__name__}

    if tda_error_details or not tda_topics: # If any error occurred or still no topics
        final_error_msg = (tda_error_details.get("message") if tda_error_details else None) or "TDA returned no topics for search."
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=final_error_msg, context_data={"tda_query": query})
        # Ensure the CPOA task instance is also updated
        if tda_task_id: _update_task_instance_status(tda_task_id, TASK_STATUS_FAILED, output_summary=tda_output_summary, error_details=tda_error_details or {"message": final_error_msg}, workflow_id_for_log=workflow_id)
        return {"error": "TDA_FAILURE", "details": final_error_msg, "search_results": [], "workflow_id": workflow_id}

    # Update CPOA task instance for TDA successfully
    if tda_task_id: _update_task_instance_status(tda_task_id, TASK_STATUS_COMPLETED, output_summary=tda_output_summary, workflow_id_for_log=workflow_id)

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

    tda_topics = [] # This will be populated by the polling result if successful
    tda_task_id_from_service = None # To store the task_id from TDA
    tda_error_details = None
    tda_output_summary = {}

    if not TDA_SERVICE_URL:
        tda_error_details = {"message": "TDA_SERVICE_URL is not configured."}
        wf_logger.error(tda_error_details["message"], extra={'task_id': tda_task_id})
    elif tda_task_id: # If CPOA task instance was created
        try:
            _update_task_instance_status(tda_task_id, TASK_STATUS_IN_PROGRESS, workflow_id_for_log=workflow_id)
            initial_tda_response = requests_with_retry("post", TDA_SERVICE_URL, CPOA_SERVICE_RETRY_COUNT, CPOA_SERVICE_RETRY_BACKOFF_FACTOR,
                                                       json=tda_input_params, timeout=30, # Shorter timeout for task dispatch
                                                       workflow_id_for_log=workflow_id, task_id_for_log=tda_task_id)

            if initial_tda_response.status_code != 202:
                tda_error_details = {"message": f"TDA service did not accept task. Status: {initial_tda_response.status_code}", "response_text": initial_tda_response.text[:200]}
                raise Exception(tda_error_details["message"])

            tda_task_init_data = initial_tda_response.json()
            tda_task_id_from_service = tda_task_init_data.get("task_id")
            tda_status_url_suffix = tda_task_init_data.get("status_url")

            if not tda_task_id_from_service or not tda_status_url_suffix:
                tda_error_details = {"message": "TDA task submission response missing task_id or status_url.", "response_data": tda_task_init_data}
                raise Exception(tda_error_details["message"])

            tda_base_url = '/'.join(TDA_SERVICE_URL.split('/')[:-1])
            tda_poll_url = f"{tda_base_url}{tda_status_url_suffix}"
            wf_logger.info(f"TDA task {tda_task_id_from_service} submitted for landing page. Polling at {tda_poll_url}", extra={'task_id': tda_task_id})

            polling_start_time = time.time()
            while True:
                if time.time() - polling_start_time > CPOA_TDA_POLLING_TIMEOUT_SECONDS:
                    tda_error_details = {"message": f"Polling TDA task {tda_task_id_from_service} timed out."}
                    raise Exception(tda_error_details["message"])
                try:
                    poll_response_tda = requests.get(tda_poll_url, timeout=10)
                    poll_response_tda.raise_for_status()
                    tda_task_status_data = poll_response_tda.json()
                    tda_task_state = tda_task_status_data.get("status")
                    wf_logger.info(f"TDA task {tda_task_id_from_service} status: {tda_task_state}", extra={'task_id': tda_task_id})

                    if tda_task_state == "SUCCESS":
                        tda_result = tda_task_status_data.get("result", {})
                        if tda_result.get("status") == "success":
                            tda_topics = tda_result.get("discovered_topics", [])
                            tda_output_summary = {"topic_count": len(tda_topics), "query_used": query_for_tda}
                            wf_logger.info(f"TDA task {tda_task_id_from_service} successful. Found {len(tda_topics)} topics.", extra={'task_id': tda_task_id})
                        else:
                            tda_error_details = {"message": "TDA task succeeded but reported internal failure.", "tda_response": tda_result}
                        break
                    elif tda_task_state == "FAILURE":
                        tda_error_details = {"message": "TDA task execution failed.", "tda_celery_response": tda_task_status_data.get("result")}
                        break
                    time.sleep(CPOA_TDA_POLLING_INTERVAL_SECONDS)
                except requests.exceptions.RequestException as e_poll_tda:
                    wf_logger.warning(f"Polling TDA task {tda_task_id_from_service} failed: {e_poll_tda}. Retrying.", extra={'task_id': tda_task_id})
                    time.sleep(CPOA_TDA_POLLING_INTERVAL_SECONDS)

        except requests.exceptions.RequestException as e_req:
            tda_error_details = {"message": f"TDA service initial call failed for landing page: {str(e_req)}", "exception_type": type(e_req).__name__}
        except json.JSONDecodeError as e_json:
             tda_error_details = {"message": f"Failed to decode TDA initial response for landing page: {str(e_json)}", "response_preview": initial_tda_response.text[:200] if 'initial_tda_response' in locals() else "N/A"}
        except Exception as e_gen_tda:
            tda_error_details = tda_error_details or {"message": f"Unexpected error during TDA interaction for landing page: {str(e_gen_tda)}", "exception_type": type(e_gen_tda).__name__}

    if tda_error_details or not tda_topics: # If any error occurred or still no topics
        final_error_msg = (tda_error_details.get("message") if tda_error_details else None) or "TDA returned no topics for landing page."
        _update_workflow_instance_status(workflow_id, WORKFLOW_STATUS_FAILED, error_message=final_error_msg, context_data={"tda_query": query_for_tda})
        # Ensure the CPOA task instance is also updated
        if tda_task_id: _update_task_instance_status(tda_task_id, TASK_STATUS_FAILED, output_summary=tda_output_summary, error_details=tda_error_details or {"message":final_error_msg}, workflow_id_for_log=workflow_id)
        return {"error": "TDA_FAILURE", "details": final_error_msg, "snippets": [], "workflow_id": workflow_id}

    # Update CPOA task instance for TDA successfully
    if tda_task_id: _update_task_instance_status(tda_task_id, TASK_STATUS_COMPLETED, output_summary=tda_output_summary, workflow_id_for_log=workflow_id)

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