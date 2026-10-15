"""Cross-platform offline ENV-NCBI-003 tests; importing never executes main()."""
import hashlib
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('env_ncbi_003_under_test', ROOT/'scripts/env_ncbi_003.py')
ENV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENV)

# Explicitly synthetic unit fixtures; never external connectivity evidence.
XML = b'<eInfoResult><DbInfo><DbName>pubmed</DbName></DbInfo></eInfoResult>'
MEDLINE = 'PMID- 31452104\nTI  - Example title\nAU  - Author\nJT  - Journal\nDP  - 2020\nMH  - Fibrosis\n'
REAL_SAMPLE = ROOT/'04_Search_Audit/ENV-NCBI-003/run_38066988929/env_ncbi_test_medline.txt'
REAL_SHA256 = '5d22acf33e40383d8ecf022df7d1019a7b7f36da6cfd8361d13d44e8762f4546'

class EnvNcbi003Tests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(ENV.urllib.request, 'urlopen', side_effect=AssertionError('Live requests forbidden in unit tests'))
        guard.start()
        self.addCleanup(guard.stop)

    def validate(self, medline=MEDLINE, einfo_status=200, efetch_status=200, xml=XML):
        return ENV.validate(einfo_status, xml, efetch_status, medline.encode('utf-8'))

    def assertComplete(self, result):
        xml_ok, fields, pmid_ok = result
        self.assertTrue(xml_ok)
        self.assertTrue(all(fields.values()), fields)
        self.assertTrue(pmid_ok)

    def test_compact_pmid(self):
        self.assertEqual(ENV.parse_medline(MEDLINE)['PMID'], ['31452104'])

    def test_aligned_pmid(self):
        self.assertEqual(ENV.parse_medline(MEDLINE.replace('PMID-', 'PMID  -'))['PMID'], ['31452104'])

    def test_missing_pmid(self):
        self.assertFalse(self.validate(MEDLINE.replace('PMID- 31452104\n', ''))[2])

    def test_wrong_pmid(self):
        self.assertFalse(self.validate(MEDLINE.replace('31452104', '999'))[2])

    def test_missing_each_required_field(self):
        for field in ('TI', 'AU', 'JT', 'DP', 'MH'):
            with self.subTest(field=field):
                text=''.join(line for line in MEDLINE.splitlines(keepends=True) if not line.startswith(field+' '))
                fields=self.validate(text)[1]
                self.assertFalse(fields[field])
                self.assertFalse(all(fields.values()))

    def test_einfo_non_200(self):
        self.assertFalse(self.validate(einfo_status=500)[0])

    def test_efetch_non_200(self):
        self.assertFalse(self.validate(efetch_status=500)[0])

    def test_invalid_xml(self):
        self.assertFalse(self.validate(xml=b'bad')[0])

    def test_all_conditions(self):
        self.assertComplete(self.validate())

    def test_archived_real_medline(self):
        if not REAL_SAMPLE.is_file():
            self.skipTest('Verified Run 38066988929 MEDLINE archive is not present in this checkout; no sample fabricated or downloaded')
        data=REAL_SAMPLE.read_bytes()
        self.assertEqual(len(data), 2855)
        self.assertEqual(hashlib.sha256(data).hexdigest(), REAL_SHA256)
        self.assertComplete(ENV.validate(200, XML, 200, data))

if __name__ == '__main__':
    unittest.main()

