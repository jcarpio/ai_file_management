#!/usr/bin/env python3
"""
FileAI Indexer v2 - Indexación masiva con IA local
  - Prioridad: docs/PDFs → imágenes → vídeos
  - Transcripción de audio con faster-whisper
  - Descripción visual de vídeos con LLaVA
  - Detección de duplicados por hash MD5
  - Watchdog para nuevo contenido
  - Reanudable: salta ficheros ya indexados
"""

import os
import json
import base64
import hashlib
import logging
import argparse
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter,
    FieldCondition, MatchValue
)
import fitz
from docx import Document as DocxDocument
from faster_whisper import WhisperModel

# ─── Configuración ────────────────────────────────────────────────────────────

OLLAMA_URL      = os.getenv("OLLAMA_URL",      "http://localhost:11434")
QDRANT_URL      = os.getenv("QDRANT_URL",      "http://localhost:6333")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "fileai")
EMBED_MODEL     = os.getenv("EMBED_MODEL",     "nomic-embed-text")
VISION_MODEL    = os.getenv("VISION_MODEL",    "llava")
TEXT_MODEL      = os.getenv("TEXT_MODEL",      "llama3.2")
WHISPER_MODEL   = os.getenv("WHISPER_MODEL",   "large-v3")
WHISPER_DEVICE  = os.getenv("WHISPER_DEVICE",  "cpu")
EMBED_DIM       = 768
MAX_WORKERS_DOC = int(os.getenv("WORKERS_DOC", "6"))
MAX_WORKERS_IMG = int(os.getenv("WORKERS_IMG", "3"))
MAX_WORKERS_VID = int(os.getenv("WORKERS_VID", "2"))
VIDEO_FRAMES    = 3
AUDIO_MINUTES   = 5
MAX_TEXT_CHARS  = 4000
WATCHDOG_SECS   = 60
LOG_DIR         = os.getenv("LOG_DIR", "/var/log/fileai")

TYPE_PRIORITY = {"pdf": 0, "word": 1, "text": 2, "image": 3, "video": 4}

SUPPORTED_EXTS = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".heic"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "pdf":   {".pdf"},
    "word":  {".docx", ".doc"},
    "text":  {".txt", ".md", ".rst", ".csv", ".json", ".xml", ".html", ".py",
              ".js", ".ts", ".sh", ".yaml", ".yml", ".ini", ".cfg", ".log"},
}

Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"{LOG_DIR}/indexer.log"),
    ]
)
log = logging.getLogger(__name__)

_whisper_model = None
_whisper_lock  = threading.Lock()

def get_whisper() -> WhisperModel:
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            log.info(f"Cargando Whisper {WHISPER_MODEL} ({WHISPER_DEVICE})...")
            _whisper_model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type="int8")
            log.info("Whisper listo.")
    return _whisper_model

# ─── Qdrant ───────────────────────────────────────────────────────────────────

def init_qdrant(client: QdrantClient):
    cols = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in cols:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
        log.info(f"Colección '{COLLECTION_NAME}' creada.")
    else:
        log.info(f"Colección '{COLLECTION_NAME}' ya existe.")

# ─── Ollama ───────────────────────────────────────────────────────────────────

def ollama_embed(text: str) -> list:
    r = httpx.post(f"{OLLAMA_URL}/api/embeddings",
                   json={"model": EMBED_MODEL, "prompt": text}, timeout=120)
    r.raise_for_status()
    return r.json()["embedding"]


def ollama_describe_text(text: str, filename: str) -> str:
    prompt = (
        f"Eres un asistente que describe ficheros para facilitar su búsqueda. "
        f"Fichero: '{filename}'.\nContenido:\n\n{text[:MAX_TEXT_CHARS]}\n\n"
        f"Describe en 2-4 frases en español: tipo de documento, tema y palabras clave. "
        f"Sé conciso y factual."
    )
    r = httpx.post(f"{OLLAMA_URL}/api/generate",
                   json={"model": TEXT_MODEL, "prompt": prompt, "stream": False},
                   timeout=180)
    r.raise_for_status()
    return r.json()["response"].strip()


