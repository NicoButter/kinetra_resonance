# Vocal lip-sync / Rhubarb

Kinetra invokes Rhubarb only through `VocalLipSyncService` and stores its output as an automatic proposal. Traditional vocal frames remain available when the executable is absent, returns an error, times out, or produces invalid/empty JSON; with `LIPSYNC_REQUIRED=true` (the default), that failure is also terminal for the ProcessingJob and is shown as the lip-sync stage error.

`RhubarbHealthCheck` verifies the integration before analysis. Resolution is deterministic: a non-empty `RHUBARB_BINARY` takes precedence, followed by the documented project-local `tools/rhubarb/Rhubarb-Lip-Sync-1.14.0-Linux/rhubarb`, then `rhubarb` on `PATH`. Every candidate must be an executable file and must successfully report its version. Its public status contains only the executable basename, availability, version, and executability; an absolute resolved path is available only to development/admin callers that explicitly request it.

The integration was checked against Rhubarb's official repository and CLI documentation: JSON output uses `mouthCues`, `--recognizer` supports `pocketSphinx` and `phonetic`, `--dialogFile` is optional, and `--extendedShapes GHX` enables G/H/X. The executable is invoked with an argument list and `shell=False`; temporary output is isolated per invocation. Configure it with `RHUBARB_BINARY`, `RHUBARB_ENABLED`, `RHUBARB_RECOGNIZER`, `RHUBARB_EXTENDED_SHAPES`, and `RHUBARB_TIMEOUT_SECONDS`. `VOCAL_LANGUAGE=es` selects `phonetic` when recognizer is `auto`; English selects `pocketSphinx`.

Rhubarb's mouth cues are visual visemes (A–H, X), not a linguistic truth. Singing, held vowels, vibrato, reverb, and imperfect vocal separation can degrade them. Human review is therefore the authoritative output. No lyrics are downloaded: a future legitimate dialog file can be passed to the backend interface.

Artifacts retain the compatible `vocals.json` manifest and add `raw/vocals/{frames,mouth_cues}.json`, `processed/vocals/{frames,visemes}.json`, and `reviewed/vN/vocals/visemes.json`. Teleo receives only compact viseme ranges, not local paths or backend diagnostics. The global timeline contains one event per viseme change, which allows a dispatcher without duplicating continuous voice frames.

## Review Editor mouth preview

The Kinetra Resonance Review Editor displays processed/reviewed visemes with a local SVG mouth rig. `MouthRenderer` is the UI-facing abstraction and `SvgAnimeMouthRenderer` is the current implementation. It uses the vendored Anime.js 4.5.0 bundle (`static/vendor/animejs/`) to morph SVG layers, with no runtime network dependency and no frontend build step.

`audio.currentTime` remains the only musical clock. On every animation frame the editor resolves `startMs <= currentTimeMs < endMs` and uses `reviewedShape ?? automaticShape`; Anime.js only smooths a changed visual pose. A seek jump greater than 250 ms snaps directly to the current pose, pauses retain it, and ending playback resets to `X`. Interrupted morphs are cancelled rather than queued. Intensity, pitch and presence add restrained expression but never select a mouth shape.

The renderer is an audit tool, not Teleo artwork. The reviewer can drag a cue vertically to override its shape, Shift-drag horizontally to correct timing, and delete false detections. Those changes become non-destructive REVIEWED actions; Rhubarb's automatic proposal remains intact.

If Rhubarb was run outside Kinetra, import its standard JSON into the same job without re-separating stems: `python manage.py import_vocal_visemes <job_uuid> /absolute/path/rhubarb.json`. It rejects a job that already has review actions so an audit trail is never silently replaced.

Rhubarb is an external dependency under the upstream MIT licence; install a compatible Linux binary from the upstream release and retain the licence notice in a distribution. Official references: [Rhubarb repository](https://github.com/DanielSWolf/rhubarb-lip-sync), [CLI/readme](https://github.com/DanielSWolf/rhubarb-lip-sync/blob/master/README.adoc), [licence](https://github.com/DanielSWolf/rhubarb-lip-sync/blob/master/LICENSE.md).
