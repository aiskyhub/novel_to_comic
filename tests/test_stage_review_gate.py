from pathlib import Path
import copy
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import comic_pipeline as cp


def make_valid_stage_report():
    return {
        "schema_version": 6,
        "stage_range": {
            "start_chapter_id": "ch000001",
            "end_chapter_id": "ch000005",
            "chapter_count": 5,
            "stage_script_hash": "mock_hash_valid",
        },
        "stage_script_hash": "mock_hash_valid",
        "reviewed_chapters": [
            "ch000001",
            "ch000002",
            "ch000003",
            "ch000004",
            "ch000005"
        ],
        "stage_approved": True,
        "round_1_initial_audit": {
            "initial_stage_script_hash": "mock_hash_initial",
            "initial_panels_snapshot": [
                {
                    "id": "p001_01",
                    "chapter_id": "ch000001",
                    "action": "主角推开房门，中景。",
                    "shot": "中景",
                    "space": "破庙主殿内",
                    "expression": "警惕"
                },
                {
                    "id": "p002_01",
                    "chapter_id": "ch000002",
                    "action": "甲看信纸。",
                    "shot": "中景",
                    "space": "书房",
                    "expression": "凝重"
                }
            ],
            "initial_verdict": "REJECTED",
            "auditors": [
                {
                    "auditor_id": "auditor_a",
                    "conversation_id": "conv_mock_auditor_a",
                    "scores": {"total_score": 75},
                    "deductions": [{"reason": "节奏需紧凑", "points": 10}],
                    "issues": [
                        {
                            "id": "auditor_a_issue_01",
                            "chapter_id": "ch000001",
                            "source_quote": "主角推开房门，环顾四周寂静无声。",
                            "problem_description": "镜头未能充分表现推门时的环境阴影变化",
                            "suggested_fix": "增加特写镜头表现门把手与阴影"
                        }
                    ]
                },
                {
                    "auditor_id": "auditor_b",
                    "conversation_id": "conv_mock_auditor_b",
                    "scores": {"total_score": 78},
                    "deductions": [{"reason": "对白可更凝练", "points": 8}],
                    "issues": [
                        {
                            "id": "auditor_b_issue_01",
                            "chapter_id": "ch000002",
                            "source_quote": "信纸上的字迹已经有些模糊。",
                            "problem_description": "信纸模糊细节应在画格提示中明确",
                            "suggested_fix": "画格提示中补充字迹淡化描述"
                        }
                    ]
                }
            ]
        },
        "screenwriter_revisions": {
            "revision_summary": "根据初审意见补充了推门特写与信纸细节描述",
            "applied_fixes": [
                {
                    "issue_ref": "auditor_a_issue_01",
                    "modified_panels": ["p001_01"],
                    "before_revision": "主角推开房门，中景。",
                    "after_revision": "特写门把手被推开，阴影落在门缝，随后切入室内中景。"
                },
                {
                    "issue_ref": "auditor_b_issue_01",
                    "modified_panels": ["p002_01"],
                    "before_revision": "甲看信纸。",
                    "after_revision": "特写信纸，纸面字迹因年代久远而模糊泛黄。"
                }
            ],
            "adjudications": []
        },
        "round_2_verification": {
            "stage_approved": True,
            "approved_at": "2026-10-07T12:00:00Z",
            "auditors_recheck": [
                {
                    "auditor_id": "auditor_a",
                    "final_scores": {"total_score": 90},
                    "verdict": "PASSED",
                    "unresolved_issue_refs": []
                },
                {
                    "auditor_id": "auditor_b",
                    "final_scores": {"total_score": 88},
                    "verdict": "PASSED",
                    "unresolved_issue_refs": []
                }
            ]
        }
    }


def make_clean_pass_stage_report():
    return {
        "schema_version": 6,
        "stage_range": {
            "start_chapter_id": "ch000001",
            "end_chapter_id": "ch000005",
            "chapter_count": 5,
            "stage_script_hash": "mock_hash_clean",
        },
        "stage_script_hash": "mock_hash_clean",
        "reviewed_chapters": [
            "ch000001", "ch000002", "ch000003", "ch000004", "ch000005"
        ],
        "stage_approved": True,
        "round_1_initial_audit": {
            "initial_verdict": "PASSED",
            "auditors": [
                {
                    "auditor_id": "auditor_a",
                    "conversation_id": "conv_mock_auditor_a",
                    "scores": {"total_score": 96},
                    "deductions": [],
                    "issues": [],
                },
                {
                    "auditor_id": "auditor_b",
                    "conversation_id": "conv_mock_auditor_b",
                    "scores": {"total_score": 94},
                    "deductions": [],
                    "issues": [],
                },
            ],
        },
        "round_2_verification": {
            "stage_approved": True,
            "approved_at": "2026-10-07T12:00:00Z",
            "auditors_recheck": [
                {
                    "auditor_id": "auditor_a",
                    "final_scores": {"total_score": 96},
                    "verdict": "PASSED",
                    "unresolved_issue_refs": [],
                },
                {
                    "auditor_id": "auditor_b",
                    "final_scores": {"total_score": 94},
                    "verdict": "PASSED",
                    "unresolved_issue_refs": [],
                },
            ],
        },
    }


