FROM python:3.13-slim

# Install ffmpeg (required by PyAV for H.264 decoding)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY car_protocol.py video_decoder.py webapp.py ./
COPY templates/ templates/
COPY static/ static/

# Expose web UI port
EXPOSE 5555

# UDP ports for car communication (video, control, handshake)
# These only matter with --network host or explicit port mapping
EXPOSE 1234/udp
EXPOSE 23458/udp
EXPOSE 23459/udp

CMD ["python", "webapp.py"]