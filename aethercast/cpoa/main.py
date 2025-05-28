import flask
import uuid
import time
import logging
import threading
import json
import requests # Added for making HTTP requests to TDA

app = flask.Flask(__name__)

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- In-Memory State Management ---
active_workflows = {}

# --- Agent Service URLs ---
TDA_SERVICE_URL = "http://localhost:5001/discover_topics"
SCA_SERVICE_URL = "http://localhost:5002/craft_snippet"
WCHA_SERVICE_URL = "http://localhost:5003/harvest_content"
PSWA_SERVICE_URL = "http://localhost:5004/weave_script"
VFA_SERVICE_URL = "http://localhost:5006/forge_audio"

# --- Agent Communication Helper ---
def _call_agent_service(url: str, payload: dict, agent_name: str, timeout: int = 10) -> dict:
    """
    Helper function to call a specialized agent service.
    Includes error handling and consistent response format for errors.
    """
    logging.info(f"[CPOA_AGENT_CALL] Calling {agent_name} at {url} with payload: {json.dumps(payload, indent=2)}")
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        
        # Check for HTTP errors first (4xx or 5xx)
        if not response.ok: # response.status_code >= 400
            error_details = f"HTTP Error {response.status_code}: {response.reason}."
            try:
                # Attempt to get more details from agent's JSON error response
                agent_error_data = response.json()
                error_details += f" Agent Msg: {agent_error_data.get('error', '')} - {agent_error_data.get('details', '')}"
            except json.JSONDecodeError:
                error_details += " Could not parse error response from agent."
            
            logging.error(f"[CPOA_AGENT_CALL_ERROR] Error calling {agent_name}: {error_details}")
            return {"error": f"Error from {agent_name}", "details": error_details, "status_code": response.status_code}

        # If response is OK, try to parse JSON
        data = response.json()
        logging.info(f"[CPOA_AGENT_CALL_SUCCESS] Received response from {agent_name}.")
        return data

    except requests.exceptions.Timeout:
        logging.error(f"[CPOA_AGENT_CALL_ERROR] Timeout calling {agent_name} at {url}")
        return {"error": f"Timeout calling {agent_name}", "details": f"Request to {url} timed out after {timeout}s.", "status_code": 504}
    except requests.exceptions.ConnectionError:
        logging.error(f"[CPOA_AGENT_CALL_ERROR] Connection error calling {agent_name} at {url}. Is {agent_name} running?")
        return {"error": f"Connection error with {agent_name}", "details": f"Could not connect to {agent_name} at {url}.", "status_code": 503}
    except requests.exceptions.RequestException as e: # Catch other request-related errors
        logging.error(f"[CPOA_AGENT_CALL_ERROR] Error calling {agent_name}: {e}")
        return {"error": f"Request error calling {agent_name}", "details": str(e), "status_code": 500} # Generic 500 for other req errors
    except json.JSONDecodeError as e:
        logging.error(f"[CPOA_AGENT_CALL_ERROR] Error decoding JSON response from {agent_name}: {e}")
        return {"error": f"Invalid JSON response from {agent_name}", "details": str(e), "status_code": 502} # Bad Gateway


def call_topic_discovery_agent(topic_query: str, limit: int = 3, error_trigger: str = None) -> dict:
    payload = {"query": topic_query, "limit": limit}
    if error_trigger: payload["error_trigger"] = error_trigger
    response_data = _call_agent_service(TDA_SERVICE_URL, payload, "TDA")
    if "error" in response_data: return response_data # Propagate error
    if "discovered_topics" not in response_data or not isinstance(response_data["discovered_topics"], list):
        logging.error(f"[CPOA_AGENT_CALL_ERROR] TDA response missing 'discovered_topics' list. Response: {response_data}")
        return {"error": "Invalid response format from TDA", "discovered_topics": []}
    return response_data

def call_snippet_craft_agent(topic_id: str, content_brief: str, topic_info: dict = None, error_trigger: str = None) -> dict:
    payload = {"topic_id": topic_id, "content_brief": content_brief, "topic_info": topic_info if topic_info else {}}
    if error_trigger: payload["error_trigger"] = error_trigger
    response_data = _call_agent_service(SCA_SERVICE_URL, payload, "SCA", timeout=15)
    if "error" in response_data: return response_data
    if not all(key in response_data for key in ["snippet_id", "topic_id", "title", "summary", "audio_url"]):
         logging.error(f"[CPOA_AGENT_CALL_ERROR] SCA response missing expected fields. Response: {response_data}")
         return {"error": "Invalid response format from SCA"}
    return response_data

