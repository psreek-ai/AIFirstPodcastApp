FROM python:3.9-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Copy requirements first for better layer caching
COPY ./aethercast/api_gateway/requirements.txt /app/requirements_api_gw.txt
RUN pip install --no-cache-dir -r /app/requirements_api_gw.txt

# API_GW also bundles CPOA, and CPOA uses WCHA. Install their requirements.
COPY ./aethercast/cpoa/requirements.txt /app/requirements_cpoa.txt
RUN pip install --no-cache-dir -r /app/requirements_cpoa.txt
COPY ./aethercast/wcha/requirements.txt /app/requirements_wcha.txt
RUN pip install --no-cache-dir -r /app/requirements_wcha.txt

# Then copy the application code
# This assumes the build context in docker-compose.yml is the project root (where the top-level 'aethercast' directory resides)
COPY ./aethercast /app/aethercast
ENV PYTHONPATH /app

EXPOSE 5001

# For production, use a production-ready WSGI server like Gunicorn.
# Example (ensure gunicorn is in requirements_api_gw.txt):
# CMD ["gunicorn", "--bind", "0.0.0.0:5001", "aethercast.api_gateway.main:app"]

# Run main.py when the container launches (Flask development server)
CMD ["python", "aethercast/api_gateway/main.py"]
