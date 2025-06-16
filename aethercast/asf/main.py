import flask
from flask import request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import logging
import time
import os
from dotenv import load_dotenv
import uuid
import requests # Ensure requests is imported
import json # Import json

# --- Load Environment Variables ---
load_dotenv()

# --- Logging Setup ---
# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="asf"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

# Initialize Flask app early so app.logger can be configured
app = flask.Flask(__name__) # Moved Flask app initialization up

# Configure JSON logging for the Flask app
def setup_json_logging(flask_app):
    flask_app.logger.handlers.clear() # Clear existing default Flask handlers
    logHandler = logging.StreamHandler()
    service_filter = ServiceNameFilter("asf")
    logHandler.addFilter(service_filter)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s"
    )
    logHandler.setFormatter(formatter)
    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO) # Default level, can be adjusted by config
    flask_app.logger.info("Standard logging configured for ASF service.")

setup_json_logging(app)

# Make the global logger use the configured app.logger
logger = app.logger

# --- ASF Configuration ---
asf_config = {}

def load_asf_configuration():
    """Loads ASF configurations from environment variables with defaults."""
    global asf_config
    default_secret = str(uuid.uuid4())
    asf_config['ASF_SECRET_KEY'] = os.getenv('ASF_SECRET_KEY', default_secret)
    if asf_config['ASF_SECRET_KEY'] == default_secret and not os.getenv('ASF_SECRET_KEY'): # Log only if not explicitly set
        logger.warning(f"Using default generated ASF_SECRET_KEY. Please set a persistent secret key for production.")

    asf_config['ASF_CORS_ALLOWED_ORIGINS'] = os.getenv('ASF_CORS_ALLOWED_ORIGINS', '*')
    asf_config['ASF_CHUNK_SIZE'] = int(os.getenv('ASF_CHUNK_SIZE', '4096'))
    asf_config['ASF_STREAM_SLEEP_INTERVAL'] = float(os.getenv('ASF_STREAM_SLEEP_INTERVAL', '0.01'))
    asf_config['ASF_UI_UPDATES_NAMESPACE'] = os.getenv('ASF_UI_UPDATES_NAMESPACE', '/ui_updates')

    # New configuration for internal API Gateway URL
    asf_config['INTERNAL_API_GW_BASE_URL'] = os.getenv('INTERNAL_API_GW_BASE_URL', 'http://api_gateway:5001')
    if not os.getenv('INTERNAL_API_GW_BASE_URL'): # Log if using default because it wasn't set
        logger.info(f"INTERNAL_API_GW_BASE_URL not set, using default: {asf_config['INTERNAL_API_GW_BASE_URL']}")


    asf_config['ASF_HOST'] = os.getenv("ASF_HOST", '0.0.0.0')
    asf_config['ASF_PORT'] = int(os.getenv("ASF_PORT", 5006))
    # ASF_DEBUG_MODE is removed, FLASK_DEBUG will be read directly where needed.

    # Adjust logger level based on FLASK_DEBUG mode
    flask_debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == 'true'
    logger.setLevel(logging.DEBUG if flask_debug_mode else logging.INFO)


    logger.info("--- ASF Configuration ---")
    for key, value in asf_config.items():
        if "SECRET_KEY" in key and value and len(value) > 4:
            logger.info(f"  {key}: {'*' * (len(value) - 4) + value[-4:]}")
        else:
            logger.info(f"  {key}: {value}")
    logger.info("--- End ASF Configuration ---")

# Load configuration at startup
load_asf_configuration()

# --- Constants ---
AUDIO_EVENT_CONNECT_ACK = 'connection_ack'
AUDIO_EVENT_ERROR = 'error'
AUDIO_EVENT_STREAM_ERROR = 'stream_error'
AUDIO_EVENT_STREAM_STATUS = 'stream_status'
AUDIO_EVENT_AUDIO_CHUNK = 'audio_chunk'
AUDIO_EVENT_AUDIO_CONTROL = 'audio_control'
AUDIO_EVENT_START_OF_STREAM = 'start_of_stream'
AUDIO_EVENT_END_OF_STREAM = 'end_of_stream'