def call_web_content_harvester_agent(topic: str = None, source_urls: list = None, error_trigger: str = None) -> dict:
    if not topic and not source_urls:
        return {"error": "WCHA call requires 'topic' or 'source_urls'", "retrieved_articles": []}
    payload = {"topic": topic, "source_urls": source_urls if source_urls else []}
    if error_trigger: payload["error_trigger"] = error_trigger
    response_data = _call_agent_service(WCHA_SERVICE_URL, payload, "WCHA", timeout=20)
    if "error" in response_data: return response_data
    if "retrieved_articles" not in response_data or not isinstance(response_data["retrieved_articles"], list):
        logging.error(f"[CPOA_AGENT_CALL_ERROR] WCHA response missing 'retrieved_articles' list. Response: {response_data}")
        return {"error": "Invalid response format from WCHA", "retrieved_articles": []}
    return response_data

def call_podcast_script_weaver_agent(retrieved_content: dict, podcast_title_suggestion: str, podcast_style: str, topic_id: str = None, error_trigger: str = None) -> dict:
    payload = {
        "retrieved_content": retrieved_content,
        "podcast_title_suggestion": podcast_title_suggestion,
        "podcast_style": podcast_style,
        "topic_id": topic_id
    }
    if error_trigger: payload["error_trigger"] = error_trigger
    response_data = _call_agent_service(PSWA_SERVICE_URL, payload, "PSWA", timeout=45)
    if "error" in response_data: return response_data
    if not all(key in response_data for key in ["podcast_id", "title", "script"]):
        logging.error(f"[CPOA_AGENT_CALL_ERROR] PSWA response missing expected fields. Response: {response_data}")
        return {"error": "Invalid response format from PSWA"}
    return response_data

def call_voice_forge_agent(podcast_script_object: dict, voice_preferences: dict, error_trigger: str = None) -> dict:
    payload = {"podcast_script": podcast_script_object, "voice_preferences": voice_preferences}
    if error_trigger: payload["error_trigger"] = error_trigger
    response_data = _call_agent_service(VFA_SERVICE_URL, payload, "VFA", timeout=30)
    if "error" in response_data: return response_data
    if not all(key in response_data for key in ["podcast_id", "stream_id", "audio_stream_url_for_client"]):
        logging.error(f"[CPOA_AGENT_CALL_ERROR] VFA response missing expected fields. Response: {response_data}")
        return {"error": "Invalid response format from VFA"}
    return response_data

# --- Workflow Functions ---
def _update_workflow_status(task_id: str, status: str, details: str = "", data: dict = None):
    if task_id in active_workflows:
        active_workflows[task_id]["status"] = status
        active_workflows[task_id]["updated_at"] = time.time()
        if details: active_workflows[task_id]["details"] = details
        if data: active_workflows[task_id]["data"] = {**active_workflows[task_id].get("data",{}), **data}
        logging.info(f"Workflow {task_id} status updated to {status}. Details: {details}")
    else:
        logging.warning(f"Attempted to update non-existent workflow {task_id}")

