"""Behavioral regressions for current production gates. Colored fixtures are never art-quality evidence."""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

import test_pipeline as fixtures

cp, cl = fixtures.cp, fixtures.cl


class ProductionGateTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_new_reference_preserves_existing_panel_bindings(self):
        f = self.f
        f.locked(); f.accept_all()
        binding = copy.deepcopy(cp.project_load(f.root)['art']['bindings'])
        f.reference()
        self.assertEqual(binding, cp.project_load(f.root)['art']['bindings'])
        self.assertEqual(2, f.invoke('status')['panels_accepted'])

    def test_font_and_page_settings_reuse_paintings(self):
        f = self.f
        f.locked(); f.accept_all()
        script = cp.project_load(f.root)['script']
        script['style'].update(font_size=30, width=1000, height=1400)
        f.invoke('set-script', file=f.json_file(script)); f.add_reviews(); f.invoke('lock-script')
        self.assertEqual(2, f.invoke('status')['panels_accepted'])

    def test_stale_pending_can_close_without_book_lock(self):
        f = self.f
        f.locked(); f.reference()
        prompt = f.base/'prompt.txt'; prompt.write_text('mechanical', encoding='utf-8')
        attempt = f.begin_one(panel='p1', prompt=str(prompt))
        script = cp.project_load(f.root)['script']; script['panels'][0]['action'] += '新动作'
        f.invoke('set-script', file=f.json_file(script))
        f.invoke('fail-panel', panel='p1', attempt=attempt['attempt'], reason='输入过期', outcome='stale')
        self.assertEqual('stale', cp.project_load(f.root)['art']['panels']['p1'][0]['status'])
        with self.assertRaises(cp.GateError):
            f.invoke('finish-panel', panel='p1', attempt=attempt['attempt'], file=f.image_file(), qa=f.qa(cp.PANEL_CHECKS))

    def test_state_field_deletion_requires_transition(self):
        f = self.f
        script=f.prepare_script()
        del script['panels'][1]['state_before']['char-a']['costume']
        del script['panels'][1]['state_after']['char-a']['costume']
        f.invoke('set-script',file=f.json_file(script))
        self.assertTrue(any('state' in e and ('costume' in e or 'change' in e) for e in f.invoke('check-script')['errors']))

    def test_malformed_script_reports_field_location(self):
        f=self.f
        script=f.prepare_script(); script['panels']=['broken']
        f.invoke('set-script',file=f.json_file(script))
        self.assertTrue(any('script.panels[0]' in e for e in f.invoke('check-script')['errors']))
        result=subprocess.run([sys.executable,'-B',str(Path(cp.__file__)),'check-script','--project',str(f.root)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(2,result.returncode)
        self.assertNotIn('Traceback',result.stdout+result.stderr)

    def test_missing_new_person_review_evidence_rejected(self):
        f=self.f; f.locked()
        bad={'checks':{k:True for k in cp.REFERENCE_CHECKS},'evidence':'mechanical test'}
        with self.assertRaises(cp.GateError):
            f.invoke('register-reference',characters=['char-a'],file=f.image_file(),qa=f.json_file(bad))

    def test_elegance_needs_specific_visual_observations(self):
        f=self.f;f.locked()
        qa=cp.load_json(f.qa(cp.REFERENCE_CHECKS))
        qa.pop('elegance_notes')
        with self.assertRaisesRegex(cp.GateError, 'elegance_notes'):
            f.invoke('register-reference',characters=['char-a'],file=f.image_file(),qa=f.json_file(qa))
        qa['elegance_notes']={'linework':'Mechanical fixture','color_and_light':'Mechanical fixture','visual_hierarchy':''}
        with self.assertRaisesRegex(cp.GateError, 'visual_hierarchy'):
            f.invoke('register-reference',characters=['char-a'],file=f.image_file(),qa=f.json_file(qa))

    def test_non_object_project_returns_diagnostic(self):
        f=self.f;f.locked()
        cp.atomic_json(f.root/'project.json',[])
        result=subprocess.run([sys.executable,'-B',str(Path(cp.__file__)),'status','--project',str(f.root)],
                              capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(2,result.returncode)
        self.assertIn('project: expected an object',result.stdout+result.stderr)
        self.assertNotIn('Traceback',result.stdout+result.stderr)

    def test_preflight_counts_pending_and_input_budget(self):
        f=self.f;f.locked();f.reference()
        prompt=f.base/'prompt.txt';prompt.write_text('mechanical',encoding='utf-8')
        f.begin_one(panel='p1',prompt=prompt)
        before=f.invoke('preflight')['preflight_counts']
        self.assertEqual(6,before['initial_panel_attempt_budget'])
        self.assertEqual(1,before['attempts_recorded'])
        self.assertEqual(1,before['pending_attempts'])
        self.assertEqual(5,before['current_input_attempt_slots'])
        self.assertEqual(0,before['panels_with_unknown_budget'])
        f.invoke('fail-panel',panel='p1',attempt=1,reason='test input closure',outcome='stale')
        after=f.invoke('preflight')['preflight_counts']
        self.assertEqual(0,after['pending_attempts'])
        self.assertEqual(5,after['current_input_attempt_slots'])

    def test_wrong_form_reference_blocks_generation(self):
        f=self.f
        script=f.prepare_script()
        script['characters'][0]['appearance_versions'].append({'id':'work','description':'工作装','visual':{'costume':'work'}})
        for panel in script['panels']:
            panel['appearance_versions']={'char-a':'work'}
            for state in ('state_before','state_after'):
                panel[state]['char-a'].update(form='work',costume='work')
        f.invoke('set-script',file=f.json_file(script)); f.add_reviews(); f.invoke('lock-script')
        image=f.image_file()
        f.invoke('register-reference',characters=['char-a'],file=image,
                 qa=f.reference_qa(image,characters=['char-a']))
        ref=cp.project_load(f.root)['art']['references'][-1]['id']
        with self.assertRaises(cp.GateError):
            f.invoke('bind-panel',panel='p1',bindings=f.json_file({'appearance_versions':{'char-a':'work'},'reference_ids':[ref]}))

    def test_tampered_page_cache_is_repaired(self):
        from PIL import Image
        f=self.f; f.locked(); f.accept_all(); f.invoke('compose',font=None)
        page=cp.project_load(f.root)['layout']['pages'][0]
        path=cp.inside(f.root,page['path']); original=page['sha256']
        Image.new('RGB',(page['width'],page['height']),'magenta').save(path)
        f.invoke('compose',font=None)
        self.assertEqual(original,cp.sha_file(path))
        self.assertTrue(cp.project_load(f.root)['layout']['cache_repairs'])

    def test_fonts_have_distinct_layout_versions(self):
        f=self.f; f.locked();f.accept_all(); f.invoke('compose',font=None)
        first=cp.project_load(f.root)['layout']
        candidates=[Path('C:/Windows/Fonts/simkai.ttf'),Path('C:/Windows/Fonts/simsun.ttc'),Path('C:/Windows/Fonts/msyh.ttc')]
        second=next((p for p in candidates if p.is_file() and p.resolve()!=Path(first['font_path']).resolve()),None)
        if second is None:self.skipTest('A second CJK font is not installed')
        f.invoke('compose',font=str(second))
        self.assertNotEqual(first['input_hash'],cp.project_load(f.root)['layout']['input_hash'])

    def test_fixed_page_height_and_overflow(self):
        f=self.f;f.locked();f.accept_all();f.invoke('compose',font=None)
        self.assertTrue(all(p['height']==1200 for p in cp.project_load(f.root)['layout']['pages']))
        script=cp.project_load(f.root)['script']
        script['pages'][0]['panel_ids']=['p1','p2'];script['pages'][0]['rows']=[['p1'],['p2']]
        # Keep both panels in the same actual chapter for this layout-only fixture.
        script['panels'][1]['chapter_id']=script['panels'][0]['chapter_id']
        script['panels'][1]['scene_id']=script['panels'][0]['scene_id']
        script['pages'].pop();script['style']['height']=600
        # A chapter cannot be omitted under the book gate, so test the renderer with a valid painting project.
        project=cp.project_load(f.root);project['script']['style']['height']=600
        with self.assertRaises(cp.GateError):cl.compose(f.root,project)

    def test_bubble_overflow_and_editable_manifest(self):
        f=self.f;f.locked();f.accept_all()
        script=cp.project_load(f.root)['script'];panel=script['panels'][0]
        panel['lettering_mode']='bubbles'
        panel['bubbles']=[{'dialogue_index':0,'rect':[0.05,0.05,0.8,0.3],'tail':None,'order':0}]
        f.invoke('set-script',file=f.json_file(script));f.add_reviews();f.invoke('lock-script');f.invoke('compose',font=None)
        layout=cp.project_load(f.root)['layout']
        self.assertTrue(all(page.get('lettering_manifest_path') for page in layout['pages']))
        panel['bubbles'][0]['rect']=[0.05,0.05,0.1,0.01]
        f.invoke('set-script',file=f.json_file(script));f.add_reviews();f.invoke('lock-script')
        with self.assertRaises(cp.GateError):f.invoke('compose',font=None)

    def test_archived_source_survives_original_move(self):
        f=self.f;f.locked();f.source.unlink()
        self.assertTrue(f.invoke('status')['script_locked'])

    def test_chapter_read_update_and_impact(self):
        f=self.f;f.locked();f.accept_all()
        before=cp.project_load(f.root)
        chapter=f.invoke('script-chapter',chapter='ch000001')
        self.assertEqual(['p1'],[p['id'] for p in chapter['panels']])
        candidate=copy.deepcopy(before['script']);candidate['panels'][0]['dialogue'][0]['text']+='文字'
        impact=f.invoke('impact',file=f.json_file(candidate))
        self.assertEqual(before,cp.project_load(f.root))
        chapter['panels'][0]['dialogue'][0]['text']+='文字'
        f.invoke('set-script-chapter',chapter='ch000001',file=f.json_file(chapter))
        self.assertEqual(before['script']['panels'][1],cp.project_load(f.root)['script']['panels'][1])
        self.assertFalse(f.invoke('status')['script_locked'])

    def test_original_sample_is_full_and_structurally_valid(self):
        f=self.f
        source=Path(__file__).parent/'fixtures'/'validation-story.txt'
        root=f.base/'真实样例结构'
        f.root=root;f.invoke('init',source=[str(source)],title='灯塔来信')
        f.invoke('confirm-source',note='本测试仅验证原始原创文本范围')
        for chapter in cp.project_load(root)['source']['chapters']:
            f.invoke('mark-read',chapter=chapter['id'],note='机械夹具阅读记录，不代表真实语义审查')
        script=cp.load_json(Path(__file__).parent/'fixtures'/'validation-script.json')
        f.invoke('set-script',file=f.json_file(script))
        self.assertEqual([],f.invoke('check-script')['errors'])
        self.assertEqual(16,len(script['panels']));self.assertEqual(4,len(script['pages']))

    def test_bad_identifier_types_return_field_diagnostics(self):
        f=self.f; original=f.prepare_script()
        mutations=[
            ('scene_id', lambda s:s['panels'][0].update(scene_id={})),
            ('chapter_id', lambda s:s['panels'][0].update(chapter_id=[])),
            ('source_unit_ids', lambda s:s['panels'][0].update(source_unit_ids=[{}])),
            ('event_ids', lambda s:s['panels'][0].update(event_ids=[[]])),
            ('appearance_versions', lambda s:s['panels'][0].update(appearance_versions={'char-a':{}})),
            ('comparison_with', lambda s:s['characters'][0].update(comparison_with=None)),
            ('other_character_id', lambda s:s['characters'][0].update(distinctions=[{'other_character_id':{}}])),
        ]
        for field, mutate in mutations:
            with self.subTest(field=field):
                script=copy.deepcopy(original);mutate(script)
                project=cp.project_load(f.root);project['script']=script
                errors=cp.script_errors(project,f.root)
                self.assertTrue(any(field in e for e in errors), errors)

    def test_visual_reference_keys_ignore_registry_order_duplicates_and_full_region_spelling(self):
        f=self.f;f.locked()
        subjects=[{'character_id':'char-a','version_id':'base','region':None}]
        full_region=[{'character_id':'char-a','version_id':'base','region':[0,0,1,1]}]
        self.assertEqual(cp.reference_visual_key('a'*64,'design','combined',subjects),
                         cp.reference_visual_key('a'*64,'design','combined',full_region))
        def register(image):
            return f.invoke('register-reference',file=image,
                bindings=f.json_file({'purpose':'combined','subjects':subjects}),
                qa=f.reference_qa(image,subjects=subjects))['reference_id']
        first_image=f.image_file('white');second_image=f.image_file('black')
        first=register(first_image);second=register(second_image)
        prompt=f.base/'prompt.txt';prompt.write_text('mechanical',encoding='utf-8')
        f.invoke('bind-panel',panel='p1',bindings=f.json_file(
            {'appearance_versions':{'char-a':'base'},'reference_ids':[first,second]}))
        one=f.begin_one(panel='p1',prompt=prompt)
        f.invoke('fail-panel',panel='p1',attempt=one['attempt'],reason='mechanical closure',outcome='cancelled')
        f.invoke('bind-panel',panel='p1',bindings=f.json_file(
            {'appearance_versions':{'char-a':'base'},'reference_ids':[second,first]}))
        two=f.begin_one(panel='p1',prompt=prompt)
        self.assertEqual(one['render_hash'],two['render_hash'])
        f.invoke('fail-panel',panel='p1',attempt=two['attempt'],reason='mechanical closure',outcome='cancelled')
        duplicate=register(first_image)
        f.invoke('bind-panel',panel='p1',bindings=f.json_file(
            {'appearance_versions':{'char-a':'base'},'reference_ids':[duplicate,second]}))
        three=f.begin_one(panel='p1',prompt=prompt)
        f.invoke('fail-panel',panel='p1',attempt=three['attempt'],reason='mechanical closure',outcome='cancelled')
        another_duplicate=register(first_image)
        f.invoke('bind-panel',panel='p1',bindings=f.json_file(
            {'appearance_versions':{'char-a':'base'},'reference_ids':[another_duplicate,second]}))
        with self.assertRaisesRegex(cp.GateError,'Three attempts exhausted'):
            f.begin_one(panel='p1',prompt=prompt)

    def test_reference_and_panel_qa_bind_exact_image_and_attempt(self):
        f=self.f;f.locked()
        reference_image=f.image_file()
        report=cp.load_json(f.reference_qa(reference_image,characters=['char-a']))
        report['image_sha256']='0'*64
        with self.assertRaisesRegex(cp.GateError,'image_sha256'):
            f.invoke('register-reference',characters=['char-a'],file=reference_image,qa=f.json_file(report))
        f.reference()
        prompt=f.base/'prompt.txt';prompt.write_text('mechanical',encoding='utf-8')
        attempt=f.begin_one(panel='p1',prompt=prompt)
        image=f.image_file()
        report=cp.load_json(f.panel_qa('p1',attempt['attempt'],image))
        report['attempt_bindings']['p1']['attempt']+=1
        with self.assertRaisesRegex(cp.GateError,'attempt_bindings'):
            f.invoke('finish-panel',panel='p1',attempt=attempt['attempt'],file=image,qa=f.json_file(report))

    def test_status_uses_only_effectively_accepted_panels_for_exhaustion(self):
        f=self.f;f.locked();f.reference()
        prompt=f.base/'prompt.txt';prompt.write_text('mechanical',encoding='utf-8')
        for number in (1,2):
            attempt=f.begin_one(panel='p1',prompt=prompt)
            self.assertEqual(number,attempt['attempt'])
            f.invoke('fail-panel',panel='p1',attempt=number,reason='mechanical closure',outcome='failed')
        attempt=f.begin_one(panel='p1',prompt=prompt)
        image=f.image_file()
        f.invoke('finish-panel',panel='p1',attempt=attempt['attempt'],file=image,
                 qa=f.panel_qa('p1',attempt['attempt'],image))
        self.assertEqual(1,f.invoke('status')['panels_accepted'])
        accepted=cp.project_load(f.root)['art']['panels']['p1'][-1]
        cp.inside(f.root,accepted['path']).write_bytes(b'corrupted after acceptance')
        status=f.invoke('status')
        self.assertEqual(0,status['panels_accepted'])
        self.assertEqual(1,status['preflight_counts']['panels_exhausted'])
        self.assertTrue(any('three attempts exhausted' in reason for reason in status['panel_blockers']['p1']))
        with self.assertRaisesRegex(cp.GateError,'Three attempts exhausted'):
            f.begin_one(panel='p1',prompt=prompt)

    def test_status_reports_malformed_script_art_source_and_style_fields(self):
        f=self.f;f.locked()
        baseline=cp.project_load(f.root)
        cases=[
            ('script',None,'script: expected an object'),
            ('art',{'references':None,'panels':None,'bindings':[]},'art.references: expected an array'),
            ('source',None,'source: expected an object'),
        ]
        for field,value,expected in cases:
            with self.subTest(field=field):
                project=copy.deepcopy(baseline);project[field]=value
                cp.atomic_json(f.root/'project.json',project)
                status=f.invoke('status')
                self.assertTrue(any(expected in item for item in status['blockers']),status['blockers'])
        project=copy.deepcopy(baseline);project['source']['issues']=['malformed issue']
        cp.atomic_json(f.root/'project.json',project)
        self.assertTrue(any('source.issues[0]' in item for item in f.invoke('status')['blockers']))
        project=copy.deepcopy(baseline);project['script']['style'].update(width=[],font_size={})
        cp.atomic_json(f.root/'project.json',project)
        status=f.invoke('status')
        self.assertTrue(any('script.style.width' in item for item in status['blockers']))
        self.assertTrue(any('script.style.font_size' in item for item in status['blockers']))

    def test_scene_and_prop_content_fingerprints_gate_art(self):
        f=self.f;script=f.prepare_script()
        path=f.root/'reference-scene.png'
        from shutil import copy2
        copy2(f.image_file(),path)
        script['settings'][0].update(reference_paths=[path.name],reference_hashes=[cp.sha_file(path)])
        script['props']=[{'id':'lens','description':'Test lens','reference_paths':[path.name],'reference_hashes':[cp.sha_file(path)]}]
        script['panels'][0]['prop_ids']=['lens']
        f.invoke('set-script',file=f.json_file(script));f.add_reviews();f.invoke('lock-script');f.accept_all()
        path.write_bytes(b'changed asset')
        status=f.invoke('status')
        self.assertFalse(status['script_locked']);self.assertEqual(0,status['panels_accepted'])
        self.assertTrue(any('content hash changed' in e for e in status['blockers']))

    def test_cancelled_attempts_count_toward_three_attempt_limit(self):
        f=self.f;f.locked();f.reference()
        prompt=f.base/'prompt.txt';prompt.write_text('mechanical',encoding='utf-8')
        for expected in (1,2,3):
            result=f.begin_one(panel='p1',prompt=prompt)
            self.assertEqual(expected,result['attempt'])
            f.invoke('fail-panel',panel='p1',attempt=expected,reason='Closed test attempt',outcome='cancelled')
        with self.assertRaises(cp.GateError):f.begin_one(panel='p1',prompt=prompt)
        self.assertIn('exhausted',' '.join(f.invoke('status')['panel_blockers']['p1']))

    def test_unequal_rows_keep_narrative_order_in_both_directions(self):
        from PIL import Image
        f=self.f
        for direction in ('ltr','rtl'):
            with self.subTest(direction=direction):
                script=f.mixed_script(direction)
                script['pages'][0]['row_weights']=[[1],[2,1],[1]]
                f.invoke('set-script',file=f.json_file(script));f.add_reviews();f.invoke('lock-script')
                f.accept_all({'p1':'#ca5362','p2':'#428d6b','p3':'#467cc2','p4':'#d3a348'})
                f.invoke('compose',font=None)
                page=cp.project_load(f.root)['layout']['pages'][0]
                with Image.open(cp.inside(f.root,page['path'])) as image:
                    b=f.color_bounds(image,'#428d6b');c=f.color_bounds(image,'#467cc2')
                self.assertAlmostEqual((b[2]-b[0])/(c[2]-c[0]),2,delta=.03)
                self.assertEqual(direction=='ltr',b[0]<c[0])
                self.assertEqual(['p1','p2','p3','p4'],page['panel_ids'])

    def test_bubbles_cannot_cover_declared_faces_or_reverse_reading_order(self):
        f=self.f;f.locked();f.accept_all()
        script=cp.project_load(f.root)['script'];panel=script['panels'][0]
        panel.update(lettering_mode='bubbles',protected_regions=[[.1,.1,.2,.2]],
                     bubbles=[{'dialogue_index':0,'rect':[.05,.05,.8,.3],'tail':None,'order':0}])
        f.invoke('set-script',file=f.json_file(script));f.add_reviews();f.invoke('lock-script')
        with self.assertRaisesRegex(cp.GateError,'protected_regions'):f.invoke('compose',font=None)
        panel['dialogue'].append(copy.deepcopy(panel['dialogue'][0]))
        panel['bubbles']=[{'dialogue_index':0,'rect':[.6,.05,.35,.3],'tail':None,'order':0},
                         {'dialogue_index':1,'rect':[.05,.05,.35,.3],'tail':None,'order':1}]
        f.invoke('set-script',file=f.json_file(script))
        self.assertTrue(any('horizontal reading order' in e for e in f.invoke('check-script')['errors']))

    def test_reference_regions_and_reviewed_targets_are_validated(self):
        f=self.f;f.locked()
        subjects=[{'character_id':'char-a','version_id':'base','region':[.9,0,.2,1]}]
        with self.assertRaisesRegex(cp.GateError,'region'):
            image=f.image_file()
            f.invoke('register-reference',file=image,qa=f.reference_qa(image,characters=['char-a']),
                     bindings=f.json_file({'purpose':'combined','subjects':subjects}))
        subjects[0]['region']=[0,0,.5,1]
        image=f.image_file()
        result=f.invoke('register-reference',file=image,qa=f.reference_qa(image,subjects=subjects),
                        bindings=f.json_file({'purpose':'combined','subjects':subjects}))
        self.assertEqual(subjects,cp.project_load(f.root)['art']['references'][-1]['subjects'])
        f.invoke('bind-panel',panel='p1',bindings=f.json_file({'appearance_versions':{'char-a':'base'},'reference_ids':[result['reference_id']]}))
        prompt=f.base/'prompt.txt';prompt.write_text('mechanical',encoding='utf-8')
        attempt=f.begin_one(panel='p1',prompt=prompt)
        with self.assertRaisesRegex(cp.GateError,'actual panel ID'):
            image=f.image_file()
            f.invoke('finish-panel',panel='p1',attempt=attempt['attempt'],file=image,
                     qa=f.panel_qa('p1',attempt['attempt'],image,['unrelated-panel']))


if __name__=='__main__':unittest.main()
