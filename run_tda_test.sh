#!/bin/bash

# Start the TDA service in detached mode
echo "Starting TDA service..."
docker-compose up -d tda

# Wait for the service to be healthy
echo "Waiting for TDA to start (15 seconds)..."
sleep 15 # Increased sleep time slightly

# Send a POST request to the /discover_topics endpoint
echo "Sending POST request to TDA's /discover_topics..."
response=$(curl -s -X POST -H "Content-Type: application/json" \
     -d '{"query": "AI in education", "limit": 2}' \
     http://localhost:5000/discover_topics)

echo "" # Newline for better output formatting
echo "Response from TDA:"
echo "${response}"
echo ""

# Stop the TDA service and remove orphaned containers
echo "Stopping TDA service..."
docker-compose down --remove-orphans

# Basic validation of the response (example)
if echo "${response}" | grep -q "discovered_topics"; then
  echo "TDA API Test: PASSED - 'discovered_topics' key found in response."
else
  echo "TDA API Test: FAILED - 'discovered_topics' key NOT found in response."
  # exit 1 # Optionally exit with error
fi
