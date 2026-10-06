"""Regressions for evidence gates; no claim of automated plot understanding."""
import copy
import subprocess
import sys
import unittest
from pathlib import Path

import test_pipeline as fixtures
from comic_adaptation import adaptation_errors

cp = fixtures.cp


class AdaptationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.script = self.f.prepare_script()
        self.source = cp.project_load(self.f.root)['source']

    def errors(self, script=None):
        return adaptation_errors(self.source, self.script if script is None else script, self.f.root)

    def test_valid_chapter_check_is_read_only_and_not_a_semantic_pass(self):
        before = (self.f.root / 'project.json').read_bytes()
        chapter = self.f.invoke('script-chapter', chapter='ch000001')
        result = self.f.invoke('check-adaptation', chapter='ch000001', file=self.f.json_file(chapter))
        self.assertEqual([], result['errors'])
        self.assertTrue(result['semantic_review_required'])
        self.assertEqual(cp.digest(chapter), result['draft_hash'])
        self.assertEqual(before, (self.f.root / 'project.json').read_bytes())

    def test_missing_ledger_blocks_review_lock_and_cli(self):
        self.script.pop('chapter_adaptations')
        self.f.invoke('set-script', file=self.f.json_file(self.script))
        with self.assertRaisesRegex(cp.GateError, 'chapter_adaptations'):
            self.f.add_reviews()
        with self.assertRaisesRegex(cp.GateError, 'chapter_adaptations'):
            self.f.invoke('lock-script')
        result = subprocess.run([sys.executable, '-X', 'utf8', cp.__file__, 'check-adaptation',
                                 '--project', str(self.f.root), '--chapter', 'ch000001'],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(2, result.returncode)
        self.assertIn('chapter_adaptations', result.stdout)

    def test_panel_id_without_actual_presentation_is_not_coverage(self):
        detail = self.script['chapter_adaptations'][0]['details'][0]
        detail['presentations'] = [{'panel_id': 'p1'}]
        self.assertTrue(any('.excerpt' in e for e in self.errors()))
        detail['presentations'] = []
        self.assertTrue(any('panel IDs alone' in e for e in self.errors()))

    def test_replacing_process_by_result_fails_even_with_source_id(self):
        panel = self.script['panels'][0]
        panel['action'] = '甲已拿到信。'
        # Paragraph and event IDs still fully cover the original; actual action no longer does.
        self.assertEqual(['u0000002'], panel['source_unit_ids'])
        self.assertTrue(any('.excerpt' in e for e in self.errors()))

    def test_original_quotes_and_panel_evidence_are_checked(self):
        detail = self.script['chapter_adaptations'][0]['details'][0]
        detail['sources'][0]['quote'] = '原文中不存在的对白'
        self.assertTrue(any('quote must occur' in e for e in self.errors()))
        detail['sources'][0]['quote'] = detail['fact']
        detail['presentations'][0]['panel_id'] = 'p2'
        self.assertTrue(any('actual panel in this chapter' in e for e in self.errors()))
        detail['presentations'][0]['panel_id'] = 'p1'
        self.script['panels'][0]['source_unit_ids'] = []
        self.assertTrue(any('bind the detail source' in e for e in self.errors()))

    def test_missing_original_reread_record_or_detail_is_blocked(self):
        record = self.script['chapter_adaptations'][0]
        record['unit_audits'] = []
        self.assertTrue(any('no reread record' in e for e in self.errors()))
        record['unit_audits'] = [{'unit_id': 'u0000002', 'detail_ids': [], 'evidence': '重新读过原文'}]
        self.assertTrue(any('.detail_ids' in e for e in self.errors()))

    def test_internal_state_is_not_reader_facing_evidence(self):
        detail = self.script['chapter_adaptations'][0]['details'][0]
        detail['presentations'] = [{'panel_id': 'p1', 'field': 'state_after.char-a.location', 'excerpt': 'room-a'}]
        self.assertTrue(any('.excerpt' in e for e in self.errors()))

    def test_foreign_volume_id_collision_is_not_local_evidence(self):
        project = cp.project_load(self.f.root)
        self.script['panels'][0]['source_unit_ids'] = [
            {'volume_id': '另一卷', 'source_index_hash': 'f' * 64, 'unit_id': 'u0000002'}]
        self.assertTrue(any('bind the detail source' in e for e in self.errors()))
        self.script['panels'][0]['source_unit_ids'] = [
            {'volume_id': project['title'], 'source_index_hash': project['source_index_hash'], 'unit_id': 'u0000002'}]
        self.assertEqual([], adaptation_errors(self.source, self.script, self.f.root,
                                              source_index_hash=project['source_index_hash'],
                                              current_names=(project['title'],)))

    def test_merge_requires_reason_and_repetition_requires_retained_detail(self):
        record = self.script['chapter_adaptations'][0]
        detail = record['details'][0]
        detail['treatment'] = 'merged'
        self.assertTrue(any('.reason' in e for e in self.errors()))
        detail['reason'] = '同一时刻且可清楚呈现'
        self.assertEqual([], self.errors())
        repeated = copy.deepcopy(detail)
        repeated.update(id='repeat', treatment='repetition', presentations=[], retained_detail_id='d1',
                        reason='机械测试的重复叙述')
        record['details'].append(repeated)
        record['unit_audits'][0]['detail_ids'].append('repeat')
        self.assertEqual([], self.errors())
        repeated['retained_detail_id'] = 'repeat'
        self.assertTrue(any('.retained_detail_id' in e for e in self.errors()))
        detail.update(treatment='paratext', presentations=[])
        self.assertTrue(any('narrative detail cannot' in e for e in self.errors()))

    def test_document_change_invalidates_existing_lock(self):
        self.f.add_reviews()
        self.f.invoke('lock-script')
        path = self.f.root / self.script['chapter_adaptations'][0]['document_path']
        path.write_text('改写过的章节文档', encoding='utf-8')
        self.assertFalse(self.f.invoke('status')['script_locked'])
        with self.assertRaisesRegex(cp.GateError, 'document'):
            self.f.invoke('assert-art')

    def test_refreshing_document_fingerprint_requires_new_reviews(self):
        self.f.add_reviews()
        path = self.f.root / self.script['chapter_adaptations'][0]['document_path']
        path.write_text('经过实际修订的机械测试文档', encoding='utf-8')
        self.script['chapter_adaptations'][0]['document_sha256'] = cp.sha_file(path)
        self.f.invoke('set-script', file=self.f.json_file(self.script))
        with self.assertRaisesRegex(cp.GateError, 'Missing current volume review'):
            self.f.invoke('lock-script')

    def test_chapter_update_replaces_only_selected_evidence(self):
        before = cp.project_load(self.f.root)['script']['chapter_adaptations'][1]
        chapter = self.f.invoke('script-chapter', chapter='ch000001')
        chapter['chapter_adaptations'][0]['unit_audits'][0]['evidence'] = '重新核对具体动作'
        self.f.invoke('set-script-chapter', chapter='ch000001', file=self.f.json_file(chapter))
        updated = cp.project_load(self.f.root)['script']['chapter_adaptations']
        self.assertEqual(before, next(r for r in updated if r['chapter_id'] == 'ch000002'))
        chapter.pop('chapter_adaptations')
        with self.assertRaisesRegex(cp.GateError, 'chapter_adaptations'):
            self.f.invoke('set-script-chapter', chapter='ch000001', file=self.f.json_file(chapter))

    def test_malformed_records_report_errors_without_crashing(self):
        for mutate in (
            lambda s: s.update(chapter_adaptations={}),
            lambda s: s['chapter_adaptations'].append(None),
            lambda s: s['chapter_adaptations'][0].update(details=[None]),
            lambda s: s['chapter_adaptations'][0]['details'][0].update(sources=[{'unit_id': {}, 'quote': '甲'}]),
            lambda s: s['chapter_adaptations'][0]['details'][0].update(presentations=[None]),
            lambda s: s['chapter_adaptations'][0]['unit_audits'][0].update(detail_ids=[{}]),
            lambda s: s['chapter_adaptations'][0].update(document_path='../escape.md'),
        ):
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(self.script)
                mutate(candidate)
                self.assertTrue(self.errors(candidate))


if __name__ == '__main__':
    unittest.main()
