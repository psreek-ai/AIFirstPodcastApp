import flask
from flask import request, jsonify # Ensure request and jsonify are imported
from flask_socketio import SocketIO, emit, join_room, leave_room
import logging
import time
import os # Added for os.path.exists

app = flask.Flask(__name__)
app.config['SECRET_KEY'] = 'aethercast_secret_asf!' # Secret key for session management
socketio = SocketIO(app, cors_allowed_origins="*") # Allow all origins for simplicity in dev

# --- Logging Configuration ---
# Use app.logger if available and not the root logger to integrate with Flask's logging
if app.logger and app.logger.name != 'root':
    logger = app.logger
    logger.setLevel(logging.INFO) # Ensure Flask's logger level is set if using it
else:
    # Fallback to basicConfig if not using Flask's logger or it's the root logger
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - ASF - %(message)s')
    logger = logging.getLogger(__name__)


# --- Global Data Structures ---
stream_id_to_filepath_map = {} # Stores mapping from stream_id to audio file path

# --- ASF Logic ---
# In a real ASF, this would be more complex, involving:
# - Using the stream_id_to_filepath_map to get actual audio data.
# - Fetching actual audio data (e.g., from S3 or a shared volume).
# - Chunking the audio data into binary format.
# - Streaming binary audio chunks.

@socketio.on('connect', namespace='/api/v1/podcasts/stream')
def handle_connect():
    """
    Handles a new WebSocket connection.
    The stream_id is expected to be part of the connection URL,
    but Flask-SocketIO namespaces don't directly expose URL parameters in connect handler.
    We'll expect a 'join_stream' event with stream_id from client.
    """
    logger.info(f"ASF: Client connected with sid: {flask.request.sid} to namespace /api/v1/podcasts/stream")
    emit('connection_ack', {'message': 'Connected to ASF. Please send join_stream with your stream_id.'})

@socketio.on('join_stream', namespace='/api/v1/podcasts/stream')
def handle_join_stream(data):
    """
    Client joins a specific stream room.
    'data' is expected to be a dict like {'stream_id': 'some_stream_id'}
    """
    stream_id = data.get('stream_id')
    if not stream_id:
        logger.warning(f"ASF: Client {flask.request.sid} tried to join stream without stream_id.")
        emit('error', {'message': 'stream_id is required for join_stream.'})
        return

    join_room(stream_id) # Use SocketIO rooms to manage clients for specific streams
    logger.info(f"ASF: Client {flask.request.sid} joined stream: {stream_id}")

    filepath = stream_id_to_filepath_map.get(stream_id)

    if not filepath:
        logger.error(f"ASF: Stream ID {stream_id} not found in map. Cannot stream audio.")
        emit('stream_error', {'message': 'Audio stream ID not found or not yet processed.'}, room=stream_id)
        return

    if not os.path.exists(filepath):
        logger.error(f"ASF: Audio file not found for stream ID {stream_id} at path: {filepath}")
        emit('stream_error', {'message': 'Audio file unavailable for this stream.'}, room=stream_id)
        return
    
    emit('stream_status', {'status': 'joined', 'stream_id': stream_id, 'message': f'Successfully joined stream {stream_id}. Preparing to stream audio.'}, room=stream_id)
    logger.info(f"ASF: Starting audio stream for {stream_id} from {filepath}")

    try:
        emit('audio_control', {'event': 'start_of_stream', 'stream_id': stream_id, 'timestamp': time.time()}, room=stream_id)
        logger.info(f"ASF: Sent start_of_stream for stream_id: {stream_id}")

        chunk_size = 4096  # 4KB chunks
        with open(filepath, 'rb') as audio_file:
            sequence_number = 0
            while True:
                audio_chunk = audio_file.read(chunk_size)
                if not audio_chunk:
                    break # End of file

                # Emit the binary audio chunk.
                # The event name 'audio_chunk' is used here.
                # The `binary=True` argument is implicitly handled by Flask-SocketIO if the data is `bytes`.
                socketio.emit('audio_chunk', audio_chunk, namespace='/api/v1/podcasts/stream', room=stream_id)
                logger.debug(f"ASF: Sent audio chunk {sequence_number} for stream {stream_id} (size: {len(audio_chunk)} bytes)")
                sequence_number += 1
                socketio.sleep(0.01) # Small sleep to yield control, adjust as needed for flow control.
                                     # Actual pacing depends on client consumption and network.

        emit('audio_control', {'event': 'end_of_stream', 'stream_id': stream_id, 'timestamp': time.time()}, room=stream_id)
        logger.info(f"ASF: Sent end_of_stream for stream_id: {stream_id} from file {filepath}")

    except Exception as e:
        logger.error(f"ASF: Error during audio streaming for stream_id {stream_id}: {e}", exc_info=True)
        emit('stream_error', {'message': f'An error occurred during streaming for stream {stream_id}.'}, room=stream_id)
    finally:
        # Optionally, leave room or close connection if appropriate.
        # For now, client manages connection lifecycle after stream ends or errors.
        pass


