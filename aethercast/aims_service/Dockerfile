# Use an official Python runtime as a parent image
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY . .

# Make port 8000 available to the world outside this container
# (Note: This is documentation, actual port exposure is in docker-compose.yml)
EXPOSE 8000

# Define environment variables (can be overridden by .env file or docker-compose)
ENV AIMS_HOST=0.0.0.0
ENV AIMS_PORT=8000
ENV FLASK_DEBUG=True

# For production, use a production-ready WSGI server like Gunicorn.
# Example (ensure gunicorn is in requirements.txt):
# CMD ["gunicorn", "--bind", "0.0.0.0:8000", "main:app"]

# Run main.py when the container launches (Flask development server)
CMD ["python", "main.py"]
