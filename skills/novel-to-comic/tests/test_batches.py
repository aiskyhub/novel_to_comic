"""Synthetic pixels validate accounting/extraction, never artistic quality."""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw
import test_pipeline as fixtures

cp = fixtures.cp


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.f = self.fresh()

    def fresh(self, count=4):
        f = fixtures.PipelineTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.mixed_script(count=count)
        f.add_reviews()
        f.invoke('lock-script')
        f.reference()
        return f

    def plan(self, count, ids=None):
        ids = ids or [f'p{i+1}' for i in range(count)]
        cols = 1 if count == 1 else 2
        rows = (count + cols - 1) // cols
        return {'canvas_pixels': [900 * cols, 600 * max(1, rows)], 'panels': [{'panel_id': pid,
                            'target_region': [(i % cols) / cols, (i // cols) / rows, 1 / cols, 1 / rows],
                            'min_pixels': [900, 600]} for i, pid in enumerate(ids)]}

    def start(self, plan, f=None):
        f = f or self.f
        prompt = f.base / 'batch-prompt.txt'
        prompt.write_text('机械测试合图；不调用任何图像服务。', encoding='utf-8')
        return f.invoke('begin-batch', plan=f.json_file(plan), prompt=str(prompt))

    def image(self, count, f=None):
        f = f or self.f
        cols = 1 if count == 1 else 2
        rows = (count + cols - 1) // cols
        image = Image.new('RGB', (900 * cols, 600 * rows), 'white')
        draw = ImageDraw.Draw(image)
        colors = ['#ca5362', '#428d6b', '#467cc2', '#d3a348']
        regions = {}
        for i in range(count):
            x, y = 900 * (i % cols), 600 * (i // cols)
            draw.rectangle((x, y, x + 899, y + 599), fill=colors[i % len(colors)])
            draw.line((x + 13, y + 11, x + 881, y + 583), fill='black', width=1)
            draw.rectangle((x + 27, y + 39, x + 83, y + 91), fill='white')
            regions[f'p{i+1}'] = [x, y, 900, 600]
        path = f.base / 'synthetic-raw.png'
        image.save(path)
        return str(path), regions

    def split(self, batch, raw, regions, f=None):
        f = f or self.f
        return f.invoke('split-batch', batch=batch['batch_id'], file=raw, regions=f.json_file(regions))

    def accept(self, item, f=None):
        f = f or self.f
        f.invoke('finish-panel', panel=item['panel_id'], attempt=item['attempt'], file=item['file'],
                 qa=f.panel_qa(item['panel_id'], item['attempt'], item['file']))

    def test_capacity_driven_batches_extract_exact_pixels_and_reading_order(self):
        for count in (1, 2, 4, 6, 8, 10):
            with self.subTest(count=count):
                f = self.fresh(count=max(4, count))
                batch = self.start(self.plan(count), f)
                raw, regions = self.image(count, f)
                result = self.split(batch, raw, regions, f)
                self.assertEqual(list(regions), [p['panel_id'] for p in result['panels']])
                with Image.open(raw) as source:
                    for item in result['panels']:
                        x, y, w, h = regions[item['panel_id']]
                        with Image.open(item['file']) as extracted:
                            self.assertEqual((w, h), extracted.size)
                            self.assertEqual(source.crop((x, y, x+w, y+h)).tobytes(), extracted.tobytes())
                        self.accept(item, f)
                again = self.split(batch, raw, regions, f)
                self.assertEqual(result, again)
                before = (f.root / 'project.json').read_bytes()
                reused = self.start(self.plan(count), f)
                self.assertFalse(reused['generation_required'])
                self.assertEqual(count, len(reused['reused']))
                self.assertEqual(before, (f.root / 'project.json').read_bytes())
                counts = f.invoke('status')['preflight_counts']
                self.assertEqual(count, counts['attempts_recorded'])
                self.assertEqual(1, counts['generation_batches_recorded'])
                self.assertEqual(0, counts['pending_batches'])

    def test_partial_acceptance_reuses_success_and_retries_only_failed_panel(self):
        batch = self.start(self.plan(2))
        raw, regions = self.image(2)
        items = self.split(batch, raw, regions)['panels']
        self.accept(items[0])
        f = self.f
        f.invoke('fail-panel', panel='p2', attempt=1, reason='机械测试：第二格需要返修')
        original_plan = self.plan(2)
        before = (f.root / 'project.json').read_bytes()
        preflight = f.invoke('preflight', plan=f.json_file(original_plan))
        planned = preflight['planned_batches'][0]
        self.assertEqual(['p1'], planned['reused_panel_ids'])
        self.assertEqual(['p2'], planned['active_panel_ids'])
        self.assertTrue(planned['requires_repack'])
        with self.assertRaisesRegex(cp.GateError, 'repack'):
            self.start(original_plan)
        self.assertEqual(before, (f.root / 'project.json').read_bytes())
        retry = self.start(self.plan(1, ['p2']))
        self.assertEqual([], retry['reused'])
        self.assertEqual(['p2'], [p['panel_id'] for p in retry['panels']])
        self.assertEqual(2, retry['panels'][0]['attempt'])
        self.assertEqual([0, 0, 1, 1], retry['panels'][0]['target_region'])
        single_raw, _ = self.image(1)
        repaired = self.split(retry, single_raw, {'p2': [0, 0, 900, 600]})['panels'][0]
        self.accept(repaired)
        project = cp.project_load(f.root)
        self.assertEqual(1, len(project['art']['panels']['p1']))
        self.assertEqual(items[0]['sha256'], project['art']['panels']['p1'][0]['sha256'])
        self.assertEqual(2, f.invoke('status')['panels_accepted'])

    def test_group_prechecks_are_atomic_and_pending_blocks_other_groups(self):
        f = self.f
        self.start(self.plan(1, ['p2']))
        before = (f.root / 'project.json').read_bytes()
        with self.assertRaisesRegex(cp.GateError, 'unfinished'):
            self.start(self.plan(2))
        self.assertEqual(before, (f.root / 'project.json').read_bytes())
        self.assertNotIn('p1', cp.project_load(f.root)['art']['panels'])
        f.invoke('fail-panel', panel='p2', attempt=1, reason='机械结算')
        project = cp.project_load(f.root)
        del project['art']['bindings']['p2']
        cp.save(f.root, project)
        before = (f.root / 'project.json').read_bytes()
        with self.assertRaisesRegex(cp.GateError, 'binding'):
            self.start(self.plan(2))
        self.assertEqual(before, (f.root / 'project.json').read_bytes())

    def test_regrouping_and_prompt_changes_do_not_reset_attempt_budget(self):
        f = self.f
        for ids in (['p1', 'p2'], ['p1'], ['p1', 'p3']):
            batch = self.start(self.plan(len(ids), ids))
            for item in batch['panels']:
                f.invoke('fail-panel', panel=item['panel_id'], attempt=item['attempt'], reason='机械失败')
        before = (f.root / 'project.json').read_bytes()
        with self.assertRaisesRegex(cp.GateError, 'Three attempts exhausted'):
            self.start(self.plan(2, ['p1', 'p4']))
        self.assertEqual(before, (f.root / 'project.json').read_bytes())

    def test_invalid_plans_leave_no_attempts(self):
        valid = self.plan(2)
        invalid = [self.plan(0), self.plan(2, ['p2', 'p1']), self.plan(2, ['p1', 'p1']),
                   self.plan(1, ['unknown'])]
        for key, value in (('target_region', [-1, 0, 1, 1]), ('target_region', [0, 0, 2, 1]),
                           ('min_pixels', [1, 1]), ('min_pixels', [True, 600]),
                           ('min_pixels', [12001, 600])):
            data = copy.deepcopy(valid)
            data['panels'][0][key] = value
            invalid.append(data)
        overlap = copy.deepcopy(valid)
        overlap['panels'][1]['target_region'] = overlap['panels'][0]['target_region']
        invalid.append(overlap)
        before = (self.f.root / 'project.json').read_bytes()
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(cp.GateError):
                self.start(plan)
            self.assertEqual(before, (self.f.root / 'project.json').read_bytes())

    def test_invalid_actual_regions_archive_raw_without_accepting_panels(self):
        batch = self.start(self.plan(2))
        raw, regions = self.image(2)
        invalid = [{'p1': regions['p1']}, {**regions, 'extra': [0, 0, 1, 1]},
                   {**regions, 'p2': [1800, 0, 900, 600]},
                   {**regions, 'p2': [0, 0, 900, 600]},
                   {**regions, 'p2': [900.0, 0, 900, 600]}]
        for mapping in invalid:
            with self.subTest(mapping=mapping), self.assertRaises(cp.GateError):
                self.split(batch, raw, mapping)
            record = cp.project_load(self.f.root)['art']['batches'][batch['batch_id']]
            self.assertTrue(cp.inside(self.f.root, record['raw']['path']).is_file())
            self.assertEqual({}, record['crops'])
            self.assertEqual(0, self.f.invoke('status')['panels_accepted'])
        self.split(batch, raw, regions)
        self.assertEqual(2, self.f.invoke('status')['preflight_counts']['attempts_recorded'])

    def test_undersized_panel_is_rejected_without_discarding_usable_sibling(self):
        batch = self.start(self.plan(2))
        raw, regions = self.image(2)
        result = self.split(batch, raw, {**regions, 'p2': [900, 0, 899, 599]})
        self.assertEqual(['p1'], [p['panel_id'] for p in result['panels']])
        self.assertEqual(['p2'], [p['panel_id'] for p in result['rejected_panels']])
        self.accept(result['panels'][0])
        with self.assertRaisesRegex(cp.GateError, 'extracted crop'):
            self.f.invoke('qa-inputs', panel='p2', attempt=1, file=raw)
        corrected = self.split(batch, raw, regions)
        self.assertEqual([], corrected['rejected_panels'])
        self.assertEqual(result['panels'][0]['sha256'], corrected['panels'][0]['sha256'])
        self.accept(corrected['panels'][1])
        self.assertEqual(2, self.f.invoke('status')['panels_accepted'])

    def test_detail_review_is_required_even_when_pixel_dimensions_pass(self):
        batch = self.start(self.plan(1))
        raw, regions = self.image(1)
        item = self.split(batch, raw, regions)['panels'][0]
        report = cp.load_json(self.f.panel_qa('p1', 1, item['file']))
        report['detail_notes'] = ''
        with self.assertRaisesRegex(cp.GateError, 'detail_notes'):
            self.f.invoke('finish-panel', panel='p1', attempt=1, file=item['file'],
                          qa=self.f.json_file(report))

    def test_single_panel_uses_full_image_and_needs_registered_crop(self):
        batch = self.start(self.plan(1))
        raw, regions = self.image(1)
        item = batch['panels'][0]
        with self.assertRaisesRegex(cp.GateError, 'extracted crop'):
            self.f.invoke('qa-inputs', panel='p1', attempt=item['attempt'], file=raw)
        oversized = self.f.base / 'larger.png'
        Image.new('RGB', (1000, 700), 'white').save(oversized)
        with self.assertRaisesRegex(cp.GateError, 'whole image'):
            self.split(batch, str(oversized), regions)

    def test_raw_tampering_invalidates_accepted_crop_and_cannot_be_resplit(self):
        batch = self.start(self.plan(2))
        raw, regions = self.image(2)
        items = self.split(batch, raw, regions)['panels']
        self.accept(items[0])
        project = cp.project_load(self.f.root)
        archived = cp.inside(self.f.root, project['art']['batches'][batch['batch_id']]['raw']['path'])
        archived.write_bytes(archived.read_bytes() + b'tampered')
        status = self.f.invoke('status')
        self.assertEqual(0, status['panels_accepted'])
        self.assertIn('raw image', status['batches'][0]['integrity_error'])
        with self.assertRaisesRegex(cp.GateError, 'raw image'):
            self.split(batch, raw, regions)
        with self.assertRaisesRegex(cp.GateError, 'raw image'):
            self.f.invoke('qa-inputs', panel='p2', attempt=1, file=items[1]['file'])

    def test_old_qa_cannot_pass_a_new_attempt_and_wrong_image_is_rejected(self):
        f = self.f
        first = self.start(self.plan(1))
        raw, regions = self.image(1)
        item = self.split(first, raw, regions)['panels'][0]
        old_qa = f.panel_qa('p1', 1, item['file'])
        f.invoke('fail-panel', panel='p1', attempt=1, reason='机械返修')
        second = self.start(self.plan(1))
        raw, regions = self.image(1)
        repaired = self.split(second, raw, regions)['panels'][0]
        with self.assertRaisesRegex(cp.GateError, 'crop SHA256'):
            f.invoke('qa-inputs', panel='p1', attempt=2, file=f.image_file('yellow'))
        with self.assertRaisesRegex(cp.GateError, 'attempt_bindings'):
            f.invoke('finish-panel', panel='p1', attempt=2, file=repaired['file'], qa=old_qa)

    def test_crop_regions_and_source_cannot_change_after_split(self):
        batch = self.start(self.plan(2))
        raw, regions = self.image(2)
        self.split(batch, raw, regions)
        with self.assertRaisesRegex(cp.GateError, 'immutable'):
            self.split(batch, raw, {'p1': regions['p2'], 'p2': regions['p1']})
        other = self.f.image_file('yellow')
        with self.assertRaisesRegex(cp.GateError, 'different raw image'):
            self.split(batch, other, regions)

    def test_stale_batch_can_archive_and_extract_but_cannot_pass_qa(self):
        f = self.f
        batch = self.start(self.plan(2))
        script = cp.project_load(f.root)['script']
        script['panels'][0]['action'] += '变更动作'
        f.invoke('set-script', file=f.json_file(script))
        raw, regions = self.image(2)
        result = self.split(batch, raw, regions)
        with self.assertRaises(cp.GateError):
            f.invoke('qa-inputs', panel='p1', attempt=1, file=result['panels'][0]['file'])
        for item in result['panels']:
            f.invoke('fail-panel', panel=item['panel_id'], attempt=1, reason='输入已过期', outcome='stale')
        self.assertEqual(0, f.invoke('status')['preflight_counts']['pending_batches'])

    def test_preflight_estimates_group_calls_separately_from_panel_attempts(self):
        f = self.f
        plan = f.json_file({'batches': [self.plan(2), self.plan(2, ['p3', 'p4'])]})
        before = (f.root / 'project.json').read_bytes()
        counts = f.invoke('preflight', plan=plan)['preflight_counts']
        self.assertEqual(2, counts['planned_generation_calls'])
        self.assertEqual(0, counts['ungrouped_panels_remaining'])
        self.assertEqual(before, (f.root / 'project.json').read_bytes())
        self.start(self.plan(2))
        counts = f.invoke('preflight', plan=plan)['preflight_counts']
        self.assertEqual(1, counts['generation_batches_recorded'])
        self.assertEqual(2, counts['attempts_recorded'])
        self.assertEqual(1, counts['pending_batches'])
        self.assertEqual(1, counts['planned_generation_calls'])
        self.assertEqual(0, counts['batches_with_raw'])

    def test_old_schemas_and_old_cli_are_rejected_without_migration(self):
        f = self.f
        project = cp.project_load(f.root)
        for version in (1, 2, 3, 4):
            project['schema_version'] = version
            cp.atomic_json(f.root / 'project.json', project)
            before = (f.root / 'project.json').read_bytes()
            with self.assertRaisesRegex(cp.GateError, 'only schema v5'):
                f.invoke('status')
            self.assertEqual(before, (f.root / 'project.json').read_bytes())
        result = subprocess.run([sys.executable, '-B', str(Path(cp.__file__)), 'begin-panel',
                                 '--project', str(f.root)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(2, result.returncode)
        self.assertIn('invalid choice', result.stderr)

    def test_new_project_runs_eight_panel_batch_through_complete_exports(self):
        f = self.fresh(count=8)
        batch = self.start(self.plan(8), f)
        raw, regions = self.image(8, f)
        for item in self.split(batch, raw, regions, f)['panels']:
            self.accept(item, f)
        f.invoke('compose', font=None)
        f.review_and_export()
        verified = f.invoke('verify-export')
        self.assertTrue(verified['ok'])
        project = cp.project_load(f.root)
        report = {'input_hash': project['layout']['input_hash'],
                  'checks': {k: True for k in ('source_scope', 'story_complete', 'visual_consistency', 'exports_opened')},
                  'evidence': '机械夹具，只验证接口与实际导出文件完整性，不是人工看图结论。'}
        f.invoke('complete', file=f.json_file(report))
        self.assertTrue(f.invoke('status')['complete'])
        self.assertEqual(5, project['schema_version'])

    def test_canvas_budget_is_required_and_rejections_do_not_register_attempts(self):
        valid = self.plan(2)
        before = (self.f.root / 'project.json').read_bytes()
        for canvas in (None, [], [True, 600], [1800, 600.0], [0, 600],
                       [1799, 600], [1800, 599], [12001, 600], [6000, 6000]):
            plan = copy.deepcopy(valid)
            if canvas is None:
                del plan['canvas_pixels']
            else:
                plan['canvas_pixels'] = canvas
            with self.subTest(canvas=canvas), self.assertRaises(cp.GateError):
                self.start(plan)
            self.assertEqual(before, (self.f.root / 'project.json').read_bytes())
        result = self.f.invoke('preflight', plan=self.f.json_file(valid))
        self.assertEqual([1800, 600], result['planned_batches'][0]['required_canvas_pixels'])
        self.assertEqual(['p1', 'p2'], result['planned_batches'][0]['active_panel_ids'])
        self.assertFalse(result['planned_batches'][0]['requires_repack'])
        self.assertEqual(before, (self.f.root / 'project.json').read_bytes())

    def test_unequal_regions_and_fractional_rows_have_correct_capacity(self):
        plan = self.plan(2)
        plan['panels'][0]['target_region'] = [0, 0, .6, 1]
        plan['panels'][1]['target_region'] = [.6, 0, .4, 1]
        plan['canvas_pixels'] = [2250, 600]
        result = self.f.invoke('preflight', plan=self.f.json_file(plan))
        self.assertEqual([2250, 600], result['planned_batches'][0]['required_canvas_pixels'])
        f = self.fresh(count=6)
        result = f.invoke('preflight', plan=f.json_file(self.plan(6)))
        self.assertEqual([1800, 1800], result['planned_batches'][0]['required_canvas_pixels'])
        plan['canvas_pixels'][0] -= 1
        with self.assertRaisesRegex(cp.GateError, 'required_canvas_pixels'):
            self.start(plan)

    def test_multiple_failed_panels_are_repacked_without_redrawing_successes(self):
        batch = self.start(self.plan(4))
        raw, regions = self.image(4)
        items = self.split(batch, raw, regions)['panels']
        self.accept(items[0])
        self.accept(items[2])
        for pid in ('p2', 'p4'):
            self.f.invoke('fail-panel', panel=pid, attempt=1, reason='机械返修')
        retry = self.start(self.plan(2, ['p2', 'p4']))
        self.assertEqual(['p2', 'p4'], [p['panel_id'] for p in retry['panels']])
        self.assertEqual([2, 2], [p['attempt'] for p in retry['panels']])
        raw, regions = self.image(2)
        repair_regions = {'p2': regions['p1'], 'p4': regions['p2']}
        for item in self.split(retry, raw, repair_regions)['panels']:
            self.accept(item)
        project = cp.project_load(self.f.root)
        self.assertEqual(4, self.f.invoke('status')['panels_accepted'])
        self.assertEqual([1, 2, 1, 2], [len(project['art']['panels'][f'p{i}']) for i in range(1, 5)])
        self.assertEqual(items[0]['sha256'], project['art']['panels']['p1'][0]['sha256'])

    def test_canvas_tampering_invalidates_batch_hash(self):
        batch = self.start(self.plan(2))
        raw, regions = self.image(2)
        items = self.split(batch, raw, regions)['panels']
        self.accept(items[0])
        project = cp.project_load(self.f.root)
        project['art']['batches'][batch['batch_id']]['canvas_pixels'][0] += 1
        cp.save(self.f.root, project)
        self.assertEqual(0, self.f.invoke('status')['panels_accepted'])
        with self.assertRaisesRegex(cp.GateError, 'plan or prompt changed'):
            self.split(batch, raw, regions)

    def test_planned_canvas_is_a_target_and_actual_crop_pixels_decide_acceptance(self):
        plan = self.plan(2)
        plan['canvas_pixels'] = [3600, 1200]
        batch = self.start(plan)
        raw, regions = self.image(2)
        result = self.split(batch, raw, regions)
        self.assertEqual([3600, 1200], batch['canvas_pixels'])
        self.assertEqual([1800, 600], [result['raw']['width'], result['raw']['height']])
        for item in result['panels']:
            self.accept(item)
        self.assertEqual(2, self.f.invoke('status')['panels_accepted'])

    def test_cross_page_batch_preserves_final_page_order(self):
        f = fixtures.PipelineTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.locked()
        f.reference()
        plan = self.plan(2)
        batch = self.start(plan, f)
        raw, regions = self.image(2, f)
        for item in self.split(batch, raw, regions, f)['panels']:
            self.accept(item, f)
        self.assertEqual([['p1'], ['p2']], [p['panel_ids'] for p in cp.project_load(f.root)['script']['pages']])
        self.assertEqual(2, f.invoke('status')['panels_accepted'])

    def test_cli_status_and_preflight_expose_the_same_read_only_plan(self):
        plan = self.f.json_file(self.plan(2))
        before = (self.f.root / 'project.json').read_bytes()
        responses = []
        for command in ('status', 'preflight'):
            result = subprocess.run([sys.executable, '-B', str(Path(cp.__file__)), command,
                                     '--project', str(self.f.root), '--plan', plan],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(0, result.returncode, result.stderr)
            responses.append(json.loads(result.stdout)['planned_batches'])
        self.assertEqual(responses[0], responses[1])
        self.assertEqual([1800, 600], responses[0][0]['required_canvas_pixels'])
        self.assertEqual(before, (self.f.root / 'project.json').read_bytes())