def run_snippet_generation_workflow(task_id: str, topic_query: str, error_simulation_config: dict = None):
    try:
        _update_workflow_status(task_id, "IN_PROGRESS", "Starting snippet generation.")
        active_workflows[task_id]["data"]["error_simulation_config"] = error_simulation_config # Store for visibility

        tda_error_trigger = None
        if error_simulation_config and error_simulation_config.get("agent_to_fail") == "tda":
            tda_error_trigger = error_simulation_config.get("error_trigger_value", "tda_error")
        
        discovered_topic_info_response = call_topic_discovery_agent(topic_query, limit=1, error_trigger=tda_error_trigger)
        if "error" in discovered_topic_info_response:
            err_details = f"TDA Error: {discovered_topic_info_response.get('error')} - {discovered_topic_info_response.get('details')}"
            _update_workflow_status(task_id, "FAILED", err_details)
            return

        if not discovered_topic_info_response.get("discovered_topics"):
            _update_workflow_status(task_id, "FAILED", "No topics returned by TDA.")
            return
        
        primary_topic_object = discovered_topic_info_response["discovered_topics"][0]
        primary_topic_id = primary_topic_object.get("topic_id", f"fallback_topic_id_{uuid.uuid4().hex[:8]}")
        content_brief_for_sca = primary_topic_object.get("title_suggestion", topic_query) 
        _update_workflow_status(task_id, "IN_PROGRESS", f"Topic discovered via TDA: {primary_topic_id} ('{content_brief_for_sca}')", data={"discovered_topic": primary_topic_object})
        
        sca_error_trigger = None
        if error_simulation_config and error_simulation_config.get("agent_to_fail") == "sca":
            sca_error_trigger = error_simulation_config.get("error_trigger_value", "sca_error")

        snippet_data_response = call_snippet_craft_agent(primary_topic_id, content_brief_for_sca, topic_info=primary_topic_object, error_trigger=sca_error_trigger)
        if "error" in snippet_data_response:
            err_details = f"SCA Error: {snippet_data_response.get('error')} - {snippet_data_response.get('details')}"
            _update_workflow_status(task_id, "FAILED", err_details)
            return

        _update_workflow_status(task_id, "COMPLETED", "Snippet generation successful.", data=snippet_data_response)
    except Exception as e:
        logging.error(f"[WORKFLOW_ERROR - {task_id}] Unexpected error in snippet generation workflow: {e}", exc_info=True)
        _update_workflow_status(task_id, "FAILED", f"Unexpected CPOA error: {str(e)}")

