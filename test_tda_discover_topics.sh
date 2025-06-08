#!/bin/bash

# Start the TDA service in detached mode
echo "Starting TDA service..."
docker-compose up -d tda

# Wait for the service to be healthy (simple sleep, can be improved with health check if available)
echo "Waiting for TDA to start..."
sleep 10 # Adjust sleep time as needed

# Send a POST request to the /discover_topics endpoint
# TDA listens on port 5000 as per docker-compose.yml and its updated README
echo "Sending POST request to TDA's /discover_topics..."
curl -X POST -H "Content-Type: application/json" \
     -d '{"query": "AI in education", "limit": 2}' \
     http://localhost:5000/discover_topics

echo "" # Newline for better output formatting

# Stop the TDA service
echo "Stopping TDA service..."
docker-compose down
