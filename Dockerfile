FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg runtime libraries are required by PyAV for H.264 decoding.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg tini && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy only application/runtime assets. Secrets and runtime state are supplied by compose.
COPY car_protocol.py video_decoder.py webapp.py ./
COPY templates/ ./templates/
COPY static/ ./static/

RUN mkdir -p /app/data

EXPOSE 5555
EXPOSE 1234/udp
EXPOSE 23458/udp
EXPOSE 23459/udp

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "webapp.py"]