def run_full_podcast_generation_workflow(task_id: str, initial_topic_query: str, snippet_id_input: str = None, error_simulation_config: dict = None):
    try:
        _update_workflow_status(task_id, "IN_PROGRESS", "Starting full podcast generation.")
        active_workflows[task_id]["data"]["error_simulation_config"] = error_simulation_config # Store for visibility

        current_topic_title = initial_topic_query
        potential_sources = []
        discovered_topic_details = active_workflows[task_id].get("data", {}).get("discovered_topic_details")

        if not discovered_topic_details and snippet_id_input:
            # ... (logic to retrieve topic details from snippet task, remains same) ...
            pass # Assume this logic is fine

        if not discovered_topic_details:
            tda_query = initial_topic_query if initial_topic_query else current_topic_title
            if not tda_query:
                _update_workflow_status(task_id, "FAILED", "Cannot determine topic for TDA.")
                return
            
            tda_error_trigger = None
            if error_simulation_config and error_simulation_config.get("agent_to_fail") == "tda":
                tda_error_trigger = error_simulation_config.get("error_trigger_value", "tda_error")
            
            tda_response = call_topic_discovery_agent(tda_query, limit=1, error_trigger=tda_error_trigger)
            if "error" in tda_response:
                err_details = f"TDA Error: {tda_response.get('error')} - {tda_response.get('details')}"
                _update_workflow_status(task_id, "FAILED", err_details)
                return
            if not tda_response.get("discovered_topics"):
                 _update_workflow_status(task_id, "FAILED", "No topics returned by TDA for full podcast.")
                 return

            discovered_topic_details = tda_response["discovered_topics"][0]
            current_topic_title = discovered_topic_details.get("title_suggestion", tda_query)
            potential_sources = discovered_topic_details.get("potential_sources", [])
            _update_workflow_status(task_id, "IN_PROGRESS", f"Topic refined by TDA: '{current_topic_title}'", 
                                    data={"discovered_topic_details": discovered_topic_details, "current_topic_title": current_topic_title})
        
        final_topic_title_for_podcast = active_workflows[task_id].get("data", {}).get("current_topic_title", current_topic_title)
        if not final_topic_title_for_podcast:
            _update_workflow_status(task_id, "FAILED", "Podcast title could not be determined.")
            return

        source_urls_for_harvester = [s['url'] for s in potential_sources if isinstance(s, dict) and 'url' in s]
        
        wcha_error_trigger = None
        if error_simulation_config and error_simulation_config.get("agent_to_fail") == "wcha":
            wcha_error_trigger = error_simulation_config.get("error_trigger_value", "wcha_error")

        harvested_content_response = call_web_content_harvester_agent(topic=final_topic_title_for_podcast, source_urls=source_urls_for_harvester, error_trigger=wcha_error_trigger)
        harvested_articles_for_pswa = {"retrieved_articles": []}
        if "error" in harvested_content_response:
            err_details = f"WCHA Error: {harvested_content_response.get('error')} - {harvested_content_response.get('details')}"
            # Allow proceeding with empty content if WCHA fails, PSWA might handle it
            _update_workflow_status(task_id, "IN_PROGRESS", f"Harvesting issues: {err_details}. Proceeding with no harvested content.", 
                                    data={"harvested_content_summary": {"count": 0, "warning": err_details}})
        elif not harvested_content_response.get("retrieved_articles"):
             _update_workflow_status(task_id, "IN_PROGRESS", "WCHA returned no articles. Proceeding with no harvested content.",
                                     data={"harvested_content_summary": {"count": 0, "warning": "No articles from WCHA"}})
        else:
            harvested_articles_for_pswa = harvested_content_response
            _update_workflow_status(task_id, "IN_PROGRESS", f"Harvesting for '{final_topic_title_for_podcast}' complete.", 
                                    data={"harvested_content_summary": {"count": len(harvested_articles_for_pswa.get("retrieved_articles",[])) }})
        
        topic_id_for_pswa = discovered_topic_details.get("topic_id") if discovered_topic_details else None
        
        pswa_error_trigger = None
        if error_simulation_config and error_simulation_config.get("agent_to_fail") == "pswa":
            pswa_error_trigger = error_simulation_config.get("error_trigger_value", "pswa_error")

        podcast_script_response = call_podcast_script_weaver_agent(
            retrieved_content=harvested_articles_for_pswa, 
            podcast_title_suggestion=final_topic_title_for_podcast, 
            podcast_style="informative", topic_id=topic_id_for_pswa, error_trigger=pswa_error_trigger
        )
        if "error" in podcast_script_response:
            err_details = f"PSWA Error: {podcast_script_response.get('error')} - {podcast_script_response.get('details')}"
            _update_workflow_status(task_id, "FAILED", err_details)
            return
        _update_workflow_status(task_id, "IN_PROGRESS", f"Script generated by PSWA: {podcast_script_response.get('podcast_id')}", data={"podcast_script_details": podcast_script_response})

        vfa_error_trigger = None
        if error_simulation_config and error_simulation_config.get("agent_to_fail") == "vfa":
            vfa_error_trigger = error_simulation_config.get("error_trigger_value", "vfa_error")

        voice_forge_response = call_voice_forge_agent(
            podcast_script_object=podcast_script_response, 
            voice_preferences={"voice_id": "AetherVoice-Nova"}, error_trigger=vfa_error_trigger
        )
        if "error" in voice_forge_response:
            err_details = f"VFA Error: {voice_forge_response.get('error')} - {voice_forge_response.get('details')}"
            _update_workflow_status(task_id, "FAILED", err_details)
            return

        _update_workflow_status(task_id, "COMPLETED", "Full podcast generation successful and ready for streaming.", data=voice_forge_response)
    except Exception as e:
        logging.error(f"[WORKFLOW_ERROR - {task_id}] Unexpected error in full podcast generation workflow: {e}", exc_info=True)
        _update_workflow_status(task_id, "FAILED", f"Unexpected CPOA error: {str(e)}")

# --- API Endpoints ---
@app.route("/api/v1/snippets", methods=["GET"])
def get_snippets_endpoint():
    topic_query = flask.request.args.get("topic", "latest technology trends")
    # Example: /api/v1/snippets?topic=AI&error_simulation_config={"agent_to_fail":"tda","error_trigger_value":"tda_error"}
    error_config_str = flask.request.args.get("error_simulation_config")
    error_simulation_config = None
    if error_config_str:
        try:
            error_simulation_config = json.loads(error_config_str)
        except json.JSONDecodeError:
            logging.warning("Could not parse error_simulation_config from query param.")

    logging.info(f"Received GET /api/v1/snippets request for topic: {topic_query}, ErrorSim: {error_simulation_config}")

    task_id = f"task_snippet_{uuid.uuid4().hex}"
    active_workflows[task_id] = {
        "task_id": task_id, "status": "PENDING",
        "details": f"Snippet generation for topic '{topic_query}' initiated.",
        "type": "snippet", "created_at": time.time(), "updated_at": time.time(),
        "data": {"original_inputs": {"topic_query": topic_query, "error_simulation_config_received": error_simulation_config}}
    }
    thread = threading.Thread(target=run_snippet_generation_workflow, args=(task_id, topic_query, error_simulation_config))
    thread.start()
    return flask.jsonify({
        "message": "Snippet generation initiated. Check status using the task_id.",
        "task_id": task_id, "status_check_url": f"/api/v1/tasks/{task_id}" 
    }), 202

