import logging
import sys
import os
import json # Ensure this is present
import sqlite3 # Add this
from datetime import datetime # Add this
import uuid # Add this
from typing import Optional, Dict, Any, List # Add this

# Ensure the 'aethercast' directory (which is one level up from 'cpoa', 'wcha', etc.)
# is in the Python path. This allows for imports like 'from aethercast.wcha.main import ...'
# when this script might be run from the repo root or from within the cpoa directory.
# Get the directory of the current script (aethercast/cpoa/main.py)
current_script_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory (aethercast/cpoa)
parent_dir = os.path.dirname(current_script_dir)
# Get the grandparent directory (aethercast) - this is what we want for package resolution
# Actually, we need the parent of 'aethercast' directory to be in sys.path
# if 'aethercast' itself is the top-level package.
# If script is run from repo root, 'aethercast' is already findable.
# If script is run from aethercast/cpoa, then '..' (parent_dir) is 'aethercast',
# and '../..' (grandparent_dir) is the repo root.
# Let's add the repo root to sys.path.
repo_root_dir = os.path.dirname(parent_dir) 
if repo_root_dir not in sys.path:
    sys.path.insert(0, repo_root_dir)

# Now the imports should work assuming the simple agent files exist
try:
    from aethercast.wcha.main import get_content_for_topic # Changed import
    from aethercast.pswa.main import weave_script
    from aethercast.vfa.main import forge_voice
    # TODO: Later, consider importing actual error indicators from agent modules if they define them
    # from aethercast.wcha.main import WCHA_ERROR_INDICATORS as WCHA_ERRORS_FROM_MODULE
    CPOA_IMPORTS_SUCCESSFUL = True
    CPOA_MISSING_IMPORT_ERROR = None
except ImportError as e:
    CPOA_IMPORTS_SUCCESSFUL = False
    CPOA_MISSING_IMPORT_ERROR = f"CPOA critical import error: {e}. One or more agent modules (WCHA, PSWA, VFA) are missing or have issues."
    # Define placeholder functions if imports fail, so CPOA can still be 'loaded' by API Gateway
    def get_content_for_topic(topic: str, max_sources: int = 3) -> str:
        return f"Error: WCHA module not loaded. Cannot get content for topic '{topic}'."
    def weave_script(content: str, topic: str) -> str:
        return "Error: PSWA module not loaded. Cannot weave script."
    def forge_voice(script: str, output_filename_prefix: str = "podcast_audio") -> Dict[str, Any]:
        return {"status": "error", "message": "Error: VFA module not loaded. Cannot forge voice.", "audio_filepath": None}

# Global Error Indicator Constants (Fallbacks/Defaults)
# These are used if not overridden by actual imports from agent modules
WCHA_ERROR_INDICATORS = (
    "Error: WCHA module not loaded", 
    "WCHA Error: Necessary web scraping libraries not installed.", 
    "Error during web search", 
    "WCHA: No search results", 
    "WCHA: Failed to harvest usable content",
    "Error fetching URL", # From old harvest_from_url
    "Failed to fetch URL", # From old harvest_from_url
    "No paragraph text found", # From old harvest_from_url
    "Content at URL", # From old harvest_from_url
    "Cannot 'harvest_from_url'", # From old harvest_from_url
    "No pre-defined content found" # From old harvest_content
)
PSWA_ERROR_PREFIXES = (
    "Error: PSWA module not loaded", 
    "OpenAI library not available", 
    "Error: OPENAI_API_KEY", 
    "OpenAI API Error:", 
    "[ERROR] Insufficient content", 
    "An unexpected error occurred during LLM call"
)
# VFA errors are typically handled by its dictionary output's 'status' and 'message' keys.

