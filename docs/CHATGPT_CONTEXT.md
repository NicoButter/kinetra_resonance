# Contexto para ChatGPT — Kinetra Resonance

Copiá este documento al iniciar una conversación nueva sobre el proyecto. Describe el estado real del repositorio al cierre de la primera etapa.

---

Estamos construyendo **Kinetra Resonance**, una herramienta local y open source para separar música en stems, analizar audio y producir datos estructurados para aplicaciones visuales y hápticas, en especial una futura integración con Teleo Música.

## Objetivo del producto

El flujo actual es:

```text
Audio proporcionado por el usuario
  → separación de stems
  → análisis musical
  → timeline JSON estructurado
  → futuras aplicaciones visuales / hápticas
```

No descarga música, no acepta URLs, no elude DRM y no realiza ripping de plataformas. La persona usuaria debe tener derecho a procesar el audio cargado.

## Estado implementado

El repositorio contiene un MVP Django local funcional con:

- interfaz web en `/` para ver canciones recientes;
- formulario en `/tracks/new/` para subir MP3, WAV, FLAC, M4A, AAC u OGG;
- `Track`, `ProcessingJob`, `Stem` y `AnalysisArtifact` con UUIDs;
- guardado aislado por canción en `media/tracks/<track_uuid>/`;
- un job local iniciado fuera del ciclo HTTP mediante `subprocess.Popen`;
- comando `python manage.py process_track <track_uuid>`;
- separación con la CLI de `audio-separator`;
- registro únicamente de stems efectivamente producidos;
- análisis básico del stem `DRUMS` con Essentia (`MonoLoader` y `RhythmExtractor2013`);
- archivo `drums.json` con duración, BPM, confianza, beats e intensidad normalizada;
- polling de estado en `/api/jobs/<uuid>/status/`;
- APIs JSON internas de tracks, stems y artifacts;
- página `/lab/` con análisis de batería finalizados;
- Django Admin.

## Arquitectura

```text
resonance/   configuración y URL raíz de Django
tracks/      carga, modelos Track/Stem, vistas web y APIs
processing/  ProcessingJob, servicio de separación y comando de gestión
analysis/    AnalysisArtifact y DrumsAnalyzer
templates/   interfaz Django
static/      estilos
docs/        documentación de producto y técnica
```

No se usa Celery, Redis, PostgreSQL, Docker, DRF ni WebSockets por ahora. SQLite y una ejecución local en proceso separado son decisiones deliberadas del MVP. La capa `StemSeparationService` permite sustituir más adelante el mecanismo de ejecución por una cola.

## Datos y archivos

```text
media/tracks/<track_uuid>/
├── source/original.<ext>
├── stems/<tipo>.<ext>
└── analysis/drums.json
```

Los nombres de directorio no dependen del título. La carga restringe extensiones y tamaño; `MAX_UPLOAD_SIZE_MB` vale 250 por defecto.

## Pipeline de job

1. `PENDING` → `PREPARING` (crea/prepara workspace).
2. `SEPARATING`: ejecuta `audio-separator -m <modelo> -o <stems_dir> <source>` sin shell.
3. Detecta nombres de stem reconocibles: vocals, drums, bass, guitar, piano, other e instrumental.
4. `ANALYZING`: si existe `DRUMS`, analiza beats y escribe `drums.json`.
5. Crea o actualiza `AnalysisArtifact(DRUMS, version=1)`.
6. Finaliza en `COMPLETED`; ante error persiste `FAILED` y un mensaje.

El modelo predeterminado es `UVR-MDX-NET-Inst_HQ_4.onnx`, ajustable con `AUDIO_SEPARATOR_DEFAULT_MODEL`. `audio-separator` puede requerir descargar el modelo la primera vez. El entorno instalado incluye soporte GPU, aunque la última verificación detectó CUDA no disponible (`torch.cuda.is_available() == False`), por lo que no se debe suponer GPU.

## Formato de drums.json

```json
{
  "format": "kinetra-resonance",
  "version": 1,
  "stem": "drums",
  "durationMs": 214000,
  "bpm": 118.4,
  "confidence": 3.82,
  "events": [
    {"timeMs": 421, "type": "beat", "intensity": 0.91}
  ]
}
```

Los timestamps están en milisegundos e `intensity` siempre pertenece a `[0.0, 1.0]`. Aún no se clasifica kick, snare, hi-hat o cymbal.

## Restricciones para cambios futuros

- Mantener el procesamiento pesado fuera del thread de la petición HTTP.
- No eliminar ni recrear `.venv`; no reinstalar dependencias sin necesidad.
- Mantener comandos sin `shell=True` y usar listas de argumentos para procesos externos.
- No usar títulos o filenames del usuario para construir rutas de almacenamiento.
- No añadir descarga desde Internet, URL ingestion ni funciones que eludan DRM.
- Conservar tests sin separación real: mockear el separador.
- Antes de terminar cambios, ejecutar `python manage.py makemigrations --check`, `migrate`, `check` y `test`.

## Próximo hito

Mejorar `DrumsAnalyzer` para detectar y clasificar `kick`, `snare`, `hi-hat` y `cymbal`, sin empezar todavía análisis de voz, letras, visemas o API de Teleo.
