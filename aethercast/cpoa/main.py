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
from aethercast.wcha.main import harvest_content
from aethercast.pswa.main import weave_script
from aethercast.vfa.main import forge_voice

# --- Logging Configuration ---
# BasicConfig should be called only once. If other modules also call it,
# it might lead to unexpected behavior. For simplicity here, we assume
# CPOA is the primary entry point for this specific execution flow.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - CPOA - %(message)s')

def get_timestamp() -> str:
    return datetime.utcnow().isoformat() + "Z"

def orchestrate_podcast_generation(topic: str) -> dict:
    """
    Orchestrates the podcast generation by calling WCHA, PSWA, and VFA in sequence.
    """
    orchestration_log = []
    status = "pending" # Initial status

    def log_step(message: str, data: any = None):
        timestamped_message = f"[{get_timestamp()}] {message}"
        logging.info(timestamped_message)
        log_entry = {"timestamp": get_timestamp(), "message": message}
        if data is not None:
            # Attempt to serialize data if it's complex, else convert to string
            try:
                log_entry["data"] = json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data)
            except TypeError: # Handle non-serializable data gracefully
                log_entry["data"] = str(data)
        orchestration_log.append(log_entry)

    log_step(f"Orchestration started for topic: '{topic}'.")
    status = "in_progress"

    # 1. Call WebContentHarvesterAgent (WCHA)
    wcha_output = None
    try:
        log_step(f"Calling WCHA: harvest_content with topic '{topic}'.")
        wcha_output = harvest_content(topic=topic)
        log_step("WCHA: harvest_content returned.", data=wcha_output)
        if not isinstance(wcha_output, str) or not wcha_output or wcha_output.startswith("No pre-defined content found"):
            log_step(f"WCHA Warning: Content might be missing or is a 'not found' message. Content: '{wcha_output}'")
            # Decide if this is a failure or if PSWA can handle it
            # For now, let PSWA try
    except Exception as e:
        log_step(f"WCHA: Error during harvest_content: {str(e)}", data={"error_type": type(e).__name__})
        status = "failed"
        return {
            "topic": topic, "status": status, "final_audio_details": None,
            "orchestration_log": orchestration_log, "error_message": f"WCHA failed: {str(e)}"
        }

    # 2. Call PodcastScriptWeaverAgent (PSWA)
    pswa_output = None
    try:
        log_step(f"Calling PSWA: weave_script with content from WCHA and topic '{topic}'.")
        # Ensure wcha_output is a string, even if it was None or some error message from WCHA
        content_for_pswa = wcha_output if isinstance(wcha_output, str) else "Content unavailable due to previous step issues."
        pswa_output = weave_script(content=content_for_pswa, topic=topic)
        log_step("PSWA: weave_script returned.", data=pswa_output)
        if not isinstance(pswa_output, str) or not pswa_output:
            log_step("PSWA Warning: Generated script is empty.")
            # This could be a failure point depending on requirements
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
        # Ensure pswa_output is a string
        script_for_vfa = pswa_output if isinstance(pswa_output, str) else "Script unavailable due to previous step issues."
        vfa_result_dict = forge_voice(script=script_for_vfa)
        log_step("VFA: forge_voice returned.", data=vfa_result_dict)
        if not isinstance(vfa_result_dict, dict) or vfa_result_dict.get("status") != "success":
            log_step(f"VFA Warning: Voice forging may have failed or been skipped. Status: {vfa_result_dict.get('status') if isinstance(vfa_result_dict, dict) else 'N/A'}")
            # This might not be a hard failure if "skipped" is acceptable for short scripts
            if isinstance(vfa_result_dict, dict) and vfa_result_dict.get("status") == "skipped":
                 status = "completed_with_warnings" # Or a more specific status
            # else: # if it's an actual error or unexpected response
            # status = "failed" 
            # return { ... }
    except Exception as e:
        log_step(f"VFA: Error during forge_voice: {str(e)}", data={"error_type": type(e).__name__})
        status = "failed"
        return {
            "topic": topic, "status": status, "final_audio_details": None,
            "orchestration_log": orchestration_log, "error_message": f"VFA failed: {str(e)}"
        }

    if status not in ["failed", "completed_with_warnings"]:
        status = "completed"
    log_step(f"Orchestration {status} for topic: '{topic}'.")
    
    return {
        "topic": topic,
        "status": status,
        "final_audio_details": vfa_result_dict,
        "orchestration_log": orchestration_log
    }

if __name__ == "__main__":
    print("--- CPOA: Testing Basic Podcast Orchestration ---")
    
    # Topic that should have mock data in WCHA
    # (as per previous subtask: "ai in healthcare", "space exploration", "climate change")
    sample_topic = "ai in healthcare" 
    print(f"\nInitiating orchestration for topic: '{sample_topic}'")
    
    orchestration_result = orchestrate_podcast_generation(topic=sample_topic)
    
    print("\n--- Orchestration Result ---")
    # Pretty-print the JSON output
    # For the log, we want to see the actual data, not JSON strings within JSON strings
    # So, we'll parse the 'data' field in the log if it's a stringified JSON
    parsed_log = []
    if orchestration_result and "orchestration_log" in orchestration_result:
        for entry in orchestration_result["orchestration_log"]:
            parsed_entry = entry.copy()
            if "data" in parsed_entry and isinstance(parsed_entry["data"], str):
                try:
                    # Attempt to parse if it's a JSON string
                    parsed_entry["data"] = json.loads(parsed_entry["data"]) 
                except json.JSONDecodeError:
                    # If not a valid JSON string, keep it as is
                    pass 
            parsed_log.append(parsed_entry)
        orchestration_result["orchestration_log"] = parsed_log

    print(json.dumps(orchestration_result, indent=2))

    # Test with a topic that might not exist in WCHA's mock data
    sample_topic_2 = "philosophy of potatoes"
    print(f"\nInitiating orchestration for topic: '{sample_topic_2}'")
    orchestration_result_2 = orchestrate_podcast_generation(topic=sample_topic_2)
    
    print("\n--- Orchestration Result for Unknown Topic ---")
    parsed_log_2 = []
    if orchestration_result_2 and "orchestration_log" in orchestration_result_2:
        for entry in orchestration_result_2["orchestration_log"]:
            parsed_entry = entry.copy()
            if "data" in parsed_entry and isinstance(parsed_entry["data"], str):
                try:
                    parsed_entry["data"] = json.loads(parsed_entry["data"])
                except json.JSONDecodeError:
                    pass
            parsed_log_2.append(parsed_entry)
        orchestration_result_2["orchestration_log"] = parsed_log_2
    print(json.dumps(orchestration_result_2, indent=2))

    print("\n--- CPOA basic orchestration testing complete ---")