UI_EVENT_CONNECT_ACK = 'ui_connection_ack'
UI_EVENT_ERROR = 'ui_error'
UI_EVENT_SUBSCRIBED = 'subscribed_ui_updates'

# --- Flask App and SocketIO Setup ---
# app is initialized earlier for logging
app.config['SECRET_KEY'] = asf_config['ASF_SECRET_KEY']
socketio = SocketIO(app, cors_allowed_origins=asf_config['ASF_CORS_ALLOWED_ORIGINS'], logger=True, engineio_logger=True)
# Explicitly set SocketIO loggers to use app.logger's level and handlers if desired,
# or they might log to stderr with their own format.
# For now, focusing on app.logger for Flask routes and our direct logs.
# If socketio/engineio logs become noisy or unformatted, address them:
# logging.getLogger('socketio').setLevel(logging.INFO if not asf_config['ASF_DEBUG_MODE'] else logging.DEBUG)
# logging.getLogger('engineio').setLevel(logging.INFO if not asf_config['ASF_DEBUG_MODE'] else logging.DEBUG)
# For them to use JSON format, they'd need their handlers replaced too.
# This is out of scope for the current subtask if app.logger itself is JSON.

ASF_UI_UPDATES_NAMESPACE = asf_config['ASF_UI_UPDATES_NAMESPACE'] # Set from loaded config

# --- Global Data Structures ---
stream_id_to_filepath_map = {} # Stores mapping from stream_id to GCS URI or local path

# --- ASF Logic ---
@socketio.on('connect', namespace='/api/v1/podcasts/stream')
def handle_connect():
    logger.info(f"ASF: Client connected with sid: {flask.request.sid} to audio namespace.")
    logger.info("ASF WebSocket connection", extra=dict(metric_name="asf_websocket_connection_count", value=1, tags={"namespace": "/api/v1/podcasts/stream", "status": "connected"}))
    emit(AUDIO_EVENT_CONNECT_ACK, {'message': 'Connected to ASF. Please send join_stream with your stream_id.'})

