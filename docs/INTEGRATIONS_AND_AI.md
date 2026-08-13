# Integraciones, extensiones e IA

Este documento es el inventario operativo de software externo que Kinetra
Resonance usa para separar o analizar audio y para representar el resultado.
Indica qué datos entran y salen, por qué se usa cada componente y si es
requerido, opcional o solamente una dependencia de interfaz.

## Alcance y principios

- Toda la ejecución de análisis es local. El proyecto no llama a OpenAI,
  ChatGPT, Whisper, Gemini, Anthropic ni a ninguna API de IA en la nube.
- Los modelos descargables se almacenan localmente; no se versionan en Git.
- Una sugerencia automática nunca sustituye la revisión humana. RAW conserva
  la propuesta, REVIEWED conserva acciones auditables y Teleo recibe el
  resultado procesado/revisado.
- `requirements.txt` define el núcleo, `requirements-adt.txt` el backend de
  batería experimental y `requirements-lock.txt` registra el entorno resuelto.
  Los modelos y binarios externos tienen sus propias licencias.

## Mapa de integración

```text
Audio original
  ├─ audio-separator + modelos locales ──> stems / voz limpia
  │                                        ↓
  │                                  Rhubarb Lip Sync ──> visemas A–H/X
  └─ Essentia + algoritmos Kinetra ───────> onsets, energía, pitch, BPM
                                             ↑
                         ADTOF opcional ────┘ (familias de batería)

Visemas + revisión humana ─> SVG anatómico + Anime.js (solo interfaz web)
```

## Modelos y herramientas de análisis

| Componente | Tipo / estado | Para qué se usa | Entrada → salida | Configuración y fallback |
| --- | --- | --- | --- | --- |
| `audio-separator` | IA local requerida para separar stems | Divide una canción en stems para el análisis y, por separado, puede aislar una voz más limpia para lip-sync. | Audio original → WAVs de stems o `vocals_lipsync.wav`. | Núcleo: `audio-separator[gpu] >=0.44.5,<0.45`. Usa `AUDIO_SEPARATOR_MODEL_DIR`; la primera ejecución puede descargar pesos. Si falla la separación de seis stems, el job no puede completarse. |
| `htdemucs_6s.yaml` | Modelo de separación local predeterminado | Obtiene `vocals`, `drums`, `bass`, `guitar`, `piano` y `other` para el perfil **Teleo Music — 6 stems**. | Original → seis stems musicales. | `TELEO_SEPARATOR_MODEL`. Es la rama musical canónica; no se reemplaza por la limpieza vocal. |
| `UVR-MDX-NET-Inst_HQ_4.onnx` | Modelo de separación local opcional | Perfil **Vocal extraction**, con salida vocals/instrumental. | Original → `vocals` + `instrumental`. | `VOCAL_SEPARATOR_MODEL`. No entrega los seis stems requeridos por Teleo. |
| Preset `vocal_clean` de audio-separator | Ensemble local, perfil de accesibilidad | Intenta reducir acompañamiento para que Rhubarb reciba una voz más adecuada. | Original → `analysis/<job>/intermediate/vocals/vocals_lipsync.wav`. | `VOCAL_ISOLATION_PRESET`, `VOCAL_ISOLATION_CPU_FALLBACK`, `VOCAL_ISOLATION_FALLBACK_ALLOWED`, `VOCAL_ISOLATION_REQUIRED`. Si se autoriza fallback, Rhubarb usa `stems/vocals.wav` y el metadata lo declara explícitamente. Kinetra no duplica ni fija la composición interna del preset. |
| Rhubarb Lip Sync 1.14 | Binario local de reconocimiento/lip-sync | Propone visemas temporales para el canal vocal. No transcribe letras ni certifica fonemas. | Voz limpia o stem vocal → JSON `mouthCues` → visemas Kinetra A–H/X. | `RHUBARB_*`, `VOCAL_LANGUAGE`, `LIPSYNC_REQUIRED`. Se verifica binario/version antes de analizar. Si no está disponible, se conservan frames vocales tradicionales; con `LIPSYNC_REQUIRED=true` el job es terminal. Véase [vocal-lip-sync.md](vocal-lip-sync.md). |
| ADTOF-pytorch | IA local opcional y experimental | Propone familia de batería y onset: kick, snare, hi-hat, tom o cymbal. | `drums.wav` → MIDI temporal → eventos Kinetra. | Instalar `requirements-adt.txt`. `DRUM_TRANSCRIPTION_BACKEND`, `DRUM_TRANSCRIPTION_DEVICE`, `DRUM_TRANSCRIPTION_ENABLED`. Ante ausencia/fallo, Kinetra continúa con onsets locales `UNASSIGNED` para revisión humana. Véase [DRUM_TRANSCRIPTION.md](DRUM_TRANSCRIPTION.md). |
| PyTorch / CUDA / ONNX Runtime | Runtime de modelos, transitivo u opcional | Ejecutan modelos que los paquetes anteriores requieren; no forman una API de negocio de Kinetra. | Tensores/modelos locales → inferencia local. | El dispositivo se resuelve por backend. ADTOF prueba CPU tras error CUDA; aislamiento vocal respeta su política de fallback configurada. No asumir que tener CUDA instalada implica que el modelo esté disponible. |

