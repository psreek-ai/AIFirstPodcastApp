#!/bin/bash

echo "Goal: Start SCA, send a POST request to /craft_snippet, show response, then stop SCA."

echo "Step 1: Start SCA service..."
# SCA depends on AIMS_Service in its docker-compose definition, but if USE_REAL_LLM_SERVICE=false,
# it shouldn't try to call it. We only need to bring up SCA.
docker-compose up -d sca
if [ $? -ne 0 ]; then echo "Failed to start SCA"; exit 1; fi

echo "Step 2: Wait for SCA (15 seconds)..."
sleep 15

echo "Step 3: Send POST request to SCA..."
# SCA listens on port 5002
REQUEST_PAYLOAD='{
    "topic_id": "topic_test_123",
    "content_brief": "The Future of Work with AI",
    "topic_info": {
        "title_suggestion": "The Future of Work with AI",
        "summary": "Exploring how AI is reshaping jobs, skills, and the workplace.",
        "keywords": ["AI", "future of work", "automation", "skills gap"],
        "potential_sources": [{"title": "Report on AI in the Workplace 2024"}]
    }
}'
ACTUAL_RESPONSE=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -d "${REQUEST_PAYLOAD}" http://localhost:5002/craft_snippet)

echo ""
echo "--- SCA Response ---"
echo "${ACTUAL_RESPONSE}"
echo "--- End SCA Response ---"
echo ""

echo "Step 4: Stop SCA service..."
docker-compose down --remove-orphans > /dev/null 2>&1

# Basic validation
if echo "${ACTUAL_RESPONSE}" | grep -q "snippet_id" && echo "${ACTUAL_RESPONSE}" | grep -q "HTTP_STATUS_CODE:200"; then
  echo "SCA API Test Result: PRELIMINARY PASS (found 'snippet_id' and HTTP 200)."
else
  echo "SCA API Test Result: PRELIMINARY FAIL (did not find 'snippet_id' or HTTP 200)."
fi
