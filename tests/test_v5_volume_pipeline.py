"""Unit and regression tests for Schema v5 volume-scoped comic pipeline features."""
from __future__ import annotations

import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import comic_pipeline as cp
import comic_sources as cs
import comic_layout as cl
import comic_batches as cb
from PIL import Image
from adaptation_fixtures import attach_adaptations


def make_dummy_png(path: Path, width: int = 100, height: int = 100, color=(200, 200, 200)):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new('RGB', (width, height), color)
    img.save(str(path))
    return path


def fixture_v5_script(source):
    template_path = Path(cp.__file__).resolve().parents[1] / 'assets' / 'script-template.json'
    script = cp.load_json(template_path)
    script.update(outline='测试夹具的全部事件顺序保留。', ending='以提供的最后一句结束。')
    script['style'].update(genre='奇幻', look='精致优雅', palette='清透自然',
                           selection_reason='自动化机械测试', width=900, height=1200, font_size=28)
    script['characters'] = [{
        'id': 'char-a', 'name': '甲', 'aliases': [], 'importance': 'major',
        'design_tier': 'lead',
        'appearance': {'gender_presentation': 'masculine', 'requirements': '机械测试男性造型', 'source_unit_ids': []},
        'identity_card': {**{key: '机械身份锚点' for key in ('face', 'eyes_brows', 'nose_mouth', 'body', 'posture', 'temperament')}, 'invariants': ['测试脸型']},
        'appearance_versions': [{'id': 'base', 'description': '测试基础衣着', 'visual': {'costume': 'coat-a'}}],
        'comparison_with': [], 'distinctions': [],
        'narrative': {key: '测试人物' for key in ('goal', 'motivation', 'voice', 'arc')},
        'visual': {key: '测试设定' for key in ('face_shape', 'eyes', 'brows', 'nose_mouth', 'body', 'posture', 'hair', 'age')},
        'source_facts': [], 'design_notes': ['机械测试夹具，不代表实际人物图']
    }]
    script['settings'] = [{'id': 'room-a', 'description': '测试房间'}]
    body = [u for u in source['units'] if u['kind'] == 'body']
    for chapter in source['chapters']:
        if chapter['has_body']:
            script['scenes'].append({'id': 'scene-' + chapter['id'], 'chapter_id': chapter['id'], 'setting_id': 'room-a'})
    state = {'form': 'base', 'costume': 'coat-a', 'injuries': [], 'items': [], 'location': 'room-a', 'knowledge': []}
    for i, unit in enumerate(body, 1):
        event_id, panel_id = f'ev{i}', f'p{i}'
        script['events'].append({
            'id': event_id, 'description': unit['text'], 'source_unit_ids': [unit['id']],
            'narrative_role': 'critical_turning_point'
        })
        script['panels'].append({
            'id': panel_id, 'chapter_id': unit['chapter_id'], 'scene_id': 'scene-' + unit['chapter_id'],
            'source_unit_ids': [unit['id']], 'event_ids': [event_id], 'cast': ['char-a'],
            'appearance_versions': {'char-a': 'base'},
            'action': unit['text'], 'shot': '中景', 'space': '人物位于测试房间中', 'expression': '平静',
            'state_before': {'char-a': copy.deepcopy(state)}, 'state_after': {'char-a': copy.deepcopy(state)},
            'dialogue': [{'kind': 'caption', 'text': unit['text']}]
        })
        script['pages'].append({'id': f'page{i}', 'chapter_id': unit['chapter_id'], 'panel_ids': [panel_id], 'columns': 1})
    script['continuity_handover'] = {
        'opening_state': {'characters': {'char-a': {'location': 'room-a'}}, 'world_state': {}, 'open_mysteries': []},
        'closing_state': {'characters': {'char-a': {'location': 'room-a'}}, 'world_state': {}, 'open_mysteries': []}
    }
    return script


