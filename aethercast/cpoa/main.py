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
    try:
        log_step(f"Calling PSWA: weave_script with content from WCHA and topic '{topic}'.")
        content_for_pswa = wcha_output if isinstance(wcha_output, str) else "Content unavailable due to previous WCHA issues."
        pswa_output = weave_script(content=content_for_pswa, topic=topic)
        log_step("PSWA: weave_script returned.", data={"script_snippet": pswa_output[:200] + "..." if pswa_output else "N/A"}) # Log snippet
        if not isinstance(pswa_output, str) or not pswa_output:
            log_step("PSWA Warning: Generated script is empty or not a string.")
            # Depending on requirements, this might be a failure point.
            # For now, we'll let it proceed to VFA, which should handle empty scripts.
    except Exception as e:
        log_step(f"PSWA: Error during weave_script: {str(e)}", data={"error_type": type(e).__name__})
        status = "failed"
        return {
            "topic": topic, "status": status, "final_audio_details": None,
            "orchestration_log": orchestration_log, "error_message": f"PSWA failed: {str(e)}"
        }

    # 3. Call VoiceForgeAgent (VFA)
    vfa_result_dict = None
    try:
        log_step("Calling VFA: forge_voice with script from PSWA.")
        script_for_vfa = pswa_output if isinstance(pswa_output, str) else "Script unavailable due to previous PSWA issues."
        vfa_result_dict = forge_voice(script=script_for_vfa)
        log_step("VFA: forge_voice returned.", data=vfa_result_dict)
        
        if not isinstance(vfa_result_dict, dict) or "status" not in vfa_result_dict:
             log_step(f"VFA Warning: Unexpected response format from VFA. Response: {vfa_result_dict}")
             status = "failed" # Or completed_with_warnings if some audio info is salvageable
             # Potentially return here if VFA output is critical and malformed
        elif vfa_result_dict.get("status") != "success":
            log_step(f"VFA Info: Voice forging was not fully successful. Status: {vfa_result_dict.get('status')}", data=vfa_result_dict)
            if vfa_result_dict.get("status") == "skipped":
                 status = "completed_with_warnings" 
            # else: # Other non-success statuses might be considered errors
            #     status = "failed" 
            #     return { ... } 
    except Exception as e:
        log_step(f"VFA: Error during forge_voice: {str(e)}", data={"error_type": type(e).__name__})
        status = "failed"
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
