FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the entire aethercast directory to /app in the image
COPY ./aethercast /app/aethercast

# Install requirements for PSWA
COPY ./aethercast/pswa/requirements.txt /app/requirements_pswa.txt
RUN pip install --no-cache-dir -r /app/requirements_pswa.txt

ENV PYTHONPATH /app

CMD ["python", "aethercast/pswa/main.py"]