@socketio.on('join_stream', namespace='/api/v1/podcasts/stream')
def handle_join_stream(data):
    stream_id = data.get('stream_id')
    client_sid = flask.request.sid
    stream_id_prefix_tag = stream_id[:8] if stream_id else "unknown"

    if not stream_id:
        logger.warning(f"ASF: Client {client_sid} tried to join stream without stream_id.")
        emit(AUDIO_EVENT_ERROR, {'message': 'stream_id is required for join_stream.'})
        # Could log an error count here if desired, but it's more of a client protocol error
        return

    join_room(stream_id)
    logger.info(f"ASF: Client {client_sid} joined stream room: {stream_id}")

    gcs_uri = stream_id_to_filepath_map.get(stream_id)

    if not gcs_uri:
        logger.error(f"ASF: Stream ID {stream_id} not found in map. Cannot stream audio.")
        logger.error("ASF audio stream error", extra=dict(metric_name="asf_audio_stream_error_count", value=1, tags={"stream_id_prefix": stream_id_prefix_tag, "reason": "stream_id_not_found_in_map"}))
        emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Audio stream ID not found or not yet processed.'}, room=stream_id)
        return

    emit(AUDIO_EVENT_STREAM_STATUS, {'status': 'joined', 'stream_id': stream_id, 'message': f'Successfully joined stream {stream_id}. Preparing to stream audio.'}, room=stream_id)
    logger.info(f"ASF: Starting audio stream for {stream_id} from GCS URI: {gcs_uri}")
    logger.info("ASF audio stream started", extra=dict(metric_name="asf_audio_stream_started_count", value=1, tags={"stream_id_prefix": stream_id_prefix_tag}))


    chunk_size = asf_config.get('ASF_CHUNK_SIZE', 4096)
    stream_sleep_interval = asf_config.get('ASF_STREAM_SLEEP_INTERVAL', 0.01)
    signed_url_from_api_gw = None

    # --- New Logic to get Signed URL ---
    if gcs_uri.startswith("gs://"):
        # try:
            # # Ensure filepath (gcs_uri) is URL-encoded for the query parameter if it could contain special chars
            # # For gs://bucket/object URIs, this is usually not an issue, but good practice for arbitrary strings.
            # # from urllib.parse import quote_plus
            # # encoded_gcs_uri = quote_plus(gcs_uri)
            # # However, requests usually handles URL encoding for query parameters.
            #
            # signed_url_fetch_endpoint = f"{asf_config['INTERNAL_API_GW_BASE_URL']}/api/v1/internal/media_access_url"
            # params = {'gcs_uri': gcs_uri}
            #
            # logger.debug(f"ASF: Requesting signed URL for GCS URI '{gcs_uri}' from endpoint: {signed_url_fetch_endpoint}")

            # signed_url_fetch_start_time = time.time() # Initialize before try block
            # try:
        pass # Minimal if block
            #     response = requests.get(signed_url_fetch_endpoint, params=params, timeout=5)
            #     response.raise_for_status()
            #     response_data = response.json()
            #     signed_url_from_api_gw = response_data.get("signed_url")
            #     # Calculate duration immediately after successful call, if needed here,
            #     # or rely on calculation within except blocks based on the pre-try start_time
            #     # For now, let's keep it simple and ensure start_time is always available.
            #     # signed_url_fetch_duration_ms = (time.time() - signed_url_fetch_start_time) * 1000
            #     # logger.info("ASF signed URL fetch processed", extra=dict(metric_name="asf_signed_url_fetch_latency_ms", value=round(signed_url_fetch_duration_ms, 2)))
            #
            #
            #     if not signed_url_from_api_gw:
            #         logger.error(f"ASF: API Gateway did not return a signed_url for GCS URI {gcs_uri} (stream {stream_id}). Response: {response_data}")
            #         logger.error("ASF signed URL fetch failure", extra=dict(metric_name="asf_signed_url_fetch_failure_count", value=1, tags={"reason": "no_url_in_response"}))
            #         emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Failed to obtain secure access for audio stream.'}, room=stream_id)
            #         return
            #     logger.info(f"ASF: Successfully obtained signed URL for GCS URI {gcs_uri} (stream {stream_id}).")
            #
            # except requests.exceptions.Timeout:
            #     signed_url_fetch_duration_ms = (time.time() - signed_url_fetch_start_time) * 1000
            #     logger.info(f"ASF signed URL fetch processed (timeout) - duration: {signed_url_fetch_duration_ms:.2f}ms", extra=dict(metric_name="asf_signed_url_fetch_latency_ms", value=round(signed_url_fetch_duration_ms, 2)))
            #     logger.error(f"ASF: Timeout requesting signed URL from API Gateway for GCS URI {gcs_uri} (stream {stream_id}).")
            #     logger.error("ASF signed URL fetch failure", extra=dict(metric_name="asf_signed_url_fetch_failure_count", value=1, tags={"reason": "timeout"}))
            #     emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Failed to prepare audio stream due to internal timeout.'}, room=stream_id)
            #     return
            # except requests.exceptions.HTTPError as e_http:
            #     signed_url_fetch_duration_ms = (time.time() - signed_url_fetch_start_time) * 1000
            #     logger.info(f"ASF signed URL fetch processed (http_error_{e_http.response.status_code}) - duration: {signed_url_fetch_duration_ms:.2f}ms", extra=dict(metric_name="asf_signed_url_fetch_latency_ms", value=round(signed_url_fetch_duration_ms, 2)))
            #     logger.error(f"ASF: HTTP error {e_http.response.status_code} requesting signed URL from API Gateway for GCS URI {gcs_uri} (stream {stream_id}). Response: {e_http.response.text}")
            #     logger.error("ASF signed URL fetch failure", extra=dict(metric_name="asf_signed_url_fetch_failure_count", value=1, tags={"reason": f"http_error_{e_http.response.status_code}"}))
            #     emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Failed to prepare audio stream due to internal error.'}, room=stream_id)
            #     return
            # except requests.exceptions.RequestException as e_req:
            #     signed_url_fetch_duration_ms = (time.time() - signed_url_fetch_start_time) * 1000
            #     logger.info(f"ASF signed URL fetch processed (request_exception) - duration: {signed_url_fetch_duration_ms:.2f}ms", extra=dict(metric_name="asf_signed_url_fetch_latency_ms", value=round(signed_url_fetch_duration_ms, 2)))
            #     logger.error(f"ASF: Error requesting signed URL from API Gateway for GCS URI {gcs_uri} (stream {stream_id}): {e_req}", exc_info=True)
            #     logger.error("ASF signed URL fetch failure", extra=dict(metric_name="asf_signed_url_fetch_failure_count", value=1, tags={"reason": "request_exception"}))
            #     emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Failed to prepare audio stream.'}, room=stream_id)
            #     return
            # except json.JSONDecodeError:
            #     signed_url_fetch_duration_ms = (time.time() - signed_url_fetch_start_time) * 1000
            #     logger.info(f"ASF signed URL fetch processed (json_decode_error) - duration: {signed_url_fetch_duration_ms:.2f}ms", extra=dict(metric_name="asf_signed_url_fetch_latency_ms", value=round(signed_url_fetch_duration_ms, 2)))
            #     logger.error(f"ASF: Failed to decode JSON response from API Gateway when fetching signed URL for GCS URI {gcs_uri} (stream {stream_id}). Response: {response.text if 'response' in locals() else 'N/A'}")
            #     logger.error("ASF signed URL fetch failure", extra=dict(metric_name="asf_signed_url_fetch_failure_count", value=1, tags={"reason": "json_decode_error"}))
            #     emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Invalid response from internal service preparing audio stream.'}, room=stream_id)
            #     return
            # except Exception as e_general: # Catch any other unexpected error from the try block
            #     logger.error(f"ASF: Unexpected error while trying to get signed URL for GCS URI {gcs_uri} (stream {stream_id}): {e_general}", exc_info=True)
            #     logger.error("ASF signed URL fetch failure", extra=dict(metric_name="asf_signed_url_fetch_failure_count", value=1, tags={"reason": "unexpected_exception_in_get_url"}))
            #     emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Unexpected error preparing audio stream.'}, room=stream_id)
            #     return
        # pass # Explicitly pass after the try-except block within the if
    else:
        logger.warning(f"ASF: Filepath for stream {stream_id} is not a GCS URI: '{gcs_uri}'. Attempting local streaming.")
        if not os.path.exists(gcs_uri):
            logger.error(f"ASF: Local audio file not found for stream ID {stream_id} at path: {gcs_uri}")
            logger.error("ASF audio stream error", extra=dict(metric_name="asf_audio_stream_error_count", value=1, tags={"stream_id_prefix": stream_id_prefix_tag, "reason": "local_file_not_found"}))
            emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Audio file unavailable for this stream.'}, room=stream_id)
            return
        signed_url_from_api_gw = gcs_uri

    # --- Streaming Logic ---
    try:
        emit(AUDIO_EVENT_AUDIO_CONTROL, {'event': AUDIO_EVENT_START_OF_STREAM, 'stream_id': stream_id, 'timestamp': time.time()}, room=stream_id)
        logger.info(f"ASF: Sent {AUDIO_EVENT_START_OF_STREAM} for stream_id: {stream_id}")

        if gcs_uri.startswith("gs://"): # Streaming from GCS signed URL
            if not signed_url_from_api_gw: # Should have been caught above, but as a safeguard
                logger.error(f"ASF: Critical error - signed URL is None before attempting GCS stream for {stream_id}.")
                emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Internal error preparing stream.'}, room=stream_id)
                return

            with requests.get(signed_url_from_api_gw, stream=True, timeout=10) as r: # Added timeout for GCS GET
                r.raise_for_status() # Check for HTTP errors from GCS (e.g., expired URL, permissions)
                logger.info(f"ASF: Streaming from GCS signed URL for stream_id: {stream_id}")
                sequence_number = 0
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk: # filter out keep-alive new chunks
                        socketio.emit(AUDIO_EVENT_AUDIO_CHUNK, chunk, namespace='/api/v1/podcasts/stream', room=stream_id)
                        logger.debug(f"ASF: Sent GCS audio chunk {sequence_number} for stream {stream_id} (size: {len(chunk)} bytes)")
                        sequence_number += 1
                        socketio.sleep(stream_sleep_interval)
            logger.info(f"ASF: Finished streaming from GCS for stream_id: {stream_id}")

        else: # Streaming from local file (legacy or testing)
            logger.info(f"ASF: Streaming from local file for stream_id: {stream_id}, path: {gcs_uri}")
            with open(gcs_uri, 'rb') as audio_file:
                sequence_number = 0
                while True:
                    audio_chunk = audio_file.read(chunk_size)
                    if not audio_chunk: break
                    socketio.emit(AUDIO_EVENT_AUDIO_CHUNK, audio_chunk, namespace='/api/v1/podcasts/stream', room=stream_id)
                    logger.debug(f"ASF: Sent local audio chunk {sequence_number} for stream {stream_id} (size: {len(audio_chunk)} bytes)")
                    sequence_number += 1
                    socketio.sleep(stream_sleep_interval)
            logger.info(f"ASF: Finished streaming from local file for stream_id: {stream_id}")

        emit(AUDIO_EVENT_AUDIO_CONTROL, {'event': AUDIO_EVENT_END_OF_STREAM, 'stream_id': stream_id, 'timestamp': time.time()}, room=stream_id)
        logger.info(f"ASF: Sent {AUDIO_EVENT_END_OF_STREAM} for stream_id: {stream_id}")
        logger.info("ASF audio stream completed", extra=dict(metric_name="asf_audio_stream_completed_count", value=1, tags={"stream_id_prefix": stream_id_prefix_tag}))

    except requests.exceptions.HTTPError as e_gcs_http:
        logger.error(f"ASF: HTTP error when streaming from GCS URL for stream {stream_id} (URL might be expired or invalid): {e_gcs_http}", exc_info=True)
        logger.error("ASF audio stream error", extra=dict(metric_name="asf_audio_stream_error_count", value=1, tags={"stream_id_prefix": stream_id_prefix_tag, "reason": "gcs_streaming_http_error"}))
        emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Failed to stream audio from source (access denied or link expired).'}, room=stream_id)
    except requests.exceptions.RequestException as e_req_stream:
        logger.error(f"ASF: Error streaming audio for stream {stream_id}: {e_req_stream}", exc_info=True)
        logger.error("ASF audio stream error", extra=dict(metric_name="asf_audio_stream_error_count", value=1, tags={"stream_id_prefix": stream_id_prefix_tag, "reason": "gcs_streaming_request_exception"}))
        emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'Failed to stream audio from source.'}, room=stream_id)
    except Exception as e_stream:
        logger.error(f"ASF: General error during audio streaming for stream_id {stream_id}: {e_stream}", exc_info=True)
        logger.error("ASF audio stream error", extra=dict(metric_name="asf_audio_stream_error_count", value=1, tags={"stream_id_prefix": stream_id_prefix_tag, "reason": "unknown_streaming_error"}))
        emit(AUDIO_EVENT_STREAM_ERROR, {'message': 'An unexpected error occurred during streaming.'}, room=stream_id)
    finally:
        pass # No specific cleanup needed here now

