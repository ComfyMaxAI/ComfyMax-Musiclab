"""Phase 1.1 tempo, vocabulary, extension and sequence regressions."""
import copy
import json
import unittest
import numpy as np
from comfymax_audio_chunker.music.model import Settings, chord, label
from comfymax_audio_chunker.music.rhythm import estimate_tempo
from comfymax_audio_chunker.music.chords import recognize, QUALITIES
from comfymax_audio_chunker.music.smoothing import smooth, regions


class TempoTests(unittest.TestCase):
    def tempo(self, times):
        return estimate_tempo(117.45, times, Settings())

    def test_perfect_grid(self):
        t = self.tempo(np.arange(20)*.5)
        self.assertEqual(t['grid_bpm'], 120)
        self.assertEqual(t['effective_bpm'], 120)
        self.assertEqual(t['librosa_bpm'], 117.45)
        self.assertEqual(t['interval_mad'], 0)

    def test_jitter(self):
        t = self.tempo([0, .49, 1.01, 1.50, 2.01, 2.5, 3, 3.51, 4.01])
        self.assertAlmostEqual(t['effective_bpm'], 120, delta=1)
        self.assertEqual(t['source'], 'span')

    def test_outlier(self):
        t = self.tempo(np.cumsum([0, .5, .5, .5, 1.5, .5, .5, .5, .5]))
        self.assertEqual(t['effective_bpm'], 120)

    def test_fallbacks(self):
        for times, reason in [([], 'too_few_intervals'), ([0,.5], 'too_few_intervals'),
                              ([0,.5,1,1.5], 'too_few_intervals'),
                              ([0,.5,1,2,3,3.5,4.5], 'unstable_intervals'),
                              (np.arange(10)*.01, 'implausible_grid_bpm'),
                              ([0,.5,.5,1,1.5], 'invalid_beat_grid'),
                              ([0,.5,float('nan'),1.5,2], 'invalid_beat_grid')]:
            with self.subTest(reason=reason, times=times):
                t = self.tempo(times)
                self.assertEqual(t['effective_bpm'], 117.45)
                self.assertEqual(t['fallback_reason'], reason)
                json.dumps(t, allow_nan=False)

    def test_settings_validation(self):
        for kwargs in [dict(extension_min_relative_energy=-1), dict(tempo_min_intervals=1),
                       dict(tempo_min_bpm=500), dict(tempo_max_relative_mad=float('nan'))]:
            with self.assertRaises(ValueError): Settings(**kwargs).validate()


class SeventhTests(unittest.TestCase):
    def vector(self, root, quality, extension=None):
        v = np.zeros(12)
        offsets, parent = QUALITIES[quality]
        v[[(root + i) % 12 for i in offsets]] = 1
        if extension is not None:
            v[(root + offsets[-1]) % 12] = extension
        return v

    def test_all_roots_sevenths_and_weak_extensions(self):
        for quality in ('7', 'min7', 'maj7'):
            for root in range(12):
                for strength, expected in [(1,quality), (.02,QUALITIES[quality][1])]:
                    with self.subTest(root=root, quality=quality, strength=strength):
                        result = recognize(self.vector(root,quality,strength), .1, .001, Settings())
                        self.assertEqual(result[0], chord(root,expected))
                        if strength == 1:
                            self.assertTrue(result[3][0]['extension_evidence']['accepted'])

    def test_labels(self):
        self.assertEqual([label(chord(0,q)) for q in ('7','min7','maj7')], ['C7','Cm7','Cmaj7'])

    def sequence(self, vectors):
        raw, scores = [], []
        for i,v in enumerate(vectors):
            value, score, margin, candidates, all_scores = recognize(v,.1,.001,Settings())
            raw.append(dict(id=f'p{i}', start_frame=i*100, end_frame=(i+1)*100, raw=value))
            scores.append(all_scores)
        before = copy.deepcopy(raw)
        seq, selected = smooth(raw,np.array(scores),.12)
        self.assertEqual(raw,before)
        return seq, regions(raw,seq,selected,100)

    def test_controlled_progression_with_passing_tones(self):
        vectors = []
        for root,quality,passing in [(0,'maj',11),(9,'min',7),(5,'maj',4),(7,'maj',5)]:
            for i in range(16):
                v = self.vector(root,quality)
                v[passing] = .02 if i%3 else .08
                vectors.append(v)
        seq, merged = self.sequence(vectors)
        self.assertEqual([label(r) for r in merged], ['C','Am','F','G'])

    def test_extension_smoothing(self):
        c = self.vector(0,'maj'); strong = self.vector(0,'maj7')
        weak = self.vector(0,'maj7',.1)
        for middle, expected in [([weak], ['C']*5),
                                 ([strong]*3, ['C','C','Cmaj7','Cmaj7','Cmaj7','C','C'])]:
            seq,_ = self.sequence([c,c]+middle+[c,c])
            self.assertEqual([label(v) for v in seq], expected)
        # A genuine one-beat dominant extension is not removed by two switches.
        seq,_ = self.sequence([c,c,self.vector(0,'7'),c,c])
        self.assertEqual([label(v) for v in seq], ['C','C','C7','C','C'])

    def test_key_does_not_restrict_candidates(self):
        for root,quality in [(4,'7'),(9,'7'),(10,'maj'),(5,'min')]:
            self.assertEqual(recognize(self.vector(root,quality),.1,.001,Settings())[0],chord(root,quality))

    def test_configurable_gate(self):
        v = self.vector(0,'maj7',.8)
        self.assertEqual(recognize(v,.1,.001,Settings())[0],chord(0,'maj7'))
        self.assertEqual(recognize(v,.1,.001,Settings(extension_min_relative_energy=.9))[0],chord(0,'maj'))
