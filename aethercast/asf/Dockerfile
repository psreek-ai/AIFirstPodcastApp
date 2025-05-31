FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the entire aethercast directory to /app in the image
COPY ./aethercast /app/aethercast

# Install requirements for ASF
COPY ./aethercast/asf/requirements.txt /app/requirements_asf.txt
RUN pip install --no-cache-dir -r /app/requirements_asf.txt

ENV PYTHONPATH /app

CMD ["python", "aethercast/asf/main.py"]
