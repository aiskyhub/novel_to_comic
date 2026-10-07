from pathlib import Path
import copy
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import comic_pipeline as cp


class TestStageReviewGate(unittest.TestCase):
    def setUp(self):
        self.template_path = Path(__file__).resolve().parents[1] / 'assets' / 'stage-review-template.json'
        self.valid_report = json.loads(self.template_path.read_text(encoding='utf-8'))

    def test_valid_stage_review_passes(self):
        self.assertTrue(cp.validate_stage_review(self.valid_report))

    def test_anti_rubber_stamp_high_score_zero_deductions_rejected(self):
        """Simulate an agent writing a 98-score 'pass in one shot' fake report with 0 deductions."""
        report = copy.deepcopy(self.valid_report)
        auditor = report['round_1_initial_audit']['auditors'][0]
        auditor['scores']['total_score'] = 98
        auditor['deductions'] = []
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('Anti-rubber-stamp gate triggered', str(ctx.exception))

    def test_zero_issues_in_round_1_rejected(self):
        """Simulate an auditor claiming 0 issues on a 5-chapter initial draft."""
        report = copy.deepcopy(self.valid_report)
        auditor = report['round_1_initial_audit']['auditors'][0]
        auditor['issues'] = []
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('reported 0 issues in round 1', str(ctx.exception))

    def test_missing_source_quote_in_issues_rejected(self):
        """Issues must bind real source quotes from novel text."""
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['auditors'][0]['issues'][0]['source_quote'] = ''
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('missing source_quote binding', str(ctx.exception))

    def test_missing_lead_agent_modifications_rejected(self):
        """Lead agent must document real applied fixes with panel IDs."""
        report = copy.deepcopy(self.valid_report)
        report['lead_agent_revisions']['applied_fixes'] = []
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('must include non-empty applied_fixes', str(ctx.exception))

    def test_applied_fix_missing_panels_rejected(self):
        report = copy.deepcopy(self.valid_report)
        report['lead_agent_revisions']['applied_fixes'][0]['modified_panels'] = []
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('missing modified_panels', str(ctx.exception))

    def test_round_2_score_below_85_rejected(self):
        """Final verification score must meet the passing threshold (>=85)."""
        report = copy.deepcopy(self.valid_report)
        report['round_2_verification']['auditors_recheck'][0]['final_scores']['total_score'] = 82
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('must be >= 85 to pass', str(ctx.exception))

    def test_round_2_unresolved_issues_rejected(self):
        """Cannot pass if round 2 has unresolved defects."""
        report = copy.deepcopy(self.valid_report)
        report['round_2_verification']['auditors_recheck'][0]['unresolved_issue_refs'] = ['auditor_a_issue_01']
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('has unresolved issues', str(ctx.exception))


    def test_placeholder_quote_rejected(self):
        """Placeholder tokens like '原文具体短引句' must be rejected by anti-hallucination gate."""
        report = copy.deepcopy(self.valid_report)
        report['round_1_initial_audit']['auditors'][0]['issues'][0]['source_quote'] = '原文破庙风雨交加与阴冷陈设的具体短引句'
        with self.assertRaises(cp.GateError) as ctx:
            cp.validate_stage_review(report)
        self.assertIn('Anti-hallucination gate', str(ctx.exception))
        self.assertIn('contains template placeholder', str(ctx.exception))

    def test_screenwriter_revisions_key_supported(self):
        """Allow modern screenwriter_revisions key as primary representation of revisions."""
        report = copy.deepcopy(self.valid_report)
        report['screenwriter_revisions'] = report.pop('lead_agent_revisions')
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


if __name__ == '__main__':
    unittest.main()
