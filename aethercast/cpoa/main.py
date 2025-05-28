import sys
import os
import json
import logging
from datetime import datetime

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
from aethercast.wcha.main import harvest_content, harvest_from_url # Updated import
from aethercast.pswa.main import weave_script
from aethercast.vfa.main import forge_voice

# --- Logging Configuration ---
# BasicConfig should be called only once. If other modules also call it,
# it might lead to unexpected behavior. For simplicity here, we assume
# CPOA is the primary entry point for this specific execution flow.
# Use a module-specific logger to avoid conflicts if other modules also configure root logger
logger = logging.getLogger(__name__)
if not logger.hasHandlers(): # Avoid adding multiple handlers if script re-run in some contexts
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - CPOA - %(message)s')


# --- Topic to URL Mapping ---
TOPIC_TO_URL_MAP = {
    "ai in healthcare": "https://en.wikipedia.org/wiki/Artificial_intelligence_in_healthcare",
    "space exploration": "https://en.wikipedia.org/wiki/Space_exploration",
    # "climate change" is in WCHA's mock data, but we can choose to map it or not.
    # Let's map it to test live fetching for it.
    "climate change": "https://en.wikipedia.org/wiki/Climate_change",
    # Example of a topic that will NOT be in this map, but IS in WCHA mock data:
    # "quantum computing" (assuming it's added to WCHA SIMULATED_WEB_CONTENT for testing)
}

def get_timestamp() -> str:
    return datetime.utcnow().isoformat() + "Z"

