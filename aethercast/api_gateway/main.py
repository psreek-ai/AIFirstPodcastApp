import sys
import os
from dotenv import load_dotenv # Added
from flask import Flask, jsonify, request, send_file, send_from_directory # Added send_from_directory
import uuid 
import sqlite3
from datetime import datetime # Added
import json # Added
import requests # Added for TDA calls

# --- Path Setup for CPOA Import ---
# Add project root to sys.path to allow imports from aethercast.cpoa
# current_dir is aethercast/api_gateway/
current_dir = os.path.dirname(os.path.abspath(__file__))
# parent_dir is aethercast/
parent_dir = os.path.dirname(current_dir)
# project_root is the directory containing aethercast/
project_root = os.path.dirname(parent_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv() # Added

# --- Service URLs ---
TDA_SERVICE_URL = os.getenv("TDA_SERVICE_URL", "http://localhost:5000/discover_topics") # Assuming TDA is on 5000 as per typical setup.


# --- Database Configuration ---
DATABASE_FILE = os.getenv("DATABASE_FILE", "aethercast_podcasts.db") # Will be created in the same dir as this script (api_gateway)
                                         # For production, choose a more persistent location.

DB_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS podcasts (
    podcast_id TEXT PRIMARY KEY,
    audio_filepath TEXT NOT NULL,
    topic TEXT NOT NULL,
    generation_timestamp TEXT NOT NULL,
    cpoa_details TEXT 
);
"""

# --- Database Helper Functions ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row # Allows accessing columns by name
    return conn

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.executescript(DB_SCHEMA_SQL) # Use executescript for multi-statement SQL
        conn.commit()
        app.logger.info("Database initialized successfully.") # Use app.logger if available
    except sqlite3.Error as e:
        # Use app.logger if available, otherwise print
        log_func = app.logger.error if hasattr(app, 'logger') else print
        log_func(f"Database initialization error: {e}")
    finally:
        if conn:
            conn.close()


# --- Attempt CPOA Import ---
cpoa_podcast_func_imported = False
cpoa_snippet_func_imported = False
CPOA_IMPORT_ERROR_MESSAGE = ""
try:
    from aethercast.cpoa.main import orchestrate_podcast_generation, orchestrate_snippet_generation
    cpoa_podcast_func_imported = True
    cpoa_snippet_func_imported = True
except ImportError as e:
    CPOA_IMPORT_ERROR_MESSAGE = str(e)
    # Try importing individually if combined fails, to see which one is the issue
    if not cpoa_podcast_func_imported:
        try:
            from aethercast.cpoa.main import orchestrate_podcast_generation
            cpoa_podcast_func_imported = True
        except ImportError as e_pod:
            CPOA_IMPORT_ERROR_MESSAGE += f" orchestrate_podcast_generation: {e_pod};"
            log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print # Ensure logger for this context
            log_func(f"Error importing CPOA orchestrate_podcast_generation: {e_pod}")
            def orchestrate_podcast_generation(*args, **kwargs): # Placeholder
                raise ImportError(f"CPOA's orchestrate_podcast_generation could not be imported: {CPOA_IMPORT_ERROR_MESSAGE}")

    if not cpoa_snippet_func_imported:
        try:
            from aethercast.cpoa.main import orchestrate_snippet_generation
            cpoa_snippet_func_imported = True
        except ImportError as e_snip:
            CPOA_IMPORT_ERROR_MESSAGE += f" orchestrate_snippet_generation: {e_snip};"
            log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print # Ensure logger for this context
            log_func(f"Error importing CPOA orchestrate_snippet_generation: {e_snip}")
            def orchestrate_snippet_generation(*args, **kwargs): # Placeholder
                raise ImportError(f"CPOA's orchestrate_snippet_generation could not be imported: {CPOA_IMPORT_ERROR_MESSAGE}")

    if not cpoa_podcast_func_imported or not cpoa_snippet_func_imported:
         log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
         log_func(f"Overall CPOA import status: podcast_func={cpoa_podcast_func_imported}, snippet_func={cpoa_snippet_func_imported}. Errors: {CPOA_IMPORT_ERROR_MESSAGE}")


# --- Flask App Initialization ---
app = Flask(__name__)

# Configure basic logging for Flask app if not already configured by Flask/debug mode
# This logging setup is fine, or Flask's default logger can be used via app.logger
if not app.debug and not app.logger.handlers: # Check if handlers are already added
    import logging
    # Ensure logging is configured before init_db might try to use app.logger
    # For simplicity, if flask's app.logger is not yet fully configured, print will be used in init_db
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - API_GW - %(message)s')

# Log loaded configuration after app is initialized, so app.logger is available
with app.app_context():
    app.logger.info("--- API Gateway Configuration ---")
    app.logger.info(f"TDA_SERVICE_URL: {TDA_SERVICE_URL}")
    app.logger.info(f"DATABASE_FILE: {DATABASE_FILE}")
    # FEND_DIR is derived, but we can log its final value
    app.logger.info(f"FEND_DIR: {FEND_DIR}")
    app.logger.info("--- End API Gateway Configuration ---")

# --- Frontend Directory Path ---
# Assuming this main.py is in aethercast/api_gateway/
# FEND_DIR should point to aethercast/fend/
FEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fend'))


# --- Remove Global Storage PODCAST_FILE_MAP ---
# PODCAST_FILE_MAP = {} # This line is now removed.

# --- Static Frontend File Serving ---
@app.route('/')
def serve_index():
    app.logger.info(f"Serving index.html from: {FEND_DIR}")
    return send_from_directory(FEND_DIR, 'index.html')

@app.route('/style.css')
def serve_style():
    app.logger.info(f"Serving style.css from: {FEND_DIR}")
    return send_from_directory(FEND_DIR, 'style.css')

@app.route('/app.js')
def serve_script():
    app.logger.info(f"Serving app.js from: {FEND_DIR}")
    return send_from_directory(FEND_DIR, 'app.js')

# --- Health Check Endpoint ---
@app.route('/health', methods=['GET'])
def health_check():
    podcast_func_status = "successfully imported" if cpoa_podcast_func_imported else f"failed to import ({CPOA_IMPORT_ERROR_MESSAGE})"
    snippet_func_status = "successfully imported" if cpoa_snippet_func_imported else f"failed to import ({CPOA_IMPORT_ERROR_MESSAGE})"
    db_status_message = "Database connection successful."
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1 FROM podcasts LIMIT 1;") # Try a simple query
        conn.close()
    except sqlite3.Error as e:
        db_status_message = f"Database connection error: {e}"
        app.logger.error(f"Health check DB error: {e}")

    return jsonify({
        "status": "API Gateway is healthy",
        "cpoa_podcast_function_status": podcast_func_status,
        "cpoa_snippet_function_status": snippet_func_status,
        "cpoa_podcast_func_imported": cpoa_podcast_func_imported,
        "cpoa_snippet_func_imported": cpoa_snippet_func_imported,
        "database_status": db_status_message
    }), 200

# --- Snippets Endpoint ---
@app.route('/api/v1/snippets', methods=['GET'])
def get_dynamic_snippets():
    app.logger.info("Request received for /api/v1/snippets")

    if not cpoa_snippet_func_imported:
        app.logger.error("CPOA snippet generation function not loaded. Cannot process snippet generation.")
        return jsonify({"error": "Service Unavailable", "message": f"Core snippet orchestration module not loaded. Import error: {CPOA_IMPORT_ERROR_MESSAGE}"}), 503

    # Call TDA to get topics
    topics_from_tda = []
    try:
        app.logger.info(f"Calling TDA service at {TDA_SERVICE_URL} to discover topics.")
        tda_payload = {"limit": request.args.get('limit', 5, type=int)} # Allow limit override via query param
        tda_response = requests.post(TDA_SERVICE_URL, json=tda_payload, timeout=30)
        tda_response.raise_for_status() # Check for HTTP errors

        tda_data = tda_response.json()
        topics_from_tda = tda_data.get("topics", []) # Assuming TDA returns {"topics": [...]}

        if not topics_from_tda:
            app.logger.warning("TDA service returned no topics.")
            return jsonify({"message": "No topics available from TDA to generate snippets.", "snippets": []}), 200

        app.logger.info(f"Received {len(topics_from_tda)} topics from TDA.")

    except requests.exceptions.HTTPError as e_http:
        error_details = str(e_http)
        if e_http.response is not None:
            try:
                error_payload = e_http.response.json()
                error_details = error_payload.get("error", error_details) if isinstance(error_payload, dict) else error_details
            except json.JSONDecodeError:
                error_details = e_http.response.text[:200]
        app.logger.error(f"TDA service call failed (HTTP error): {error_details}", exc_info=True)
        return jsonify({"error": "Service Unavailable", "message": f"Failed to connect to Topic Discovery Agent: {error_details}"}), 503
    except requests.exceptions.RequestException as e_req:
        app.logger.error(f"TDA service call failed (network/request error): {e_req}", exc_info=True)
        return jsonify({"error": "Service Unavailable", "message": f"Topic Discovery Agent is unreachable: {e_req}"}), 503
    except json.JSONDecodeError as e_json:
        app.logger.error(f"TDA service response was not valid JSON: {e_json}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "Invalid response from Topic Discovery Agent."}), 500
    except Exception as e_gen: # Catch-all for other unexpected TDA call errors
        app.logger.error(f"Unexpected error calling TDA service: {e_gen}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": f"An unexpected error occurred while fetching topics: {e_gen}"}), 500

    generated_snippets = []
    for topic_obj in topics_from_tda:
        # Adapt TDA's TopicObject to CPOA's expected topic_info structure if needed.
        # Assuming TDA's TopicObject fields are directly usable or CPOA can handle them.
        # Specifically, CPOA's orchestrate_snippet_generation expects 'topic_id' and 'title_suggestion'.
        # Let's assume TDA provides 'id' and 'title'.
        topic_info_for_cpoa = {
            "topic_id": topic_obj.get("id") or topic_obj.get("topic_id"), # Adapt based on TDA's actual output
            "title_suggestion": topic_obj.get("title") or topic_obj.get("title_suggestion"), # Adapt
            "summary": topic_obj.get("summary"),
            "keywords": topic_obj.get("keywords", [])
            # Pass other fields from topic_obj if they exist and SCA might use them
        }
        # Ensure essential fields for CPOA are present
        if not topic_info_for_cpoa["title_suggestion"]:
            app.logger.warning(f"Skipping snippet generation for topic from TDA due to missing title: {topic_obj}")
            continue

        app.logger.info(f"Requesting snippet generation from CPOA for topic: {topic_info_for_cpoa.get('title_suggestion')}")
        try:
            snippet_result = orchestrate_snippet_generation(topic_info=topic_info_for_cpoa)
            if snippet_result and "error" not in snippet_result:
                generated_snippets.append(snippet_result)
                app.logger.info(f"Snippet generated successfully for topic: {topic_info_for_cpoa.get('title_suggestion')}")
            else:
                app.logger.error(f"Snippet generation failed for topic '{topic_info_for_cpoa.get('title_suggestion')}': {snippet_result.get('details', 'Unknown CPOA error')}")
        except Exception as e_cpoa_snip:
            app.logger.error(f"Unexpected error calling CPOA orchestrate_snippet_generation for topic '{topic_info_for_cpoa.get('title_suggestion')}': {e_cpoa_snip}", exc_info=True)
            # Continue to next topic

    if not generated_snippets:
        app.logger.info("No snippets were successfully generated for the discovered topics.")
        return jsonify({"message": "No snippets generated for the available topics.", "snippets": []}), 200

    app.logger.info(f"Successfully generated {len(generated_snippets)} snippets.")
    return jsonify({"snippets": generated_snippets}), 200


# --- Podcast Generation Endpoint ---
@app.route('/api/v1/podcasts', methods=['POST'])
def create_podcast_generation_task():
    data = request.get_json()

    if not data or 'topic' not in data or not data['topic']:
        app.logger.warning("Bad request to /api/v1/podcasts: Missing or empty 'topic'.")
        return jsonify({"error": "Bad Request", "message": "Missing or empty 'topic' in request body."}), 400
    
    topic = data['topic'] # This is just a string for now.
                         # Consider if this should be a more structured topic_info object in the future.
    app.logger.info(f"Received podcast generation request for topic string: '{topic}'")

    if not cpoa_podcast_func_imported: # Check specific function import
        app.logger.error("CPOA podcast generation function not loaded. Cannot process podcast generation.")
        return jsonify({"error": "Service Unavailable", "message": f"Core podcast orchestration module (podcast func) not loaded. Import error: {CPOA_IMPORT_ERROR_MESSAGE}"}), 503

    try:
        # For full podcast generation, CPOA's orchestrate_podcast_generation currently expects a simple topic string.
        # If it were to expect a topic_info object similar to snippet generation, this would need adjustment.
        app.logger.info(f"Invoking CPOA orchestrate_podcast_generation for topic: '{topic}'")
        cpoa_result = orchestrate_podcast_generation(topic=topic)
        app.logger.info(f"CPOA returned for topic '{topic}'. Status: {cpoa_result.get('status')}")
        
        generation_status = cpoa_result.get("status")
        final_audio_details = cpoa_result.get("final_audio_details", {}) 
        audio_filepath = final_audio_details.get("audio_filepath") if final_audio_details else None

        if generation_status == "completed" and audio_filepath:
            podcast_id = str(uuid.uuid4())
            timestamp = datetime.now().isoformat()
            cpoa_details_json = json.dumps(cpoa_result) # Store the whole CPOA result as JSON

            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO podcasts (podcast_id, audio_filepath, topic, generation_timestamp, cpoa_details) VALUES (?, ?, ?, ?, ?)",
                    (podcast_id, audio_filepath, topic, timestamp, cpoa_details_json)
                )
                conn.commit()
                app.logger.info(f"Podcast {podcast_id} metadata saved to DB for topic '{topic}'. File at: {audio_filepath}")
            except sqlite3.Error as e:
                app.logger.error(f"Database error saving podcast metadata for topic '{topic}': {e}", exc_info=True)
                # CPOA ran, audio was generated, but DB save failed. This is a server-side issue.
                return jsonify({"error": "Database Error", "message": "Failed to record podcast metadata after successful generation."}), 500
            finally:
                if conn:
                    conn.close()
            
            response_data = {
                "podcast_id": podcast_id,
                "topic": topic,
                "generation_status": generation_status,
                "audio_url": f"/api/v1/podcasts/{podcast_id}/audio.mp3",
                "message": "Podcast generated successfully and metadata saved.",
                "details": cpoa_result 
            }
            return jsonify(response_data), 201 
        
        elif generation_status in ["completed_with_warnings", "completed_with_errors"] or \
             (generation_status == "completed" and not audio_filepath):
            log_message = (
                f"Podcast generation for topic '{topic}' completed with issues (no audio or warnings/errors from VFA). "
                f"Status: {generation_status}. Audio filepath: {audio_filepath}. "
                f"CPOA details: {cpoa_result.get('error_message', 'No specific error message from CPOA.')}"
            )
            app.logger.warning(log_message)
            response_data = {
                "topic": topic,
                "generation_status": generation_status,
                "message": cpoa_result.get("error_message") or "Podcast generation completed but no usable audio was produced or an issue occurred.",
                "details": cpoa_result
            }
            return jsonify(response_data), 200 
        
        else: # Covers CPOA "failed" status or other unexpected CPOA outcomes
            app.logger.error(f"Podcast generation failed for topic '{topic}'. CPOA status: {generation_status}. CPOA details: {cpoa_result}")
            response_data = {
                "topic": topic,
                "generation_status": generation_status or "unknown_cpoa_failure",
                "message": cpoa_result.get("error_message") or "Podcast generation failed at CPOA level.",
                "details": cpoa_result
            }
            return jsonify(response_data), 500 
            
    except ImportError as ie: 
        app.logger.error(f"CPOA function unavailable during request: {ie}")
        return jsonify({"error": "Service Unavailable", "message": "Core podcast orchestration module is not available."}), 503
    except Exception as e:
        app.logger.error(f"Unexpected error during CPOA orchestration for topic '{topic}': {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred during podcast generation."}), 500

# --- Serve Podcast Audio Endpoint ---
@app.route('/api/v1/podcasts/<string:podcast_id>/audio.mp3', methods=['GET'])
def serve_podcast_audio(podcast_id: str):
    app.logger.info(f"Request received to serve audio for podcast_id: {podcast_id}")
    
    audio_filepath = None
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT audio_filepath FROM podcasts WHERE podcast_id = ?", (podcast_id,))
        db_record = cursor.fetchone()
        
        if db_record and db_record["audio_filepath"]:
            audio_filepath = db_record["audio_filepath"]
        else:
            app.logger.warning(f"Podcast ID '{podcast_id}' not found in database or audio_filepath is null.")
            return jsonify({"error": "Not Found", "message": "Invalid or expired podcast_id."}), 404
            
    except sqlite3.Error as e:
        app.logger.error(f"Database error retrieving audio_filepath for podcast_id '{podcast_id}': {e}", exc_info=True)
        return jsonify({"error": "Database Error", "message": "Error retrieving podcast metadata."}), 500
    finally:
        if conn:
            conn.close()

    # audio_filepath should be set if we reach here from the try block successfully
    if not os.path.exists(audio_filepath): # This check is crucial
        app.logger.error(f"Audio file for podcast_id '{podcast_id}' not found at expected path: {audio_filepath}")
        # Note: No PODCAST_FILE_MAP.pop here as we are using DB.
        # A more advanced system might flag this record in the DB as having a missing file.
        return jsonify({"error": "Not Found", "message": "Audio file missing or no longer available."}), 404

    # Security: Validate that the resolved path is within an expected directory.
    # For now, SHARED_AUDIO_DIR is in VFA, not directly known here without import/config.
    # Assuming audio_filepath from DB is trusted to be from the correct shared location.
    # A better approach would be to store relative paths or IDs and resolve them against a configured base path here.

    try:
        app.logger.info(f"Attempting to send file: {audio_filepath} for podcast_id: {podcast_id}")
        # send_file needs absolute path. Assume audio_filepath from DB is absolute.
        # If it's relative, it needs to be resolved to an absolute path.
        # Example: audio_filepath = os.path.join(app.config['SHARED_AUDIO_BASE_PATH'], audio_filepath)
        return send_file(audio_filepath, mimetype='audio/mpeg')
    except Exception as e:
        app.logger.error(f"Error sending file for podcast_id '{podcast_id}': {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An error occurred while trying to serve the audio file."}), 500


# --- Main Block ---
if __name__ == '__main__':
    init_db() # Initialize the database and create tables if they don't exist

    # Set a default TDA port if necessary for local testing.
    # This is just for the print statement, actual TDA_SERVICE_URL is used by the endpoint.
    tda_port_for_message = TDA_SERVICE_URL.split(':')[-1].split('/')[0] if "://" in TDA_SERVICE_URL else "5000 (default)"

    print(f"Starting Aethercast API Gateway on http://0.0.0.0:5001")
    print(f"Ensure other Aethercast services are running:")
    print(f" - CPOA (used internally, no direct URL needed from Gateway's perspective for startup)")
    print(f" - TDA at {TDA_SERVICE_URL} (typically on port {tda_port_for_message})")
    print(f" - SCA at {os.getenv('SCA_SERVICE_URL', 'http://localhost:5002/craft_snippet')}")
    print(f" - PSWA at {os.getenv('PSWA_SERVICE_URL', 'http://localhost:5004/weave_script')}")
    print(f" - VFA at {os.getenv('VFA_SERVICE_URL', 'http://localhost:5005/forge_voice')}")
    print(f" - ASF at {os.getenv('ASF_WEBSOCKET_BASE_URL', 'ws://localhost:5006/api/v1/podcasts/stream')} and its notification endpoint")

    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=True)