### Cómo se preservan los resultados automáticos

Los nombres de modelo, rutas locales, diagnósticos y pesos no se exportan a
`teleo_experience.json`. Kinetra guarda los artefactos del job en
`media/tracks/<track UUID>/analysis/<job UUID>/`; Teleo recibe datos temporales
compactos, no el runtime web ni los modelos. Las visemas siguen siendo una
propuesta de Rhubarb: `reviewedShape` tiene precedencia sobre
`automaticShape` de forma inmediata en el editor.

## Librerías de análisis que no son IA

| Componente | Uso en Kinetra | Qué no hace |
| --- | --- | --- |
| Essentia | Carga mono a 44.1 kHz, BPM/rhythm, flujo espectral y descriptores de audio. | No clasifica automáticamente instrumentos completos ni genera letras. |
| NumPy | Cálculo numérico de energía, FFT, autocorrelación, normalización y postprocesamiento. | No incorpora modelos entrenados por sí mismo. |
| Algoritmos propios de Kinetra | Detectan onsets, estiman pitch monofónico, energía/presencia vocal y bandas de `other`; fusionan resultados y validan calidad. | No son IA generativa ni sustituyen ADTOF/Rhubarb. Bajo, guitarra y piano siguen siendo aproximaciones conservadoras; piano no es transcripción polifónica completa. |
| `pretty_midi` y `mido` | Adaptan el MIDI temporal producido por ADTOF a eventos de dominio Kinetra. | MIDI no se guarda en Teleo Experience ni su velocidad se trata como confianza/intensidad. |
| FFmpeg | Requisito del entorno que audio-separator comprueba para aislamiento vocal. | No es un modelo ni toma decisiones de análisis. |

## Interfaz, extensiones y runtime web

| Componente | Para qué se usa | Datos y límites |
| --- | --- | --- |
| Django | Aplicación local, jobs, persistencia, revisión y entrega de media/static. | No contiene IA; el procesamiento pesado corre fuera de la petición HTTP mediante `process_track`. |
| Anime.js 4.5.0 | Suaviza morphs y transiciones discretas del SVG de la boca. | Bundle local en `static/vendor/animejs/`, licencia MIT, sin npm ni red en tiempo de ejecución. No interpreta fonemas. |
| SVG + `SvgAnimeMouthRenderer` | Representa mandíbula, labios, dientes y lengua desde `MouthPose`. | Sólo recibe parámetros anatómicos normalizados; no conoce códigos Rhubarb. No es el renderer de Teleo Android. |
| HTML Audio, Canvas y `requestAnimationFrame` | Reproducción y visualización sincronizada del Lab/Review Editor. | `audio.currentTime` es el único reloj; no existe una timeline paralela. Canvas no modifica artifacts. |

## Configuración mínima por caso

```bash
# Separación musical canónica
TELEO_SEPARATOR_MODEL=htdemucs_6s.yaml

# Voz limpia opcional para Rhubarb
VOCAL_ISOLATION_PRESET=vocal_clean
VOCAL_ISOLATION_FALLBACK_ALLOWED=true

# Visemas
RHUBARB_ENABLED=true
RHUBARB_RECOGNIZER=phonetic
RHUBARB_EXTENDED_SHAPES=GHX
LIPSYNC_REQUIRED=true

# Transcripción automática de batería (opcional)
DRUM_TRANSCRIPTION_ENABLED=True
DRUM_TRANSCRIPTION_BACKEND=adtof
DRUM_TRANSCRIPTION_DEVICE=auto
```

La lista completa y valores de referencia están en [`.env.example`](../.env.example).
El proyecto no carga este archivo por sí mismo: hay que exportar las variables
en el entorno que inicia Django.

## Instalación, licencia y operación

1. Instalar el núcleo con `pip install -r requirements.txt`.
2. Instalar ADTOF sólo si se necesita clasificación automática de batería:
   `pip install -r requirements-adt.txt`.
3. Instalar un binario compatible de Rhubarb y configurarlo mediante
   `RHUBARB_BINARY`, ubicarlo en `tools/rhubarb/...`, o exponerlo en `PATH`.
4. Mantener FFmpeg disponible para el aislamiento vocal y un directorio de
   modelos escribible para audio-separator.
5. Antes de distribuir comercialmente, revisar por separado licencias de
   audio-separator/modelos, Rhubarb y ADTOF. En particular, ADTOF es
   experimental y su upstream no declara licencia explícita en el commit fijado.

Para la arquitectura del pipeline, consultar [ANALYSIS_PIPELINE.md](ANALYSIS_PIPELINE.md); para límites, instalación y licencia de ADTOF, [DRUM_TRANSCRIPTION.md](DRUM_TRANSCRIPTION.md); y para visemas/articulación, [vocal-lip-sync.md](vocal-lip-sync.md) y [VOCAL_ARTICULATION.md](VOCAL_ARTICULATION.md).
