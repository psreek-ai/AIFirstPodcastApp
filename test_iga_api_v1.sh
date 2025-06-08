#!/bin/bash

echo "Goal: Start IGA, send a POST request to /generate_image, show response, then stop IGA."

echo "Step 1: Start IGA service..."
docker-compose up -d iga
if [ $? -ne 0 ]; then echo "Failed to start IGA"; exit 1; fi

echo "Step 2: Wait for IGA (15 seconds)..."
sleep 15

echo "Step 3: Send POST request to IGA..."
# IGA listens on port 5007
REQUEST_PAYLOAD='{
    "prompt": "A futuristic cityscape with flying cars"
}'
ACTUAL_RESPONSE=$(curl -s -w "\nHTTP_STATUS_CODE:%{http_code}\n" -X POST -H "Content-Type: application/json" -d "${REQUEST_PAYLOAD}" http://localhost:5007/generate_image)

echo ""
echo "--- IGA Response ---"
echo "${ACTUAL_RESPONSE}"
echo "--- End IGA Response ---"
echo ""

echo "Step 4: Stop IGA service..."
docker-compose down --remove-orphans > /dev/null 2>&1

# Basic validation
if echo "${ACTUAL_RESPONSE}" | grep -q "image_url" && echo "${ACTUAL_RESPONSE}" | grep -q "unsplash.com" && echo "${ACTUAL_RESPONSE}" | grep -q "HTTP_STATUS_CODE:200"; then
  echo "IGA API Test Result: PRELIMINARY PASS (found 'image_url' with 'unsplash.com' and HTTP 200)."
else
  echo "IGA API Test Result: PRELIMINARY FAIL (conditions not met)."
fi
