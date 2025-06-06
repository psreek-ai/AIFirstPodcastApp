import os
import uuid
import logging # Added for better logging
from flask import Flask, jsonify, request
from dotenv import load_dotenv
import openai # Added

# --- Load Environment Variables ---
load_dotenv()

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Configuration ---
# Use Flask's logger if available and not the root logger to avoid duplicate messages when running with Flask.
if app.logger and app.logger.name != 'root':
    logger = app.logger
else:
    logger = logging.getLogger(__name__)
    if not logger.hasHandlers(): # Avoid adding handlers if already configured (e.g., by Flask/Gunicorn)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - AIMS - %(message)s')

# --- AIMS Configuration ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AIMS_LLM_MODEL_ID = os.getenv("AIMS_LLM_MODEL_ID", "gpt-3.5-turbo") # Default model

logger.info("--- AIMS Service Configuration ---")
if OPENAI_API_KEY:
    logger.info(f"  OPENAI_API_KEY: Loaded (masked length: {len(OPENAI_API_KEY[:4])}****)")
else:
    logger.error("CRITICAL: OPENAI_API_KEY is not set. Real LLM calls will fail.")
    # Consider raising an error here or preventing app start if API key is mandatory for all operations.
    # For now, it will allow starting but /v1/generate will fail if key is missing.
logger.info(f"  AIMS_LLM_MODEL_ID (default): {AIMS_LLM_MODEL_ID}")
logger.info("--- End AIMS Service Configuration ---")

# Set OpenAI API key globally for the openai library
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    # App will run, but API calls will fail if this is not set.
    # The /v1/generate endpoint will check this before making a call.
    pass


