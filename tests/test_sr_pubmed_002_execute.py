import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('engine', Path(__file__).resolve().parents[1] / 'scripts/sr_pubmed_002_execute.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
QUERY = Path(__file__).resolve().parents[1] / '02_Search_Strategies/PubMed_SR-PUBMED-001_query.txt'

def search(ids='', count=3):
    return ('<eSearchResult><Count>'+str(count)+'</Count><QueryTranslation>test</QueryTranslation><WebEnv>w</WebEnv><QueryKey>1</QueryKey><IdList>'+''.join('<Id>'+i+'</Id>' for i in ids.split(',') if i)+'</IdList></eSearchResult>').encode()

class Fake:
    responses = []
    def __init__(self, out, **kw): self.out=Path(out); self.index=0
    def request(self, endpoint, params, name):
        value=self.responses[self.index]; self.index+=1
        (self.out/(name+'.raw')).write_bytes(value)
        return value

class Tests(unittest.TestCase):
    def run_case(self, responses):
        Fake.responses=responses
        with tempfile.TemporaryDirectory() as d:
            out,r=m.execute(QUERY,d,2,client_factory=Fake)
            self.assertTrue((out/'final_report.json').exists())
            for row in json.loads((out/'sha256_manifest.json').read_text())['files']:
                self.assertFalse(Path(row['path']).is_absolute())
                self.assertEqual(m.sha((out/row['path']).read_bytes()),row['sha256'])
            return r
    def good(self): return [search(),search('1,2'),search('3'),b'PMID- 1\nPMID- 2\n',b'PMID- 3\n']
    def test_complete(self): self.assertEqual(self.run_case(self.good())['status'],'API_RETRIEVAL_COMPLETE')
    def test_page_missing(self): self.assertEqual(self.run_case([search(),search('1')])['status'],'FAIL')
    def test_duplicate(self): self.assertEqual(self.run_case([search(),search('1,1'),search('3')])['status'],'FAIL')
    def test_changed_count(self): self.assertEqual(self.run_case([search(),search('1,2',4)])['status'],'FAIL')
    def test_fetch_missing(self):
        x=self.good(); x[3]=b'PMID- 1\n'; self.assertEqual(self.run_case(x)['status'],'FAIL')
    def test_fetch_duplicate(self):
        x=self.good(); x[3]=b'PMID- 1\nPMID- 1\n'; self.assertEqual(self.run_case(x)['status'],'FAIL')
    def test_limit(self): self.assertEqual(self.run_case([search(count=10000)])['status'],'FAIL')
    def test_hash_reject(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad';p.write_bytes(b'bad')
            with self.assertRaises(ValueError): m.execute(p,Path(d)/'runs')
            self.assertFalse((Path(d)/'runs').exists())
    def test_retry_and_bytes(self):
        class Response:
            code=200;headers={}
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self): return b'\x00\xff\r\n'
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m.urllib.request,'urlopen',side_effect=[TimeoutError('timeout'),Response()]) as op:
                c=m.Client(d,sleep=lambda x:None);b=c.request('einfo.fcgi',{},'test')
                self.assertEqual(op.call_count,2);self.assertEqual((Path(d)/'test.attempt2.raw').read_bytes(),b)
                self.assertEqual(len(c.attempts),2)
    def test_retry_exhausted(self):
        with tempfile.TemporaryDirectory() as d:
            c=m.Client(d,opener=lambda *a,**k:(_ for _ in ()).throw(TimeoutError()),sleep=lambda x:None)
            with self.assertRaises(TimeoutError): c.request('test',{},'test')
            self.assertEqual(len(c.attempts),3);self.assertTrue((Path(d)/'requests.jsonl').exists())
    def test_dry_run_offline(self):
        spec=importlib.util.spec_from_file_location('dry',QUERY.parents[1]/'scripts/sr_pubmed_002.py')
        dry=importlib.util.module_from_spec(spec);spec.loader.exec_module(dry)
        with tempfile.TemporaryDirectory() as d, patch.object(m.urllib.request,'urlopen',side_effect=AssertionError('network')), patch('sys.argv',['dry','--query-file',str(QUERY),'--output-dir',d,'--run-id','PREPARATION-ONLY','--dry-run']): dry.main()

if __name__=='__main__': unittest.main()
