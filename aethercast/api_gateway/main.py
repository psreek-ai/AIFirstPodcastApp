import sys
import os
from flask import Flask, jsonify, request, send_file # Added send_file
import uuid 

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

# --- Attempt CPOA Import ---
orchestrate_podcast_generation_imported = False
CPOA_IMPORT_ERROR_MESSAGE = ""
try:
    from aethercast.cpoa.main import orchestrate_podcast_generation
    orchestrate_podcast_generation_imported = True
except ImportError as e:
    CPOA_IMPORT_ERROR_MESSAGE = str(e)
    print(f"Error importing CPOA: {e}", file=sys.stderr)
    # Define a placeholder if import fails, so the app can still run and report status
    def orchestrate_podcast_generation(*args, **kwargs):
        # This placeholder will be called if orchestrate_podcast_generation_imported is False later
        # The check for orchestrate_podcast_generation_imported should happen before calling this.
        # However, to be safe, it can raise an error too.
        raise ImportError(f"CPOA's orchestrate_podcast_generation could not be imported: {CPOA_IMPORT_ERROR_MESSAGE}")

# --- Flask App Initialization ---
app = Flask(__name__)

# Configure basic logging for Flask app if not already configured by Flask/debug mode
if not app.debug: # Only configure if not in debug mode (debug usually configures logging)
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - API_GW - %(message)s')


# --- Global Storage (for podcast_id to filepath mapping - for now) ---
PODCAST_FILE_MAP = {}

# --- Health Check Endpoint ---
@app.route('/health', methods=['GET'])
def health_check():
    cpoa_status_message = "successfully imported" if orchestrate_podcast_generation_imported else f"failed to import ({CPOA_IMPORT_ERROR_MESSAGE})"
    return jsonify({
        "status": "API Gateway is healthy",
        "cpoa_orchestration_function_status": cpoa_status_message,
        "cpoa_import_successful": orchestrate_podcast_generation_imported 
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
        final_audio_details = cpoa_result.get("final_audio_details", {}) # Ensure it's a dict
        audio_filepath = final_audio_details.get("audio_filepath") if final_audio_details else None

        if generation_status == "completed" and audio_filepath:
            podcast_id = str(uuid.uuid4())
            PODCAST_FILE_MAP[podcast_id] = audio_filepath
            app.logger.info(f"Podcast {podcast_id} created for topic '{topic}'. File at: {audio_filepath}")
            
            response_data = {
                "podcast_id": podcast_id,
                "topic": topic,
                "generation_status": generation_status,
                "audio_url": f"/api/v1/podcasts/{podcast_id}/audio.mp3", # Placeholder, actual serving not implemented
                "message": "Podcast generated successfully.",
                "details": cpoa_result 
            }
            return jsonify(response_data), 201 # HTTP 201 Created
        
        elif generation_status in ["completed_with_warnings", "completed_with_errors"] or \
             (generation_status == "completed" and not audio_filepath):
            log_message = (
                f"Podcast generation for topic '{topic}' completed with issues. "
                f"Status: {generation_status}. Audio filepath: {audio_filepath}. "
                f"CPOA details: {cpoa_result.get('error_message', 'No specific error message.')}"
            )
            app.logger.warning(log_message)
            response_data = {
                "topic": topic,
                "generation_status": generation_status,
                "message": cpoa_result.get("error_message") or "Podcast generation completed but no usable audio was produced or an issue occurred.",
                "details": cpoa_result
            }
            return jsonify(response_data), 200 # HTTP 200 OK, but client should check status and details
        
        else: # Covers "failed" or any other unexpected CPOA status
            app.logger.error(f"Podcast generation failed for topic '{topic}'. CPOA status: {generation_status}. CPOA details: {cpoa_result}")
            response_data = {
                "topic": topic,
                "generation_status": generation_status or "unknown_failure",
                "message": cpoa_result.get("error_message") or "Podcast generation failed at CPOA level.",
                "details": cpoa_result
            }
            return jsonify(response_data), 500 # HTTP 500 Internal Server Error
            
    except ImportError as ie: # Should be caught by the global flag, but as a safeguard if placeholder fails
        app.logger.error(f"CPOA function unavailable during request: {ie}")
        return jsonify({"error": "Service Unavailable", "message": "Core podcast orchestration module is not available."}), 503
    except Exception as e:
        app.logger.error(f"Unexpected error during CPOA orchestration for topic '{topic}': {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred during podcast generation."}), 500

# --- Serve Podcast Audio Endpoint ---
@app.route('/api/v1/podcasts/<string:podcast_id>/audio.mp3', methods=['GET'])
def serve_podcast_audio(podcast_id: str):
    app.logger.info(f"Request received to serve audio for podcast_id: {podcast_id}")

    audio_filepath = PODCAST_FILE_MAP.get(podcast_id)

    if not audio_filepath:
        app.logger.warning(f"Podcast ID '{podcast_id}' not found in PODCAST_FILE_MAP.")
        return jsonify({"error": "Not Found", "message": "Invalid or expired podcast_id."}), 404

    if not os.path.exists(audio_filepath):
        app.logger.error(f"Audio file for podcast_id '{podcast_id}' not found at expected path: {audio_filepath}")
        # Optional: Clean up the map if the file is missing
        PODCAST_FILE_MAP.pop(podcast_id, None)
        return jsonify({"error": "Not Found", "message": "Audio file missing or no longer available."}), 404

    try:
        app.logger.info(f"Attempting to send file: {audio_filepath} for podcast_id: {podcast_id}")
        # For now, hardcoding mimetype='audio/mpeg' as VFA defaults to MP3.
        # Future: Store audio_format from VFA and map to correct MIME type.
        # e.g., if VFA returns "mp3", mimetype="audio/mpeg"
        # if VFA returns "ogg_opus" or "opus", mimetype="audio/ogg"
        # if VFA returns "wav", mimetype="audio/wav"
        return send_file(audio_filepath, mimetype='audio/mpeg')
    except Exception as e:
        app.logger.error(f"Error sending file for podcast_id '{podcast_id}': {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An error occurred while trying to serve the audio file."}), 500


# --- Main Block ---
if __name__ == '__main__':
    print("Starting Aethercast API Gateway on http://0.0.0.0:5001")
    # Note: Setting debug=False for production, use True for development if needed.
    # use_reloader=False can be helpful if running in certain environments or to avoid double initializations.
    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=True)
