import sys
import os
from flask import Flask, jsonify, request, send_file
import uuid 
import sqlite3 # Added
from datetime import datetime # Added
import json # Added

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

# --- Database Configuration ---
DATABASE_FILE = 'aethercast_podcasts.db' # Will be created in the same dir as this script (api_gateway)
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
orchestrate_podcast_generation_imported = False
CPOA_IMPORT_ERROR_MESSAGE = ""
try:
    from aethercast.cpoa.main import orchestrate_podcast_generation
    orchestrate_podcast_generation_imported = True
except ImportError as e:
    CPOA_IMPORT_ERROR_MESSAGE = str(e)
    # Use app.logger if available, otherwise print
    log_func = app.logger.error if hasattr(app, 'logger') and app.logger else print
    log_func(f"Error importing CPOA: {e}")
    # Define a placeholder if import fails, so the app can still run and report status
    def orchestrate_podcast_generation(*args, **kwargs):
        raise ImportError(f"CPOA's orchestrate_podcast_generation could not be imported: {CPOA_IMPORT_ERROR_MESSAGE}")

# --- Flask App Initialization ---
app = Flask(__name__)

# Configure basic logging for Flask app if not already configured by Flask/debug mode
# This logging setup is fine, or Flask's default logger can be used via app.logger
if not app.debug and not app.logger.handlers: # Check if handlers are already added
    import logging
    # Ensure logging is configured before init_db might try to use app.logger
    # For simplicity, if flask's app.logger is not yet fully configured, print will be used in init_db
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - API_GW - %(message)s')


# --- Remove Global Storage PODCAST_FILE_MAP ---
# PODCAST_FILE_MAP = {} # This line is now removed.

# --- Health Check Endpoint ---
@app.route('/health', methods=['GET'])
def health_check():
    cpoa_status_message = "successfully imported" if orchestrate_podcast_generation_imported else f"failed to import ({CPOA_IMPORT_ERROR_MESSAGE})"
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
        "cpoa_orchestration_function_status": cpoa_status_message,
        "cpoa_import_successful": orchestrate_podcast_generation_imported,
        "database_status": db_status_message
    }), 200

# --- Podcast Generation Endpoint ---
@app.route('/api/v1/podcasts', methods=['POST'])
def create_podcast_generation_task():
    data = request.get_json()

    if not data or 'topic' not in data or not data['topic']:
        app.logger.warning("Bad request to /api/v1/podcasts: Missing or empty 'topic'.")
        return jsonify({"error": "Bad Request", "message": "Missing or empty 'topic' in request body."}), 400
    
    topic = data['topic']
    app.logger.info(f"Received podcast generation request for topic: '{topic}'")

    if not orchestrate_podcast_generation_imported:
        app.logger.error("CPOA module not loaded. Cannot process podcast generation.")
        return jsonify({"error": "Service Unavailable", "message": f"Core podcast orchestration module not loaded. Import error: {CPOA_IMPORT_ERROR_MESSAGE}"}), 503

    try:
        app.logger.info(f"Invoking CPOA for topic: '{topic}'")
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

    try:
        app.logger.info(f"Attempting to send file: {audio_filepath} for podcast_id: {podcast_id}")
        return send_file(audio_filepath, mimetype='audio/mpeg')
    except Exception as e:
        app.logger.error(f"Error sending file for podcast_id '{podcast_id}': {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An error occurred while trying to serve the audio file."}), 500


# --- Main Block ---
if __name__ == '__main__':
    init_db() # Initialize the database and create tables if they don't exist
    print("Starting Aethercast API Gateway on http://0.0.0.0:5001")
    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=True)