# --- Logging Configuration ---
# BasicConfig should be called only once. If other modules also call it,
# it might lead to unexpected behavior. For simplicity here, we assume
# CPOA is the primary entry point for this specific execution flow.
# Use a module-specific logger to avoid conflicts if other modules also configure root logger
logger = logging.getLogger(__name__)
if not logger.hasHandlers(): # Avoid adding multiple handlers if script re-run in some contexts
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - CPOA - %(message)s')

# TOPIC_TO_URL_MAP has been removed as WCHA's get_content_for_topic will use web search.

def get_timestamp() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _update_task_status_in_db(db_path: str, task_id: str, new_status: str, error_msg: Optional[str] = None) -> None:
    """Updates the status, last_updated_timestamp, and error_message for a task in the database."""
    logger.info(f"Task {task_id}: Attempting to update DB status to '{new_status}'. Error msg: '{error_msg if error_msg else 'None'}'")
    timestamp = datetime.now().isoformat() # Use current time for DB update
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # This assumes 'podcasts' table has 'podcast_id', 'status', 'last_updated_timestamp', 'error_message'
        # The 'cpoa_details' and 'audio_filepath' are set by the API gateway thread after orchestration completes.
        cursor.execute(
            "UPDATE podcasts SET status = ?, last_updated_timestamp = ?, error_message = ? WHERE podcast_id = ?",
            (new_status, timestamp, error_msg, task_id)
        )
        conn.commit()
        logger.info(f"Task {task_id}: Successfully updated DB status to '{new_status}'.")
    except sqlite3.Error as e:
        logger.error(f"CPOA: Database error for task {task_id} updating to status {new_status}: {type(e).__name__} - {e}")
    except Exception as e:
        logger.error(f"CPOA: Unexpected error in _update_task_status_in_db for task {task_id}: {type(e).__name__} - {e}")
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
    vfa_result_dict: Dict[str, Any] = {"status": "not_run", "message": "VFA not reached.", "audio_filepath": None}
    current_orchestration_stage: str = "initialization"
    # final_cpoa_status and final_error_message will be determined by the outcome of the steps.
    # Initialize them here to ensure they are always defined before the finally block.
    final_cpoa_status: str = "pending" 
    final_error_message: Optional[str] = None

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
    if not CPOA_IMPORTS_SUCCESSFUL:
        critical_error_msg = str(CPOA_MISSING_IMPORT_ERROR) # Should be set if CPOA_IMPORTS_SUCCESSFUL is False
        log_step(f"CPOA critical failure: Missing agent imports. {critical_error_msg}")
        final_cpoa_status = "failed"
        final_error_message = critical_error_msg
        # The _update_task_status_in_db will be called in the 'finally' block
    
    else: # Proceed with orchestration only if imports were successful
        try:
            log_step(f"Orchestration started for topic: '{topic}'")
            current_orchestration_stage = "processing_wcha"
            _update_task_status_in_db(db_path, task_id, current_orchestration_stage, error_msg=None)

            log_step("Calling WCHA (get_content_for_topic)...", data={"topic": topic})
            # Assuming get_content_for_topic is defined and imported
            wcha_output = get_content_for_topic(topic=topic) 
            log_step("WCHA finished.", data={"output_length": len(wcha_output)})

            # Check WCHA_ERROR_INDICATORS (ensure this constant is defined in CPOA, possibly imported or hardcoded)
            if not wcha_output or any(str(wcha_output).startswith(prefix) for prefix in WCHA_ERROR_INDICATORS):
                final_error_message = str(wcha_output) if wcha_output else "WCHA returned no content or an error string."
                log_step(f"WCHA indicated failure: {final_error_message}", data={"wcha_output": wcha_output})
                final_cpoa_status = "failed" 
                # Raise an exception to be caught by the main try-except block, which will update DB.
                raise Exception(f"WCHA critical failure: {final_error_message}")

            current_orchestration_stage = "processing_pswa"
            _update_task_status_in_db(db_path, task_id, current_orchestration_stage, error_msg=None)
            
            log_step("Calling PSWA (weave_script with LLM)...", data={"content_length": len(wcha_output), "topic": topic})
            # Assuming weave_script is defined and imported
            pswa_output = weave_script(content=wcha_output, topic=topic)
            log_step("PSWA finished.", data={"output_length": len(pswa_output)})
            
            # Check PSWA_ERROR_PREFIXES (ensure this constant is defined in CPOA)
            if not pswa_output or any(str(pswa_output).startswith(prefix) for prefix in PSWA_ERROR_PREFIXES):
                final_error_message = str(pswa_output) if pswa_output else "PSWA returned no script or an error string."
                log_step(f"PSWA indicated failure: {final_error_message}", data={"pswa_output": pswa_output})
                final_cpoa_status = "failed"
                raise Exception(f"PSWA critical failure: {final_error_message}")

            current_orchestration_stage = "processing_vfa"
            _update_task_status_in_db(db_path, task_id, current_orchestration_stage, error_msg=None)
            
            log_step("Calling VFA (forge_voice with TTS)...", data={"script_length": len(pswa_output)})
            # Assuming forge_voice is defined and imported
            vfa_result_dict = forge_voice(script=pswa_output)
            log_step("VFA finished.", data=vfa_result_dict)

            vfa_status = vfa_result_dict.get("status")
            if vfa_status == "success":
                final_cpoa_status = "completed"
                final_error_message = None # Clear any previous non-critical error
            elif vfa_status == "skipped":
                final_cpoa_status = "completed_with_warnings"
                final_error_message = vfa_result_dict.get("message", "VFA skipped audio generation.")
            elif vfa_status == "error": 
                final_cpoa_status = "completed_with_errors" 
                final_error_message = vfa_result_dict.get("message", "VFA reported an internal error.")
            else: # Unknown status from VFA or VFA indicated a more critical failure
                final_cpoa_status = "failed"
                final_error_message = f"VFA returned an unknown or failure status: '{vfa_status}'. Details: {vfa_result_dict.get('message')}"
                log_step(f"VFA failure: {final_error_message}", data=vfa_result_dict)
                # For an unknown/critical VFA failure, we might also want to raise an exception if it means subsequent steps are impossible
                # For now, setting status to "failed" and logging is the primary path.

        except Exception as e:
            # This block catches exceptions from agent calls that were not handled by prefix checks (if they raise),
            # or exceptions raised by CPOA itself (like the WCHA/PSWA critical failures above).
            logger.error(f"CPOA: Critical error during orchestration for task {task_id} (at stage '{current_orchestration_stage}'): {type(e).__name__} - {e}", exc_info=True)
            if not final_error_message: # Preserve specific error if already set by a prefix check + raise
                final_error_message = f"Critical orchestration error at {current_orchestration_stage} stage: {type(e).__name__} - {str(e)}"
            log_step(f"Critical orchestration error: {final_error_message}")
            final_cpoa_status = "failed" # Ensure status is 'failed'
            
            # Ensure vfa_result_dict reflects error if exception happened before or during VFA call
            if current_orchestration_stage != "processing_vfa" or vfa_result_dict.get('status') == 'not_run':
                 vfa_result_dict = {"status": "error", "message": final_error_message, "audio_filepath": None}
    
    # This final DB update happens regardless of CPOA_IMPORTS_SUCCESSFUL outcome or try/except block.
    # It ensures the DB reflects the final_cpoa_status determined by the logic above.
    _update_task_status_in_db(db_path, task_id, final_cpoa_status, error_msg=final_error_message)
    log_step(f"Orchestration ended with overall status: {final_cpoa_status}.")

    # The CPOA result dictionary, which will be used by the API Gateway thread
    # to do the *final* update to the cpoa_details column in the database.
    cpoa_final_result = {
        "task_id": task_id,
        "topic": topic,
        "status": final_cpoa_status, 
        "error_message": final_error_message,
        "final_audio_details": vfa_result_dict, 
        "orchestration_log": orchestration_log
    }
    return cpoa_final_result

