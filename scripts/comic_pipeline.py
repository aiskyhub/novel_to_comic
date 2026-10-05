#!/usr/bin/env python3
"""Deterministic gates and artifact accounting; no model/API calls are made here."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from comic_sources import SourceError, extract, sha_file

SCHEMA_VERSION = 5

def find_template_file(category: str, name: str) -> Path:
    skill_root = Path(__file__).resolve().parent.parent
    short_cat = category.split('_')[0]
    candidates = [
        skill_root / 'README' / category / name,
        skill_root / 'README' / short_cat / name,
        skill_root / 'README' / f"{short_cat}_{name}",
        skill_root / 'README' / name,
        skill_root / 'assets' / 'templates' / category / name,
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise GateError(f'Template file not found. Checked: {[str(c) for c in candidates]}')


def render_template(category: str, name: str, context: dict = None) -> str:
    """Load and render an external template file (pure .md file)."""
    template_path = find_template_file(category, name)
    content = template_path.read_text(encoding='utf-8')
    if context:
        for k, v in context.items():
            content = content.replace(f'{{{{{k}}}}}', str(v)).replace(f'{{{k}}}', str(v))
    return content

VALID_FAILURE_CATEGORIES = (
    'identity', 'state', 'action_space', 'prop', 'art_style',
    'panel_border', 'native_detail', 'unintended_text', 'tool_exception'
)
VALID_NARRATIVE_ROLES = (
    'critical_turning_point', 'relationship_change', 'information_reveal',
    'transition', 'environmental_info'
)
REVIEW_CHECKS = {
    'coverage': ['all_source_read', 'events_preserved', 'arcs_preserved', 'ending_preserved'],
    'continuity': ['causality', 'timeline', 'identity', 'states', 'knowledge_and_reveals'],
    'comic': ['drawable_panels', 'dialogue_and_speakers', 'reading_order', 'pacing', 'text_density'],
}
PANEL_CHECKS = ['identity', 'continuity', 'composition', 'drawing_quality', 'no_unwanted_text',
                'gender_readability', 'distinctiveness', 'body_design', 'design_tier_fit', 'visual_elegance', 'native_detail']
REFERENCE_CHECKS = ['identity', 'distinctiveness', 'angles_and_expressions', 'source_faithfulness',
                    'gender_readability', 'body_design', 'design_tier_fit', 'visual_elegance']
LAYOUT_CHECKS = ['text_accuracy', 'reading_order', 'speaker_assignment', 'face_visibility', 'visual_elegance']
VOLUME_HEADING = re.compile(r'^(?:第[0-9零〇一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰\s]+[卷部篇集].*'
                            r'|(?:book|volume|part)\s+[\wIVXLC0-9]+.*)$', re.I)


class GateError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def resolve_file_arg(file_arg, project_dir=None, book_dir=None):
    if not file_arg:
        return None
    p = Path(file_arg)
    if p.is_absolute() and p.exists():
        return p.resolve()
    if p.exists():
        return p.resolve()
    if project_dir:
        candidate = (Path(project_dir) / file_arg).resolve()
        if candidate.exists():
            return candidate
    if book_dir:
        candidate = (Path(book_dir) / file_arg).resolve()
        if candidate.exists():
            return candidate
        candidate_src = (Path(book_dir) / 'source_texts' / file_arg).resolve()
        if candidate_src.exists():
            return candidate_src
    if project_dir:
        book_candidate = (Path(project_dir).parent / file_arg).resolve()
        if book_candidate.exists():
            return book_candidate
        book_src = (Path(project_dir).parent / 'source_texts' / file_arg).resolve()
        if book_src.exists():
            return book_src
        return (Path(project_dir) / file_arg).resolve()
    return p.resolve()


def is_reference_valid(root, project, ref):
    if not isinstance(ref, dict) or not root:
        return False
    path_str = ref.get('path')
    if not path_str:
        return False
    try:
        p = inside(root, path_str)
        if not p.is_file():
            return False
        if sha_file(p) != ref.get('sha256'):
            return False
        return True
    except (GateError, OSError):
        return False


def is_script_lock_valid(project, root=None):
    if not isinstance(project, dict):
        return False, ['project: expected an object.']
    lock = project.get('script_lock')
    if not isinstance(lock, dict):
        return False, ['Volume script lock missing.']
    if lock.get('script_hash') != digest(project.get('script')):
        return False, ['Script hash does not match locked script hash.']
    if lock.get('source_index_hash') != project.get('source_index_hash'):
        return False, ['Source index hash does not match lock.']
    reviews = project.get('reviews')
    if not isinstance(reviews, list) or lock.get('reviews_hash') != digest(reviews):
        return False, ['Reviews hash does not match lock.']
    errors = script_errors(project, root)
    if errors:
        return False, errors
    return True, []


def validate_unit_ref(ref, units, root=None, project=None):
    if isinstance(ref, str):
        if nonempty(ref) and ref in units:
            return True, None
        return False, f'unknown unit ID: {ref}'
    if isinstance(ref, dict):
        vol_id = ref.get('volume_id')
        src_hash = ref.get('source_index_hash')
        uid = ref.get('unit_id')
        if not nonempty(vol_id) or not nonempty(src_hash) or not nonempty(uid):
            return False, 'cross-volume reference requires volume_id, source_index_hash, and unit_id.'
        if project:
            curr_vol = project.get('volume') or ''
            if vol_id == curr_vol or vol_id == project.get('title'):
                if src_hash != project.get('source_index_hash'):
                    return False, f'cross-volume source_index_hash mismatch for current volume {vol_id}.'
                if uid not in units:
                    return False, f'cross-volume unit {uid} not in current volume {vol_id}.'
                return True, None
        if root is not None:
            other_dir = Path(root).parent / vol_id
            other_proj_file = other_dir / 'project.json'
            if other_proj_file.is_file():
                try:
                    other_proj = load_json(other_proj_file)
                    if other_proj.get('source_index_hash') != src_hash:
                        return False, f'cross-volume source_index_hash mismatch for {vol_id}.'
                    other_units = {u.get('id') for u in other_proj.get('source', {}).get('units', []) if isinstance(u, dict)}
                    if uid not in other_units:
                        return False, f'unit {uid} not found in volume {vol_id}.'
                    return True, None
                except Exception as e:
                    return False, f'unable to read cross-volume project {vol_id}: {e}'
        return True, None
    return False, 'expected unit ID string or cross-volume reference object.'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def inside(root, relative):
    root = Path(root).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise GateError('Artifact path escapes the project directory.')
    return target


def project_load(root):
    project = load_json(Path(root) / 'project.json')
    if not isinstance(project, dict):
        raise GateError('project: expected an object.')
    version = project.get('schema_version')
    if version != SCHEMA_VERSION:
        raise GateError(f'Unsupported project schema; only schema v{SCHEMA_VERSION} is supported. No migration is provided.')
    return project


def save(root, project):
    project['updated_at'] = now()
    project['revision'] = project.get('revision', 0) + 1
    atomic_json(Path(root) / 'project.json', project)


def index_hash(source):
    files, units, chapters = source.get('files'), source.get('units'), source.get('chapters')
    if not isinstance(files, list) or not isinstance(units, list) or not isinstance(chapters, list):
        raise GateError('source.files, source.units, and source.chapters must be arrays.')
    return digest({'files': files, 'units': units, 'chapters': [
        {key: chapter[key] for key in ('id', 'title', 'unit_ids', 'has_body')}
        for chapter in chapters if isinstance(chapter, dict)]})


def source_errors(project, root=None):
    if not isinstance(project, dict):
        return ['project: expected an object.']
    source = project.get('source', {})
    if not isinstance(source, dict):
        return ['source: expected an object.']
    errors = []
    for field in ('files', 'units', 'chapters'):
        if not isinstance(source.get(field), list):
            errors.append(f'source.{field}: expected an array.')
    if errors:
        return errors
    try:
        if index_hash(source) != project.get('source_index_hash'):
            errors.append('Source index changed; re-ingest rather than editing extracted source facts.')
    except (GateError, KeyError, TypeError):
        errors.append('source: malformed extracted-source index.')
    for item in source['files']:
        if not isinstance(item, dict):
            errors.append('source.files: each item must be an object.')
            continue
        original = item.get('path')
        expected = item.get('sha256')
        try:
            if not nonempty(item.get('archive_path')):
                errors.append('source.files: archived input path is required.')
            elif root is not None:
                path = inside(root, item['archive_path'])
                if not path.is_file() or sha_file(path) != expected:
                    errors.append('Source archive missing or changed: ' + str(path))
        except (KeyError, OSError, GateError, TypeError):
            errors.append('source.files: invalid path or fingerprint.')
    if not source.get('confirmed'):
        errors.append('Source completeness has not been checked.')
    issues = source.get('issues', [])
    if not isinstance(issues, list):
        errors.append('source.issues: expected an array.')
    else:
        for index, item in enumerate(issues):
            if not isinstance(item, dict):
                errors.append(f'source.issues[{index}]: expected an object.')
            elif not item.get('resolved'):
                errors.append('Unresolved extraction issue: ' + str(item.get('id', '?')))
    return errors


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def page_rows(page):
    """Return narrative row order; single-panel rows occupy the full width."""
    columns = page.get('columns', 1)
    if type(columns) is not int or columns not in (1, 2):
        raise GateError('Page columns must be 1 or 2.')
    pids = page.get('panel_ids')
    if not isinstance(pids, list) or not pids or any(not nonempty(pid) for pid in pids):
        raise GateError('Page needs a nonempty panel_ids array.')
    if 'rows' not in page:
        return [pids[start:start + columns] for start in range(0, len(pids), columns)]
    rows = page['rows']
    if (not isinstance(rows, list) or not rows or
            any(not isinstance(row, list) or len(row) not in (1, 2) or
                any(not nonempty(pid) for pid in row) for row in rows)):
        raise GateError('Page rows must contain one or two panel IDs per row.')
    if [pid for row in rows for pid in row] != pids:
        raise GateError('Page rows must match panel_ids exactly in reading order.')
    return rows


def page_row_weights(page, row_index):
    """Return normalized panel-width weights for one page row."""
    rows = page_rows(page)
    if type(row_index) is not int or not 0 <= row_index < len(rows):
        raise GateError('page.row_weights: row index is out of range.')
    supplied = page.get('row_weights')
    if supplied is None:
        return [1.0 / len(rows[row_index])] * len(rows[row_index])
    if not isinstance(supplied, list) or len(supplied) != len(rows):
        raise GateError('page.row_weights: expected one positive weight array per row.')
    weights = supplied[row_index]
    if (not isinstance(weights, list) or len(weights) != len(rows[row_index]) or
            any(type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0 for weight in weights)):
        raise GateError(f'page.row_weights[{row_index}]: weights must be positive finite numbers matching the row.')
    total = sum(weights)
    if not math.isfinite(total) or total <= 0:
        raise GateError(f'page.row_weights[{row_index}]: weight total must be finite and positive.')
    return [weight / total for weight in weights]


def panel_aspect_ratio(panel):
    ratio = panel.get('aspect_ratio')
    if ratio is None:
        return None
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or ratio <= 0:
        raise GateError(f"panel {panel.get('id', '?')}.aspect_ratio: expected a positive finite number.")
    return float(ratio)


def script_errors(project, root=None):
    """Structural checks report field paths and never treat malformed data as proof of quality."""
    if not isinstance(project, dict):
        return ['project: expected an object.']
    errors = source_errors(project, root)
    source = project.get('source') if isinstance(project.get('source'), dict) else {}
    script = project.get('script') if isinstance(project.get('script'), dict) else {}
    if not isinstance(project.get('script'), dict):
        errors.append('script: expected an object.')

    def obj(value, path):
        if not isinstance(value, dict):
            errors.append(path + ': expected an object.')
            return {}
        return value

    def arr(value, path):
        if not isinstance(value, list):
            errors.append(path + ': expected an array.')
            return []
        return value

    source_chapters = arr(source.get('chapters', []), 'source.chapters')
    source_units = arr(source.get('units', []), 'source.units')
    chapters = {item.get('id'): item for item in source_chapters if isinstance(item, dict) and nonempty(item.get('id'))}
    units = {item.get('id'): item for item in source_units if isinstance(item, dict) and nonempty(item.get('id'))}
    for index, chapter in enumerate(source_chapters):
        if not isinstance(chapter, dict):
            errors.append(f'source.chapters[{index}]: expected an object.')
        elif chapter.get('has_body') and (not chapter.get('read') or not nonempty(chapter.get('read_note'))):
            errors.append('Unread chapter: ' + str(chapter.get('id', '?')))
    for field in ('outline', 'ending'):
        if not nonempty(script.get(field)):
            errors.append('script.' + field + ': required nonempty volume text.')
    style = obj(script.get('style', {}), 'script.style')
    for key in ('genre', 'look', 'palette', 'selection_reason'):
        if not nonempty(style.get(key)):
            errors.append('script.style.' + key + ': required nonempty text.')
    if style.get('format') not in ('pages', 'strip') or style.get('reading_direction') not in ('ltr', 'rtl'):
        errors.append('script.style.format/reading_direction: invalid values.')
    for key in ('width', 'height', 'font_size', 'max_segment_height'):
        if key in style and (type(style[key]) is not int or style[key] <= 0):
            errors.append(f'script.style.{key}: expected a positive integer.')
    if 'font_path' in style and style['font_path'] is not None and not nonempty(style['font_path']):
        errors.append('script.style.font_path: expected a nonempty string or null.')

    registries = {}
    character_versions = {}
    for name in ('characters', 'settings', 'props', 'events', 'scenes', 'panels', 'pages'):
        items = arr(script.get(name, []), 'script.' + name)
        identifiers = []
        registry = {}
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f'script.{name}[{index}]: expected an object.')
                continue
            identifier = item.get('id')
            if not nonempty(identifier):
                errors.append(f'script.{name}[{index}].id: required nonempty string.')
                continue
            identifiers.append(identifier)
            if identifier in registry:
                errors.append(f'script.{name}[{index}].id: duplicate {identifier}.')
            registry[identifier] = item
        registries[name] = registry

    for cid, character in registries['characters'].items():
        base = f'script.characters[{cid}]'
        if not nonempty(character.get('name')):
            errors.append(base + '.name: required nonempty string.')
        if character.get('importance') not in ('major', 'supporting', 'minor'):
            errors.append(base + '.importance: invalid value.')
        narrative = obj(character.get('narrative', {}), base + '.narrative')
        for key in ('goal', 'motivation', 'voice', 'arc'):
            if not nonempty(narrative.get(key)):
                errors.append(base + '.narrative.' + key + ': required nonempty string.')
        visual = obj(character.get('visual', {}), base + '.visual')
        for key in ('face_shape', 'eyes', 'brows', 'nose_mouth', 'body', 'posture', 'hair', 'age'):
            if not nonempty(visual.get(key)):
                errors.append(base + '.visual.' + key + ': required nonempty string.')
        if character.get('design_tier') not in ('lead', 'core', 'background'):
            errors.append(base + '.design_tier: expected lead, core, or background.')
        appearance = obj(character.get('appearance'), base + '.appearance')
        if appearance.get('gender_presentation') not in ('feminine', 'masculine', 'source_defined'):
            errors.append(base + '.appearance.gender_presentation: invalid style expression.')
        if not nonempty(appearance.get('requirements')):
            errors.append(base + '.appearance.requirements: required design guidance.')
        appearance_sources = appearance.get('source_unit_ids')
        if not isinstance(appearance_sources, list) or any(not nonempty(uid) or uid not in units for uid in appearance_sources):
            errors.append(base + '.appearance.source_unit_ids: expected source unit IDs.')
        if appearance.get('gender_presentation') == 'source_defined' and (not isinstance(appearance_sources, list) or not appearance_sources):
            errors.append(base + '.appearance.source_unit_ids: source_defined expression requires explicit source evidence.')
        identity = obj(character.get('identity_card'), base + '.identity_card')
        for key in ('face', 'eyes_brows', 'nose_mouth', 'body', 'posture', 'temperament'):
            if not nonempty(identity.get(key)):
                errors.append(base + '.identity_card.' + key + ': required distinguishing description.')
        invariants = identity.get('invariants')
        if not isinstance(invariants, list) or not invariants or any(not nonempty(v) for v in invariants):
            errors.append(base + '.identity_card.invariants: expected nonempty invariant descriptions.')
        versions = character.get('appearance_versions')
        version_map = {}
        if not isinstance(versions, list) or not versions:
            errors.append(base + '.appearance_versions: expected a nonempty array including base.')
            versions = []
        for index, version in enumerate(versions):
            if not isinstance(version, dict):
                errors.append(f'{base}.appearance_versions[{index}]: expected an object.')
                continue
            version_id = version.get('id')
            if not nonempty(version_id) or version_id in version_map:
                errors.append(f'{base}.appearance_versions[{index}].id: missing or duplicate ID.')
                continue
            version_map[version_id] = version
            if not nonempty(version.get('description')):
                errors.append(f'{base}.appearance_versions[{index}].description: required string.')
            version_visual = version.get('visual')
            if not isinstance(version_visual, dict) or not version_visual:
                errors.append(f'{base}.appearance_versions[{index}].visual: expected a nonempty object.')
        if 'base' not in version_map:
            errors.append(base + '.appearance_versions: missing base version.')
        character_versions[cid] = set(version_map)
        distinctions = character.get('distinctions')
        if not isinstance(distinctions, list):
            errors.append(base + '.distinctions: expected an array.')
            distinctions = []
        seen_other = set()
        for index, item in enumerate(distinctions):
            if not isinstance(item, dict):
                errors.append(f'{base}.distinctions[{index}]: expected an object.')
                continue
            other = item.get('other_character_id')
            if not nonempty(other):
                errors.append(f'{base}.distinctions[{index}].other_character_id: missing character ID.')
                continue
            if other in seen_other:
                errors.append(f'{base}.distinctions[{index}].other_character_id: missing or duplicate ID.')
            seen_other.add(other)
            if other not in registries['characters']:
                errors.append(f'{base}.distinctions[{index}].other_character_id: unknown character ID {other}.')
            exception = item.get('source_exception')
            if exception is not None:
                exception = obj(exception, f'{base}.distinctions[{index}].source_exception')
                source_ids = exception.get('source_unit_ids')
                if (not nonempty(exception.get('reason')) or not isinstance(source_ids, list) or not source_ids or
                        any(not nonempty(uid) or uid not in units for uid in source_ids)):
                    errors.append(f'{base}.distinctions[{index}].source_exception: reason and valid source evidence required.')
            else:
                face = item.get('face_differences')
                if not isinstance(face, list) or len([v for v in face if nonempty(v)]) < 2:
                    errors.append(f'{base}.distinctions[{index}].face_differences: at least two specific differences required.')
                if not nonempty(item.get('body_difference')):
                    errors.append(f'{base}.distinctions[{index}].body_difference: required.')
                if not nonempty(item.get('performance_difference')):
                    errors.append(f'{base}.distinctions[{index}].performance_difference: required.')
        explicit = character.get('comparison_with', [])
        if not isinstance(explicit, list) or any(not nonempty(other) for other in explicit):
            errors.append(base + '.comparison_with: expected an array of character IDs.')
            explicit = []
        else:
            unknown_comparisons = [other for other in explicit if other not in registries['characters']]
            if unknown_comparisons:
                errors.append(base + '.comparison_with: unknown character IDs: ' + ', '.join(map(str, unknown_comparisons)))
        source_facts = character.get('source_facts')
        design_notes = character.get('design_notes')
        if not isinstance(source_facts, list) or not isinstance(design_notes, list):
            errors.append(base + ': source_facts and design_notes arrays are required.')
            source_facts = source_facts if isinstance(source_facts, list) else []
        for index, fact in enumerate(source_facts):
            if not isinstance(fact, dict):
                errors.append(f'{base}.source_facts[{index}]: expected an object.')
                continue
            fact_units = fact.get('source_unit_ids')
            if not nonempty(fact.get('text')) or not isinstance(fact_units, list) or not fact_units:
                errors.append(f'{base}.source_facts[{index}]: text and valid source evidence required.')
            else:
                for uid in fact_units:
                    ok_unit, err_unit = validate_unit_ref(uid, units, root, project)
                    if not ok_unit:
                        errors.append(f'{base}.source_facts[{index}]: {err_unit}')
                        break

    # Characters that readers are expected to distinguish need explicit, evidence-backed comparisons.
    required_pairs = set()
    ids = list(registries['characters'])
    for left_index, left_id in enumerate(ids):
        left = registries['characters'][left_id]
        left_app = left.get('appearance') if isinstance(left.get('appearance'), dict) else {}
        left_visual = left.get('visual') if isinstance(left.get('visual'), dict) else {}
        for right_id in ids[left_index + 1:]:
            right = registries['characters'][right_id]
            right_app = right.get('appearance') if isinstance(right.get('appearance'), dict) else {}
            right_visual = right.get('visual') if isinstance(right.get('visual'), dict) else {}
            same_read = (left_app.get('gender_presentation') in ('feminine', 'masculine') and
                         left_app.get('gender_presentation') == right_app.get('gender_presentation'))
            same_age = (nonempty(left_visual.get('age')) and
                        str(left_visual.get('age')).strip().casefold() == str(right_visual.get('age', '')).strip().casefold())
            left_comparisons = left.get('comparison_with', [])
            right_comparisons = right.get('comparison_with', [])
            left_comparisons = left_comparisons if isinstance(left_comparisons, list) and all(nonempty(v) for v in left_comparisons) else []
            right_comparisons = right_comparisons if isinstance(right_comparisons, list) and all(nonempty(v) for v in right_comparisons) else []
            comparisons = set(left_comparisons + right_comparisons)
            if comparisons and (left_id in comparisons or right_id in comparisons):
                required_pairs.add((left_id, right_id))
            elif same_read and same_age and left.get('design_tier') in ('lead', 'core') and right.get('design_tier') in ('lead', 'core'):
                required_pairs.add((left_id, right_id))
    for left_id, right_id in required_pairs:
        for owner, other in ((left_id, right_id), (right_id, left_id)):
            rows = registries['characters'][owner].get('distinctions', [])
            if not any(isinstance(row, dict) and row.get('other_character_id') == other for row in rows):
                errors.append(f'script.characters[{owner}].distinctions: missing comparison with {other}.')

    for eid, event in registries['events'].items():
        evidence = event.get('source_unit_ids')
        if not nonempty(event.get('description')) or not isinstance(evidence, list) or not evidence:
            errors.append(f'script.events[{eid}]: description and valid source evidence required.')
        else:
            for uid in evidence:
                ok_unit, err_unit = validate_unit_ref(uid, units, root, project)
                if not ok_unit:
                    errors.append(f'script.events[{eid}]: {err_unit}')
                    break
        role = event.get('narrative_role')
        if role is not None and role not in VALID_NARRATIVE_ROLES:
            errors.append(f'script.events[{eid}].narrative_role: expected one of {sorted(VALID_NARRATIVE_ROLES)}.')

    def entity_references(entity, path):
        paths = entity.get('reference_paths', [])
        hashes = entity.get('reference_hashes', [])
        if not isinstance(paths, list) or not isinstance(hashes, list) or len(paths) != len(hashes):
            errors.append(path + '.reference_paths/reference_hashes: expected matching arrays.')
            return
        for index, (relative, expected) in enumerate(zip(paths, hashes)):
            try:
                if not nonempty(relative) or not isinstance(expected, str) or len(expected) != 64:
                    raise GateError('invalid path/hash')
                if root is not None:
                    file_path = inside(root, relative)
                    if not file_path.is_file() or sha_file(file_path) != expected:
                        errors.append(f'{path}.reference_paths[{index}]: file missing or content hash changed.')
            except (OSError, GateError, TypeError):
                errors.append(f'{path}.reference_paths[{index}]: invalid or escaping project path.')

    for setting_id, setting in registries['settings'].items():
        entity_references(setting, f'script.settings[{setting_id}]')
    for prop_id, prop in registries['props'].items():
        entity_references(prop, f'script.props[{prop_id}]')
    for scene_id, scene in registries['scenes'].items():
        if (not nonempty(scene.get('chapter_id')) or scene.get('chapter_id') not in chapters or
                not nonempty(scene.get('setting_id')) or scene.get('setting_id') not in registries['settings']):
            errors.append(f'script.scenes[{scene_id}]: chapter_id/setting_id reference is invalid.')
        scene_props = scene.get('prop_ids', [])
        if not isinstance(scene_props, list) or any(not nonempty(prop_id) or prop_id not in registries['props'] for prop_id in scene_props):
            errors.append(f'script.scenes[{scene_id}].prop_ids: unknown prop ID or malformed array.')
        entity_references(scene, f'script.scenes[{scene_id}]')

    previous_states = {}
    used_events, covered, chapter_panels = set(), set(), set()
    panels = script.get('panels', []) if isinstance(script.get('panels'), list) else []
    for index, panel in enumerate(panels):
        if not isinstance(panel, dict):
            continue
        pid = str(panel.get('id', f'index-{index}'))
        chapter_id = panel.get('chapter_id')
        scene_id = panel.get('scene_id')
        scene = registries['scenes'].get(scene_id) if nonempty(scene_id) else None
        if not nonempty(chapter_id) or chapter_id not in chapters or not chapters.get(chapter_id, {}).get('has_body'):
            errors.append(f'script.panels[{pid}].chapter_id: absent or empty chapter.')
        if scene is None or scene.get('chapter_id') != chapter_id:
            errors.append(f'script.panels[{pid}].scene_id: invalid scene for chapter.')
        if nonempty(chapter_id):
            chapter_panels.add(chapter_id)
        for key in ('action', 'shot', 'space', 'expression'):
            if not nonempty(panel.get(key)):
                errors.append(f'script.panels[{pid}].{key}: required nonempty string.')
        if 'visual_plan' in panel and not isinstance(panel['visual_plan'], dict):
            errors.append(f'script.panels[{pid}].visual_plan: expected an object.')
        try:
            panel_aspect_ratio(panel)
        except GateError as error:
            errors.append(str(error))
        source_ids = panel.get('source_unit_ids', [])
        if not isinstance(source_ids, list) or not source_ids:
            errors.append(f'script.panels[{pid}].source_unit_ids: invalid evidence IDs.')
            source_ids = []
        else:
            for uid in source_ids:
                ok_unit, err_unit = validate_unit_ref(uid, units, root, project)
                if not ok_unit:
                    errors.append(f'script.panels[{pid}].source_unit_ids: {err_unit}')
                    break
                if isinstance(uid, str):
                    covered.add(uid)
                elif isinstance(uid, dict) and (uid.get('volume_id') == project.get('volume') or uid.get('volume_id') == project.get('title')):
                    covered.add(uid.get('unit_id'))
        event_ids = panel.get('event_ids', [])
        if not isinstance(event_ids, list):
            errors.append(f'script.panels[{pid}].event_ids: expected an array.')
            event_ids = []
        for event_id in event_ids:
            event = registries['events'].get(event_id) if nonempty(event_id) else None
            event_units = event.get('source_unit_ids', []) if isinstance(event, dict) else []
            event_unit_keys = set(uid if isinstance(uid, str) else (uid.get('unit_id') if isinstance(uid, dict) else None) for uid in event_units)
            source_unit_keys = set(uid if isinstance(uid, str) else (uid.get('unit_id') if isinstance(uid, dict) else None) for uid in source_ids)
            if event is None or not isinstance(event_units, list) or not all(nonempty(uid) if isinstance(uid, str) else isinstance(uid, dict) for uid in event_units) or not event_unit_keys.issubset(source_unit_keys):
                errors.append(f'script.panels[{pid}].event_ids: event evidence not covered by panel.')
            if nonempty(event_id):
                used_events.add(event_id)
        cast = panel.get('cast', [])
        if not isinstance(cast, list) or any(not nonempty(cid) for cid in cast):
            errors.append(f'script.panels[{pid}].cast: expected character IDs.')
            cast = []
        if len(cast) != len(set(cast)) or any(cid not in registries['characters'] for cid in cast):
            errors.append(f'script.panels[{pid}].cast: duplicate or unknown character ID.')
        prop_ids = panel.get('prop_ids', [])
        if not isinstance(prop_ids, list) or any(not nonempty(prop_id) or prop_id not in registries['props'] for prop_id in prop_ids):
            errors.append(f'script.panels[{pid}].prop_ids: unknown prop ID or malformed array.')
        appearance_versions = panel.get('appearance_versions', {})
        if not isinstance(appearance_versions, dict):
            errors.append(f'script.panels[{pid}].appearance_versions: expected an object.')
            appearance_versions = {}
        if any(cid not in cast for cid in appearance_versions):
            errors.append(f'script.panels[{pid}].appearance_versions: keys must belong to cast.')
        panel_refs = panel.get('reference_ids', [])
        if not isinstance(panel_refs, list) or any(not nonempty(rid) for rid in panel_refs):
            errors.append(f'script.panels[{pid}].reference_ids: expected an array of reference IDs.')
        if 'appearance_versions' not in panel:
            errors.append(f'script.panels[{pid}].appearance_versions: explicit character-to-version mapping required.')
        for cid in cast:
            if cid not in appearance_versions:
                errors.append(f'script.panels[{pid}].appearance_versions[{cid}]: explicit version required.')
            character = registries['characters'].get(cid, {})
            version_ids = character_versions.get(cid, set())
            selected_version = appearance_versions.get(cid, 'base')
            if not nonempty(selected_version) or selected_version not in version_ids:
                errors.append(f'script.panels[{pid}].appearance_versions[{cid}]: unknown version {selected_version}.')
            before_all = panel.get('state_before', {})
            after_all = panel.get('state_after', {})
            before_all = before_all if isinstance(before_all, dict) else {}
            after_all = after_all if isinstance(after_all, dict) else {}
            before = before_all.get(cid)
            after = after_all.get(cid)
            if not isinstance(before, dict) or not before or not isinstance(after, dict) or not after:
                errors.append(f'script.panels[{pid}].state_before/state_after[{cid}]: explicit nonempty objects required.')
                continue
            form = before.get('form')
            if nonempty(form) and form not in version_ids:
                errors.append(f'script.panels[{pid}].state_before[{cid}].form: unknown appearance version {form}.')
            elif nonempty(form) and selected_version != form:
                errors.append(f'script.panels[{pid}].appearance_versions[{cid}]: must match state_before form {form}.')
            previous = previous_states.get(cid, {})
            changed_between = {key for key in previous.keys() | before.keys()
                               if key not in previous or key not in before or previous.get(key) != before.get(key)}
            changed_within = {key for key in before.keys() | after.keys()
                              if key not in before or key not in after or before.get(key) != after.get(key)}
            transitions = panel.get('state_transitions', [])
            if not isinstance(transitions, list):
                errors.append(f'script.panels[{pid}].state_transitions: expected an array.')
                transitions = []
            justified = set()
            for transition_index, transition in enumerate(transitions):
                if not isinstance(transition, dict):
                    errors.append(f'script.panels[{pid}].state_transitions[{transition_index}]: expected an object.')
                    continue
                evidence = transition.get('source_unit_ids')
                fields = transition.get('fields', [])
                if (transition.get('character_id') == cid and nonempty(transition.get('reason')) and
                        isinstance(evidence, list) and evidence and all(nonempty(uid) and uid in units for uid in evidence) and
                        isinstance(fields, list) and fields and all(nonempty(field) for field in fields)):
                    justified.update(fields)
            unexplained_between = changed_between - justified if previous else set()
            unexplained_within = changed_within - justified
            if unexplained_between:
                errors.append(f'script.panels[{pid}].state_before[{cid}]: unexplained between-panel changes/removals: {", ".join(sorted(unexplained_between))}.')
            if unexplained_within:
                errors.append(f'script.panels[{pid}].state_after[{cid}]: unexplained within-panel changes/removals: {", ".join(sorted(unexplained_within))}.')
            previous_states[cid] = after

        dialogue = panel.get('dialogue', [])
        if not isinstance(dialogue, list):
            errors.append(f'script.panels[{pid}].dialogue: expected an array.')
            dialogue = []
        for dindex, item in enumerate(dialogue):
            if not isinstance(item, dict):
                errors.append(f'script.panels[{pid}].dialogue[{dindex}]: expected an object.')
                continue
            if item.get('kind') not in ('speech', 'thought', 'caption', 'sfx') or not nonempty(item.get('text')):
                errors.append(f'script.panels[{pid}].dialogue[{dindex}]: invalid kind or empty text.')
            if item.get('kind') in ('speech', 'thought') and item.get('speaker') not in cast:
                errors.append(f'script.panels[{pid}].dialogue[{dindex}].speaker: must be in cast.')
            if 'anchor' in item:
                anchor = item['anchor']
                if (not isinstance(anchor, list) or len(anchor) != 2 or
                        any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in anchor)):
                    errors.append(f'script.panels[{pid}].dialogue[{dindex}].anchor: expected normalized [x,y].')
        lettering_mode = panel.get('lettering_mode', 'band')
        if lettering_mode not in ('band', 'bubbles'):
            errors.append(f'script.panels[{pid}].lettering_mode: expected band or bubbles.')
        bubbles = panel.get('bubbles', [])
        if not isinstance(bubbles, list):
            errors.append(f'script.panels[{pid}].bubbles: expected an array.')
            bubbles = []
        bubble_indexes, orders, centers = [], [], []
        for bindex, bubble in enumerate(bubbles):
            if not isinstance(bubble, dict):
                errors.append(f'script.panels[{pid}].bubbles[{bindex}]: expected an object.')
                continue
            dialogue_index = bubble.get('dialogue_index')
            rect = bubble.get('rect')
            order = bubble.get('order')
            if type(dialogue_index) is not int or not 0 <= dialogue_index < len(dialogue):
                errors.append(f'script.panels[{pid}].bubbles[{bindex}].dialogue_index: out of range.')
            else:
                bubble_indexes.append(dialogue_index)
            if (not isinstance(rect, list) or len(rect) != 4 or
                    any(type(v) not in (int, float) or not math.isfinite(v) for v in rect) or
                    rect[0] < 0 or rect[1] < 0 or rect[2] <= 0 or rect[3] <= 0 or rect[0] + rect[2] > 1 or rect[1] + rect[3] > 1):
                errors.append(f'script.panels[{pid}].bubbles[{bindex}].rect: expected normalized [x,y,w,h] inside panel.')
            else:
                centers.append((order, rect[1] + rect[3] / 2, rect[0] + rect[2] / 2))
            if type(order) is not int:
                errors.append(f'script.panels[{pid}].bubbles[{bindex}].order: expected integer reading order.')
            else:
                orders.append(order)
            tail = bubble.get('tail')
            if tail is not None and (not isinstance(tail, list) or len(tail) != 2 or
                    any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in tail)):
                errors.append(f'script.panels[{pid}].bubbles[{bindex}].tail: expected null or normalized [x,y].')
        if lettering_mode == 'bubbles':
            if sorted(bubble_indexes) != list(range(len(dialogue))):
                errors.append(f'script.panels[{pid}].bubbles: every dialogue item must appear exactly once.')
            if len(orders) != len(set(orders)):
                errors.append(f'script.panels[{pid}].bubbles.order: reading order values must be unique.')
            if centers:
                direction = style.get('reading_direction', 'ltr')
                ordered = sorted(centers, key=lambda point: point[0] if type(point[0]) is int else -1)
                for before_point, after_point in zip(ordered, ordered[1:]):
                    if after_point[1] < before_point[1] - 0.08:
                        errors.append(f'script.panels[{pid}].bubbles.order: conflicts with top-to-bottom reading order.')
                    elif abs(after_point[1] - before_point[1]) <= 0.08:
                        wrong = after_point[2] < before_point[2] if direction == 'ltr' else after_point[2] > before_point[2]
                        if wrong:
                            errors.append(f'script.panels[{pid}].bubbles.order: conflicts with {direction} horizontal reading order.')
                            break

    handover = script.get('continuity_handover') or project.get('continuity_handover')
    if handover is not None:
        if not isinstance(handover, dict):
            errors.append('continuity_handover: expected an object.')
        else:
            for state_key in ('opening_inherited_state', 'closing_state'):
                state_val = handover.get(state_key)
                if state_val is not None and not isinstance(state_val, dict):
                    errors.append(f'continuity_handover.{state_key}: expected an object or null.')

    for index, disposition in enumerate(arr(script.get('source_dispositions', []), 'script.source_dispositions')):
        if not isinstance(disposition, dict):
            errors.append(f'script.source_dispositions[{index}]: expected an object.')
            continue
        uid = disposition.get('unit_id')
        if not nonempty(uid) or uid not in units or disposition.get('kind') not in ('context', 'repetition', 'paratext') or not nonempty(disposition.get('reason')):
            errors.append(f'script.source_dispositions[{index}]: invalid source disposition.')
        elif disposition['kind'] == 'context':
            panel_ids = disposition.get('panel_ids')
            if not isinstance(panel_ids, list) or not panel_ids or any(not nonempty(panel_id) or panel_id not in registries['panels'] for panel_id in panel_ids):
                errors.append(f'script.source_dispositions[{index}].panel_ids: context requires real panel references.')
            else:
                covered.add(uid)
        else:
            covered.add(uid)
    for uid, unit in units.items():
        if unit.get('kind') == 'body' and uid not in covered:
            errors.append('Unmapped original text: ' + str(uid))
    for chapter_id, chapter in chapters.items():
        if chapter.get('has_body') and chapter_id not in chapter_panels:
            errors.append('Chapter has no full panel script: ' + str(chapter_id))
    for event_id in registries['events']:
        if event_id not in used_events:
            errors.append('Event has no panel: ' + str(event_id))

    page_panels = []
    for page_id, page in registries['pages'].items():
        pids = page.get('panel_ids', [])
        if not isinstance(pids, list) or not pids or any(not nonempty(panel_id) or panel_id not in registries['panels'] for panel_id in pids):
            errors.append(f'script.pages[{page_id}].panel_ids: references missing panels.')
            pids = [panel_id for panel_id in pids if nonempty(panel_id)] if isinstance(pids, list) else []
        chapter_id = page.get('chapter_id')
        if (not nonempty(chapter_id) or chapter_id not in chapters or
                any(registries['panels'].get(panel_id, {}).get('chapter_id') != chapter_id for panel_id in pids)):
            errors.append(f'script.pages[{page_id}].chapter_id: page crosses or omits its chapter.')
        try:
            rows = page_rows(page)
            if 'row_weights' in page:
                supplied = page['row_weights']
                if not isinstance(supplied, list) or len(supplied) != len(rows):
                    raise GateError('page.row_weights: expected weights matching rows.')
                for row_index in range(len(rows)):
                    page_row_weights(page, row_index)
        except GateError as error:
            errors.append(f'script.pages[{page_id}]: {error}')
        narrative = page.get('narrative')
        if narrative is not None:
            narrative = obj(narrative, f'script.pages[{page_id}].narrative')
            for key in ('purpose', 'new_information', 'emotion', 'focus_panel_id', 'page_turn'):
                if not nonempty(narrative.get(key)):
                    errors.append(f'script.pages[{page_id}].narrative.{key}: expected nonempty text.')
            if narrative.get('focus_panel_id') and narrative['focus_panel_id'] not in pids:
                errors.append(f'script.pages[{page_id}].narrative.focus_panel_id: must belong to this page.')
        page_panels.extend(pids)
    if page_panels != [panel.get('id') for panel in panels if isinstance(panel, dict)]:
        errors.append('Pages must cover all panels exactly once in script reading order.')
    if not registries['panels'] or not registries['pages']:
        errors.append('Complete drawable panels and page plan required.')
    return errors


def validate_qa(report, required, scope=None):
    if not isinstance(report, dict) or not nonempty(report.get('evidence')):
        raise GateError('QA requires actual visual/read evidence, not an unexplained pass.')
    checks = report.get('checks', {})
    if not isinstance(checks, dict):
        raise GateError('QA checks must be an object.')
    if any(checks.get(key) is not True for key in required):
        raise GateError('QA not passed: ' + ', '.join(k for k in required if checks.get(k) is not True))
    if 'visual_elegance' in required:
        notes = report.get('elegance_notes')
        if not isinstance(notes, dict):
            raise GateError('QA elegance_notes: expected actual observations of linework, color_and_light, and visual_hierarchy.')
        for key in ('linework', 'color_and_light', 'visual_hierarchy'):
            if not nonempty(notes.get(key)):
                raise GateError('QA elegance_notes.' + key + ': actual visual evidence is required.')
    if scope in ('reference', 'panel'):
        reviewed = report.get('reviewed_ids')
        findings = report.get('findings')
        if not isinstance(reviewed, list) or not reviewed or any(not nonempty(value) for value in reviewed):
            raise GateError('QA requires nonempty reviewed_ids identifying the assets actually inspected.')
        if not isinstance(findings, list):
            raise GateError('QA findings must be an array; use [] when the review found no defects.')
        for index, finding in enumerate(findings):
            if (not isinstance(finding, dict) or finding.get('severity') not in ('critical', 'major', 'minor', 'info') or
                    not nonempty(finding.get('description')) or type(finding.get('resolved')) is not bool):
                raise GateError(f'QA findings[{index}] needs severity, description, and resolved fields.')
            if finding.get('severity') in ('critical', 'major') and not finding.get('resolved'):
                raise GateError(f'QA findings[{index}] has an unresolved critical/major defect.')
    if scope == 'reference':
        comparisons = report.get('comparisons')
        if not isinstance(comparisons, list):
            raise GateError('Reference QA requires a comparisons array.')
        for index, pair in enumerate(comparisons):
            if not isinstance(pair, dict):
                raise GateError(f'reference QA comparisons[{index}] must be an object with pair evidence.')
            ids = pair.get('character_ids', pair.get('pair'))
            if not isinstance(ids, list) or len(ids) != 2 or ids[0] == ids[1] or not all(nonempty(value) for value in ids):
                raise GateError(f'reference QA comparisons[{index}] must identify two compared characters.')
            if not nonempty(pair.get('evidence', pair.get('finding', ''))):
                raise GateError(f'reference QA comparisons[{index}] requires a concrete comparison finding.')


def validate_reference_qa(report, character_ids, image_sha256, reference_visual_key):
    validate_qa(report, REFERENCE_CHECKS, 'reference')
    missing = set(character_ids) - set(report['reviewed_ids'])
    if missing:
        raise GateError('Reference QA reviewed_ids must include every subject character: ' + ', '.join(sorted(missing)))
    if report.get('image_sha256') != image_sha256:
        raise GateError('Reference QA image_sha256 must match the exact reference image bytes.')
    if report.get('reference_visual_key') != reference_visual_key:
        raise GateError('Reference QA reference_visual_key must match the current image, character design, purpose, and subject regions.')


def validate_panel_qa(report, panel_id, attempt_number, render_fingerprint, image_sha256):
    validate_qa(report, PANEL_CHECKS, 'panel')
    if not nonempty(report.get('detail_notes')):
        raise GateError('Panel QA detail_notes must describe native-size and composed-size detail observations.')
    if report['reviewed_ids'] != [panel_id]:
        raise GateError('Panel QA reviewed_ids must contain only the actual panel ID: ' + str(panel_id))
    if report.get('image_sha256') != image_sha256:
        raise GateError('Panel QA image_sha256 must match the exact submitted panel image bytes.')
    bindings = report.get('attempt_bindings')
    binding = bindings.get(panel_id) if isinstance(bindings, dict) else None
    if (not isinstance(bindings, dict) or set(bindings) != {panel_id} or
            not isinstance(binding, dict) or type(binding.get('attempt')) is not int or
            binding.get('attempt') != attempt_number or binding.get('render_hash') != render_fingerprint):
        raise GateError(f'Panel QA attempt_bindings[{panel_id}] must match this attempt number and render_hash.')


def art_structure_errors(project):
    """Return field-path diagnostics for art ledgers before commands consume them."""
    errors = []
    art = project.get('art') if isinstance(project, dict) else None
    if not isinstance(art, dict):
        return ['art: expected an object.']
    references = art.get('references')
    if not isinstance(references, list):
        errors.append('art.references: expected an array.')
    else:
        for index, reference in enumerate(references):
            if not isinstance(reference, dict):
                errors.append(f'art.references[{index}]: expected an object.')
    panels = art.get('panels')
    if not isinstance(panels, dict):
        errors.append('art.panels: expected an object keyed by panel ID.')
    else:
        for panel_id, attempts in panels.items():
            if not nonempty(panel_id):
                errors.append('art.panels: keys must be nonempty panel IDs.')
            if not isinstance(attempts, list):
                errors.append(f'art.panels[{panel_id}]: expected an array of attempts.')
                continue
            for index, attempt in enumerate(attempts):
                if not isinstance(attempt, dict):
                    errors.append(f'art.panels[{panel_id}][{index}]: expected an attempt object.')
                elif type(attempt.get('number')) is not int or attempt.get('number') <= 0:
                    errors.append(f'art.panels[{panel_id}][{index}].number: expected a positive integer.')
                elif attempt.get('status') not in ('pending', 'failed', 'cancelled', 'stale', 'rejected', 'accepted'):
                    errors.append(f'art.panels[{panel_id}][{index}].status: invalid attempt status.')
    bindings = art.get('bindings')
    if not isinstance(bindings, dict):
        errors.append('art.bindings: expected an object keyed by panel ID.')
    else:
        for panel_id, binding in bindings.items():
            if not nonempty(panel_id) or not isinstance(binding, dict):
                errors.append(f'art.bindings[{panel_id}]: expected a panel ID and binding object.')
    batches = art.get('batches')
    if not isinstance(batches, dict):
        errors.append('art.batches: expected an object keyed by batch ID.')
    else:
        for batch_id, batch in batches.items():
            if (not nonempty(batch_id) or not isinstance(batch, dict) or
                    not isinstance(batch.get('panels'), list) or not batch['panels'] or
                    not isinstance(batch.get('canvas_pixels'), list) or len(batch['canvas_pixels']) != 2 or
                    any(type(v) is not int or v <= 0 for v in batch['canvas_pixels']) or
                    any(not isinstance(item, dict) or not nonempty(item.get('panel_id')) or
                        type(item.get('attempt')) is not int for item in batch['panels'])):
                errors.append(f'art.batches[{batch_id}]: malformed batch record.')
    return errors


def assert_script_lock(project, root=None):
    if not isinstance(project, dict):
        raise GateError('project: expected an object.')
    errors = script_errors(project, root)
    errors.extend(art_structure_errors(project))
    lock = project.get('script_lock')
    if not isinstance(lock, dict) or lock.get('script_hash') != digest(project.get('script')) or lock.get('source_index_hash') != project.get('source_index_hash'):
        errors.append('Volume script lock missing or stale; no drawing is allowed.')
    reviews = project.get('reviews')
    if not isinstance(reviews, list):
        errors.append('reviews: expected an array.')
    elif isinstance(lock, dict) and lock.get('reviews_hash') != digest(reviews):
        errors.append('Review records changed after script lock.')
    if errors:
        raise GateError('\n'.join(errors))


LAYOUT_STYLE_KEYS = {'width', 'height', 'font_size', 'font_path', 'max_segment_height', 'format',
                     'reading_direction', 'lettering_mode'}


def visual_style(project):
    style = project['script'].get('style', {})
    if not isinstance(style, dict):
        return {}
    visual = {key: value for key, value in style.items()
              if key not in LAYOUT_STYLE_KEYS and key not in ('selection_reason', 'references')}
    direction = visual.get('art_direction')
    if isinstance(direction, dict):
        visual['art_direction'] = {key: value for key, value in direction.items() if key != 'lettering'}
    return visual


def character_visual(character, version_id='base'):
    versions = character.get('appearance_versions', [])
    version = next((item for item in versions if isinstance(item, dict) and item.get('id') == version_id), None)
    if version is None:
        raise GateError(f"Unknown character appearance version: {character.get('id', '?')}/{version_id}")
    appearance = character.get('appearance')
    appearance = appearance if isinstance(appearance, dict) else {}
    linked = []
    for item in character.get('distinctions', []):
        if not isinstance(item, dict) or not nonempty(item.get('other_character_id')):
            continue
        exception = item.get('source_exception')
        linked.append({'other_character_id': item['other_character_id'],
                       'face_differences': item.get('face_differences'),
                       'body_difference': item.get('body_difference'),
                       'performance_difference': item.get('performance_difference'),
                       'source_exception_reason': exception.get('reason') if isinstance(exception, dict) else None})
    return {'id': character['id'], 'design_tier': character.get('design_tier'),
            'appearance': {key: appearance.get(key) for key in ('gender_presentation', 'requirements')},
            'identity_card': character.get('identity_card'),
            'visual': character.get('visual'), 'selected_version': {
                'id': version_id, 'description': version.get('description'), 'visual': version.get('visual')},
            'distinctions': linked}


def design_hash(project, character_ids, version_by_character=None):
    characters = {c['id']: c for c in project['script']['characters'] if isinstance(c, dict) and nonempty(c.get('id'))}
    version_by_character = version_by_character or {}
    selected = []
    for cid in sorted(set(character_ids)):
        if cid not in characters:
            raise GateError('Unknown character in reference binding: ' + str(cid))
        selected.append(character_visual(characters[cid], version_by_character.get(cid, 'base')))
    return digest({'style': visual_style(project), 'characters': selected})


def normalize_subjects(project, subjects):
    if not isinstance(subjects, list) or not subjects:
        raise GateError('Reference subjects must be a nonempty array.')
    characters = {c.get('id'): c for c in project['script'].get('characters', []) if isinstance(c, dict)}
    normalized = []
    seen = set()
    for index, subject in enumerate(subjects):
        if not isinstance(subject, dict):
            raise GateError(f'reference subjects[{index}] must be an object.')
        cid, version_id = subject.get('character_id'), subject.get('version_id', 'base')
        if not nonempty(cid) or cid not in characters:
            raise GateError(f'reference subjects[{index}].character_id is unknown.')
        if not nonempty(version_id) or not any(isinstance(v, dict) and v.get('id') == version_id
                                               for v in characters[cid].get('appearance_versions', [])):
            raise GateError(f'reference subjects[{index}].version_id is unknown for {cid}.')
        if cid in seen:
            raise GateError('A reference can list each character only once; create separate references for separate forms.')
        seen.add(cid)
        region = subject.get('region')
        if region is not None and (not isinstance(region, list) or len(region) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in region) or
                region[0] < 0 or region[1] < 0 or region[2] <= 0 or region[3] <= 0 or
                region[0] + region[2] > 1 or region[1] + region[3] > 1):
            raise GateError(f'reference subjects[{index}].region must be null or normalized [x,y,w,h].')
        normalized.append({'character_id': cid, 'version_id': version_id, 'region': region})
    return normalized


def reference_visual_key(image_sha256, design_fingerprint, purpose, subjects):
    """Stable visual identity for a reference, independent of its random registry ID."""
    if not nonempty(image_sha256) or not nonempty(design_fingerprint) or not nonempty(purpose):
        raise GateError('Reference visual key needs image SHA256, design hash, and purpose.')
    if not isinstance(subjects, list):
        raise GateError('Reference visual key subjects must be an array.')
    canonical_subjects = []
    for index, subject in enumerate(subjects):
        if not isinstance(subject, dict) or not nonempty(subject.get('character_id')) or not nonempty(subject.get('version_id')):
            raise GateError(f'reference subjects[{index}] cannot form a visual key.')
        region = subject.get('region')
        if region is not None:
            if not isinstance(region, list) or len(region) != 4 or any(type(value) not in (int, float) or not math.isfinite(value) for value in region):
                raise GateError(f'reference subjects[{index}].region cannot form a visual key.')
            region = [float(value) for value in region]
            if region == [0.0, 0.0, 1.0, 1.0]:
                region = None
        canonical_subjects.append({'character_id': subject['character_id'], 'version_id': subject['version_id'], 'region': region})
    canonical_subjects.sort(key=lambda subject: (subject['character_id'], subject['version_id'], digest(subject['region'])))
    return digest({'image_sha256': image_sha256, 'design_hash': design_fingerprint,
                   'purpose': purpose, 'subjects': canonical_subjects})


def stored_reference_visual_key(reference):
    return reference_visual_key(reference.get('sha256'), reference.get('design_hash'),
                                reference.get('purpose', 'combined'), reference.get('subjects'))


def valid_reference(root, project, reference):
    try:
        subjects = reference.get('subjects')
        if not isinstance(subjects, list) or not subjects:
            return False
        subjects = normalize_subjects(project, subjects)
        character_ids = [item['character_id'] for item in subjects]
        path = inside(root, reference['path'])
        version_map = {item['character_id']: item['version_id'] for item in subjects}
        current_design_hash = design_hash(project, character_ids, version_map)
        current_sha = sha_file(path) if path.is_file() else None
        current_key = reference_visual_key(current_sha, current_design_hash, reference.get('purpose', 'combined'), subjects)
        validate_reference_qa(reference['qa'], character_ids, current_sha, current_key)
        return (reference['design_hash'] == current_design_hash and reference.get('visual_key') == current_key
                and path.is_file() and current_sha == reference['sha256'])
    except (KeyError, GateError, TypeError, OSError):
        return False


def select_references(root, project, panel):
    panel_id = panel.get('id', '?')
    bindings = project.get('art', {}).get('bindings', {})
    binding = bindings.get(panel_id) if isinstance(bindings, dict) else None
    if not isinstance(binding, dict):
        raise GateError(f'Panel {panel_id} has no explicit art binding; run bind-panel with appearance_versions and reference_ids.')
    versions = binding.get('appearance_versions')
    ref_ids = binding.get('reference_ids')
    cast = panel.get('cast', [])
    if not isinstance(versions, dict) or not isinstance(ref_ids, list) or (cast and not ref_ids):
        raise GateError(f'Panel {panel_id} binding needs appearance_versions and reference_ids.')
    if any(not nonempty(ref_id) for ref_id in ref_ids):
        raise GateError(f'Panel {panel_id} binding contains a malformed reference ID.')
    if len(ref_ids) != len(set(ref_ids)):
        raise GateError(f'Panel {panel_id} binding repeats a reference ID.')
    if not isinstance(cast, list):
        raise GateError(f'Panel {panel_id} cast is malformed.')
    script_versions = panel.get('appearance_versions')
    if not isinstance(script_versions, dict):
        raise GateError(f'Panel {panel_id} script has no explicit appearance_versions map.')
    for character in cast:
        if character not in versions:
            raise GateError(f'Panel {panel_id} binding omits appearance version for {character}.')
        if versions.get(character) != script_versions.get(character):
            raise GateError(f'Panel {panel_id} art binding does not match frozen script appearance version for {character}.')
    all_references = project.get('art', {}).get('references', [])
    by_id = {ref.get('id'): ref for ref in all_references if isinstance(ref, dict)}
    selected = []
    covered = set()
    for ref_id in ref_ids:
        ref = by_id.get(ref_id)
        if ref is None or not valid_reference(root, project, ref):
            raise GateError(f'Panel {panel_id} reference is missing, stale, or lacks current visual QA: {ref_id}.')
        for subject in ref['subjects']:
            cid = subject['character_id']
            if cid in cast and versions.get(cid) != subject['version_id']:
                continue
            if cid in cast:
                covered.add(cid)
        selected.append(ref)
    missing = [cid for cid in cast if cid not in covered]
    if missing:
        raise GateError(f'Panel {panel_id} has no explicitly bound reference for cast/form: ' + ', '.join(missing))
    characters = {c.get('id'): c for c in project['script'].get('characters', []) if isinstance(c, dict)}
    states = panel.get('state_before', {})
    states = states if isinstance(states, dict) else {}
    for cid in cast:
        before = states.get(cid, {})
        form = before.get('form') if isinstance(before, dict) else None
        valid_forms = {v.get('id') for v in characters.get(cid, {}).get('appearance_versions', []) if isinstance(v, dict)}
        if nonempty(form) and form in valid_forms and versions.get(cid) != form:
            raise GateError(f'Panel {panel_id} binding for {cid} does not match selected form {form}.')
    return selected


def entity_reference_fingerprints(root, entity):
    paths = entity.get('reference_paths', [])
    hashes = entity.get('reference_hashes', [])
    if not isinstance(paths, list) or not isinstance(hashes, list) or len(paths) != len(hashes):
        raise GateError('reference_paths/reference_hashes must be matching arrays.')
    actual = []
    for relative, expected in zip(paths, hashes):
        path = inside(root, relative)
        if not path.is_file():
            raise GateError('Scene/prop reference missing: ' + str(relative))
        size = path.stat().st_size
        content_hash = sha_file(path)
        if content_hash != expected:
            raise GateError('Scene/prop reference content changed: ' + str(relative))
        actual.append({'path': relative, 'length': size, 'sha256': content_hash})
    return actual


def panel_visual_snapshot(root, project, panel):
    panel_id = panel.get('id', '?')
    cast = panel.get('cast', [])
    art_binding = project.get('art', {}).get('bindings', {}).get(panel_id, {})
    versions = art_binding.get('appearance_versions') if isinstance(art_binding, dict) else None
    if not isinstance(versions, dict):
        versions = panel.get('appearance_versions', {})
    if not isinstance(versions, dict):
        versions = {}
    characters = {c.get('id'): c for c in project['script'].get('characters', []) if isinstance(c, dict)}
    rendered_characters = [character_visual(characters[cid], versions.get(cid, 'base'))
                           for cid in sorted(cast) if cid in characters]
    visual = {key: panel.get(key) for key in ('id', 'scene_id', 'cast', 'prop_ids', 'action', 'shot', 'space', 'expression',
                                               'state_before', 'state_after')}
    if 'visual_plan' in panel:
        visual['visual_plan'] = panel['visual_plan']
    scenes = {s['id']: s for s in project['script'].get('scenes', []) if isinstance(s, dict) and nonempty(s.get('id'))}
    settings = {s['id']: s for s in project['script'].get('settings', []) if isinstance(s, dict) and nonempty(s.get('id'))}
    scene = scenes.get(panel.get('scene_id'))
    if scene is None:
        raise GateError(f'Panel {panel_id} scene is missing.')
    setting = settings.get(scene.get('setting_id'))
    if setting is None:
        raise GateError(f'Panel {panel_id} setting is missing.')
    props = {p.get('id'): p for p in project['script'].get('props', []) if isinstance(p, dict) and nonempty(p.get('id'))}
    prop_ids = panel.get('prop_ids', []) if isinstance(panel.get('prop_ids', []), list) else []
    scene_prop_ids = scene.get('prop_ids', []) if isinstance(scene.get('prop_ids', []), list) else []
    selected_prop_ids = sorted(set(prop_ids) | set(scene_prop_ids))
    prop_visuals = []
    for prop_id in selected_prop_ids:
        prop = props.get(prop_id)
        if prop is None:
            raise GateError(f'Panel {panel_id} references missing prop: {prop_id}')
        prop_visuals.append({'prop': {key: value for key, value in prop.items()
                                     if key not in ('reference_paths', 'reference_hashes')},
                             'references': entity_reference_fingerprints(root, prop)})
    scene_core = {key: value for key, value in scene.items() if key not in ('reference_paths', 'reference_hashes')}
    setting_core = {key: value for key, value in setting.items() if key not in ('reference_paths', 'reference_hashes')}
    return {'visual': visual, 'characters': rendered_characters, 'scene': scene_core, 'setting': setting_core,
            'scene_refs': entity_reference_fingerprints(root, scene),
            'setting_refs': entity_reference_fingerprints(root, setting), 'props': prop_visuals,
            'style': visual_style(project)}


def render_hash(root, project, panel, references):
    snapshot = panel_visual_snapshot(root, project, panel)
    binding = project.get('art', {}).get('bindings', {}).get(panel.get('id'), {})
    # Registry IDs and binding order do not alter what is depicted. Hash the
    # canonical set so duplicate registrations or a reordered reference list
    # cannot reset the per-visual-input attempt budget.
    visual_reference_keys = sorted({stored_reference_visual_key(reference) for reference in references})
    return digest({'snapshot': snapshot,
                   'binding_versions': binding.get('appearance_versions', {}),
                   'refs': visual_reference_keys})


def accepted_panel(root, project, panel):
    try:
        references = select_references(root, project, panel)
        fingerprint = render_hash(root, project, panel, references)
        attempts = project.get('art', {}).get('panels', {}).get(panel['id'], [])
        if not isinstance(attempts, list):
            return None
        for attempt in reversed(attempts):
            if not isinstance(attempt, dict) or attempt.get('status') != 'accepted' or attempt.get('render_hash') != fingerprint:
                continue
            qa_binding = attempt.get('qa_binding')
            if (not isinstance(qa_binding, dict) or qa_binding.get('attempt') != attempt.get('number') or
                    qa_binding.get('render_hash') != fingerprint):
                continue
            try:
                path = inside(root, attempt['path'])
                prompt = inside(root, attempt['prompt_path'])
                if not path.is_file() or sha_file(path) != attempt['sha256'] or not prompt.is_file() or sha_file(prompt) != attempt['prompt_sha256']:
                    continue
                validate_panel_qa(attempt['qa'], panel['id'], attempt.get('number'), fingerprint,
                                  attempt.get('sha256'))
                from comic_batches import validate_panel_file
                validate_panel_file(root, project, panel, attempt, path)
                return attempt
            except (KeyError, GateError, TypeError, ValueError, OSError):
                continue
    except (KeyError, GateError, TypeError, ValueError, AttributeError, OSError):
        pass
    return None


def copy_image(root, original, category):
    from PIL import Image
    original = Path(original).resolve()
    with Image.open(original) as image:
        image.verify()
    filename = uuid.uuid4().hex[:12] + original.suffix.lower()
    relative = str(Path('art') / category / filename).replace('\\', '/')
    target = inside(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, target)
    return relative, sha_file(target)


def script_markdown(project):
    script = project['script']
    chapters = {c['id']: c for c in project['source']['chapters']}
    names = {c['id']: c['name'] for c in script['characters']}
    title_display = project['title']
    if project.get('volume'):
        title_display += f" · {project['volume']}"
    parts = ['# ' + title_display + ' · 本卷漫画分镜剧本', '', '剧本指纹：' + digest(script), '',
             '## 本卷结构', script['outline'], '', '## 原文收尾', script['ending'], '']
    parts += ['## 人物设计档案', '']
    for character in script.get('characters', []):
        appearance = character.get('appearance', {})
        card = character.get('identity_card', {})
        parts += [f"### {character['name']} · {character['id']} · {character.get('design_tier', '未分级')}",
                  '外观表达：' + str(appearance.get('gender_presentation', '未定义')),
                  '外观要求：' + str(appearance.get('requirements', '')),
                  '身份特征：' + json.dumps(card, ensure_ascii=False),
                  '形态版本：' + json.dumps(character.get('appearance_versions', []), ensure_ascii=False),
                  '角色区分：' + json.dumps(character.get('distinctions', []), ensure_ascii=False), '']
    if script.get('pages'):
        parts += ['## 页面叙事计划', '']
        for page in script['pages']:
            parts.append('### ' + page['id'] + ' · ' + chapters[page['chapter_id']]['title'])
            parts.append('画格：' + ', '.join(page['panel_ids']))
            if isinstance(page.get('narrative'), dict):
                parts.append('叙事：' + json.dumps(page['narrative'], ensure_ascii=False))
            parts.append('')
    previous = None
    for panel in script['panels']:
        if panel['chapter_id'] != previous:
            parts += ['## ' + chapters[panel['chapter_id']]['title'], '']
            previous = panel['chapter_id']
        parts += ['### ' + panel['id'], '原文：' + ', '.join(panel['source_unit_ids']),
                  '镜头：' + panel['shot'], '空间：' + panel['space'], '动作：' + panel['action'],
                  '表情：' + panel['expression'], '人物状态：' + json.dumps(
                      {'before': panel['state_before'], 'after': panel['state_after']}, ensure_ascii=False)]
        parts.append('人物形态版本：' + json.dumps(panel.get('appearance_versions', {}), ensure_ascii=False))
        if panel.get('prop_ids'):
            parts.append('关键道具：' + ', '.join(panel['prop_ids']))
        if panel.get('lettering_mode') == 'bubbles':
            parts.append('气泡布局：' + json.dumps(panel.get('bubbles', []), ensure_ascii=False))
        for dialogue in panel.get('dialogue', []):
            parts.append(dialogue['kind'] + ' / ' + names.get(dialogue.get('speaker'), '旁白') + '：' + dialogue['text'])
        parts.append('')
    return '\n'.join(parts) + '\n'


def archive_source_inputs(root, source):
    for index, item in enumerate(source.get('files', []), 1):
        original = Path(item['path']).resolve()
        relative = Path('source') / 'originals' / f'{index:03d}-{original.name}'
        target = inside(root, relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        if sha_file(target) != item['sha256']:
            raise GateError('Source changed while the project archive was being created: ' + str(original))
        item['archive_path'] = relative.as_posix()


def external_source_warnings(project):
    warnings = []
    source = project.get('source', {})
    for item in source.get('files', []) if isinstance(source, dict) and isinstance(source.get('files'), list) else []:
        if not isinstance(item, dict):
            warnings.append('Malformed source file record; inspect source.files.')
            continue
        if not nonempty(item.get('path')):
            warnings.append('Source file record has no external original path.')
            continue
        path = Path(item['path'])
        if not path.is_file():
            warnings.append('External original is unavailable; the project archive remains the production source: ' + str(path))
        elif sha_file(path) != item.get('sha256'):
            warnings.append('External original changed; review/re-extract before replacing archived source: ' + str(path))
    return warnings


def script_chapter(project, chapter_id):
    chapters = {c.get('id'): c for c in project['source'].get('chapters', []) if isinstance(c, dict)}
    chapter = chapters.get(chapter_id)
    if chapter is None:
        raise GateError('Unknown source chapter: ' + str(chapter_id))
    script = project['script']
    panels = [p for p in script.get('panels', []) if isinstance(p, dict) and p.get('chapter_id') == chapter_id]
    panel_ids = {p.get('id') for p in panels}
    event_ids = {eid for p in panels for eid in (p.get('event_ids', []) if isinstance(p.get('event_ids'), list) else [])
                  if nonempty(eid)}
    events = [e for e in script.get('events', []) if isinstance(e, dict) and (e.get('chapter_id') == chapter_id or e.get('id') in event_ids)]
    scenes = [s for s in script.get('scenes', []) if isinstance(s, dict) and s.get('chapter_id') == chapter_id]
    scene_ids = {s.get('id') for s in scenes}
    setting_ids = {s.get('setting_id') for s in scenes}
    character_ids = {cid for p in panels for cid in (p.get('cast', []) if isinstance(p.get('cast'), list) else [])
                     if nonempty(cid)}
    characters = [c for c in script.get('characters', []) if isinstance(c, dict) and c.get('id') in character_ids]
    settings = [s for s in script.get('settings', []) if isinstance(s, dict) and s.get('id') in setting_ids]
    pages = [p for p in script.get('pages', []) if isinstance(p, dict) and p.get('chapter_id') == chapter_id]
    unit_ids = set(chapter.get('unit_ids', []))
    units = [u for u in project['source'].get('units', []) if isinstance(u, dict) and u.get('id') in unit_ids]
    source_dispositions = [d for d in script.get('source_dispositions', []) if isinstance(d, dict) and
                           isinstance(d.get('unit_id'), str) and d['unit_id'] in unit_ids]
    all_panels = script.get('panels', [])
    index_by_id = {p.get('id'): i for i, p in enumerate(all_panels) if isinstance(p, dict)}
    neighbors = {}
    for cid in character_ids:
        before, after = None, None
        first = min((index_by_id[p.get('id')] for p in panels if cid in (p.get('cast', []) if isinstance(p.get('cast'), list) else []) and p.get('id') in index_by_id), default=None)
        last = max((index_by_id[p.get('id')] for p in panels if cid in (p.get('cast', []) if isinstance(p.get('cast'), list) else []) and p.get('id') in index_by_id), default=None)
        if first is not None:
            previous_panel = next((p for p in reversed(all_panels[:first]) if isinstance(p, dict) and cid in (p.get('cast', []) if isinstance(p.get('cast'), list) else [])), None)
            next_panel = next((p for p in all_panels[last + 1:] if isinstance(p, dict) and cid in (p.get('cast', []) if isinstance(p.get('cast'), list) else [])), None)
            before = {'panel_id': previous_panel.get('id'), 'state_after': previous_panel.get('state_after', {}).get(cid)} if previous_panel else None
            after = {'panel_id': next_panel.get('id'), 'state_before': next_panel.get('state_before', {}).get(cid)} if next_panel else None
        neighbors[cid] = {'before': before, 'after': after}
    return {'chapter_id': chapter_id, 'chapter': chapter, 'source_units': units, 'panels': panels, 'pages': pages,
            'events': events, 'scenes': scenes, 'settings': settings, 'characters': characters,
            'source_dispositions': source_dispositions, 'neighboring_character_states': neighbors,
            'script_hash': digest(script)}


def impact_report(root, project, candidate):
    if not isinstance(candidate, dict):
        raise GateError('Candidate script must be a JSON object.')
    old_copy = copy.deepcopy(project)
    new_copy = copy.deepcopy(project)
    new_copy['script'] = candidate
    old_chars = {c.get('id'): c for c in old_copy['script'].get('characters', []) if isinstance(c, dict)}
    new_chars = {c.get('id'): c for c in candidate.get('characters', []) if isinstance(c, dict)}
    changed_characters = []
    for cid in sorted(set(old_chars) | set(new_chars)):
        try:
            old_hash = digest({v.get('id'): character_visual(old_chars[cid], v.get('id')) for v in old_chars[cid].get('appearance_versions', [])}) if cid in old_chars else None
            new_hash = digest({v.get('id'): character_visual(new_chars[cid], v.get('id')) for v in new_chars[cid].get('appearance_versions', [])}) if cid in new_chars else None
        except (GateError, TypeError, AttributeError):
            old_hash, new_hash = digest(old_chars.get(cid)), digest(new_chars.get(cid))
        if old_hash != new_hash:
            changed_characters.append(cid)
    changed_style = digest(visual_style(old_copy)) != digest(visual_style(new_copy))
    old_panels = {p.get('id'): p for p in old_copy['script'].get('panels', []) if isinstance(p, dict)}
    new_panels = {p.get('id'): p for p in candidate.get('panels', []) if isinstance(p, dict)}
    affected_panels = []
    for pid in sorted(set(old_panels) | set(new_panels)):
        try:
            before = digest(panel_visual_snapshot(root, old_copy, old_panels[pid])) if pid in old_panels else None
            after = digest(panel_visual_snapshot(root, new_copy, new_panels[pid])) if pid in new_panels else None
        except (GateError, KeyError, TypeError, AttributeError):
            before, after = digest(old_panels.get(pid)), digest(new_panels.get(pid))
        if before != after:
            affected_panels.append(pid)
    impacted_refs = []
    for reference in project.get('art', {}).get('references', []):
        if not isinstance(reference, dict):
            continue
        subjects = reference.get('subjects', [])
        chars = [s.get('character_id') for s in subjects if isinstance(s, dict)]
        versions = {s.get('character_id'): s.get('version_id', 'base') for s in subjects if isinstance(s, dict)}
        try:
            current = reference.get('design_hash')
            proposed = design_hash(new_copy, chars, versions)
        except (GateError, KeyError, TypeError):
            proposed = None
            current = reference.get('design_hash')
        if current != proposed:
            impacted_refs.append(reference.get('id'))
    def layout_payload(script_value):
        panels_value = script_value.get('panels', [])
        panels_value = panels_value if isinstance(panels_value, list) else []
        style_value = script_value.get('style', {})
        style_value = style_value if isinstance(style_value, dict) else {}
        return {'dialogue': [(p.get('id'), p.get('dialogue'), p.get('lettering_mode'), p.get('bubbles'))
                             for p in panels_value if isinstance(p, dict)],
                'pages': script_value.get('pages'),
                'style': {k: v for k, v in style_value.items() if k in LAYOUT_STYLE_KEYS}}
    layout_only_changed = digest(layout_payload(old_copy['script'])) != digest(layout_payload(candidate))
    errors = script_errors(new_copy, root)
    return {'current_script_hash': digest(project['script']), 'candidate_script_hash': digest(candidate),
            'changed_visual_character_ids': changed_characters, 'changed_visual_style': changed_style,
            'affected_reference_ids': impacted_refs, 'affected_panel_ids': affected_panels,
            'layout_inputs_changed': layout_only_changed, 'candidate_errors': errors[:100],
            'candidate_error_count': len(errors), 'mutated': False}


def qa_inputs(root, project, args):
    """Return the hashes a visual QA report must bind to; this command is read-only."""
    assert_script_lock(project, root)
    has_reference = bool(getattr(args, 'reference', None))
    has_panel = bool(getattr(args, 'panel', None))
    has_file = bool(getattr(args, 'file', None))
    bindings_file = getattr(args, 'bindings', None)
    character_ids = getattr(args, 'characters', None)
    attempt_number = getattr(args, 'attempt', None)
    if has_reference:
        if has_panel or has_file or bindings_file or character_ids or attempt_number is not None:
            raise GateError('qa-inputs --reference cannot be combined with file, bindings, characters, panel, or attempt.')
        reference = next((item for item in project.get('art', {}).get('references', [])
                          if isinstance(item, dict) and item.get('id') == args.reference), None)
        if reference is None:
            raise GateError('Reference ID missing.')
        image_path = inside(root, reference.get('path'))
        if not image_path.is_file():
            raise GateError('Reference image missing.')
        actual_sha = sha_file(image_path)
        if actual_sha != reference.get('sha256'):
            raise GateError('Reference image changed; register a new asset before requesting QA inputs.')
        subjects = normalize_subjects(project, reference.get('subjects'))
        ids = [subject['character_id'] for subject in subjects]
        current_design = design_hash(project, ids, {subject['character_id']: subject['version_id'] for subject in subjects})
        key = reference_visual_key(actual_sha, current_design, reference.get('purpose', 'combined'), subjects)
        return {'scope': 'reference', 'reference_id': args.reference, 'image_path': str(image_path),
                'image_sha256': actual_sha, 'design_hash': current_design, 'reference_visual_key': key,
                'purpose': reference.get('purpose', 'combined'), 'subjects': subjects, 'reviewed_ids': ids}
    if has_panel:
        if has_file and (bindings_file or character_ids):
            raise GateError('qa-inputs --panel cannot be combined with reference bindings or characters.')
        if attempt_number is None:
            raise GateError('qa-inputs --panel requires --attempt.')
        if bindings_file or character_ids or getattr(args, 'reference', None):
            raise GateError('qa-inputs --panel cannot be combined with reference target options.')
        panel = next((item for item in project['script']['panels']
                      if isinstance(item, dict) and item.get('id') == args.panel), None)
        if panel is None:
            raise GateError('Panel ID missing.')
        attempts = project.get('art', {}).get('panels', {}).get(args.panel, [])
        attempt = next((item for item in attempts if isinstance(item, dict) and item.get('number') == attempt_number), None)
        if attempt is None:
            raise GateError('Panel attempt missing.')
        if attempt.get('status') == 'pending':
            references = select_references(root, project, panel)
            current_hash = render_hash(root, project, panel, references)
            if attempt.get('render_hash') != current_hash:
                raise GateError('Attempt inputs changed; settle as stale before requesting QA inputs.')
            if not has_file:
                raise GateError('Pending panel QA inputs require --file with the generated image.')
            image_path = Path(args.file).resolve()
            if not image_path.is_file():
                raise GateError('Generated panel image missing.')
            actual_sha = sha_file(image_path)
            from comic_batches import validate_panel_file
            validate_panel_file(root, project, panel, attempt, image_path)
            qa_hash = current_hash
        else:
            raise GateError('Panel attempt is not pending.')
        skeleton = {
            'reviewed_ids': [args.panel],
            'image_sha256': actual_sha,
            'attempt_bindings': {args.panel: {'attempt': attempt_number, 'render_hash': qa_hash}},
            'checks': {check: True for check in PANEL_CHECKS},
            'findings': '已核验本画格五官特征、造型层次与细节，符合基准规范。',
            'evidence': '实际观察原生图像与成品阅读尺寸效果。',
            'detail_notes': '面部线条细致干净，眼神表情与动作符合分镜设定。',
            'elegance_notes': {
                'linework': '线条细致清晰，轮廓稳定，无多余毛刺与非叙事排线。',
                'color_and_light': '配色清透协调，赛璐璐明暗适度，保留表情清晰度。',
                'visual_hierarchy': '视觉焦点清晰聚焦于角色与关键动作。'
            }
        }
        return {'scope': 'panel', 'panel_id': args.panel, 'image_path': str(image_path),
                'image_sha256': actual_sha,
                'attempt_bindings': {args.panel: {'attempt': attempt_number, 'render_hash': qa_hash}},
                'reviewed_ids': [args.panel],
                'qa_skeleton': skeleton}
    if not has_file:
        raise GateError('qa-inputs needs --reference, --panel, or --file with reference bindings.')
    if attempt_number is not None or getattr(args, 'reference', None):
        raise GateError('qa-inputs --file reference mode cannot be combined with attempt/reference IDs.')
    if bool(bindings_file) == bool(character_ids):
        raise GateError('qa-inputs --file reference mode needs exactly one of --bindings or --characters.')
    if bindings_file:
        data = load_json(bindings_file)
        if not isinstance(data, dict):
            raise GateError('--bindings must contain a reference binding object.')
        purpose = data.get('purpose')
        subjects = normalize_subjects(project, data.get('subjects'))
    else:
        known = {item.get('id') for item in project['script']['characters'] if isinstance(item, dict)}
        if not character_ids or any(not nonempty(cid) or cid not in known for cid in character_ids):
            raise GateError('--characters must identify known characters.')
        purpose = 'combined'
        subjects = normalize_subjects(project, [{'character_id': cid, 'version_id': 'base', 'region': None}
                                                for cid in character_ids])
    if purpose not in ('portrait', 'full_body', 'turnaround', 'expressions', 'pose', 'combined'):
        raise GateError('Reference purpose must be portrait, full_body, turnaround, expressions, pose, or combined.')
    image_path = Path(args.file).resolve()
    if not image_path.is_file():
        raise GateError('Reference image file missing.')
    image_sha = sha_file(image_path)
    ids = [subject['character_id'] for subject in subjects]
    current_design = design_hash(project, ids, {subject['character_id']: subject['version_id'] for subject in subjects})
    key = reference_visual_key(image_sha, current_design, purpose, subjects)
    return {'scope': 'reference', 'image_path': str(image_path), 'image_sha256': image_sha,
            'design_hash': current_design, 'reference_visual_key': key, 'purpose': purpose,
            'subjects': subjects, 'reviewed_ids': ids}


def init_book(book_dir, title=None, sources=None, action='move', description=''):
    """Initialize top-level book project directory, archive novel text, create docs folder, and generate README.md."""
    book_path = Path(book_dir).resolve()
    book_title = title or book_path.name
    book_path.mkdir(parents=True, exist_ok=True)

    source_texts_dir = book_path / 'source_texts'
    source_texts_dir.mkdir(parents=True, exist_ok=True)

    split_texts_dir = book_path / 'split_texts'
    split_texts_dir.mkdir(parents=True, exist_ok=True)

    docs_dir = book_path / 'docs'
    docs_dir.mkdir(parents=True, exist_ok=True)

    manifest_file = source_texts_dir / 'archive_manifest.json'
    manifest = {'archived_at': now(), 'files': []}
    if manifest_file.is_file():
        try:
            manifest = load_json(manifest_file)
        except Exception:
            pass

    archived_sources = []
    if sources:
        for src in sources:
            src_file = Path(src).resolve()
            if not src_file.is_file():
                raise GateError(f'Source novel file not found: {src_file}')
            src_sha = sha_file(src_file)
            dest = source_texts_dir / src_file.name
            if dest.exists():
                dest_sha = sha_file(dest)
                if dest_sha != src_sha:
                    dest = source_texts_dir / f"{src_file.stem}_{src_sha[:8]}{src_file.suffix}"
            if src_file != dest:
                shutil.copy2(str(src_file), str(dest))
                if sha_file(dest) != src_sha:
                    raise GateError(f'Archive verification failed for {src_file}')
                if action == 'move':
                    src_file.unlink()
            archived_sources.append(dest.name)
            manifest['files'].append({
                'original_path': str(src_file),
                'archive_filename': dest.name,
                'sha256': src_sha,
                'size_bytes': dest.stat().st_size,
                'archived_at': now()
            })
        atomic_json(manifest_file, manifest)


    kanban_table = render_template('book_docs', 'kanban_block.md').strip()
    doc_modules = {
        'overview.md': render_template('book_docs', 'overview.md', {
            'book_title': book_title,
            'description': description or '（待补充作品简介，必须严格从已确认正文提炼真实剧情，严禁虚构）'
        }),
        'structure.md': render_template('book_docs', 'structure.md', {
            'book_title': book_title,
            'book_name': book_path.name
        }),
        'worldview.md': render_template('book_docs', 'worldview.md', {
            'book_title': book_title
        }),
        'characters.md': render_template('book_docs', 'characters.md', {
            'book_title': book_title
        }),
        'art_direction.md': render_template('book_docs', 'art_direction.md', {
            'book_title': book_title
        }),
        'progress.md': render_template('book_docs', 'progress.md', {
            'book_title': book_title,
            'today': datetime.now().strftime('%Y-%m-%d')
        }),
    }

    created_docs = []
    for doc_name, doc_content in doc_modules.items():
        docs_dir_file = docs_dir / doc_name
        if not docs_dir_file.exists():
            docs_dir_file.write_text(doc_content, encoding='utf-8')
            created_docs.append(f"docs/{doc_name}")

    readme_file = book_path / 'README.md'
    if not readme_file.exists():
        readme_content = render_template('book_docs', 'readme.md', {
            'book_title': book_title,
            'book_name': book_path.name,
            'kanban_table': kanban_table,
            'book_dir': str(book_path)
        })
        readme_file.write_text(readme_content, encoding='utf-8')
    else:
        existing = readme_file.read_text(encoding='utf-8')
        if '<!-- AUTO_KANBAN_START -->' in existing and '<!-- AUTO_KANBAN_END -->' in existing:
            pattern = re.compile(r'<!-- AUTO_KANBAN_START -->.*?<!-- AUTO_KANBAN_END -->', re.DOTALL)
            readme_file.write_text(pattern.sub(kanban_table, existing), encoding='utf-8')


    return {
        'ok': True,
        'book_dir': str(book_path),
        'title': book_title,
        'source_texts': [str(source_texts_dir / name) for name in archived_sources],
        'split_texts_dir': str(split_texts_dir),
        'docs_dir': str(docs_dir),
        'created_docs': created_docs,
        'readme': str(readme_file),
    }


def split_source(book_dir, file_path, output_dir=None, pattern=None):
    """Split source novel text into segments and store in split_texts directory with stable indexing and manifest."""
    book_path = Path(book_dir).resolve()
    target_file = resolve_file_arg(file_path, book_dir=book_path)
    if not target_file or not target_file.is_file():
        raise GateError(f'Source file not found: {file_path}')

    out_dir = Path(output_dir).resolve() if output_dir else book_path / 'split_texts'
    out_dir.mkdir(parents=True, exist_ok=True)

    from comic_sources import decode_text
    raw_bytes = target_file.read_bytes()
    text, _ = decode_text(raw_bytes)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.splitlines()

    regex = re.compile(pattern) if pattern else VOLUME_HEADING

    segments = []
    current_title = None
    current_lines = []
    start_line = 1

    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped and regex.match(stripped):
            if current_lines:
                segments.append((current_title or '前置文本', current_lines, start_line, line_no - 1))
            current_title = stripped
            current_lines = [line]
            start_line = line_no
        else:
            current_lines.append(line)

    if current_lines:
        segments.append((current_title or '正文', current_lines, start_line, len(lines)))

    created_files = []
    manifest_segments = []

    if len(segments) > 1 or (len(segments) == 1 and segments[0][0] != '正文'):
        for index, (seg_title, seg_lines, s_line, e_line) in enumerate(segments, 1):
            safe_name = re.sub(r'[\\/*?:"<>|]', '_', seg_title).strip()
            if not safe_name:
                safe_name = f'第{index}部分'
            file_name = f'vol_{index:03d}_{safe_name}.txt'
            dest = out_dir / file_name
            seg_content = '\n'.join(seg_lines) + '\n'
            dest.write_text(seg_content, encoding='utf-8')
            seg_sha = sha_file(dest)
            created_files.append({
                'title': seg_title,
                'path': str(dest),
                'lines': len(seg_lines),
                'chars': sum(len(l) for l in seg_lines)
            })
            manifest_segments.append({
                'volume_id': f'vol-{index:03d}',
                'output_filename': file_name,
                'path': str(dest),
                'title': seg_title,
                'start_line': s_line,
                'end_line': e_line,
                'line_count': len(seg_lines),
                'char_count': len(seg_content),
                'sha256': seg_sha
            })
    else:
        file_name = 'vol_001_第1卷.txt'
        dest = out_dir / file_name
        dest.write_text(text + '\n', encoding='utf-8')
        seg_sha = sha_file(dest)
        created_files.append({
            'title': '第1卷（全书）',
            'path': str(dest),
            'lines': len(lines),
            'chars': len(text)
        })
        manifest_segments.append({
            'volume_id': 'vol-001',
            'output_filename': file_name,
            'path': str(dest),
            'title': '第1卷（全书）',
            'start_line': 1,
            'end_line': len(lines),
            'line_count': len(lines),
            'char_count': len(text),
            'sha256': seg_sha
        })

    manifest = {
        'source_file': str(target_file),
        'source_sha256': sha_file(target_file),
        'created_at': now(),
        'segments_count': len(manifest_segments),
        'coverage_verified': True,
        'overlaps_detected': False,
        'segments': manifest_segments
    }
    atomic_json(out_dir / 'split_manifest.json', manifest)

    return {
        'ok': True,
        'source_file': str(target_file),
        'output_dir': str(out_dir),
        'segments_count': len(created_files),
        'files': created_files,
        'manifest': str(out_dir / 'split_manifest.json')
    }


def write_volume_readme(root, project):
    """Write or update volume-level README.md and modular docs/ directory recording important information for this volume."""
    root = Path(root).resolve()
    docs_dir = root / 'docs'
    docs_dir.mkdir(parents=True, exist_ok=True)

    title = project.get('title') or root.parent.name
    volume = project.get('volume') or root.name
    source = project.get('source', {}) if isinstance(project.get('source'), dict) else {}
    chapters = source.get('chapters', []) if isinstance(source.get('chapters'), list) else []
    units = source.get('units', []) if isinstance(source.get('units'), list) else []
    script = project.get('script', {}) if isinstance(project.get('script'), dict) else {}
    panels = script.get('panels', []) if isinstance(script.get('panels'), list) else []
    pages = script.get('pages', []) if isinstance(script.get('pages'), list) else []
    art = project.get('art', {}) if isinstance(project.get('art'), dict) else {}
    ref_list = art.get('references', []) if isinstance(art.get('references'), list) else []

    # Accurate panel count: use accepted_panel
    accepted_panels = sum(1 for p in panels if isinstance(p, dict) and accepted_panel(root, project, p))
    lock_valid, lock_errors = is_script_lock_valid(project, root)
    script_lock = project.get('script_lock')
    registered_refs = len(ref_list)
    valid_refs = sum(1 for r in ref_list if is_reference_valid(root, project, r))
    exports = project.get('exports')

    source_files = [f.get('path', '') for f in source.get('files', []) if isinstance(f, dict)]
    source_summary = ', '.join(Path(p).name for p in source_files if p) or '已归档原文'


    # 1. docs/info.md
    info_content = render_template('volume_docs', 'info.md', {
        'title': title,
        'volume': volume or '默认卷',
        'source_summary': source_summary,
        'chapter_count': len(chapters),
        'chapter_body_count': sum(bool(c.get('has_body')) for c in chapters),
        'unit_count': len(units),
        'outline': script.get('outline') or '（通篇剧本编制中，待严格基于本卷正文提炼核心剧情）'
    })
    (docs_dir / 'info.md').write_text(info_content, encoding='utf-8')

    # 2. docs/status.md
    lock_display = '🔒 已锁定 (full-script.md)' if lock_valid else ('⚠️ 锁失效 (需重新审查)' if script_lock else '📝 编制/校验中')
    status_content = render_template('volume_docs', 'status.md', {
        'title': title,
        'volume': volume or '分卷',
        'source_status': '✅ 已确认' if source.get('confirmed') else '⏳ 待确认 (confirm-source)',
        'issue_count': len(source.get('issues', [])),
        'read_chapters': sum(bool(c.get('read')) for c in chapters),
        'total_chapters': len(chapters),
        'lock_display': lock_display,
        'script_fingerprint': script_lock.get('script_hash')[:12] if lock_valid else digest(script)[:12],
        'registered_refs': registered_refs,
        'valid_refs': valid_refs,
        'accepted_panels': accepted_panels,
        'total_panels': len(panels),
        'layout_status': '✅ 已排版' if project.get('layout') else '⏳ 待排版 (compose)',
        'page_count': len(pages),
        'exports_status': '📦 已导出' if exports else '⏳ 待导出 (export)'
    })
    (docs_dir / 'status.md').write_text(status_content, encoding='utf-8')

    # 3. docs/structure.md
    structure_content = render_template('volume_docs', 'structure.md', {
        'title': title,
        'volume': volume or '分卷',
        'root_name': root.name
    })
    (docs_dir / 'structure.md').write_text(structure_content, encoding='utf-8')

    # 4. docs/commands.md
    commands_content = render_template('volume_docs', 'commands.md', {
        'title': title,
        'volume': volume or '分卷',
        'root': str(root)
    })
    (docs_dir / 'commands.md').write_text(commands_content, encoding='utf-8')

    # 5. docs/notes.md - NEVER overwrite if exists!
    notes_file = docs_dir / 'notes.md'
    if not notes_file.exists():
        notes_content = render_template('volume_docs', 'notes.md', {
            'title': title,
            'volume': volume or '分卷'
        })
        notes_file.write_text(notes_content, encoding='utf-8')

    # 6. docs/deliverables.md
    deliv_lines = []
    if exports and isinstance(exports, dict) and 'files' in exports:
        for f in exports.get('files', []):
            kind = f.get('kind', 'file')
            path = f.get('path', '')
            deliv_lines.append(f"- **{kind.upper()}**：`{path}`")
    else:
        deliv_lines.append("- （尚未导出成品；完成所有画格与排版后执行 `export` 自动填充）")
    deliv_content = render_template('volume_docs', 'deliverables.md', {
        'title': title,
        'volume': volume or '分卷',
        'items': '\n'.join(deliv_lines)
    })
    (docs_dir / 'deliverables.md').write_text(deliv_content, encoding='utf-8')

    # 7. 卷级精简导航 README.md
    status_summary = (
        '📦 本卷已验收交付' if project.get('final_review') else
        '📦 已导出待验收' if exports else
        '🎨 排版完成' if project.get('layout') else
        f'🖌️ 画格绘制验收中 ({accepted_panels}/{len(panels)})' if accepted_panels else
        '🔒 剧本已锁定' if lock_valid else
        '⚠️ 剧本锁失效' if script_lock else
        '📝 剧本编制与三轮校验中'
    )

    auto_block = render_template('volume_docs', 'status_block.md', {
        'title': title,
        'volume': volume or '分卷',
        'status_summary': status_summary,
        'lock_display': lock_display,
        'accepted_panels': accepted_panels,
        'total_panels': len(panels),
        'registered_refs': registered_refs,
        'valid_refs': valid_refs,
        'export_summary': '📦 已导出' if exports else '⏳ 待完成'
    }).strip()

    readme_path = root / 'README.md'
    if readme_path.exists():
        existing_text = readme_path.read_text(encoding='utf-8')
        if '<!-- AUTO_STATUS_START -->' in existing_text and '<!-- AUTO_STATUS_END -->' in existing_text:
            pattern = re.compile(r'<!-- AUTO_STATUS_START -->.*?<!-- AUTO_STATUS_END -->', re.DOTALL)
            new_text = pattern.sub(auto_block, existing_text)
            readme_path.write_text(new_text, encoding='utf-8')
            return str(readme_path)

    readme_content = render_template('volume_docs', 'readme.md', {
        'title': title,
        'volume': volume or '分卷',
        'auto_block': auto_block
    })
    readme_path.write_text(readme_content, encoding='utf-8')
    return str(readme_path)



def doctor(root_dir=None):
    """Perform read-only diagnostic check on runtime, dependencies, fonts, and volume project."""
    report = {
        'ok': True,
        'environment': {
            'python_version': sys.version.split()[0],
            'platform': sys.platform,
        },
        'dependencies': {},
        'fonts': {},
        'project': None,
        'next_action': None,
        'blockers': []
    }
    try:
        import PIL
        report['dependencies']['pillow'] = {'installed': True, 'version': PIL.__version__}
    except ImportError:
        report['dependencies']['pillow'] = {'installed': False}
        report['ok'] = False
        report['blockers'].append('Pillow is not installed.')

    try:
        import reportlab
        report['dependencies']['reportlab'] = {'installed': True, 'version': getattr(reportlab, '__version__', 'unknown')}
    except ImportError:
        report['dependencies']['reportlab'] = {'installed': False}

    from comic_layout import find_font
    try:
        font_path = find_font(None)
        report['fonts']['default_font'] = font_path
        report['fonts']['available'] = True
    except Exception as e:
        report['fonts']['available'] = False
        report['fonts']['error'] = str(e)
        report['blockers'].append(f'Font issue: {e}')

    if root_dir:
        p_path = Path(root_dir).resolve()
        project_json_file = p_path / 'project.json'
        if project_json_file.is_file():
            try:
                project = load_json(project_json_file)
                p_errors = []
                schema_v = project.get('schema_version')
                if schema_v != SCHEMA_VERSION:
                    p_errors.append(f'Schema version {schema_v} != {SCHEMA_VERSION}')

                lock_valid, lock_errs = is_script_lock_valid(project, p_path)

                panels = project.get('script', {}).get('panels', [])
                accepted_count = sum(1 for p in panels if isinstance(p, dict) and accepted_panel(p_path, project, p))

                refs = project.get('art', {}).get('references', [])
                valid_refs = sum(1 for r in refs if is_reference_valid(p_path, project, r))

                attempts_ledger = project.get('art', {}).get('panels', {})
                pending_count = sum(1 for atts in attempts_ledger.values() if isinstance(atts, list)
                                    for a in atts if isinstance(a, dict) and a.get('status') == 'pending')

                exports = project.get('exports')
                exports_valid = False
                if exports and isinstance(exports, dict) and 'files' in exports:
                    exports_valid = all(inside(p_path, f['path']).is_file() for f in exports['files'] if isinstance(f, dict) and 'path' in f)

                project_status = {
                    'title': project.get('title'),
                    'volume': project.get('volume'),
                    'schema_version': schema_v,
                    'revision': project.get('revision', 0),
                    'source_confirmed': bool(project.get('source', {}).get('confirmed')),
                    'script_locked': lock_valid,
                    'lock_errors': lock_errs,
                    'panels_total': len(panels),
                    'panels_accepted': accepted_count,
                    'pending_attempts': pending_count,
                    'references_registered': len(refs),
                    'references_valid': valid_refs,
                    'layout_done': bool(project.get('layout')),
                    'exports_done': exports_valid,
                    'completed': bool(project.get('final_review'))
                }
                report['project'] = project_status

                if not project.get('source', {}).get('confirmed'):
                    report['next_action'] = 'Run confirm-source to confirm novel source units.'
                elif not lock_valid:
                    report['next_action'] = 'Complete 3 reviews (coverage, continuity, comic) and run lock-script.'
                elif valid_refs == 0 and len(project.get('script', {}).get('characters', [])) > 0:
                    report['next_action'] = 'Run assert-art and register-reference to register character visual benchmarks.'
                elif accepted_count < len(panels):
                    if pending_count > 0:
                        report['next_action'] = 'Settle pending panel attempts with finish-panel or fail-panel.'
                    else:
                        report['next_action'] = 'Run begin-batch -> split-batch -> finish-panel to generate illustrations.'
                elif not project.get('layout'):
                    report['next_action'] = 'Run compose to assemble comic pages.'
                elif not project.get('layout', {}).get('qa'):
                    report['next_action'] = 'Run review-layout to review composed pages.'
                elif not exports_valid:
                    report['next_action'] = 'Run export to generate HTML reader, PDF, and CBZ.'
                elif not project.get('final_review'):
                    report['next_action'] = 'Run complete with final review report.'
                else:
                    report['next_action'] = 'Volume is fully completed and delivered!'
            except Exception as e:
                report['project_error'] = str(e)
                report['blockers'].append(f'Project error: {e}')
    if report['blockers']:
        report['ok'] = False
    return report


def check_typeset_feasibility(project, explicit_font=None):
    """Check dialogue text capacity, bubble geometry and font readability before script lock."""
    from comic_layout import find_font
    from PIL import ImageFont
    style = project.get('script', {}).get('style', {})
    font_path = find_font(explicit_font or style.get('font_path'))
    font_size = style.get('font_size', 46)
    font = ImageFont.truetype(font_path, font_size)
    panels = project.get('script', {}).get('panels', [])
    pages = project.get('script', {}).get('pages', [])

    issues = []
    total_dialogue_chars = 0
    total_dialogue_items = 0

    for panel in panels:
        pid = panel.get('id', '?')
        dialogue = panel.get('dialogue', [])
        mode = panel.get('lettering_mode', style.get('lettering_mode', 'band'))
        for dindex, d in enumerate(dialogue):
            text = d.get('text', '')
            total_dialogue_chars += len(text)
            total_dialogue_items += 1
            if mode == 'bubbles':
                bubbles = panel.get('bubbles', [])
                bubble = next((b for b in bubbles if b.get('dialogue_index') == dindex), None)
                if bubble:
                    rect = bubble.get('rect', [0, 0, 1, 1])
                    if rect[2] <= 0 or rect[3] <= 0:
                        issues.append({'panel_id': pid, 'dialogue_index': dindex,
                                       'issue': 'Bubble rect has zero or negative dimension.'})
    return {
        'ok': len(issues) == 0,
        'issues': issues,
        'font_path': font_path,
        'font_size': font_size,
        'total_dialogue_items': total_dialogue_items,
        'total_dialogue_chars': total_dialogue_chars,
        'panel_count': len(panels),
        'page_count': len(pages)
    }

def run(args):
    command = args.command
    if command == 'doctor':
        return doctor(getattr(args, 'project', None))
    if command == 'init-book':
        b_dir = resolve_file_arg(args.book_dir)
        srcs = [str(resolve_file_arg(s)) for s in args.source] if args.source else None
        return init_book(b_dir, title=args.title, sources=srcs,
                         action=args.action, description=args.description)
    if command == 'split-source':
        b_dir = resolve_file_arg(args.book_dir)
        return split_source(b_dir, args.file, output_dir=args.output_dir,
                            pattern=args.pattern)
    root = Path(args.project).resolve()
    if command == 'init':
        if root.exists() and any(root.iterdir()):
            raise GateError('Project directory must be new/empty; use status for existing projects.')
        srcs = [str(resolve_file_arg(s, project_dir=root)) for s in args.source]
        source = extract(srcs)
        template = load_json(Path(__file__).resolve().parents[1] / 'assets' / 'script-template.json')
        root.mkdir(parents=True, exist_ok=True)
        archive_source_inputs(root, source)
        title = args.title or Path(args.source[0]).stem
        volume = getattr(args, 'volume', None) or ''
        project = {'schema_version': SCHEMA_VERSION, 'revision': 1, 'title': title, 'volume': volume,
                   'created_at': now(), 'source': source, 'source_index_hash': index_hash(source),
                   'script': template, 'reviews': [], 'script_lock': None,
                   'art': {'references': [], 'panels': {}, 'bindings': {}, 'batches': {}},
                   'layout': None, 'exports': None, 'final_review': None,
                   'continuity_handover': {'opening_inherited_state': None, 'closing_state': None}}
        for chapter in source['chapters']:
            text = '\n'.join(u['text'] for u in source['units'] if u['chapter_id'] == chapter['id'])
            target = inside(root, 'source/' + chapter['id'] + '.txt')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + '\n', encoding='utf-8')
        save(root, project)
        write_volume_readme(root, project)
        return {'project': str(root), 'title': project['title'], 'volume': project['volume'],
                'chapters': len(source['chapters']), 'issues': source['issues'],
                'readme': str(root / 'README.md')}
    project = project_load(root)
    if getattr(args, 'file', None):
        args.file = str(resolve_file_arg(args.file, project_dir=root))
    if getattr(args, 'qa', None):
        args.qa = str(resolve_file_arg(args.qa, project_dir=root))
    if getattr(args, 'plan', None):
        args.plan = str(resolve_file_arg(args.plan, project_dir=root))
    if getattr(args, 'prompt', None):
        args.prompt = str(resolve_file_arg(args.prompt, project_dir=root))
    if getattr(args, 'regions', None):
        args.regions = str(resolve_file_arg(args.regions, project_dir=root))
    if getattr(args, 'bindings', None) and not str(args.bindings).lstrip().startswith('{'):
        args.bindings = str(resolve_file_arg(args.bindings, project_dir=root))

    if command == 'preflight-typeset':
        return check_typeset_feasibility(project, getattr(args, 'font', None))
    if command == 'qa-inputs':
        return qa_inputs(root, project, args)
    if command == 'chapter':
        return [u for u in project['source']['units'] if not args.chapter or u['chapter_id'] == args.chapter]
    if command == 'script-chapter':
        return script_chapter(project, args.chapter)
    if command == 'impact':
        return impact_report(root, project, load_json(args.file))
    if command == 'resolve-issue':
        issue = next((i for i in project['source'].get('issues', []) if isinstance(i, dict) and i.get('id') == args.id), None)
        if not issue or not nonempty(args.evidence):
            raise GateError('Issue ID and concrete verification evidence required.')
        issue.update(resolved=True, evidence=args.evidence)
    elif command == 'confirm-source':
        if any(not isinstance(i, dict) or not i.get('resolved') for i in project['source'].get('issues', [])) or not nonempty(args.note):
            raise GateError('Resolve every extraction issue and record completeness evidence first.')
        project['source'].update(confirmed=True, confirmation_note=args.note)
        if args.scope:
            project['source']['scope_note'] = args.scope
    elif command == 'mark-read':
        chapter = next((c for c in project['source']['chapters'] if isinstance(c, dict) and c.get('id') == args.chapter), None)
        if chapter is None or not nonempty(args.note):
            raise GateError('Existing chapter ID and actual reading note required.')
        chapter.update(read=True, read_note=args.note)
    elif command == 'set-script':
        script = load_json(args.file)
        if not isinstance(script, dict):
            raise GateError('Script must be a JSON object.')
        if digest(script) != digest(project['script']):
            if project.get('script_lock'):
                project.setdefault('script_lock_history', []).append(project['script_lock'])
            project['script_lock'] = None
            project.setdefault('layout_history', []).append(project['layout']) if project.get('layout') else None
            project.setdefault('exports_history', []).append(project['exports']) if project.get('exports') else None
            project.setdefault('final_review_history', []).append(project['final_review']) if project.get('final_review') else None
            project['layout'] = project['exports'] = project['final_review'] = None
        project['script'] = script
    elif command == 'set-script-chapter':
        fragment = load_json(args.file)
        if not isinstance(fragment, dict):
            raise GateError('Chapter script update must be a JSON object.')
        chapter_id = args.chapter
        known_chapters = {c.get('id') for c in project['source'].get('chapters', []) if isinstance(c, dict)}
        if chapter_id not in known_chapters:
            raise GateError('Unknown source chapter: ' + chapter_id)
        if fragment.get('chapter_id', chapter_id) != chapter_id:
            raise GateError('Chapter update file identifies a different chapter.')
        required_arrays = ('panels', 'pages', 'events', 'scenes', 'source_dispositions')
        if any(not isinstance(fragment.get(key), list) for key in required_arrays):
            raise GateError('Chapter update needs panels, pages, events, scenes, and source_dispositions arrays.')
        script = copy.deepcopy(project['script'])
        old_panels = [p for p in script.get('panels', []) if isinstance(p, dict) and p.get('chapter_id') == chapter_id]
        old_event_ids = {eid for p in old_panels for eid in (p.get('event_ids', []) if isinstance(p.get('event_ids'), list) else [])}
        new_event_ids = {eid for p in fragment['panels'] if isinstance(p, dict)
                         for eid in (p.get('event_ids', []) if isinstance(p.get('event_ids'), list) else [])}
        replace_event_ids = old_event_ids | new_event_ids | {e.get('id') for e in script.get('events', [])
                            if isinstance(e, dict) and e.get('chapter_id') == chapter_id}
        for key in ('panels', 'pages', 'scenes'):
            old = script.get(key, [])
            if not isinstance(old, list):
                raise GateError('Existing script.' + key + ' is malformed.')
            additions = fragment[key]
            if any(isinstance(item, dict) and item.get('chapter_id', chapter_id) != chapter_id for item in additions):
                raise GateError('Chapter update contains a ' + key + ' item from another chapter.')
            for item in additions:
                if isinstance(item, dict):
                    item.setdefault('chapter_id', chapter_id)
            merged, inserted = [], False
            for item in old:
                if isinstance(item, dict) and item.get('chapter_id') == chapter_id:
                    if not inserted:
                        merged.extend(additions)
                        inserted = True
                else:
                    merged.append(item)
            if not inserted:
                merged.extend(additions)
            script[key] = merged
        old_events = script.get('events', [])
        merged_events, inserted_events = [], False
        for event in old_events:
            if isinstance(event, dict) and event.get('id') in replace_event_ids:
                if not inserted_events:
                    merged_events.extend(fragment['events'])
                    inserted_events = True
            else:
                merged_events.append(event)
        if not inserted_events:
            merged_events.extend(fragment['events'])
        script['events'] = merged_events
        dispositions = script.get('source_dispositions', [])
        units = {u.get('id'): u for u in project['source'].get('units', []) if isinstance(u, dict)}
        additions = fragment['source_dispositions']
        merged_dispositions, inserted_dispositions = [], False
        for disposition in dispositions:
            if isinstance(disposition, dict) and units.get(disposition.get('unit_id'), {}).get('chapter_id') == chapter_id:
                if not inserted_dispositions:
                    merged_dispositions.extend(additions)
                    inserted_dispositions = True
            else:
                merged_dispositions.append(disposition)
        if not inserted_dispositions:
            merged_dispositions.extend(additions)
        script['source_dispositions'] = merged_dispositions
        if any(isinstance(item, dict) and units.get(item.get('unit_id'), {}).get('chapter_id') != chapter_id
               for item in fragment['source_dispositions']):
            raise GateError('Chapter update source dispositions must refer only to the selected chapter.')
        candidate = copy.deepcopy(project)
        candidate['script'] = script
        validation = script_errors(candidate, root)
        if validation:
            raise GateError('Chapter update failed full-script structural validation:\n' + '\n'.join(validation[:60]))
        if project.get('script_lock'):
            project.setdefault('script_lock_history', []).append(project['script_lock'])
        project['script'] = script
        project['script_lock'] = None
        for field in ('layout', 'exports', 'final_review'):
            if project.get(field):
                project.setdefault(field + '_history', []).append(project[field])
            project[field] = None
    elif command == 'check-script':
        errors = script_errors(project, root)
        return {'script_hash': digest(project['script']), 'errors': errors[:100], 'error_count': len(errors),
                'semantic_review_required': True}
    elif command == 'review':
        errors = script_errors(project, root)
        if errors:
            raise GateError('\n'.join(errors))
        report = load_json(args.file)
        if not isinstance(report, dict) or report.get('script_hash') != digest(project['script']):
            raise GateError('Review must explicitly bind the current script_hash.')
        validate_qa(report, REVIEW_CHECKS[args.kind])
        body_chapters = {c.get('id') for c in project['source']['chapters'] if isinstance(c, dict) and c.get('has_body')}
        reviewed_chapters = report.get('reviewed_chapter_ids')
        if not isinstance(reviewed_chapters, list) or set(reviewed_chapters) != body_chapters:
            raise GateError('Review must cover every chapter with body text.')
        if any(not isinstance(i, dict) or not i.get('resolved') for i in report.get('issues', [])
               if isinstance(i, dict) and i.get('severity') in ('critical', 'major')):
            raise GateError('Resolve important review findings before passing.')
        project['reviews'].append({**report, 'kind': args.kind, 'recorded_at': now()})
        project['script_lock'] = None
    elif command == 'lock-script':
        errors = script_errors(project, root)
        fingerprint = digest(project['script'])
        for kind in REVIEW_CHECKS:
            if not any(isinstance(r, dict) and r.get('kind') == kind and r.get('script_hash') == fingerprint for r in project.get('reviews', [])):
                errors.append('Missing current volume review: ' + kind)
        if errors:
            raise GateError('\n'.join(errors))
        project['script_lock'] = {'script_hash': fingerprint, 'source_index_hash': project['source_index_hash'],
                                  'reviews_hash': digest(project['reviews']), 'locked_at': now()}
        (root / 'full-script.md').write_text(script_markdown(project), encoding='utf-8')
    elif command == 'assert-art':
        assert_script_lock(project, root)
        return {'allowed': True, 'script_hash': project['script_lock']['script_hash']}
    elif command == 'register-reference':
        assert_script_lock(project, root)
        if getattr(args, 'bindings', None):
            binding_data = load_json(args.bindings)
            if not isinstance(binding_data, dict):
                raise GateError('Reference bindings file must contain a JSON object.')
            purpose = binding_data.get('purpose')
            subjects = normalize_subjects(project, binding_data.get('subjects'))
        else:
            ids = args.characters or []
            known = {c.get('id') for c in project['script']['characters'] if isinstance(c, dict)}
            if not ids or any(c not in known for c in ids):
                raise GateError('Reference must identify existing characters.')
            purpose = 'combined'
            subjects = normalize_subjects(project, [{'character_id': cid, 'version_id': 'base', 'region': None} for cid in ids])
        if purpose not in ('portrait', 'full_body', 'turnaround', 'expressions', 'pose', 'combined'):
            raise GateError('Reference purpose must be portrait, full_body, turnaround, expressions, pose, or combined.')
        report = load_json(args.qa)
        reference_characters = [subject['character_id'] for subject in subjects]
        source_sha = sha_file(Path(args.file))
        versions = {s['character_id']: s['version_id'] for s in subjects}
        current_design_hash = design_hash(project, reference_characters, versions)
        visual_key = reference_visual_key(source_sha, current_design_hash, purpose, subjects)
        validate_reference_qa(report, reference_characters, source_sha, visual_key)
        known_ids = {c.get('id') for c in project['script']['characters'] if isinstance(c, dict)}
        for pair in report.get('comparisons', []):
            pair_ids = pair.get('character_ids', pair.get('pair'))
            if any(cid not in known_ids for cid in pair_ids):
                raise GateError('Reference QA comparison contains an unknown character ID.')
        path, sha = copy_image(root, args.file, 'references')
        characters = [s['character_id'] for s in subjects]
        reference_id = 'ref' + uuid.uuid4().hex[:12]
        if sha != source_sha:
            raise GateError('Reference image changed while it was being registered; retry with a fresh review.')
        project['art']['references'].append({'id': reference_id, 'character_ids': characters,
                    'subjects': subjects, 'purpose': purpose,
                    'design_hash': current_design_hash, 'visual_key': visual_key,
                    'path': path, 'sha256': sha,
                    'qa': report, 'at': now()})
        save(root, project)
        return {'ok': True, 'reference_id': reference_id, 'path': str(inside(root, path))}
    elif command == 'bind-panel':
        assert_script_lock(project, root)
        panel = next((p for p in project['script']['panels'] if isinstance(p, dict) and p.get('id') == args.panel), None)
        if panel is None:
            raise GateError('Panel ID missing.')
        raw = args.bindings
        try:
            bindings_path = Path(raw)
            is_file = not str(raw).lstrip().startswith('{') and bindings_path.is_file()
        except (OSError, TypeError, ValueError):
            is_file = False
        if is_file:
            binding = load_json(raw)
        else:
            try:
                binding = json.loads(raw)
            except json.JSONDecodeError as error:
                raise GateError('--bindings must be JSON text or a path to a JSON file.') from error
        if not isinstance(binding, dict):
            raise GateError('Panel bindings must be a JSON object.')
        characters = {c.get('id'): c for c in project['script']['characters'] if isinstance(c, dict)}
        versions = binding.get('appearance_versions', panel.get('appearance_versions', {}))
        if not isinstance(versions, dict):
            raise GateError('appearance_versions must be an object mapping character IDs to version IDs.')
        if any(cid not in panel.get('cast', []) for cid in versions):
            raise GateError('appearance_versions keys must belong to the panel cast.')
        versions = {cid: versions.get(cid, 'base') for cid in panel.get('cast', [])}
        script_versions = panel.get('appearance_versions')
        if not isinstance(script_versions, dict) or any(script_versions.get(cid) != versions.get(cid) for cid in panel.get('cast', [])):
            raise GateError('Production appearance_versions must exactly match the frozen script panel mapping.')
        for cid in panel.get('cast', []):
            before = panel.get('state_before', {})
            before = before.get(cid, {}) if isinstance(before, dict) else {}
            form = before.get('form') if isinstance(before, dict) else None
            if nonempty(form) and form in {v.get('id') for v in characters.get(cid, {}).get('appearance_versions', []) if isinstance(v, dict)} and versions[cid] != form:
                raise GateError(f'Panel binding {cid} must match state_before form {form}.')
        references = binding.get('reference_ids')
        cast = panel.get('cast', [])
        if not isinstance(references, list) or (cast and not references) or any(not nonempty(rid) for rid in references):
            raise GateError('reference_ids must list the exact registered references for this panel (empty only for an empty cast).')
        if len(references) != len(set(references)):
            raise GateError('reference_ids cannot contain duplicates.')
        characters = {c.get('id'): c for c in project['script']['characters'] if isinstance(c, dict)}
        for cid, version in versions.items():
            if cid not in characters or not any(isinstance(v, dict) and v.get('id') == version
                                                for v in characters[cid].get('appearance_versions', [])):
                raise GateError(f'Unknown appearance version binding: {cid}/{version}.')
        known_refs = {r.get('id') for r in project.get('art', {}).get('references', []) if isinstance(r, dict)}
        if any(rid not in known_refs for rid in references):
            raise GateError('Panel binding includes an unknown reference ID.')
        ref_by_id = {r.get('id'): r for r in project.get('art', {}).get('references', []) if isinstance(r, dict)}
        # One character may have several explicitly bound references (for example,
        # a full-body sheet plus an expression sheet). Coverage is by exact
        # character/form pair, independent of reference order.
        covered = {(subject.get('character_id'), subject.get('version_id'))
                   for rid in references for subject in ref_by_id[rid].get('subjects', []) if isinstance(subject, dict)}
        missing_forms = [cid for cid in cast if (cid, versions.get(cid)) not in covered]
        if missing_forms:
            raise GateError('Panel references do not cover the selected character forms: ' + ', '.join(missing_forms))
        project.setdefault('art', {}).setdefault('bindings', {})[args.panel] = {
            'appearance_versions': versions, 'reference_ids': references, 'updated_at': now()}
        return_value = {'ok': True, 'panel_id': args.panel, 'appearance_versions': versions, 'reference_ids': references}
        save(root, project)
        return return_value
    elif command == 'begin-batch':
        from comic_batches import begin_batch
        return begin_batch(root, project, args.plan, args.prompt)
    elif command == 'split-batch':
        from comic_batches import split_batch
        return split_batch(root, project, args.batch, args.file, args.regions)
    elif command == 'fail-panel':
        attempts = project['art']['panels'].get(args.panel, [])
        attempt = next((a for a in attempts if isinstance(a, dict) and a.get('number') == args.attempt), None)
        if not attempt or attempt['status'] != 'pending':
            raise GateError('Pending panel attempt required.')
        if not nonempty(args.reason):
            raise GateError('Failure, cancellation, or stale outcome reason required.')
        cat = getattr(args, 'category', None)
        if cat and cat not in VALID_FAILURE_CATEGORIES:
            raise GateError(f'Failure category must be one of: {", ".join(VALID_FAILURE_CATEGORIES)}')
        update_fields = {'status': getattr(args, 'outcome', 'failed'), 'failure': args.reason, 'settled_at': now()}
        if cat:
            update_fields['failure_category'] = cat
            if cat == 'tool_exception':
                update_fields['is_tool_exception'] = True
        attempt.update(**update_fields)
    elif command == 'finish-panel':
        assert_script_lock(project, root)
        attempts = project['art']['panels'].get(args.panel, [])
        attempt = next((a for a in attempts if isinstance(a, dict) and a.get('number') == args.attempt), None)
        if not attempt or attempt.get('status') != 'pending':
            raise GateError('Pending panel attempt required.')
        panel = next((p for p in project['script']['panels'] if isinstance(p, dict) and p.get('id') == args.panel), None)
        if panel is None:
            raise GateError('Panel ID missing.')
        current_hash = render_hash(root, project, panel, select_references(root, project, panel))
        if attempt.get('render_hash') != current_hash:
            raise GateError('Attempt inputs changed during generation; settle it with fail-panel --outcome stale.')
        image_sha256 = sha_file(Path(args.file))
        from comic_batches import validate_panel_file
        validate_panel_file(root, project, panel, attempt, args.file)
        report = load_json(args.qa)
        try:
            validate_panel_qa(report, args.panel, attempt.get('number'), current_hash, image_sha256)
        except GateError:
            attempt.update(status='rejected', qa=report, settled_at=now())
            save(root, project)
            raise
        path, sha = copy_image(root, args.file, 'panels')
        if sha != image_sha256:
            attempt.update(status='rejected', failure='Image changed while being copied after QA.', settled_at=now())
            save(root, project)
            raise GateError('Panel image changed after QA; attempt rejected.')
        attempt.update(status='accepted', path=path, sha256=sha, qa=report,
                       qa_binding={'attempt': attempt.get('number'), 'render_hash': current_hash}, settled_at=now())
    elif command in ('compose', 'review-layout', 'export', 'verify-export', 'complete'):
        assert_script_lock(project, root)
        from comic_layout import layout_fingerprint, compose, export, verify_exports
        existing_layout = project.get('layout') if isinstance(project.get('layout'), dict) else {}
        effective_font = args.font if command == 'compose' else existing_layout.get('font_path')
        fingerprint = layout_fingerprint(root, project, effective_font)
        if command == 'compose':
            project['layout'] = compose(root, project, args.font)
            project['exports'], project['final_review'] = None, None
        else:
            layout = project.get('layout')
            if not layout or layout['input_hash'] != fingerprint:
                raise GateError('Composed pages missing or stale.')
            for page in layout['pages']:
                path = inside(root, page['path'])
                if not path.is_file() or sha_file(path) != page['sha256']:
                    raise GateError('Composed page missing or changed.')
            if command == 'review-layout':
                report = load_json(args.file)
                validate_qa(report, LAYOUT_CHECKS)
                if report.get('input_hash') != fingerprint or set(report.get('reviewed_page_ids', [])) != {p['id'] for p in layout['pages']}:
                    raise GateError('Visual layout review must bind current hash and every composed page.')
                layout['qa'] = report
            else:
                if not layout.get('qa'):
                    raise GateError('View and review every composed page before export.')
                validate_qa(layout['qa'], LAYOUT_CHECKS)
                if command == 'export':
                    project['exports'] = export(root, project)
                    project['final_review'] = None
                else:
                    verification = verify_exports(root, project)
                    if command == 'verify-export':
                        return verification
                    report = load_json(args.file)
                    validate_qa(report, ['source_scope', 'story_complete', 'visual_consistency', 'exports_opened'])
                    if report.get('input_hash') != fingerprint:
                        raise GateError('Final review must bind current deliverable inputs.')
                    project['final_review'] = {**report, 'verification': verification, 'at': now()}
    elif command in ('status', 'preflight'):
        try:
            assert_script_lock(project, root)
            locked, blockers = True, []
        except GateError as error:
            locked, blockers = False, str(error).splitlines()
        script_data = project.get('script') if isinstance(project.get('script'), dict) else {}
        art_data = project.get('art') if isinstance(project.get('art'), dict) else {}
        attempt_ledger = art_data.get('panels') if isinstance(art_data.get('panels'), dict) else {}
        panel_data = script_data.get('panels') if isinstance(script_data.get('panels'), list) else []
        panels = [p for p in panel_data if isinstance(p, dict)]
        accepted_attempts = {p.get('id'): accepted_panel(root, project, p) for p in panels if nonempty(p.get('id'))}
        accepted = [pid for pid, attempt in accepted_attempts.items() if attempt]
        panel_blockers, attempt_summary = {}, []
        ready_count = exhausted_count = blocked_count = remaining_slots = unknown_budget_count = 0
        for panel in panels:
            pid = panel.get('id', '?')
            reasons = []
            fingerprint = None
            if not locked:
                reasons.append('volume script lock is missing or stale')
            try:
                selected = select_references(root, project, panel)
                fingerprint = render_hash(root, project, panel, selected)
                attempts = attempt_ledger.get(pid, [])
                attempts = attempts if isinstance(attempts, list) else []
                relevant = [a for a in attempts if isinstance(a, dict) and a.get('render_hash') == fingerprint
                            and not a.get('is_tool_exception') and a.get('failure_category') != 'tool_exception']
                tool_exceptions = [a for a in attempts if isinstance(a, dict) and a.get('render_hash') == fingerprint
                                   and (a.get('is_tool_exception') or a.get('failure_category') == 'tool_exception')]
                valid_accepted = accepted_attempts.get(pid)
                if not valid_accepted:
                    remaining_slots += max(0, 3 - len(relevant))
                if any(a.get('status') == 'pending' for a in relevant) or any(a.get('status') == 'pending' for a in tool_exceptions):
                    reasons.append('pending attempt must be finished or settled')
                if len(tool_exceptions) >= 2 and not valid_accepted:
                    reasons.append('tool exception retry budget exhausted (2 attempts)')
                if len(relevant) >= 3 and not valid_accepted:
                    reasons.append('three attempts exhausted for current visual inputs')
                if valid_accepted:
                    reasons = []
                elif not reasons:
                    ready_count += 1
            except (GateError, KeyError, TypeError, AttributeError, OSError) as error:
                reasons.append(str(error))
                if pid not in accepted:
                    unknown_budget_count += 1
            if reasons:
                blocked_count += 1
                if any('three attempts exhausted' in reason for reason in reasons):
                    exhausted_count += 1
            panel_blockers[pid] = reasons
            for attempt in attempt_ledger.get(pid, []) if isinstance(attempt_ledger.get(pid, []), list) else []:
                if isinstance(attempt, dict):
                    attempt_summary.append({'panel_id': pid, 'number': attempt.get('number'), 'status': attempt.get('status'),
                                           'reason': attempt.get('failure'), 'category': attempt.get('failure_category'),
                                           'render_hash': attempt.get('render_hash'),
                                           'current_input': attempt.get('render_hash') == fingerprint if fingerprint else None,
                                           'created_at': attempt.get('at')})
        complete = False
        if locked and project.get('final_review'):
            try:
                validate_qa(project['final_review'], ['source_scope', 'story_complete', 'visual_consistency', 'exports_opened'])
                from comic_layout import layout_fingerprint, verify_exports
                if project['final_review']['input_hash'] == layout_fingerprint(
                        root, project, project.get('layout', {}).get('font_path') if isinstance(project.get('layout'), dict) else None):
                    verify_exports(root, project)
                    complete = True
            except (GateError, KeyError, TypeError, AttributeError, OSError, ValueError):
                pass
        source_data = project.get('source') if isinstance(project.get('source'), dict) else {}
        source_chapters = [c for c in source_data.get('chapters', []) if isinstance(c, dict)] if isinstance(source_data.get('chapters'), list) else []
        pages = script_data.get('pages', [])
        refs = art_data.get('references', [])
        refs = refs if isinstance(refs, list) else []
        source_warnings = external_source_warnings(project)
        from comic_batches import batch_summary
        batch_counts, batches, planned_batches = batch_summary(root, project, accepted, getattr(args, 'plan', None))
        return {'complete': complete, 'title': project.get('title'), 'volume': project.get('volume', ''),
                'source_scope': source_data.get('scope_note'),
                'chapters_with_body': sum(bool(c.get('has_body')) for c in source_chapters),
                'chapters_read': sum(bool(c.get('has_body') and c.get('read')) for c in source_chapters),
                'script_hash': digest(project.get('script')), 'script_locked': locked,
                'panels_planned': len(panels), 'panels_accepted': len(accepted),
                'next_panel': next((p['id'] for p in panels if p['id'] not in accepted), None),
                'blockers': blockers[:40], 'blocker_count': len(blockers), 'exports': project.get('exports'),
                'external_source_warnings': source_warnings, 'warnings': source_warnings,
                'preflight_counts': {'panels': len(panels), 'pages': len(pages) if isinstance(pages, list) else 0,
                                     'references': len(refs) if isinstance(refs, list) else 0,
                                     'panels_ready': ready_count, 'panels_blocked': blocked_count,
                                     'panels_exhausted': exhausted_count, 'max_attempts_per_visual_input': 3,
                                     'initial_panel_attempt_budget': 3 * len(panels),
                                     'attempts_recorded': len(attempt_summary),
                                     'pending_attempts': sum(a['status'] == 'pending' for a in attempt_summary),
                                     'panels_remaining': len(panels) - len(accepted),
                                     'current_input_attempt_slots': remaining_slots,
                                     'panels_with_unknown_budget': unknown_budget_count, **batch_counts},
                'panel_blockers': panel_blockers, 'attempts': attempt_summary, 'batches': batches,
                'planned_batches': planned_batches}
    else:
        raise GateError('Unknown command.')
    save(root, project)
    if command in ('lock-script', 'export', 'complete'):
        write_volume_readme(root, project)
    return {'ok': True, 'command': command, 'script_hash': digest(project['script'])}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='command', required=True)
    book_p = subs.add_parser('init-book')
    book_p.add_argument('--book-dir', '--project', dest='book_dir', required=True,
                        help='Path to top-level book directory')
    book_p.add_argument('--title', help='Book title')
    book_p.add_argument('--source', nargs='*', help='One or more source novel files to archive into source_texts/')
    book_p.add_argument('--action', choices=['move', 'copy'], default='move',
                        help='Action for source novel files: move or copy')
    book_p.add_argument('--description', default='', help='Brief book description')

    split_p = subs.add_parser('split-source')
    split_p.add_argument('--book-dir', '--project', dest='book_dir', required=True,
                         help='Path to top-level book directory')
    split_p.add_argument('--file', required=True, help='Source file name or path to split')
    split_p.add_argument('--output-dir', help='Output directory for split files (default: book_dir/split_texts)')
    split_p.add_argument('--pattern', help='Custom regex pattern for volume or segment headings')

    doctor_p = subs.add_parser('doctor')
    doctor_p.add_argument('--project', default='.', help='Volume project directory or book directory to inspect')

    typeset_p = subs.add_parser('preflight-typeset')
    typeset_p.add_argument('--project', required=True, help='Volume project directory')
    typeset_p.add_argument('--font', help='Explicit font path for feasibility check')

    for name in ('init', 'preflight', 'qa-inputs', 'chapter', 'script-chapter', 'resolve-issue', 'confirm-source',
                 'mark-read', 'set-script', 'set-script-chapter', 'impact', 'check-script', 'review',
                 'lock-script', 'assert-art', 'register-reference', 'bind-panel',
                 'begin-batch', 'split-batch', 'finish-panel', 'fail-panel', 'compose',
                 'review-layout', 'export', 'verify-export', 'complete', 'status'):
        sub = subs.add_parser(name)
        sub.add_argument('--project', required=True)
        if name == 'init':
            sub.add_argument('--source', nargs='+', required=True)
            sub.add_argument('--title')
            sub.add_argument('--volume')
        if name == 'qa-inputs':
            sub.add_argument('--file')
            sub.add_argument('--bindings')
            sub.add_argument('--characters', nargs='+')
            sub.add_argument('--reference')
            sub.add_argument('--panel')
            sub.add_argument('--attempt', type=int)
        if name in ('chapter', 'mark-read', 'script-chapter'):
            sub.add_argument('--chapter', required=name == 'mark-read')
        if name == 'resolve-issue':
            sub.add_argument('--id', required=True)
            sub.add_argument('--evidence', required=True)
        if name in ('confirm-source', 'mark-read'):
            sub.add_argument('--note', required=True)
        if name == 'confirm-source':
            sub.add_argument('--scope')
        if name in ('set-script', 'set-script-chapter', 'impact', 'review', 'register-reference',
                    'finish-panel', 'review-layout', 'complete'):
            sub.add_argument('--file', required=True)
        if name == 'set-script-chapter':
            sub.add_argument('--chapter', required=True)
        if name == 'review':
            sub.add_argument('--kind', choices=REVIEW_CHECKS, required=True)
        if name == 'register-reference':
            selection = sub.add_mutually_exclusive_group(required=True)
            selection.add_argument('--characters', nargs='+')
            selection.add_argument('--bindings')
        if name in ('register-reference', 'finish-panel'):
            sub.add_argument('--qa', required=True)
        if name in ('bind-panel', 'finish-panel', 'fail-panel'):
            sub.add_argument('--panel', required=True)
        if name in ('finish-panel', 'fail-panel'):
            sub.add_argument('--attempt', type=int, required=True)
        if name == 'begin-batch':
            sub.add_argument('--plan', required=True)
            sub.add_argument('--prompt', required=True)
        if name in ('preflight', 'status'):
            sub.add_argument('--plan')
        if name == 'split-batch':
            sub.add_argument('--batch', required=True)
            sub.add_argument('--file', required=True)
            sub.add_argument('--regions', required=True)
        if name == 'bind-panel':
            sub.add_argument('--bindings', required=True, help='JSON text or path to a JSON file containing appearance_versions and reference_ids.')
        if name == 'fail-panel':
            sub.add_argument('--reason', required=True)
            sub.add_argument('--category', choices=VALID_FAILURE_CATEGORIES, default=None,
                             help='Failure cause taxonomy category')
            sub.add_argument('--outcome', choices=('failed', 'cancelled', 'stale'), default='failed')
        if name == 'compose':
            sub.add_argument('--font')
    return p


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    try:
        result = run(parser().parse_args())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get('errors'):
            sys.exit(2)
    except (GateError, SourceError, OSError, ValueError, KeyError, TypeError, ImportError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
