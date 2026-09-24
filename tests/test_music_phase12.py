import copy
import unittest
from dataclasses import asdict
import numpy as np
from comfymax_audio_chunker.music.chords import QUALITIES, recognize
from comfymax_audio_chunker.music.model import Settings, chord, label
from comfymax_audio_chunker.music.temporal import stabilize, apply_structure
from comfymax_audio_chunker.music.rhythm import estimate_tempo, master_beats
from comfymax_audio_chunker.music.structure import build_bars
from comfymax_audio_chunker.music.evaluation import evaluate


def vector(root,quality,strength=1):
    result=np.zeros(12)
    offsets,parent=QUALITIES[quality]
    result[[(root+n)%12 for n in offsets]]=1
    if parent: result[(root+offsets[-1])%12]=strength
    return result


def observations(vectors,settings=None):
    settings=settings or Settings(); rows=[]; scores=[]
    for i,v in enumerate(vectors):
        value,score,margin,candidates,all_scores=recognize(v,.1,.001,settings)
        rows.append(dict(id=f'p{i}',beat_id=f'b{i+1}',beat_index=i+1,interval_kind='beat',
                         start_frame=i*100,end_frame=(i+1)*100,start_seconds=i*.5,end_seconds=(i+1)*.5,
                         raw=value,best_label=label(value),score=score,margin=margin,candidates=candidates,
                         chroma=v.tolist(),harmonic_rms=.1))
        scores.append(all_scores)
    return rows,np.array(scores)


class TemporalTests(unittest.TestCase):
    def result(self,vectors,settings=None):
        settings=settings or Settings(); raw,scores=observations(vectors,settings); before=copy.deepcopy(raw)
        result=stabilize(raw,scores,settings,[])
        self.assertEqual(raw,before)
        return [label(v) for v in result[1]],result

    def test_isolated_major_and_minor_seventh(self):
        for root,parent,seventh in [(5,'maj','maj7'),(9,'min','min7')]:
            v=vector(root,parent); ext=vector(root,seventh,.8)
            names,result=self.result([v,ext,v,v])
            self.assertEqual(names,[label(chord(root,parent))]*4)
            self.assertTrue(result[3][1])
            self.assertFalse(any(e['accepted'] for e in result[3][1]))

    def test_sustained_sevenths_all_roots(self):
        for root in range(12):
            for quality in ('7','maj7','min7'):
                with self.subTest(root=root,quality=quality):
                    names,_=self.result([vector(root,quality)]*4)
                    self.assertEqual(names,[label(chord(root,quality))]*4)

    def test_single_resolving_dominant(self):
        names,result=self.result([vector(7,'maj'),vector(7,'7'),vector(0,'maj')])
        self.assertEqual(names,['G','G7','C'])
        self.assertIn('strong_resolving_dominant',result[5][1])
        names,_=self.result([vector(7,'maj'),vector(7,'7'),vector(7,'maj')])
        self.assertEqual(names,['G']*3)

    def test_fraction_config_controls_two_beat_extension(self):
        vs=[vector(0,'maj'),vector(0,'maj7'),vector(0,'maj7'),vector(0,'maj')]
        self.assertEqual(self.result(vs)[0],['C']*4)
        self.assertEqual(self.result(vs,Settings(extension_temporal_min_fraction=.5))[0],['C','Cmaj7','Cmaj7','C'])

    def test_unknown_bridge_and_conflict(self):
        raw,scores=observations([vector(0,'maj')]*3)
        raw[1]['raw']=chord(); scores[1,:]=0; scores[1,:24]=.1; scores[1,0]=.66; scores[1,7]=.70
        result=stabilize(raw,scores,Settings(),[])
        self.assertEqual([label(v) for v in result[1]],['C']*3)
        self.assertEqual(result[4][1]['resolved_to'],'C')
        scores[1,7]=.95
        result=stabilize(raw,scores,Settings(),[])
        self.assertEqual(label(result[1][1]),'unknown')  # Strong conflict blocks the C bridge.
        scores[1,0]=scores[1,7]=.70
        raw[2]['raw']=chord(7,'maj'); scores[2,:24]=0; scores[2,7]=1
        result=stabilize(raw,scores,Settings(),[])
        self.assertEqual(label(result[1][1]),'unknown')

    def test_no_bridge_silence_or_long_gap(self):
        raw,scores=observations([vector(0,'maj')]*3)
        raw[1]['raw']=chord(quality='N')
        self.assertEqual(label(stabilize(raw,scores,Settings(),[])[1][1]),'N')
        raw[1]['raw']=chord(); raw[1]['end_frame']=1000
        self.assertEqual(label(stabilize(raw,scores,Settings(),[])[1][1]),'unknown')

    def test_hysteresis_preserves_strong_one_beat_change(self):
        names,_=self.result([vector(0,'maj'),vector(7,'maj'),vector(0,'maj')])
        self.assertEqual(names,['C','G','C'])


