FROM python:3.9-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY ./aethercast/asf/requirements.txt /app/requirements_asf.txt
RUN pip install --no-cache-dir -r /app/requirements_asf.txt

COPY ./aethercast /app/aethercast
ENV PYTHONPATH /app

EXPOSE 5006

# For production, use a production-ready WSGI server like Gunicorn.
# Example (ensure gunicorn is in requirements_asf.txt):
# CMD ["gunicorn", "--bind", "0.0.0.0:5006", "aethercast.asf.main:app"]

# Run main.py when the container launches (Flask development server)
CMD ["python", "aethercast/asf/main.py"]
