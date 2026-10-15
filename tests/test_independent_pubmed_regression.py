"""Independent offline probes; never calls a real network opener."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('reviewed', ROOT/'scripts/sr_pubmed_002_execute.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
SOURCE = ROOT/'02_Search_Strategies/PubMed_SR-PUBMED-001_query.txt'
EVIDENCE = ROOT/'04_Search_Audit/ENV-NCBI-003/run_38066988929/env_ncbi_test_medline.txt'

def xml(ids='', count=2, key='1', env='original', start=0):
    return ('<eSearchResult><Count>'+str(count)+'</Count><RetStart>'+str(start)+'</RetStart><RetMax>'+str(1 if ids else 0)+'</RetMax><QueryKey>'+key+'</QueryKey><WebEnv>'+env+'</WebEnv><QueryTranslation>test</QueryTranslation><IdList>'+''.join('<Id>'+i+'</Id>' for i in ids.split(',') if i)+'</IdList></eSearchResult>').encode()

def med(i):
    return ('PMID- '+i+'\nTI  - Offline fixture\nDP  - 2020\nAB  - first line\n      continuation\n').encode()

class Fake:
    values=[]
    def __init__(self,out,**kw):self.out=Path(out);self.i=0
    def request(self,ep,params,name):
        b=self.values[self.i];self.i+=1
        (self.out/(name+'.raw')).write_bytes(b)
        return b

def simulate(values):
    Fake.values=values
    with tempfile.TemporaryDirectory(prefix='independent-offline-') as d:
        out,r=M.execute(SOURCE,d,1,client_factory=Fake,simulation=True)
        return r

class Probes(unittest.TestCase):
    def test_real_medline(self):
        if not EVIDENCE.exists():self.skipTest('Supply verified local ENV-NCBI-003 sample; never committed')
        b=EVIDENCE.read_bytes()
        self.assertEqual(hashlib.sha256(b).hexdigest(),'5d22acf33e40383d8ecf022df7d1019a7b7f36da6cfd8361d13d44e8762f4546')
        self.assertEqual(M.parse_medline(b),['31452104'])
    def test_multiline(self): self.assertEqual(M.parse_medline(med('1')),['1'])
    def test_empty(self):
        with self.assertRaises(ValueError):M.parse_medline(b'')
    def test_count_mismatch(self):
        self.assertEqual(simulate([xml(),xml('',start=0)])['status'],'INCOMPLETE')
    def test_missing_history(self):
        self.assertEqual(simulate([xml(key='')])['status'],'FAILED')
    def test_http_body_invalid(self):self.assertEqual(simulate([b'<html>error</html>'])['status'],'FAILED')
    def test_incomplete_zero_result_schema_accepted_REPRO(self):
        self.assertEqual(simulate([b'<eSearchResult><Count>0</Count></eSearchResult>'])['status'],'FAILED')
    def test_wrong_history_is_accepted_REPRO(self):
        r=simulate([xml(),xml('1',env='changed',key='999'),xml('2',env='changed',key='999',start=1),med('1'),med('2')])
        self.assertEqual(r['status'],'INCOMPLETE')
    def test_wrong_page_boundary_is_accepted_REPRO(self):
        r=simulate([xml(),xml('1',start=100),xml('2',start=100),med('1'),med('2')])
        self.assertEqual(r['status'],'INCOMPLETE')
    def test_truncated_abstract_is_accepted_REPRO(self):
        with self.assertRaises(ValueError):M.parse_medline(b'PMID- 1\nTI  - Title\nDP  - 2020\nAB  - This abstract was cut mid')
    def test_orphan_boundary_rejected(self):
        with self.assertRaises(ValueError):M.parse_medline(b'      orphan\n'+med('1'))
    def test_artifact_missing_raw_report_stays_complete_REPRO(self):
        Fake.values=[xml(),xml('1'),xml('2',start=1),med('1'),med('2')]
        with tempfile.TemporaryDirectory() as d:
            out,r=M.execute(SOURCE,d,1,client_factory=Fake,simulation=True)
            original=json.loads((out/'final_report.json').read_text())
            manifest=json.loads((out/'sha256_manifest.json').read_text())
            (out/'efetch_0.raw').unlink() # Only a temporary simulated artifact.
            self.assertEqual(original['status'],'API_RETRIEVAL_COMPLETE')
            self.assertTrue(any(not (out/row['path']).exists() for row in manifest['files']))
    def test_tampered_artifact_hash_detectable(self):
        Fake.values=[xml(),xml('1'),xml('2',start=1),med('1'),med('2')]
        with tempfile.TemporaryDirectory() as d:
            out,r=M.execute(SOURCE,d,1,client_factory=Fake,simulation=True)
            manifest=json.loads((out/'sha256_manifest.json').read_text())
            row=next(x for x in manifest['files'] if x['path']=='efetch_0.raw')
            (out/row['path']).write_bytes(b'tampered simulated fixture')
            self.assertNotEqual(M.sha((out/row['path']).read_bytes()),row['sha256'])
    def test_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad';p.write_bytes(SOURCE.read_bytes()+b' ')
            with self.assertRaises(ValueError):M.load_query(p)

if __name__=='__main__':unittest.main(verbosity=2)
