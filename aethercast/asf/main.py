import flask
from flask import request, jsonify # Ensure request and jsonify are imported
from flask_socketio import SocketIO, emit, join_room, leave_room
import logging
import time
import os # Added for os.path.exists
from dotenv import load_dotenv # Added
import uuid # Added for default secret key

# --- Load Environment Variables ---
load_dotenv() # Added

# --- ASF Configuration ---
asf_config = {}

def load_asf_configuration():
    """Loads ASF configurations from environment variables with defaults."""
    global asf_config
    default_secret = str(uuid.uuid4())
    asf_config['ASF_SECRET_KEY'] = os.getenv('ASF_SECRET_KEY', default_secret)
    if asf_config['ASF_SECRET_KEY'] == default_secret:
        logger.warning(f"Using default generated ASF_SECRET_KEY: {default_secret}. Please set a persistent secret key in your environment for production.")

    asf_config['ASF_CORS_ALLOWED_ORIGINS'] = os.getenv('ASF_CORS_ALLOWED_ORIGINS', '*')
    asf_config['ASF_CHUNK_SIZE'] = int(os.getenv('ASF_CHUNK_SIZE', '4096'))
    asf_config['ASF_STREAM_SLEEP_INTERVAL'] = float(os.getenv('ASF_STREAM_SLEEP_INTERVAL', '0.01'))
    asf_config['ASF_UI_UPDATES_NAMESPACE'] = os.getenv('ASF_UI_UPDATES_NAMESPACE', '/ui_updates') # Added

    asf_config['ASF_HOST'] = os.getenv("ASF_HOST", '0.0.0.0')
    asf_config['ASF_PORT'] = int(os.getenv("ASF_PORT", 5006))
    asf_config['ASF_DEBUG_MODE'] = os.getenv("ASF_DEBUG", "True").lower() == "true"

    logger.info("--- ASF Configuration ---")
    for key, value in asf_config.items():
        if "SECRET_KEY" in key and value: # Mask secret key
            logger.info(f"  {key}: {'*' * (len(value) - 4) + value[-4:] if len(value) > 4 else '****'}")
        else:
            logger.info(f"  {key}: {value}")
    logger.info("--- End ASF Configuration ---")

# --- Logging Configuration (must be set up before load_asf_configuration uses logger) ---
# Use app.logger if available and not the root logger to integrate with Flask's logging
# This initial app object is temporary, just to get the logger context.
# It will be replaced by the fully configured one.
_temp_app_for_logger = flask.Flask(__name__)
if _temp_app_for_logger.logger and _temp_app_for_logger.logger.name != 'root':
    logger = _temp_app_for_logger.logger
    logger.setLevel(logging.INFO)
else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - ASF - %(message)s')
    logger = logging.getLogger(__name__)

# Load configuration at startup
load_asf_configuration()

# --- Constants ---
# Socket.IO Event Names - Audio Namespace
AUDIO_EVENT_CONNECT_ACK = 'connection_ack'
AUDIO_EVENT_ERROR = 'error' # Generic client-facing error
AUDIO_EVENT_STREAM_ERROR = 'stream_error' # Specific to stream failures
AUDIO_EVENT_STREAM_STATUS = 'stream_status'
AUDIO_EVENT_AUDIO_CHUNK = 'audio_chunk'
AUDIO_EVENT_AUDIO_CONTROL = 'audio_control'
AUDIO_EVENT_START_OF_STREAM = 'start_of_stream' # Used as data in AUDIO_EVENT_AUDIO_CONTROL
AUDIO_EVENT_END_OF_STREAM = 'end_of_stream'   # Used as data in AUDIO_EVENT_AUDIO_CONTROL

# Socket.IO Event Names - UI Namespace
UI_EVENT_CONNECT_ACK = 'ui_connection_ack'
UI_EVENT_ERROR = 'ui_error' # Generic client-facing error for UI namespace
UI_EVENT_SUBSCRIBED = 'subscribed_ui_updates'
# Event names for CPOA to send (used by send_ui_update, received by client)
# Example: 'generation_status', 'task_error' - these are dynamic based on CPOA needs, not ASF internals.

# HTTP Endpoint Error Types/Messages
HTTP_ERROR_NO_PAYLOAD = "NO_JSON_PAYLOAD"
HTTP_ERROR_MISSING_PARAMETERS = "MISSING_PARAMETERS"
HTTP_ERROR_ASF_CONFIG_ERROR = "ASF_SERVER_CONFIG_ERROR"
HTTP_ERROR_SOCKETIO_EMIT_FAILED = "SOCKETIO_EMIT_FAILED"

# --- Flask App and SocketIO Setup ---
app = flask.Flask(__name__)
app.config['SECRET_KEY'] = asf_config['ASF_SECRET_KEY']
socketio = SocketIO(app, cors_allowed_origins=asf_config['ASF_CORS_ALLOWED_ORIGINS'])

# Define namespace after config is loaded
ASF_UI_UPDATES_NAMESPACE = None # Will be set in main block or after load_asf_configuration if called there

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
    emit(AUDIO_EVENT_CONNECT_ACK, {'message': 'Connected to ASF. Please send join_stream with your stream_id.'})

