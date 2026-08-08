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
teleo_experience.json
```

## Analizadores

| Analizador | Algoritmo inicial | Salida | Limitación principal |
| --- | --- | --- | --- |
| `DrumsAnalyzer` | `RhythmExtractor2013`, flujo espectral y distribución de energía por bandas | eventos, duración, tipo, intensidad y confianza | Clasificación conservadora; usa `unknown` cuando no hay evidencia suficiente. Crash/cymbal quedan preparados pero requieren un modelo especializado. |
| `BassAnalyzer` | onsets por flujo espectral, pitch por autocorrelación | notas con rango, Hz, MIDI, nombre, intensidad y confianza | Omite pitch/MIDI cuando la confianza es menor a 0.45. |
| `GuitarAnalyzer` | igual que bass, con rango de guitarra y descriptor de ataque | notas y ataque | Aproximación monofónica; acordes pueden no transcribirse correctamente. |
| `PianoAnalyzer` | onsets y pitch conservador | colección de notas | El schema permite notas simultáneas, pero el algoritmo inicial no es una transcripción polifónica completa. |
| `VocalsAnalyzer` | frames de 40 ms, energía, pitch por autocorrelación y brillo espectral | presencia, intensidad, pitch y brillo | No contiene letra, fonemas ni visemas. Reduce frames con pocos cambios. |
| `OtherAnalyzer` | energía FFT por bandas en frames de 50 ms | energía low/mid/high/overall normalizada | Describe ambiente energético, no identifica instrumentos. |

Los timestamps y duraciones son enteros en milisegundos. Los valores normalizados se recortan a `[0, 1]` y usan hasta cuatro decimales. Todas las colecciones quedan ordenadas temporalmente.

## Teleo Experience schema v1

El builder solo lee `AnalysisArtifact(stage=PROCESSED)` relacionados con el ProcessingJob actual y exige `DRUMS`, `BASS`, `GUITAR`, `PIANO`, `VOCALS` y `OTHER`. Si falta alguno, no genera una experiencia completa y el job termina en `INCOMPLETE`.

La experiencia incluye metadatos del track, eventos/notas/frames por canal y los arrays futuros `visemes`, `lyrics`, `sections` y `haptics`, inicialmente vacíos. No se genera contenido ficticio.

`timeline` contiene eventos discretos de percusión y comienzos de notas de bass/guitar/piano. Los frames continuos de vocals/other no se duplican allí para controlar el tamaño móvil; Teleo puede leerlos desde sus canales dedicados.

Los artifacts se almacenan en:

```text
media/tracks/<track_uuid>/analysis/<job_uuid>/
├── raw/
├── processed/
└── teleo_experience.json
```

Esto conserva resultados históricos y evita contaminación entre reprocesamientos.

Cada processed artifact contiene un bloque `quality`. Teleo Experience expone esos bloques en `channelsQuality`; los eventos de canales `unreliable` no entran en las colecciones de render ni en la timeline.

## Analysis Lab

`/lab/jobs/<job_uuid>/` carga original, stems y ambos niveles de artifacts. El tiempo proviene exclusivamente de `audio.currentTime`; el Canvas se actualiza con `requestAnimationFrame`. Cada frame visual consulta solo la ventana −5/+10 segundos mediante búsqueda binaria sobre colecciones ordenadas. El control de confianza es visual y nunca reescribe datos.

## Estados y errores

El job avanza por separación, análisis de cada stem y construcción de experiencia usando `current_stage` y progreso aproximado. Una separación sin seis stems o un conjunto incompleto termina en `INCOMPLETE`. Una excepción de algoritmo o serialización termina en `FAILED`, registra el analizador en `error_message` y escribe el traceback mediante logging.

## Evolución

Las interfaces mantienen separados los analizadores para incorporar transcripción polifónica, modelos de percusión, `VisemeAnalyzer`, `LyricsAnalyzer`, alineación, traducción, secciones y generación háptica sin cambiar el contrato superior del schema v1 mientras sea posible.