def ollama_describe_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    r = httpx.post(f"{OLLAMA_URL}/api/generate",
                   json={"model": VISION_MODEL,
                         "prompt": ("Describe esta imagen en español en 2-3 frases: "
                                    "qué se ve, colores, personas, lugar y texto visible."),
                         "images": [b64], "stream": False},
                   timeout=300)
    r.raise_for_status()
    return r.json()["response"].strip()


def ollama_summarize_video(filename: str, visual: str, transcript: str) -> str:
    prompt = (
        f"Vídeo: '{filename}'.\n"
        f"Descripción visual: {visual}\n"
        f"Transcripción (primeros {AUDIO_MINUTES} min): {transcript[:2000]}\n\n"
        f"Resume en 3-5 frases en español de qué trata, quién habla y temas principales."
    )
    r = httpx.post(f"{OLLAMA_URL}/api/generate",
                   json={"model": TEXT_MODEL, "prompt": prompt, "stream": False},
                   timeout=180)
    r.raise_for_status()
    return r.json()["response"].strip()

# ─── Whisper ──────────────────────────────────────────────────────────────────

def transcribe_audio(video_path: str) -> str:
    tmp = f"/tmp/fileai_{hashlib.md5(video_path.encode()).hexdigest()}.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-t", str(AUDIO_MINUTES * 60),
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", tmp, "-y"],
            capture_output=True, timeout=120,
        )
        if not Path(tmp).exists():
            return ""
        whisper = get_whisper()
        segments, info = whisper.transcribe(tmp, beam_size=5, language=None, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments)
        log.info(f"  Whisper: {info.language}, {len(text)} chars")
        return text.strip()
    except Exception as e:
        log.warning(f"  Whisper error: {e}")
        return ""
    finally:
        Path(tmp).unlink(missing_ok=True)

# ─── Extractores ──────────────────────────────────────────────────────────────

def extract_pdf(path: str) -> str:
    doc = fitz.open(path)
    return "\n".join(page.get_text() for i, page in enumerate(doc) if i < 20)

def extract_word(path: str) -> str:
    doc = DocxDocument(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

def extract_text_file(path: str) -> str:
    try:
        return open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        return ""

def get_video_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=30)
        return float(json.loads(r.stdout)["format"].get("duration", 60))
    except Exception:
        return 60.0

def extract_video_frames(path: str, duration: float) -> list:
    tmp_dir = Path("/tmp/fileai_frames"); tmp_dir.mkdir(exist_ok=True)
    base    = hashlib.md5(path.encode()).hexdigest()
    frames  = []
    step    = duration / (VIDEO_FRAMES + 1)
    for i in range(1, VIDEO_FRAMES + 1):
        out = str(tmp_dir / f"{base}_{i}.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-ss", str(step * i), "-i", path,
                 "-frames:v", "1", "-q:v", "2", out, "-y"],
                capture_output=True, timeout=30)
            if Path(out).exists():
                frames.append(out)
        except Exception:
            pass
    return frames

# ─── Helpers ──────────────────────────────────────────────────────────────────

def file_type(path: str):
    ext = Path(path).suffix.lower()
    for ftype, exts in SUPPORTED_EXTS.items():
        if ext in exts:
            return ftype
    return None

def file_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def point_id(path: str) -> int:
    return int(hashlib.md5(path.encode()).hexdigest()[:8], 16)

# ─── Procesador ───────────────────────────────────────────────────────────────

