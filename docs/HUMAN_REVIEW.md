# Human Review — Resonance Review Editor

La revisión humana nunca modifica `raw/` ni `processed/`.

```text
RAW → PROCESSED → ReviewSession + ordered ReviewActions
                → REVIEWED → teleo_experience.reviewed.json
```

## Auditoría y reconstrucción

`ReviewSession` pertenece a un ProcessingJob y admite versiones históricas. `ReviewAction` conserva acción, canal, event ID, valores originales/destino, secuencia, acción padre y un `batch_id` opcional.

La sesión guarda un cursor. Undo mueve el cursor al padre; Redo selecciona la rama hija más reciente. Crear una acción después de Undo crea otra rama sin borrar el audit trail anterior. REVIEWED se reconstruye siempre desde PROCESSED recorriendo la rama raíz→cursor; no se guardan snapshots por click.

Eventos nuevos usan IDs `manual-<channel>-<uuid>`. Los postprocesos nuevos asignan IDs deterministas como `drums-000001`. Para artifacts anteriores sin IDs, el loader los asigna en memoria por orden temporal sin reescribir el JSON legado.

## Acciones

- `DELETE`, `ADD`, `RELABEL`, `ASSIGN_DRUM_PIECE`, `MOVE`, `RESIZE`.
- `CHANGE_INTENSITY`, `CHANGE_PITCH`, `MERGE`, `SPLIT`.
- `CONFIRM` guarda `reviewMetadata.confirmedByHuman`.
- `MARK_RANGE` registra voz activa/silencio/sospechoso o rangos other unreliable/exclude/energy multiplier.

El backend valida canal, pertenencia al job, existencia de event ID, duración del track, intensidad `[0,1]`, MIDI `[0,127]`, tipos de drum y compatibilidad temporal/MIDI para merge. Una asignación masiva conserva una acción por evento y comparte `batch_id`; Undo/Redo mueve todo el batch como una unidad.

## Revisión semántica de batería

`drums.wav` sigue siendo un único stem. Las lanes no crean, cortan ni duplican audio: solamente clasifican metadata temporal.

Cada detección conserva dos verdades separadas:

```json
{
  "id": "drums-000193",
  "timeMs": 84230,
  "detectedType": "kick",
  "detectedConfidence": 0.73,
  "reviewedType": null,
  "effectiveType": "kick"
}
```

`detectedType` es la sugerencia inmutable de la IA. `reviewedType` es la decisión humana. Todos los hits sin decisión humana se muestran en `UNASSIGNED`, aunque `effectiveType` pueda usar la predicción como fallback. `UNKNOWN` significa que el humano sí auditó el golpe pero no pudo identificarlo.

Los tipos admitidos son `UNASSIGNED`, `KICK`, `SNARE`, `HI_HAT`, `TOM`, `CRASH`, `SPLASH`, `RIDE`, `CYMBAL` y `UNKNOWN`.

## Editor

Ruta: `/review/jobs/<job_uuid>/`.

Comparte el renderer Canvas con Analysis Lab. El único reloj musical es `audio.currentTime`; el dibujo usa `requestAnimationFrame`, búsqueda binaria y solo eventos visibles. En DRUMS se convierte en diez lanes con drag vertical para asignar y `Shift+drag` horizontal para cambiar `timeMs`. Incluye selección Ctrl/Cmd+click, rango Shift+click, marquee, filtros, contadores y asignación masiva.

La barra **Full track navigation** permite recorrer la canción completa con pasos de 10 ms. Puede arrastrarse mientras el audio está pausado o reproduciéndose; actualiza directamente `audio.currentTime` sin cambiar el estado del reproductor.

`Audition Hit` reproduce desde el único `drums.wav` una ventana configurable —150 ms antes y 350 ms después por defecto— y luego restaura fuente, posición, velocidad y estado de reproducción. `Rapid Drum Review` encadena unassigned → audition → shortcut → siguiente unassigned.

| Tecla | Acción |
| --- | --- |
| Space | Play/pause |
| ← / → | ±100 ms |
| Shift+← / Shift+→ | ±1000 ms |
| A | Audition del hit seleccionado |
| K/S/H/T | Asignar Kick/Snare/Hi-hat/Tom |
| C/P/R/Y | Asignar Crash/Splash/Ride/Cymbal |
| U/N | Asignar Unassigned/Unknown |
| `[` / `]` | Unassigned anterior/siguiente |
| Delete | Eliminar selección o batch |
| Ctrl/Cmd+Z | Undo |
| Ctrl/Cmd+Y | Redo |

## Materialización

`Finish Human Review` presenta el resumen activo, recalcula quality y materializa:

```text
analysis/<job_uuid>/reviewed/v<review_version>/
├── drums.json
├── bass.json
├── guitar.json
├── piano.json
├── vocals.json
└── other.json
```

Luego genera `teleo_experience.reviewed.json` con metadata `status`, `reviewVersion`, `sourceAnalysisVersion`, `reviewSessionId` y `reviewedAt`. Cada canal contiene `analysisSource: "human-reviewed"`; no se distribuye identidad del reviewer.

El `reviewed/vN/drums.json` conserva `detectedType`, `detectedConfidence`, `reviewedType`, `effectiveType` y `originalDetection`. Su campo final `type` usa la decisión humana y cae en la predicción solo para hits pendientes. El Teleo Experience es deliberadamente compacto: distribuye `timeMs`, `durationMs`, `type` e `intensity`, sin `ReviewAction` ni datos internos del clasificador.

## Semántica destinada a Teleo

- `KICK`: pulso/círculo y háptica fuerte.
- `SNARE`: impacto vertical o lateral.
- `HI_HAT`: evento rápido y pequeño.
- `TOM`: pulso medio.
- `CRASH`: expansión grande.
- `SPLASH`: expansión corta.
- `RIDE`: evento brillante o sostenido.
- `CYMBAL`: platillo genérico.
- `UNKNOWN`: golpe genérico.

Esto documenta el contrato; Resonance no implementa todavía el renderer ni la háptica de Teleo.

## Prioridad futura de Teleo

Cuando exista `teleo_experience.reviewed.json`, Teleo debe preferirlo sobre la experiencia automática. Los niveles previstos son `AUTOMATIC`, `HUMAN_REVIEWED` y `TELEO_MASTER`. Ningún flujo actual asigna `TELEO_MASTER`; queda reservado para una futura revisión integral musical, lingüística, visual, háptica y de accesibilidad.

`ReviewDatasetExporter` exporta pares IA/humano para `ASSIGN_DRUM_PIECE`, `ADD`, `DELETE` y el `RELABEL` legado: detected kick → human snare, detected kick → DELETE o detected none → ADD kick. No entrena ni modifica modelos.
