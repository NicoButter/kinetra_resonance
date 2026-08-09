# Automatic Drum Transcription

## Arquitectura

```text
drums.wav
  ├─ AutomaticDrumTranscriptionService
  │    └─ ADTOFDrumTranscriptionBackend (experimental y reemplazable)
  │         └─ ADTOFMidiAdapter → Kinetra DrumEvent
  └─ DrumOnsetDetector → possible hits + intensity
                 ↓
       DrumEventFusionService
                 ↓
       DrumsPostProcessor → Human Review → Teleo
```

El dominio y el pipeline de aplicación dependen del protocolo `DrumTranscriptionBackend`, no del paquete `adtof_pytorch`. MIDI es un formato temporal privado del adapter; no se guarda en Teleo Experience. Los pesos y el código upstream tampoco se copian al repositorio.

## Upstream verificado

La integración fue comprobada contra [`xavriley/ADTOF-pytorch`](https://github.com/xavriley/ADTOF-pytorch) commit [`85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9`](https://github.com/xavriley/ADTOF-pytorch/commit/85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9), versión de paquete `0.1.0`.

- API real: `transcribe_to_midi(audio, midi_out, *, threshold=None, thresholds=None, fps=100, return_activations=False, weights=None, device="cuda")`.
- CLI real: `adtof --audio input.wav --out output.mid --device cuda|cpu`.
- Device upstream: acepta solamente `cuda` o `cpu` y cae internamente a CPU si CUDA no está disponible. Kinetra resuelve `auto` antes de invocarlo y además reintenta CPU ante un fallo de ejecución CUDA.
- Dependencias declaradas: `torch`, `librosa`, `pretty_midi`, `numpy`; Python `>=3.9`.
- Pesos: upstream los incluye como package data y los carga por defecto.
- Salida: un drum track MIDI con duración fija de nota de 0.1 s y velocidad fija 100. Esa velocidad no representa confidence ni intensidad.

El entorno probado usa Python 3.14.6, `adtof-pytorch 0.1.0`, `pretty_midi 0.2.11.post0` y `torch 2.13.0+cu130`. La instalación y la inferencia CPU fueron correctas. Aunque torch fue compilado con CUDA 13.0, `torch.cuda.is_available()` fue falso en esta máquina.

## Instalación opcional

Core y revisión manual:

```bash
pip install -r requirements.txt
```

Backend experimental adicional:

```bash
pip install -r requirements-adt.txt
```

`requirements-adt.txt` fija el commit inspeccionado. Kinetra inicia sin este archivo instalado; un análisis produce el warning `Automatic drum transcription unavailable. Human classification required.` y continúa con onsets `UNASSIGNED`.

## Configuración

```text
DRUM_TRANSCRIPTION_BACKEND=adtof
DRUM_TRANSCRIPTION_DEVICE=auto  # auto | cpu | cuda
DRUM_TRANSCRIPTION_ENABLED=True
DRUM_EVENT_MATCH_TOLERANCE_MS=50
```

## Mapping MIDI

Las cinco salidas exactas de ADTOF son:

| MIDI | Familia Kinetra |
| ---: | --- |
| 35 | `kick` |
| 38 | `snare` |
| 47 | `tom` |
| 42 | `hi_hat` |
| 49 | `cymbal` |

El adapter también colapsa variantes General MIDI cercanas dentro de esas cinco familias para robustez y convierte cualquier otra nota a `unknown`. Nunca infiere crash/splash/ride automáticamente. `confidence` permanece `null`; `intensity` se calcula desde `drums.wav` alrededor del onset.

## Fusión y fallback

La fusión realiza matching global y uno-a-uno dentro de ±50 ms:

- ADTOF + onset local: conserva tiempo/clase ADTOF e intensidad local.
- Solo ADTOF: conserva el evento y estima intensidad en ese tiempo.
- Solo onset local: crea `automatic.type: null`, `automaticType: "unassigned"`, `source: "kinetra-onset"`.

Si ADTOF no está instalado, no carga, falla en CPU/CUDA o produce MIDI inválido, el servicio devuelve un resultado de fallback en vez de propagar el error al `ProcessingJob`. RAW/PROCESSED y la revisión manual continúan disponibles.

## Prueba manual y comparación de migración

Prueba ejecutada sobre el stem real de 253.794 s del job `18735cb9-5e2d-44e6-8e19-04dc9a821323`, sin sobrescribir artifacts:

| Resultado | Total | Kick | Snare | Hi-hat | Tom | Cymbal | Unknown/Unassigned |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Clasificador heurístico anterior | 522 | 49 | 0 | 33 | 0 | 0 | 440 unknown |
| ADTOF puro | 589 | 134 | 123 | 198 | 117 | 17 | 0 |
| Fusión ADTOF + onsets | 710 | 134 | 123 | 198 | 117 | 17 | 121 unassigned |

La inferencia ADTOF tardó 6,47 s en CPU. Hubo 401 matches, 188 eventos solo ADTOF y 121 onsets locales sin match. Todos los 710 eventos fusionados tuvieron intensidad. El MIDI fue válido y se observaron exactamente las cinco familias automáticas.

## Licencia y distribución

**ADTOF backend is an optional experimental automatic drum transcription backend. Review upstream code/model licensing before commercial distribution.**

El commit inspeccionado de ADTOF-pytorch no contiene un archivo de licencia explícito y el paquete incluye pesos convertidos desde el proyecto ADTOF original. El [repositorio ADTOF original](https://github.com/MZehren/ADTOF) declara CC BY-NC-SA 4.0. Por lo tanto, no debe asumirse permiso comercial para código ni modelo ADTOF-pytorch sin revisión legal y confirmación de sus titulares. Esto no modifica la licencia MIT de Kinetra Resonance.
