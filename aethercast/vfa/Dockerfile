FROM python:3.9-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY ./aethercast/vfa/requirements.txt /app/requirements_vfa.txt
RUN pip install --no-cache-dir -r /app/requirements_vfa.txt

COPY ./aethercast /app/aethercast
ENV PYTHONPATH /app

EXPOSE 5005

# For production, use a production-ready WSGI server like Gunicorn.
# Example (ensure gunicorn is in requirements_vfa.txt):
# CMD ["gunicorn", "--bind", "0.0.0.0:5005", "aethercast.vfa.main:app"]

# Run main.py when the container launches (Flask development server)
CMD ["python", "aethercast/vfa/main.py"]
