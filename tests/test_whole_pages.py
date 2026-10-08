"""Whole-page production regressions with synthetic PNGs; never artistic QA evidence."""
import copy
import subprocess
import sys
import unittest
from pathlib import Path
from PIL import Image

import test_pipeline as fixtures
from comic_pages import build_page_prompt

cp, cl = fixtures.cp, fixtures.cl


class WholePageTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def locked_page(self):
        f = self.f
        f.root = f.base / '整页项目'
        source = f.base / '整页原文.txt'
        source.write_text('第1章 场景\n甲进入房间。\n甲打开信封。\n甲读完信。\n', encoding='utf-8')
        f.invoke('init', source=[str(source)], title='整页机械夹具')
        script = f.prepare_script()
        script['pages'] = [{'id': 'page01', 'chapter_id': 'ch000001',
                            'panel_ids': ['p1', 'p2', 'p3'], 'rows': [['p1'], ['p2'], ['p3']], 'columns': 1}]
        f.invoke('set-script', file=f.json_file(script))
        f.add_reviews()
        f.invoke('lock-script')
        f.reference()

    def begin(self, text=None):
        f = self.f
        path = f.base / '整页提示词.txt'
        if text is None:
            text = f.invoke('build-prompt', page='page01', output=None)['prompt']
        path.write_text(text, encoding='utf-8')
        return f.invoke('begin-page', page='page01', prompt=str(path))

    def test_one_attempt_contains_three_panels_and_no_independent_image_ledgers(self):
        self.locked_page()
        result = self.begin()
        self.assertEqual(1, result['generation_calls'])
        project = cp.project_load(self.f.root)
        self.assertEqual({'references', 'pages'}, set(project['art']))
        self.assertEqual(1, len(project['art']['pages']['page01']))
        prompt = Path(result['prompt']).read_text(encoding='utf-8')
        for panel in project['script']['panels']:
            self.assertIn(panel['action'], prompt)
            for dialogue in panel['dialogue']:
                self.assertIn(dialogue['text'], prompt)
        self.assertEqual(3, len(project['script']['pages'][0]['panel_ids']))

    def test_pending_and_three_attempt_limit_prevent_duplicate_generation(self):
        self.locked_page()
        result = self.begin()
        with self.assertRaisesRegex(cp.GateError, 'unfinished attempt'):
            self.begin()
        for number in (1, 2, 3):
            if number > 1:
                result = self.begin()
            self.f.invoke('fail-page', page='page01', attempt=result['attempt'], reason='机械失败')
        with self.assertRaisesRegex(cp.GateError, 'Three whole-page attempts'):
            self.begin()
        self.assertEqual(3, self.f.invoke('status')['preflight_counts']['attempts_recorded'])

    def test_missing_locked_dialogue_is_rejected_before_attempt_registration(self):
        self.locked_page()
        with self.assertRaisesRegex(cp.GateError, 'omits exact locked dialogue'):
            self.begin('一张漫画页，但未列出原著台词。')
        self.assertEqual({}, cp.project_load(self.f.root)['art']['pages'])

    def test_native_whole_page_bytes_are_preserved_through_archival_and_export(self):
        self.locked_page()
        f = self.f
        result = self.begin()
        raw = f.image_file('#b8c9d2')
        f.invoke('finish-page', page='page01', attempt=result['attempt'], file=raw,
                 qa=f.page_qa('page01', result['attempt'], raw))
        self.assertFalse(self.begin()['generation_required'])
        f.invoke('prepare-pages')
        page = cp.project_load(f.root)['layout']['pages'][0]
        self.assertEqual(Path(raw).read_bytes(), cp.inside(f.root, page['path']).read_bytes())
        self.assertEqual((1080, 2400), (page['width'], page['height']))
        for preview in page['phone_previews']:
            with Image.open(cp.inside(f.root, preview['path'])) as image:
                self.assertEqual((preview['width'], round(2400 * preview['width'] / 1080)), image.size)
        f.review_and_export()
        self.assertTrue(f.invoke('verify-export')['ok'])
        project = cp.project_load(f.root)
        final = {'input_hash': project['layout']['input_hash'],
                 'checks': {k: True for k in ('source_scope','story_complete','visual_consistency','exports_opened')},
                 'evidence': '机械导出夹具，不声明艺术验收。'}
        f.invoke('complete', file=f.json_file(final))
        self.assertTrue(f.invoke('status')['complete'])
        Image.new('RGB', (1080, 2400), 'magenta').save(cp.inside(f.root, page['path']))
        with self.assertRaisesRegex(cp.GateError, 'Whole page missing or changed'):
            f.invoke('verify-export')
        f.invoke('prepare-pages')
        self.assertEqual(Path(raw).read_bytes(), cp.inside(f.root, page['path']).read_bytes())

    def test_mobile_review_rejects_unreadable_text_and_missing_page_observations(self):
        self.locked_page()
        f = self.f
        result = self.begin()
        image = f.image_file()
        report = cp.load_json(f.page_qa('page01', result['attempt'], image))
        for minimum in (0, -1, True, None):
            candidate = copy.deepcopy(report)
            candidate['phone_reading_notes'][0]['min_body_css_px'] = minimum
            with self.assertRaises(cp.GateError):
                f.invoke('finish-page', page='page01', attempt=result['attempt'], file=image, qa=f.json_file(candidate))
        missing = copy.deepcopy(report)
        del missing['phone_reading_notes']
        with self.assertRaisesRegex(cp.GateError, 'phone_reading_notes'):
            f.invoke('finish-page', page='page01', attempt=result['attempt'], file=image, qa=f.json_file(missing))
        accepted_report = copy.deepcopy(report)
        accepted_report['phone_reading_notes'][0]['min_body_css_px'] = 14
        f.invoke('finish-page', page='page01', attempt=result['attempt'], file=image, qa=f.json_file(accepted_report))

    def test_actual_short_page_is_rejected_without_stretch_or_padding(self):
        self.locked_page()
        result = self.begin()
        image = self.f.base / 'short.png'
        Image.new('RGB', (1080, 1440), 'white').save(image)
        with self.assertRaisesRegex(cp.GateError, 'height/width'):
            self.f.invoke('qa-inputs', page='page01', attempt=result['attempt'], file=str(image))
        with Image.open(image) as source:
            self.assertEqual((1080,1440),source.size)

    def test_phone_preview_tampering_is_rejected_and_regenerated_without_art_calls(self):
        f=self.f
        f.exported()
        project=cp.project_load(f.root)
        preview=cp.inside(f.root,project['layout']['pages'][0]['phone_previews'][0]['path'])
        preview.write_bytes(preview.read_bytes()+b'tamper')
        with self.assertRaisesRegex(cp.GateError,'Phone preview missing/changed'):
            f.invoke('verify-export')
        before=f.invoke('status')['preflight_counts']['attempts_recorded']
        f.invoke('prepare-pages');f.review_and_export()
        self.assertEqual(before,f.invoke('status')['preflight_counts']['attempts_recorded'])
        self.assertTrue(f.invoke('verify-export')['ok'])

    def test_stale_page_can_be_closed_without_a_lock_and_failed_output_is_archived(self):
        self.locked_page()
        f=self.f
        result=self.begin()
        script=cp.project_load(f.root)['script']
        script['panels'][0]['action']+='动作修订'
        f.invoke('set-script',file=f.json_file(script))
        image=f.image_file()
        with self.assertRaises(cp.GateError):
            f.invoke('qa-inputs',page='page01',attempt=result['attempt'],file=image)
        f.invoke('fail-page',page='page01',attempt=result['attempt'],reason='输入过期',outcome='stale',file=image)
        attempt=cp.project_load(f.root)['art']['pages']['page01'][0]
        self.assertEqual('stale',attempt['status'])
        self.assertEqual(Path(image).read_bytes(),cp.inside(f.root,attempt['path']).read_bytes())

    def test_page_dialogue_and_reference_bytes_invalidate_accepted_native_page(self):
        self.f.locked()
        self.f.accept_all()
        self.assertEqual(2,self.f.invoke('status')['pages_accepted'])
        script = cp.project_load(self.f.root)['script']
        script['panels'][0]['dialogue'][0]['text'] += '修订文字'
        self.f.invoke('set-script',file=self.f.json_file(script))
        self.f.add_reviews(); self.f.invoke('lock-script')
        self.assertEqual(1,self.f.invoke('status')['pages_accepted'])
        project = cp.project_load(self.f.root)
        path = cp.inside(self.f.root,project['art']['references'][0]['path'])
        path.write_bytes(path.read_bytes()+b'changed')
        self.assertEqual(0,self.f.invoke('status')['pages_accepted'])

    def test_registered_prompt_and_attempt_hashes_cannot_be_replaced(self):
        self.locked_page()
        result = self.begin()
        prompt = Path(result['prompt'])
        prompt.write_text(prompt.read_text(encoding='utf-8')+'tamper',encoding='utf-8')
        with self.assertRaisesRegex(cp.GateError,'prompt changed'):
            self.f.invoke('qa-inputs',page='page01',attempt=1,file=self.f.image_file())

    def test_old_generation_commands_and_old_schema_are_rejected_without_aliases(self):
        for command in ('begin-batch','split-batch','bind-panel','finish-panel','fail-panel','compose'):
            result = subprocess.run([sys.executable,'-B',str(Path(cp.__file__)),command,'--project',str(self.f.root)],
                                    capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(2,result.returncode)
            self.assertIn('invalid choice',result.stderr)
        project = cp.project_load(self.f.root)
        project['schema_version']=5
        cp.atomic_json(self.f.root/'project.json',project)
        with self.assertRaisesRegex(cp.GateError,'only schema v6'):
            self.f.invoke('status')

    def test_script_gate_relaxed_font_size_and_rejects_invalid_canvas_and_grid(self):
        self.locked_page()
        baseline = cp.project_load(self.f.root)['script']
        script_relaxed = copy.deepcopy(baseline)
        script_relaxed['style']['font_size'] = 47
        self.f.invoke('set-script', file=self.f.json_file(script_relaxed))
        self.assertEqual([], self.f.invoke('check-script')['errors'])
        for change,needle in ((lambda s:s['style'].update(font_size=-1),'positive integer'),
                              (lambda s:s['style'].update(height=1440),'height/width'),
                              (lambda s:s['pages'][0].update(columns=2),'columns must be 1')):
            script=copy.deepcopy(baseline);change(script)
            self.f.invoke('set-script',file=self.f.json_file(script))
            self.assertTrue(any(needle in e for e in self.f.invoke('check-script')['errors']))


if __name__ == '__main__':
    unittest.main()
