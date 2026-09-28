from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from qfbench2_track_forecasting.grid import GridSpec
from text_events import load_text, retrieve, adjust_samples


class TextEventTests(unittest.TestCase):
    def fixture(self, root):
        text = root/'text'
        text.mkdir()
        (text/'one.txt').write_text('Rates increase. Inflation eased. 中文🙂', encoding='utf-8')
        (text/'later.txt').write_text('Inflation collapsed', encoding='utf-8')
        (text/'corpus_index.json').write_text(json.dumps({'documents':[
            {'doc_id':'one','file':'one.txt','timestamp':'2024-01-01'},
            {'doc_id':'later','file':'later.txt','timestamp':'2024-02-01'}]}))
        return text

    def test_release_filter_and_exact_quote(self):
        with tempfile.TemporaryDirectory() as name:
            text = self.fixture(Path(name))
            docs,audit = load_text(text,'2024-01-01')
            self.assertEqual(list(docs),['one'])
            self.assertEqual(audit['excluded'][0]['reason'],'post_cutoff')
            hit = retrieve(docs,'inflation')[0]
            self.assertEqual(hit['quote'],docs['one']['text'][hit['span_start']:hit['span_end']])
            self.assertEqual(retrieve(docs,'unrelated'),[])

    def test_missing_dates_and_index_no_text_fallback(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            self.assertEqual(load_text(root,'2024-01-01')[0],{})
            text=self.fixture(root)
            path=text/'corpus_index.json'
            index=json.loads(path.read_text())
            index['documents'][0].pop('timestamp')
            path.write_text(json.dumps(index))
            docs,audit=load_text(text,'2024-01-01')
            self.assertEqual(docs,{})
            self.assertEqual(len(audit['excluded']),2)

    def test_unicode_filenames_are_valid_single_components(self):
        with tempfile.TemporaryDirectory() as name:
            text=self.fixture(Path(name))
            path=text/'corpus_index.json'
            index=json.loads(path.read_text())
            filename='bis_cœuré_constâncio.txt'
            (text/'one.txt').rename(text/filename)
            index['documents'][0]['file']=filename
            path.write_text(json.dumps(index),encoding='utf-8')
            docs,_=load_text(text,'2024-01-01')
            self.assertIn('one',docs)

    def test_duplicate_ids_traversal_and_bad_cutoff(self):
        with tempfile.TemporaryDirectory() as name:
            text=self.fixture(Path(name))
            path=text/'corpus_index.json'
            original=json.loads(path.read_text())
            for changed in ('duplicate','traversal'):
                index=deepcopy(original)
                if changed=='duplicate': index['documents'].append(index['documents'][0])
                else: index['documents'][0]['file']='../one.txt'
                path.write_text(json.dumps(index))
                with self.assertRaises(ValueError): load_text(text,'2024-01-01')
            with self.assertRaises(ValueError): load_text(text,'20240101')

    def event_fixture(self):
        grid=GridSpec(('A','B'),(1,5))
        samples=np.arange(800,dtype=float).reshape(200,2,2)
        docs={'one':{'text':'Rates increase.','released':'2024-01-01','sha256':'synthetic'}}
        event={'event_id':'e1','asset':'A','horizon':1,'doc_id':'one','span_start':0,'span_end':15,
               'quote':'Rates increase.','mean_shift_sigma':100,'vol_multiplier':100}
        return grid,samples,docs,event

    def test_bounded_adjustment_and_preserved_other_cells(self):
        grid,samples,docs,event=self.event_fixture()
        adjusted,audit=adjust_samples(samples,grid,[event],docs,'2024-01-01')
        self.assertAlmostEqual(adjusted[:,0,0].mean()-samples[:,0,0].mean(),samples[:,0,0].std()*0.25)
        self.assertAlmostEqual(adjusted[:,0,0].std()/samples[:,0,0].std(),1.25)
        np.testing.assert_array_equal(adjusted[:,1,:],samples[:,1,:])
        self.assertEqual(audit['event_count'],1)
        self.assertIn('not measured',audit['quality_evidence'])

    def test_no_events_bit_identical_and_zero_spread(self):
        grid,samples,docs,event=self.event_fixture()
        adjusted,audit=adjust_samples(samples,grid,[],docs,'2024-01-01')
        self.assertEqual(adjusted.tobytes(),samples.tobytes())
        self.assertEqual(audit['text_contribution'],'none')
        zeros=np.zeros_like(samples)
        np.testing.assert_array_equal(adjust_samples(zeros,grid,[event],docs,'2024-01-01')[0],zeros)

    def test_aggregate_clamp_cannot_be_bypassed_by_multiple_events(self):
        grid,samples,docs,event=self.event_fixture()
        events=[dict(event,event_id=str(i)) for i in range(20)]
        adjusted,audit=adjust_samples(samples,grid,events,docs,'2024-01-01')
        self.assertEqual(audit['mean_shift_sigma'][0][0],0.25)
        self.assertEqual(audit['vol_multiplier'][0][0],1.25)
        self.assertTrue(np.isfinite(adjusted).all())

    def test_invalid_events_fail_whole_adjustment(self):
        grid,samples,docs,event=self.event_fixture()
        changes=[{'asset':'unknown'},{'horizon':True},{'mean_shift_sigma':float('nan')},
                 {'vol_multiplier':0},{'quote':'invented'},{'span_end':100},{'doc_id':'missing'}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):
                adjust_samples(samples,grid,[dict(event,**change)],docs,'2024-01-01')
        with self.assertRaises(ValueError): adjust_samples(samples,grid,[event,event],docs,'2024-01-01')
        with self.assertRaises(ValueError): adjust_samples(samples,grid,[event],docs,'2023-12-31')


if __name__=='__main__': unittest.main()
