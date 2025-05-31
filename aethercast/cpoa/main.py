import logging
import sys
import os
from dotenv import load_dotenv # Added
import json
import sqlite3
from datetime import datetime
import uuid
from typing import Optional, Dict, Any, List
import requests # Added for service calls
import time # Added for retry logic

# Ensure the 'aethercast' directory is in the Python path.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_script_dir)
repo_root_dir = os.path.dirname(parent_dir)
if repo_root_dir not in sys.path:
    sys.path.insert(0, repo_root_dir)

load_dotenv() # Added

# --- Service URLs ---
PSWA_SERVICE_URL = os.getenv("PSWA_SERVICE_URL", "http://localhost:5004/weave_script")
VFA_SERVICE_URL = os.getenv("VFA_SERVICE_URL", "http://localhost:5005/forge_voice")
ASF_NOTIFICATION_URL = os.getenv("ASF_NOTIFICATION_URL", "http://localhost:5006/asf/internal/notify_new_audio")
ASF_WEBSOCKET_BASE_URL = os.getenv("ASF_WEBSOCKET_BASE_URL", "ws://localhost:5006/api/v1/podcasts/stream")
SCA_SERVICE_URL = os.getenv("SCA_SERVICE_URL", "http://localhost:5002/craft_snippet")
CPOA_DATABASE_PATH = os.getenv("CPOA_DATABASE_PATH", "cpoa_orchestration_tasks.db")
CPOA_ASF_SEND_UI_UPDATE_URL = os.getenv("CPOA_ASF_SEND_UI_UPDATE_URL", "http://localhost:5006/asf/internal/send_ui_update") # Added
CPOA_SERVICE_RETRY_COUNT = int(os.getenv("CPOA_SERVICE_RETRY_COUNT", "3"))
CPOA_SERVICE_RETRY_BACKOFF_FACTOR = float(os.getenv("CPOA_SERVICE_RETRY_BACKOFF_FACTOR", "0.5"))

# Moved TDA_SERVICE_URL to be loaded in load_cpoa_configuration

# --- Logging Configuration ---
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - CPOA - %(message)s')

# Load CPOA configurations (this will now include TDA_SERVICE_URL)
# Ensure this is called early, but after logger basicConfig if logger is used within.
# Note: Global `pswa_config` style might be better if config is only loaded once.
# For now, assuming this structure from existing file.
# It seems CPOA_ASF_SEND_UI_UPDATE_URL etc. are module-level globals set from os.getenv directly.
# To be consistent, TDA_SERVICE_URL should also be a module-level global for now.
TDA_SERVICE_URL = os.getenv("TDA_SERVICE_URL", "http://localhost:5000/discover_topics")


# Log loaded configuration
logger.info("--- CPOA Configuration ---")
logger.info(f"TDA_SERVICE_URL: {TDA_SERVICE_URL}")
logger.info(f"PSWA_SERVICE_URL: {PSWA_SERVICE_URL}")
logger.info(f"VFA_SERVICE_URL: {VFA_SERVICE_URL}")
logger.info(f"ASF_NOTIFICATION_URL: {ASF_NOTIFICATION_URL}")
logger.info(f"ASF_WEBSOCKET_BASE_URL: {ASF_WEBSOCKET_BASE_URL}")
logger.info(f"SCA_SERVICE_URL: {SCA_SERVICE_URL}")
logger.info(f"CPOA_DATABASE_PATH: {CPOA_DATABASE_PATH}")
logger.info(f"CPOA_ASF_SEND_UI_UPDATE_URL: {CPOA_ASF_SEND_UI_UPDATE_URL}") # Added
logger.info(f"CPOA_SERVICE_RETRY_COUNT: {CPOA_SERVICE_RETRY_COUNT}")
logger.info(f"CPOA_SERVICE_RETRY_BACKOFF_FACTOR: {CPOA_SERVICE_RETRY_BACKOFF_FACTOR}")
logger.info("--- End CPOA Configuration ---")


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


def get_timestamp() -> str: # This function seems unused in the provided snippet, consider removing if not used elsewhere.
    return datetime.utcnow().isoformat() + "Z"

