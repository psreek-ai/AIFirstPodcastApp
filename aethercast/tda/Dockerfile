FROM python:3.9-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY ./aethercast/tda/requirements.txt /app/requirements_tda.txt
RUN pip install --no-cache-dir -r /app/requirements_tda.txt

COPY ./aethercast /app/aethercast
ENV PYTHONPATH /app

EXPOSE 5000

# For production, use a production-ready WSGI server like Gunicorn.
# Example (ensure gunicorn is in requirements_tda.txt):
# CMD ["gunicorn", "--bind", "0.0.0.0:5000", "aethercast.tda.main:app"]

# Run main.py when the container launches (Flask development server)
CMD ["python", "aethercast/tda/main.py"]
