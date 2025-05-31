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
CPOA_SERVICE_RETRY_COUNT = int(os.getenv("CPOA_SERVICE_RETRY_COUNT", "3")) # Added
CPOA_SERVICE_RETRY_BACKOFF_FACTOR = float(os.getenv("CPOA_SERVICE_RETRY_BACKOFF_FACTOR", "0.5")) # Added

# --- Logging Configuration ---
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - CPOA - %(message)s')

# Log loaded configuration
logger.info("--- CPOA Configuration ---")
logger.info(f"PSWA_SERVICE_URL: {PSWA_SERVICE_URL}")
logger.info(f"VFA_SERVICE_URL: {VFA_SERVICE_URL}")
logger.info(f"ASF_NOTIFICATION_URL: {ASF_NOTIFICATION_URL}")
logger.info(f"ASF_WEBSOCKET_BASE_URL: {ASF_WEBSOCKET_BASE_URL}")
logger.info(f"SCA_SERVICE_URL: {SCA_SERVICE_URL}")
logger.info(f"CPOA_DATABASE_PATH: {CPOA_DATABASE_PATH}")
logger.info(f"CPOA_SERVICE_RETRY_COUNT: {CPOA_SERVICE_RETRY_COUNT}") # Added
logger.info(f"CPOA_SERVICE_RETRY_BACKOFF_FACTOR: {CPOA_SERVICE_RETRY_BACKOFF_FACTOR}") # Added
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