class TestStageReviewGate(unittest.TestCase):
    def setUp(self):
        self.valid_report = make_valid_stage_report()

    def test_valid_stage_review_passes(self):
        self.assertTrue(cp.validate_stage_review(self.valid_report))

    def test_clean_pass_with_zero_issues_allowed(self):
        """Auditors who find 0 issues after thorough inspection are allowed to pass round 1 directly."""
        report = make_clean_pass_stage_report()
        self.assertTrue(cp.validate_stage_review(report))

    def test_score_over_100_rejected(self):
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['auditors'][0]['scores']['total_score'] = 105
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('out of valid range (0-100)', str(ctx.exception))

    def test_score_negative_rejected(self):
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['auditors'][0]['scores']['total_score'] = -5
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('out of valid range (0-100)', str(ctx.exception))

    def test_missing_source_quote_in_issues_rejected(self):
        """Issues must bind real source quotes from novel text."""
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['auditors'][0]['issues'][0]['source_quote'] = ''
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('missing source_quote binding', str(ctx.exception))

    def test_placeholder_quote_rejected(self):
        """Placeholder tokens like '待填' must be rejected by anti-hallucination gate."""
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['auditors'][0]['issues'][0]['source_quote'] = '这是原文具体短引句待填内容'
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('Anti-hallucination gate', str(ctx.exception))
        self.assertIn('contains template placeholder', str(ctx.exception))

    def test_missing_lead_agent_modifications_rejected(self):
        """When round 1 has issues, revisions must document fixes or adjudications."""
        report = copy.deepcopy(self.valid_report)
        report['screenwriter_revisions']['applied_fixes'] = []
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('must document applied_fixes or adjudications', str(ctx.exception))

    def test_applied_fix_missing_panels_rejected(self):
        report = copy.deepcopy(self.valid_report)
        report['screenwriter_revisions']['applied_fixes'][0]['modified_panels'] = []
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('missing modified_panels', str(ctx.exception))

    def test_identical_before_after_revision_rejected(self):
        """Before and after revision texts must differ to prove real modification occurred."""
        report = copy.deepcopy(self.valid_report)
        fix = report['screenwriter_revisions']['applied_fixes'][0]
        fix['after_revision'] = fix['before_revision']
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('before_revision and after_revision are identical', str(ctx.exception))

    def test_unknown_issue_ref_rejected(self):
        """Fixes must reference real issues from round 1."""
        report = copy.deepcopy(self.valid_report)
        report['screenwriter_revisions']['applied_fixes'][0]['issue_ref'] = 'unknown_issue_999'
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('references unknown issue_ref', str(ctx.exception))

    def test_adjudication_dismissing_issue_with_reason(self):
        """Screenwriter can adjudicate an issue as false positive with explicit documented reason."""
        report = copy.deepcopy(self.valid_report)
        # Auditor B's issue is dismissed with reason instead of modifying panel
        report['screenwriter_revisions']['applied_fixes'] = [report['screenwriter_revisions']['applied_fixes'][0]]
        report['screenwriter_revisions']['adjudications'] = [
            {
                "issue_ref": "auditor_b_issue_01",
                "reason": "原著后文在第3章明确说明信纸材质与笔迹清晰，此处属于审阅人未通读后文导致的误报，维持原镜。",
            }
        ]
        # In round 2, auditor B confirms adjudication
        report['round_2_verification']['auditors_recheck'][1]['unresolved_issue_refs'] = ['auditor_b_issue_01']
        self.assertTrue(cp.validate_stage_review(report))

    def test_adjudication_missing_reason_rejected(self):
        report = copy.deepcopy(self.valid_report)
        report['screenwriter_revisions']['adjudications'] = [
            {"issue_ref": "auditor_b_issue_01", "reason": ""}
        ]
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('missing reason', str(ctx.exception))

    def test_round_2_score_below_85_rejected(self):
        """Final verification score must meet the passing threshold (>=85)."""
        report = copy.deepcopy(self.valid_report)
        report['round_2_verification']['auditors_recheck'][0]['final_scores']['total_score'] = 82
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('must be >= 85 to pass', str(ctx.exception))

    def test_round_2_unresolved_issues_rejected(self):
        """Cannot pass if round 2 has unresolved defects that were not adjudicated."""
        report = copy.deepcopy(self.valid_report)
        report['round_2_verification']['auditors_recheck'][0]['unresolved_issue_refs'] = ['auditor_a_issue_01']
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('has unresolved issues', str(ctx.exception))

    def test_round_2_auditor_mismatch_rejected(self):
        """Round 2 auditors must correspond to round 1 auditors."""
        report = copy.deepcopy(self.valid_report)
        report['round_2_verification']['auditors_recheck'][1]['auditor_id'] = 'stranger_auditor'
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('Round 2 auditors_recheck must correspond to round 1 auditors', str(ctx.exception))

    def test_lead_agent_revisions_backward_compat(self):
        """Allow legacy lead_agent_revisions key for backward compatibility."""
        report = copy.deepcopy(self.valid_report)
        report['lead_agent_revisions'] = report.pop('screenwriter_revisions')
        self.assertTrue(cp.validate_stage_review(report))

    def test_duplicate_auditor_ids_rejected(self):
        """Auditors must have distinct identities."""
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['auditors'][1]['auditor_id'] = report['round_1_initial_audit']['auditors'][0]['auditor_id']
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('requires distinct auditor IDs', str(ctx.exception))

    def test_duplicate_subagent_conversation_ids_rejected(self):
        """Subagents must have distinct conversation IDs."""
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['auditors'][1]['conversation_id'] = report['round_1_initial_audit']['auditors'][0]['conversation_id']
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('requires distinct subagent conversation IDs', str(ctx.exception))

    def test_missing_stage_script_hash_rejected(self):
        """Deleting stage_script_hash must be rejected unconditionally."""
        report = copy.deepcopy(self.valid_report)
        report.pop('stage_script_hash', None)
        report['stage_range'].pop('stage_script_hash', None)
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn("requires non-empty 'stage_script_hash'", str(ctx.exception))

    def test_unhandled_round_1_issue_rejected(self):
        """Every identified issue in round 1 must have an applied_fix or adjudication."""
        report = copy.deepcopy(self.valid_report)
        # Add a third issue in round 1
        new_issue = {
            "id": "auditor_a_issue_02",
            "chapter_id": "ch000001",
            "source_quote": "主角推开房门，环顾四周寂静无声。",
            "problem_description": "第二处缺陷未被修复",
            "suggested_fix": "补充修改"
        }
        report['round_1_initial_audit']['auditors'][0]['issues'].append(new_issue)
        # screenwriter_revisions only handles auditor_a_issue_01 and auditor_b_issue_01
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('auditor_a_issue_02', str(ctx.exception))
        self.assertIn('have neither an applied_fix nor an adjudication', str(ctx.exception))

    def test_identical_snapshot_with_fixes_rejected(self):
        """If fixes are claimed, initial snapshot hash cannot match current stage hash."""
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['initial_stage_script_hash'] = report['stage_script_hash']
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertTrue(any(w in str(ctx.exception) for w in ('initial snapshot', 'matches current stage_script_hash', 'no actual modifications')))

    def test_panel_still_containing_before_revision_rejected(self):
        """If panel in project still contains the old before_revision string, gate rejects it."""
        report = copy.deepcopy(self.valid_report)
        mock_proj = {
            'source': {
                'chapters': [{'id': f'ch00000{i}'} for i in range(1, 6)],
                'units': [
                    {'id': 'u1', 'chapter_id': 'ch000001', 'text': '主角推开房门，环顾四周寂静无声。'},
                    {'id': 'u2', 'chapter_id': 'ch000002', 'text': '信纸上的字迹已经有些模糊。'}
                ]
            },
            'script': {
                'panels': [
                    {'id': 'p001_01', 'chapter_id': 'ch000001', 'action': '主角推开房门，中景。'},
                    {'id': 'p002_01', 'chapter_id': 'ch000002', 'action': '特写信纸，纸面字迹因年代久远而模糊泛黄。'}
                ],
                'scenes': [], 'pages': [], 'chapter_adaptations': [], 'characters': [], 'events': [], 'settings': []
            }
        }
        report['stage_script_hash'] = cp.stage_script_fingerprint(mock_proj, report['reviewed_chapters'])
        # Setup snapshot where p001_01 was '主角推开房门，中景。' and p002_01 was '甲看信纸。'
        report['round_1_initial_audit']['initial_panels_snapshot'] = [
            {'id': 'p001_01', 'chapter_id': 'ch000001', 'action': '主角推开房门，中景。'},
            {'id': 'p002_01', 'chapter_id': 'ch000002', 'action': '甲看信纸。'}
        ]
        init_proj = copy.deepcopy(mock_proj)
        init_proj['script']['panels'] = copy.deepcopy(report['round_1_initial_audit']['initial_panels_snapshot'])
        report['round_1_initial_audit']['initial_stage_script_hash'] = cp.stage_script_fingerprint(init_proj, report['reviewed_chapters'])

        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report, project=mock_proj)
        self.assertTrue(any(w in str(ctx.exception) for w in ('verification failed', 'still contains', 'not found', 'neither action')))

    def test_normal_revision_retaining_old_words_allowed(self):
        """Modifying shot from '中景' to '中景，低机位' (which contains '中景') must be allowed and pass verification."""
        mock_proj = {
            'source': {
                'chapters': [{'id': f'ch00000{i}'} for i in range(1, 6)],
                'units': [
                    {'id': 'u1', 'chapter_id': 'ch000001', 'text': '主角推开房门，环顾四周寂静无声。'},
                    {'id': 'u2', 'chapter_id': 'ch000002', 'text': '信纸上的字迹已经有些模糊。'}
                ]
            },
            'script': {
                'panels': [
                    {'id': 'p001_01', 'chapter_id': 'ch000001', 'shot': '中景，低机位', 'action': '主角推开房门，中景。'},
                    {'id': 'p002_01', 'chapter_id': 'ch000002', 'shot': '全景', 'action': '特写信纸，纸面字迹因年代久远而模糊泛黄。'}
                ],
                'scenes': [], 'pages': [], 'chapter_adaptations': [], 'characters': [], 'events': [], 'settings': []
            }
        }
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['initial_panels_snapshot'] = [
            {'id': 'p001_01', 'chapter_id': 'ch000001', 'shot': '中景', 'action': '主角推开房门，中景。'},
            {'id': 'p002_01', 'chapter_id': 'ch000002', 'shot': '全景', 'action': '甲看信纸。'}
        ]
        report['screenwriter_revisions']['applied_fixes'][0] = {
            'issue_ref': 'auditor_a_issue_01',
            'modified_panels': ['p001_01'],
            'field': 'shot',
            'before_revision': '中景',
            'after_revision': '中景，低机位'
        }
        report['stage_script_hash'] = cp.stage_script_fingerprint(mock_proj, report['reviewed_chapters'])
        # Compute real initial hash for snapshot
        init_proj = copy.deepcopy(mock_proj)
        init_proj['script']['panels'] = copy.deepcopy(report['round_1_initial_audit']['initial_panels_snapshot'])
        report['round_1_initial_audit']['initial_stage_script_hash'] = cp.stage_script_fingerprint(init_proj, report['reviewed_chapters'])
        self.assertTrue(cp.validate_stage_review(report, project=mock_proj))

    def test_arbitrary_initial_hash_rejected(self):
        """If report fills an arbitrary 64-char string for initial_stage_script_hash, gate rejects it."""
        mock_proj = {
            'source': {
                'chapters': [{'id': f'ch00000{i}'} for i in range(1, 6)],
                'units': [
                    {'id': 'u1', 'chapter_id': 'ch000001', 'text': '主角推开房门，环顾四周寂静无声。'},
                    {'id': 'u2', 'chapter_id': 'ch000002', 'text': '信纸上的字迹已经有些模糊。'}
                ]
            },
            'script': {
                'panels': [{'id': 'p001_01', 'chapter_id': 'ch000001', 'action': '特写门把手被推开，阴影落在门缝，随后切入室内中景。'}],
                'scenes': [], 'pages': [], 'chapter_adaptations': [], 'characters': [], 'events': [], 'settings': []
            }
        }
        report = copy.deepcopy(self.valid_report)
        report['screenwriter_revisions']['applied_fixes'] = [report['screenwriter_revisions']['applied_fixes'][0]]
        report['screenwriter_revisions']['adjudications'] = [
            {'issue_ref': 'auditor_b_issue_01', 'reason': '误报维持原状'}
        ]
        report['stage_script_hash'] = cp.stage_script_fingerprint(mock_proj, report['reviewed_chapters'])
        report['round_1_initial_audit']['initial_stage_script_hash'] = 'a' * 64
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report, project=mock_proj)
        self.assertIn('initial_stage_script_hash mismatch', str(ctx.exception))

    def test_event_without_chapter_id_covered_by_fingerprint(self):
        """Events referenced by panel event_ids without explicit chapter_id must be covered by stage_script_fingerprint."""
        proj = {
            'script': {
                'panels': [{'id': 'p1', 'chapter_id': 'ch01', 'event_ids': ['ev_orphan']}],
                'scenes': [], 'pages': [], 'chapter_adaptations': [], 'characters': [],
                'events': [{'id': 'ev_orphan', 'description': '重要原著事件'}],
                'settings': []
            }
        }
        h1 = cp.stage_script_fingerprint(proj, ['ch01'])
        proj2 = copy.deepcopy(proj)
        proj2['script']['events'][0]['description'] = '修改后的事件描述'
        h2 = cp.stage_script_fingerprint(proj2, ['ch01'])
        self.assertNotEqual(h1, h2, "Modifying event description without chapter_id must change fingerprint")

    def test_fingerprint_covers_scenes_pages_and_settings(self):
        """Fingerprint must change when scenes, settings, pages, or characters change."""
        proj = {
            'script': {
                'panels': [{'id': 'p1', 'chapter_id': 'ch01', 'cast': ['c1']}],
                'scenes': [{'id': 's1', 'chapter_id': 'ch01', 'setting_id': 'set1'}],
                'pages': [{'id': 'pg1', 'chapter_id': 'ch01', 'panel_ids': ['p1']}],
                'chapter_adaptations': [],
                'characters': [{'id': 'c1', 'name': '甲'}],
                'events': [{'id': 'ev1', 'chapter_id': 'ch01'}],
                'settings': [{'id': 'set1', 'name': '密室'}]
            }
        }
        h1 = cp.stage_script_fingerprint(proj, ['ch01'])
        # Modify scene
        proj2 = copy.deepcopy(proj)
        proj2['script']['scenes'][0]['setting_id'] = 'set2'
        h2 = cp.stage_script_fingerprint(proj2, ['ch01'])
        self.assertNotEqual(h1, h2, "Modifying scene setting must change fingerprint")

        # Modify page
        proj3 = copy.deepcopy(proj)
        proj3['script']['pages'][0]['panel_ids'] = ['p1', 'p2']
        h3 = cp.stage_script_fingerprint(proj3, ['ch01'])
        self.assertNotEqual(h1, h3, "Modifying page panels must change fingerprint")

    def test_check_phone_rows_panel_count_gate(self):
        """Phone rows gate must accept 3 and 4 panels, but reject 1, 2, or 5 panels."""
        import comic_layout as cl
        # 3 panels -> valid
        cl.check_phone_rows({'panel_ids': ['p1', 'p2', 'p3'], 'columns': 1}, [['p1'], ['p2'], ['p3']])
        # 4 panels -> valid
        cl.check_phone_rows({'panel_ids': ['p1', 'p2', 'p3', 'p4'], 'columns': 1}, [['p1'], ['p2'], ['p3'], ['p4']])

        # 1 panel -> rejected
        with self.assertRaises(cp.GateError) as ctx1:
            cl.check_phone_rows({'panel_ids': ['p1'], 'columns': 1}, [['p1']])
        self.assertIn('require 3–4 narrative panels', str(ctx1.exception))

        # 2 panels -> rejected
        with self.assertRaises(cp.GateError) as ctx2:
            cl.check_phone_rows({'panel_ids': ['p1', 'p2'], 'columns': 1}, [['p1'], ['p2']])
        self.assertIn('require 3–4 narrative panels', str(ctx2.exception))

        # 5 panels -> rejected
        with self.assertRaises(cp.GateError) as ctx5:
            cl.check_phone_rows({'panel_ids': [f'p{i}' for i in range(5)], 'columns': 1}, [[f'p{i}'] for i in range(5)])
        self.assertIn('require 3–4 narrative panels', str(ctx5.exception))

    def test_preview_page_path_traversal_blocked(self):
        """preview_page must reject traversal page_ids or non-existent pages."""
        import comic_layout as cl
        mock_proj = {'script': {'pages': [{'id': 'page01'}]}}
        with self.assertRaises(cp.GateError) as ctx:
            cl.preview_page('.', mock_proj, '../../../escaped-previews')
        self.assertIn('Invalid page_id', str(ctx.exception))

        with self.assertRaises(cp.GateError) as ctx2:
            cl.preview_page('.', mock_proj, 'nonexistent_page')
        self.assertIn('does not exist in project pages', str(ctx2.exception))


if __name__ == '__main__':
    unittest.main()
