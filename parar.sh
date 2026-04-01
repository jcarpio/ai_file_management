#!/bin/bash
# FileAI - Parar todos los procesos
DATA_DIR="$HOME/fileai_data"

echo "Parando FileAI..."

for pidfile in "$DATA_DIR/indexer.pid" "$DATA_DIR/watchdog.pid"; do
    if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        kill "$pid" 2>/dev/null && echo "Proceso $pid parado" || echo "Proceso $pid ya no existe"
        rm -f "$pidfile"
    fi
done

podman stop qdrant 2>/dev/null && echo "Qdrant parado" || true
echo "Listo."
