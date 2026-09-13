# Pipeline de análisis y Teleo Experience

Kinetra Resonance convierte música en datos temporales estructurados para experiencias visuales y táctiles. Los stems son material intermedio, no el producto final.

```text
Original audio
      ↓
6-stem separation
      ↓
Vocals / Drums / Bass / Guitar / Piano / Other
      ↓
Per-stem analyzers
      ↓
RAW artifacts (inmutables)
      ↓
MusicalPostProcessor
      ↓
PROCESSED artifacts
      ↓
QualityValidator
      ↓
TeleoExperienceBuilder
      ↓
teleo_experience.json (canonical artifact)
      ↓
TeleoPublicationValidator
      ↓
LocalBundlePublisher
      ↓
catalog.json + tracks/<uuid>/experience.json
```

La rama de batería dentro de ese flujo es:

```text
drums.wav
  ├─→ AutomaticDrumTranscriptionService → ADTOF backend → class + onset
  └─→ DrumOnsetDetector → possible hits + audio intensity
                              ↓
                    DrumEventFusionService (±50 ms)
                              ↓
                  automatic DrumEvents (RAW)
                              ↓
                    DrumsPostProcessor
                              ↓
          Review Editor → reviewed/vN/drums.json
                              ↓
              teleo_experience.reviewed.json
```

MIDI es exclusivamente el transporte intermedio del adapter ADTOF. No forma parte del dominio principal, de los artifacts Teleo ni de `teleo_experience*.json`.

## Analizadores

| Analizador | Algoritmo inicial | Salida | Limitación principal |
| --- | --- | --- | --- |
| `DrumsAnalyzer` | ADTOF opcional para familia/onset + detector local para onsets/intensidad + fusión temporal | eventos automáticos, duración, tipo, intensidad y metadata del backend | ADTOF solo propone kick/snare/hi-hat/tom/cymbal y no entrega confidence por evento. Crash/splash/ride son refinamientos humanos. |
| `BassAnalyzer` | onsets por flujo espectral, pitch por autocorrelación | notas con rango, Hz, MIDI, nombre, intensidad y confianza | Omite pitch/MIDI cuando la confianza es menor a 0.45. |
| `GuitarAnalyzer` | igual que bass, con rango de guitarra y descriptor de ataque | notas y ataque | Aproximación monofónica; acordes pueden no transcribirse correctamente. |
| `PianoAnalyzer` | onsets y pitch conservador | colección de notas | El schema permite notas simultáneas, pero el algoritmo inicial no es una transcripción polifónica completa. |
| `VocalsAnalyzer` | frames de 40 ms, energía, pitch por autocorrelación y brillo espectral; enriquecimiento con cues de Rhubarb | presencia, intensidad, pitch, brillo y visemas A–H/X | No contiene letras ni fonemas verificados. Los visemas son aproximaciones visuales. |
| `OtherAnalyzer` | energía FFT por bandas en frames de 50 ms | energía low/mid/high/overall normalizada | Describe ambiente energético, no identifica instrumentos. |

Los timestamps y duraciones son enteros en milisegundos. Los valores normalizados se recortan a `[0, 1]` y usan hasta cuatro decimales. Todas las colecciones quedan ordenadas temporalmente.

## Teleo Experience schema v1

El builder solo lee `AnalysisArtifact(stage=PROCESSED)` relacionados con el ProcessingJob actual y exige `DRUMS`, `BASS`, `GUITAR`, `PIANO`, `VOCALS` y `OTHER`. Si falta alguno, no genera una experiencia completa y el job termina en `INCOMPLETE`.

La experiencia incluye metadatos del track, eventos/notas/frames por canal y visemas vocales cuando Rhubarb produjo cues válidos. Los arrays futuros `lyrics`, `sections` y `haptics` permanecen vacíos. No se genera contenido ficticio.

