#!/usr/bin/env python3
"""
FileAI Watchdog - Detecta ficheros nuevos en los discos y los indexa automáticamente.
Uso: python watchdog_indexer.py /run/media/jose/Disco1 /run/media/jose/Disco2
"""

import os
import sys
import time
import logging
import argparse
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

log = logging.getLogger("fileai.watchdog")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/data/watchdog.log"),
    ]
)

# Extensiones que queremos indexar (debe coincidir con indexer.py)
WATCHED_EXTS = {
    ".pdf", ".docx", ".doc",
    ".txt", ".md", ".csv", ".json", ".xml",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".heic",
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
}

# Tiempo de espera antes de indexar (evita indexar mientras se está copiando)
DEBOUNCE_SECONDS = 30

# Cola de ficheros pendientes: {path: timestamp_detección}
pending: dict[str, float] = {}


class FileAIHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = event.src_path
        ext = Path(path).suffix.lower()
        if ext in WATCHED_EXTS:
            pending[path] = time.time()
            log.info(f"Nuevo fichero detectado: {path}")

    def on_moved(self, event):
        """También captura ficheros que se mueven/renombran al destino."""
        if event.is_directory:
            return
        path = event.dest_path
        ext = Path(path).suffix.lower()
        if ext in WATCHED_EXTS:
            pending[path] = time.time()
            log.info(f"Fichero movido detectado: {path}")


def index_file(path: str):
    """Llama al indexer para un único fichero."""
    log.info(f"Indexando nuevo fichero: {path}")
    try:
        result = subprocess.run(
            [sys.executable, "/app/indexer.py", path],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode == 0:
            log.info(f"✓ Indexado: {path}")
        else:
            log.warning(f"✗ Error indexando {path}: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        log.warning(f"Timeout indexando: {path}")
    except Exception as e:
        log.warning(f"Excepción indexando {path}: {e}")


def process_pending():
    """Procesa ficheros que llevan más de DEBOUNCE_SECONDS en la cola."""
    now = time.time()
    ready = [p for p, t in pending.items() if now - t >= DEBOUNCE_SECONDS]
    for path in ready:
        del pending[path]
        # Verificar que el fichero existe y ya no está siendo escrito
        try:
            p = Path(path)
            if not p.exists():
                continue
            size1 = p.stat().st_size
            time.sleep(2)
            size2 = p.stat().st_size
            if size1 != size2:
                # Aún se está escribiendo, posponer
                pending[path] = now
                continue
        except Exception:
            continue
        index_file(path)


def main():
    parser = argparse.ArgumentParser(description="FileAI Watchdog")
    parser.add_argument("dirs", nargs="+", help="Directorios a vigilar")
    parser.add_argument("--debounce", type=int, default=DEBOUNCE_SECONDS,
                        help=f"Segundos de espera antes de indexar (default: {DEBOUNCE_SECONDS})")
    args = parser.parse_args()

    global DEBOUNCE_SECONDS
    DEBOUNCE_SECONDS = args.debounce

    observer = Observer()
    handler = FileAIHandler()
    for d in args.dirs:
        if not Path(d).exists():
            log.warning(f"Directorio no existe: {d}")
            continue
        observer.schedule(handler, d, recursive=True)
        log.info(f"Vigilando: {d}")

    observer.start()
    log.info(f"Watchdog activo. Debounce: {DEBOUNCE_SECONDS}s. Ctrl+C para parar.")

    try:
        while True:
            process_pending()
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("Parando watchdog...")
        observer.stop()
    observer.join()
    log.info("Watchdog detenido.")


if __name__ == "__main__":
    main()
