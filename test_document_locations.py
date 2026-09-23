from pathlib import Path
import tempfile
import unittest

from document_locations import ensure_document_roots,normalize_document_roots,suggested_document_locations


class DocumentLocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.home=Path(self.temporary.name)

    def folder(self,name):
        path=self.home/name;path.mkdir(parents=True,exist_ok=True);return path

    def test_migration_preserves_legacy_source_and_absent_vault_without_changing_organization(self):
        source=self.folder('old-source');vault=self.home/'not-created-vault'
        settings={'source':str(source),'vault':str(vault),'auto':True}
        self.assertTrue(ensure_document_roots(settings,source,vault))
        self.assertEqual(settings['document_roots'],[str(source),str(vault)])
        self.assertEqual((settings['source'],settings['vault'],settings['auto']),(str(source),str(vault),True))
        self.assertEqual(normalize_document_roots(settings['document_roots']),[source])
        vault.mkdir()
        self.assertEqual(normalize_document_roots(settings['document_roots']),[source,vault])

    def test_first_run_migrates_demo_but_explicit_choices_never_gain_source_or_vault(self):
        demo=self.folder('demo');vault=self.folder('vault');chosen=self.folder('chosen')
        settings={};ensure_document_roots(settings,demo,vault)
        self.assertEqual(settings['document_roots'],[str(demo),str(vault)])
        for explicit in ([],[str(chosen)],None,'bad setting'):
            settings={'document_roots':explicit}
            self.assertFalse(ensure_document_roots(settings,demo,vault))
            self.assertEqual(settings['document_roots'],explicit)

    def test_duplicate_nested_roots_are_scanned_once_regardless_of_order(self):
        parent=self.folder('Documents');child=self.folder('Documents/company');other=self.folder('Downloads')
        for values in ([child,other,parent,child],[parent,child,other,parent]):
            roots=normalize_document_roots(values)
            self.assertEqual(set(roots),{parent,other})
            self.assertEqual(len(roots),2)

    def test_missing_relative_malformed_and_file_roots_do_not_expand_search_scope(self):
        selected=self.folder('selected');file=self.home/'file.txt';file.write_text('synthetic')
        self.assertEqual(normalize_document_roots([selected,'relative',None,42,'',file,self.home/'absent']),[selected])
        self.assertEqual(normalize_document_roots('C:\\'),[])
        self.assertEqual(normalize_document_roots(None),[])

    def test_suggestions_are_existing_choices_without_scanning_or_changing_settings(self):
        for name in ('Desktop','Downloads','Documents','OneDrive'):
            self.folder(name)
        settings={'document_roots':[],'source':'','vault':''};before=dict(settings)
        suggestions=suggested_document_locations(home=self.home)
        self.assertEqual({item['label'] for item in suggestions},{'바탕화면','다운로드','문서','OneDrive'})
        self.assertEqual({Path(item['path']) for item in suggestions},{self.home/name for name in ('Desktop','Downloads','Documents','OneDrive')})
        self.assertEqual(settings,before)
        self.assertEqual(sorted(path.name for path in self.home.iterdir()),['Desktop','Documents','Downloads','OneDrive'])


if __name__=='__main__':unittest.main()
