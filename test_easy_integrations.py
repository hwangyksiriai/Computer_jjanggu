"""Cross-feature integration with synthetic folders, never a live mailbox."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from easy_app import EasyApp


class EasyIntegrationTests(unittest.TestCase):
    def test_mail_sync_adds_only_attachment_roots_and_disconnect_preserves_other_scopes(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            local=base/'documents';attachment=base/'mail'/'attachments'
            local.mkdir();attachment.mkdir(parents=True)
            app=EasyApp.__new__(EasyApp)
            app.settings={'document_roots':[str(local)]};app.status=SimpleNamespace(set=Mock())
            roots=[str(local)]
            app.document_roots=lambda:[Path(p) for p in roots]
            def set_roots(values):
                roots[:]=values;app.settings['document_roots']=list(values)
            app.set_document_roots=Mock(side_effect=set_roots)
            app.document_locations_changed=Mock()
            store=SimpleNamespace(attachment_roots=Mock(return_value=[str(attachment)]))
            app.get_mail_store=lambda:store
            app.mail_attachments_changed()
            self.assertEqual(roots,[str(local),str(attachment)])
            self.assertNotIn(str(base/'mail'),roots)
            app.mail_attachments_changed()
            self.assertEqual(roots,[str(local),str(attachment)])
            store.attachment_roots.return_value=[]
            app.mail_attachments_changed()
            self.assertEqual(roots,[str(local)])
            self.assertEqual(app.document_locations_changed.call_count,3)

    def test_mail_import_preserves_saved_offline_folder_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);offline=base/'disconnected-drive';cache=base/'mail-attachments'
            cache.mkdir()
            app=EasyApp.__new__(EasyApp)
            app.settings={'document_roots':[str(offline)]}
            app.status=SimpleNamespace(set=Mock());app.document_roots=Mock(return_value=[])
            app.get_mail_store=lambda:SimpleNamespace(attachment_roots=lambda:[str(cache)])
            app.set_document_roots=Mock();app.document_locations_changed=Mock()
            app.mail_attachments_changed()
            self.assertEqual(app.set_document_roots.call_args.args[0],[str(offline),str(cache)])

    def test_document_scope_change_leaves_active_photo_search_and_saved_photos_alone(self):
        app=EasyApp.__new__(EasyApp)
        app.bubble=None;app.page='more';app.refresh_smart_collections=Mock()
        document=SimpleNamespace(closed=False,context_title=None,search_query='계약서',
                                 update_results=Mock(),coverage=Mock())
        galleries=[SimpleNamespace(_closed=False,search_query='파란 사진',update_results=Mock(),coverage=Mock()),
                   SimpleNamespace(_closed=False,search_query=None,saved_context=True,update_results=Mock(),coverage=Mock())]
        app.result_browsers=[document,*galleries]
        app.document_locations_changed()
        document.update_results.assert_called_once_with([],'계약서')
        for gallery in galleries:
            gallery.update_results.assert_not_called();gallery.coverage.configure.assert_not_called()

    def test_final_update_ignores_photo_gallery_and_closed_file_windows(self):
        app=EasyApp.__new__(EasyApp)
        document=SimpleNamespace(closed=False,final_state_changed=Mock())
        closed=SimpleNamespace(closed=True,final_state_changed=Mock())
        gallery=SimpleNamespace(_closed=False)
        app.result_browsers=[document,closed,gallery]
        app.final_versions_changed()
        document.final_state_changed.assert_called_once_with()
        closed.final_state_changed.assert_not_called()


if __name__=='__main__':unittest.main()
