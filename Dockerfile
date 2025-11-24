FROM python:3.9-slim

# Install FFmpeg
RUN apt-get update && apt-get install -y ffmpeg

# Set working directory
WORKDIR /app

# Copy files
COPY requirements.txt .
COPY . .

# Install Python packages
RUN pip install -r requirements.txt

# Create directories
RUN mkdir -p static/uploads static/temp

# Run the app
CMD ["python", "app.py"]