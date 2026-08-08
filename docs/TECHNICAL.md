# Documentación técnica

## Requisitos y arranque

El proyecto usa Python, Django, NumPy, Essentia y `audio-separator`. Las dependencias declaradas están en `requirements.txt` y sus versiones instaladas en `requirements-lock.txt`.

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py runserver
```

En desarrollo, Django sirve los archivos de `media/`. No debe considerarse esa configuración de archivos estáticos/media apta para producción.

Configuración opcional, basada en variables de entorno:

| Variable | Predeterminado | Uso |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | clave solo de desarrollo | clave de Django |
| `DJANGO_DEBUG` | `True` | modo debug |
| `TELEO_SEPARATOR_MODEL` | `htdemucs_6s.yaml` | modelo Teleo de seis stems |
| `VOCAL_SEPARATOR_MODEL` | `UVR-MDX-NET-Inst_HQ_4.onnx` | modelo vocals/instrumental |
| `MAX_UPLOAD_SIZE_MB` | `250` | límite de carga |

Usar `.env.example` como referencia. Este MVP no carga automáticamente `.env`; el shell, el servicio de ejecución o una futura integración de configuración deben exportar las variables.

## Modelo de datos

| Modelo | Responsabilidad |
| --- | --- |
| `tracks.Track` | Audio original, metadatos y espacio de almacenamiento de una canción. |
| `processing.ProcessingJob` | Perfil, modelo, estado, progreso, etapa y error de un intento de procesamiento. |
| `tracks.Stem` | Un stem realmente generado y su tipo. Restricción única `(track, type)`. |
| `analysis.AnalysisArtifact` | Salida de análisis versionada; por ahora solo `DRUMS`. |

Todos los identificadores primarios son UUID. `ProcessingJob.track` es una clave foránea: una canción puede conservar varios jobs de reprocesamiento. Los stems y artifacts vinculados al Track representan el resultado vigente.

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

`analysis.services.DrumsAnalyzer` carga el audio mono con Essentia. `RhythmExtractor2013(method='multifeature')` devuelve BPM, beats y confianza. Para cada beat se calcula la media de amplitud absoluta en una ventana de 50 ms; el vector resultante se normaliza por su máximo. La salida se redondea y serializa a JSON UTF-8.

Actualmente se asume 44.1 kHz al convertir posiciones de beat a índices de muestra. Un refinamiento futuro debería obtener el sample rate efectivo y ampliar la robustez frente a archivos inusuales.

## Rutas

| Ruta | Método | Propósito |
| --- | --- | --- |
| `/` | GET | Inicio y tracks recientes |
| `/tracks/new/` | GET, POST | Carga y creación de job |
| `/tracks/<uuid>/` | GET | Estado, stems y resultado |
| `/lab/` | GET | Laboratorio de batería |
| `/api/jobs/<uuid>/status/` | GET | Polling de job |
| `/api/tracks/` | GET | Lista de tracks |
| `/api/tracks/<uuid>/` | GET | Track individual |
| `/api/tracks/<uuid>/stems/` | GET | Stems detectados |
| `/api/tracks/<uuid>/analysis/` | GET | Artifacts generados |

Las APIs usan `JsonResponse`; no hay autenticación ni Django REST Framework en el MVP local.

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