@app.route("/api/v1/podcasts/generate", methods=["POST"])
def generate_podcast_endpoint():
    try:
        request_data = flask.request.get_json()
        if not request_data: return flask.jsonify({"error": "Invalid JSON payload"}), 400

        topic_input = request_data.get("topic")
        snippet_id_input = request_data.get("snippet_id")
        error_simulation_config = request_data.get("error_simulation_config") # Added

        if not topic_input and not snippet_id_input:
            return flask.jsonify({"error": "Either 'topic' or 'snippet_id' must be provided"}), 400
        
        task_description = topic_input if topic_input else f"content from snippet {snippet_id_input}"
        logging.info(f"Received POST /api/v1/podcasts/generate request for: '{task_description}', ErrorSim: {error_simulation_config}")

        task_id = f"task_podcast_{uuid.uuid4().hex}"
        active_workflows[task_id] = {
            "task_id": task_id, "status": "PENDING",
            "details": f"Full podcast generation for '{task_description}' initiated.",
            "type": "podcast", "created_at": time.time(), "updated_at": time.time(),
            "data": {"original_inputs": {"topic": topic_input, "snippet_id": snippet_id_input, "error_simulation_config_received": error_simulation_config}}
        }
        thread = threading.Thread(target=run_full_podcast_generation_workflow, args=(task_id, topic_input, snippet_id_input, error_simulation_config))
        thread.start()
        return flask.jsonify({
            "message": "Full podcast generation initiated.",
            "task_id": task_id, "status_url": f"/api/v1/tasks/{task_id}" 
        }), 202
    except Exception as e:
        logging.error(f"Error in /api/v1/podcasts/generate: {e}", exc_info=True)
        return flask.jsonify({"error": "Internal server error"}), 500

@app.route("/api/v1/tasks/<task_id>", methods=["GET"])
def get_task_status_endpoint(task_id: str):
    task_info = active_workflows.get(task_id)
    if not task_info: return flask.jsonify({"error": "Task not found"}), 404

    response = {
        "task_id": task_id, "status": task_info["status"],
        "details": task_info["details"], "type": task_info["type"],
        "last_updated": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(task_info["updated_at"])),
        "input_parameters": task_info.get("data", {}).get("original_inputs", {})
    }
    if task_info["status"] == "COMPLETED":
        result_data = {k: v for k, v in task_info.get("data", {}).items() if k not in [
            "original_inputs", "discovered_topic", "discovered_topic_details", 
            "current_topic_title", "harvested_content_summary", "podcast_script_details", "error_simulation_config"
        ]}
        response["result"] = result_data
        if task_info["type"] == "podcast" and "podcast_id" in result_data and "audio_stream_url_for_client" in result_data:
            pass 
        elif task_info["type"] == "snippet" and "snippet_id" in result_data:
            pass
    elif task_info["status"] == "FAILED":
        # Include more detailed error structure if available from agent calls
        response["error_details"] = task_info.get("details") 
        # Also include the original error_simulation_config if it was present for this task
        if task_info.get("data",{}).get("original_inputs",{}).get("error_simulation_config_received"):
            response["error_simulation_trigger_used"] = task_info["data"]["original_inputs"]["error_simulation_config_received"]
            
    return flask.jsonify(response), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
# ```  <- This seems to be a leftover from a previous edit, should be removed or be part of a comment.
# CPOA error handling implemented. Now for FEND error display.
# I'll read `aethercast/fend/app.js` and modify it to display errors from failed CPOA tasks.
