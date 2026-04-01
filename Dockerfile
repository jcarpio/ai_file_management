FROM fedora:43

LABEL maintainer="fileai"
LABEL description="FileAI - Indexador semántico de ficheros con IA local"
LABEL version="2.0"

# ── Dependencias del sistema ──────────────────────────────────────────────────
RUN dnf install -y \
    ffmpeg \
    python3 \
    python3-pip \
    python3-devel \
    gcc \
    gcc-c++ \
    && dnf clean all

# ── Directorio de trabajo ─────────────────────────────────────────────────────
RUN mkdir -p /opt/fileai /var/log/fileai /tmp/fileai_frames
WORKDIR /opt/fileai

# ── Dependencias Python ───────────────────────────────────────────────────────
# Copiamos requirements primero para aprovechar caché de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copiar scripts ────────────────────────────────────────────────────────────
COPY indexer.py .
COPY search.py .
COPY duplicates.py .
COPY search_ui.html .

# ── Variables de entorno por defecto ──────────────────────────────────────────
ENV OLLAMA_URL=http://host.containers.internal:11434
ENV QDRANT_URL=http://host.containers.internal:6333
ENV COLLECTION_NAME=fileai
ENV EMBED_MODEL=nomic-embed-text
ENV VISION_MODEL=llava
ENV TEXT_MODEL=llama3.2
ENV WHISPER_MODEL=large-v3
ENV WHISPER_DEVICE=cpu
ENV WORKERS_DOC=6
ENV WORKERS_IMG=3
ENV WORKERS_VID=2
ENV LOG_DIR=/var/log/fileai

# ── Punto de entrada ──────────────────────────────────────────────────────────
ENTRYPOINT ["python3", "/opt/fileai/indexer.py"]
CMD ["--help"]