class VolumeV5PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        # Create a sample two-volume source manuscript with chapters and body text
        self.source_text = (
            "第1卷 起源之章\n"
            "第1章 初入祖地\n"
            "甲拿起信封。\n"
            "第2章 石壁传承\n"
            "甲写下回答。\n"
            "\n"
            "第2卷 征战之章\n"
            "第3章 远赴荒原\n"
            "乙走出大门。\n"
        )
        self.raw_file = self.base / '原稿.txt'
        self.raw_file.write_text(self.source_text, encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_volume1_complete_while_volume2_uninitialized(self):
        """Volume 1 can be locked, drawn, composed, and delivered without waiting for Volume 2."""
        book_dir = self.base / '星云纪'
        # 1. init-book and split
        cp.init_book(book_dir, title='星云纪', sources=[str(self.raw_file)], action='copy')
        split_res = cp.split_source(book_dir, book_dir / 'source_texts' / '原稿.txt')
        self.assertEqual(split_res['segments_count'], 2)
        vol1_src = book_dir / 'split_texts' / 'vol_001_第1卷 起源之章.txt'
        self.assertTrue(vol1_src.is_file())

        vol1_dir = book_dir / '第1卷'
        vol2_dir = book_dir / '第2卷'
        self.assertFalse(vol2_dir.exists())

        # 2. init Volume 1
        cp.run(Namespace(command='init', project=str(vol1_dir), source=[str(vol1_src)],
                         title='星云纪', volume='第1卷'))
        self.assertTrue((vol1_dir / 'project.json').is_file())
        self.assertFalse(vol2_dir.exists())

        # 3. read & confirm
        proj = cp.project_load(vol1_dir)
        for issue in proj['source'].get('issues', []):
            cp.run(Namespace(command='resolve-issue', project=str(vol1_dir), id=issue['id'], evidence='已确认测试夹具原文无误'))
        for ch in proj['source']['chapters']:
            if ch.get('has_body'):
                cp.run(Namespace(command='mark-read', project=str(vol1_dir), chapter=ch['id'], note='完成正文阅读'))
        cp.run(Namespace(command='confirm-source', project=str(vol1_dir), note='全卷阅读确认', scope=None))

        # 4. create script with continuity_handover
        proj = cp.project_load(vol1_dir)
        script = fixture_v5_script(proj['source'])
        attach_adaptations(vol1_dir, proj['source'], script)
        script_file = self.base / 'v1_script.json'
        script_file.write_text(json.dumps(script, ensure_ascii=False), encoding='utf-8')
        cp.run(Namespace(command='set-script', project=str(vol1_dir), file=str(script_file)))
        proj = cp.project_load(vol1_dir)

        # 5. review & lock
        for kind, checks in cp.REVIEW_CHECKS.items():
            rep = {'script_hash': cp.digest(proj['script']),
                   'reviewed_chapter_ids': [c['id'] for c in proj['source']['chapters'] if c['has_body']],
                   'checks': {k: True for k in checks}, 'evidence': f'{kind} 审查通过', 'issues': []}
            rep_file = self.base / f"{kind}.json"
            rep_file.write_text(json.dumps(rep, ensure_ascii=False), encoding='utf-8')
            cp.run(Namespace(command='review', project=str(vol1_dir), kind=kind, file=str(rep_file)))

        lock_res = cp.run(Namespace(command='lock-script', project=str(vol1_dir)))
        self.assertTrue(lock_res['ok'])
        self.assertTrue((vol1_dir / 'full-script.md').is_file())

        # 6. assert-art & register-reference
        cp.run(Namespace(command='assert-art', project=str(vol1_dir)))
        ref_png = vol1_dir / 'art' / 'raw' / 'ref_a.png'
        make_dummy_png(ref_png, 800, 1000)
        proj = cp.project_load(vol1_dir)
        d_hash = cp.design_hash(proj, ['char-a'])
        inputs = cp.run(Namespace(command='qa-inputs', project=str(vol1_dir), file=str(ref_png),
                                  characters=['char-a'], reference=None, panel=None, attempt=None, bindings=None))
        qa_rep = {
            'image_sha256': cp.sha_file(ref_png), 'design_hash': d_hash,
            'reference_visual_key': inputs['reference_visual_key'],
            'checks': {k: True for k in cp.REFERENCE_CHECKS},
            'reviewed_ids': ['char-a'],
            'comparisons': [], 'findings': [],
            'evidence': '基准图检验合格',
            'detail_notes': '细节完整，符合设定',
            'elegance_notes': {'linework': '线条利落', 'color_and_light': '光影通透', 'visual_hierarchy': '主体突出'}
        }
        qa_file = self.base / 'ref_qa.json'
        qa_file.write_text(json.dumps(qa_rep, ensure_ascii=False), encoding='utf-8')
        cp.run(Namespace(command='register-reference', project=str(vol1_dir), file=str(ref_png),
                         qa=str(qa_file), characters=['char-a']))

        # Bind panel to reference
        ref_id = cp.project_load(vol1_dir)['art']['references'][-1]['id']
        for p in script['panels']:
            bind_data = {'appearance_versions': {'char-a': 'base'}, 'reference_ids': [ref_id]}
            b_file = self.base / f"{p['id']}_bind.json"
            b_file.write_text(json.dumps(bind_data), encoding='utf-8')
            cp.run(Namespace(command='bind-panel', project=str(vol1_dir), panel=p['id'], bindings=str(b_file)))

        # 7. batch generate & finish panels
        panel_ids = [p['id'] for p in script['panels']]
        count = len(panel_ids)
        cols = 1 if count == 1 else 2
        rows = (count + cols - 1) // cols
        canvas_w = 900 * cols
        canvas_h = 600 * max(1, rows)
        plan = {
            'canvas_pixels': [canvas_w, canvas_h],
            'panels': [{
                'panel_id': pid,
                'target_region': [(i % cols) / cols, (i // cols) / rows, 1 / cols, 1 / rows],
                'min_pixels': [900, 600]
            } for i, pid in enumerate(panel_ids)]
        }
        plan_file = self.base / 'plan.json'
        plan_file.write_text(json.dumps(plan), encoding='utf-8')
        prompt_file = self.base / 'prompt.txt'
        prompt_file.write_text('batch prompt', encoding='utf-8')
        batch_info = cp.run(Namespace(command='begin-batch', project=str(vol1_dir), plan=str(plan_file), prompt=str(prompt_file)))

        batch_id = batch_info['batch_id']
        batch_img = vol1_dir / 'art' / 'raw' / f'{batch_id}.png'
        make_dummy_png(batch_img, canvas_w, canvas_h)
        regions = {pid: [900 * (i % cols), 600 * (i // cols), 900, 600] for i, pid in enumerate(panel_ids)}
        reg_file = self.base / 'regions.json'
        reg_file.write_text(json.dumps(regions), encoding='utf-8')
        cp.run(Namespace(command='split-batch', project=str(vol1_dir), batch=batch_id,
                         file=str(batch_img), regions=str(reg_file)))

        # Finish all panels
        proj = cp.project_load(vol1_dir)
        for p_id in panel_ids:
            crop_path = vol1_dir / 'art' / 'crops' / batch_id / f"{p_id}.png"
            p_obj = next(p for p in proj['script']['panels'] if p['id'] == p_id)
            p_inputs = cp.run(Namespace(command='qa-inputs', project=str(vol1_dir), file=str(crop_path),
                                        panel=p_id, attempt=1, reference=None, characters=None, bindings=None))
            p_snap = cp.panel_visual_snapshot(vol1_dir, proj, p_obj)
            p_qa = {
                'image_sha256': cp.sha_file(crop_path),
                'visual_hash': cp.digest(p_snap),
                'attempt_bindings': p_inputs['attempt_bindings'],
                'checks': {k: True for k in cp.PANEL_CHECKS},
                'reviewed_ids': [p_id],
                'comparisons': [], 'findings': [],
                'evidence': '画格质检通过',
                'detail_notes': '细节完整，无崩坏',
                'elegance_notes': {'linework': '线画干净', 'color_and_light': '清透协调', 'visual_hierarchy': '焦点清晰'}
            }
            p_qa_file = self.base / f"{p_id}_qa.json"
            p_qa_file.write_text(json.dumps(p_qa, ensure_ascii=False), encoding='utf-8')
            cp.run(Namespace(command='finish-panel', project=str(vol1_dir), panel=p_id, attempt=1,
                             file=str(crop_path), qa=str(p_qa_file)))

        # 8. compose, review-layout, export, verify-export, complete
        cp.run(Namespace(command='compose', project=str(vol1_dir), font=None))
        proj = cp.project_load(vol1_dir)
        layout_rep = {
            'input_hash': proj['layout']['input_hash'],
            'reviewed_page_ids': [p['id'] for p in proj['layout']['pages']],
            'checks': {k: True for k in cp.LAYOUT_CHECKS},
            'evidence': '版式审阅合格',
            'elegance_notes': {'linework': '页面线条清晰', 'color_and_light': '整页明暗平衡', 'visual_hierarchy': '动线顺畅'}
        }
        lrep_file = self.base / 'layout_rep.json'
        lrep_file.write_text(json.dumps(layout_rep, ensure_ascii=False), encoding='utf-8')
        cp.run(Namespace(command='review-layout', project=str(vol1_dir), file=str(lrep_file)))

        cp.run(Namespace(command='export', project=str(vol1_dir)))
        verify_res = cp.run(Namespace(command='verify-export', project=str(vol1_dir)))
        self.assertTrue(verify_res['ok'])

        # complete Volume 1
        final_rep = {
            'input_hash': proj['layout']['input_hash'],
            'checks': {k: True for k in ('source_scope', 'story_complete', 'visual_consistency', 'exports_opened')},
            'evidence': '本卷交付质检完成'
        }
        frep_file = self.base / 'final_rep.json'
        frep_file.write_text(json.dumps(final_rep, ensure_ascii=False), encoding='utf-8')
        complete_res = cp.run(Namespace(command='complete', project=str(vol1_dir), file=str(frep_file)))
        self.assertTrue(complete_res['ok'])

        # Status check: Volume 1 is complete! Volume 2 is NOT initialized!
        status_v1 = cp.run(Namespace(command='status', project=str(vol1_dir), plan=None))
        self.assertTrue(status_v1['complete'])
        self.assertFalse(vol2_dir.exists())

    def test_manuscript_archive_conflict_resolution_and_manifest(self):
        """Archive two manuscripts with identical names but different content; both are preserved with archive_manifest.json."""
        book_dir = self.base / '书目_归档测试'
        doc1 = self.base / 'part1' / '原稿.txt'
        doc1.parent.mkdir(parents=True)
        doc1.write_text("第一份稿件内容ABC", encoding='utf-8')

        doc2 = self.base / 'part2' / '原稿.txt'
        doc2.parent.mkdir(parents=True)
        doc2.write_text("第二份稿件内容XYZ，不同内容", encoding='utf-8')

        # First archive
        res1 = cp.init_book(book_dir, title='书目_归档测试', sources=[str(doc1)], action='copy')
        manifest_file = book_dir / 'source_texts' / 'archive_manifest.json'
        self.assertTrue(manifest_file.is_file())
        m1 = cp.load_json(manifest_file)
        self.assertEqual(len(m1['files']), 1)
        self.assertEqual(m1['files'][0]['archive_filename'], '原稿.txt')

        # Second archive with same filename but different hash
        res2 = cp.init_book(book_dir, title='书目_归档测试', sources=[str(doc2)], action='copy')
        m2 = cp.load_json(manifest_file)
        self.assertEqual(len(m2['files']), 2)
        # Should have distinct filenames
        archived_names = [f['archive_filename'] for f in m2['files']]
        self.assertEqual(len(set(archived_names)), 2)
        for name in archived_names:
            self.assertTrue((book_dir / 'source_texts' / name).is_file())

    def test_duplicate_volume_split_naming_and_manifest(self):
        """Splitting source with duplicate headings gives numbered stable files and valid split_manifest.json."""
        book_dir = self.base / '重复分卷测试'
        text_with_dups = (
            "第1卷 上部\n第1章 A\n正文A\n"
            "第1卷 上部\n第2章 B\n正文B\n"
        )
        src = self.base / 'dup_novel.txt'
        src.write_text(text_with_dups, encoding='utf-8')
        cp.init_book(book_dir, sources=[str(src)], action='copy')
        split_res = cp.split_source(book_dir, src)
        self.assertEqual(split_res['segments_count'], 2)

        manifest_file = book_dir / 'split_texts' / 'split_manifest.json'
        self.assertTrue(manifest_file.is_file())
        manifest = cp.load_json(manifest_file)
        self.assertEqual(manifest['segments_count'], 2)
        files = [s['output_filename'] for s in manifest['segments']]
        self.assertEqual(files[0], 'vol_001_第1卷 上部.txt')
        self.assertEqual(files[1], 'vol_002_第1卷 上部.txt')
        # Ensure neither overwrote the other
        for f in files:
            self.assertTrue((book_dir / 'split_texts' / f).is_file())

    def test_notes_preservation_and_kanban_updates(self):
        """Volume notes.md and manual remarks in README outside AUTO markers are strictly preserved."""
        vol_dir = self.base / 'vol_notes_test'
        src = self.base / 'src_notes.txt'
        src.write_text("第1章 来信\n正文文本。\n", encoding='utf-8')
        cp.run(Namespace(command='init', project=str(vol_dir), source=[str(src)],
                         title='注记测试', volume='第1卷'))

        notes_file = vol_dir / 'docs' / 'notes.md'
        self.assertTrue(notes_file.is_file())
        custom_note = "# 自定义人工注记\n这是制作人补充的特殊设定禁忌，绝对不可删除！"
        notes_file.write_text(custom_note, encoding='utf-8')

        # Add manual comments to README
        readme_file = vol_dir / 'README.md'
        readme_content = readme_file.read_text(encoding='utf-8')
        custom_tail = "\n\n## 📝 制作组手工日志\n- 2026-10-05: 确认主笔画风风格定调。"
        readme_file.write_text(readme_content + custom_tail, encoding='utf-8')

        # Re-run write_volume_readme (simulate pipeline state change)
        proj = cp.project_load(vol_dir)
        cp.write_volume_readme(vol_dir, proj)

        # Verify notes.md was NOT overwritten
        self.assertEqual(notes_file.read_text(encoding='utf-8'), custom_note)
        # Verify manual comments in README are preserved
        updated_readme = readme_file.read_text(encoding='utf-8')
        self.assertIn("制作组手工日志", updated_readme)
        self.assertIn("确认主笔画风风格定调", updated_readme)

    def test_accepted_panel_accounting_rejects_failed_attempts(self):
        """accepted_panel helper strictly excludes failed, pending, or non-terminal attempts."""
        vol_dir = self.base / 'panel_acct_test'
        src = self.base / 'src_acct.txt'
        src.write_text("第1章 来信\n正文文本。\n", encoding='utf-8')
        cp.run(Namespace(command='init', project=str(vol_dir), source=[str(src)],
                         title='画格统计', volume='第1卷'))
        proj = cp.project_load(vol_dir)
        # Empty panels
        panel = {'id': 'p1'}
        proj['art']['panels']['p1'] = [
            {'number': 1, 'status': 'failed', 'path': 'art/crops/batch-01/p1.png'},
            {'number': 2, 'status': 'pending'}
        ]
        self.assertIsNone(cp.accepted_panel(vol_dir, proj, panel))

    def test_cross_volume_unit_ref_validation(self):
        """Cross-volume source unit references require volume_id, source_index_hash, and unit_id."""
        vol_dir = self.base / 'cross_ref_test'
        src = self.base / 'src_cross.txt'
        src.write_text("第1章 来信\n正文文本。\n", encoding='utf-8')
        cp.run(Namespace(command='init', project=str(vol_dir), source=[str(src)],
                         title='跨卷测试', volume='第1卷'))
        proj = cp.project_load(vol_dir)
        known_units = {u['id']: u for u in proj['source']['units']}

        # 1. Intra-volume string ref
        valid, err = cp.validate_unit_ref('u0000001', known_units, root=vol_dir, project=proj)
        self.assertTrue(valid)
        self.assertIsNone(err)

        # 2. Valid cross-volume dict ref
        src_hash = proj.get('source_index_hash')
        cross_ref = {
            'volume_id': '跨卷测试',
            'source_index_hash': src_hash,
            'unit_id': 'u0000001'
        }
        valid2, err2 = cp.validate_unit_ref(cross_ref, known_units, root=vol_dir, project=proj)
        self.assertTrue(valid2)

        # 3. Bad hash or missing volume
        bad_ref = {'volume_id': '跨卷测试', 'source_index_hash': 'wrong_hash', 'unit_id': 'u0000001'}
        valid3, err3 = cp.validate_unit_ref(bad_ref, known_units, root=vol_dir, project=proj)
        self.assertFalse(valid3)
        self.assertIn('mismatch', err3)

    def test_doctor_and_preflight_typeset_cli(self):
        """doctor and preflight-typeset run cleanly and report diagnostic info."""
        vol_dir = self.base / 'doctor_test'
        src = self.base / 'src_doc.txt'
        src.write_text("第1章 来信\n正文文本。\n", encoding='utf-8')
        cp.run(Namespace(command='init', project=str(vol_dir), source=[str(src)],
                         title='体检测试', volume='第1卷'))

        # Run doctor
        doc_res = cp.run(Namespace(command='doctor', project=str(vol_dir)))
        self.assertTrue(doc_res['ok'])
        self.assertIn('environment', doc_res)
        self.assertEqual(doc_res['project']['title'], '体检测试')

        # Run preflight-typeset
        pf_res = cp.run(Namespace(command='preflight-typeset', project=str(vol_dir)))
        self.assertTrue(pf_res['ok'])
