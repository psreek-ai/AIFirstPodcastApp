FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the entire aethercast directory to /app in the image
COPY ./aethercast /app/aethercast

# Install requirements for SCA
COPY ./aethercast/sca/requirements.txt /app/requirements_sca.txt
RUN pip install --no-cache-dir -r /app/requirements_sca.txt

ENV PYTHONPATH /app

CMD ["python", "aethercast/sca/main.py"]
