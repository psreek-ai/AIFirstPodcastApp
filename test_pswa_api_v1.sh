#!/bin/bash

echo "Goal: Start PSWA, send POST requests for different test scenarios, show responses, then stop PSWA."

echo "Step 1: Start PSWA service..."
# PSWA depends on AIMS_Service in docker-compose, but if PSWA_TEST_MODE_ENABLED=True, it won't call it.
docker-compose up -d pswa
if [ $? -ne 0 ]; then echo "Failed to start PSWA"; exit 1; fi

echo "Step 2: Wait for PSWA (15 seconds)..."
sleep 15

# --- Test Scenario 1: Default ---
echo "Step 3.1: Send POST request to PSWA (/weave_script) - Default Scenario..."
# PSWA listens on port 5004
REQUEST_PAYLOAD_DEFAULT='{
    "content": "Some sample content about AI.",
    "topic": "AI Explained"
}'
RESPONSE_DEFAULT=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -d "${REQUEST_PAYLOAD_DEFAULT}" http://localhost:5004/weave_script)

echo ""
echo "--- PSWA Response (Default Scenario) ---"
echo "${RESPONSE_DEFAULT}"
echo "--- End PSWA Response (Default Scenario) ---"
echo ""

if echo "${RESPONSE_DEFAULT}" | grep -q "script_id" && echo "${RESPONSE_DEFAULT}" | grep -q "Test Mode Default Title" && echo "${RESPONSE_DEFAULT}" | grep -q "HTTP_STATUS_CODE:200"; then
  echo "PSWA API Test (Default Scenario): PRELIMINARY PASS."
else
  echo "PSWA API Test (Default Scenario): PRELIMINARY FAIL."
fi
echo ""

# --- Test Scenario 2: Insufficient Content ---
echo "Step 3.2: Send POST request to PSWA (/weave_script) - Insufficient Content Scenario..."
REQUEST_PAYLOAD_INSUFFICIENT='{
    "content": "Too short.",
    "topic": "Brief Topic"
}'
RESPONSE_INSUFFICIENT=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -H "X-Test-Scenario: insufficient_content" -d "${REQUEST_PAYLOAD_INSUFFICIENT}" http://localhost:5004/weave_script)

echo ""
echo "--- PSWA Response (Insufficient Content Scenario) ---"
echo "${RESPONSE_INSUFFICIENT}"
echo "--- End PSWA Response (Insufficient Content Scenario) ---"
echo ""

if echo "${RESPONSE_INSUFFICIENT}" | grep -q "script_id" && echo "${RESPONSE_INSUFFICIENT}" | grep -q "Insufficient content for test topic" && echo "${RESPONSE_INSUFFICIENT}" | grep -q "HTTP_STATUS_CODE:200"; then
  echo "PSWA API Test (Insufficient Content): PRELIMINARY PASS."
else
  echo "PSWA API Test (Insufficient Content): PRELIMINARY FAIL."
fi
echo ""

# --- Test Scenario 3: Empty Segments ---
echo "Step 3.3: Send POST request to PSWA (/weave_script) - Empty Segments Scenario..."
REQUEST_PAYLOAD_EMPTY='{
    "content": "Some content.",
    "topic": "Empty Segments Topic"
}'
RESPONSE_EMPTY=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -H "X-Test-Scenario: empty_segments" -d "${REQUEST_PAYLOAD_EMPTY}" http://localhost:5004/weave_script)

echo ""
echo "--- PSWA Response (Empty Segments Scenario) ---"
echo "${RESPONSE_EMPTY}"
echo "--- End PSWA Response (Empty Segments Scenario) ---"
echo ""

if echo "${RESPONSE_EMPTY}" | grep -q "script_id" && echo "${RESPONSE_EMPTY}" | grep -q "Empty Segments" && echo "${RESPONSE_EMPTY}" | grep -q "\"segments\": \[\]" && echo "${RESPONSE_EMPTY}" | grep -q "HTTP_STATUS_CODE:200"; then
  echo "PSWA API Test (Empty Segments): PRELIMINARY PASS."
else
  echo "PSWA API Test (Empty Segments): PRELIMINARY FAIL."
fi
echo ""

echo "Step 4: Stop PSWA service..."
docker-compose down --remove-orphans > /dev/null 2>&1

echo "PSWA API tests completed."
