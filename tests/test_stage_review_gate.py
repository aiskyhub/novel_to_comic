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


if __name__ == '__main__':
    unittest.main()