def process_file(path: str, qdrant: QdrantClient) -> dict:
    p     = Path(path)
    ftype = file_type(path)
    if not ftype:
        return {"status": "skip", "path": path}

    try:
        stat = p.stat()
    except Exception as e:
        return {"status": "error", "path": path, "error": str(e)}

    try:
        fhash = file_hash(path)
    except Exception as e:
        return {"status": "error", "path": path, "error": f"hash: {e}"}

    # Comprobar si ya indexado o duplicado
    existing_pts, _ = qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[FieldCondition(key="hash", match=MatchValue(value=fhash))]),
        limit=10, with_vectors=True,
    )

    if any(pt.payload.get("path") == path for pt in existing_pts):
        return {"status": "already_indexed", "path": path}

    if existing_pts:
        orig = existing_pts[0]
        vec  = orig.vector or ollama_embed(
            f"{orig.payload.get('filename','')} {orig.payload.get('description','')}")
        qdrant.upsert(collection_name=COLLECTION_NAME, points=[PointStruct(
            id=point_id(path), vector=vec, payload={
                "path": path, "filename": p.name, "extension": p.suffix.lower(),
                "type": ftype, "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "description": orig.payload.get("description", ""),
                "transcription": orig.payload.get("transcription", ""),
                "hash": fhash, "indexed_at": datetime.now().isoformat(),
                "is_duplicate": True, "duplicate_of": orig.payload.get("path", ""),
            })])
        return {"status": "duplicate", "path": path,
                "duplicate_of": orig.payload.get("path", ""), "type": ftype}

    # Generar descripción
    description   = ""
    transcription = ""
    try:
        if ftype == "image":
            description = ollama_describe_image(path)

        elif ftype == "video":
            duration = get_video_duration(path)
            frames   = extract_video_frames(path, duration)
            visual   = " | ".join(ollama_describe_image(fr) for fr in frames)
            for fr in frames:
                Path(fr).unlink(missing_ok=True)
            transcription = transcribe_audio(path)
            description   = (ollama_summarize_video(p.name, visual, transcription)
                             if transcription else visual or "Vídeo sin contenido extraíble.")

        elif ftype == "pdf":
            text = extract_pdf(path)
            description = ollama_describe_text(text, p.name) if text.strip() else "PDF sin texto."

        elif ftype == "word":
            text = extract_word(path)
            description = ollama_describe_text(text, p.name) if text.strip() else "Word vacío."

        elif ftype == "text":
            text = extract_text_file(path)
            description = ollama_describe_text(text, p.name) if text.strip() else "Texto vacío."

    except Exception as e:
        return {"status": "error", "path": path, "error": f"descripción: {e}"}

    if not description:
        return {"status": "error", "path": path, "error": "descripción vacía"}

    # Embedding
    try:
        embed_input = f"{p.name} {description}"
        if transcription:
            embed_input += f" {transcription[:1000]}"
        embedding = ollama_embed(embed_input)
    except Exception as e:
        return {"status": "error", "path": path, "error": f"embed: {e}"}

    # Guardar
    qdrant.upsert(collection_name=COLLECTION_NAME, points=[PointStruct(
        id=point_id(path), vector=embedding, payload={
            "path": path, "filename": p.name, "extension": p.suffix.lower(),
            "type": ftype, "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "description": description, "transcription": transcription,
            "hash": fhash, "indexed_at": datetime.now().isoformat(),
            "is_duplicate": False, "duplicate_of": None,
        })])

    return {"status": "ok", "path": path, "type": ftype, "description": description}

# ─── Escaneo con prioridad ────────────────────────────────────────────────────

def scan_prioritized(directories: list) -> list:
    files = []
    for d in directories:
        for root, _, filenames in os.walk(d):
            for fname in filenames:
                fp = os.path.join(root, fname)
                if file_type(fp):
                    files.append(fp)
    files.sort(key=lambda f: TYPE_PRIORITY.get(file_type(f) or "text", 99))
    docs = sum(1 for f in files if file_type(f) in ("pdf","word","text"))
    imgs = sum(1 for f in files if file_type(f) == "image")
    vids = sum(1 for f in files if file_type(f) == "video")
    log.info(f"Encontrados: {len(files)} ficheros — docs={docs} imgs={imgs} vids={vids}")
    return files


def run_batch(files: list, qdrant: QdrantClient, workers: int,
              stats: dict, total: int, offset: int = 0):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_file, f, qdrant): f for f in files}
        for i, fut in enumerate(as_completed(futures), 1):
            res    = fut.result()
            idx    = offset + i
            status = res.get("status")
            if status == "ok":
                stats["ok"] += 1
                log.info(f"[{idx}/{total}] ✓ {Path(res['path']).name} ({res['type']})")
            elif status == "duplicate":
                stats["duplicate"] += 1
                log.info(f"[{idx}/{total}] ⚑ DUPL {Path(res['path']).name}")
            elif status == "already_indexed":
                stats["already"] += 1
            elif status == "skip":
                stats["skip"] += 1
            else:
                stats["error"] += 1
                log.warning(f"[{idx}/{total}] ✗ {res['path']}: {res.get('error')}")

            if idx % 100 == 0:
                elapsed = (datetime.now() - stats["start"]).total_seconds()
                rate    = idx / max(elapsed, 1)
                rem_min = (total - idx) / max(rate, 0.01) / 60
                log.info(f"─── {idx}/{total} | {rate:.1f} f/s | ~{rem_min:.0f} min restantes ───")

