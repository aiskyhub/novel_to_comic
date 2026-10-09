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


    def test_six_generation_retries_allowed_on_render_failure(self):
        self.locked_page()
        for number in range(1, 7):
            result = self.begin()
            self.assertEqual(number, result['attempt'])
            self.f.invoke('fail-page', page='page01', attempt=number, reason='生图失败：API调用超时')
        with self.assertRaisesRegex(cp.GateError, 'Six image generation retries exhausted'):
            self.begin()
        self.assertEqual(6, self.f.invoke('status')['preflight_counts']['attempts_recorded'])

    def test_three_reviews_exhausted_fallback_selects_best_candidate_with_defect_explanation(self):
        self.locked_page()
        f = self.f
        images = [f.image_file('#112233'), f.image_file('#445566'), f.image_file('#778899')]
        r1 = self.begin()
        f.invoke('fail-page', page='page01', attempt=r1['attempt'], reason='人物发色不符', file=images[0])
        r2 = self.begin()
        f.invoke('fail-page', page='page01', attempt=r2['attempt'], reason='背景细节偏差', file=images[1])
        r3 = self.begin()
        f.invoke('fail-page', page='page01', attempt=r3['attempt'], reason='文字略微紧凑', file=images[2])

        with self.assertRaisesRegex(cp.GateError, 'Three whole-page attempts'):
            self.begin()

        raw_qa = cp.load_json(f.page_qa('page01', 2, images[1]))
        raw_qa['checks']['drawing_quality'] = False
        with self.assertRaisesRegex(cp.GateError, 'QA not passed'):
            f.invoke('finish-page', page='page01', attempt=2, file=images[1], qa=f.json_file(raw_qa))

        fallback_qa = copy.deepcopy(raw_qa)
        fallback_qa['defect_explanation'] = '经三次审图均未达完全标准，择优选择第2次尝试图片。缺陷说明：背景细节微小偏差，但人物与对白完全准确，予以兜底放行。'
        f.invoke('finish-page', page='page01', attempt=2, file=images[1], qa=f.json_file(fallback_qa))

        st = f.invoke('status')
        self.assertEqual(0, st['pages_accepted'])
        self.assertEqual(1, st['pages_flawed'])
        self.assertEqual(1, st['pages_settled'])
        self.assertIsNone(st['next_page'])
        f.invoke('prepare-pages')
        self.assertEqual(1, len(cp.project_load(f.root)['layout']['pages']))

    def test_six_generation_failures_allow_placeholder_page_creation_and_finish(self):
        self.locked_page()
        for number in range(1, 7):
            self.begin()
            self.f.invoke('fail-page', page='page01', attempt=number, reason='生图接口报错：504 Gateway Timeout')
        with self.assertRaisesRegex(cp.GateError, 'Six image generation retries exhausted'):
            self.begin()

        res = self.f.invoke('placeholder-page', page='page01', reason='生图服务器持续无响应，6次重试耗尽', finish=True)
        self.assertTrue(res['ok'])
        self.assertTrue(res['finished'])
        self.assertEqual('placeholder_pending', res['status'])
        self.assertTrue(Path(res['image']).is_file())
        self.assertTrue(Path(res['qa']).is_file())

        from PIL import Image
        with Image.open(res['image']) as img:
            self.assertEqual((1080, 2400), img.size)
            self.assertEqual('PNG', img.format)

        st = self.f.invoke('status')
        self.assertEqual(0, st['pages_accepted'])
        self.assertEqual(1, st['pages_placeholder'])
        self.assertEqual(1, st['pages_settled'])
        self.assertIsNone(st['next_page'])
        self.f.invoke('prepare-pages')
        self.assertEqual(1, len(cp.project_load(self.f.root)['layout']['pages']))

    def test_flawed_page_cannot_be_finished_before_exhausting_three_attempts(self):
        self.locked_page()
        f = self.f
        image = f.image_file('#112233')
        r1 = self.begin()
        raw_qa = cp.load_json(f.page_qa('page01', r1['attempt'], image))
        raw_qa['checks']['drawing_quality'] = False
        raw_qa['defect_explanation'] = '第一次生成即尝试使用缺陷说明兜底放行'
        with self.assertRaisesRegex(cp.GateError, 'Three whole-page attempts must be exhausted before fallback acceptance'):
            f.invoke('finish-page', page='page01', attempt=r1['attempt'], file=image, qa=f.json_file(raw_qa))

    def test_unresolved_critical_defect_blocks_page_finish(self):
        self.locked_page()
        f = self.f
        image = f.image_file('#112233')
        r1 = self.begin()
        raw_qa = cp.load_json(f.page_qa('page01', r1['attempt'], image))
        raw_qa['findings'] = [{
            'severity': 'critical',
            'description': '分镜严重缺格且角色脸部严重坍塌',
            'resolved': False
        }]
        raw_qa['defect_explanation'] = '严重缺陷但希望强行放行'
        with self.assertRaisesRegex(cp.GateError, 'unresolved critical defect'):
            f.invoke('finish-page', page='page01', attempt=r1['attempt'], file=image, qa=f.json_file(raw_qa))

    def test_placeholder_page_cannot_be_created_before_exhausting_six_failures(self):
        self.locked_page()
        # Only 5 generation failures
        for number in range(1, 6):
            self.begin()
            self.f.invoke('fail-page', page='page01', attempt=number, reason='生图报错超时')
        with self.assertRaisesRegex(cp.GateError, 'Six image generation retries must be recorded'):
            self.f.invoke('placeholder-page', page='page01', reason='提前创建占位图')

    def test_delivery_blocked_with_placeholder_unless_explicitly_authorized(self):
        self.locked_page()
        for number in range(1, 7):
            self.begin()
            self.f.invoke('fail-page', page='page01', attempt=number, reason='生图超时')
        self.f.invoke('placeholder-page', page='page01', reason='6次生图失败占位', finish=True)
        self.f.invoke('prepare-pages')
        self.f.review_and_export()

        # 1. Without allow_placeholders: complete is blocked
        final_qa = {
            'input_hash': cp.project_load(self.f.root)['layout']['input_hash'],
            'checks': {'source_scope': True, 'story_complete': True, 'visual_consistency': True, 'exports_opened': True},
            'evidence': '全部完成检查'
        }
        with self.assertRaisesRegex(cp.GateError, 'Final delivery blocked.*placeholder_pending'):
            self.f.invoke('complete', file=self.f.json_file(final_qa))

        # 2. With allow_placeholders: complete succeeds
        authorized_qa = copy.deepcopy(final_qa)
        authorized_qa['allow_placeholders'] = True
        res = self.f.invoke('complete', file=self.f.json_file(authorized_qa))
        self.assertTrue(res['ok'])
        self.assertTrue(self.f.invoke('status')['complete'])

    def test_delivery_blocked_with_accepted_flawed_unless_explicitly_authorized(self):
        self.locked_page()
        f = self.f
        images = [f.image_file('#112233'), f.image_file('#445566'), f.image_file('#778899')]
        for i in range(3):
            r = self.begin()
            f.invoke('fail-page', page='page01', attempt=r['attempt'], reason=f'瑕疵{i}', file=images[i])
        fallback_qa = cp.load_json(f.page_qa('page01', 3, images[2]))
        fallback_qa['checks']['drawing_quality'] = False
        fallback_qa['defect_explanation'] = '三次耗尽择优放行第3次'
        f.invoke('finish-page', page='page01', attempt=3, file=images[2], qa=f.json_file(fallback_qa))

        f.invoke('prepare-pages')
        f.review_and_export()

        # 1. Without allow_flawed: complete is blocked
        final_qa = {
            'input_hash': cp.project_load(f.root)['layout']['input_hash'],
            'checks': {'source_scope': True, 'story_complete': True, 'visual_consistency': True, 'exports_opened': True},
            'evidence': '最终核验'
        }
        with self.assertRaisesRegex(cp.GateError, 'Final delivery blocked.*accepted_flawed'):
            f.invoke('complete', file=f.json_file(final_qa))

        # 2. With allow_flawed: complete succeeds
        authorized_qa = copy.deepcopy(final_qa)
        authorized_qa['allow_flawed'] = True
        res = f.invoke('complete', file=f.json_file(authorized_qa))
        self.assertTrue(res['ok'])
        self.assertTrue(f.invoke('status')['complete'])


if __name__ == '__main__':
    unittest.main()
