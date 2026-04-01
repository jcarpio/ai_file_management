#!/usr/bin/env python3"""
FileAI Duplicates - Lista y gestiona ficheros duplicados detectados durante la indexación
Uso:
  python duplicates.py list                    # muestra todos los duplicados
  python duplicates.py list --type image       # filtra por tipo
  python duplicates.py export duplicates.csv   # exporta a CSV
  python duplicates.py delete --dry-run        # muestra qué se borraría
  python duplicates.py delete                  # borra duplicados del disco (¡cuidado!)
"""

import argparse
import csv
import sys
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

QDRANT_URL      = "http://localhost:6333"
COLLECTION_NAME = "fileai"

TYPE_ICONS = {
    "image": "🖼",
    "video": "▶",
    "pdf":   "📄",
    "word":  "📝",
    "text":  "{ }",
}


def get_all_duplicates(client: QdrantClient, file_type: str | None = None) -> list[dict]:
    """Recupera todos los puntos marcados como duplicados."""
    must_conditions = [FieldCondition(key="is_duplicate", match=MatchValue(value=True))]
    if file_type:
        must_conditions.append(FieldCondition(key="type", match=MatchValue(value=file_type)))

    results = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=must_conditions),
            limit=100,
            offset=offset,
            with_payload=True,
        )
        results.extend([pt.payload for pt in batch])
        if offset is None:
            break
    return results


def cmd_list(client, args):
    dupes = get_all_duplicates(client, args.type)
    if not dupes:
        print("No se encontraron duplicados." +
              (f" (filtro: {args.type})" if args.type else ""))
        return

    # Agrupar por hash para mostrar grupos
    groups: dict[str, list[dict]] = {}
    for d in dupes:
        h = d.get("hash", "?")
        groups.setdefault(h, []).append(d)

    # Añadir los originales a cada grupo
    for h, dupe_list in groups.items():
        original_path = dupe_list[0].get("duplicate_of", "")
        orig_batch, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[
                FieldCondition(key="hash",         match=MatchValue(value=h)),
                FieldCondition(key="is_duplicate", match=MatchValue(value=False)),
            ]),
            limit=1,
            with_payload=True,
        )
        original = orig_batch[0].payload if orig_batch else {"path": original_path, "filename": "?"}

        print(f"\n{'─'*70}")
        icon = TYPE_ICONS.get(original.get("type", ""), "◈")
        size_mb = original.get("size_bytes", 0) / (1024 * 1024)
        print(f"{icon}  ORIGINAL  [{size_mb:.1f} MB]  {original.get('filename','?')}")
        print(f"   {original.get('path','?')}")
        for d in dupe_list:
            size_mb2 = d.get("size_bytes", 0) / (1024 * 1024)
            print(f"   ⚑ COPIA   [{size_mb2:.1f} MB]  {d.get('filename','?')}")
            print(f"      {d.get('path','?')}")

    total_size = sum(d.get("size_bytes", 0) for d in dupes)
    print(f"\n{'='*70}")
    print(f"Total duplicados: {len(dupes)} ficheros en {len(groups)} grupos")
    print(f"Espacio recuperable: {total_size / (1024**3):.2f} GB")


def cmd_export(client, args):
    dupes = get_all_duplicates(client)
    if not dupes:
        print("No hay duplicados para exportar.")
        return

    outfile = args.output
    with open(outfile, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "filename", "path", "type", "size_bytes", "modified",
            "hash", "duplicate_of", "indexed_at"
        ])
        writer.writeheader()
        for d in dupes:
            writer.writerow({k: d.get(k, "") for k in writer.fieldnames})

    total_size = sum(d.get("size_bytes", 0) for d in dupes)
    print(f"Exportados {len(dupes)} duplicados a '{outfile}'")
    print(f"Espacio recuperable: {total_size / (1024**3):.2f} GB")


def cmd_delete(client, args):
    dupes = get_all_duplicates(client, args.type)
    if not dupes:
        print("No hay duplicados para eliminar.")
        return

    total_size = sum(d.get("size_bytes", 0) for d in dupes)
    print(f"{'[DRY-RUN] ' if args.dry_run else ''}Se {'borrarían' if args.dry_run else 'borrarán'} "
          f"{len(dupes)} ficheros ({total_size / (1024**3):.2f} GB)")

    if not args.dry_run:
        confirm = input("\n¿Confirmas el borrado? Escribe 'SI' para continuar: ")
        if confirm.strip() != "SI":
            print("Cancelado.")
            return

    deleted = 0
    errors = 0
    for d in dupes:
        path = d.get("path", "")
        if args.dry_run:
            print(f"  [borraría] {path}")
            continue
        try:
            p = Path(path)
            if p.exists():
                p.unlink()
                print(f"  ✓ Borrado: {path}")
                deleted += 1
            else:
                print(f"  ! No existe: {path}")
        except Exception as e:
            print(f"  ✗ Error borrando {path}: {e}")
            errors += 1

    if not args.dry_run:
        print(f"\nBorrados: {deleted}  Errores: {errors}")
        print("Nota: los registros en Qdrant se mantienen marcados como duplicados.")
        print("Para limpiar Qdrant, ejecuta: python duplicates.py clean-db")


def cmd_clean_db(client, args):
    """Elimina de Qdrant los puntos duplicados cuyos ficheros ya no existen."""
    dupes = get_all_duplicates(client)
    removed = 0
    for d in dupes:
        path = d.get("path", "")
        if not Path(path).exists():
            point_id = int(__import__("hashlib").md5(path.encode()).hexdigest()[:8], 16)
            client.delete(
                collection_name=COLLECTION_NAME,
                points_selector=[point_id],
            )
            removed += 1
            print(f"  ✓ Eliminado de Qdrant: {path}")
    print(f"\nRegistros eliminados de Qdrant: {removed}")


def main():
    parser = argparse.ArgumentParser(description="FileAI Duplicate Manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="Lista duplicados")
    p_list.add_argument("--type", choices=["image","video","pdf","word","text"])

    p_exp = sub.add_parser("export", help="Exporta duplicados a CSV")
    p_exp.add_argument("output", nargs="?", default="duplicates.csv")

    p_del = sub.add_parser("delete", help="Borra duplicados del disco")
    p_del.add_argument("--type", choices=["image","video","pdf","word","text"])
    p_del.add_argument("--dry-run", action="store_true", help="Solo muestra, no borra")

    sub.add_parser("clean-db", help="Limpia Qdrant de registros huérfanos")

    args = parser.parse_args()
    client = QdrantClient(url=QDRANT_URL)

    if args.cmd == "list":       cmd_list(client, args)
    elif args.cmd == "export":   cmd_export(client, args)
    elif args.cmd == "delete":   cmd_delete(client, args)
    elif args.cmd == "clean-db": cmd_clean_db(client, args)


if __name__ == "__main__":
    main()
