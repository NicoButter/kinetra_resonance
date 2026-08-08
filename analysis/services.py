import json
from pathlib import Path

import numpy as np
from django.conf import settings


class DrumsAnalyzer:
    def analyze(self, audio_path):
        import essentia.standard as es

        signal = es.MonoLoader(filename=str(audio_path))()
        sample_rate = 44100
        duration_ms = int(round(len(signal) / sample_rate * 1000))
        bpm, beats, confidence, _, _ = es.RhythmExtractor2013(method='multifeature')(signal)
        beats = np.asarray(beats)
        if len(beats):
            indices = np.clip((beats * sample_rate).astype(int), 0, max(len(signal) - 1, 0))
            window = max(1, int(sample_rate * 0.05))
            strengths = np.array([np.mean(np.abs(signal[index:min(index + window, len(signal))])) for index in indices])
            maximum = float(strengths.max()) if strengths.size else 0.0
            intensities = strengths / maximum if maximum > 0 else np.zeros_like(strengths)
        else:
            intensities = np.array([])
        return {
            'format': 'kinetra-resonance', 'version': 1, 'stem': 'drums',
            'durationMs': duration_ms, 'bpm': round(float(bpm), 2), 'confidence': round(float(confidence), 2),
            'events': [{'timeMs': int(round(float(beat) * 1000)), 'type': 'beat', 'intensity': round(float(np.clip(intensity, 0, 1)), 4)} for beat, intensity in zip(beats, intensities)],
        }

    def write(self, track, stem):
        artifact_dir = Path(settings.MEDIA_ROOT) / 'tracks' / str(track.id) / 'analysis'
        artifact_dir.mkdir(parents=True, exist_ok=True)
        payload = self.analyze(stem.file.path)
        path = artifact_dir / 'drums.json'
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return payload, path
