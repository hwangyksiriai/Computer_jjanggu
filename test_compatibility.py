import bootstrap
import unittest
import zipfile
import uuid
import shutil
from pathlib import Path
import numpy as np
from core import extract,Library
from audio_input import to_16khz,input_config
from unittest.mock import patch

class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.base=(Path(__file__).parent/'.local/tests').resolve()
        self.tmp=self.base/uuid.uuid4().hex; self.tmp.mkdir(parents=True)
    def tearDown(self):
        assert self.base in self.tmp.resolve().parents
        shutil.rmtree(self.tmp)
    def test_text_encodings_and_unicode_paths(self):
        for encoding in ('utf-8-sig','utf-16','cp949'):
            p=self.tmp/f'한글 공백 ({encoding}).txt'; p.write_bytes('청구서 지급기한'.encode(encoding))
            self.assertIn('청구서',extract(p)[0],encoding)
    def test_office_xml_variants(self):
        fixtures=[('invoice.docx','word/document.xml','<w:document xmlns:w="urn:test"><w:p><w:r><w:t>Invoice 1200</w:t></w:r></w:p></w:document>'),
                  ('invoice.pptx','ppt/slides/slide1.xml','<a:p xmlns:a="urn:test"><a:t>Invoice 1200</a:t></a:p>'),
                  ('inline.xlsx','xl/worksheets/sheet1.xml','<worksheet xmlns="urn:test"><sheetData><row><c t="inlineStr"><is><t>Invoice 1200</t></is></c></row></sheetData></worksheet>'),
                  ('shared.xlsx','xl/sharedStrings.xml','<sst xmlns="urn:test"><si><t>Invoice 1200</t></si></sst>')]
        for name,part,xml in fixtures:
            p=self.tmp/name
            with zipfile.ZipFile(p,'w') as z: z.writestr(part,xml)
            self.assertIn('Invoice 1200',extract(p)[0],name)
    def test_pdf_encryption_and_corrupt_file(self):
        import pymupdf
        p=self.tmp/'한글 이름.pdf'; doc=pymupdf.open(); page=doc.new_page(); page.insert_text((50,50),'Invoice USD 1200'); doc.save(p); doc.close()
        self.assertIn('Invoice',extract(p)[0])
        from pypdf import PdfReader,PdfWriter
        writer=PdfWriter(); writer.append(PdfReader(p)); writer.encrypt('test-only'); secure=self.tmp/'locked.pdf'
        with secure.open('wb') as f: writer.write(f)
        self.assertIn('암호',extract(secure)[1])
        corrupt=self.tmp/'corrupt.docx'; corrupt.write_bytes(b'not a zip')
        self.assertIn('분석 불가',extract(corrupt)[1])
    def test_tcl_multifile_paths(self):
        import tkinter
        t=tkinter.Tcl(); paths=(str(self.tmp/'한글 공백.txt'),str(self.tmp/'중괄호 {a}.pdf'))
        # Real Tcl list serialization, the format used by TkDND.
        t.call('set','files',paths)
        encoded=t.eval('set files')
        self.assertEqual(t.splitlist(encoded),paths)
    def test_audio_conversion(self):
        for rate in (16000,44100,48000):
            signal=np.sin(2*np.pi*440*np.arange(rate)/rate).astype(np.float32)
            out=to_16khz(signal,rate)
            self.assertEqual(len(out),16000)
            self.assertEqual(out.dtype,np.float32)
            self.assertAlmostEqual(float(np.sqrt(np.mean(out**2))),2**-.5,places=2)
    def test_missing_default_microphone(self):
        with patch('audio_input.sd.default') as default:
            default.device=(-1,1)
            with self.assertRaisesRegex(ValueError,'기본 마이크'): input_config()

if __name__=='__main__': unittest.main()