@socketio.on('disconnect', namespace='/api/v1/podcasts/stream')
def handle_disconnect():
    # Rooms are automatically left on disconnect by default with Flask-SocketIO
    logger.info(f"ASF: Client disconnected sid: {flask.request.sid}")

# Basic HTTP endpoint to confirm ASF is running (optional)
@app.route('/asf/health', methods=['GET'])
def health_check():
    return flask.jsonify({"status": "AudioStreamFeeder is healthy and running"}), 200

# --- Internal HTTP Endpoints ---
@app.route('/asf/internal/notify_new_audio', methods=['POST'])
def notify_new_audio():
    """
    Internal endpoint for other services (like VFA) to notify ASF about new audio files.
    Expects JSON: {"stream_id": "...", "filepath": "..."}
    """
    data = request.get_json()
    if not data:
        logger.error("ASF_NOTIFY: Received empty payload for /notify_new_audio")
        return jsonify({"error": "No JSON payload received"}), 400

    stream_id = data.get('stream_id')
    filepath = data.get('filepath')

    if not stream_id or not filepath:
        missing_params = []
        if not stream_id:
            missing_params.append('stream_id')
        if not filepath:
            missing_params.append('filepath')
        logger.error(f"ASF_NOTIFY: Missing parameters in /notify_new_audio: {', '.join(missing_params)}. Payload: {data}")
        return jsonify({"error": f"Missing required parameters: {', '.join(missing_params)}"}), 400

    # Store the mapping
    stream_id_to_filepath_map[stream_id] = filepath
    logger.info(f"ASF_NOTIFY: Received new audio notification. Stream ID: {stream_id}, Filepath: {filepath}. Map updated.")

    # TODO: In a real system, we might want to trigger something here if a client is already
    # waiting for this stream_id, or if the stream should start proactively.
    # For now, just storing the path is sufficient.

    return jsonify({"message": "Notification received successfully", "stream_id": stream_id}), 200


if __name__ == '__main__':
    asf_port = int(os.getenv("ASF_PORT", 5006)) # Default to 5006, allow override
    asf_host = os.getenv("ASF_HOST", '0.0.0.0')
    asf_debug_mode = os.getenv("ASF_DEBUG", "True").lower() == "true"

    logger.info(f"Starting AudioStreamFeeder (ASF) with Flask-SocketIO on {asf_host}:{asf_port} (Debug: {asf_debug_mode})...")
    # The host '0.0.0.0' makes it accessible externally if needed.
    # allow_unsafe_werkzeug=True is for development with Werkzeug dev server.
    # In production, use a proper WSGI server like Gunicorn with eventlet or gevent.
    socketio.run(app, host=asf_host, port=asf_port, debug=asf_debug_mode, allow_unsafe_werkzeug=True)
