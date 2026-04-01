#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# FileAI - Construir imagen y subir a Docker Hub
# Uso: bash build_and_push.sh tu_usuario_dockerhub
# ═══════════════════════════════════════════════════════════════════════════════

set -e

DOCKERHUB_USER="${1:-}"
if [ -z "$DOCKERHUB_USER" ]; then
    echo "Uso: bash build_and_push.sh tu_usuario_dockerhub"
    exit 1
fi

IMAGE="docker.io/${DOCKERHUB_USER}/fileai:latest"

echo "══ Construyendo imagen: $IMAGE"
echo "   (El primer build tarda 10-20 min por la descarga de Whisper large-v3)"
echo ""

# Construir con podman
podman build -t "$IMAGE" .

echo ""
echo "══ Login en Docker Hub..."
podman login docker.io

echo ""
echo "══ Subiendo imagen..."
podman push "$IMAGE"

echo ""
echo "✓ Imagen disponible en: https://hub.docker.com/r/${DOCKERHUB_USER}/fileai"
echo ""
echo "Cualquier usuario puede crear su toolbox con:"
echo "  toolbox create fileai --image $IMAGE"
