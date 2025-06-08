#!/bin/bash

echo "Goal: Start VFA, send POST requests for different scenarios, show responses, then stop VFA."

echo "Step 1: Start VFA service..."
# VFA depends on AIMS_TTS_Service, but if VFA_TEST_MODE_ENABLED=True, it won't call it.
docker-compose up -d vfa
if [ $? -ne 0 ]; then echo "Failed to start VFA"; exit 1; fi

echo "Step 2: Wait for VFA (15 seconds)..."
sleep 15

# --- Test Scenario 1: Default (Successful Test Mode Synthesis) ---
echo "Step 3.1: Send POST request to VFA (/forge_voice) - Default Scenario..."
# VFA listens on port 5005
REQUEST_PAYLOAD_DEFAULT='{
    "script": {
        "script_id": "test_script_001",
        "topic": "Valid Test Script",
        "title": "A Valid Script for Testing",
        "segments": [
            {"segment_title": "INTRO", "content": "This is a valid introduction for testing purposes."},
            {"segment_title": "MAIN", "content": "This is the main content, long enough to pass minimum length requirements."}
        ]
    },
    "voice_params": {
        "voice_name": "test-voice-accent",
        "audio_encoding": "mp3"
    }
}'
RESPONSE_DEFAULT=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -d "${REQUEST_PAYLOAD_DEFAULT}" http://localhost:5005/forge_voice)

echo ""
echo "--- VFA Response (Default Scenario) ---"
echo "${RESPONSE_DEFAULT}"
echo "--- End VFA Response (Default Scenario) ---"
echo ""

if echo "${RESPONSE_DEFAULT}" | grep -q "\"status\":\"success\"" && echo "${RESPONSE_DEFAULT}" | grep -q "audio_filepath" && echo "${RESPONSE_DEFAULT}" | grep -q "test_mode_bypassed_aims_tts" && echo "${RESPONSE_DEFAULT}" | grep -q "HTTP_STATUS_CODE:200"; then
  echo "VFA API Test (Default Scenario): PRELIMINARY PASS."
else
  echo "VFA API Test (Default Scenario): PRELIMINARY FAIL."
fi
echo ""

# --- Test Scenario 2: Simulate AIMS_TTS Error ---
echo "Step 3.2: Send POST request to VFA (/forge_voice) - Simulate AIMS_TTS Error Scenario..."
RESPONSE_AIMS_ERROR=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -H "X-Test-Scenario: vfa_error_aims_tts" -d "${REQUEST_PAYLOAD_DEFAULT}" http://localhost:5005/forge_voice) # Re-use default payload

echo ""
echo "--- VFA Response (AIMS_TTS Error Scenario) ---"
echo "${RESPONSE_AIMS_ERROR}"
echo "--- End VFA Response (AIMS_TTS Error Scenario) ---"
echo ""

if echo "${RESPONSE_AIMS_ERROR}" | grep -q "\"error_code\":\"VFA_TEST_MODE_AIMS_TTS_ERROR\"" && echo "${RESPONSE_AIMS_ERROR}" | grep -q "\"engine_used\":\"test_mode_aims_tts_error\"" && echo "${RESPONSE_AIMS_ERROR}" | grep -q "HTTP_STATUS_CODE:500"; then # VFA main.py returns 500 for this test scenario
  echo "VFA API Test (AIMS_TTS Error Scenario): PRELIMINARY PASS."
else
  echo "VFA API Test (AIMS_TTS Error Scenario): PRELIMINARY FAIL."
fi
echo ""

# --- Test Scenario 3: Simulate VFA File Save Error ---
echo "Step 3.3: Send POST request to VFA (/forge_voice) - Simulate File Save Error Scenario..."
RESPONSE_FILE_ERROR=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -H "X-Test-Scenario: vfa_error_file_save" -d "${REQUEST_PAYLOAD_DEFAULT}" http://localhost:5005/forge_voice) # Re-use default payload

echo ""
echo "--- VFA Response (File Save Error Scenario) ---"
echo "${RESPONSE_FILE_ERROR}"
echo "--- End VFA Response (File Save Error Scenario) ---"
echo ""

if echo "${RESPONSE_FILE_ERROR}" | grep -q "\"error_code\":\"VFA_TEST_MODE_FILE_SAVE_ERROR\"" && echo "${RESPONSE_FILE_ERROR}" | grep -q "\"engine_used\":\"test_mode_dummy_file_error\"" && echo "${RESPONSE_FILE_ERROR}" | grep -q "HTTP_STATUS_CODE:500"; then # VFA main.py returns 500
  echo "VFA API Test (File Save Error Scenario): PRELIMINARY PASS."
else
  echo "VFA API Test (File Save Error Scenario): PRELIMINARY FAIL."
fi
echo ""

# --- Test Scenario 4: Script Too Short ---
echo "Step 3.4: Send POST request to VFA (/forge_voice) - Script Too Short Scenario..."
REQUEST_PAYLOAD_SHORT='{
    "script": {
        "script_id": "test_script_002",
        "topic": "Short Script",
        "title": "Too Short",
        "segments": [{"segment_title": "INTRO", "content": "Brief."}]
    }
}' # VFA_MIN_SCRIPT_LENGTH is 20 by default, "Brief." is too short.
RESPONSE_SHORT_SCRIPT=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -d "${REQUEST_PAYLOAD_SHORT}" http://localhost:5005/forge_voice)

echo ""
echo "--- VFA Response (Script Too Short Scenario) ---"
echo "${RESPONSE_SHORT_SCRIPT}"
echo "--- End VFA Response (Script Too Short Scenario) ---"
echo ""

if echo "${RESPONSE_SHORT_SCRIPT}" | grep -q "\"status\":\"skipped\"" && echo "${RESPONSE_SHORT_SCRIPT}" | grep -q "Text too short" && echo "${RESPONSE_SHORT_SCRIPT}" | grep -q "HTTP_STATUS_CODE:200"; then # Skipped is a 200 OK
  echo "VFA API Test (Script Too Short Scenario): PRELIMINARY PASS."
else
  echo "VFA API Test (Script Too Short Scenario): PRELIMINARY FAIL."
fi
echo ""

echo "Step 4: Stop VFA service..."
docker-compose down --remove-orphans > /dev/null 2>&1

echo "VFA API tests completed."
