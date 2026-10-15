"""Real failed History XML, plus offline request-contract regressions. No live calls."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from test_sr_pubmed_002_execute import m,QUERY,search

RAW=Path(__file__).parent/'fixtures/esearch_wrong_subquery_38669738950.xml'

class HistoryTests(unittest.TestCase):
    def test_real_failed_xml_exact_bytes(self):
        b=RAW.read_bytes()
        self.assertEqual(len(b),427)
        self.assertEqual(hashlib.sha256(b).hexdigest(),'095a7ff05185d1d794f40357eef6d2e94a4bb9512a8c4005dc86e03bf4c0d0e4')
    def test_real_wrong_subquery_rejected(self):
        with self.assertRaisesRegex(ValueError,'History error.*Wrong subquery #1'):m.parse_search(RAW.read_bytes())
    def test_blank_translation_still_rejected(self):
        with self.assertRaisesRegex(ValueError,'Empty QueryTranslation'):m.parse_search(search().replace(b'<QueryTranslation>test</QueryTranslation>',b'<QueryTranslation/>'))
    def test_missing_translation_still_rejected(self):
        with self.assertRaisesRegex(ValueError,'Missing ESearch field'):m.parse_search(search().replace(b'<QueryTranslation>test</QueryTranslation>',b''))
    def test_page_request_contract(self):
        requests=[];responses=iter([search(),search('1,2'),search('3'),b'PMID- 1\nTI  - One\nDP  - 2020\n\nPMID- 2\nTI  - Two\nDP  - 2021\n',b'PMID- 3\nTI  - Three\nDP  - 2020\n'])
        class Fake:
            def __init__(self,out,**kw):pass
            def request(self,ep,params,name):requests.append((ep,params,name));return next(responses)
        with tempfile.TemporaryDirectory() as d:
            _,r=m.execute(QUERY,d,2,simulation=True,client_factory=Fake)
        self.assertEqual(r['status'],'API_RETRIEVAL_COMPLETE')
        pages=[p for ep,p,n in requests if ep=='esearch.fcgi' and n!='esearch_initial']
        self.assertEqual(len(pages),2)
        for page in pages:
            self.assertEqual(page['usehistory'],'y');self.assertEqual(page['WebEnv'],'w');self.assertEqual(page['term'],'#1')
        self.assertEqual([(p['retstart'],p['retmax']) for p in pages],[(0,2),(2,1)])
    def test_real_failure_stays_incomplete(self):
        responses=iter([search(),RAW.read_bytes()])
        class Fake:
            def __init__(self,out,**kw):pass
            def request(self,*a):return next(responses)
        with tempfile.TemporaryDirectory() as d:
            _,r=m.execute(QUERY,d,2,simulation=True,client_factory=Fake)
        self.assertEqual(r['status'],'INCOMPLETE');self.assertIn('History error',r['errors'][0]);self.assertEqual(r['batches'],[])

if __name__=='__main__':unittest.main()
