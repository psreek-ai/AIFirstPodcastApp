FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the entire aethercast directory to /app in the image
# This ensures that any shared modules within aethercast (if any in future) are available
# and that imports like 'from aethercast.tda...' work as expected.
COPY ./aethercast /app/aethercast

# Install requirements for TDA
COPY ./aethercast/tda/requirements.txt /app/requirements_tda.txt
RUN pip install --no-cache-dir -r /app/requirements_tda.txt

ENV PYTHONPATH /app

CMD ["python", "aethercast/tda/main.py"]
