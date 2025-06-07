FROM python:3.9-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY ./aethercast/sca/requirements.txt /app/requirements_sca.txt
RUN pip install --no-cache-dir -r /app/requirements_sca.txt

COPY ./aethercast /app/aethercast
ENV PYTHONPATH /app

EXPOSE 5002

# For production, use a production-ready WSGI server like Gunicorn.
# Example (ensure gunicorn is in requirements_sca.txt):
# CMD ["gunicorn", "--bind", "0.0.0.0:5002", "aethercast.sca.main:app"]

# Run main.py when the container launches (Flask development server)
CMD ["python", "aethercast/sca/main.py"]
