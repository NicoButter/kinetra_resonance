# Kinetra Resonance

**Music Separation & Analysis Engine**

Kinetra Resonance transforma audio musical proporcionado por el usuario en stems y datos estructurados para futuras experiencias visuales y hápticas.

```text
Audio
  ↓
Stem Separation
  ↓
Musical Analysis
  ↓
Structured Timeline
  ↓
Visual / Haptic Applications
```

## Qué hace hoy

- Carga archivos MP3, WAV, FLAC, M4A, AAC y OGG desde una interfaz Django local.
- Conserva el audio original de forma aislada por UUID.
- Ejecuta `audio-separator` en un proceso local separado de la petición web.
- Detecta y registra únicamente los stems producidos: vocals, drums, bass, guitar, piano, other e instrumental.
- Analiza el stem de batería con Essentia y genera `drums.json`.
- Muestra progreso con polling, permite descargar el original, stems y JSON, y conserva historial de trabajos.

## Inicio rápido

El repositorio ya incluye un entorno `.venv` con las dependencias del proyecto.

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py runserver
```

Abrí http://127.0.0.1:8000 y elegí **Process music**. La carga crea un job que se ejecuta en segundo plano. También se puede procesar manualmente:

```bash
python manage.py process_track <track_uuid>
```

## Configuración

Copiá `.env.example` o exportá las variables necesarias en el entorno desde el que iniciás Django:

```bash
DJANGO_SECRET_KEY=
DJANGO_DEBUG=True
AUDIO_SEPARATOR_DEFAULT_MODEL=UVR-MDX-NET-Inst_HQ_4.onnx
MAX_UPLOAD_SIZE_MB=250
```

El soporte GPU depende del runtime local. El proyecto puede ejecutarse en CPU cuando CUDA no está disponible. La primera ejecución de `audio-separator` puede requerir descargar el modelo seleccionado.

## Archivos producidos

```text
media/tracks/<track_uuid>/
├── source/original.<extension>
├── stems/
│   ├── drums.wav
│   └── ...stems disponibles
└── analysis/drums.json
```

Ejemplo de salida:

```json
{
  "format": "kinetra-resonance",
  "version": 1,
  "stem": "drums",
  "durationMs": 214000,
  "bpm": 118.4,
  "confidence": 3.82,
  "events": [{"timeMs": 421, "type": "beat", "intensity": 0.91}]
}
```

## Desarrollo

```bash
python manage.py makemigrations --check
python manage.py migrate
python manage.py check
python manage.py test
```

La documentación ampliada está en:

- [Documentación técnica](docs/TECHNICAL.md)
- [Contexto para ChatGPT](docs/CHATGPT_CONTEXT.md)

## Alcance actual

El MVP implementa BPM, beats, timestamps e intensidad normalizada para batería. Aún no incluye clasificación kick/snare/hi-hat/cymbal, análisis de voz o letras, visemas, traducción, API para Teleo, Celery, Redis, PostgreSQL, Docker ni procesamiento distribuido.

Kinetra Resonance no descarga música ni elude DRM. Las personas usuarias son responsables de procesar únicamente audio para el que tengan autorización legal.

## Licencia

Este repositorio se distribuye bajo [MIT](LICENSE).
