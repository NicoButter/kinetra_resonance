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
- perfiles explícitos `TELEO_6_STEM` y `VOCAL_EXTRACTION`;
- comando `python manage.py process_track <job_uuid>`;
- separación con la CLI de `audio-separator`;
- registro únicamente de stems efectivamente producidos;
- analizadores RAW separados para drums, bass, guitar, piano, vocals y other;
- `MusicalPostProcessor` y postprocesadores por canal que generan artifacts PROCESSED sin alterar RAW;
- `QualityValidator` con estado, score, warnings y metrics por canal;
- `ReviewSession`/`ReviewAction` y Resonance Review Editor no destructivo;
- editor multilanes de batería con `ASSIGN_DRUM_PIECE`, batches, audition y Rapid Drum Review;
- artifacts JSON asociados obligatoriamente al ProcessingJob productor;
- `TeleoExperienceBuilder` que valida los seis artifacts y genera `teleo_experience.json`;
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

No se usa Celery, Redis, PostgreSQL, Docker, DRF ni WebSockets por ahora. SQLite y una ejecución local en proceso separado son decisiones deliberadas del MVP. Cada Track conserva historial de ProcessingJobs y puede reprocesarse sin volver a cargar el source.

## Datos y archivos

```text
media/tracks/<track_uuid>/
├── source/original.<ext>
├── stems/<tipo>.<ext>
└── analysis/<job_uuid>/{raw/,processed/,reviewed/vN/,teleo_experience*.json}
```

Los nombres de directorio no dependen del título. La carga restringe extensiones y tamaño; `MAX_UPLOAD_SIZE_MB` vale 250 por defecto.

## Pipeline de job

1. `PENDING` → `PREPARING` (crea/prepara workspace).
2. `SEPARATING`: ejecuta `audio-separator` sin shell, con modelo, directorio, WAV y nombres de salida explícitos.
3. Detecta nombres de stem reconocibles: vocals, drums, bass, guitar, piano, other e instrumental.
4. En Teleo valida obligatoriamente los seis tipos; si falta alguno finaliza en `INCOMPLETE`.
5. `ANALYZING`: ejecuta los seis analizadores y registra artifacts RAW.
6. `POSTPROCESSING`: genera artifacts PROCESSED sin sobrescribir RAW.
7. `QUALITY`: valida cada canal.
8. `BUILDING`: usa exclusivamente PROCESSED y genera `teleo_experience.json`.

El perfil predeterminado es `TELEO_6_STEM`, con `htdemucs_6s.yaml`. `VOCAL_EXTRACTION` conserva la salida vocals/instrumental con `UVR-MDX-NET-Inst_HQ_4.onnx`. Ambos modelos son configurables por entorno. No se debe suponer GPU disponible.

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
    {"timeMs": 421, "detectedType": "kick", "detectedConfidence": 0.73, "reviewedType": null, "intensity": 0.91}
  ]
}
```

Los timestamps están en milisegundos e `intensity` siempre pertenece a `[0.0, 1.0]`. `detectedType` es una sugerencia automática y nunca se sobrescribe; `reviewedType` contiene exclusivamente la asignación humana. El editor admite kick, snare, hi-hat, tom, crash, splash, ride, cymbal y unknown. Un hit sin revisión permanece visualmente en `UNASSIGNED`.

## Restricciones para cambios futuros

- Mantener el procesamiento pesado fuera del thread de la petición HTTP.
- No eliminar ni recrear `.venv`; no reinstalar dependencias sin necesidad.
- Mantener comandos sin `shell=True` y usar listas de argumentos para procesos externos.
- No usar títulos o filenames del usuario para construir rutas de almacenamiento.
- No añadir descarga desde Internet, URL ingestion ni funciones que eludan DRM.
- Conservar tests sin separación real: mockear el separador.
- Antes de terminar cambios, ejecutar `python manage.py makemigrations --check`, `migrate`, `check` y `test`.

## Próximo hito

Usar el dataset AI-vs-human exportable para evaluar y mejorar `DrumClassifier`, sin entrenar automáticamente ni implementar todavía la representación visual/háptica de Teleo.

El destino arquitectónico es: audio original → seis stems → analizadores por stem → JSON estructurados (`metadata`, instrumentos, timeline, visemas, haptics) → paquete de experiencia Teleo. Los WAV son material intermedio, no el producto final principal.
