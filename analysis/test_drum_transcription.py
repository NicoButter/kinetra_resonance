from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from analysis.drum_transcription import (
    ADTOFDrumTranscriptionBackend,
    ADTOFMidiAdapter,
    AutomaticDrumTranscriptionService,
    DrumEventFusionService,
    DrumTranscriptionBackendUnavailable,
    DrumTranscriptionResult,
)


def fake_midi(notes, *, is_drum=True):
    return SimpleNamespace(instruments=[SimpleNamespace(is_drum=is_drum, notes=notes)])


def note(pitch, start, end, velocity=100):
    return SimpleNamespace(pitch=pitch, start=start, end=end, velocity=velocity)


class ADTOFMidiAdapterTests(SimpleTestCase):
    def test_maps_adtof_five_classes_and_unknown_midi(self):
        midi = fake_midi([
            note(35, .1, .2), note(38, .2, .3), note(42, .3, .4),
            note(47, .4, .5), note(49, .5, .6), note(60, .6, .7),
        ])
        events = ADTOFMidiAdapter(midi_loader=lambda _: midi).convert('output.mid')
        self.assertEqual(
            [event['automaticType'] for event in events],
            ['kick', 'snare', 'hi_hat', 'tom', 'cymbal', 'unknown'],
        )

    def test_timestamp_and_velocity_are_not_confidence_or_intensity(self):
        midi = fake_midi([note(38, 1.2346, 1.3346, velocity=73)])
        event = ADTOFMidiAdapter(midi_loader=lambda _: midi).convert('output.mid')[0]
        self.assertEqual((event['timeMs'], event['durationMs']), (1235, 100))
        self.assertEqual(event['automatic']['midiVelocity'], 73)
        self.assertIsNone(event['automatic']['confidence'])
        self.assertNotIn('intensity', event)

    def test_rejects_midi_without_drum_track(self):
        midi = fake_midi([note(35, 0, .1)], is_drum=False)
        with self.assertRaisesRegex(Exception, 'no drum track'):
            ADTOFMidiAdapter(midi_loader=lambda _: midi).convert('output.mid')


class ADTOFBackendTests(SimpleTestCase):
    def test_backend_calls_verified_programmatic_api_and_adapts_midi(self):
        calls = []

        def transcribe(audio, midi_out, *, device):
            calls.append((audio, midi_out, device))
            Path(midi_out).write_bytes(b'MThd')
            return Path(midi_out)

        adapter = Mock()
        adapter.convert.return_value = [{
            'timeMs': 100,
            'durationMs': 100,
            'automaticType': 'kick',
            'automatic': {'backend': 'adtof', 'type': 'kick', 'confidence': None},
        }]
        backend = ADTOFDrumTranscriptionBackend(
            midi_adapter=adapter,
            transcribe_function=transcribe,
            version_resolver=lambda: '0.1.0',
        )
        result = backend.transcribe('drums.wav', device='cpu')
        self.assertEqual(calls[0][0::2], ('drums.wav', 'cpu'))
        self.assertEqual((result.backend, result.backend_version, result.device), ('adtof', '0.1.0', 'cpu'))
        self.assertEqual(result.events[0]['automaticType'], 'kick')


class AutomaticDrumTranscriptionServiceTests(SimpleTestCase):
    def test_backend_unavailable_returns_manual_fallback(self):
        backend = Mock()
        backend.transcribe.side_effect = DrumTranscriptionBackendUnavailable('ADTOF backend unavailable')
        service = AutomaticDrumTranscriptionService(backend=backend, backend_name='adtof', device='cpu', enabled=True)
        result = service.transcribe('drums.wav')
        self.assertFalse(result.available)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.events, [])
        self.assertIn('Automatic drum transcription unavailable. Human classification required.', result.warnings)

    def test_cuda_failure_retries_cpu(self):
        devices = []

        def transcribe(_, *, device):
            devices.append(device)
            if device == 'cuda':
                raise RuntimeError('CUDA out of memory')
            return DrumTranscriptionResult(events=[{
                'timeMs': 100,
                'automaticType': 'kick',
                'automatic': {'backend': 'adtof', 'type': 'kick', 'confidence': None},
            }], backend='adtof', backend_version='0.1.0', device=device)

        backend = Mock()
        backend.transcribe.side_effect = transcribe
        service = AutomaticDrumTranscriptionService(
            backend=backend, backend_name='adtof', device='auto', enabled=True,
            cuda_available=lambda: True,
        )
        result = service.transcribe('drums.wav')
        self.assertEqual(devices, ['cuda', 'cpu'])
        self.assertEqual(result.device, 'cpu')
        self.assertTrue(result.fallback_used)
        self.assertTrue(result.available)

    def test_explicit_unavailable_cuda_uses_cpu(self):
        backend = Mock()
        backend.transcribe.return_value = DrumTranscriptionResult(backend='adtof', device='cpu')
        result = AutomaticDrumTranscriptionService(
            backend=backend, backend_name='adtof', device='cuda', enabled=True,
            cuda_available=lambda: False,
        ).transcribe('drums.wav')
        backend.transcribe.assert_called_once_with('drums.wav', device='cpu')
        self.assertIn('CUDA unavailable; using CPU.', result.warnings)


@override_settings(DRUM_EVENT_MATCH_TOLERANCE_MS=50)
class DrumEventFusionServiceTests(SimpleTestCase):
    @staticmethod
    def automatic(time_ms, event_type):
        return {
            'timeMs': time_ms,
            'durationMs': 100,
            'automaticType': event_type,
            'automatic': {'backend': 'adtof', 'type': event_type, 'confidence': None},
            'reviewedType': None,
            'effectiveType': event_type,
        }

    def test_matching_tolerance_adtof_only_onset_only_and_deduplication(self):
        automatic = [self.automatic(100, 'kick'), self.automatic(500, 'snare')]
        onsets = [
            {'timeMs': 149, 'durationMs': 80, 'intensity': .8},
            {'timeMs': 700, 'durationMs': 80, 'intensity': .4},
        ]
        events = DrumEventFusionService().fuse(automatic, onsets)
        self.assertEqual(len(events), 3)
        matched = next(event for event in events if event['timeMs'] == 100)
        self.assertEqual((matched['source'], matched['intensity'], matched['kinetraOnset']['deltaMs']), ('adtof+kinetra-onset', .8, 49))
        adtof_only = next(event for event in events if event['timeMs'] == 500)
        self.assertEqual(adtof_only['source'], 'adtof')
        onset_only = next(event for event in events if event['timeMs'] == 700)
        self.assertEqual(onset_only['automaticType'], 'unassigned')
        self.assertEqual(onset_only['automatic']['type'], None)
        self.assertEqual(onset_only['source'], 'kinetra-onset')
        self.assertEqual(len({event['id'] for event in events}), 3)

    def test_outside_tolerance_does_not_match(self):
        events = DrumEventFusionService(matching_tolerance_ms=50).fuse(
            [self.automatic(100, 'kick')],
            [{'timeMs': 151, 'durationMs': 80, 'intensity': .5}],
        )
        self.assertEqual(len(events), 2)