`timeline` contiene eventos discretos de percusión, comienzos de notas de bass/guitar/piano y cambios de visema. Los frames continuos de vocals/other no se duplican allí para controlar el tamaño móvil; Teleo puede leerlos desde sus canales dedicados.

Los artifacts se almacenan en:

```text
media/tracks/<track_uuid>/analysis/<job_uuid>/
├── raw/
├── processed/
└── teleo_experience.json
```

Esto conserva resultados históricos y evita contaminación entre reprocesamientos.

El builder continúa siendo la única fuente canónica y nunca vuelve a analizar audio durante un export. La publicación selecciona el artifact FINAL del mismo job, prefiriendo `TELEO_REVIEWED` únicamente cuando su `ReviewSession` está completada. Resuelve valores efectivos, agrega versionado de experiencia y `sourceHash`, valida el protocolo y escribe el bundle mediante reemplazo atómico.

Los artifacts internos permanecen bajo `MEDIA_ROOT`; la salida pública vive por defecto en `var/teleo_publish/` y contiene solamente `catalog.json` y `tracks/<uuid>/experience.json`. No incluye audio, stems, RAW/PROCESSED/REVIEWED, diagnósticos ni rutas locales.

Cada processed artifact contiene un bloque `quality`. Teleo Experience expone esos bloques en `channelsQuality`; los eventos de canales `unreliable` no entran en las colecciones de render ni en la timeline.

`raw/drums.json` añade `transcription`: backend, versión, device efectivo, tiempo, clases, conteos, matching, warnings y uso de fallback. La misma metadata se copia a `ProcessingJob.metadata.drumTranscription` para observabilidad. Un evento ADTOF emparejado toma intensidad del audio local; uno ADTOF sin onset local se conserva; un onset local sin ADTOF se conserva como `UNASSIGNED` con `source: "kinetra-onset"`.

## Analysis Lab

`/lab/jobs/<job_uuid>/` carga original, stems y ambos niveles de artifacts. El tiempo proviene exclusivamente de `audio.currentTime`; el Canvas se actualiza con `requestAnimationFrame`. Cada frame visual consulta solo la ventana visible mediante búsqueda binaria sobre colecciones ordenadas. Esa ventana puede ajustarse entre 0.25 y 30 segundos.

La fuente de audio y la visualización permanecen vinculadas: Original muestra todos los canales; drums, bass, guitar, piano y other enfocan su artifact; cualquier variante vocal enfoca los visemas. En vocals, el Lab muestra la timeline A–H/X y el mismo renderer SVG articulatorio del Review Editor. `Space` controla play/pausa, el arrastre sobre espacio vacío desplaza `audio.currentTime` y **Track position** permite recorrer toda la canción. Los controles de etapa, zoom y confianza solo cambian la vista y nunca reescriben datos.

## Estados y errores

El job avanza por separación, análisis de cada stem y construcción de experiencia usando `current_stage` y progreso aproximado. Una separación sin seis stems o un conjunto incompleto termina en `INCOMPLETE`. Una excepción de algoritmo o serialización termina en `FAILED`, registra el analizador en `error_message` y escribe el traceback mediante logging.

## Evolución

Las interfaces mantienen separados los analizadores para incorporar transcripción polifónica, modelos de percusión, `VisemeAnalyzer`, `LyricsAnalyzer`, alineación, traducción, secciones y generación háptica sin cambiar el contrato superior del schema v1 mientras sea posible.
## Vocal accessibility branch

The master audio now feeds two independent branches. `htdemucs_6s` continues to own music-analysis stems. For lip-sync, the selected vocal accessibility profile resolves a separate input before Rhubarb: Standard reuses `stems/vocals.wav`, while Clean (the default) uses the original audio with audio-separator's official `vocal_clean` preset. The job-owned clean WAV and diagnostics live beneath `analysis/<job UUID>/intermediate/vocals/`. Teleo still consumes only canonical processed/reviewed visemes and receives no separator model names or local paths.