def orchestrate_podcast_generation(topic: str, task_id: str, db_path: str) -> Dict[str, Any]:
    """
    Orchestrates the podcast generation by calling WCHA, PSWA, and VFA in sequence.
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

    def log_step(message: str, data: Optional[Dict[str, Any]] = None) -> None:
        timestamp = datetime.now().isoformat()
        log_entry: Dict[str, Any] = {"timestamp": timestamp, "message": message}
        log_data_str = "N/A"
        if data is not None:
            try:
                log_data_str = json.dumps(data) 
            except TypeError: 
                try:
                    log_data_str = str(data)
                except Exception:
                    log_data_str = "Data could not be serialized or converted to string"
        
        preview_limit = 500  # To prevent excessively long log lines
        log_entry["data_preview"] = log_data_str[:preview_limit] + "..." if len(log_data_str) > preview_limit else log_data_str
        
        orchestration_log.append(log_entry)
        # Also log to the main CPOA logger for real-time visibility if needed
        logger.info(f"Task {task_id} @ {current_orchestration_stage}: {message} - Data Preview: {log_entry['data_preview']}")

    # Check for import issues first
    if not WCHA_IMPORT_SUCCESSFUL: # Changed from CPOA_IMPORTS_SUCCESSFUL for clarity if WCHA is the only direct import concern
        current_orchestration_stage = "initialization_failure"
        critical_error_msg = str(WCHA_MISSING_IMPORT_ERROR if WCHA_MISSING_IMPORT_ERROR else "WCHA module import error not specified.")
        log_step(f"CPOA critical failure: WCHA module import error. {critical_error_msg}")
        final_cpoa_status = "failed_wcha_module_error" # Specific status
        final_error_message = critical_error_msg
        # The _update_task_status_in_db will be called in the 'finally' block
    
    else: # Proceed with orchestration only if imports were successful
        try:
            log_step(f"Orchestration started for topic: '{topic}'")
            current_orchestration_stage = "wcha_content_retrieval" # More specific stage
            _update_task_status_in_db(db_path, task_id, current_orchestration_stage, error_msg=None)

            log_step("Calling WCHA (get_content_for_topic)...", data={"topic": topic})
            wcha_output = get_content_for_topic(topic=topic) 
            log_step("WCHA finished.", data={"output_length": len(wcha_output)})

            if not wcha_output or any(str(wcha_output).startswith(prefix) for prefix in WCHA_ERROR_INDICATORS):
                final_error_message = str(wcha_output) if wcha_output else "WCHA returned no content or an error string."
                log_step(f"WCHA content retrieval failure: {final_error_message}", data={"wcha_output": wcha_output})
                final_cpoa_status = "failed_wcha_content_harvest" # Specific status
                raise Exception(f"WCHA critical failure: {final_error_message}")

            current_orchestration_stage = "pswa_script_generation" # More specific stage
            _update_task_status_in_db(db_path, task_id, current_orchestration_stage, error_msg=None)

            log_step("Calling PSWA Service (weave_script)...", data={"url": PSWA_SERVICE_URL, "topic": topic, "content_length": len(wcha_output)})
            try:
                pswa_payload = {"content": wcha_output, "topic": topic}
                response = requests_with_retry("post", PSWA_SERVICE_URL,
                                               max_retries=retry_count, backoff_factor=backoff_factor,
                                               json=pswa_payload, timeout=180) # Increased timeout for LLM
                # response.raise_for_status() is handled by requests_with_retry

                # PSWA now returns a structured script object.
                # A 200 OK from PSWA (ensured by requests_with_retry) means a valid structured script.
                # If PSWA had an internal error (like parsing LLM output or insufficient content),
                # it should have returned a non-200 code, which requests_with_retry would raise as HTTPError.
                structured_script_from_pswa = response.json()
                log_step("PSWA Service finished successfully. Received structured script.",
                         data={"script_id": structured_script_from_pswa.get("script_id"),
                               "title": structured_script_from_pswa.get("title")})

                # Validate essential parts of the structured script from PSWA
                if not isinstance(structured_script_from_pswa, dict) or \
                   not structured_script_from_pswa.get("script_id") or \
                   not structured_script_from_pswa.get("title") or \
                   not isinstance(structured_script_from_pswa.get("segments"), list): # Segments should be a list (can be empty)
                    final_error_message = "PSWA service returned invalid or malformed structured script (missing script_id, title, or segments list)."
                    log_step(final_error_message, data=structured_script_from_pswa)
                    final_cpoa_status = "failed_pswa_bad_script_structure"
                    raise Exception(f"PSWA critical failure: {final_error_message}")

                # VFA has been updated to expect this structured_script_from_pswa dictionary
                # in the 'script' field of its payload.

            except requests.exceptions.RequestException as e_req: # Includes HTTPError from requests_with_retry if max retries failed for 5xx or non-retryable 4xx
                pswa_err_payload_str = "N/A"
                status_code_str = "N/A"
                if hasattr(e_req, 'response') and e_req.response is not None:
                    status_code_str = str(e_req.response.status_code)
                    try:
                        pswa_err_payload_str = json.dumps(e_req.response.json())
                    except json.JSONDecodeError:
                        pswa_err_payload_str = e_req.response.text[:500]

                final_error_message = f"PSWA service call failed after retries (HTTP status: {status_code_str}, type: {type(e_req).__name__}): {str(e_req)}. Response payload: {pswa_err_payload_str}"
                log_step(f"PSWA service request exception after retries: {final_error_message}", data={"error": str(e_req), "response_payload": pswa_err_payload_str})
                final_cpoa_status = "failed_pswa_request_exception" # Specific status
                raise Exception(final_error_message) # Re-raise to be caught by outer try-except
            except json.JSONDecodeError as e_json: # If PSWA returns non-JSON on success status (unlikely if service is well-behaved)
                final_error_message = f"PSWA service response was not valid JSON despite success status: {str(e_json)}"
                log_step(f"PSWA service JSON decode error: {final_error_message}", data={"response_text": response.text[:500] if 'response' in locals() and response is not None else "N/A"})
                final_cpoa_status = "failed_pswa_json_decode" # Specific status
                raise Exception(final_error_message)

            current_orchestration_stage = "vfa_audio_generation" # More specific stage
            _update_task_status_in_db(db_path, task_id, current_orchestration_stage, error_msg=None)

            log_step("Calling VFA Service (forge_voice)...",
                     data={"url": VFA_SERVICE_URL, "script_id": structured_script_from_pswa.get("script_id")})
            try:
                # VFA expects the entire structured script from PSWA in its "script" field
                vfa_payload = {"script": structured_script_from_pswa}
                response = requests_with_retry("post", VFA_SERVICE_URL,
                                               max_retries=retry_count, backoff_factor=backoff_factor,
                                               json=vfa_payload, timeout=90)
                vfa_result_dict = response.json() # VFA service returns the dict directly
                log_step("VFA Service finished.", data=vfa_result_dict)

            except requests.exceptions.RequestException as e_req_vfa: # Includes HTTPError
                vfa_err_payload_str = "N/A"
                status_code_str = "N/A"
                if hasattr(e_req_vfa, 'response') and e_req_vfa.response is not None:
                    status_code_str = str(e_req_vfa.response.status_code)
                    try:
                        vfa_err_payload_str = json.dumps(e_req_vfa.response.json().get("message", e_req_vfa.response.json()))
                    except json.JSONDecodeError:
                         vfa_err_payload_str = e_req_vfa.response.text[:500]

                final_error_message = f"VFA service call failed after retries (HTTP status: {status_code_str}, type: {type(e_req_vfa).__name__}): {str(e_req_vfa)}. Response payload: {vfa_err_payload_str}"
                log_step(f"VFA service request exception after retries: {final_error_message}", data={"error": str(e_req_vfa), "response_payload": vfa_err_payload_str})
                vfa_result_dict = {"status": "error", "message": final_error_message, "audio_filepath": None} # Ensure vfa_result_dict is updated
                final_cpoa_status = "failed_vfa_request_exception" # Specific status
                raise Exception(final_error_message)
            except json.JSONDecodeError as e_json_vfa: # If VFA returns non-JSON on success
                final_error_message = f"VFA service response was not valid JSON despite success status: {str(e_json_vfa)}"
                log_step(f"VFA service JSON decode error: {final_error_message}", data={"response_text": response.text[:500] if 'response' in locals() and response is not None else "N/A"})
                vfa_result_dict = {"status": "error", "message": final_error_message, "audio_filepath": None} # Ensure vfa_result_dict is updated
                final_cpoa_status = "failed_vfa_json_decode" # Specific status
                raise Exception(final_error_message)

            vfa_status = vfa_result_dict.get("status")
            if vfa_status == "success":
                final_cpoa_status = "completed" # Base success status
                final_error_message = None # Clear previous step errors if VFA succeeded

                audio_filepath = vfa_result_dict.get("audio_filepath")
                stream_id = vfa_result_dict.get("stream_id")

                if audio_filepath and stream_id:
                    current_orchestration_stage = "asf_notification" # More specific stage
                    log_step("Notifying ASF about new audio...", data={"url": ASF_NOTIFICATION_URL, "stream_id": stream_id, "filepath": audio_filepath})
                    try:
                        asf_payload = {"stream_id": stream_id, "filepath": audio_filepath}
                        asf_response = requests_with_retry("post", ASF_NOTIFICATION_URL,
                                                           max_retries=retry_count, backoff_factor=backoff_factor,
                                                           json=asf_payload, timeout=10)
                        asf_notification_status_message = f"ASF notified successfully for stream {stream_id}."
                        log_step(asf_notification_status_message, data=asf_response.json())
                        # final_cpoa_status remains "completed"
                    except requests.exceptions.RequestException as e_asf_req: # Includes HTTPError
                        asf_err_payload_str = "N/A"
                        status_code_str = "N/A"
                        if hasattr(e_asf_req, 'response') and e_asf_req.response is not None:
                            status_code_str = str(e_asf_req.response.status_code)
                            try:
                                asf_err_payload_str = json.dumps(e_asf_req.response.json().get("error", e_asf_req.response.json()))
                            except json.JSONDecodeError:
                                asf_err_payload_str = e_asf_req.response.text[:200]

                        asf_notification_status_message = (
                            f"ASF notification failed after retries (HTTP status: {status_code_str}, type: {type(e_asf_req).__name__}): {str(e_asf_req)}. Response: {asf_err_payload_str}"
                        )
                        log_step(asf_notification_status_message, data={"error": str(e_asf_req), "response_payload": asf_err_payload_str})
                        final_error_message = asf_notification_status_message # Store this as the primary error if main task was ok
                        final_cpoa_status = "completed_with_asf_notification_failure" # Specific status
                    except json.JSONDecodeError as e_asf_json: # Should not happen if ASF is well-behaved
                        asf_notification_status_message = f"ASF notification response was not valid JSON: {str(e_asf_json)}"
                        log_step(asf_notification_status_message, data={"response_text": asf_response.text[:500] if 'asf_response' in locals() and asf_response is not None else "N/A"})
                        final_error_message = asf_notification_status_message
                        final_cpoa_status = "completed_with_asf_notification_failure_json_decode"
                else: # audio_filepath or stream_id missing from VFA success response
                    asf_notification_status_message = "ASF notification skipped: audio_filepath or stream_id missing from VFA success response."
                    log_step(asf_notification_status_message, data=vfa_result_dict)
                    final_error_message = asf_notification_status_message
                    final_cpoa_status = "completed_with_vfa_data_missing" # More specific status

            elif vfa_status == "skipped":
                final_cpoa_status = "completed_with_vfa_skipped" # Specific status
                final_error_message = vfa_result_dict.get("message", "VFA skipped audio generation.")
            elif vfa_status == "error": # VFA itself reported an error in its JSON response
                final_cpoa_status = "failed_vfa_reported_error" # Specific status
                final_error_message = vfa_result_dict.get("message", "VFA reported an internal error.")
            else: # Unknown status from VFA
                final_cpoa_status = "failed_vfa_unknown_status" # Specific status
                final_error_message = f"VFA service returned an unknown status: '{vfa_status}'. Details: {vfa_result_dict.get('message')}"
                log_step(f"VFA unknown status: {final_error_message}", data=vfa_result_dict)

        except Exception as e: # Outer exception handler for WCHA, PSWA, VFA stages
            logger.error(f"CPOA: Orchestration failed for task {task_id} at stage '{current_orchestration_stage}': {type(e).__name__} - {e}", exc_info=True)
            if not final_error_message: # Ensure an error message is set
                final_error_message = f"Orchestration error at {current_orchestration_stage}: {type(e).__name__} - {str(e)}"

            # Update status based on stage if not already a specific failure status
            if final_cpoa_status == "pending" or not final_cpoa_status.startswith("failed_"):
                if current_orchestration_stage == "wcha_content_retrieval":
                    final_cpoa_status = "failed_wcha_exception"
                elif current_orchestration_stage == "pswa_script_generation":
                    final_cpoa_status = "failed_pswa_exception"
                elif current_orchestration_stage == "vfa_audio_generation":
                    final_cpoa_status = "failed_vfa_exception"
                else:
                    final_cpoa_status = "failed_unknown_stage_exception"
            log_step(f"Orchestration critical error: {final_error_message}")
            
            # Ensure vfa_result_dict reflects error if exception occurred before or during VFA, and not caught by VFA specific try-except
            if vfa_result_dict.get('status') == 'not_run' and current_orchestration_stage != "vfa_audio_generation":
                 vfa_result_dict = {"status": "error", "message": final_error_message, "audio_filepath": None, "stream_id": None}
            elif current_orchestration_stage == "vfa_audio_generation" and vfa_result_dict.get('status') != 'error': # If in VFA stage but no specific VFA error caught
                 vfa_result_dict = {"status": "error", "message": final_error_message, "audio_filepath": None, "stream_id": None}

    # This final DB update by CPOA sets its final assessment of cpoa_status and cpoa_error_message.
    # The API Gateway will then do one more update for fields like final_audio_filepath, etc.
    _update_task_status_in_db(db_path, task_id, final_cpoa_status, error_msg=final_error_message)
    log_step(f"Orchestration ended with CPOA's final status: {final_cpoa_status}. Error (if any): {final_error_message}")

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
    return cpoa_final_result

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
        # response.raise_for_status() # Handled by requests_with_retry

        snippet_data = response.json()
        logger.info(f"CPOA: {function_name} - SCA Service call successful for topic_id {topic_id}. Snippet data received.")
        return snippet_data

    except requests.exceptions.RequestException as e_req: # Includes HTTPError from requests_with_retry
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
