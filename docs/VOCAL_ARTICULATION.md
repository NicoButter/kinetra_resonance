# Vocal articulation preview

Kinetra Resonance uses an articulatory visualization for vocal review. It is a visual phonetic approximation and lip-reading support tool, not a claim of perfect phoneme recognition. Rhubarb remains the source of automatic mouth cues and human review remains authoritative.

```text
Rhubarb mouth cue (A–H/X)
        ↓
effectiveShape (reviewedShape ?? automaticShape)
        ↓
ArticulationMapper
        ↓
normalized MouthPose + bounded vocal expression
        ↓
visual-only coarticulation
        ↓
MouthRenderer
        ↓
SvgAnimeMouthRenderer anatomical rig
```

`audio.currentTime` remains the only clock. Coarticulation does not move, add, or delay Rhubarb cues. Near the start and end of an existing cue it adds small, bounded influences from the neighboring base poses. During continuous playback those computed poses are applied directly, avoiding an animation queue or cumulative delay. Anime.js handles discrete preview/design morphs; it does not know phonetic labels.

## Canonical mapping

The single mapping table lives in `static/js/mouth-shapes.js`. It follows Rhubarb 1.14's official mouth-shape guidance and Preston Blair export aliases:

| Rhubarb | Kinetra alias | Visual articulation |
| --- | --- | --- |
| A | MBP | closed lips with visible pressure |
| B | CONS | small opening, clenched/visible teeth, general consonant family |
| C | OPEN-MID | medium open position |
| D | OPEN-WIDE | large jaw and lip opening |
| E | ROUND | medium rounding, less projection than F |
| F | UW | strongly rounded and puckered lips for UW/OW/W |
| G | FV | upper incisors contact the raised lower lip |
| H | L | partially open mouth with raised, forward tongue behind upper teeth |
| X | REST | relaxed closed/rest position without lip pressure |

The alphabetical Rhubarb code is always retained. In particular, Rhubarb `F` is not the `/f/` sound: its alias is `UW`. Rhubarb `G` is the optional F/V labiodental position.

Source: [Rhubarb Lip Sync 1.14 mouth-shape documentation](https://github.com/DanielSWolf/rhubarb-lip-sync/blob/v1.14.0/README.adoc#mouth-shapes).

## MouthPose contract

`static/js/mouth-pose.js` defines the only normalized anatomical contract. Every finite value is clamped to `0..1`; NaN and infinity become zero. Base poses are frozen and are never modified directly.

- `jawOpen`, `lipOpen`, `lipWidth`
- `lipRound`, `lipPucker`, `lipSpread`
- `lipClosure`, `lipPressure`
- `upperTeethVisible`, `lowerTeethVisible`
- `lowerLipRaise`, `labiodentalContact`
- `tongueVisible`, `tongueRaise`, `tongueForward`

Articulation is dominant. Intensity changes jaw/lip opening only within bounded limits, presence attenuates that secondary change, and valid pitch adds at most a few percentage points of spread/jaw tension. Expression never changes F/V contact, teeth visibility, tongue identity, or the chosen articulation.

## SVG rig

The renderer receives only MouthPose values and converts them into responsive geometry for `mouth-root`, upper/lower lips, mouth interior, upper/lower teeth, tongue, and jaw. F/V physically raises the independent lower lip to the upper-incisor edge. L raises and advances an independent tongue layer. Jaw geometry moves separately from the mouth interior. These distinctions remain available without color and when reduced motion disables morph durations.

The Review Editor displays `Rhubarb code · articulation alias`, neighboring context, and a full debug pose readout. Its developer-only Articulation Lab provides temporary sliders and Copy Pose JSON. Slider values never enter review actions or the database.

## Data contract

Processed/reviewed vocal JSON continues to store the original/reviewed viseme plus compact dynamic expression values. It does not repeat the 15-value static MouthPose on every cue. Teleo Experience is unchanged and receives no SVG paths or web-renderer data. A future Teleo renderer can implement the same compact `viseme + intensity + pitchNormalized` contract with its own platform-specific mapper.

Fine-grained phonemes, lyrics, forced alignment, WhisperX, Phonemizer, and eSpeak are explicitly outside this increment.

## Checks

`node static/js/test-articulation.js` verifies the mapping, normalized ranges, immutable bases, A/X distinction, FV contact, L tongue, UW pucker, expression limits, null/subtle pitch handling, blending, coarticulation boundaries, and the artificial sequence A→G→C→F→H→X. `tools/articulation_visual_check.html` is the mandatory muted visual fixture for MBP/UW/FV/L and includes a rapid transition stress check.
