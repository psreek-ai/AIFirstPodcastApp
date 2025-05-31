# Aethercast ASF (AudioStreamFeeder) WebSocket Test Client
#
# This script connects to the ASF WebSocket endpoint, joins a specific stream,
# and receives audio chunks. It can optionally save the received audio to a file.
#
# Installation:
# pip install websocket-client
#
# Usage:
# python utils/asf_client_tester.py --stream_id <your_stream_id>
#   [--ws_url <websocket_url>]
#   [--output_file <filename.mp3>]
#
# Example:
# python utils/asf_client_tester.py --stream_id strm_12345abcdef --output_file received.mp3
# python utils/asf_client_tester.py --stream_id strm_12345abcdef --ws_url ws://custom.host:5006/api/v1/podcasts/stream

import websocket
import threading
import time
import json
import argparse
import os

# --- Global Variables ---
# These will be set by command-line arguments
ASF_DEFAULT_WEBSOCKET_URL = "ws://localhost:5006/api/v1/podcasts/stream"
DEFAULT_STREAM_ID = "test_stream_123" # Default if not provided
DEFAULT_OUTPUT_FILENAME = None # No saving by default

# --- WebSocket Event Handlers ---

def on_open(ws):
    print("## Connection opened. ##")
    print(f"Attempting to join stream: {ws.stream_id}")
    join_message = {"stream_id": ws.stream_id}
    ws.send(json.dumps(join_message))

    if ws.output_filename:
        try:
            # Ensure directory exists if filename includes path
            output_dir = os.path.dirname(ws.output_filename)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir)
                print(f"Created directory: {output_dir}")
            ws.audio_file = open(ws.output_filename, "wb")
            print(f"Saving received audio to: {ws.output_filename}")
        except IOError as e:
            print(f"Error opening output file {ws.output_filename}: {e}")
            ws.audio_file = None # Ensure it's None if open failed
            # Optionally, could close WebSocket connection here if file saving is critical
            # ws.close()


def on_message(ws, message):
    if isinstance(message, bytes):
        print(f"Received audio chunk of {len(message)} bytes.")
        if hasattr(ws, 'audio_file') and ws.audio_file:
            try:
                ws.audio_file.write(message)
            except IOError as e:
                print(f"Error writing to audio file: {e}")
                # Consider how to handle this - maybe stop trying to write or close file
    else:
        try:
            data = json.loads(message)
            print(f"Received JSON: {data}")

            if data.get('event') == 'end_of_stream':
                print("== End of Stream signal received from server. ==")
                # The server signals end, client can choose to close or wait.
                # Closing the file here is an option, or in on_close.
                # If server closes connection after this, on_close will handle it.
            elif data.get('message') == 'Connected to ASF. Please send join_stream with your stream_id.':
                print("Server Acknowledged Connection. Join message sent from on_open.")
            elif data.get('status') == 'joined':
                 print(f"Successfully joined stream: {data.get('stream_id')}. Message: {data.get('message')}")
            elif data.get('message') and 'Audio stream ID not found' in data.get('message'):
                 print(f"ERROR from server: {data.get('message')}")
                 print("Client will close connection due to stream ID error.")
                 ws.close() # Close connection if stream_id is invalid
            elif data.get('message') and 'Audio file unavailable' in data.get('message'):
                 print(f"ERROR from server: {data.get('message')}")
                 print("Client will close connection due to file unavailable error.")
                 ws.close()

        except json.JSONDecodeError:
            print(f"Received non-JSON text message: {message}")

def on_error(ws, error):
    print(f"## Error: {error} ##")

def on_close(ws, close_status_code, close_msg):
    print("## Connection closed. ##")
    if close_status_code or close_msg:
        print(f"Status Code: {close_status_code}")
        print(f"Close Message: {close_msg}")

    if hasattr(ws, 'audio_file') and ws.audio_file:
        try:
            ws.audio_file.close()
            print(f"Audio file {ws.output_filename} closed.")
        except IOError as e:
            print(f"Error closing audio file {ws.output_filename}: {e}")

# --- Main Execution ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aethercast ASF WebSocket Test Client")
    parser.add_argument("--ws_url", type=str, default=os.getenv("ASF_WEBSOCKET_URL", ASF_DEFAULT_WEBSOCKET_URL),
                        help=f"WebSocket URL for ASF. Default: {ASF_DEFAULT_WEBSOCKET_URL} (or from ASF_WEBSOCKET_URL env var)")
    parser.add_argument("--stream_id", type=str, default=os.getenv("STREAM_ID", DEFAULT_STREAM_ID),
                        help=f"Stream ID to join. Default: {DEFAULT_STREAM_ID} (or from STREAM_ID env var)")
    parser.add_argument("--output_file", type=str, default=os.getenv("OUTPUT_FILENAME", DEFAULT_OUTPUT_FILENAME),
                        help="Filename to save the received audio stream (e.g., received.mp3). Default: No saving. (or from OUTPUT_FILENAME env var)")

    args = parser.parse_args()

    if not args.stream_id:
        print("Error: --stream_id is required.")
        parser.print_help()
        exit(1)

    print(f"Connecting to ASF at: {args.ws_url}")
    print(f"Attempting to join stream_id: {args.stream_id}")
    if args.output_file:
        print(f"Will save received audio to: {args.output_file}")
    else:
        print("Audio will not be saved (no --output_file specified).")

    # websocket.enableTrace(True) # Uncomment for detailed WebSocket logs

    ws_app = websocket.WebSocketApp(args.ws_url,
                                  on_open=on_open,
                                  on_message=on_message,
                                  on_error=on_error,
                                  on_close=on_close)

    # Pass custom attributes to the WebSocketApp instance for use in handlers
    ws_app.stream_id = args.stream_id
    ws_app.output_filename = args.output_file
    ws_app.audio_file = None # Initialize to None, will be opened in on_open if filename provided

    # Run the client. This will block until the connection is closed.
    # To run in a separate thread (e.g., for graceful shutdown logic later):
    # ws_thread = threading.Thread(target=ws_app.run_forever)
    # ws_thread.daemon = True # Optional: if you want main thread to exit even if ws_thread is running
    # ws_thread.start()
    # try:
    #     while ws_thread.is_alive():
    #         time.sleep(1)
    # except KeyboardInterrupt:
    #     print("Keyboard interrupt received. Closing WebSocket...")
    #     ws_app.close()
    #     ws_thread.join(timeout=5) # Wait for thread to finish
    # print("Client finished.")

    try:
        ws_app.run_forever()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received. Closing WebSocket...")
    finally:
        # ws_app.close() is implicitly called by run_forever() on exit/error,
        # but explicit close in on_close handler for the file is important.
        # If ws_app.close() was not called yet due to abrupt stop, ensure file is closed.
        if hasattr(ws_app, 'audio_file') and ws_app.audio_file and not ws_app.audio_file.closed:
            print("Ensuring audio file is closed due to program interruption...")
            ws_app.audio_file.close()
            print(f"Audio file {ws_app.output_filename} closed.")
    print("Client has shut down.")
