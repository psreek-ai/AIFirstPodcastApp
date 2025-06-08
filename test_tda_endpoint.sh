#!/bin/bash

echo "Starting TDA service..."
docker-compose up -d tda

echo "Waiting for TDA to start (15 seconds)..."
sleep 15

echo "Sending POST request to TDA's /discover_topics..."
# Send request and capture HTTP status code and body separately
# Store response body in a variable
response_body=$(mktemp)
http_status=$(curl -s -w "%{http_code}" -X POST -H "Content-Type: application/json" \
     -d '{"query": "AI in education", "limit": 2}' \
     http://localhost:5000/discover_topics \
     -o ${response_body})

echo ""
echo "HTTP Status Code: ${http_status}"
echo "Response from TDA:"
cat ${response_body}
echo ""
echo ""

# Cleanup temp file
rm -f ${response_body}

# Stop the TDA service and remove orphaned containers
echo "Stopping TDA service..."
docker-compose down --remove-orphans

if [ "${http_status}" -eq 200 ]; then
  echo "TDA API Test: PASSED - HTTP status code is 200."
else
  echo "TDA API Test: FAILED - HTTP status code is ${http_status}."
  # exit 1 # Optionally exit with error
fi