@socketio.on('join_stream', namespace='/api/v1/podcasts/stream')
def handle_join_stream(data):
    """
    Client joins a specific stream room.
    'data' is expected to be a dict like {'stream_id': 'some_stream_id'}
    """
    stream_id = data.get('stream_id')
    if not stream_id:
        logger.warning(f"ASF: Client {flask.request.sid} tried to join stream without stream_id.")
        emit(AUDIO_EVENT_ERROR, {'message': 'stream_id is required for join_stream.'})
        return

    join_room(stream_id) # Use SocketIO rooms to manage clients for specific streams
    logger.info(f"ASF: Client {flask.request.sid} joined stream: {stream_id}")

    filepath = stream_id_to_filepath_map.get(stream_id)

    if not filepath:
        logger.error(f"ASF: Stream ID {stream_id} not found in map. Cannot stream audio.")
        emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Audio stream ID not found or not yet processed.'}, room=stream_id)
        return

    if not os.path.exists(filepath):
        logger.error(f"ASF: Audio file not found for stream ID {stream_id} at path: {filepath}")
        emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Audio file unavailable for this stream.'}, room=stream_id)
        return
    
    emit(AUDIO_EVENT_STREAM_STATUS, {'status': 'joined', 'stream_id': stream_id, 'message': f'Successfully joined stream {stream_id}. Preparing to stream audio.'}, room=stream_id)
    logger.info(f"ASF: Starting audio stream for {stream_id} from {filepath}")

    chunk_size = asf_config.get('ASF_CHUNK_SIZE', 4096)
    stream_sleep_interval = asf_config.get('ASF_STREAM_SLEEP_INTERVAL', 0.01)

    try:
        emit(AUDIO_EVENT_AUDIO_CONTROL, {'event': AUDIO_EVENT_START_OF_STREAM, 'stream_id': stream_id, 'timestamp': time.time()}, room=stream_id)
        logger.info(f"ASF: Sent {AUDIO_EVENT_START_OF_STREAM} for stream_id: {stream_id}")

        with open(filepath, 'rb') as audio_file:
            sequence_number = 0
            while True:
                audio_chunk = audio_file.read(chunk_size)
                if not audio_chunk:
                    break # End of file

                # Emit the binary audio chunk.
                # The `binary=True` argument is implicitly handled by Flask-SocketIO if the data is `bytes`.
                socketio.emit(AUDIO_EVENT_AUDIO_CHUNK, audio_chunk, namespace='/api/v1/podcasts/stream', room=stream_id)
                logger.debug(f"ASF: Sent audio chunk {sequence_number} for stream {stream_id} (size: {len(audio_chunk)} bytes)")
                sequence_number += 1
                socketio.sleep(stream_sleep_interval) # Use configured sleep interval

        emit(AUDIO_EVENT_AUDIO_CONTROL, {'event': AUDIO_EVENT_END_OF_STREAM, 'stream_id': stream_id, 'timestamp': time.time()}, room=stream_id)
        logger.info(f"ASF: Sent {AUDIO_EVENT_END_OF_STREAM} for stream_id: {stream_id} from file {filepath}")

    except Exception as e:
        logger.error(f"ASF: Error during audio streaming for stream_id {stream_id}: {e}", exc_info=True)
        emit(AUDIO_EVENT_STREAM_ERROR, {'message': f'An error occurred during streaming for stream {stream_id}.'}, room=stream_id)
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
        return jsonify({"error": HTTP_ERROR_NO_PAYLOAD, "details": "No JSON payload received"}), 400

    stream_id = data.get('stream_id')
    filepath = data.get('filepath')

    if not stream_id or not filepath:
        missing_params = []
        if not stream_id:
            missing_params.append('stream_id')
        if not filepath:
            missing_params.append('filepath')
        logger.error(f"ASF_NOTIFY: Missing parameters in /notify_new_audio: {', '.join(missing_params)}. Payload: {data}")
        return jsonify({"error": HTTP_ERROR_MISSING_PARAMETERS, "details": f"Missing required parameters: {', '.join(missing_params)}"}), 400

    # Store the mapping
    stream_id_to_filepath_map[stream_id] = filepath
    logger.info(f"ASF_NOTIFY: Received new audio notification. Stream ID: {stream_id}, Filepath: {filepath}. Map updated.")

    # TODO: In a real system, we might want to trigger something here if a client is already
    # waiting for this stream_id, or if the stream should start proactively.
    # For now, just storing the path is sufficient.

    return jsonify({"message": "Notification received successfully", "stream_id": stream_id}), 200


# --- UI Update Namespace Handlers ---
@socketio.on('connect', namespace=lambda: ASF_UI_UPDATES_NAMESPACE) # Use lambda to access config post-init
def handle_ui_connect():
    logger.info(f"ASF: Client {request.sid} connected to UI updates namespace: {ASF_UI_UPDATES_NAMESPACE}")
    emit(UI_EVENT_CONNECT_ACK, {'message': f'Connected to ASF UI updates on namespace {ASF_UI_UPDATES_NAMESPACE}.'})