def orchestrate_podcast_generation(topic: str) -> dict:
    """
    Orchestrates the podcast generation by calling WCHA, PSWA, and VFA in sequence.
    Uses live web harvesting if topic is mapped, otherwise uses mock data.
    """
    orchestration_log = []
    status = "pending" # Initial status

    def log_step(message: str, data: any = None):
        timestamped_message = f"[{get_timestamp()}] {message}"
        logger.info(timestamped_message) # Use module-specific logger
        log_entry = {"timestamp": get_timestamp(), "message": message}
        if data is not None:
            try:
                log_entry["data"] = json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data)
            except TypeError: 
                log_entry["data"] = str(data)
        orchestration_log.append(log_entry)

    log_step(f"Orchestration started for topic: '{topic}'.")
    status = "in_progress"

    # 1. Call WebContentHarvesterAgent (WCHA) with new logic
    wcha_output = None
    normalized_topic = topic.lower().strip() if topic else ""
    
    try:
        if normalized_topic in TOPIC_TO_URL_MAP:
            mapped_url = TOPIC_TO_URL_MAP[normalized_topic]
            log_step(f"Topic '{normalized_topic}' found in URL map. Attempting live harvest from: {mapped_url}")
            wcha_output = harvest_from_url(mapped_url)
            log_step(f"WCHA: harvest_from_url for '{mapped_url}' returned.", data=wcha_output)

            # Content Validation & Fallback
            # Check for common error indicators or empty content from harvest_from_url
            is_error_or_empty = not wcha_output or \
                                wcha_output.startswith("Error fetching URL") or \
                                wcha_output.startswith("Failed to fetch URL") or \
                                wcha_output.startswith("No paragraph text found") or \
                                wcha_output.startswith("Content at URL") or \
                                wcha_output.startswith("Cannot 'harvest_from_url'")

            if is_error_or_empty:
                log_step(f"WCHA: Live harvest from '{mapped_url}' failed or returned no meaningful content. Falling back to mock harvest for topic '{topic}'.", data={"reason": wcha_output})
                wcha_output = harvest_content(topic=topic) # Fallback call
                log_step(f"WCHA: Fallback harvest_content for '{topic}' returned.", data=wcha_output)
            else:
                log_step(f"WCHA: Successfully harvested content from URL '{mapped_url}'.")
        else:
            log_step(f"Topic '{normalized_topic}' not in URL map. Using mock harvest for topic '{topic}'.")
            wcha_output = harvest_content(topic=topic)
            log_step(f"WCHA: Mock harvest_content for '{topic}' returned.", data=wcha_output)

        # Additional check for "No pre-defined content found" from mock harvest, if it was used
        if isinstance(wcha_output, str) and wcha_output.startswith("No pre-defined content found"):
            log_step(f"WCHA Warning: Final content is a 'not found' message. Content: '{wcha_output}'")
            # PSWA will handle this by using its placeholder.

    except Exception as e:
        log_step(f"WCHA: Critical error during content harvesting: {str(e)}", data={"error_type": type(e).__name__})
        status = "failed"
        return {
            "topic": topic, "status": status, "final_audio_details": None,
            "orchestration_log": orchestration_log, "error_message": f"WCHA failed critically: {str(e)}"
        }

    # 2. Call PodcastScriptWeaverAgent (PSWA)
    pswa_output = None
    # Define known PSWA error prefixes/patterns
    PSWA_ERROR_PREFIXES = [
        "OpenAI library not available",
        "Error: OPENAI_API_KEY environment variable",
        "OpenAI API Error:",
        "An unexpected error occurred during LLM call:",
        "[ERROR] Insufficient content provided" # Error from the LLM itself via prompt instruction
    ]

    try:
        content_for_pswa = wcha_output if isinstance(wcha_output, str) else "Content unavailable due to previous WCHA issues."
        log_step(f"Calling PSWA (LLM): weave_script for topic '{topic}'. Content length: {len(content_for_pswa)} chars.", 
                 data={"topic": topic, "content_preview": content_for_pswa[:100] + "..."})
        
        pswa_output = weave_script(content=content_for_pswa, topic=topic)
        
        pswa_output_stripped = pswa_output.strip() if pswa_output else ""
        is_pswa_error = False
        if not pswa_output_stripped:
            is_pswa_error = True
            log_step("PSWA (LLM) Warning: Returned empty script.", data=pswa_output)
        else:
            for prefix in PSWA_ERROR_PREFIXES:
                if pswa_output_stripped.startswith(prefix):
                    is_pswa_error = True
                    log_step("PSWA (LLM) indicated an error or failed to generate a script.", data=pswa_output)
                    break
        
        if not is_pswa_error:
            log_step("PSWA (LLM) successfully generated script.", data={"script_snippet": pswa_output_stripped[:200] + "..."})

    except Exception as e:
        log_step(f"PSWA: Critical error during weave_script (LLM call): {str(e)}", data={"error_type": type(e).__name__})
        status = "failed"
        return {
            "topic": topic, "status": status, "final_audio_details": None,
            "orchestration_log": orchestration_log, "error_message": f"PSWA failed: {str(e)}"
        }

    # 3. Call VoiceForgeAgent (VFA)
    vfa_result_dict = None
    # This variable will help distinguish between exceptions and VFA-reported issues
    error_occurred_during_orchestration = False 
    current_error_message = None

    try:
        # ... (WCHA and PSWA calls remain the same, they will set status="failed" and return if an exception occurs) ...
        # If WCHA or PSWA failed by exception, status is already "failed".
        # We only proceed to VFA if previous steps didn't set status to "failed".
        if status == "failed": # If WCHA or PSWA hard failed by exception
            # This return is already handled in their respective except blocks,
            # but as a safeguard if logic changes.
            # The final_result construction at the end will use the status and error_message.
            pass # Let it fall through to the final result construction.

        log_step("Calling VFA (Google Cloud TTS): forge_voice with script from PSWA.")
        script_for_vfa = pswa_output if isinstance(pswa_output, str) else "Script unavailable due to previous PSWA issues."
        vfa_result_dict = forge_voice(script=script_for_vfa)
        
        # Enhanced logging for VFA's output
        vfa_status = vfa_result_dict.get('status', 'unknown')
        vfa_message = vfa_result_dict.get('message', 'No message from VFA.')
        
        log_data_for_vfa_step = {
            "vfa_status": vfa_status,
            "vfa_message": vfa_message,
            "engine_used": vfa_result_dict.get("engine_used"),
            "script_char_count": vfa_result_dict.get("script_char_count")
        }

        if vfa_status == "success":
            log_data_for_vfa_step["audio_filepath"] = vfa_result_dict.get("audio_filepath")
            log_data_for_vfa_step["audio_format"] = vfa_result_dict.get("audio_format")
            log_step("VFA (Google Cloud TTS) successfully generated audio.", data=log_data_for_vfa_step)
            # status remains "in_progress" to be set to "completed" finally
        elif vfa_status == "skipped":
            log_step("VFA (Google Cloud TTS) skipped audio generation.", data=log_data_for_vfa_step)
            status = "completed_with_warnings" # Overall CPOA status
            current_error_message = vfa_message # VFA's reason for skipping becomes the primary message
        elif vfa_status == "error":
            log_step("VFA (Google Cloud TTS) encountered an error during synthesis.", data=log_data_for_vfa_step)
            status = "completed_with_errors" # VFA completed its process but reported an internal error
            current_error_message = vfa_message # VFA's error message
        else: # Unexpected VFA status or malformed dict
            log_step(f"VFA (Google Cloud TTS) returned an unexpected status or malformed response: {vfa_status}", data=vfa_result_dict)
            status = "failed" # Treat unexpected VFA response as a CPOA failure
            current_error_message = f"VFA returned unexpected status: {vfa_status}. Full response: {vfa_result_dict}"

    except Exception as e:
        log_step(f"VFA: Critical error during forge_voice (Google Cloud TTS call): {str(e)}", data={"error_type": type(e).__name__})
        status = "failed" # CPOA status
        current_error_message = f"VFA failed critically: {str(e)}"
        error_occurred_during_orchestration = True # To ensure this overrides any VFA dict status later

    # Refined final status determination
    # This logic is now effectively integrated into the VFA try-except block's status setting.
    # The 'status' variable holds the most current state.
    # If an exception occurred in WCHA/PSWA, 'status' would already be 'failed'.
    # If VFA call had an exception, 'status' is 'failed', 'error_occurred_during_orchestration' is True.
    # If VFA returned 'error', 'status' is 'completed_with_errors'.
    # If VFA returned 'skipped', 'status' is 'completed_with_warnings'.
    # If VFA returned 'success' and no prior agent failed, 'status' is 'in_progress' here.

    final_status_to_set = status
    final_error_message = current_error_message

    if error_occurred_during_orchestration: # An agent call raised an exception
        final_status_to_set = "failed"
        # final_error_message would have been set by the except block that set error_occurred_during_orchestration
        # If it's from VFA's critical error, current_error_message already has it.
        # If it's from WCHA/PSWA, their return statements would have exited early with "failed" status and message.
        # This path is mainly for VFA critical failure.
    elif final_status_to_set == "in_progress": # No exceptions, VFA was successful
        final_status_to_set = "completed"
        final_error_message = None # Clear any previous non-critical message if all good
    
    log_step(f"Orchestration finished with status: '{final_status_to_set}' for topic: '{topic}'.")
    
    final_result = {
        "topic": topic,
        "status": final_status_to_set,
        "final_audio_details": vfa_result_dict, # This will be None if VFA was never called or errored before returning dict
        "orchestration_log": orchestration_log
    }
    if final_error_message:
        final_result["error_message"] = final_error_message
    elif final_status_to_set == "failed" and "error_message" not in final_result:
        final_result["error_message"] = "An unspecified error occurred during orchestration."

    return final_result

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
