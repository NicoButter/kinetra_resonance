# Kinetra Resonance

**Music Separation & Analysis Engine**

Kinetra Resonance converts music into structured temporal data for visual and tactile experiences. Stem separation is an intermediate processing step, not the final product.

```text
Audio
  ↓
Stem Separation
  ↓
drums.wav → optional ADTOF transcription + Kinetra onset/intensity fusion
          → automatic coarse drum families
  ↓
Musical Post-processing → Human Review
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
- Propone cinco familias de batería —kick, snare, hi-hat, tom y cymbal— mediante el backend ADTOF opcional, y conserva onsets locales no emparejados como `UNASSIGNED`.
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

La aplicación y la revisión manual funcionan sin ADTOF. Para habilitar el backend experimental en un entorno separado/reproducible:

```bash
pip install -r requirements-adt.txt
```

Ese archivo fija ADTOF-pytorch a un commit conocido; la dependencia y sus pesos quedan en el entorno Python, no en Git.

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
DRUM_TRANSCRIPTION_BACKEND=adtof
DRUM_TRANSCRIPTION_DEVICE=auto
DRUM_TRANSCRIPTION_ENABLED=True
DRUM_EVENT_MATCH_TOLERANCE_MS=50
```

`auto` usa CUDA solo si `torch.cuda.is_available()` devuelve verdadero. Un fallo CUDA reintenta en CPU. Si el backend no está instalado, falla al cargar o entrega MIDI inválido, el job continúa con el detector local y eventos `UNASSIGNED` para clasificación humana.

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
    ├── processed/drums/{kick,snare,hi_hat,tom,cymbal,unassigned}.json
    ├── reviewed/vN/{drums,bass,guitar,piano,vocals,other}.json
    ├── reviewed/vN/drums/{kick,snare,hi_hat,tom,crash,splash,ride,cymbal,unknown,unassigned}.json
    ├── teleo_experience.json
    └── teleo_experience.reviewed.json
```

Ejemplo de salida:

```json
{
  "format": "kinetra-resonance",
  "version": 1,
  "stem": "drums",
  "durationMs": 214000,
  "bpm": 118.4,
  "transcription": {
    "backend": "adtof",
    "backendVersion": "0.1.0",
    "device": "cpu",
    "classes": ["kick", "snare", "hi_hat", "tom", "cymbal"]
  },
  "events": [{"timeMs": 421, "automaticType": "kick", "automatic": {"backend": "adtof", "type": "kick", "confidence": null}, "reviewedType": null, "effectiveType": "kick", "intensity": 0.91}]
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
- [Human Review y Resonance Review Editor](docs/HUMAN_REVIEW.md)
- [Contexto para ChatGPT](docs/CHATGPT_CONTEXT.md)

## Alcance actual

El pipeline previsto para Teleo es:

```text
Original audio → 6-stem separation → Per-stem analyzers
               → Structured JSON artifacts → Teleo Experience package
```

Los WAV son material intermedio. El producto principal actual es `teleo_experience.json`. La extracción de pitch es conservadora y aproximada; piano todavía usa análisis monofónico, ADTOF solo propone cinco familias gruesas y visemas, letras, secciones y háptica permanecen vacíos, sin información ficticia.

## Analysis Lab

Desde `/lab/` se puede abrir el laboratorio sincronizado de cada job. Utiliza el elemento HTML5 audio como único reloj, Canvas nativo y `requestAnimationFrame`. Permite cambiar entre original/stems, comparar RAW y PROCESSED, filtrar por confianza e inspeccionar eventos y calidad sin modificar los JSON.

## Human Review Workflow

```text
Automatic Analysis → Post Processing → Human Review
                   → Reviewed artifacts → Human-reviewed Teleo Experience
```

El **Resonance Review Editor** está disponible en `/review/jobs/<job_uuid>/`. Cada edición crea un `ReviewAction` auditable y REVIEWED se reconstruye desde PROCESSED más la rama activa de acciones. AI output is never overwritten by human review.

Para DRUMS funciona como un pequeño secuenciador de metadata. Una sugerencia automática pendiente se dibuja en su familia (`KICK`, `SNARE`, `HI-HAT`, `TOM` o `CYMBAL`) con badge `AI · UNREVIEWED`; `UNASSIGNED` queda reservado para onsets sin clasificación. La confirmación muestra `✓`, una corrección humana `H` y un agregado manual `M`. Rapid Drum Review recorre todos los eventos `UNREVIEWED`, no solo los unassigned. Incluye audition de 150/350 ms sobre el único `drums.wav` y no genera sub-stems de cuerpos de batería.

Al finalizar se generan `reviewed/v<version>/*.json` y `teleo_experience.reviewed.json`. Teleo deberá preferir esta experiencia cuando exista, aunque la integración API todavía no forma parte de este repositorio.

Kinetra Resonance no descarga música ni elude DRM. Las personas usuarias son responsables de procesar únicamente audio para el que tengan autorización legal.

## Licencia

Este repositorio se distribuye bajo [MIT](LICENSE).

ADTOF-pytorch es un backend automático **opcional y experimental**. No se copia ni vende código o pesos upstream desde este repositorio. El commit inspeccionado no contiene una licencia explícita y distribuye pesos convertidos del proyecto ADTOF original; revisá por separado las licencias de código y modelo antes de cualquier distribución comercial. Ver [notas de integración y licencia](docs/DRUM_TRANSCRIPTION.md).