@socketio.on('disconnect', namespace=lambda: ASF_UI_UPDATES_NAMESPACE)
def handle_ui_disconnect():
    logger.info(f"ASF: Client {request.sid} disconnected from UI updates namespace: {ASF_UI_UPDATES_NAMESPACE}")
    # Rooms are left automatically by Flask-SocketIO on disconnect from namespace

@socketio.on('subscribe_to_ui_updates', namespace=lambda: ASF_UI_UPDATES_NAMESPACE)
def handle_subscribe_ui_updates(data):
    client_id = data.get('client_id')
    if not client_id:
        logger.warning(f"ASF: Client {request.sid} attempted to subscribe to UI updates without a client_id.")
        emit(UI_EVENT_ERROR, {'message': 'client_id is required for UI update subscription.'})
        return

    join_room(client_id) # Use client_id as the room name
    logger.info(f"ASF: Client {request.sid} (client_id: {client_id}) subscribed to UI updates in room '{client_id}' on namespace {ASF_UI_UPDATES_NAMESPACE}.")
    emit(UI_EVENT_SUBSCRIBED, {'status': 'success', 'client_id': client_id, 'subscribed_to_room': client_id})


# --- Internal HTTP Endpoint for CPOA to send UI updates ---
@app.route('/asf/internal/send_ui_update', methods=['POST'])
def send_ui_update():
    """
    Internal endpoint for CPOA to send UI updates to specific clients.
    Expects JSON: {"client_id": "...", "event_name": "...", "data": {...}}
    """
    payload = request.get_json()
    if not payload:
        logger.error("ASF_SEND_UI: Received empty payload for /send_ui_update")
        return jsonify({"error": HTTP_ERROR_NO_PAYLOAD, "details": "No JSON payload received"}), 400

    client_id = payload.get('client_id')
    event_name = payload.get('event_name')
    event_data = payload.get('data')

    if not all([client_id, event_name, event_data is not None]): # event_data can be an empty dict
        missing_params = [p for p, v in {"client_id": client_id, "event_name": event_name, "data": event_data}.items() if v is None] # Check for None specifically for data if it can be empty dict
        if event_data is None and "data" not in missing_params : missing_params.append("data")

        logger.error(f"ASF_SEND_UI: Missing parameters in /send_ui_update: {', '.join(missing_params)}. Payload: {payload}")
        return jsonify({"error": HTTP_ERROR_MISSING_PARAMETERS, "details": f"Missing required parameters: {', '.join(missing_params)}"}), 400

    if not ASF_UI_UPDATES_NAMESPACE: # Ensure namespace is loaded
        logger.error("ASF_SEND_UI: ASF_UI_UPDATES_NAMESPACE not configured/loaded. Cannot emit message.")
        return jsonify({"error": HTTP_ERROR_ASF_CONFIG_ERROR, "details": "ASF server configuration error for UI namespace."}), 500

    try:
        logger.info(f"ASF_SEND_UI: Emitting '{event_name}' to client_id (room) '{client_id}' in namespace '{ASF_UI_UPDATES_NAMESPACE}' with data: {event_data}")
        socketio.emit(event_name, event_data, room=client_id, namespace=ASF_UI_UPDATES_NAMESPACE)
        return jsonify({"status": "success", "message": "UI update sent to client."}), 200
    except Exception as e:
        logger.error(f"ASF_SEND_UI: Failed to emit SocketIO event for client_id '{client_id}': {e}", exc_info=True)
        return jsonify({"error": HTTP_ERROR_SOCKETIO_EMIT_FAILED, "details": str(e)}), 500


if __name__ == '__main__':
    # Set the namespace globally after config is loaded and before app runs
    ASF_UI_UPDATES_NAMESPACE = asf_config.get('ASF_UI_UPDATES_NAMESPACE')
    if not ASF_UI_UPDATES_NAMESPACE:
        logger.error("ASF_UI_UPDATES_NAMESPACE is not defined in config. UI updates will not work.")
        # Optionally exit or raise error if this is critical for startup
    else:
        logger.info(f"ASF UI Updates Namespace configured to: {ASF_UI_UPDATES_NAMESPACE}")

    asf_host = asf_config.get('ASF_HOST')
    asf_port = asf_config.get('ASF_PORT')
    asf_debug_mode = asf_config.get('ASF_DEBUG_MODE')

    logger.info(f"Starting AudioStreamFeeder (ASF) with Flask-SocketIO on {asf_host}:{asf_port} (Debug: {asf_debug_mode})...")
    # The host '0.0.0.0' makes it accessible externally if needed.
    # allow_unsafe_werkzeug=True is for development with Werkzeug dev server.
    # In production, use a proper WSGI server like Gunicorn with eventlet or gevent.
    # For eventlet, you would typically run: `eventlet_wsgi.server(eventlet.listen((asf_host, asf_port)), app)`
    # but socketio.run handles this for development.
    socketio.run(app, host=asf_host, port=asf_port, debug=asf_debug_mode, allow_unsafe_werkzeug=True if asf_debug_mode else False)