def _update_task_status_in_db(db_path: str, task_id: str, new_cpoa_status: str, error_msg: Optional[str] = None) -> None:
    """
    Updates the cpoa_status, cpoa_error_message, and last_updated_timestamp for a task in the database.
    This function is called by CPOA during its orchestration process.
    """
    logger.info(f"Task {task_id}: Attempting to update CPOA status in DB to '{new_cpoa_status}'. Error msg: '{error_msg if error_msg else 'None'}'")
    timestamp = datetime.now().isoformat()
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE podcasts
            SET cpoa_status = ?, cpoa_error_message = ?, last_updated_timestamp = ?
            WHERE podcast_id = ?
            """,
            (new_cpoa_status, error_msg, timestamp, task_id)
        )
        conn.commit()
        logger.info(f"Task {task_id}: Successfully updated CPOA status in DB to '{new_cpoa_status}'.")
    except sqlite3.Error as e:
        logger.error(f"CPOA: Database error for task {task_id} updating to status {new_cpoa_status}: {type(e).__name__} - {e}")
    except Exception as e: # Catch any other unexpected error during DB update
        logger.error(f"CPOA: Unexpected error in _update_task_status_in_db for task {task_id} (status: {new_cpoa_status}): {type(e).__name__} - {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --- Helper function to send UI updates to ASF ---
def _send_ui_update(client_id: Optional[str], event_name: str, data: Dict[str, Any]):
    """
    Sends a UI update message to ASF's internal endpoint.
    This is a non-critical operation; failures are logged but do not halt orchestration.
    """
    if not client_id:
        logger.info("No client_id provided, skipping UI update.")
        return

    if not CPOA_ASF_SEND_UI_UPDATE_URL:
        logger.warning("CPOA_ASF_SEND_UI_UPDATE_URL not configured. Cannot send UI update.")
        return

    payload = {
        "client_id": client_id,
        "event_name": event_name,
        "data": data
    }
    try:
        # Using requests_with_retry for this internal call, but with very few retries.
        # Timeout should be short as it's an internal call.
        response = requests_with_retry(
            "post",
            CPOA_ASF_SEND_UI_UPDATE_URL,
            max_retries=1, # Low retry for internal, quick calls
            backoff_factor=0.1, # Short backoff
            json=payload,
            timeout=5 # Short timeout
        )
        if response.status_code == 200:
            logger.info(f"Successfully sent UI update '{event_name}' for client_id '{client_id}'. Data: {data}")
        else:
            logger.warning(f"Failed to send UI update '{event_name}' for client_id '{client_id}'. ASF responded with {response.status_code}: {response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending UI update '{event_name}' for client_id '{client_id}' to ASF: {e}")
    except Exception as e_unexp: # Catch any other unexpected error
        logger.error(f"Unexpected error in _send_ui_update for client_id '{client_id}': {e_unexp}", exc_info=True)


def orchestrate_podcast_generation(topic: str, task_id: str, db_path: str, voice_params_input: Optional[dict] = None, client_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Orchestrates the podcast generation by calling WCHA, PSWA, and VFA in sequence,
    optionally using provided voice parameters for VFA.
    Uses web search via WCHA's get_content_for_topic.
    Updates task status in a database.
    """
    orchestration_log: List[Dict[str, Any]] = []
    # Initialize vfa_result_dict with a 'not_run' status. This will be updated if VFA is reached.
    vfa_result_dict: Dict[str, Any] = {"status": "not_run", "message": "VFA not reached.", "audio_filepath": None, "stream_id": None}
    current_orchestration_stage: str = "initialization"
    # final_cpoa_status and final_error_message will be determined by the outcome of the steps.
    # Initialize them here to ensure they are always defined before the finally block.
    final_cpoa_status: str = "pending" # Default status
    final_error_message: Optional[str] = None
    asf_notification_status_message: Optional[str] = None # For ASF notification outcome

    # Get retry configurations
    retry_count = CPOA_SERVICE_RETRY_COUNT
    backoff_factor = CPOA_SERVICE_RETRY_BACKOFF_FACTOR

    def log_step(message: str, data: Optional[Dict[str, Any]] = None, is_error_payload: bool = False) -> None:
        timestamp = datetime.now().isoformat()
        # current_orchestration_stage is available in the outer function's scope
        log_entry: Dict[str, Any] = {"timestamp": timestamp, "stage": current_orchestration_stage, "message": message}

        log_data_str_for_preview = "N/A"

        if data is not None:
            # Store the actual dictionary for structured_data if it's not an error payload meant only for preview
            if not is_error_payload:
                 log_entry["structured_data"] = data

            # Create string representation for data_preview
            try:
                # For error payloads or complex data, ensure sensitive info is handled if necessary before full dump
                log_data_str_for_preview = json.dumps(data)
            except TypeError: 
                try:
                    log_data_str_for_preview = str(data) # Fallback to string representation
                except Exception:
                    log_data_str_for_preview = "Data could not be serialized or converted to string for preview"
        
        preview_limit = 250  # Keep preview concise
        log_entry["data_preview"] = log_data_str_for_preview[:preview_limit] + "..." if len(log_data_str_for_preview) > preview_limit else log_data_str_for_preview
        
        orchestration_log.append(log_entry)
        # Also log to the main CPOA logger for real-time visibility if needed
        logger.info(f"Task {task_id} @ {current_orchestration_stage}: {message} - Preview: {log_entry['data_preview']}")

    # Log initial parameters
    log_step("Orchestration process started.", data={"task_id": task_id, "topic": topic, "db_path": db_path, "voice_params_input": voice_params_input, "client_id": client_id})

    # Check for import issues first
    if not WCHA_IMPORT_SUCCESSFUL:
        current_orchestration_stage = "initialization_failure"
        critical_error_msg = str(WCHA_MISSING_IMPORT_ERROR if WCHA_MISSING_IMPORT_ERROR else "WCHA module import error not specified.")
        log_step(f"CPOA critical failure: WCHA module import error.", data={"error_message": critical_error_msg})
        _send_ui_update(client_id, "task_error", {"message": critical_error_msg, "stage": current_orchestration_stage})
        final_cpoa_status = "failed_wcha_module_error"
        final_error_message = critical_error_msg
    
    else: # Proceed with orchestration
        try:
            current_orchestration_stage = "wcha_content_retrieval"
            _update_task_status_in_db(db_path, task_id, current_orchestration_stage, error_msg=None)
            _send_ui_update(client_id, "generation_status", {"message": "Fetching and processing web content...", "stage": current_orchestration_stage})
            log_step("Calling WCHA (get_content_for_topic)...", data={"topic": topic})
            wcha_output = get_content_for_topic(topic=topic)

            wcha_data_log = {"output_length": len(wcha_output)}
            if any(str(wcha_output).startswith(prefix) for prefix in WCHA_ERROR_INDICATORS):
                 wcha_data_log["wcha_error_content"] = wcha_output # Capture full error if it's an error string
            log_step("WCHA finished.", data=wcha_data_log)


            if not wcha_output or any(str(wcha_output).startswith(prefix) for prefix in WCHA_ERROR_INDICATORS):
                final_error_message = str(wcha_output) if wcha_output else "WCHA returned no content or an error string."
                log_step(f"WCHA content retrieval failure.", data={"error_message": final_error_message, "wcha_output_preview": wcha_output[:100]}, is_error_payload=True)
                final_cpoa_status = "failed_wcha_content_harvest"
                raise Exception(f"WCHA critical failure: {final_error_message}")

            current_orchestration_stage = "pswa_script_generation"
            _update_task_status_in_db(db_path, task_id, current_orchestration_stage, error_msg=None)
            _send_ui_update(client_id, "generation_status", {"message": "Crafting podcast script with AI...", "stage": current_orchestration_stage})
            log_step("Calling PSWA Service (weave_script)...",
                     data={"url": PSWA_SERVICE_URL, "topic": topic, "content_input_length": len(wcha_output)})
            try:
                pswa_payload = {"content": wcha_output, "topic": topic}
                response = requests_with_retry("post", PSWA_SERVICE_URL,
                                               max_retries=retry_count, backoff_factor=backoff_factor,
                                               json=pswa_payload, timeout=180)

                structured_script_from_pswa = response.json()
                pswa_success_data = {
                    "script_id": structured_script_from_pswa.get("script_id"),
                    "title": structured_script_from_pswa.get("title"),
                    "segment_count": len(structured_script_from_pswa.get("segments", []))
                }
                log_step("PSWA Service finished successfully.", data=pswa_success_data)

                if not isinstance(structured_script_from_pswa, dict) or \
                   not pswa_success_data["script_id"] or \
                   not pswa_success_data["title"] or \
                   not isinstance(structured_script_from_pswa.get("segments"), list):
                    final_error_message = "PSWA service returned invalid or malformed structured script."
                    log_step(final_error_message, data={"received_script_preview": structured_script_from_pswa}, is_error_payload=True)
                    final_cpoa_status = "failed_pswa_bad_script_structure"
                    raise Exception(f"PSWA critical failure: {final_error_message}")

            except requests.exceptions.RequestException as e_req:
                err_payload_str = "N/A"
                status_code = "N/A"
                if hasattr(e_req, 'response') and e_req.response is not None:
                    status_code = str(e_req.response.status_code)
                    try:
                        err_payload_str = json.dumps(e_req.response.json())
                    except json.JSONDecodeError:
                        err_payload_str = e_req.response.text[:200] # Use a smaller preview for error payloads in log

                final_error_message = f"PSWA service call failed (HTTP status: {status_code}, type: {type(e_req).__name__}): {str(e_req)}."
                log_step(f"PSWA service request exception.", data={"error_message": final_error_message, "response_payload_preview": err_payload_str}, is_error_payload=True)
                final_cpoa_status = "failed_pswa_request_exception"
                raise Exception(final_error_message)
            except json.JSONDecodeError as e_json:
                final_error_message = f"PSWA service response was not valid JSON: {str(e_json)}"
                log_step(f"PSWA service JSON decode error.", data={"error_message": final_error_message, "response_text_preview": response.text[:200] if 'response' in locals() and response is not None else "N/A"}, is_error_payload=True)
                final_cpoa_status = "failed_pswa_json_decode"
                raise Exception(final_error_message)

            current_orchestration_stage = "vfa_audio_generation"
            _update_task_status_in_db(db_path, task_id, current_orchestration_stage, error_msg=None)
            _send_ui_update(client_id, "generation_status", {"message": "Synthesizing audio...", "stage": current_orchestration_stage})
            vfa_call_data = {"url": VFA_SERVICE_URL, "script_id": structured_script_from_pswa.get("script_id"), "title": structured_script_from_pswa.get("title")}
            if voice_params_input:
                vfa_call_data["voice_params_input"] = voice_params_input

            log_step("Calling VFA Service (forge_voice)...", data=vfa_call_data)
            try:
                vfa_payload = {"script": structured_script_from_pswa}
                if voice_params_input:
                    vfa_payload["voice_params"] = voice_params_input

                response = requests_with_retry("post", VFA_SERVICE_URL,
                                               max_retries=retry_count, backoff_factor=backoff_factor,
                                               json=vfa_payload, timeout=90)
                vfa_result_dict = response.json()
                vfa_success_data = {
                    "status": vfa_result_dict.get("status"),
                    "audio_filepath": vfa_result_dict.get("audio_filepath"),
                    "stream_id": vfa_result_dict.get("stream_id"),
                    "tts_settings_used": vfa_result_dict.get("tts_settings_used")
                }
                log_step("VFA Service finished.", data=vfa_success_data)

            except requests.exceptions.RequestException as e_req_vfa:
                err_payload_str = "N/A"
                status_code = "N/A"
                if hasattr(e_req_vfa, 'response') and e_req_vfa.response is not None:
                    status_code = str(e_req_vfa.response.status_code)
                    try:
                        err_payload_str = json.dumps(e_req_vfa.response.json().get("message", e_req_vfa.response.json()))
                    except json.JSONDecodeError:
                         err_payload_str = e_req_vfa.response.text[:200]

                final_error_message = f"VFA service call failed (HTTP status: {status_code}, type: {type(e_req_vfa).__name__}): {str(e_req_vfa)}."
                log_step(f"VFA service request exception.", data={"error_message": final_error_message, "response_payload_preview": err_payload_str}, is_error_payload=True)
                vfa_result_dict = {"status": "error", "message": final_error_message, "audio_filepath": None}
                final_cpoa_status = "failed_vfa_request_exception"
                raise Exception(final_error_message)
            except json.JSONDecodeError as e_json_vfa:
                final_error_message = f"VFA service response was not valid JSON: {str(e_json_vfa)}"
                log_step(f"VFA service JSON decode error.", data={"error_message": final_error_message, "response_text_preview": response.text[:200] if 'response' in locals() and response is not None else "N/A"}, is_error_payload=True)
                vfa_result_dict = {"status": "error", "message": final_error_message, "audio_filepath": None}
                final_cpoa_status = "failed_vfa_json_decode"
                raise Exception(final_error_message)

            vfa_status = vfa_result_dict.get("status")
            if vfa_status == "success":
                final_cpoa_status = "completed"
                final_error_message = None

                audio_filepath = vfa_result_dict.get("audio_filepath")
                stream_id = vfa_result_dict.get("stream_id")

                if audio_filepath and stream_id:
                    current_orchestration_stage = "asf_notification"
                    _send_ui_update(client_id, "generation_status", {"message": "Preparing audio stream...", "stage": current_orchestration_stage})
                    asf_call_data = {"url": ASF_NOTIFICATION_URL, "stream_id": stream_id, "filepath": audio_filepath}
                    log_step("Notifying ASF about new audio...", data=asf_call_data)
                    try:
                        asf_payload = {"stream_id": stream_id, "filepath": audio_filepath}
                        asf_response = requests_with_retry("post", ASF_NOTIFICATION_URL,
                                                           max_retries=retry_count, backoff_factor=backoff_factor,
                                                           json=asf_payload, timeout=10)
                        asf_notification_status_message = f"ASF notified successfully for stream {stream_id}."
                        log_step(asf_notification_status_message, data={"response_status": asf_response.status_code, "response_payload_preview": asf_response.json() if asf_response.content else None})

                    except requests.exceptions.RequestException as e_asf_req:
                        err_payload_str = "N/A"
                        status_code = "N/A"
                        if hasattr(e_asf_req, 'response') and e_asf_req.response is not None:
                            status_code = str(e_asf_req.response.status_code)
                            try:
                                err_payload_str = json.dumps(e_asf_req.response.json().get("error", e_asf_req.response.json()))
                            except json.JSONDecodeError:
                                err_payload_str = e_asf_req.response.text[:200]

                        asf_notification_status_message = (
                            f"ASF notification failed (HTTP status: {status_code}, type: {type(e_asf_req).__name__}): {str(e_asf_req)}."
                        )
                        log_step(asf_notification_status_message, data={"error_message": str(e_asf_req), "response_payload_preview": err_payload_str}, is_error_payload=True)
                        final_error_message = asf_notification_status_message
                        final_cpoa_status = "completed_with_asf_notification_failure"
                    except json.JSONDecodeError as e_asf_json:
                        asf_notification_status_message = f"ASF notification response was not valid JSON: {str(e_asf_json)}"
                        log_step(asf_notification_status_message, data={"response_text_preview": asf_response.text[:200] if 'asf_response' in locals() and asf_response is not None else "N/A"}, is_error_payload=True)
                        final_error_message = asf_notification_status_message
                        final_cpoa_status = "completed_with_asf_notification_failure_json_decode"
                else:
                    asf_notification_status_message = "ASF notification skipped: audio_filepath or stream_id missing from VFA success response."
                    log_step(asf_notification_status_message, data={"vfa_result": vfa_result_dict}, is_error_payload=True) # Log as error/warning
                    final_error_message = asf_notification_status_message
                    final_cpoa_status = "completed_with_vfa_data_missing"

            elif vfa_status == "skipped":
                final_cpoa_status = "completed_with_vfa_skipped"
                final_error_message = vfa_result_dict.get("message", "VFA skipped audio generation.")
                log_step(f"VFA skipped audio generation.", data={"vfa_result": vfa_result_dict})
            elif vfa_status == "error":
                final_cpoa_status = "failed_vfa_reported_error"
                final_error_message = vfa_result_dict.get("message", "VFA reported an internal error.")
                log_step(f"VFA reported an internal error.", data={"vfa_result": vfa_result_dict}, is_error_payload=True)
            else:
                final_cpoa_status = "failed_vfa_unknown_status"
                final_error_message = f"VFA service returned an unknown status: '{vfa_status}'. Details: {vfa_result_dict.get('message')}"
                log_step(f"VFA unknown status.", data={"vfa_result": vfa_result_dict}, is_error_payload=True)

        except Exception as e:
            logger.error(f"CPOA: Orchestration failed for task {task_id} at stage '{current_orchestration_stage}': {type(e).__name__} - {str(e)}", exc_info=True) # Keep exc_info for main logger
            
            # Set final_error_message if not already specifically set by a caught exception block
            if not final_error_message:
                final_error_message = f"Orchestration error at stage {current_orchestration_stage}: {type(e).__name__} - {str(e)}"

            # Update final_cpoa_status based on stage if not already a specific failure status
            if final_cpoa_status == "pending" or not final_cpoa_status.startswith("failed_") and not final_cpoa_status.startswith("completed_with_"):
                stage_error_map = {
                    "wcha_content_retrieval": "failed_wcha_exception",
                    "pswa_script_generation": "failed_pswa_exception",
                    "vfa_audio_generation": "failed_vfa_exception",
                    "asf_notification": "failed_asf_notification_exception" # Should be caught by ASF block, but as fallback
                }
                final_cpoa_status = stage_error_map.get(current_orchestration_stage, "failed_unknown_stage_exception")

            log_step(f"Orchestration ended with critical error.", data={"error_message": final_error_message, "exception_type": type(e).__name__}, is_error_payload=True)
            _send_ui_update(client_id, "task_error", {"message": final_error_message, "stage": current_orchestration_stage})

            # Ensure vfa_result_dict reflects error if exception occurred before or during VFA, and not caught by VFA specific try-except
            if vfa_result_dict.get('status') == 'not_run' and current_orchestration_stage != "vfa_audio_generation":
                 vfa_result_dict = {"status": "error", "message": final_error_message, "audio_filepath": None, "stream_id": None}
            elif current_orchestration_stage == "vfa_audio_generation" and vfa_result_dict.get('status') != 'error':
                 vfa_result_dict = {"status": "error", "message": final_error_message, "audio_filepath": None, "stream_id": None}

    # Final DB update for CPOA's status
    _update_task_status_in_db(db_path, task_id, final_cpoa_status, error_msg=final_error_message)

    # Final log entry summarizing the outcome
    log_step(f"Orchestration process ended.",
             data={"final_cpoa_status": final_cpoa_status,
                   "final_error_message_preview": final_error_message[:200] if final_error_message else None,
                   "asf_notification_outcome": asf_notification_status_message})

    # Send final UI update on overall success or specific handled failures
    if final_cpoa_status.startswith("failed_") or final_cpoa_status.startswith("completed_with_"):
        if not final_cpoa_status.endswith("_exception"): # Avoid sending generic exception message if already sent by outer try-except
            _send_ui_update(client_id, "task_error", {"message": final_error_message or f"Task ended with status: {final_cpoa_status}", "final_status": final_cpoa_status})
    elif final_cpoa_status == "completed":
         _send_ui_update(client_id, "generation_status", {"message": "Podcast generation complete!", "final_status": final_cpoa_status, "is_terminal": True})

    # Ensure vfa_result_dict (which is final_audio_details) contains stream_id if available
    stream_id_for_url = vfa_result_dict.get("stream_id")
    asf_ws_url = None
    if stream_id_for_url:
        # The client will use this base URL and then send a 'join_stream' message with the stream_id.
        # So, we just provide the base ASF WebSocket URL.
        asf_ws_url = ASF_WEBSOCKET_BASE_URL
        # If ASF expected stream_id in query param: asf_ws_url = f"{ASF_WEBSOCKET_BASE_URL}?stream_id={stream_id_for_url}"

    cpoa_final_result = {
        "task_id": task_id,
        "topic": topic,
        "status": final_cpoa_status,
        "error_message": final_error_message, # This might now include ASF notification issues
        "asf_notification_status": asf_notification_status_message, # Specific status for ASF notification
        "asf_websocket_url": asf_ws_url, # New field for client consumption
        "final_audio_details": vfa_result_dict, # This contains the stream_id from VFA
        "orchestration_log": orchestration_log
    }
    # The API Gateway will use this returned dict to perform its own final update on the podcast record.
    # Ensure tts_settings_used from VFA's response is part of final_audio_details in cpoa_final_result
    if "tts_settings_used" not in vfa_result_dict : # Ensure key exists even on VFA failure/skip for consistency
         vfa_result_dict["tts_settings_used"] = voice_params_input if voice_params_input else None # Default to input if VFA didn't provide


    return cpoa_final_result


