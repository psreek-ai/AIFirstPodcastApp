#!/bin/bash

echo "Goal: Start TDA, send a POST request to /discover_topics, show response, then stop TDA."

echo "Step 1: Start TDA service..."
docker-compose up -d tda
if [ $? -ne 0 ]; then echo "Failed to start TDA"; exit 1; fi

echo "Step 2: Wait for TDA (20 seconds)..."
sleep 20

echo "Step 3: Send POST request to TDA..."
# Use localhost:5000 for TDA. Capture output.
ACTUAL_RESPONSE=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -d '{"query": "AI in education", "limit": 2}' http://localhost:5000/discover_topics)

echo ""
echo "--- TDA Response ---"
echo "${ACTUAL_RESPONSE}"
echo "--- End TDA Response ---"
echo ""

echo "Step 4: Stop TDA service..."
docker-compose down --remove-orphans > /dev/null 2>&1 # Suppress output for down

# Basic validation based on expected output structure
if echo "${ACTUAL_RESPONSE}" | grep -q "discovered_topics" && echo "${ACTUAL_RESPONSE}" | grep -q "HTTP_STATUS_CODE:200"; then
  echo "TDA API Test Result: PRELIMINARY PASS (found 'discovered_topics' and HTTP 200)."
else
  echo "TDA API Test Result: PRELIMINARY FAIL (did not find 'discovered_topics' or HTTP 200)."
fi
