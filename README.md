# Kinetra Resonance

**Music Separation & Analysis Engine**

Kinetra Resonance converts music into structured temporal data for visual and tactile experiences. Stem separation is an intermediate processing step, not the final product.

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
- Ejecuta analizadores RAW específicos para drums, bass, guitar, piano, vocals y other.
- Postprocesa eventos musicales sin sobrescribir los datos RAW y valida calidad por canal.
- Construye `teleo_experience.json` exclusivamente desde artifacts procesados confiables.
- Muestra progreso con polling, conteos, tamaños y descargas, y conserva historial de trabajos.

## Inicio rápido

El repositorio ya incluye un entorno `.venv` con las dependencias del proyecto.

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py runserver
```

Abrí http://127.0.0.1:8000 y elegí **Process music**. La carga crea un job que se ejecuta en segundo plano. También se puede procesar manualmente:

```bash
python manage.py process_track <job_uuid>
```

## Configuración

Copiá `.env.example` o exportá las variables necesarias en el entorno desde el que iniciás Django:

```bash
DJANGO_SECRET_KEY=
DJANGO_DEBUG=True
TELEO_SEPARATOR_MODEL=htdemucs_6s.yaml
VOCAL_SEPARATOR_MODEL=UVR-MDX-NET-Inst_HQ_4.onnx
MAX_UPLOAD_SIZE_MB=250
```

El perfil predeterminado, **Teleo Music — 6 stems**, usa `htdemucs_6s.yaml` y exige vocals, drums, bass, guitar, piano y other. El perfil opcional **Vocal extraction** usa `UVR-MDX-NET-Inst_HQ_4.onnx`. El soporte GPU depende del runtime local; la primera ejecución de cada modelo puede requerir descargarlo.

Una canción existente puede reprocesarse desde su página de detalle con otro perfil o modelo sin volver a subir el audio. Cada intento conserva su `ProcessingJob`; los stems y artifacts representan siempre la salida vigente.

## Archivos producidos

```text
media/tracks/<track_uuid>/
├── source/original.<extension>
├── stems/
│   ├── vocals.wav
│   ├── drums.wav
│   ├── bass.wav
│   ├── guitar.wav
│   ├── piano.wav
│   └── other.wav
└── analysis/<job_uuid>/
    ├── raw/{drums,bass,guitar,piano,vocals,other}.json
    ├── processed/{drums,bass,guitar,piano,vocals,other}.json
    └── teleo_experience.json
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
- [Pipeline de análisis y Teleo Experience](docs/ANALYSIS_PIPELINE.md)
- [Contexto para ChatGPT](docs/CHATGPT_CONTEXT.md)

## Alcance actual

El pipeline previsto para Teleo es:

```text
Original audio → 6-stem separation → Per-stem analyzers
               → Structured JSON artifacts → Teleo Experience package
```

Los WAV son material intermedio. El producto principal actual es `teleo_experience.json`. La extracción de pitch es conservadora y aproximada; piano todavía usa análisis monofónico, la clasificación de percusión es heurística y visemas, letras, secciones y háptica permanecen vacíos, sin información ficticia.

## Analysis Lab

Desde `/lab/` se puede abrir el laboratorio sincronizado de cada job. Utiliza el elemento HTML5 audio como único reloj, Canvas nativo y `requestAnimationFrame`. Permite cambiar entre original/stems, comparar RAW y PROCESSED, filtrar por confianza e inspeccionar eventos y calidad sin modificar los JSON.

Kinetra Resonance no descarga música ni elude DRM. Las personas usuarias son responsables de procesar únicamente audio para el que tengan autorización legal.

## Licencia

Este repositorio se distribuye bajo [MIT](LICENSE).
