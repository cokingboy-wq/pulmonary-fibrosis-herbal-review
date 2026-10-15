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
    return ('<eSearchResult><Count>'+str(count)+'</Count><RetStart>'+str(2 if ids=='3' else 0)+'</RetStart><RetMax>'+str(len(ids.split(',')) if ids else 0)+'</RetMax><QueryTranslation>test</QueryTranslation><WebEnv>w</WebEnv><QueryKey>1</QueryKey><IdList>'+''.join('<Id>'+i+'</Id>' for i in ids.split(',') if i)+'</IdList></eSearchResult>').encode()

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
            out,r=m.execute(QUERY,d,2,client_factory=Fake,simulation=True)
            self.assertTrue((out/'final_report.json').exists())
            for row in json.loads((out/'sha256_manifest.json').read_text())['files']:
                self.assertFalse(Path(row['path']).is_absolute())
                self.assertEqual(m.sha((out/row['path']).read_bytes()),row['sha256'])
            return r
    def good(self): return [search(),search('1,2'),search('3'),b'PMID- 1\nTI  - Title one\nDP  - 2020\n\nPMID- 2\nTI  - Title two\nDP  - 2021\n',b'PMID- 3\nTI  - Last batch\nDP  - 2022\n']
    def test_complete(self): self.assertEqual(self.run_case(self.good())['status'],'API_RETRIEVAL_COMPLETE')
    def test_page_missing(self): self.assertEqual(self.run_case([search(),search('1')])['status'],'INCOMPLETE')
    def test_duplicate(self): self.assertEqual(self.run_case([search(),search('1,1'),search('3')])['status'],'INCOMPLETE')
    def test_changed_count(self): self.assertEqual(self.run_case([search(),search('1,2',4)])['status'],'INCOMPLETE')
    def test_fetch_missing(self):
        x=self.good(); x[3]=b'PMID- 1\n'; self.assertEqual(self.run_case(x)['status'],'INCOMPLETE')
    def test_fetch_duplicate(self):
        x=self.good(); x[3]=b'PMID- 1\nPMID- 1\n'; self.assertEqual(self.run_case(x)['status'],'INCOMPLETE')
    def test_limit(self): self.assertEqual(self.run_case([search(count=10000)])['status'],'INCOMPLETE')
    def test_empty_200(self):
        x=self.good();x[3]=b'';self.assertEqual(self.run_case(x)['status'],'INCOMPLETE')
    def test_unexpected_pmid(self):
        x=self.good();x[3]=x[3].replace(b'PMID- 2',b'PMID- 9');self.assertEqual(self.run_case(x)['status'],'INCOMPLETE')
    def test_html_200(self):
        x=self.good();x[3]=b'<html>Error</html>';self.assertEqual(self.run_case(x)['status'],'INCOMPLETE')
    def test_missing_pmid(self):
        x=self.good();x[3]=x[3].replace(b'PMID- 1\n',b'');self.assertEqual(self.run_case(x)['status'],'INCOMPLETE')
    def test_duplicate_valid_records(self):
        x=self.good();x[3]=x[3].replace(b'PMID- 2',b'PMID- 1');self.assertEqual(self.run_case(x)['status'],'INCOMPLETE')
    def test_missing_record(self):
        x=self.good();x[3]=x[3].split(b'\n\n')[0];self.assertEqual(self.run_case(x)['status'],'INCOMPLETE')
    def test_failed_initial(self): self.assertEqual(self.run_case([b'<bad'])['status'],'FAILED')
    def test_web_pending(self): self.assertEqual(self.run_case(self.good())['web_evidence_status'],'PENDING_WEB_EVIDENCE')
    def test_connection_failure_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            out,r=m.execute(QUERY,d,simulation=True,retries=0,sleep=lambda x:None,
                opener=lambda *a,**k:(_ for _ in ()).throw(TimeoutError('offline simulation')))
            metadata=json.loads((out/'run_metadata.json').read_text())
            self.assertEqual(metadata['request_state'],'REQUEST_ATTEMPTED')
            self.assertFalse(metadata['ncbi_accessed']);self.assertEqual(r['status'],'FAILED')
            self.assertTrue(out.name.startswith('SIMULATION-'))
    def test_http_invalid_metadata(self):
        class Response:
            code=200;headers={}
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self, size=-1):
                b=getattr(self,'b',b'not XML');self.b=b'';return b
        with tempfile.TemporaryDirectory() as d:
            out,r=m.execute(QUERY,d,simulation=True,opener=lambda *a,**k:Response(),sleep=lambda x:None)
            self.assertEqual(json.loads((out/'run_metadata.json').read_text())['request_state'],'HTTP_RESPONSE_RECEIVED')
            self.assertEqual(r['status'],'FAILED')
    def test_middle_network_failure(self):
        class Response:
            code=200;headers={}
            def __init__(self,b):self.b=b
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self, size=-1):
                b,self.b=self.b,b"";return b
        with tempfile.TemporaryDirectory() as d:
            values=iter([Response(x) for x in self.good()[:4]]+[TimeoutError('midstream')])
            def opener(*a,**kw):
                value=next(values)
                if isinstance(value,Exception):raise value
                return value
            out,r=m.execute(QUERY,d,2,simulation=True,opener=opener,sleep=lambda x:None,retries=0)
            self.assertEqual(r['status'],'INCOMPLETE');self.assertTrue((out/'efetch_0.attempt1.raw').exists())
            self.assertEqual(json.loads((out/'run_metadata.json').read_text())['request_state'],'VALID_SEARCH_RESULT')
    def test_offline_pipeline_metadata(self):
        r=self.run_case(self.good())
        self.assertEqual(r['reconciliation']['missing'],[])
        self.assertEqual(r['batches'][-1]['requested'],['3'])
    def test_partial_read_saved(self):
        import http.client
        class Response:
            code=200;headers={}
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self, size=-1):raise http.client.IncompleteRead(b'PMID- 1',20)
        with tempfile.TemporaryDirectory() as d:
            c=m.Client(d,opener=lambda *a,**k:Response(),sleep=lambda x:None,retries=0)
            with self.assertRaises(http.client.IncompleteRead):c.request('efetch.fcgi',{},'partial')
            p=Path(d)/'partial.attempt1.raw';self.assertEqual(p.read_bytes(),b'PMID- 1')
            self.assertEqual(c.attempts[0]['sha256'],m.sha(p.read_bytes()))
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
            def read(self, size=-1):
                b=getattr(self,'b',b'\x00\xff\r\n');self.b=b'';return b
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