def pretty_print_orchestration_result(result: dict):
        return {
            "topic": topic, "status": status, "final_audio_details": None,
            "orchestration_log": orchestration_log, "error_message": f"VFA failed: {str(e)}"
        }

    # Determine final overall status if not already set to failed or completed_with_warnings
    if status == "in_progress": # Only change if no intermediate failures/warnings set it
        status = "completed"
        
    log_step(f"Orchestration finished with status: '{status}' for topic: '{topic}'.")
    
    final_result = {
        "topic": topic,
        "status": status,
        "final_audio_details": vfa_result_dict,
        "orchestration_log": orchestration_log
    }
    if status == "failed" and "error_message" not in final_result: # Ensure error message exists if failed
        final_result["error_message"] = "An unspecified error occurred during orchestration."

    return final_result

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
    
    # Example 1: Topic in TOPIC_TO_URL_MAP (e.g., "ai in healthcare")
    # This should attempt live fetching.
    # Ensure 'requests' and 'bs4' are installed if you want this to succeed with live data.
    # pip install requests beautifulsoup4
    topic_live_fetch = "ai in healthcare"
    print(f"\nInitiating orchestration for TOPIC IN URL_MAP: '{topic_live_fetch}'")
    result_live = orchestrate_podcast_generation(topic=topic_live_fetch)
    print(f"\n--- Result for '{topic_live_fetch}' ---")
    pretty_print_orchestration_result(result_live)

    # Example 2: Topic NOT in TOPIC_TO_URL_MAP, but IS in WCHA's SIMULATED_WEB_CONTENT
    # For this, let's assume "space exploration" is NOT in TOPIC_TO_URL_MAP for this test
    # but IS in SIMULATED_WEB_CONTENT.
    # (Current TOPIC_TO_URL_MAP has "space exploration", so let's use a different one or modify map for test)
    # Let's add a mock-only topic to WCHA's SIMULATED_WEB_CONTENT for this test.
    # This would typically be done by editing WCHA's main.py, but for here, imagine it exists:
    # SIMULATED_WEB_CONTENT["quantum computing"] = "Quantum computers use qubits..."
    topic_mock_only = "quantum computing" # Assuming this is NOT in TOPIC_TO_URL_MAP
                                          # but you've ensured it's in WCHA's mock data.
                                          # If not, it will fallback to "No pre-defined content"
    print(f"\nInitiating orchestration for MOCK-ONLY TOPIC: '{topic_mock_only}'")
    result_mock_only = orchestrate_podcast_generation(topic=topic_mock_only)
    print(f"\n--- Result for '{topic_mock_only}' ---")
    pretty_print_orchestration_result(result_mock_only)


    # Example 3: Topic in neither URL_MAP nor WCHA's SIMULATED_WEB_CONTENT
    topic_not_found = "underwater basket weaving"
    print(f"\nInitiating orchestration for UNKNOWN TOPIC: '{topic_not_found}'")
    result_not_found = orchestrate_podcast_generation(topic=topic_not_found)
    print(f"\n--- Result for '{topic_not_found}' ---")
    pretty_print_orchestration_result(result_not_found)
    
    # Example 4: Topic that might fail live fetching (e.g., bad URL in map)
    # To test this, temporarily add a bad URL to TOPIC_TO_URL_MAP for a specific topic
    # For instance: TOPIC_TO_URL_MAP["bad url test"] = "http://thissitedoesnotexistatallforsure.xyz"
    # Then call orchestrate_podcast_generation("bad url test")
    # This will demonstrate the fallback to harvest_content.
    # For now, we rely on the "ai in healthcare" example which might sometimes fail (network etc.)
    # or be blocked, naturally testing the fallback.

    print("\n--- CPOA enhanced orchestration testing complete ---")
