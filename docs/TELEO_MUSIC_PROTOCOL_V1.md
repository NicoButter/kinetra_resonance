# Teleo Music Protocol v1

Teleo Music Protocol is the host-agnostic JSON contract produced by Kinetra Resonance. Version 1 consists of a catalog and one canonical experience per track. Kinetra compiles these files; an operator chooses where to host them; Teleo or another compatible runtime consumes them.

```text
Audio → Kinetra Resonance → Teleo Music Protocol → self-hosted server → Teleo
```

The public contract contains no audio, stems, analyzer diagnostics, review actions, model paths, credentials or absolute filesystem paths.

Version 1 does not require a separate publication manifest. Kinetra keeps generator provenance and checksums in its local `TeleoPublication` records; Teleo consumes only `catalog.json` and `experience.json`.

## Compatibility and versions

- `format` identifies the document family. A client must reject an unknown value.
- `version` is the protocol schema version. A v1 client must reject an unsupported schema version.
- `experienceVersion` identifies a concrete revision of one track experience. It starts at 1 and increases only when the canonical export content changes.
- A v1 client must ignore unknown optional fields. Required fields retain their documented meaning for the lifetime of v1.

Re-exporting the same canonical artifact, source hash, quality and lyrics policy is deterministic and keeps the same `experienceVersion`. A completed human review, corrected viseme or another relevant canonical change produces the next version. Opening a page and repeating an unchanged export do not increment it.

## Catalog

`catalog.json` is required at the bundle root:

```json
{
  "format": "teleo-music-catalog",
  "version": 1,
  "tracks": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "Example",
      "artist": "Example Artist",
      "durationMs": 214000,
      "experienceVersion": 2,
      "quality": "HUMAN_REVIEWED",
      "experienceUrl": "tracks/550e8400-e29b-41d4-a716-446655440000/experience.json"
    }
  ]
}
```

Required track fields are `id`, `title`, `artist`, `durationMs`, `experienceVersion`, `quality` and `experienceUrl`. `experienceUrl` is always relative, uses `/` separators and has the stable form `tracks/<id>/experience.json`. The same bundle can therefore move between compatible hosts without regeneration.

There is one active catalog entry per track ID. Kinetra selects its highest exported `experienceVersion` and orders the catalog deterministically by case-insensitive artist, title and ID. Catalog ID, duration and experience version must match the referenced experience.

## Experience

The required publication fields are:

```json
{
  "format": "teleo-music",
  "version": 1,
  "experienceVersion": 2,
  "quality": "HUMAN_REVIEWED",
  "sourceHash": {
    "algorithm": "sha256",
    "value": "64-lowercase-hex-characters"
  },
  "track": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Example",
    "artist": "Example Artist",
    "durationMs": 214000,
    "bpm": 118.4
  },
  "drums": {"events": []},
  "bass": {"notes": []},
  "guitar": {"notes": []},
  "piano": {"notes": []},
  "vocals": {"frames": [], "visemes": []},
  "other": {"frames": []},
  "timeline": [],
  "lyrics": [],
  "sections": [],
  "haptics": []
}
```

`format`, `version`, `experienceVersion`, `quality`, `sourceHash`, `track.id` and `track.durationMs` are required in a publication bundle. Channel objects and arrays are retained from the canonical `TeleoExperienceBuilder` output. Empty arrays are valid. `analysis`, `channelsQuality`, `track.title`, `track.artist` and `track.bpm` are protocol metadata and may be ignored by a renderer that does not need them.

`sourceHash` is the SHA-256 of the exact original audio bytes processed by the selected `ProcessingJob`. It lets a runtime compare local media with the experience. Kinetra calculates it once and persists it on the Track. The experience file checksum is kept in Kinetra's publication record rather than embedded in the file, avoiding a self-referential hash.

## Time and normalization

All temporal values are integer milliseconds. Arrays are sorted ascending by `timeMs` or `startMs`. Point events satisfy `0 <= timeMs <= durationMs`; intervals satisfy `0 <= startMs < endMs <= durationMs`. An interval is active when `startMs <= t < endMs`.

Normalized values such as `intensity`, `confidence`, `presence` and `pitchNormalized` are within `0..1`. The publication validator reports invalid canonical data; it does not silently clamp it.

## Channels

- `drums.events` contains effective events. `type` is the resolved reviewed value when present, otherwise the automatic value. Core families are `kick`, `snare`, `hi_hat`, `tom` and `cymbal`; reviewed data may retain the existing refinements `crash`, `splash` and `ride`. `unassigned` or `unknown` may appear in non-master automatic material and must not be interpreted as a certified classification.
- `bass.notes`, `guitar.notes` and `piano.notes` retain compact start/end, pitch, MIDI, note, intensity and confidence data when available.
- `vocals.frames` may carry compact presence and expression data useful to a renderer.
- `vocals.visemes` uses `startMs`, `endMs`, `shape`, and optionally `intensity` and `pitchNormalized`. `shape` is one of `A B C D E F G H X`. These are Rhubarb mouth-shape codes; `F` is not the phoneme `/f/`. Review is resolved before export, so editor fields such as `automaticShape` and `reviewedShape` are not part of the public cue.
- `other.frames` retains compact frequency-band/energy expression data.
- `timeline` is a cross-channel index of canonical events.
- `haptics` contains already compiled haptic events when the canonical builder provides them. A client does not reconstruct haptics from drums.

## Quality

- `AUTOMATIC`: generated from validated processed artifacts without a completed human review.
- `HUMAN_REVIEWED`: generated from a completed review artifact for the same ProcessingJob.
- `TELEO_MASTER`: reserved for a separately approved master workflow. Successful export alone never assigns it.

Quality describes workflow provenance, not medical, phonetic or perceptual accuracy. A partial review is not promoted to `HUMAN_REVIEWED` by the publisher.

## Lyrics and media

Lyrics are empty by default. They are included only through the explicit `PUBLISH_LYRICS` setting or the explicit UI/CLI option, and only if the canonical experience already contains them. The operator remains responsible for permission to distribute them.

Audio and cover art are outside Teleo Music Protocol v1 publication bundles. The runtime selects local audio and can use `sourceHash` plus `durationMs` to validate it.

## Forward compatibility

A producer may add optional fields without changing `version`. Consumers must ignore optional fields they do not recognize. A breaking change to required fields, units or semantics requires a new schema version. Unknown `format` or unsupported `version` values must be rejected.
