"""Generation accounting and lossless panel extraction; never calls an image model."""
from __future__ import annotations

import copy
import json
import math
import shutil
import uuid
from pathlib import Path

from comic_layout import core, _allocated_widths, _check_raster_dimensions


def display_width(project, panel_id):
    """Use the same margins, gutters and rounding as the page compositor."""
    c = core()
    width = project['script']['style'].get('width', 1536)
    margin, gutter = max(28, width // 28), max(18, width // 55)
    for page in project['script']['pages']:
        for index, row in enumerate(c.page_rows(page)):
            if panel_id in row:
                available = width - 2 * margin - gutter * (len(row) - 1)
                return _allocated_widths(available, c.page_row_weights(page, index))[row.index(panel_id)]
    raise c.GateError('Panel is missing from page layout: ' + panel_id)


def _rect(rect, normalized=False):
    c = core()
    valid = (isinstance(rect, list) and len(rect) == 4 and
             all(type(v) in (int, float) and math.isfinite(v) for v in rect))
    if not valid:
        raise c.GateError('Region must be [x,y,width,height] with finite numbers.')
    x, y, width, height = rect
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise c.GateError('Region must have nonnegative origin and positive dimensions.')
    if normalized:
        if x + width > math.nextafter(1.0, math.inf) or y + height > math.nextafter(1.0, math.inf):
            raise c.GateError('Target region exceeds the normalized canvas.')
    elif any(type(v) is not int for v in rect):
        raise c.GateError('Actual regions must use integer pixels.')
    return rect


def _no_overlap(items, field):
    c = core()
    for index, item in enumerate(items):
        x, y, w, h = item[field]
        for other in items[index + 1:]:
            ox, oy, ow, oh = other[field]
            right, bottom = min(x + w, ox + ow), min(y + h, oy + oh)
            if field == 'target_region':
                # Adjacent fractional rows can differ by one ULP after addition.
                right, bottom = math.nextafter(right, -math.inf), math.nextafter(bottom, -math.inf)
            if max(x, ox) < right and max(y, oy) < bottom:
                raise c.GateError('Panel regions overlap.')


def plan_capacity(items, canvas):
    """Check a planning target, never a promise about model output dimensions."""
    c = core()
    if (not isinstance(canvas, list) or len(canvas) != 2 or
            any(type(v) is not int or v <= 0 for v in canvas)):
        raise c.GateError('canvas_pixels must contain positive integer width and height.')
    _check_raster_dimensions(*canvas, 'Batch canvas', 'canvas_pixels')
    required = [1, 1]
    for item in items:
        for axis in (0, 1):
            quotient = item['min_pixels'][axis] / item['target_region'][axis + 2]
            if not math.isfinite(quotient):
                raise c.GateError('Target region requires a canvas beyond raster safety limits.')
            # Remove one floating-point ULP so e.g. thirds do not require an extra pixel.
            required[axis] = max(required[axis], math.ceil(math.nextafter(quotient, -math.inf)))
    _check_raster_dimensions(*required, 'Required batch canvas', 'required_canvas_pixels')
    if any(actual < minimum for actual, minimum in zip(canvas, required)):
        raise c.GateError(f'canvas_pixels cannot fit min_pixels in target regions; '
                          f'required_canvas_pixels={required}. Repack or reduce the group.')
    return required


def validate_plan(project, data):
    c = core()
    items = data.get('panels') if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise c.GateError('Batch plan must contain a nonempty panels array.')
    panels = {p['id']: p for p in project['script']['panels']}
    ids = []
    for item in items:
        if not isinstance(item, dict) or not c.nonempty(item.get('panel_id')) or item['panel_id'] not in panels:
            raise c.GateError('Batch plan contains an unknown panel ID.')
        pid = item['panel_id']
        ids.append(pid)
        _rect(item.get('target_region'), normalized=True)
        minimum = item.get('min_pixels')
        if (not isinstance(minimum, list) or len(minimum) != 2 or
                any(type(v) is not int or v <= 0 for v in minimum)):
            raise c.GateError('min_pixels must contain positive integer width and height.')
        _check_raster_dimensions(*minimum, f'Panel {pid}', 'min_pixels')
        width = display_width(project, pid)
        ratio = panels[pid].get('aspect_ratio', minimum[0] / minimum[1])
        if minimum[0] < width or minimum[1] < round(width / ratio):
            raise c.GateError(f'Panel {pid}: min_pixels is below its composed display size.')
    if len(ids) != len(set(ids)):
        raise c.GateError('Batch plan contains duplicate panels.')
    order = [p['id'] for p in project['script']['panels']]
    if [pid for pid in order if pid in ids] != ids:
        raise c.GateError('Batch panels must follow script reading order.')
    _no_overlap(items, 'target_region')
    if len(items) == 1 and items[0]['target_region'] != [0, 0, 1, 1]:
        raise c.GateError('Single-panel plans must use the whole canvas region.')
    plan_capacity(items, data.get('canvas_pixels'))
    return copy.deepcopy(items)


def _supporting_paths(root, project, panel):
    c = core()
    script = project['script']
    scene = next((s for s in script['scenes'] if s['id'] == panel['scene_id']), {})
    setting = next((s for s in script['settings'] if s['id'] == scene.get('setting_id')), {})
    prop_ids = set(panel.get('prop_ids', [])) | set(scene.get('prop_ids', []))
    entities = [scene, setting] + [p for p in script.get('props', []) if p.get('id') in prop_ids]
    return list(dict.fromkeys(str(c.inside(root, path)) for entity in entities
                             for path in entity.get('reference_paths', [])))


def begin_batch(root, project, plan_path, prompt_path):
    c = core()
    c.assert_script_lock(project, root)
    plan_file = c.resolve_file_arg(plan_path, project_dir=root) if hasattr(c, 'resolve_file_arg') else Path(plan_path)
    prompt_file = c.resolve_file_arg(prompt_path, project_dir=root) if hasattr(c, 'resolve_file_arg') else Path(prompt_path)
    plan = c.load_json(plan_file)
    items = validate_plan(project, plan)
    canvas = copy.deepcopy(plan['canvas_pixels'])
    required = plan_capacity(items, canvas)
    panels = {p['id']: p for p in project['script']['panels']}
    ready, reused, reference_map, supporting = [], [], {}, []

    # Check for existing pending batch matching this exact plan (Idempotency)
    plan_candidate_hash = c.digest({'canvas_pixels': canvas, 'panels': items})
    for existing_batch_id, existing_batch in project.get('art', {}).get('batches', {}).items():
        if (isinstance(existing_batch, dict) and
                existing_batch.get('plan_hash') == plan_candidate_hash and
                existing_batch.get('created_script_hash') == c.digest(project['script']) and
                existing_batch.get('raw') is None):
            existing_panels = existing_batch.get('panels', [])
            all_pending = True
            for ep in existing_panels:
                pid = ep['panel_id']
                atts = project['art']['panels'].get(pid, [])
                if not any(a.get('status') == 'pending' and a.get('batch_id') == existing_batch_id for a in atts if isinstance(a, dict)):
                    all_pending = False
                    break
            if all_pending and existing_panels:
                target = c.inside(root, existing_batch['prompt_path'])
                paths = list(dict.fromkeys([r['path'] for r in existing_batch.get('reference_bindings', [])] +
                                           existing_batch.get('supporting_reference_paths', [])))
                return {'batch_id': existing_batch_id, 'already_accepted': False, 'generation_required': True,
                        'canvas_pixels': existing_batch['canvas_pixels'], 'required_canvas_pixels': required,
                        'panels': existing_batch['panels'], 'reused': existing_batch.get('reused', []),
                        'prompt': str(target), 'referenced_image_paths': paths,
                        'reference_bindings': existing_batch['reference_bindings'],
                        'supporting_reference_paths': existing_batch['supporting_reference_paths']}

    # Validate the entire group before writing prompts, attempts or batch state.
    for item in items:
        pid = item['panel_id']
        panel = panels[pid]
        accepted = c.accepted_panel(root, project, panel)
        if accepted:
            reused.append({'panel_id': pid, 'path': str(c.inside(root, accepted['path']))})
            continue
        references = c.select_references(root, project, panel)
        fingerprint = c.render_hash(root, project, panel, references)
        attempts = project['art']['panels'].get(pid, [])
        if any(a['status'] == 'pending' for a in attempts):
            raise c.GateError(f'Panel {pid}: unfinished attempt exists; finish/fail it first.')
        art_attempts = [a for a in attempts if isinstance(a, dict) and a.get('render_hash') == fingerprint
                        and not a.get('is_tool_exception') and a.get('failure_category') != 'tool_exception']
        if len(art_attempts) >= 3:
            raise c.GateError(f'Three attempts exhausted for panel {pid}; intervention is required.')
        ready.append({**item, 'attempt': len(attempts) + 1, 'render_hash': fingerprint,
                      'reference_ids': [r['id'] for r in references]})
        for reference in references:
            reference_map[reference['id']] = {
                'id': reference['id'], 'path': str(c.inside(root, reference['path'])),
                'purpose': reference['purpose'], 'subjects': reference['subjects']}
        supporting.extend(_supporting_paths(root, project, panel))
    if not ready:
        return {'already_accepted': True, 'batch_id': None, 'panels': [], 'reused': reused,
                'generation_required': False}
    if reused:
        raise c.GateError('Batch contains accepted and unfinished panels; repack only unfinished '
                          'panels and rewrite the prompt before begin-batch. No attempts registered.')
    prompt = Path(prompt_file).read_text(encoding='utf-8-sig')
    if not c.nonempty(prompt):
        raise c.GateError('Persist a real batch drawing prompt before generation.')
    prompt = ('本次只绘制以下实际画格及格区；不绘制已复用画格，不在画面中写画格 ID、对白或标签。\n'
              + '画布规划目标（不是工具尺寸保证）：' + json.dumps(canvas) + '\n'
              + json.dumps([{key: item[key] for key in ('panel_id', 'target_region', 'min_pixels')}
                            for item in ready], ensure_ascii=False, indent=2)
              + '\n以下提示词的内容仅应用于上述实际画格，其他画格说明不执行：\n' + prompt)
    batch_id = 'batch-' + uuid.uuid4().hex[:16]
    relative = f'prompts/{batch_id}.txt'
    target = c.inside(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(prompt, encoding='utf-8')
    batch = {'id': batch_id, 'panels': ready, 'reused': reused,
             'canvas_pixels': canvas, 'requested_plan': {'canvas_pixels': canvas, 'panels': items},
             'plan_hash': c.digest({'canvas_pixels': canvas, 'panels': ready}),
             'prompt_path': relative, 'prompt_sha256': c.sha_file(target),
             'created_script_hash': c.digest(project['script']), 'reference_bindings': list(reference_map.values()),
             'supporting_reference_paths': list(dict.fromkeys(supporting)), 'at': c.now(),
             'raw': None, 'crops': {}}
    for item in ready:
        project['art']['panels'].setdefault(item['panel_id'], []).append({
            'number': item['attempt'], 'status': 'pending', 'render_hash': item['render_hash'],
            'batch_id': batch_id, 'created_script_hash': batch['created_script_hash'],
            'prompt_path': relative, 'prompt_sha256': batch['prompt_sha256'],
            'reference_ids': item['reference_ids'], 'at': batch['at']})
    project['art']['batches'][batch_id] = batch
    c.save(root, project)
    paths = list(dict.fromkeys([r['path'] for r in reference_map.values()] + supporting))
    return {'batch_id': batch_id, 'already_accepted': False, 'generation_required': True,
            'canvas_pixels': canvas, 'required_canvas_pixels': required,
            'panels': ready, 'reused': reused, 'prompt': str(target),
            'referenced_image_paths': paths, 'reference_bindings': batch['reference_bindings'],
            'supporting_reference_paths': batch['supporting_reference_paths']}


def _batch(root, project, batch_id):
    c = core()
    batch = project['art']['batches'].get(batch_id)
    if not isinstance(batch, dict):
        raise c.GateError('Batch ID missing.')
    prompt = c.inside(root, batch['prompt_path'])
    if (c.digest({'canvas_pixels': batch['canvas_pixels'], 'panels': batch['panels']}) != batch['plan_hash'] or not prompt.is_file() or
            c.sha_file(prompt) != batch['prompt_sha256']):
        raise c.GateError('Batch plan or prompt changed after registration.')
    if batch.get('raw'):
        path = c.inside(root, batch['raw']['path'])
        if not path.is_file() or c.sha_file(path) != batch['raw']['sha256']:
            raise c.GateError('Batch raw image missing or changed.')
    return batch


def split_batch(root, project, batch_id, file_path, regions_path):
    from PIL import Image
    c = core()
    # Archiving/extraction may recover an old output even with a stale script lock.
    # QA and acceptance still require the current volume lock and input hashes.
    batch = _batch(root, project, batch_id)
    file_resolved = c.resolve_file_arg(file_path, project_dir=root) if hasattr(c, 'resolve_file_arg') else Path(file_path)
    regions_resolved = c.resolve_file_arg(regions_path, project_dir=root) if hasattr(c, 'resolve_file_arg') else Path(regions_path)
    original = Path(file_resolved).resolve()
    source_sha = c.sha_file(original)
    if batch['raw'] and batch['raw']['sha256'] != source_sha:
        raise c.GateError('Batch already has a different raw image; register a new attempt for a new generation.')
    with Image.open(original) as image:
        _check_raster_dimensions(image.width, image.height, 'Batch raw image', 'actual_size')
        if getattr(image, 'n_frames', 1) != 1 or image.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P'):
            raise c.GateError('Batch extraction requires a single-frame 8-bit image.')
        image.load()
        if not batch['raw']:
            relative = f'art/raw/{batch_id}{original.suffix.lower()}'
            target = c.inside(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if original != target:
                shutil.copy2(original, target)
            if c.sha_file(target) != source_sha:
                raise c.GateError('Raw image changed while being archived.')
            batch['raw'] = {'path': relative, 'sha256': source_sha, 'width': image.width,
                            'height': image.height, 'at': c.now()}
            c.save(root, project)
        regions = c.load_json(regions_resolved)
        if not isinstance(regions, dict) or set(regions) != {p['panel_id'] for p in batch['panels']}:
            raise c.GateError('Actual regions must cover every generated panel exactly once, with no extra IDs.')
        specs = []
        for item in batch['panels']:
            pid = item['panel_id']
            rect = _rect(regions[pid])
            x, y, w, h = rect
            if x + w > image.width or y + h > image.height:
                raise c.GateError(f'Panel {pid}: region exceeds raw image bounds.')
            reason = None
            if w < item['min_pixels'][0] or h < item['min_pixels'][1]:
                reason = 'Crop is below min_pixels; reduce the group size or generate alone.'
            specs.append({'panel_id': pid, 'region': rect, 'rejection': reason})
        _no_overlap(specs, 'region')
        if len(specs) == 1 and specs[0]['region'] != [0, 0, image.width, image.height]:
            raise c.GateError('Single-panel batches must use the whole image region.')
        if batch['crops']:
            if any(s['panel_id'] in batch['crops'] and
                   batch['crops'][s['panel_id']]['region'] != s['region'] for s in specs):
                raise c.GateError('Registered crop regions are immutable; do not replace reviewed panel inputs.')
            for crop in batch['crops'].values():
                path = c.inside(root, crop['path'])
                if not path.is_file() or c.sha_file(path) != crop['sha256']:
                    raise c.GateError('Registered crop missing or changed; restore the recorded file before reuse.')
        crops = dict(batch['crops'])
        rejected = []
        for spec in specs:
            pid = spec['panel_id']
            if spec['rejection']:
                rejected.append({'panel_id': pid, 'reason': spec['rejection']})
                continue
            if pid in crops:
                continue
            x, y, w, h = spec['region']
            relative = f'art/crops/{batch_id}/{pid}.png'
            target = c.inside(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            image.crop((x, y, x + w, y + h)).save(target, format='PNG')
            crops[pid] = {'path': relative, 'sha256': c.sha_file(target), 'region': spec['region'],
                          'width': w, 'height': h}
        batch['crops'] = crops
        batch['extraction_issues'] = rejected
        batch['production_metrics'] = {
            'canvas_pixels': [image.width, image.height],
            'extracted_count': len(crops),
            'rejected_count': len(rejected),
            'panel_count': len(batch['panels'])
        }
        c.save(root, project)
    return _split_result(root, batch)


def _split_result(root, batch):
    c = core()
    return {'batch_id': batch['id'], 'raw': batch['raw'],
            'rejected_panels': batch.get('extraction_issues', []), 'panels': [
        {**item, **batch['crops'][item['panel_id']],
         'file': str(c.inside(root, batch['crops'][item['panel_id']]['path']))}
        for item in batch['panels'] if item['panel_id'] in batch['crops']]}


def validate_panel_file(root, project, panel, attempt, file_path):
    from PIL import Image
    c = core()
    batch = _batch(root, project, attempt.get('batch_id'))
    pid = panel['id']
    item = next((p for p in batch['panels'] if p['panel_id'] == pid), None)
    crop = batch['crops'].get(pid)
    if (not item or item['attempt'] != attempt['number'] or item['render_hash'] != attempt['render_hash'] or
            not crop or not batch['raw']):
        raise c.GateError('Panel must bind its registered batch attempt and extracted crop.')
    extracted = c.inside(root, crop['path'])
    if not extracted.is_file() or c.sha_file(extracted) != crop['sha256'] or c.sha_file(Path(file_path)) != crop['sha256']:
        raise c.GateError('Submitted panel image does not match its registered crop SHA256.')
    with Image.open(extracted) as image:
        if image.size != (crop['width'], crop['height']):
            raise c.GateError('Panel crop dimensions changed.')
        width = display_width(project, pid)
        ratio = panel.get('aspect_ratio', image.width / image.height)
        height = max(1, round(width / ratio))
        scale = min(width / image.width, height / image.height)
        if (image.width < item['min_pixels'][0] or image.height < item['min_pixels'][1] or scale > 1):
            raise c.GateError('Panel native pixels cannot satisfy the current composed display size without enlargement.')


def batch_summary(root, project, accepted, plan_path=None):
    c = core()
    art = project.get('art') if isinstance(project.get('art'), dict) else {}
    ledger = art.get('panels') if isinstance(art.get('panels'), dict) else {}
    batches = art.get('batches') if isinstance(art.get('batches'), dict) else {}
    summaries = []
    for bid, batch in batches.items():
        if not isinstance(batch, dict) or not isinstance(batch.get('panels'), list):
            continue
        states = []
        for item in batch['panels']:
            if not isinstance(item, dict) or not c.nonempty(item.get('panel_id')) or 'attempt' not in item:
                continue
            attempts = ledger.get(item['panel_id'], [])
            attempts = attempts if isinstance(attempts, list) else []
            found = next((a for a in attempts if isinstance(a, dict) and a.get('number') == item['attempt']), {})
            states.append({'panel_id': item['panel_id'], 'attempt': item['attempt'], 'status': found.get('status')})
        try:
            _batch(root, project, bid)
            integrity_error = None
        except (c.GateError, KeyError, TypeError, OSError) as error:
            integrity_error = str(error)
        summaries.append({'batch_id': bid, 'panels': states, 'raw': batch.get('raw'),
                          'canvas_pixels': batch.get('canvas_pixels'),
                          'prompt_path': batch.get('prompt_path'), 'integrity_error': integrity_error})
    counts = {'generation_batches_recorded': len(batches),
              'batches_with_raw': sum(bool(b.get('raw')) for b in summaries),
              'pending_batches': sum(any(p['status'] == 'pending' for p in b['panels']) for b in summaries),
              'planned_generation_calls': None, 'ungrouped_panels_remaining': None}
    planned = []
    if plan_path:
        data = c.load_json(plan_path)
        plans = data.get('batches', [data]) if isinstance(data, dict) else None
        if not isinstance(plans, list):
            raise c.GateError('Preflight plan must contain a batches array or a single batch plan.')
        covered = set()
        calls = 0
        pending = {pid for pid, attempts in ledger.items() if isinstance(attempts, list)
                   and any(isinstance(a, dict) and a.get('status') == 'pending' for a in attempts)}
        for plan in plans:
            items = validate_plan(project, plan)
            ids = {p['panel_id'] for p in items}
            if ids & covered:
                raise c.GateError('Preflight groups cannot share panel IDs.')
            covered.update(ids)
            calls += bool(ids - set(accepted) - pending)
            reused_ids = [p['panel_id'] for p in items if p['panel_id'] in accepted]
            active_ids = [p['panel_id'] for p in items if p['panel_id'] not in accepted and p['panel_id'] not in pending]
            pending_ids = [p['panel_id'] for p in items if p['panel_id'] not in accepted and p['panel_id'] in pending]
            planned.append({'canvas_pixels': plan['canvas_pixels'],
                            'required_canvas_pixels': plan_capacity(items, plan['canvas_pixels']),
                            'reused_panel_ids': reused_ids, 'active_panel_ids': active_ids,
                            'pending_panel_ids': pending_ids,
                            'requires_repack': bool(reused_ids and len(reused_ids) != len(items))})
        all_ids = {p['id'] for p in project['script']['panels']}
        counts.update(planned_generation_calls=calls,
                      ungrouped_panels_remaining=len(all_ids - covered - set(accepted) - pending))
    return counts, summaries, planned
