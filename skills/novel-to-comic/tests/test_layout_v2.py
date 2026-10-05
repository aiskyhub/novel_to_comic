"""Mechanical layout/export regressions; fixtures are not visual-quality evidence."""
import unittest

import test_pipeline as fixtures

cp, cl = fixtures.cp, fixtures.cl


class LayoutSafetyMechanicalTests(unittest.TestCase):
    """Exercise deterministic raster limits and export/cache integrity only."""

    def setUp(self):
        self.f = fixtures.PipelineTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_fixed_page_multirow_overflow_names_first_failing_page_row(self):
        f = self.f
        f.accepted_mixed()
        project = cp.project_load(f.root)
        project['script']['style'].update(format='pages', height=1000)

        with self.assertRaisesRegex(cp.GateError, r'Page page-mixed row 3 exceeds fixed height 1000px'):
            cl.compose(f.root, project)

    def test_unallocatable_fixed_page_height_fails_with_style_field(self):
        f = self.f
        f.locked()
        f.accept_all()
        project = cp.project_load(f.root)
        project['script']['style']['height'] = 1_000_000_000

        with self.assertRaisesRegex(cp.GateError, r'style\.height.*unsafe'):
            cl.compose(f.root, project)

    def test_strip_extreme_panel_ratio_is_blocked_before_generation(self):
        f = self.f
        script = f.mixed_script(max_height=6000)
        script['panels'][0]['aspect_ratio'] = 0.001
        f.invoke('set-script', file=f.json_file(script))
        f.add_reviews()
        f.invoke('lock-script')
        f.reference()
        prompt = f.base / 'prompt.txt'
        prompt.write_text('mechanical only', encoding='utf-8')
        with self.assertRaisesRegex(cp.GateError, 'composed display size'):
            f.begin_one(panel='p1', prompt=prompt)
        self.assertEqual({}, cp.project_load(f.root)['art']['panels'])

    def test_pdf_html_and_cbz_tampering_each_fails_integrity_gate(self):
        f = self.f
        f.exported()
        project = cp.project_load(f.root)
        files = {item['kind']: cp.inside(f.root, item['path']) for item in project['exports']['files']}

        for label, kind in (('HTML reader', 'reader'), ('PDF', 'pdf'), ('CBZ', 'cbz')):
            with self.subTest(format=label):
                original = files[kind].read_bytes()
                try:
                    files[kind].write_bytes(original + b'\nmechanical tamper')
                    with self.assertRaisesRegex(cp.GateError, f'Export missing/changed: {kind}'):
                        f.invoke('verify-export')
                finally:
                    files[kind].write_bytes(original)

        self.assertTrue(f.invoke('verify-export')['ok'])

    def test_cache_repair_archive_hash_is_verified_and_record_cannot_disappear(self):
        from PIL import Image

        f = self.f
        f.exported()
        project = cp.project_load(f.root)
        page = project['layout']['pages'][0]
        page_path = cp.inside(f.root, page['path'])
        Image.new('RGB', (page['width'], page['height']), 'magenta').save(page_path)
        tampered_bytes = page_path.read_bytes()
        f.invoke('compose', font=None)
        f.review_and_export()

        repaired = cp.project_load(f.root)
        records = repaired['layout']['cache_repairs']
        self.assertEqual(1, len(records), 'Mechanical cache-repair fixture must record one damaged page.')
        record = records[0]
        archived = cp.inside(f.root, record['archived_path'])
        self.assertEqual(tampered_bytes, archived.read_bytes())
        self.assertEqual(record['archived_sha256'], cp.sha_file(archived))
        self.assertTrue(f.invoke('verify-export')['ok'])

        archived.write_bytes(archived.read_bytes() + b'changed')
        with self.assertRaisesRegex(cp.GateError, 'Cache repair archive missing/changed'):
            f.invoke('verify-export')

    def test_unregistered_cache_repair_archive_is_not_silently_ignored(self):
        from PIL import Image

        f = self.f
        f.exported()
        project = cp.project_load(f.root)
        page = project['layout']['pages'][0]
        page_path = cp.inside(f.root, page['path'])
        Image.new('RGB', (page['width'], page['height']), 'magenta').save(page_path)
        f.invoke('compose', font=None)
        f.review_and_export()

        project = cp.project_load(f.root)
        self.assertTrue(project['layout']['cache_repairs'])
        del project['layout']['cache_repairs']
        cp.save(f.root, project)
        with self.assertRaisesRegex(cp.GateError, 'Cache repair archive records are missing or invalid'):
            f.invoke('verify-export')


if __name__ == '__main__':
    unittest.main()
