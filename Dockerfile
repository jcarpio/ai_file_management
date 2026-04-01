FROM fedora:43

LABEL maintainer="fileai"
LABEL description="FileAI - Indexador semántico de ficheros con IA local"
LABEL version="2.1"

# ── Dependencias del sistema ──────────────────────────────────────────────────
RUN dnf install -y \
    ffmpeg \
    python3 \
    python3-pip \
    python3-devel \
    gcc \
    gcc-c++ \
    findutils \
    hostname \
    iproute \
    procps-ng \
    shadow-utils \
    which \
    && dnf clean all

# ── Entorno Python ────────────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── Directorios de trabajo ────────────────────────────────────────────────────
RUN mkdir -p /opt/fileai /var/log/fileai /tmp/fileai_frames
WORKDIR /opt/fileai

# ── Dependencias Python ───────────────────────────────────────────────────────
COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# ── Copiar aplicación ─────────────────────────────────────────────────────────
# Se copian todos los ficheros Python y recursos estáticos que ya están usando
# indexer.py, watchdog_indexer.py, duplicates.py, search.py y search_ui.html.
COPY *.py ./
COPY *.html ./
COPY *.txt ./

# ── Permisos seguros para ejecutar con podman --userns=keep-id ──────────────
RUN chmod 755 /opt /opt/fileai && \
    find /opt/fileai -maxdepth 1 -type f -name "*.py" -exec chmod 644 {} \; && \
    find /opt/fileai -maxdepth 1 -type f -name "*.html" -exec chmod 644 {} \; && \
    find /opt/fileai -maxdepth 1 -type f -name "*.txt" -exec chmod 644 {} \; && \
    find /opt/fileai -maxdepth 1 -type f -name "*.sh" -exec chmod 755 {} \; && \
    chmod 1777 /tmp/fileai_frames && \
    chmod 0777 /var/log/fileai

# ── Variables de entorno por defecto ──────────────────────────────────────────
# Con podman --network host, localhost apunta al host real.
ENV OLLAMA_URL=http://127.0.0.1:11434 \
    QDRANT_URL=http://127.0.0.1:6333 \
    COLLECTION_NAME=fileai \
    EMBED_MODEL=nomic-embed-text \
    VISION_MODEL=llava \
    TEXT_MODEL=llama3.2 \
    WHISPER_MODEL=large-v3 \
    WHISPER_DEVICE=cpu \
    WORKERS_DOC=6 \
    WORKERS_IMG=3 \
    WORKERS_VID=2 \
    LOG_DIR=/tmp

# ── Comando por defecto ───────────────────────────────────────────────────────
# Sin ENTRYPOINT para que puedas usar la imagen tanto con bash como con python3.
CMD ["/bin/bash"]