class StructureTests(unittest.TestCase):
    def bars(self,count=8,anchor=0,numerator=4,denominator=4,total=None):
        beats=master_beats(np.arange(count)*.5,200,total or count*100)
        return build_bars(beats,total or count*100,dict(numerator=numerator,denominator=denominator,source='manual'),
                          dict(beat_id=beats[anchor]['id'] if anchor is not None else None,source='manual'))

    def test_no_invented_downbeat(self):
        bars,reason=self.bars(anchor=None)
        self.assertEqual(bars,[]); self.assertEqual(reason,'first_downbeat_unknown')

    def test_pickup_and_incomplete_bar(self):
        bars,_=self.bars(count=8,anchor=2)
        self.assertTrue(bars[0]['pickup']); self.assertEqual(bars[0]['beat_ids'],['b1','b2'])
        self.assertEqual(bars[1]['beat_ids'],['b3','b4','b5','b6'])
        self.assertTrue(bars[-1]['incomplete']); self.assertEqual(bars[-1]['end_frame'],800)

    def test_three_four_six_eight_and_unsupported_grid(self):
        for n,d in [(3,4),(6,8)]:
            bars,_=self.bars(count=6,numerator=n,denominator=d)
            self.assertEqual([len(b['beat_ids']) for b in bars],[3,3])
            self.assertEqual(bars[0]['beat_unit'],'1/4')
        bars,reason=self.bars(numerator=3,denominator=8)
        self.assertEqual(bars,[]); self.assertEqual(reason,'bar_not_aligned_to_quarter_beat_grid')

    def test_bar_context_can_resolve_edge_unknown_without_forcing_changes(self):
        raw,scores=observations([vector(0,'maj')]*4)
        raw[0]['raw']=chord(); scores[0,:]=0; scores[0,0]=.70; scores[0,21]=.71
        # A neutral initial unknown has no agreeing left neighbor.
        scores[0,21]=.85
        without=stabilize(raw,scores,Settings(),[])
        self.assertEqual(label(without[1][0]),'unknown')
        # Enough support for C but a narrow top-candidate tie; use a stronger
        # unknown emission by setting transition penalty to zero for this test.
        scores[0,21]=.71
        bars,_=self.bars(count=4)
        settings=Settings(transition_penalty=0)
        self.assertEqual(label(stabilize(raw,scores,settings,[])[1][0]),'unknown')
        result=stabilize(raw,scores,settings,bars)
        self.assertEqual(label(result[1][0]),'C')
        self.assertEqual(result[4][0]['reason'],'isolated_gap_supported_by_bar_majority')

    def test_bar_weak_Am_and_genuine_internal_change(self):
        raw,scores=observations([vector(0,'maj')]*4)
        raw[2]['raw']=chord(9,'min'); scores[2,:24]=0; scores[2,0]=.69; scores[2,21]=.70
        bars,_=self.bars(count=4)
        result=stabilize(raw,scores,Settings(),bars)
        self.assertEqual([label(v) for v in result[1]],['C']*4)
        raw,scores=observations([vector(0,'maj')]*2+[vector(7,'maj')]*2)
        bars,_=self.bars(count=4)
        result=stabilize(raw,scores,Settings(),bars)
        self.assertEqual([label(v) for v in result[1]],['C','C','G','G'])
        self.assertEqual(bars[0]['harmonic_summary']['possible_internal_changes'],[200])


class SpanAndMetricsTests(unittest.TestCase):
    def test_quantized_grid_span_no_rounding(self):
        exact=np.arange(65)*.5
        quantized=np.round(exact/(512/22050))*(512/22050)
        tempo=estimate_tempo(117.45,quantized,Settings())
        self.assertAlmostEqual(tempo['span_bpm'],120,delta=.4)
        self.assertEqual(tempo['effective_bpm'],tempo['span_bpm'])
        self.assertEqual(tempo['source'],'span')
        off=estimate_tempo(123.4,np.arange(65)*(60/123.4),Settings())
        self.assertAlmostEqual(off['effective_bpm'],123.4)

    def test_span_falls_back_if_outlier_disagrees(self):
        tempo=estimate_tempo(117.45,np.cumsum([0]+[.5]*4+[1.5]+[.5]*4),Settings())
        self.assertEqual(tempo['effective_bpm'],120)
        self.assertEqual(tempo['source'],'grid')

    def test_metrics_weight_by_duration_and_match_boundaries_once(self):
        truth=[dict(chord(0,'maj'),start_frame=0,end_frame=800),dict(chord(7,'maj'),start_frame=800,end_frame=1000)]
        self.assertEqual(evaluate(truth,truth,100)['exact_quality_accuracy'],1)
        guess=[dict(chord(0,'maj7'),start_frame=0,end_frame=700),dict(chord(),start_frame=700,end_frame=800),truth[1]]
        metrics=evaluate(truth,guess,100)
        self.assertAlmostEqual(metrics['root_accuracy'],.9)
        self.assertAlmostEqual(metrics['triad_family_accuracy'],.9)
        self.assertAlmostEqual(metrics['exact_quality_accuracy'],.2)
        self.assertEqual(metrics['unknown_percentage'],10)
        self.assertEqual(metrics['false_seventh_regions'],1)
        self.assertEqual(metrics['false_chord_changes'],2)