# ─── Watchdog ─────────────────────────────────────────────────────────────────

def watchdog_loop(directories: list, qdrant: QdrantClient):
    log.info(f"Watchdog activo — comprobando cada {WATCHDOG_SECS}s")
    known: dict = {}
    while True:
        new_files = []
        for d in directories:
            for root, _, filenames in os.walk(d):
                for fname in filenames:
                    fp = os.path.join(root, fname)
                    if not file_type(fp):
                        continue
                    try:
                        mtime = Path(fp).stat().st_mtime
                    except Exception:
                        continue
                    if fp not in known or known[fp] != mtime:
                        known[fp] = mtime
                        new_files.append(fp)
        if new_files:
            log.info(f"Watchdog: {len(new_files)} ficheros nuevos/modificados")
            st = {"ok":0,"duplicate":0,"already":0,"skip":0,"error":0,"start":datetime.now()}
            run_batch(new_files, qdrant, MAX_WORKERS_DOC, st, len(new_files))
            log.info(f"Watchdog: ok={st['ok']} dupl={st['duplicate']} err={st['error']}")
        time.sleep(WATCHDOG_SECS)

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="FileAI Indexer v2")
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--dry-run",  action="store_true")
    ap.add_argument("--watchdog", action="store_true",
                    help="Tras indexar, queda monitorizando nuevo contenido")
    ap.add_argument("--workers-doc", type=int, default=MAX_WORKERS_DOC)
    ap.add_argument("--workers-img", type=int, default=MAX_WORKERS_IMG)
    ap.add_argument("--workers-vid", type=int, default=MAX_WORKERS_VID)
    args = ap.parse_args()

    qdrant = QdrantClient(url=QDRANT_URL)
    init_qdrant(qdrant)

    all_files = scan_prioritized(args.dirs)

    if args.dry_run:
        for f in all_files[:50]:
            print(f"  [{file_type(f)}] {f}")
        log.info(f"Dry-run. Total: {len(all_files)}")
        return

    docs  = [f for f in all_files if file_type(f) in ("pdf","word","text")]
    imgs  = [f for f in all_files if file_type(f) == "image"]
    vids  = [f for f in all_files if file_type(f) == "video"]
    total = len(all_files)
    stats = {"ok":0,"duplicate":0,"already":0,"skip":0,"error":0,"start":datetime.now()}

    log.info("="*55)
    log.info(f"INICIANDO  docs={len(docs)}  imgs={len(imgs)}  vids={len(vids)}")
    log.info("="*55)

    if docs:
        log.info("── FASE 1: Documentos y PDFs ──")
        run_batch(docs, qdrant, args.workers_doc, stats, total, 0)

    if imgs:
        log.info("── FASE 2: Imágenes ──")
        run_batch(imgs, qdrant, args.workers_img, stats, total, len(docs))

    if vids:
        log.info("── FASE 3: Vídeos (cargando Whisper...) ──")
        get_whisper()
        run_batch(vids, qdrant, args.workers_vid, stats, total, len(docs)+len(imgs))

    elapsed = (datetime.now() - stats["start"]).total_seconds()
    log.info("="*55)
    log.info(f"COMPLETADO en {elapsed/3600:.1f}h")
    log.info(f"  Indexados:   {stats['ok']}")
    log.info(f"  Duplicados:  {stats['duplicate']}")
    log.info(f"  Ya existían: {stats['already']}")
    log.info(f"  Errores:     {stats['error']}")
    log.info("="*55)

    if args.watchdog:
        watchdog_loop(args.dirs, qdrant)

if __name__ == "__main__":
    main()