@socketio.on('disconnect', namespace='/api/v1/podcasts/stream')
def handle_disconnect():
    logger.info(f"ASF: Client disconnected sid: {flask.request.sid} from audio namespace.")
    logger.info("ASF WebSocket connection", extra=dict(metric_name="asf_websocket_connection_count", value=1, tags={"namespace": "/api/v1/podcasts/stream", "status": "disconnected"}))

@app.route('/asf/health', methods=['GET'])
def health_check():
    # Check connectivity to API Gateway for signed URLs if INTERNAL_API_GW_BASE_URL is set
    api_gw_status = "Not configured or not checked."
    if asf_config.get('INTERNAL_API_GW_BASE_URL'):
        try:
            # Check if the base URL is reachable, maybe a specific health endpoint on API GW if available
            # For now, just checking the base URL with a timeout.
            # This doesn't guarantee the /api/v1/internal/media_access_url endpoint itself is working.
            health_url = f"{asf_config['INTERNAL_API_GW_BASE_URL']}/health" # Assuming API GW has a health endpoint
            response = requests.get(health_url, timeout=2)
            if response.status_code == 200:
                api_gw_status = f"Successfully connected to API Gateway at {asf_config['INTERNAL_API_GW_BASE_URL']} (HTTP {response.status_code})."
            else:
                api_gw_status = f"Connected to API Gateway at {asf_config['INTERNAL_API_GW_BASE_URL']}, but got HTTP {response.status_code}."
        except requests.exceptions.ConnectionError:
            api_gw_status = f"Failed to connect to API Gateway at {asf_config['INTERNAL_API_GW_BASE_URL']} (Connection Error)."
        except requests.exceptions.Timeout:
            api_gw_status = f"Timed out connecting to API Gateway at {asf_config['INTERNAL_API_GW_BASE_URL']}."
        except Exception as e_gw_health:
             api_gw_status = f"Error checking API Gateway health: {str(e_gw_health)}"


    return flask.jsonify({
        "status": "AudioStreamFeeder is healthy and running",
        "api_gateway_connectivity": api_gw_status,
        "config": { # Expose some non-sensitive config for easier debugging
            "chunk_size": asf_config.get('ASF_CHUNK_SIZE'),
            "sleep_interval": asf_config.get('ASF_STREAM_SLEEP_INTERVAL'),
            "ui_namespace": ASF_UI_UPDATES_NAMESPACE,
            "internal_api_gw_url_configured": bool(asf_config.get('INTERNAL_API_GW_BASE_URL'))
        }
    }), 200