@app.route('/v1/generate', methods=['POST'])
def generate_text():
    request_id = f"aims_req_{uuid.uuid4().hex}"
    logger.info(f"Request {request_id}: Received /v1/generate request.")

    if not OPENAI_API_KEY:
        logger.error(f"Request {request_id}: OpenAI API key not configured. Cannot process request.")
        return jsonify({
            "request_id": request_id,
            "error": {
                "type": "configuration_error",
                "message": "Service not configured. OpenAI API key is missing."
            }
        }), 503 # Service Unavailable

    try:
        data = request.get_json()
        if not data:
            logger.warning(f"Request {request_id}: No JSON payload received.")
            return jsonify({
                "request_id": request_id,
                "error": {"type": "invalid_request_error", "message": "No JSON payload received."}
            }), 400
    except Exception as e:
        logger.warning(f"Request {request_id}: Error parsing JSON payload: {e}")
        return jsonify({
            "request_id": request_id,
            "error": {"type": "invalid_request_error", "message": f"Invalid JSON payload: {str(e)}"}
        }), 400


    prompt = data.get("prompt")
    if not prompt:
        logger.warning(f"Request {request_id}: Missing 'prompt' in request payload.")
        return jsonify({
            "request_id": request_id,
            "error": {"type": "invalid_request_error", "message": "Missing 'prompt' in request payload."}
        }), 400

    model_override = data.get("model_id_override", data.get("model")) # Accept "model" for broader compatibility
    max_tokens = data.get("max_tokens", 150) # Default max_tokens
    temperature = data.get("temperature", 0.7) # Default temperature
    response_format_type = data.get("response_format", {}).get("type") # e.g. {"type": "json_object"}

    actual_model_to_use = model_override if model_override else AIMS_LLM_MODEL_ID

    logger.info(f"Request {request_id}: Using model '{actual_model_to_use}'. Prompt (first 50 chars): '{prompt[:50]}...'")

    messages = [{"role": "user", "content": prompt}]
    # Optional: Add a system prompt if AIMS should enforce one
    # messages.insert(0, {"role": "system", "content": "You are a helpful assistant."})

    openai_call_params = {
        "model": actual_model_to_use,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # Handle response_format for JSON mode if supported by the model
    # Note: Not all models support response_format. This is a simplified check.
    # A more robust solution might check model capabilities if known.
    if response_format_type == "json_object" and ("gpt-4-1106-preview" in actual_model_to_use or "gpt-3.5-turbo-0125" in actual_model_to_use or "gpt-4-turbo" in actual_model_to_use):
        openai_call_params["response_format"] = {"type": "json_object"}
        logger.info(f"Request {request_id}: Requesting JSON object response format from LLM.")
    elif response_format_type == "json_object":
         logger.warning(f"Request {request_id}: JSON object response format requested, but model '{actual_model_to_use}' might not support it or is not explicitly handled. Proceeding without response_format.")


    try:
        logger.debug(f"Request {request_id}: Making OpenAI API call with params: {openai_call_params}")
        completion = openai.ChatCompletion.create(**openai_call_params)
        logger.info(f"Request {request_id}: OpenAI API call successful.")

        generated_text = completion.choices[0].message['content']
        finish_reason = completion.choices[0].finish_reason
        usage_data = completion.usage
        model_used = completion.model # Actual model string returned by OpenAI

        response_payload = {
            "request_id": request_id,
            "model_id": model_used,
            "choices": [
                {
                    "text": generated_text,
                    "finish_reason": finish_reason
                }
            ],
            "usage": {
                "prompt_tokens": usage_data['prompt_tokens'],
                "completion_tokens": usage_data['completion_tokens'],
                "total_tokens": usage_data['total_tokens']
            }
        }
        return jsonify(response_payload), 200

    except openai.error.APIError as e:
        logger.error(f"Request {request_id}: OpenAI API Error: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "api_error", "message": str(e)}}), e.http_status or 500
    except openai.error.AuthenticationError as e:
        logger.error(f"Request {request_id}: OpenAI Authentication Error: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "authentication_error", "message": str(e)}}), e.http_status or 401
    except openai.error.RateLimitError as e:
        logger.error(f"Request {request_id}: OpenAI Rate Limit Error: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "rate_limit_error", "message": str(e)}}), e.http_status or 429
    except openai.error.InvalidRequestError as e:
        logger.error(f"Request {request_id}: OpenAI Invalid Request Error: {e}", exc_info=True)
        # Check if the error is due to response_format not being supported by the model
        if "response_format" in str(e) and "is not supported with this model" in str(e):
             return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": f"The model '{actual_model_to_use}' does not support the requested 'response_format'. Error: {str(e)}" }}), 400
        return jsonify({"request_id": request_id, "error": {"type": "invalid_request_error", "message": str(e)}}), e.http_status or 400
    except openai.error.OpenAIError as e: # Catch other OpenAI specific errors
        logger.error(f"Request {request_id}: Generic OpenAI Error: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "openai_error", "message": str(e)}}), e.http_status or 500
    except Exception as e:
        logger.error(f"Request {request_id}: Unexpected error during LLM call: {e}", exc_info=True)
        return jsonify({"request_id": request_id, "error": {"type": "internal_server_error", "message": "An unexpected error occurred on the server."}}), 500

if __name__ == '__main__':
    host = os.getenv('AIMS_HOST', '0.0.0.0')
    port = int(os.getenv('AIMS_PORT', 8000))
    # FLASK_DEBUG from .env is automatically picked up by Flask if app.run(debug=None)
    # Or, explicitly pass it:
    debug_mode_str = os.getenv('FLASK_DEBUG', 'False').lower()
    debug_mode = debug_mode_str == 'true'

    if not OPENAI_API_KEY:
        print("WARNING: OPENAI_API_KEY is not set. The AIMS service /v1/generate endpoint will return errors.")

    print(f"--- AIMS Service starting on {host}:{port} (Debug: {debug_mode}) ---")
    # When using `flask run`, it picks up FLASK_DEBUG. For `python main.py`, set it explicitly.
    app.run(host=host, port=port, debug=debug_mode)
