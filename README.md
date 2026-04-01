# FileAI — Indexador y Buscador semántico de ficheros

Sistema completo para indexar y buscar ficheros con IA local (Ollama + Qdrant).

## Requisitos previos

```bash
# Fedora 43
sudo dnf install ffmpeg python3-pip

# Docker (para Qdrant)
sudo dnf install docker
sudo systemctl start docker
sudo systemctl enable docker
```

## 1. Instalar Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
systemctl --user start ollama

# Descargar modelos necesarios
ollama pull nomic-embed-text   # embeddings (274 MB)
ollama pull llava              # descripción de imágenes (~4 GB)
ollama pull llama3.2           # descripción de textos/PDFs (~2 GB)
```

> Con 128GB RAM puedes usar modelos más grandes. Alternativa ligera para imágenes: `ollama pull moondream`

## 2. Arrancar Qdrant

```bash
sudo docker run -d \
  --name qdrant \
  --restart unless-stopped \
  -p 6333:6333 \
  -v ~/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

## 3. Instalar dependencias Python

```bash
cd fileai/
pip install --user -r requirements.txt
```

## 4. Indexar tus discos duros

```bash
# Indexar uno o varios directorios (puedes combinarlo todo)
python indexer.py /run/media/jose/Disco1/ /run/media/jose/Disco2/

# Con más hilos paralelos (aprovecha el 395+)
python indexer.py /run/media/jose/Disco1/ --workers 8

# Solo explorar sin indexar
python indexer.py /run/media/jose/Disco1/ --dry-run
```

> Con 12TB y --workers 8, el tiempo depende del modelo de visión.
> PDFs y textos son rápidos (~1-2s/fichero), imágenes ~5-15s, vídeos ~20-60s.
> Puedes interrumpirlo y reanudarlo: ya sabe qué está indexado.

## 5. Buscar

### Línea de comandos

```bash
# Búsqueda básica
python search.py "fotos de la playa en verano"

# Filtrar por tipo
python search.py "facturas 2023" --type pdf
python search.py "contratos de alquiler" --type word
python search.py "código de la web" --type text

# Más resultados
python search.py "reuniones de trabajo" --limit 20
```

### Interfaz web

Abre `search_ui.html` en tu navegador. Permite buscar visualmente con filtros por tipo y muestra relevancia en porcentaje.

```bash
firefox search_ui.html
# o
xdg-open search_ui.html
```

## Estimación de tiempo para 12TB

| Tipo         | Velocidad aprox | 12TB estimado     |
|--------------|-----------------|-------------------|
| Textos/JSON  | ~500/min        | Horas             |
| PDFs         | ~30-60/min      | Días              |
| Imágenes     | ~10-20/min      | Días              |
| Vídeos       | ~3-6/min        | Semanas           |

Recomendación: empieza con documentos y fotos, luego deja los vídeos en segundo plano.

## Consejos para el 395+ con 128GB

- Aumenta `--workers` hasta 8-12 para PDFs y textos
- Para imágenes, prueba `moondream` en lugar de `llava` (más rápido)
- Qdrant aguanta perfectamente millones de vectores en RAM con 128GB
- Puedes monitorizar el progreso en `indexer.log`

## Estructura de ficheros

```
fileai/
├── indexer.py       # Indexador principal
├── search.py        # Búsqueda en línea de comandos  
├── search_ui.html   # Interfaz web de búsqueda
├── requirements.txt # Dependencias Python
├── indexer.log      # Log de progreso (se crea al indexar)
└── README.md        # Este fichero
```
