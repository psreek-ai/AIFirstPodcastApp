#!/bin/bash

set -e

echo "Starting Aethercast deployment..."

# 1. Check for dependencies
if ! command -v docker &> /dev/null
then
    echo "Docker could not be found. Please install Docker and try again."
    exit 1
fi

if ! docker compose version &> /dev/null
then
    echo "Docker Compose V2 could not be found. Please install Docker Compose V2 and try again."
    exit 1
fi

echo "Dependencies checked."

# 2. Create .env files
echo "Creating .env files from .env.example files..."
for f in $(find . -name ".env.example"); do
  cp -n "$f" "${f%.example}"
done
echo ".env files created."

# 3. Handle GCP credentials
echo "Checking for GCP credentials..."
for dir in aethercast/aims_service aethercast/aims_tts_service aethercast/iga aethercast/api_gateway; do
  if [ ! -f "$dir/gcp-credentials.json" ]; then
    echo "Creating placeholder gcp-credentials.json for $dir"
    echo '{}' > "$dir/gcp-credentials.json"
  fi
done
echo "GCP credentials checked."

# 4. Start services
echo "Starting Docker Compose services..."
docker compose up -d --build
echo "Docker Compose services started."

# 5. Un-skip and run integration tests
echo "Un-skipping integration tests..."
sed -i "s/@unittest.skip(\"Skipping full integration tests in this environment\")/ /" tests/integration/test_full_flow.py

echo "Running integration tests..."
python -m unittest tests/integration/test_full_flow.py

echo "Deployment successful!"
