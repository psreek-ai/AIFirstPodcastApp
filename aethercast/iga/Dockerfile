FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 5007

# For production, use a production-ready WSGI server like Gunicorn.
# Example (ensure gunicorn is in requirements.txt):
# CMD ["gunicorn", "--bind", "0.0.0.0:5007", "main:app"]

# Run main.py when the container launches (Flask development server)
CMD ["python", "main.py"]