# --- Snippet DB Interaction ---
def _save_snippet_to_db(snippet_object: dict, db_path: str):
    """Saves a single snippet object to the topics_snippets table."""
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        keywords_json = json.dumps(snippet_object.get("keywords", []))
        # original_topic_details might be complex; ensure it's stored as JSON string
        original_topic_details_json = json.dumps(snippet_object.get("original_topic_details_from_tda")) \
            if snippet_object.get("original_topic_details_from_tda") else None

        current_ts = datetime.now().isoformat()

        # Relevance score for snippets might not be directly applicable or could be defaulted
        relevance_score = snippet_object.get("relevance_score", 0.5) # Default if not present

        cursor.execute(
            """
            INSERT OR REPLACE INTO topics_snippets (
                id, type, title, summary, keywords,
                source_url, source_name, original_topic_details,
                llm_model_used_for_snippet, cover_art_prompt,
                generation_timestamp, last_accessed_timestamp, relevance_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snippet_object.get("snippet_id"),
                'snippet', # type
                snippet_object.get("title"),
                snippet_object.get("summary"), # Using summary for snippet's main text content
                keywords_json,
                None, # source_url (can be null for snippets, or link to original topic if available)
                None, # source_name (can be null for snippets)
                original_topic_details_json,
                snippet_object.get("llm_model_used"),
                snippet_object.get("cover_art_prompt"),
                snippet_object.get("generation_timestamp", current_ts),
                current_ts, # last_accessed_timestamp
                relevance_score
            )
        )
        conn.commit()
        logger.info(f"Saved/Replaced snippet {snippet_object.get('snippet_id')} to DB: {snippet_object.get('title')}")
    except sqlite3.Error as e:
        logger.error(f"Database error saving snippet {snippet_object.get('snippet_id')}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error saving snippet {snippet_object.get('snippet_id')} to DB: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --- Topic Exploration DB Helper ---
def _get_topic_details_from_db(db_path: str, topic_id: str) -> Optional[Dict[str, Any]]:
    """Fetches details for a specific topic_id from the topics_snippets table."""
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM topics_snippets WHERE id = ? AND type = 'topic'", (topic_id,))
        row = cursor.fetchone()
        if row:
            topic_details = dict(row)
            # Deserialize keywords if stored as JSON string
            if isinstance(topic_details.get('keywords'), str):
                try:
                    topic_details['keywords'] = json.loads(topic_details['keywords'])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to decode keywords JSON for topic {topic_id} from DB: {topic_details['keywords']}")
                    topic_details['keywords'] = [] # Default to empty list on error
            return topic_details
        return None
    except sqlite3.Error as e:
        logger.error(f"Database error fetching topic {topic_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()


def orchestrate_snippet_generation(topic_info: dict) -> Dict[str, Any]:
    """
    Orchestrates snippet generation by calling the SnippetCraftAgent (SCA) service.
    """
    function_name = "orchestrate_snippet_generation"
    logger.info(f"CPOA: {function_name} called for topic_info: {topic_info.get('title_suggestion', 'N/A')}")

    topic_id = topic_info.get("topic_id")
    if not topic_id:
        topic_id = f"topic_adhoc_{uuid.uuid4().hex[:6]}"
        logger.warning(f"CPOA: {function_name} - topic_id missing from input, generated adhoc topic_id: {topic_id}")
    
    content_brief = topic_info.get("title_suggestion") # Using title_suggestion as the content_brief
    if not content_brief:
        logger.error(f"CPOA: {function_name} - 'title_suggestion' (for content_brief) missing from topic_info for topic_id: {topic_id}.")
        return {"error": "SCA_REQUEST_INVALID", "details": "Missing title_suggestion for content_brief."}

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
        db_path = CPOA_DATABASE_PATH # Use configured DB path
        if not db_path:
            logger.error(f"CPOA: {function_name} - CPOA_DATABASE_PATH not configured. Cannot save snippet to DB.")
        else:
            _save_snippet_to_db(snippet_data, db_path)

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
        return {"error": "SCA_CALL_FAILED_AFTER_RETRIES", "details": error_message}

    except json.JSONDecodeError as e_json:
        error_message = f"SCA service response was not valid JSON for topic_id {topic_id}: {str(e_json)}"
        logger.error(f"CPOA: {function_name} - {error_message}", exc_info=True)
        return {"error": "SCA_RESPONSE_INVALID_JSON", "details": error_message, "raw_response": response.text[:500] if 'response' in locals() and response is not None else "N/A"}

    except Exception as e: # Catch-all for other unexpected errors
        error_message = f"Unexpected error during SCA call for topic_id {topic_id}: {str(e)}"
        logger.error(f"CPOA: {function_name} - {error_message}", exc_info=True)
        return {"error": "SCA_CALL_UNEXPECTED_ERROR", "details": error_message}


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
    
    # Use configured database path
    db_path_main = CPOA_DATABASE_PATH
    logger.info(f"Using database path from environment for __main__: {db_path_main}")

    # Create a dummy podcasts table if it doesn't exist for the db_path_main
    try:
        conn = sqlite3.connect(db_path_main)
        cursor = conn.cursor()
        cursor.execute("""
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
                cpoa_full_orchestration_log TEXT
            )
        """)
        conn.commit()
        logger.info(f"Database '{db_path_main}' initialized for testing in __main__ (using new schema).")
    except sqlite3.Error as e:
        logger.error(f"Error creating DB table in __main__ with new schema: {e}")
    finally:
        if conn:
            conn.close()

    sample_topic_1 = "AI in Healthcare"
    sample_task_id_1 = "task_" + uuid.uuid4().hex
    print(f"\nTest 1: Orchestrating for topic '{sample_topic_1}' (Task ID: {sample_task_id_1})")
    try:
        conn = sqlite3.connect(db_path_main)
        cursor = conn.cursor()
        # Simulate initial record creation by API Gateway (simplified for test)
        cursor.execute(
            "INSERT INTO podcasts (podcast_id, topic, cpoa_status, task_created_timestamp, last_updated_timestamp) VALUES (?, ?, ?, ?, ?)",
            (sample_task_id_1, sample_topic_1, "pending_api_gateway", datetime.now().isoformat(), datetime.now().isoformat())
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Error inserting initial task {sample_task_id_1} in __main__: {e}")
    finally:
        if conn:
            conn.close()

    result1 = orchestrate_podcast_generation(topic=sample_topic_1, task_id=sample_task_id_1, db_path=db_path_main)
    print(f"\n--- Result for '{sample_topic_1}' ---")
    pretty_print_orchestration_result(result1)

    sample_topic_2 = "The Future of Space Travel"
    sample_task_id_2 = "task_" + uuid.uuid4().hex
    print(f"\nTest 2: Orchestrating for topic '{sample_topic_2}' (Task ID: {sample_task_id_2})")
    try:
        conn = sqlite3.connect(db_path_main)
        cursor = conn.cursor()
        # Simulate initial record creation by API Gateway
        cursor.execute(
            "INSERT INTO podcasts (podcast_id, topic, cpoa_status, task_created_timestamp, last_updated_timestamp) VALUES (?, ?, ?, ?, ?)",
            (sample_task_id_2, sample_topic_2, "pending_api_gateway", datetime.now().isoformat(), datetime.now().isoformat())
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Error inserting initial task {sample_task_id_2} in __main__: {e}")
    finally:
        if conn:
            conn.close()

    result2 = orchestrate_podcast_generation(topic=sample_topic_2, task_id=sample_task_id_2, db_path=db_path_main)
    print(f"\n--- Result for '{sample_topic_2}' ---")
    pretty_print_orchestration_result(result2)

    # You might want to keep the db_path_main for inspection after tests, or remove it.
    # try:
    #     os.remove(db_path_main)
    #     logger.info(f"Cleaned up database: {db_path_main}")
    # except OSError as e:
    #     logger.error(f"Error removing database {db_path_main}: {e}")

    print("\n--- CPOA orchestration testing with service calls complete ---")
    print(f"NOTE: Ensure PSWA (URL: {PSWA_SERVICE_URL}), VFA (URL: {VFA_SERVICE_URL}), ASF (Notification URL: {ASF_NOTIFICATION_URL}), and SCA (URL: {SCA_SERVICE_URL}) services are running for these tests to fully succeed.")
    print(f"Database used in __main__: {db_path_main}")

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
    depth_mode: str = "deeper" # Depth mode currently illustrative, not deeply implemented
) -> List[Dict[str, Any]]:
    """
    Orchestrates topic exploration by getting related topics from TDA
    and then generating snippets for them via SCA.
    """
    logger.info(f"Starting topic exploration. Mode: {depth_mode}, Topic ID: {current_topic_id}, Keywords: {keywords}")
    explored_snippets: List[Dict[str, Any]] = []

    query_for_tda = None
    original_topic_title = "original topic"

    if current_topic_id:
        if not CPOA_DATABASE_PATH:
            logger.error("CPOA_DATABASE_PATH not configured. Cannot fetch topic details for exploration.")
            # Depending on desired strictness, could raise error or return empty
            return []

        original_topic = _get_topic_details_from_db(CPOA_DATABASE_PATH, current_topic_id)
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
    return explored_snippets