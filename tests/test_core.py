import unittest
from evidence_link_audit import analyze

class EvidenceTests(unittest.TestCase):
    def test_stale_snippet(self):
        r=analyze({'files':{'x':'new'},'evidence':[{'id':'a','path':'x','start':1,'contains':'old'}]})
        self.assertFalse(r['valid'])
    def test_range_end_is_inclusive(self):
        r=analyze({'files':{'x':'a\nb\nc'},'evidence':[{'id':'a','path':'x','start':2,'end':3,'contains':'b\nc'}]})
        self.assertTrue(r['valid'])
    def test_crlf_normalization(self):
        a={'id':'a','path':'x','start':1,'end':2}
        r1=analyze({'files':{'x':'a\r\nb\r\n'},'evidence':[a]})
        r2=analyze({'files':{'x':'a\nb\n'},'evidence':[a]})
        self.assertEqual(r1,r2)
    def test_missing_file(self):
        self.assertFalse(analyze({'files':{},'evidence':[{'id':'a','path':'x','start':1}]})['valid'])
    def test_bad_range(self):
        self.assertFalse(analyze({'files':{'x':'a'},'evidence':[{'id':'a','path':'x','start':0}]})['valid'])
