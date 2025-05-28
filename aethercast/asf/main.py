import flask
from flask_socketio import SocketIO, emit, join_room, leave_room
import logging
import time

app = flask.Flask(__name__)
app.config['SECRET_KEY'] = 'aethercast_secret_asf!' # Secret key for session management
socketio = SocketIO(app, cors_allowed_origins="*") # Allow all origins for simplicity in dev

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- ASF Logic ---
# In a real ASF, this would be more complex, involving:
# - Receiving notifications from VFA about new audio available for a stream_id.
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
    
    emit('stream_status', {'status': 'joined', 'stream_id': stream_id, 'message': f'Successfully joined stream {stream_id}. Preparing to stream simulated audio.'}, room=stream_id)

    # Simulate audio streaming
    # In a real system, this would involve fetching audio data associated with stream_id
    # and sending binary audio chunks.
    
    # Send a start message
    emit('audio_control', {'event': 'start_of_stream', 'stream_id': stream_id, 'timestamp': time.time()}, room=stream_id)
    logger.info(f"ASF: Sent start_of_stream for stream_id: {stream_id}")
    socketio.sleep(0.1) # Short pause

    # Simulate sending a few text "audio chunks"
    for i in range(1, 6):
        chunk_content = f"Simulated audio chunk {i} for stream {stream_id}. Timestamp: {time.time()}"
        logger.info(f"ASF: Sending chunk {i} for stream_id: {stream_id}")
        # In a real app, this would be: emit('audio_chunk', b'binary_audio_data_here', room=stream_id)
        emit('text_chunk', {'data': chunk_content, 'sequence': i, 'stream_id': stream_id}, room=stream_id)
        socketio.sleep(1) # Simulate time it takes for audio chunk to play or be processed

    # Send an end message
    emit('audio_control', {'event': 'end_of_stream', 'stream_id': stream_id, 'timestamp': time.time()}, room=stream_id)
    logger.info(f"ASF: Sent end_of_stream for stream_id: {stream_id}")
    
    # Optionally, server can close connection after stream ends, or wait for client to close.
    # For now, leave it to client.

@socketio.on('disconnect', namespace='/api/v1/podcasts/stream')
def handle_disconnect():
    # Rooms are automatically left on disconnect by default with Flask-SocketIO
    logger.info(f"ASF: Client disconnected sid: {flask.request.sid}")

# Basic HTTP endpoint to confirm ASF is running (optional)
@app.route('/asf/health', methods=['GET'])
def health_check():
    return flask.jsonify({"status": "AudioStreamFeeder is healthy and running"}), 200


if __name__ == '__main__':
    logger.info("Starting AudioStreamFeeder (ASF) with Flask-SocketIO...")
    # Use port 5005 for ASF as defined in VFA's ASF_WEBSOCKET_BASE_URL
    # The host '0.0.0.0' makes it accessible externally if needed (e.g., from other containers/machines)
    # allow_unsafe_werkzeug=True is for development with Werkzeug dev server.
    # In production, use a proper WSGI server like Gunicorn with eventlet or gevent.
    socketio.run(app, host='0.0.0.0', port=5005, debug=True, allow_unsafe_werkzeug=True)
