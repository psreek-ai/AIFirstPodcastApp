FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the entire aethercast directory to /app in the image
# This makes all submodules like cpoa, common, etc., available
# The paths in the application code (e.g., from aethercast.cpoa.main)
# should then work correctly provided /app is in PYTHONPATH (which it typically is when WORKDIR is /app)
COPY ./aethercast /app/aethercast

# Install requirements for API Gateway
# Ensure this path is correct relative to the build context defined in docker-compose.yml
# If build context is ./aethercast/api_gateway, then requirements.txt should be at the root of that context.
# For simplicity, let's assume the build context for each service will be its own directory.
# So, this Dockerfile will be in ./aethercast/api_gateway/
COPY ./aethercast/api_gateway/requirements.txt /app/requirements_api_gw.txt
RUN pip install --no-cache-dir -r /app/requirements_api_gw.txt

# Install requirements for CPOA (as it's bundled with API Gateway)
# Ensure this path is correct.
COPY ./aethercast/cpoa/requirements.txt /app/requirements_cpoa.txt
RUN pip install --no-cache-dir -r /app/requirements_cpoa.txt

# Set PYTHONPATH if necessary, though WORKDIR /app usually adds /app to it.
# Explicitly adding /app ensures modules within aethercast can be found.
ENV PYTHONPATH /app

# Command to run the API Gateway application
CMD ["python", "aethercast/api_gateway/main.py"]
