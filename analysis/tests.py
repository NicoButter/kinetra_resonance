from unittest.mock import patch
import numpy as np
from django.test import SimpleTestCase
from .services import DrumsAnalyzer

class DrumsAnalyzerTests(SimpleTestCase):
    def test_intensities_are_normalized(self):
        class Loader:
            def __call__(self): return np.ones(44100, dtype=np.float32)
        class Rhythm:
            def __init__(self, **kwargs): pass
            def __call__(self, signal): return 120.0, np.array([.1, .5]), 3.2, None, None
        with patch('essentia.standard.MonoLoader', return_value=Loader()), patch('essentia.standard.RhythmExtractor2013', Rhythm): payload = DrumsAnalyzer().analyze('unused.wav')
        self.assertEqual(payload['durationMs'], 1000); self.assertTrue(all(0 <= event['intensity'] <= 1 for event in payload['events']))
