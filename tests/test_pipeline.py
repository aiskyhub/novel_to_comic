"""Mechanical regression fixtures; these do not claim to validate fictional art quality."""
from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import comic_pipeline as cp
import comic_sources as cs
import comic_layout as cl


def fixture_script(source):
    script = cp.load_json(Path(cp.__file__).resolve().parents[1] / 'assets' / 'script-template.json')
    script.update(outline='测试夹具的全部事件顺序保留。', ending='以提供的最后一句结束。')
    script['style'].update(genre='测试', look='清晰线条', palette='灰色', selection_reason='自动化机械测试',
                           width=900, height=1200, font_size=28)
    script['characters'] = [{'id': 'char-a', 'name': '甲', 'aliases': [], 'importance': 'major',
        'design_tier': 'lead',
        'appearance': {'gender_presentation': 'masculine', 'requirements': '机械测试男性造型', 'source_unit_ids': []},
        'identity_card': {**{key: '机械身份锚点' for key in ('face', 'eyes_brows', 'nose_mouth', 'body', 'posture', 'temperament')}, 'invariants': ['测试脸型']},
        'appearance_versions': [{'id': 'base', 'description': '测试基础衣着', 'visual': {'costume': 'coat-a'}}],
        'comparison_with': [], 'distinctions': [],
        'narrative': {key: '测试人物' for key in ('goal', 'motivation', 'voice', 'arc')},
        'visual': {key: '测试设定' for key in ('face_shape', 'eyes', 'brows', 'nose_mouth', 'body', 'posture', 'hair', 'age')},
        'source_facts': [], 'design_notes': ['机械测试夹具，不代表实际人物图']}]
    script['settings'] = [{'id': 'room-a', 'description': '测试房间'}]
    body = [u for u in source['units'] if u['kind'] == 'body']
    for chapter in source['chapters']:
        if chapter['has_body']:
            script['scenes'].append({'id': 'scene-' + chapter['id'], 'chapter_id': chapter['id'], 'setting_id': 'room-a'})
    state = {'form': 'base', 'costume': 'coat-a', 'injuries': [], 'items': [], 'location': 'room-a', 'knowledge': []}
    for i, unit in enumerate(body, 1):
        event_id, panel_id = f'ev{i}', f'p{i}'
        script['events'].append({'id': event_id, 'description': unit['text'], 'source_unit_ids': [unit['id']]})
        script['panels'].append({'id': panel_id, 'chapter_id': unit['chapter_id'], 'scene_id': 'scene-' + unit['chapter_id'],
            'source_unit_ids': [unit['id']], 'event_ids': [event_id], 'cast': ['char-a'],
            'appearance_versions': {'char-a': 'base'},
            'action': unit['text'], 'shot': '中景', 'space': '人物位于测试房间中', 'expression': '平静',
            'state_before': {'char-a': copy.deepcopy(state)}, 'state_after': {'char-a': copy.deepcopy(state)},
            'dialogue': [{'kind': 'caption', 'text': unit['text']}]})
        script['pages'].append({'id': f'page{i}', 'chapter_id': unit['chapter_id'], 'panel_ids': [panel_id], 'columns': 1})
    return script


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='comic-tests-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / '中文作品'
        self.source = self.base / '原文.txt'
        self.source.write_text('第1章 来信\n甲拿起信封。\n第2章 回信\n甲写下回答。\n', encoding='utf-8')
        self.invoke('init', source=[str(self.source)], title='通用测试')

    def invoke(self, name, **kwargs):
        from argparse import Namespace
        kwargs.setdefault('scope', None)
        kwargs.setdefault('bindings', None)
        kwargs.setdefault('outcome', 'failed')
        return cp.run(Namespace(command=name, project=str(self.root), **kwargs))

    def json_file(self, value, stem='data'):
        import uuid
        path = self.base / (stem + '-' + uuid.uuid4().hex[:8] + '.json')
        cp.atomic_json(path, value)
        return str(path)

    def prepare_script(self):
        self.invoke('confirm-source', note='已检查夹具来源与边界')
        project = cp.project_load(self.root)
        for chapter in project['source']['chapters']:
            if chapter['has_body']:
                self.invoke('mark-read', chapter=chapter['id'], note='已读取夹具正文：' + chapter['title'])
        script = fixture_script(project['source'])
        self.invoke('set-script', file=self.json_file(script))
        return script

    def add_reviews(self):
        project = cp.project_load(self.root)
        for kind, checks in cp.REVIEW_CHECKS.items():
            report = {'script_hash': cp.digest(project['script']),
                'reviewed_chapter_ids': [c['id'] for c in project['source']['chapters'] if c['has_body']],
                'checks': {k: True for k in checks}, 'evidence': '自动化夹具报告，只测试结构与关卡。', 'issues': []}
            self.invoke('review', kind=kind, file=self.json_file(report))

    def locked(self):
        self.prepare_script()
        self.add_reviews()
        self.invoke('lock-script')

    def begin_one(self, panel, prompt):
        """Exercise the unified batch interface for existing single-frame scenarios."""
        plan = {'canvas_pixels': [1536, 1024], 'panels': [{'panel_id': panel, 'target_region': [0, 0, 1, 1],
                            'min_pixels': [1536, 1024]}]}
        result = self.invoke('begin-batch', plan=self.json_file(plan), prompt=str(prompt))
        if result['already_accepted']:
            return {'already_accepted': True, 'path': result['reused'][0]['path']}
        return {**result['panels'][0], 'batch_id': result['batch_id']}

    def image_file(self, color='white'):
        from PIL import Image
        import uuid
        path = self.base / ('mechanical-' + uuid.uuid4().hex[:8] + '.png')
        Image.new('RGB', (1536, 1024), color).save(path)
        return str(path)

    def qa(self, keys, reviewed_ids=None, image_sha256=None, reference_visual_key=None,
           attempt_bindings=None):
        report = {'checks': {k: True for k in keys},
                  'reviewed_ids': reviewed_ids or (['char-a'] if keys == cp.REFERENCE_CHECKS else [p['id'] for p in cp.project_load(self.root)['script']['panels']]),
                  'comparisons': [], 'findings': [],
                  'detail_notes': '机械夹具，只检查原生像素与报告字段，不宣称艺术细节达标。',
                  'elegance_notes': {k: '机械夹具，仅检查字段关卡，非真实美感结论。'
                                     for k in ('linework', 'color_and_light', 'visual_hierarchy')},
                  'evidence': '机械夹具，仅用于文件/流程回归，非真实图像验收。'}
        if image_sha256 is not None:
            report['image_sha256'] = image_sha256
        if reference_visual_key is not None:
            report['reference_visual_key'] = reference_visual_key
        if attempt_bindings is not None:
            report['attempt_bindings'] = attempt_bindings
        return self.json_file(report)

    def reference_qa(self, image_path, characters=None, subjects=None, purpose='combined'):
        if subjects is None:
            characters = characters or ['char-a']
            subjects = [{'character_id': cid, 'version_id': 'base', 'region': None} for cid in characters]
        inputs = self.invoke('qa-inputs', file=str(image_path),
                             bindings=self.json_file({'purpose': purpose, 'subjects': subjects}))
        return self.qa(cp.REFERENCE_CHECKS, inputs['reviewed_ids'], inputs['image_sha256'],
                       inputs['reference_visual_key'])

    def panel_qa(self, panel_id, attempt_number, image_path, reviewed_ids=None):
        attempt = next(a for a in cp.project_load(self.root)['art']['panels'][panel_id]
                       if a['number'] == attempt_number)
        batch = cp.project_load(self.root)['art']['batches'][attempt['batch_id']]
        if not batch['crops']:
            from PIL import Image
            with Image.open(image_path) as image:
                region = [0, 0, image.width, image.height]
            self.invoke('split-batch', batch=batch['id'], file=str(image_path),
                        regions=self.json_file({panel_id: region}))
        inputs = self.invoke('qa-inputs', panel=panel_id, attempt=attempt_number, file=str(image_path))
        return self.qa(cp.PANEL_CHECKS, reviewed_ids or inputs['reviewed_ids'], inputs['image_sha256'],
                       attempt_bindings=inputs['attempt_bindings'])

    def reference(self):
        image = self.image_file()
        self.invoke('register-reference', characters=['char-a'], file=image,
                    qa=self.reference_qa(image, characters=['char-a']))
        reference = cp.project_load(self.root)['art']['references'][-1]
        for panel in cp.project_load(self.root)['script']['panels']:
            if panel['id'] not in cp.project_load(self.root)['art'].get('bindings', {}):
                self.invoke('bind-panel', panel=panel['id'], bindings=self.json_file(
                    {'appearance_versions': {'char-a': 'base'}, 'reference_ids': [reference['id']]}))

    def accept_all(self, colors=None):
        self.reference()
        for panel in cp.project_load(self.root)['script']['panels']:
            prompt = self.base / 'prompt.txt'
            prompt.write_text('机械测试，不调用图像服务', encoding='utf-8')
            attempt = self.begin_one(panel=panel['id'], prompt=str(prompt))
            image = self.image_file((colors or {}).get(panel['id'], '#b8c9d2'))
            self.invoke('finish-panel', panel=panel['id'], attempt=attempt['attempt'],
                        file=image, qa=self.panel_qa(panel['id'], attempt['attempt'], image))

    def exported(self):
        self.locked()
        self.accept_all()
        self.invoke('compose', font=None)
        self.review_and_export()

    def review_and_export(self):
        layout = cp.project_load(self.root)['layout']
        report = {'input_hash': layout['input_hash'], 'reviewed_page_ids': [p['id'] for p in layout['pages']],
                  'elegance_notes': {k: '机械页面夹具，不代替真实看图。'
                                     for k in ('linework', 'color_and_light', 'visual_hierarchy')},
                  'checks': {k: True for k in cp.LAYOUT_CHECKS}, 'evidence': '机械页面夹具检查'}
        self.invoke('review-layout', file=self.json_file(report))
        self.invoke('export')

    def mixed_script(self, direction='ltr', max_height=6000, count=4):
        self.root = self.base / ('混合分格-' + direction)
        source = self.base / (direction + '.txt')
        source.write_text('第1章 场景\n甲进入房间。\n甲打开信封。\n甲读完信。\n甲走到窗前。\n'
                          + ''.join(f'甲观察第{i}件物品。\n' for i in range(5, count + 1)), encoding='utf-8')
        self.invoke('init', source=[str(source)], title='混合分格机械测试')
        script = self.prepare_script()
        script['style'].update(format='strip', reading_direction=direction, max_segment_height=max_height)
        for panel in script['panels']:
            panel['dialogue'] = []
        rows = [['p1'], ['p2', 'p3'], ['p4']]
        rows.extend([f'p{i}' for i in range(start, min(start + 2, count + 1))]
                    for start in range(5, count + 1, 2))
        script['pages'] = [{'id': 'page-mixed', 'chapter_id': script['panels'][0]['chapter_id'],
                            'panel_ids': [p['id'] for p in script['panels']], 'rows': rows}]
        self.invoke('set-script', file=self.json_file(script))
        return script

    def accepted_mixed(self, direction='ltr', max_height=6000):
        self.mixed_script(direction, max_height)
        self.add_reviews()
        self.invoke('lock-script')
        self.accept_all({'p1': '#ca5362', 'p2': '#428d6b', 'p3': '#467cc2', 'p4': '#d3a348'})

    def color_bounds(self, image, color):
        from PIL import Image, ImageColor
        mask = Image.new('1', image.size)
        rgb = ImageColor.getrgb(color)
        pixels = image.load()
        mask.putdata([pixels[x, y] == rgb for y in range(image.height) for x in range(image.width)])
        bounds = mask.getbbox()
        self.assertIsNotNone(bounds, 'Rendered illustration color missing: ' + color)
        return bounds

    def test_page_rows_reject_missing_reordered_duplicate_or_malformed_panels(self):
        script = self.mixed_script()
        invalid = [[], [[]], None, ['p1', 'p2', 'p3', 'p4'],
                   [['p1', 'p2', 'p3'], ['p4']], [['p2', 'p1'], ['p3', 'p4']],
                   [['p1'], ['p2', 'p3']], [['p1'], ['p2', 'p2'], ['p4']],
                   [['p1'], ['p2', 3], ['p4']]]
        for rows in invalid:
            with self.subTest(rows=rows):
                candidate = copy.deepcopy(script)
                candidate['pages'][0]['rows'] = rows
                self.invoke('set-script', file=self.json_file(candidate))
                self.assertTrue(any('Page rows' in error for error in self.invoke('check-script')['errors']))

    def test_mixed_rows_render_full_width_pairs_and_both_reading_directions(self):
        from PIL import Image
        for direction in ('ltr', 'rtl'):
            with self.subTest(direction=direction):
                self.accepted_mixed(direction)
                self.invoke('compose', font=None)
                layout = cp.project_load(self.root)['layout']
                self.assertEqual(1, len(layout['pages']))
                self.assertEqual(['p1', 'p2', 'p3', 'p4'], layout['pages'][0]['panel_ids'])
                with Image.open(cp.inside(self.root, layout['pages'][0]['path'])) as image:
                    a, b, c, d = [self.color_bounds(image, color) for color in
                                   ('#ca5362', '#428d6b', '#467cc2', '#d3a348')]
                self.assertEqual((a[0], a[2]), (d[0], d[2]))
                self.assertEqual(b[2] - b[0], c[2] - c[0])
                self.assertGreater(a[2] - a[0], b[2] - b[0])
                self.assertEqual(b[1], c[1])
                self.assertLess(a[3], b[1])
                self.assertLess(b[3], d[1])
                self.assertEqual(b[0] < c[0], direction == 'ltr')
                for bounds in (a, b, c, d):
                    self.assertAlmostEqual((bounds[2] - bounds[0]) / (bounds[3] - bounds[1]), 1.5, delta=0.02)
                self.review_and_export()
                self.assertEqual(1, self.invoke('verify-export')['page_count'])

    def test_mixed_rows_split_at_row_boundaries_within_segment_limit(self):
        self.accepted_mixed(max_height=1000)
        self.invoke('compose', font=None)
        pages = cp.project_load(self.root)['layout']['pages']
        self.assertEqual([['p1', 'p2', 'p3'], ['p4']], [page['panel_ids'] for page in pages])
        self.assertTrue(all(page['height'] <= 1000 for page in pages))
        self.review_and_export()
        self.assertEqual(2, self.invoke('verify-export')['page_count'])

    def test_columns_fallback_renders_last_single_panel_at_full_width(self):
        from PIL import Image
        script = self.mixed_script()
        script['pages'] = [{'id': 'page-grid', 'chapter_id': script['panels'][0]['chapter_id'],
                            'panel_ids': ['p1', 'p2', 'p3'], 'columns': 2},
                           {'id': 'page-last', 'chapter_id': script['panels'][0]['chapter_id'], 'panel_ids': ['p4']}]
        self.invoke('set-script', file=self.json_file(script))
        self.add_reviews()
        self.invoke('lock-script')
        self.accept_all({'p1': '#ca5362', 'p2': '#428d6b', 'p3': '#467cc2', 'p4': '#d3a348'})
        self.invoke('compose', font=None)
        pages = cp.project_load(self.root)['layout']['pages']
        with Image.open(cp.inside(self.root, pages[0]['path'])) as image:
            a, b, c = [self.color_bounds(image, color) for color in ('#ca5362', '#428d6b', '#467cc2')]
        self.assertEqual(a[1], b[1])
        self.assertLess(a[3], c[1])
        self.assertGreater(c[2] - c[0], a[2] - a[0])

    def test_visual_plan_change_invalidates_only_affected_painting(self):
        self.locked()
        self.accept_all()
        script = cp.project_load(self.root)['script']
        script['panels'][0]['visual_plan'] = {'focal_point': '信封', 'depth': '前景手部，中景人物，简化背景'}
        self.invoke('set-script', file=self.json_file(script))
        self.add_reviews()
        self.invoke('lock-script')
        status = self.invoke('status')
        self.assertEqual(1, status['panels_accepted'])
        self.assertEqual('p1', status['next_panel'])

    def test_layout_version_invalidates_exports_and_page_cache_but_reuses_art(self):
        self.exported()
        before = cp.project_load(self.root)['layout']
        with patch.object(cl, 'LAYOUT_VERSION', cl.LAYOUT_VERSION + 1):
            with self.assertRaises(cp.GateError):
                self.invoke('verify-export')
            self.assertEqual(2, self.invoke('status')['panels_accepted'])
            self.invoke('compose', font=None)
            after = cp.project_load(self.root)['layout']
            self.assertNotEqual(before['input_hash'], after['input_hash'])
            self.assertNotEqual(before['pages'][0]['path'], after['pages'][0]['path'])
            self.review_and_export()
            self.assertEqual(2, self.invoke('verify-export')['page_count'])

    def test_pre_art_gate_rejects_empty_and_partial_script(self):
        with self.assertRaises(cp.GateError):
            self.invoke('assert-art')
        script = self.prepare_script()
        script['panels'].pop()
        script['pages'].pop()
        self.invoke('set-script', file=self.json_file(script))
        self.assertTrue(any('Unmapped' in x or 'Chapter has no' in x for x in self.invoke('check-script')['errors']))
        with self.assertRaises(cp.GateError):
            self.invoke('lock-script')

    def test_three_current_reviews_required(self):
        self.prepare_script()
        project = cp.project_load(self.root)
        for kind in ('coverage', 'continuity'):
            report = {'script_hash': cp.digest(project['script']), 'reviewed_chapter_ids': ['ch000001', 'ch000002'],
                      'checks': {k: True for k in cp.REVIEW_CHECKS[kind]}, 'evidence': '机械审查测试'}
            self.invoke('review', kind=kind, file=self.json_file(report))
        with self.assertRaises(cp.GateError):
            self.invoke('lock-script')
        self.add_reviews()
        self.invoke('lock-script')
        self.assertTrue(self.invoke('assert-art')['allowed'])
        self.assertTrue((self.root / 'full-script.md').is_file())

    def test_source_modified_invalidates_lock(self):
        self.locked()
        self.source.write_text(self.source.read_text(encoding='utf-8') + '新增事件。', encoding='utf-8')
        self.assertTrue(self.invoke('status')['script_locked'])
        self.assertTrue(self.invoke('status')['external_source_warnings'])
        project = cp.project_load(self.root)
        archived = cp.inside(self.root, project['source']['files'][0]['archive_path'])
        archived.write_text(archived.read_text(encoding='utf-8') + '新增事件。', encoding='utf-8')
        with self.assertRaises(cp.GateError):
            self.invoke('assert-art')
        self.assertFalse(self.invoke('status')['script_locked'])

    def test_index_tampering_rejected(self):
        self.locked()
        project = cp.project_load(self.root)
        project['source']['units'][1]['text'] = '偷偷删改正文'
        cp.save(self.root, project)
        with self.assertRaises(cp.GateError):
            self.invoke('assert-art')

    def test_script_change_invalidates_old_reviews(self):
        self.locked()
        script = cp.project_load(self.root)['script']
        script['panels'][0]['action'] += '（新镜头）'
        self.invoke('set-script', file=self.json_file(script))
        with self.assertRaises(cp.GateError):
            self.invoke('lock-script')

    def test_duplicate_ids_and_wrong_speaker_rejected(self):
        script = self.prepare_script()
        script['panels'][1]['id'] = script['panels'][0]['id']
        script['panels'][0]['dialogue'] = [{'kind': 'speech', 'speaker': 'unknown', 'text': '你好'}]
        self.invoke('set-script', file=self.json_file(script))
        errors = self.invoke('check-script')['errors']
        self.assertTrue(any('duplicate' in x for x in errors))
        self.assertTrue(any('speaker' in x for x in errors))

    def test_state_transition_needs_source_evidence(self):
        script = self.prepare_script()
        script['panels'][1]['state_before']['char-a']['costume'] = 'coat-b'
        script['panels'][1]['state_after']['char-a']['costume'] = 'coat-b'
        self.invoke('set-script', file=self.json_file(script))
        self.assertTrue(any('state' in x and ('change' in x or 'transition' in x) for x in self.invoke('check-script')['errors']))
        script['panels'][1]['state_transitions'] = [{'character_id': 'char-a', 'fields': ['costume'],
                                                  'reason': '夹具换装', 'source_unit_ids': script['panels'][1]['source_unit_ids']}]
        self.invoke('set-script', file=self.json_file(script))
        self.assertEqual([], self.invoke('check-script')['errors'])

    def test_major_unresolved_review_rejected(self):
        self.prepare_script()
        project = cp.project_load(self.root)
        report = {'script_hash': cp.digest(project['script']), 'reviewed_chapter_ids': ['ch000001', 'ch000002'],
                  'checks': {k: True for k in cp.REVIEW_CHECKS['coverage']}, 'evidence': '发现遗漏',
                  'issues': [{'severity': 'major', 'resolved': False}]}
        with self.assertRaises(cp.GateError):
            self.invoke('review', kind='coverage', file=self.json_file(report))

    def test_attempt_limit_and_pending_no_double_generation(self):
        self.locked()
        self.reference()
        prompt = self.base / 'prompt.txt'
        prompt.write_text('机械测试', encoding='utf-8')
        for i in range(3):
            attempt = self.begin_one(panel='p1', prompt=str(prompt))
            with self.assertRaises(cp.GateError):
                self.begin_one(panel='p1', prompt=str(prompt))
            self.invoke('fail-panel', panel='p1', attempt=attempt['attempt'], reason='机械失败测试')
            prompt.write_text('微小提示词变动' + str(i), encoding='utf-8')
        with self.assertRaises(cp.GateError):
            self.begin_one(panel='p1', prompt=str(prompt))

    def test_reference_or_prompt_file_change_invalidates_art(self):
        self.locked()
        self.accept_all()
        self.assertEqual(2, self.invoke('status')['panels_accepted'])
        project = cp.project_load(self.root)
        path = cp.inside(self.root, project['art']['panels']['p1'][0]['prompt_path'])
        path.write_text('changed', encoding='utf-8')
        self.assertEqual(1, self.invoke('status')['panels_accepted'])
        reference = cp.inside(self.root, project['art']['references'][0]['path'])
        reference.write_bytes(b'changed')
        self.assertEqual(0, self.invoke('status')['panels_accepted'])

    def test_dialogue_change_reuses_paintings_but_requires_new_lock(self):
        self.locked()
        self.accept_all()
        script = cp.project_load(self.root)['script']
        script['panels'][0]['dialogue'][0]['text'] += '新增排版文字'
        self.invoke('set-script', file=self.json_file(script))
        with self.assertRaises(cp.GateError):
            self.invoke('assert-art')
        self.add_reviews()
        self.invoke('lock-script')
        self.assertEqual(2, self.invoke('status')['panels_accepted'])
        prompt = self.base / 'prompt.txt'
        prompt.write_text('此提示不会被调用', encoding='utf-8')
        self.assertTrue(self.begin_one(panel='p1', prompt=str(prompt))['already_accepted'])

    def test_exports_real_order_hashes_completion_and_missing_file(self):
        self.exported()
        self.assertEqual(2, self.invoke('verify-export')['page_count'])
        self.assertFalse(self.invoke('status')['complete'])
        project = cp.project_load(self.root)
        report = {'input_hash': project['layout']['input_hash'],
                  'checks': {k: True for k in ['source_scope', 'story_complete', 'visual_consistency', 'exports_opened']},
                  'evidence': '机械夹具验证，非真实漫画视觉测试'}
        self.invoke('complete', file=self.json_file(report))
        self.assertTrue(self.invoke('status')['complete'])
        pdf = next(x for x in project['exports']['files'] if x['kind'] == 'pdf')
        cp.inside(self.root, pdf['path']).unlink()
        self.assertFalse(self.invoke('status')['complete'])

    def test_path_escape_rejected(self):
        with self.assertRaises(cp.GateError):
            cp.inside(self.root, '../outside.png')

    def test_unicode_encodings_duplicate_headings_and_empty_chapter(self):
        for encoding in ('utf-16', 'gb18030', 'utf-8-sig'):
            file = self.base / (encoding + '.txt')
            file.write_bytes('前言\n说明文字。\n第1章 同号\n正文。\n第1章 同号\n下一段。\n第2章 空章\n'.encode(encoding))
            source = cs.extract([file])
            self.assertEqual(4, len(source['chapters']))
            self.assertEqual(4, len({c['id'] for c in source['chapters']}))
            self.assertFalse(source['chapters'][-1]['has_body'])
            self.assertEqual(1, len(source['issues']))

    def test_docx_table_and_epub_spine_order(self):
        from docx import Document
        doc = Document()
        doc.add_paragraph('第1章 正文')
        doc.add_paragraph('文本。')
        doc.add_table(rows=1, cols=1).cell(0, 0).text = '表内书信。'
        path = self.base / '正文.docx'
        doc.save(path)
        self.assertIn('表内书信。', [u['text'] for u in cs.extract([path])['units']])
        path = self.base / '正文.epub'
        with zipfile.ZipFile(path, 'w') as book:
            book.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
            book.writestr('OPS/book.opf', '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>'
                '</manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
            book.writestr('OPS/a.xhtml', '<html><body><p>后段。</p></body></html>')
            book.writestr('OPS/b.xhtml', '<html><body><p>先段。</p><script>隐藏内容</script></body></html>')
        self.assertEqual(['先段。', '后段。'], [u['text'] for u in cs.extract([path])['units']])

    def test_pdf_unreadable_page_flagged(self):
        from reportlab.pdfgen import canvas
        path = self.base / '空白页.pdf'
        pdf = canvas.Canvas(str(path))
        pdf.drawString(30, 700, 'Chapter 1')
        pdf.drawString(30, 680, 'Story text.')
        pdf.showPage()
        pdf.showPage()
        pdf.save()
        source = cs.extract([path])
        self.assertTrue(any(i.get('locator', {}).get('page') == 2 for i in source['issues']))

    def test_missing_font_glyph_stops_layout(self):
        self.locked()
        self.accept_all()
        script = cp.project_load(self.root)['script']
        script['panels'][0]['dialogue'][0]['text'] = '𐐷'
        self.invoke('set-script', file=self.json_file(script))
        self.add_reviews()
        self.invoke('lock-script')
        with self.assertRaises(cp.GateError):
            self.invoke('compose', font=None)


    def test_book_and_volume_hierarchy(self):
        book_dir = self.base / '神作小说'
        vol1_dir = book_dir / '第1卷'
        from argparse import Namespace
        init_res = cp.run(Namespace(command='init', project=str(vol1_dir),
                                    source=[str(self.source)], title='神作小说', volume='第1卷'))
        self.assertEqual(init_res['title'], '神作小说')
        self.assertEqual(init_res['volume'], '第1卷')
        loaded = cp.project_load(vol1_dir)
        self.assertEqual(loaded['title'], '神作小说')
        self.assertEqual(loaded['volume'], '第1卷')

        status_res = cp.run(Namespace(command='status', project=str(vol1_dir), plan=None))
        self.assertEqual(status_res['title'], '神作小说')
        self.assertEqual(status_res['volume'], '第1卷')

        # 校验剧本锁定后的 markdown 标题
        cp.run(Namespace(command='confirm-source', project=str(vol1_dir), note='来源确认', scope=None))
        for ch in loaded['source']['chapters']:
            if ch['has_body']:
                cp.run(Namespace(command='mark-read', project=str(vol1_dir), chapter=ch['id'], note='读完'))
        script = fixture_script(loaded['source'])
        script_file = self.json_file(script)
        cp.run(Namespace(command='set-script', project=str(vol1_dir), file=script_file))
        updated = cp.project_load(vol1_dir)
        for kind, checks in cp.REVIEW_CHECKS.items():
            report = {'script_hash': cp.digest(updated['script']),
                      'reviewed_chapter_ids': [c['id'] for c in updated['source']['chapters'] if c['has_body']],
                      'checks': {k: True for k in checks}, 'evidence': '测试证据', 'issues': []}
            cp.run(Namespace(command='review', project=str(vol1_dir), kind=kind, file=self.json_file(report)))
        cp.run(Namespace(command='lock-script', project=str(vol1_dir)))
        full_md = (vol1_dir / 'full-script.md').read_text(encoding='utf-8')
        self.assertIn('# 神作小说 · 第1卷 · 本卷漫画分镜剧本', full_md)

    def test_init_book_and_split_source(self):
        from argparse import Namespace
        book_dir = self.base / '星辰变漫改'
        raw_novel = self.base / '星辰变全书.txt'
        raw_novel.write_text(
            "第1卷 潜龙在渊\n第1章 异宝流星泪\n秦羽仰望星空。\n第2章 苦修\n少年挥汗如雨。\n"
            "第2卷 暴乱星海\n第1章 初入凶域\n波涛汹涌，海怪盘踞。\n",
            encoding='utf-8'
        )

        # 1. 运行 init-book，验证小说被移动到 source_texts，创建 split_texts、docs 及 README.md
        init_res = cp.run(Namespace(
            command='init-book',
            book_dir=str(book_dir),
            title='星辰变',
            source=[str(raw_novel)],
            action='move',
            description='热血修真史诗'
        ))
        self.assertTrue(init_res['ok'])
        self.assertEqual(init_res['title'], '星辰变')
        self.assertFalse(raw_novel.exists(), "原小说文件应该已被移动")
        archived_novel = book_dir / 'source_texts' / '星辰变全书.txt'
        self.assertTrue(archived_novel.is_file(), "原稿应存在于 source_texts/")
        self.assertTrue((book_dir / 'split_texts').is_dir(), "split_texts/ 目录应已创建")
        self.assertTrue((book_dir / 'docs').is_dir(), "docs/ 目录应已创建")
        for doc_name in ('overview.md', 'structure.md', 'worldview.md', 'characters.md', 'art_direction.md', 'progress.md'):
            self.assertTrue((book_dir / 'docs' / doc_name).is_file(), f"docs/{doc_name} 应存在")

        readme_text = (book_dir / 'README.md').read_text(encoding='utf-8')
        self.assertIn("# 《星辰变》漫画项目", readme_text)
        self.assertIn("全书分卷制作总览看板", readme_text)
        self.assertIn("怎么做：漫改全流程标准作业程序 (SOP)", readme_text)
        self.assertIn("各种规范引导文件在哪", readme_text)
        self.assertIn("原著真实性绝对铁律", readme_text)
        self.assertIn("docs/overview.md", readme_text)
        self.assertIn("docs/structure.md", readme_text)
        self.assertIn("docs/worldview.md", readme_text)
        self.assertIn("docs/characters.md", readme_text)
        self.assertIn("docs/art_direction.md", readme_text)
        self.assertIn("docs/progress.md", readme_text)
        self.assertIn("source_texts/", readme_text)
        self.assertIn("split_texts/", readme_text)
        self.assertIn("原著真实性原则", (book_dir / 'docs' / 'overview.md').read_text(encoding='utf-8'))

        # 2. 运行 split-source，验证切分结果统一存入 split_texts/
        split_res = cp.run(Namespace(
            command='split-source',
            book_dir=str(book_dir),
            file='星辰变全书.txt',
            output_dir=None,
            pattern=None
        ))
        self.assertTrue(split_res['ok'])
        self.assertEqual(split_res['segments_count'], 2)
        vol1_file = book_dir / 'split_texts' / 'vol_001_第1卷 潜龙在渊.txt'
        vol2_file = book_dir / 'split_texts' / 'vol_002_第2卷 暴乱星海.txt'
        self.assertTrue(vol1_file.is_file())
        self.assertTrue(vol2_file.is_file())
        self.assertTrue((book_dir / 'split_texts' / 'split_manifest.json').is_file())
        self.assertIn("秦羽仰望星空", vol1_file.read_text(encoding='utf-8'))
        self.assertIn("海怪盘踞", vol2_file.read_text(encoding='utf-8'))

        # 3. 验证以第1卷切割文本初始化该卷独立制作工作区，并验证生成该卷 docs 模块与精简 README.md
        vol1_project = book_dir / '第1卷'
        vol1_init = cp.run(Namespace(
            command='init',
            project=str(vol1_project),
            source=[str(vol1_file)],
            title='星辰变',
            volume='第1卷'
        ))
        self.assertEqual(vol1_init['title'], '星辰变')
        self.assertEqual(vol1_init['volume'], '第1卷')
        self.assertTrue((vol1_project / 'project.json').is_file())

        # 验证分卷 docs 目录及 6 个模块化文件
        vol1_docs = vol1_project / 'docs'
        self.assertTrue(vol1_docs.is_dir(), "分卷目录下应建立 docs/ 模块文件夹")
        for doc_file in ('info.md', 'status.md', 'commands.md', 'structure.md', 'notes.md', 'deliverables.md'):
            self.assertTrue((vol1_docs / doc_file).is_file(), f"分卷 docs/{doc_file} 应存在")

        self.assertIn("《星辰变》· 第1卷 基本信息", (vol1_docs / 'info.md').read_text(encoding='utf-8'))
        self.assertIn("原著真实性原则", (vol1_docs / 'info.md').read_text(encoding='utf-8'))
        self.assertIn("制作状态看板", (vol1_docs / 'status.md').read_text(encoding='utf-8'))
        self.assertIn("常用操作命令速查", (vol1_docs / 'commands.md').read_text(encoding='utf-8'))
        self.assertIn("工程目录结构", (vol1_docs / 'structure.md').read_text(encoding='utf-8'))
        self.assertIn("关键注记与特别要求", (vol1_docs / 'notes.md').read_text(encoding='utf-8'))
        self.assertIn("导出品交付路径", (vol1_docs / 'deliverables.md').read_text(encoding='utf-8'))

        # 验证精简的卷根目录 README.md（作为索引与导航）
        vol1_readme = vol1_project / 'README.md'
        self.assertTrue(vol1_readme.is_file(), "分卷目录下应自动生成 README.md")
        vol1_readme_text = vol1_readme.read_text(encoding='utf-8')
        self.assertIn("《星辰变》· 第1卷 漫画制作工程", vol1_readme_text)
        self.assertIn("原著真实性绝对原则", vol1_readme_text)
        self.assertIn("本卷关键状态速览", vol1_readme_text)
        self.assertIn("本卷模块化文档导航", vol1_readme_text)
        self.assertIn("docs/info.md", vol1_readme_text)
        self.assertIn("docs/status.md", vol1_readme_text)
        self.assertIn("docs/commands.md", vol1_readme_text)
        self.assertIn("docs/structure.md", vol1_readme_text)
        self.assertIn("docs/notes.md", vol1_readme_text)
        self.assertIn("docs/deliverables.md", vol1_readme_text)


if __name__ == '__main__':
    unittest.main()