@app.route('/asf/internal/notify_new_audio', methods=['POST'])
def notify_new_audio():
    try: data = request.get_json()
    except Exception: return jsonify({"error_code": "ASF_NOTIFY_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not data: return jsonify({"error_code": "ASF_NOTIFY_INVALID_PAYLOAD", "message": "Payload required."}), 400

    stream_id = data.get('stream_id')
    filepath = data.get('filepath') # This should now be a GCS URI

    if not stream_id or not isinstance(stream_id, str) or not stream_id.strip():
        return jsonify({"error_code": "ASF_NOTIFY_INVALID_STREAM_ID", "message": "'stream_id' must be non-empty string."}), 400
    if not filepath or not isinstance(filepath, str) or not filepath.strip():
        return jsonify({"error_code": "ASF_NOTIFY_INVALID_FILEPATH", "message": "'filepath' (GCS URI) must be non-empty string."}), 400

    # Validate if it's a GCS URI or allow local paths for testing/legacy
    if not filepath.startswith("gs://") and not os.path.isabs(filepath): # Basic check
         logger.warning(f"ASF_NOTIFY: Filepath '{filepath}' for stream '{stream_id}' does not look like a GCS URI or an absolute local path. Proceeding, but streaming might fail if not handled.")

    stream_id_to_filepath_map[stream_id] = filepath
    logger.info(f"ASF_NOTIFY: New audio notification. Stream ID: {stream_id}, Path/URI: {filepath}. Map updated.")
    return jsonify({"message": "Notification received successfully", "stream_id": stream_id}), 200

# --- UI Update Namespace Handlers ---
@socketio.on('connect', namespace=ASF_UI_UPDATES_NAMESPACE)
def handle_ui_connect():
    logger.info(f"ASF: Client {request.sid} connected to UI updates namespace: {ASF_UI_UPDATES_NAMESPACE}")
    logger.info("ASF WebSocket connection", extra=dict(metric_name="asf_websocket_connection_count", value=1, tags={"namespace": ASF_UI_UPDATES_NAMESPACE, "status": "connected"}))
    emit(UI_EVENT_CONNECT_ACK, {'message': f'Connected to ASF UI updates on namespace {ASF_UI_UPDATES_NAMESPACE}.'})

@socketio.on('disconnect', namespace=ASF_UI_UPDATES_NAMESPACE)
def handle_ui_disconnect():
    logger.info(f"ASF: Client {request.sid} disconnected from UI updates namespace: {ASF_UI_UPDATES_NAMESPACE}")
    logger.info("ASF WebSocket connection", extra=dict(metric_name="asf_websocket_connection_count", value=1, tags={"namespace": ASF_UI_UPDATES_NAMESPACE, "status": "disconnected"}))

@socketio.on('subscribe_to_ui_updates', namespace=ASF_UI_UPDATES_NAMESPACE)
def handle_subscribe_ui_updates(data):
    client_id = data.get('client_id')
    if not client_id:
        logger.warning(f"ASF: Client {request.sid} attempted UI subscription without client_id.")
        emit(UI_EVENT_ERROR, {'message': 'client_id is required for UI update subscription.'})
        return
    join_room(client_id)
    logger.info(f"ASF: Client {request.sid} (client_id: {client_id}) subscribed to UI updates in room '{client_id}'.")
    emit(UI_EVENT_SUBSCRIBED, {'status': 'success', 'client_id': client_id, 'subscribed_to_room': client_id})

@app.route('/asf/internal/send_ui_update', methods=['POST'])
def send_ui_update():
    try: payload = request.get_json()
    except Exception: return jsonify({"error_code": "ASF_SENDUI_MALFORMED_JSON", "message": "Malformed JSON."}), 400
    if not payload: return jsonify({"error_code": "ASF_SENDUI_INVALID_PAYLOAD", "message": "Payload required."}), 400

    client_id = payload.get('client_id')
    event_name = payload.get('event_name')
    event_data = payload.get('data')

    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        return jsonify({"error_code": "ASF_SENDUI_INVALID_CLIENT_ID", "message": "'client_id' must be non-empty string."}), 400
    if not event_name or not isinstance(event_name, str) or not event_name.strip():
        return jsonify({"error_code": "ASF_SENDUI_INVALID_EVENT_NAME", "message": "'event_name' must be non-empty string."}), 400
    if 'data' not in payload: # Check for presence of 'data' key
        return jsonify({"error_code": "ASF_SENDUI_MISSING_DATA", "message": "'data' field required."}), 400

    if not ASF_UI_UPDATES_NAMESPACE:
        logger.error("ASF_SEND_UI: ASF_UI_UPDATES_NAMESPACE not configured. Cannot emit.")
        return jsonify({"error_code": "ASF_CONFIG_ERROR_UI_NAMESPACE", "message": "ASF server config error for UI namespace."}), 500

    try:
        logger.info(f"ASF_SEND_UI: Emitting '{event_name}' to client_id '{client_id}' in namespace '{ASF_UI_UPDATES_NAMESPACE}' with data: {event_data}")
        socketio.emit(event_name, event_data, room=client_id, namespace=ASF_UI_UPDATES_NAMESPACE)
        logger.info("ASF UI update relayed", extra=dict(metric_name="asf_ui_update_relayed_count", value=1, tags={"event_name": event_name}))
        return jsonify({"status": "success", "message": "UI update sent."}), 200
    except Exception as e:
        logger.error(f"ASF_SEND_UI: Failed to emit SocketIO event for client_id '{client_id}': {e}", exc_info=True)
        logger.error("ASF UI update relay failed", extra=dict(metric_name="asf_ui_update_relay_failed_count", value=1, tags={"event_name": event_name}))
        return jsonify({"error_code": "ASF_SOCKETIO_EMIT_FAILED", "message": "Failed to emit SocketIO event for UI update."}), 500

if __name__ == '__main__':
    if not ASF_UI_UPDATES_NAMESPACE: # Ensure it's set from config before running
        logger.critical("ASF_UI_UPDATES_NAMESPACE is not defined in config. UI updates will not work. Exiting.")
        exit(1) # Critical configuration missing

    asf_host = asf_config.get('ASF_HOST')
    asf_port = asf_config.get('ASF_PORT')
    # Read FLASK_DEBUG directly for running the app
    flask_debug_mode_run = os.getenv("FLASK_DEBUG", "false").lower() == 'true'

    logger.info(f"Starting AudioStreamFeeder (ASF) with Flask-SocketIO on {asf_host}:{asf_port} (Debug: {flask_debug_mode_run}).")
    # allow_unsafe_werkzeug=True is needed for Werkzeug dev server if debug is True and reloader is on.
    # Gunicorn with eventlet/gevent worker is preferred for production.
    socketio.run(app, host=asf_host, port=asf_port, debug=flask_debug_mode_run,
                 allow_unsafe_werkzeug=True if flask_debug_mode_run else False,
                 # Consider use_reloader=False if issues with background threads or state
                 )
