# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY . .

# Make port 9000 available to the world outside this container
# (Actual port mapping is in docker-compose.yml)
EXPOSE 9000

# Define environment variables for Flask app (can be overridden)
ENV AIMS_TTS_HOST=0.0.0.0
ENV AIMS_TTS_PORT=9000
ENV FLASK_DEBUG=True
# Note: For production, FLASK_DEBUG should be False.
# For production, use a production-ready WSGI server like Gunicorn.
# Example for Gunicorn (would require adding gunicorn to requirements.txt):
# CMD ["gunicorn", "--bind", "0.0.0.0:9000", "main:app"]

# Run main.py when the container launches using Flask's development server
CMD ["python", "main.py"]
