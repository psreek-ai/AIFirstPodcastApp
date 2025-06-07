FROM python:3.9-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY ./aethercast/pswa/requirements.txt /app/requirements_pswa.txt
RUN pip install --no-cache-dir -r /app/requirements_pswa.txt

COPY ./aethercast /app/aethercast
ENV PYTHONPATH /app

EXPOSE 5004

# For production, use a production-ready WSGI server like Gunicorn.
# Example (ensure gunicorn is in requirements_pswa.txt):
# CMD ["gunicorn", "--bind", "0.0.0.0:5004", "aethercast.pswa.main:app"]

# Run main.py when the container launches (Flask development server)
CMD ["python", "aethercast/pswa/main.py"]
