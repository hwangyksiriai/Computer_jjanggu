"""Photo discovery uses temporary originals and never moves or edits them."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from photo_library import PhotoLibrary,photo_roots
from knowledge import Knowledge


class CountingAI:
    def __init__(self):self.calls=[]
    def ready_for(self,feature):return feature=='vision'
    def detector_ready(self):return True
    def call(self,op,**kwargs):
        self.calls.append((op,kwargs['path']))
        if op=='image':return [1.,0.]
        if op=='objects':return [dict(label='chair',score=.95,
                                     box=dict(xmin=0,ymin=0,xmax=1,ymax=1))]
        raise AssertionError(op)


class PhotoLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.home=self.base/'person'
        for name in ('Desktop','Downloads','Pictures','Documents'):(self.home/name).mkdir(parents=True)
        self.ai=CountingAI()
        self.library=PhotoLibrary(self.base/'catalog',ai=self.ai)

    def tearDown(self):self.temp.cleanup()

    def photo(self,relative,color='blue',exif=None):
        path=self.home/relative;path.parent.mkdir(parents=True,exist_ok=True)
        image=Image.new('RGB',(96,64),color)
        kwargs={'exif':exif} if exif is not None else {}
        image.save(path,**kwargs)
        return path.resolve()

    def snapshot(self):
        return {str(p.relative_to(self.home)):(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns)
                for p in self.home.rglob('*') if p.is_file()}

    def source_analysis(self,path,visual=None,objects=None):
        source=Knowledge(self.base/'document-catalog')
        stat=path.stat();signature=f'{stat.st_mtime}:{stat.st_size}'
        fields=dict(issuer='Document-only metadata',visual_colors={'red':1.},
                    objects=objects if objects is not None else [dict(label='chair',score=.94,
                        box=dict(xmin=.1,ymin=.1,xmax=.9,ymax=.9),colors={'blue':.9})],_objects_version=1)
        with source.connect() as connection:
            connection.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)',
                (str(path),path.name,'','사진','분석됨',stat.st_mtime,stat.st_size,str(path.parent)))
            connection.execute('INSERT OR REPLACE INTO knowledge(path,signature,visual,fields) VALUES(?,?,?,?)',
                (str(path),signature,json.dumps([.4,.6] if visual is None else visual),json.dumps(fields)))
        return source

    def test_reuse_document_analysis_preserves_own_thumbnail_colors_and_originals(self):
        path=self.photo('Pictures/unknown_002.png','blue')
        source=self.source_analysis(path);roots=[self.home/'Pictures']
        self.library.ai=None;self.library.scan(roots)
        before=self.snapshot();source_before=hashlib.sha256(source.db.read_bytes()).hexdigest()
        own=self.library.rows(roots)[0]
        self.assertEqual(self.library.reuse_analysis(source,roots),1)
        reused=self.library.rows(roots)[0]
        self.assertEqual(reused['visual'],[.4,.6]);self.assertEqual(reused['fields']['_objects_version'],1)
        self.assertEqual(reused['fields']['objects'][0]['label'],'chair')
        self.assertEqual(reused['thumbnail'],own['thumbnail'])
        for name in ('brightness','photo_width','photo_height','visual_colors'):
            self.assertEqual(reused['fields'][name],own['fields'][name])
        self.assertNotIn('issuer',reused['fields'])
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(hashlib.sha256(source.db.read_bytes()).hexdigest(),source_before)
        with patch('photo_library.Image.open',side_effect=AssertionError('Completed cache reopened')):
            self.assertEqual(self.library.reuse_analysis(source,roots),0)

    def test_scan_reuses_analysis_after_registering_new_photos_before_ai(self):
        path=self.photo('Pictures/unknown_002.png')
        source=self.source_analysis(path);roots=[self.home/'Pictures'];before=self.snapshot()
        state=self.library.scan(roots,reuse_from=source)
        self.assertEqual(state['reused_analysis'],1);self.assertEqual(state['phase'],'complete')
        row=self.library.rows(roots)[0]
        self.assertEqual(row['visual'],[.4,.6]);self.assertTrue(row['thumbnail'])
        self.assertEqual(self.ai.calls,[]);self.assertEqual(self.snapshot(),before)

    def test_reuse_rejects_stale_source_signature_source_metadata_and_original(self):
        paths=[self.photo(f'Pictures/unknown_{number}.png') for number in range(3)]
        for path in paths:source=self.source_analysis(path)
        roots=[self.home/'Pictures'];self.library.ai=None;self.library.scan(roots)
        with source.connect() as connection:
            connection.execute('UPDATE knowledge SET signature=? WHERE path=?',('stale',str(paths[0])))
            connection.execute('UPDATE files SET size=size+1 WHERE path=?',(str(paths[1]),))
        old_time=paths[2].stat().st_mtime
        os.utime(paths[2],(old_time+2,old_time+2))
        before=self.snapshot()
        self.assertEqual(self.library.reuse_analysis(source,roots),0)
        self.assertTrue(all(not row['visual'] for row in self.library.rows(roots)))
        self.assertEqual(self.snapshot(),before)

    def test_reuse_skips_rotated_exif_for_orientation_correct_analysis(self):
        exif=Image.Exif();exif[274]=6
        path=self.photo('Pictures/rotated.jpg',exif=exif)
        source=self.source_analysis(path);roots=[self.home/'Pictures']
        self.library.ai=None;self.library.scan(roots)
        before=self.snapshot()
        self.assertEqual(self.library.reuse_analysis(source,roots),0)
        row=self.library.rows(roots)[0]
        self.assertFalse(row['visual']);self.assertNotIn('_objects_version',row['fields'])
        self.assertEqual((row['fields']['photo_width'],row['fields']['photo_height']),(64,96))
        self.assertEqual(self.snapshot(),before)

    def test_reuse_only_fills_missing_analysis_without_replacing_valid_values(self):
        first=self.photo('Pictures/first.png');second=self.photo('Pictures/second.png')
        self.source_analysis(first);source=self.source_analysis(second)
        roots=[self.home/'Pictures'];self.library.ai=None;self.library.scan(roots)
        for row in self.library.rows(roots):
            fields=row['fields']
            if row['path']==str(first):visual=[1.,0.]
            else:visual=[];fields.update(objects=[],_objects_version=1)
            self.library.store_photo(row,row['thumbnail'],fields,visual,'사진 미리보기 준비됨')
        self.assertEqual(self.library.reuse_analysis(source,roots),2)
        rows={row['path']:row for row in self.library.rows(roots)}
        self.assertEqual(rows[str(first)]['visual'],[1.,0.])
        self.assertEqual(rows[str(first)]['fields']['objects'][0]['label'],'chair')
        self.assertEqual(rows[str(second)]['visual'],[.4,.6])
        self.assertEqual(rows[str(second)]['fields']['objects'],[])

    def test_reuse_skips_invalid_cache_and_respects_requested_roots(self):
        bad=self.photo('Pictures/bad.png');outside=self.photo('Downloads/outside.png')
        self.source_analysis(bad);source=self.source_analysis(outside)
        roots=[self.home/'Pictures',self.home/'Downloads']
        self.library.ai=None;self.library.scan(roots)
        with source.connect() as connection:
            connection.execute('UPDATE knowledge SET visual=?,fields=? WHERE path=?',
                               ('[NaN, 1]',json.dumps({'objects':'broken','_objects_version':1}),str(bad)))
        self.assertEqual(self.library.reuse_analysis(source,[self.home/'Pictures']),0)
        self.assertTrue(all(not row['visual'] for row in self.library.rows(roots)))
        self.assertEqual(self.library.reuse_analysis(source,roots,cancelled=lambda:True),0)

    def test_common_folders_are_found_without_knowing_names_or_locations(self):
        originals={self.photo('Desktop/IMG_0937.jpg'),self.photo('Downloads/Kakao_001.png'),
                   self.photo('Pictures/2024/여행/DSC_0081.jpg'),self.photo('Documents/attachment_17.png')}
        (self.home/'Documents/notes.txt').write_text('의자 사진 설명',encoding='utf8')
        before=self.snapshot()
        roots=photo_roots(home=self.home)
        self.assertEqual(set(roots),{self.home/name for name in ('Desktop','Downloads','Pictures','Documents')})
        state=self.library.scan(roots)
        rows=self.library.rows(roots)
        self.assertEqual(state['phase'],'complete');self.assertEqual(state['registered'],4)
        self.assertEqual({Path(row['path']) for row in rows},originals)
        self.assertTrue(all(row['thumbnail'] and row['visual'] for row in rows))
        self.assertEqual(self.snapshot(),before)
        with self.library.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM moves').fetchone()[0],0)

    def test_roots_include_extra_and_vault_without_duplicate_descendants(self):
        vault=self.base/'vault';vault.mkdir()
        nested=self.home/'Pictures/holiday';nested.mkdir()
        roots=photo_roots(source=nested,vault=vault,extra=(self.home,),home=self.home)
        self.assertEqual(set(roots),{self.home,vault})
        self.assertNotIn(self.home/'Pictures',roots)

    def test_excluded_child_folder_and_generated_folders_are_not_catalogued(self):
        visible=self.photo('Pictures/kept.png')
        excluded=self.home/'Pictures/private'
        self.photo('Pictures/private/secret.png')
        self.photo('Pictures/node_modules/fixture.png')
        self.photo('Pictures/.hidden/preview.png')
        self.photo('Pictures/AppData/cached.png')
        self.photo('Pictures/.ignored.png')
        before=self.snapshot()
        self.library.exclude=[excluded.resolve()]
        self.library.scan([self.home/'Pictures'])
        self.assertEqual({Path(row['path']) for row in self.library.rows([self.home/'Pictures'])},{visible})
        self.assertEqual(self.snapshot(),before)

    def test_excluded_folder_is_respected_even_when_explicitly_passed_as_root(self):
        excluded=self.home/'Pictures/private'
        self.photo('Pictures/private/secret.png')
        self.library.exclude=[excluded.resolve()]
        before=self.snapshot()
        state=self.library.scan([excluded])
        self.assertEqual(state['registered'],0)
        self.assertEqual(self.library.rows([excluded]),[])
        self.assertEqual(self.snapshot(),before)

    def test_unsupported_photo_is_visible_as_unreadable_instead_of_silently_missing(self):
        unsupported=self.home/'Downloads/IMG_0099.HEIC'
        unsupported.write_bytes(b'unsupported-camera-format-for-test')
        before=self.snapshot()
        state=self.library.scan([self.home/'Downloads'])
        rows=self.library.rows([self.home/'Downloads'])
        self.assertEqual(state['registered'],1);self.assertEqual(state['unreadable'],1)
        self.assertEqual(rows[0]['name'],'IMG_0099.HEIC')
        self.assertTrue(rows[0]['fields']['photo_error']);self.assertFalse(rows[0]['thumbnail'])
        self.assertEqual(self.library.coverage([self.home/'Downloads'])['unreadable'],1)
        self.assertEqual(self.ai.calls,[]);self.assertEqual(self.snapshot(),before)

    def test_unchanged_photos_reuse_thumbnail_and_ai_analysis(self):
        self.photo('Pictures/unknown_002.png')
        roots=[self.home/'Pictures'];self.library.scan(roots)
        first=self.library.rows(roots)[0];thumb=Path(first['thumbnail'])
        thumb_mtime=thumb.stat().st_mtime_ns;first_calls=list(self.ai.calls)
        self.assertEqual([op for op,_ in first_calls],['image','objects'])
        before=self.snapshot()
        with patch('photo_library.Image.open',side_effect=AssertionError('Cached image reopened')):
            state=self.library.scan(roots)
        second=self.library.rows(roots)[0]
        self.assertEqual(state['phase'],'complete');self.assertEqual(state['unreadable'],0)
        self.assertEqual(self.ai.calls,first_calls);self.assertEqual(second['thumbnail'],first['thumbnail'])
        self.assertEqual(thumb.stat().st_mtime_ns,thumb_mtime)
        self.assertEqual(self.snapshot(),before)

    def test_changed_photo_invalidates_cached_appearance(self):
        original=self.photo('Pictures/unknown_002.png','blue')
        roots=[self.home/'Pictures'];self.library.scan(roots)
        first=self.library.rows(roots)[0]
        old_time=original.stat().st_mtime
        Image.new('RGB',(96,64),'red').save(original)
        os.utime(original,(old_time+2,old_time+2))
        before=self.snapshot()
        self.library.scan(roots);second=self.library.rows(roots)[0]
        self.assertNotEqual(first['thumbnail'],second['thumbnail'])
        self.assertGreater(second['fields']['visual_colors']['red'],.9)
        self.assertEqual(len(self.ai.calls),4);self.assertEqual(self.snapshot(),before)

    def test_cancel_before_scanning_keeps_originals_and_registers_nothing(self):
        self.photo('Pictures/unknown_002.png')
        before=self.snapshot()
        state=self.library.scan([self.home/'Pictures'],cancelled=lambda:True)
        self.assertEqual(state['phase'],'paused');self.assertEqual(state['registered'],0)
        self.assertEqual(self.library.rows([self.home/'Pictures']),[])
        self.assertEqual(self.ai.calls,[]);self.assertEqual(self.snapshot(),before)

    def test_cancel_after_catalog_and_resume_preserves_registered_files(self):
        self.photo('Pictures/unknown_002.png');self.photo('Pictures/unknown_003.png','red')
        roots=[self.home/'Pictures'];cancel={'now':False};progress=[]
        def observe(state):
            progress.append(state)
            if state['phase']=='thumbnails':cancel['now']=True
        before=self.snapshot()
        state=self.library.scan(roots,progress=observe,cancelled=lambda:cancel['now'])
        self.assertEqual(state['phase'],'paused');self.assertEqual(state['registered'],2)
        self.assertEqual(len(self.library.rows(roots)),2);self.assertEqual(self.ai.calls,[])
        self.assertEqual(progress[-1]['phase'],'paused')
        state=self.library.scan(roots)
        self.assertEqual(state['phase'],'complete')
        self.assertEqual(self.library.coverage(roots)['visual'],2)
        self.assertEqual(self.snapshot(),before)

    def test_cancel_during_analysis_resumes_without_repeating_completed_photo(self):
        self.photo('Pictures/unknown_002.png');self.photo('Pictures/unknown_003.png','red')
        roots=[self.home/'Pictures'];cancel={'now':False}
        call=self.ai.call
        def cancel_after_detection(op,**kwargs):
            result=call(op,**kwargs)
            if op=='objects':cancel['now']=True
            return result
        before=self.snapshot()
        with patch.object(self.ai,'call',side_effect=cancel_after_detection):
            state=self.library.scan(roots,cancelled=lambda:cancel['now'])
        self.assertEqual(state['phase'],'paused')
        self.assertEqual(self.library.coverage(roots)['visual'],1)
        self.assertEqual(len(self.ai.calls),2)
        state=self.library.scan(roots)
        self.assertEqual(state['phase'],'complete')
        self.assertEqual(self.library.coverage(roots)['visual'],2)
        self.assertEqual(len(self.ai.calls),4)
        self.assertEqual(self.snapshot(),before)

    def test_exif_capture_date_is_independent_from_file_modification_date(self):
        exif=Image.Exif();exif[36867]='2022:07:19 12:34:56'
        self.photo('Pictures/unknown_002.jpg',exif=exif)
        roots=[self.home/'Pictures'];self.library.scan(roots)
        row=self.library.rows(roots)[0]
        self.assertEqual(row['fields']['taken_date'],'2022-07-19')
        self.assertEqual((row['fields']['photo_width'],row['fields']['photo_height']),(96,64))


if __name__=='__main__':unittest.main()
