"""Unit and regression tests for Schema v6 volume-scoped comic pipeline features."""
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
                           selection_reason='自动化机械测试', format='pages', width=1080, height=2400, font_size=54)
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
        event_id = f'ev{i}'
        pid = f'p{i}'
        script['events'].append({
            'id': event_id, 'description': unit['text'], 'source_unit_ids': [unit['id']],
            'narrative_role': 'critical_turning_point'
        })
        script['panels'].append({
            'id': pid, 'chapter_id': unit['chapter_id'], 'scene_id': 'scene-' + unit['chapter_id'],
            'source_unit_ids': [unit['id']], 'event_ids': [event_id], 'cast': ['char-a'],
            'appearance_versions': {'char-a': 'base'},
            'action': unit['text'] + '：' + pid, 'shot': '中景', 'space': '人物位于测试房间中', 'expression': '平静',
            'state_before': {'char-a': copy.deepcopy(state)}, 'state_after': {'char-a': copy.deepcopy(state)},
            'dialogue': [{'kind': 'caption', 'text': unit['text']}]
        })
    page_idx = 1
    for chapter in source['chapters']:
        if not chapter['has_body']:
            continue
        ch_units = [u for u in body if u['chapter_id'] == chapter['id']]
        ch_pids = [f'p{body.index(u)+1}' for u in ch_units]
        i = 0
        while i < len(ch_pids):
            remaining = len(ch_pids) - i
            if remaining == 4:
                chunk = ch_pids[i:i+4]
                i += 4
            else:
                chunk = ch_pids[i:i+3]
                i += 3
            script['pages'].append({'id': f'page{page_idx}', 'chapter_id': chapter['id'], 'panel_ids': chunk, 'rows': [[p] for p in chunk], 'columns': 1})
            page_idx += 1
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
