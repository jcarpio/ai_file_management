#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# FileAI - Script de arranque completo
# Ejecutar ANTES de salir de viaje
# Uso: bash arrancar.sh
# ═══════════════════════════════════════════════════════════════════════════════

set -e

# ── Configuración ─────────────────────────────────────────────────────────────
DOCKERHUB_USER="${DOCKERHUB_USER:-tunombre}"     # Cambia esto por tu usuario de Docker Hub
IMAGE_NAME="fileai"
IMAGE_TAG="latest"
FULL_IMAGE="${DOCKERHUB_USER}/${IMAGE_NAME}:${IMAGE_TAG}"
TOOLBOX_NAME="fileai"
QDRANT_STORAGE="$HOME/qdrant_storage"
DATA_DIR="$HOME/fileai_data"

# Discos a indexar — ajusta estas rutas a las tuyas
DISCO1="/run/media/jose/WDBlueSN580"
DISCO2="/run/media/jose/Nuevo vol/SSD_WDBlueSN580_2TB"  # ajusta si es necesario

# ── Colores ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()      { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
section() { echo -e "\n${BOLD}══ $* ══${NC}"; }

# ═══════════════════════════════════════════════════════════════════════════════
section "1/6  Verificaciones previas"
# ═══════════════════════════════════════════════════════════════════════════════

command -v podman &>/dev/null || error "Podman no instalado. Instala con: sudo dnf install podman"
command -v toolbox &>/dev/null || error "Toolbox no instalado. Instala con: sudo dnf install toolbox"

# Verificar discos montados
for disco in "$DISCO1" "$DISCO2"; do
    if [ -d "$disco" ]; then
        ok "Disco accesible: $disco"
    else
        warn "Disco NO encontrado: $disco (continuamos de todos modos)"
    fi
done

mkdir -p "$QDRANT_STORAGE" "$DATA_DIR"
ok "Directorios de datos listos"

# ═══════════════════════════════════════════════════════════════════════════════
section "2/6  Arrancar Qdrant"
# ═══════════════════════════════════════════════════════════════════════════════

if podman ps --format "{{.Names}}" | grep -q "^qdrant$"; then
    ok "Qdrant ya está corriendo"
else
    info "Arrancando Qdrant..."
    podman run -d \
        --name qdrant \
        --restart unless-stopped \
        -p 6333:6333 \
        -v "$QDRANT_STORAGE:/qdrant/storage:z" \
        qdrant/qdrant
    sleep 5
    ok "Qdrant arrancado en http://localhost:6333"
fi

# ═══════════════════════════════════════════════════════════════════════════════
section "3/6  Arrancar Ollama"
# ═══════════════════════════════════════════════════════════════════════════════

if systemctl --user is-active --quiet ollama; then
    ok "Ollama ya está corriendo"
else
    info "Arrancando Ollama..."
    systemctl --user start ollama
    sleep 3
fi

# Verificar modelos necesarios
for model in nomic-embed-text llava llama3.2; do
    if ollama list 2>/dev/null | grep -q "^$model"; then
        ok "Modelo disponible: $model"
    else
        info "Descargando modelo: $model (puede tardar varios minutos)..."
        ollama pull "$model"
        ok "Modelo descargado: $model"
    fi
done

# ═══════════════════════════════════════════════════════════════════════════════
section "4/6  Preparar Toolbox FileAI"
# ═══════════════════════════════════════════════════════════════════════════════

if toolbox list --containers 2>/dev/null | grep -q "$TOOLBOX_NAME"; then
    ok "Toolbox '$TOOLBOX_NAME' ya existe"
else
    info "Creando toolbox desde imagen Docker Hub: $FULL_IMAGE"
    toolbox create "$TOOLBOX_NAME" --image "docker.io/$FULL_IMAGE"
    ok "Toolbox creado"
fi

# ═══════════════════════════════════════════════════════════════════════════════
section "5/6  Lanzar indexación en segundo plano"
# ═══════════════════════════════════════════════════════════════════════════════

LOG_FILE="$DATA_DIR/indexer.log"
PID_FILE="$DATA_DIR/indexer.pid"

# Construir lista de discos disponibles
DISCOS=""
[ -d "$DISCO1" ] && DISCOS="$DISCOS \"$DISCO1\""
[ -d "$DISCO2" ] && DISCOS="$DISCOS \"$DISCO2\""

if [ -z "$DISCOS" ]; then
    error "Ningún disco accesible. Verifica que estén montados."
fi

info "Lanzando indexación en segundo plano..."
info "Log: $LOG_FILE"

# Lanzar dentro del toolbox como proceso nohup (sobrevive al cierre de terminal)
nohup toolbox run --container "$TOOLBOX_NAME" bash -c "
    python3 /app/indexer.py $DISCOS 2>&1 | tee -a $LOG_FILE
" > "$LOG_FILE" 2>&1 &

INDEXER_PID=$!
echo $INDEXER_PID > "$PID_FILE"
ok "Indexador arrancado con PID $INDEXER_PID"

# ═══════════════════════════════════════════════════════════════════════════════
section "6/6  Lanzar Watchdog para nuevo contenido"
# ═══════════════════════════════════════════════════════════════════════════════

WATCHDOG_LOG="$DATA_DIR/watchdog.log"
WATCHDOG_PID_FILE="$DATA_DIR/watchdog.pid"

nohup toolbox run --container "$TOOLBOX_NAME" bash -c "
    python3 /app/watchdog_indexer.py $DISCOS 2>&1 | tee -a $WATCHDOG_LOG
" > "$WATCHDOG_LOG" 2>&1 &

WATCHDOG_PID=$!
echo $WATCHDOG_PID > "$WATCHDOG_PID_FILE"
ok "Watchdog arrancado con PID $WATCHDOG_PID"

# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║  ✓ Todo arrancado. Puedes salir de viaje :)      ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Ver progreso en tiempo real:${NC}"
echo -e "    tail -f $LOG_FILE"
echo ""
echo -e "  ${BOLD}Ver duplicados detectados (al volver):${NC}"
echo -e "    toolbox run --container $TOOLBOX_NAME python3 /app/duplicates.py list"
echo ""
echo -e "  ${BOLD}Parar todo:${NC}"
echo -e "    bash parar.sh"
echo ""
