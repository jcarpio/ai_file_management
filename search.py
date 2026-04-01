#!/usr/bin/env python3
"""
FileAI Search - Búsqueda semántica en los ficheros indexados
Uso: python search.py "fotos de la playa en verano"
     python search.py "facturas 2023" --type pdf --limit 10
"""

import argparse
import httpx
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

OLLAMA_URL      = "http://localhost:11434"
QDRANT_URL      = "http://localhost:6333"
COLLECTION_NAME = "fileai"
EMBED_MODEL     = "nomic-embed-text"


def embed(text: str) -> list[float]:
    r = httpx.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def search(query: str, file_type: str | None = None, limit: int = 10):
    client = QdrantClient(url=QDRANT_URL)
    vector = embed(query)

    search_filter = None
    if file_type:
        search_filter = Filter(must=[
            FieldCondition(key="type", match=MatchValue(value=file_type))
        ])

    results = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        query_filter=search_filter,
        limit=limit,
        with_payload=True,
    )

    if not results:
        print("No se encontraron resultados.")
        return

    print(f"\n🔍 Resultados para: '{query}'\n{'─'*60}")
    for i, r in enumerate(results, 1):
        p = r.payload
        size_mb = p.get("size_bytes", 0) / (1024 * 1024)
        print(f"\n{i}. [{p.get('type','?').upper()}] {p.get('filename','?')}")
        print(f"   📁 {p.get('path','?')}")
        print(f"   📝 {p.get('description','')}")
        print(f"   📅 {p.get('modified','?')[:10]}  💾 {size_mb:.1f} MB  "
              f"🎯 Relevancia: {r.score:.2%}")


def main():
    parser = argparse.ArgumentParser(description="FileAI Search")
    parser.add_argument("query", help="Consulta en lenguaje natural")
    parser.add_argument("--type", choices=["image", "video", "pdf", "word", "text"],
                        help="Filtrar por tipo de fichero")
    parser.add_argument("--limit", type=int, default=10,
                        help="Número de resultados (default: 10)")
    args = parser.parse_args()

    search(args.query, args.type, args.limit)


if __name__ == "__main__":
    main()
