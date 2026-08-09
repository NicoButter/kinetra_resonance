# Documentación técnica

## Requisitos y arranque

El proyecto usa Python, Django, NumPy, Essentia y `audio-separator`. Las dependencias declaradas están en `requirements.txt` y sus versiones instaladas en `requirements-lock.txt`.

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py runserver
```

En desarrollo, Django sirve los archivos de `media/` mediante `resonance.media.range_media`, con soporte de `Range`/`206 Partial Content` para que HTML Audio pueda hacer seek. No debe considerarse esa configuración apta para producción; el servidor o storage de producción también debe admitir byte ranges.

Configuración opcional, basada en variables de entorno:

| Variable | Predeterminado | Uso |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | clave solo de desarrollo | clave de Django |
| `DJANGO_DEBUG` | `True` | modo debug |
| `TELEO_SEPARATOR_MODEL` | `htdemucs_6s.yaml` | modelo Teleo de seis stems |
| `VOCAL_SEPARATOR_MODEL` | `UVR-MDX-NET-Inst_HQ_4.onnx` | modelo vocals/instrumental |
| `MAX_UPLOAD_SIZE_MB` | `250` | límite de carga |
| `DRUM_AUDITION_BEFORE_MS` | `150` | audio previo al hit durante audition |
| `DRUM_AUDITION_AFTER_MS` | `350` | audio posterior al hit durante audition |

Usar `.env.example` como referencia. Este MVP no carga automáticamente `.env`; el shell, el servicio de ejecución o una futura integración de configuración deben exportar las variables.

## Modelo de datos

| Modelo | Responsabilidad |
| --- | --- |
| `tracks.Track` | Audio original, metadatos y espacio de almacenamiento de una canción. |
| `processing.ProcessingJob` | Perfil, modelo, estado, progreso, etapa y error de un intento de procesamiento. |
| `tracks.Stem` | Un stem realmente generado y su tipo. Restricción única `(track, type)`. |
| `analysis.AnalysisArtifact` | Salida versionada `RAW`, `PROCESSED`, `REVIEWED` o `FINAL`, vinculada al job productor. |
| `analysis.ReviewSession` | Revisión versionada, estado, cursor de Undo/Redo y control optimista de versión. |
| `analysis.ReviewAction` | Acción humana inmutable con canal, evento, payload auditado, padre, secuencia y `batch_id` opcional. |

Todos los identificadores primarios son UUID. `ProcessingJob.track` es una clave foránea: una canción puede conservar varios jobs. Cada artifact tiene una FK obligatoria a su ProcessingJob para impedir que el builder mezcle ejecuciones. Los stems representan la separación vigente; los JSON históricos se conservan por job.

### Estados de ProcessingJob

`PENDING`, `PREPARING`, `SEPARATING`, `ANALYZING`, `COMPLETED`, `INCOMPLETE`, `FAILED`, `CANCELLED`.

`progress` se valida entre 0 y 100. En error se conserva `error_message` y se establece `finished_at`.

## Procesamiento

El formulario crea Track y ProcessingJob y llama a `tracks.services.launch_processing()`. Ese helper inicia un proceso independiente:

```bash
python manage.py process_track <job_uuid>
```

El comando limpia las salidas generadas del Track antes de reprocesar, preserva el source y conserva los registros históricos de ProcessingJob. No hay bloqueo de concurrencia en esta etapa; debe evitarse iniciar dos jobs simultáneos para el mismo Track.

`StemSeparationService` busca primero `.venv/bin/audio-separator` relativo a `BASE_DIR`; si no existe, intenta encontrarlo en `PATH`. Llama a la CLI sin shell, fuerza WAV y nombres de salida explícitos. `TELEO_6_STEM` usa `htdemucs_6s.yaml`; `VOCAL_EXTRACTION` usa el modelo UVR de dos stems.

Teleo requiere exactamente los tipos vocals, drums, bass, guitar, piano y other. Si falta alguno, el job termina en `INCOMPLETE`, informa los tipos esperados/recibidos y no ejecuta el analizador.

## Análisis de batería

`analysis.services.DrumsAnalyzer` carga el audio mono con Essentia. `RhythmExtractor2013(method='multifeature')` conserva BPM y confianza rítmica. `DrumOnsetDetector` conserva flujo espectral e intensidad RMS; `AutomaticDrumTranscriptionService` obtiene familias desde un backend intercambiable; `DrumEventFusionService` une ambos resultados uno-a-uno. `reviewedType` empieza en `null` y se modifica solamente al reconstruir ReviewActions. Artifacts históricos con `type/confidence` o `detectedType/detectedConfidence` se adaptan en memoria y no se reescriben.

La implementación `ADTOFDrumTranscriptionBackend` y `ADTOFMidiAdapter` importa ADTOF/torch/pretty_midi de manera lazy durante el análisis. Ningún import opcional ocurre al iniciar Django. Consultá [Automatic Drum Transcription](DRUM_TRANSCRIPTION.md) para API verificada, instalación, mapping, fallback y licencia.

`DrumPieceType` define `unassigned`, `kick`, `snare`, `hi_hat`, `tom`, `crash`, `splash`, `ride`, `cymbal` y `unknown`. `ASSIGN_DRUM_PIECE` cambia la lane semántica sin cambiar el timestamp. `MOVE` cambia el timestamp sin cambiar la lane. No existen sub-stems kick/snare/hi-hat: `drums.wav` permanece intacto.

La clasificación y la revisión son dimensiones independientes: `automaticType` es la sugerencia, `reviewedType` es la decisión humana, `effectiveType` resuelve `reviewedType ?? automaticType` y `reviewStatus` es `UNREVIEWED`, `CONFIRMED`, `OVERRIDDEN`, `MANUAL` o `DELETED`. El editor posiciona todos los hits por `effectiveType`; por eso un kick sugerido pero aún no auditado permanece en KICK, no en UNASSIGNED.

`processed/drums.json` continúa como manifest compatible y se materializan `processed/drums/{kick,snare,hi_hat,tom,cymbal,unassigned}.json`. Al finalizar una revisión se conservan los manifests versionados y se agregan `reviewed/vN/drums/` por pieza. Son JSON de metadata con IDs estables, nunca audio separado. Teleo conserva su timeline global y expone grupos de drums por familia para evitar filtrado runtime.

Todos los analizadores cargan a 44.1 kHz de forma explícita. Bajo, guitarra y piano usan onsets por flujo espectral y pitch por autocorrelación con umbral de confianza. Vocals y other generan frames temporales normalizados. Consultar `ANALYSIS_PIPELINE.md` para algoritmos y limitaciones.

## Postprocesamiento y calidad

`MusicalPostProcessor` carga únicamente los seis artifacts RAW del job y ejecuta postprocesadores separados. Bass/guitar fusionan segmentos compatibles con thresholds configurables; drums aplica refractory windows por tipo sin reclasificar UNKNOWN; vocals/other suavizan y reducen frames con heartbeat; piano conserva notas y delega la detección patológica al validador.

`QualityValidator` añade a cada artifact procesado `{status, score, warnings, metrics}`. El builder consulta exclusivamente `stage=PROCESSED`. Un canal `unreliable` conserva sus datos procesados para inspección, pero entrega una colección vacía a las estructuras renderizables de Teleo.

## Rutas

| Ruta | Método | Propósito |
| --- | --- | --- |
| `/` | GET | Inicio y tracks recientes |
| `/tracks/new/` | GET, POST | Carga y creación de job |
| `/tracks/<uuid>/` | GET | Estado, stems y resultado |
| `/tracks/<uuid>/delete/` | GET, POST | Confirmación y borrado permanente del agregado del track |
| `/lab/` | GET | Laboratorio de batería |
| `/lab/jobs/<job_uuid>/` | GET | Laboratorio RAW/PROCESSED sincronizado con audio |
| `/review/jobs/<job_uuid>/` | GET | Resonance Review Editor |
| `/api/jobs/<uuid>/status/` | GET | Polling de job |
| `/api/tracks/` | GET | Lista de tracks |
| `/api/tracks/<uuid>/` | GET | Track individual |
| `/api/tracks/<uuid>/stems/` | GET | Stems detectados |
| `/api/tracks/<uuid>/analysis/` | GET | Artifacts generados |

Las APIs usan `JsonResponse`; no hay autenticación ni Django REST Framework en el MVP local.

Los endpoints de `/api/reviews/` guardan acciones, reconstruyen REVIEWED, mueven el cursor Undo/Redo, resumen y finalizan. Todas las escrituras requieren la versión actual de la sesión; una versión obsoleta responde `409`.

`POST /api/reviews/<session>/actions/batch/` crea acciones individuales con un `batch_id` común y avanza la versión optimista una vez. El endpoint de datos incluye `drumReview` y `deletedDrums` para contadores/auditoría sin incorporar eliminados al artifact materializado.

## Eliminación de tracks

`TrackDeletionService` trata al track como un agregado de propiedad. El grafo de base de datos es: `Track → ProcessingJob` (`processing_jobs`, `CASCADE`), `Track → Stem` (`stems`, `CASCADE`), `Track/ProcessingJob → AnalysisArtifact` (`analysis_artifacts`, `CASCADE`), `ProcessingJob → ReviewSession` (`review_sessions`, `CASCADE`) y `ReviewSession → ReviewAction` (`actions`, `CASCADE`). La cadena `ReviewAction.parent` también usa `CASCADE`: es un historial interno de la misma revisión y no debe bloquear el borrado del track.

El servicio resuelve únicamente `MEDIA_ROOT/tracks/<track_uuid>`, comprueba que permanezca dentro de `MEDIA_ROOT`, elimina las filas dentro de `transaction.atomic()` y programa `shutil.rmtree()` con `transaction.on_commit()`. Si el filesystem falla tras el commit, se registra el error y no se recrean filas de base de datos. No hay signals de borrado ni un segundo mecanismo que elimine esos archivos.

## Seguridad y límites actuales

- Solo se aceptan extensiones explícitas y archivos no vacíos hasta el tamaño configurado.
- Las rutas de archivos siempre se derivan del UUID y un nombre interno fijo; no del título.
- El origen de subida no permite URLs.
- La validación de extensión no sustituye una validación profunda del contenido. Para un despliegue expuesto se necesitarían autenticación, límites de tasa, validación MIME/contenido, almacenamiento privado y una estrategia de workers.
- No se debe publicar `DJANGO_SECRET_KEY` ni los directorios `media/`, bases SQLite o archivos `.env`.

## Pruebas y verificaciones

```bash
python manage.py makemigrations --check
python manage.py migrate
python manage.py check
python manage.py test
```

Los tests cubren modelos/rutas de almacenamiento, validación de carga, creación de job, endpoint de estado, API de stems y normalización del analizador. No ejecutan una separación real.

## Evolución prevista

1. Clasificación de eventos de batería: kick, snare, hi-hat y cymbal.
2. Revisar sample rate, duración y metadatos de audio de forma uniforme.
3. Incorporar cola de jobs persistente (Celery/RQ) si se requieren concurrencia y reintentos.
4. Añadir análisis de otros instrumentos, API autenticada para Teleo y despliegue de producción.
