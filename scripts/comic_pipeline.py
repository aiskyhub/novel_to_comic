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
from comic_adaptation import adaptation_errors

SCHEMA_VERSION = 6

def find_template_file(category: str, name: str) -> Path:
    skill_root = Path(__file__).resolve().parent.parent
    canonical = skill_root / 'assets' / 'templates' / category / name
    if canonical.is_file():
        return canonical
    raise GateError(f'Template file not found at canonical path: {canonical}')


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
PAGE_DRAWING_CHECKS = ['identity', 'continuity', 'composition', 'drawing_quality', 'no_unwanted_text',
                'gender_readability', 'distinctiveness', 'body_design', 'design_tier_fit', 'visual_elegance', 'native_detail']
REFERENCE_CHECKS = ['identity', 'distinctiveness', 'angles_and_expressions', 'source_faithfulness',
                    'gender_readability', 'body_design', 'design_tier_fit', 'visual_elegance']
LAYOUT_CHECKS = ['text_accuracy', 'reading_order', 'speaker_assignment', 'face_visibility', 'visual_elegance', 'phone_readability']
PAGE_ART_CHECKS = PAGE_DRAWING_CHECKS + LAYOUT_CHECKS
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
    if type(columns) is not int or columns != 1:
        raise GateError('Page columns must be 1.')
    pids = page.get('panel_ids')
    if not isinstance(pids, list) or not pids or any(not nonempty(pid) for pid in pids):
        raise GateError('Page needs a nonempty panel_ids array.')
    if 'rows' not in page:
        return [pids[start:start + columns] for start in range(0, len(pids), columns)]
    rows = page['rows']
    if (not isinstance(rows, list) or not rows or
            any(not isinstance(row, list) or len(row) != 1 or
                any(not nonempty(pid) for pid in row) for row in rows)):
        raise GateError('Page rows must contain one narrative panel ID per row.')
    if [pid for row in rows for pid in row] != pids:
        raise GateError('Page rows must match panel_ids exactly in reading order.')
    return rows




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
    if style.get('format') != 'pages' or style.get('reading_direction') not in ('ltr', 'rtl'):
        errors.append('script.style.format/reading_direction: invalid values.')
    for key in ('width', 'height', 'font_size'):
        if key in style and (type(style[key]) is not int or style[key] <= 0):
            errors.append(f'script.style.{key}: expected a positive integer.')
    for removed_key in ('font_path','max_segment_height'):
        if removed_key in style:
            errors.append('script.style.'+removed_key+': not part of whole-page production.')
    from comic_layout import phone_style_errors, check_phone_rows
    errors.extend(phone_style_errors(style))

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
            if style.get('format') == 'pages':
                check_phone_rows(page, rows)
            if 'row_weights' in page:
                raise GateError('page.row_weights is not part of whole-page production.')
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
    errors.extend(adaptation_errors(source, script, root, source_index_hash=project.get('source_index_hash'),
                                    current_names=(project.get('volume'), project.get('title'))))
    return errors


def validate_qa(report, required, scope=None):
    if not isinstance(report, dict) or not nonempty(report.get('evidence')):
        raise GateError('QA requires actual visual/read evidence, not an unexplained pass.')
    checks = report.get('checks', {})
    if not isinstance(checks, dict):
        raise GateError('QA checks must be an object.')
    has_defect_explanation = nonempty(report.get('defect_explanation')) or nonempty(report.get('flaw_explanation'))
    if any(checks.get(key) is not True for key in required):
        if not has_defect_explanation:
            raise GateError('QA not passed: ' + ', '.join(k for k in required if checks.get(k) is not True))
    if 'phone_readability' in required:
        notes = report.get('phone_reading_notes')
        reviewed = report.get('reviewed_page_ids')
        if (not isinstance(notes, list) or not isinstance(reviewed, list) or not reviewed or
                len(notes) != len(reviewed) or
                any(not isinstance(item, dict) for item in notes) or
                [item.get('page_id') for item in notes] != reviewed):
            raise GateError('QA phone_reading_notes: require one actual phone reading record per reviewed page in order.')
        for item in notes:
            if (item.get('preview_widths') != [360, 390, 430] or not nonempty(item.get('evidence')) or
                    'min_body_css_px' not in item):
                raise GateError('QA phone_reading_notes: inspect 360/390/430 CSS px previews and record actual observations.')
            minimum = item.get('min_body_css_px')
            if minimum is not None and (type(minimum) not in (int, float) or not math.isfinite(minimum) or minimum <= 0):
                raise GateError('QA phone_reading_notes.min_body_css_px: body text size must be a positive number or null.')
    if 'visual_elegance' in required:
        notes = report.get('elegance_notes')
        if not isinstance(notes, dict):
            raise GateError('QA elegance_notes: expected actual observations of linework, color_and_light, and visual_hierarchy.')
        for key in ('linework', 'color_and_light', 'visual_hierarchy'):
            if not nonempty(notes.get(key)):
                raise GateError('QA elegance_notes.' + key + ': actual visual evidence is required.')
    if scope in ('reference', 'page'):
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
                if not has_defect_explanation:
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


def stage_batches(project):
    """Return ordered list of required review stage specs covering all body chapters in batches of up to 5."""
    chapters = project.get('source', {}).get('chapters', [])
    body_cids = [c['id'] for c in chapters if isinstance(c, dict) and c.get('has_body')]
    stages = []
    for i in range(0, len(body_cids), 5):
        batch = body_cids[i:i+5]
        stages.append({
            'stage_index': (i // 5) + 1,
            'start_chapter_id': batch[0],
            'end_chapter_id': batch[-1],
            'chapter_count': len(batch),
            'reviewed_chapters': batch,
        })
    return stages


def stage_script_fingerprint(project, chapter_ids):
    """Compute deterministic fingerprint of the script content for specified chapters, covering panels, scenes, pages, adaptations, characters, events, and settings."""
    cids = set(chapter_ids)
    script = project.get('script', {})
    panels = [p for p in script.get('panels', [])
              if isinstance(p, dict) and p.get('chapter_id') in cids]
    scenes = [s for s in script.get('scenes', [])
              if isinstance(s, dict) and s.get('chapter_id') in cids]
    pages = [pg for pg in script.get('pages', [])
             if isinstance(pg, dict) and pg.get('chapter_id') in cids]
    adaptations = [a for a in script.get('chapter_adaptations', [])
                   if isinstance(a, dict) and a.get('chapter_id') in cids]
    cast_ids = {cid for p in panels for cid in (p.get('cast', []) if isinstance(p.get('cast'), list) else [])
                if nonempty(cid)}
    characters = [c for c in script.get('characters', [])
                  if isinstance(c, dict) and c.get('id') in cast_ids]
    event_ids = {eid for p in panels for eid in (p.get('event_ids', []) if isinstance(p.get('event_ids'), list) else [])
                 if nonempty(eid)}
    events = [e for e in script.get('events', [])
              if isinstance(e, dict) and (e.get('chapter_id') in cids or e.get('id') in event_ids)]
    setting_ids = {s.get('setting_id') for s in scenes if isinstance(s, dict) and nonempty(s.get('setting_id'))}
    settings = [st for st in script.get('settings', [])
                if isinstance(st, dict) and st.get('id') in setting_ids]
    return digest({
        'chapters': sorted(cids),
        'panels': panels,
        'scenes': scenes,
        'pages': pages,
        'adaptations': adaptations,
        'characters': characters,
        'events': events,
        'settings': settings,
    })


def validate_stage_review(report, root=None, project=None):
    """Validate 5-chapter stage review report, strictly enforcing evidence grounding, diff verification, and audit trail."""
    if not isinstance(report, dict):
        raise GateError('Stage review must be a JSON object.')

    stage_range = report.get('stage_range')
    if not isinstance(stage_range, dict) or not nonempty(stage_range.get('start_chapter_id')) or not nonempty(stage_range.get('end_chapter_id')):
        raise GateError('Stage review missing valid stage_range (start_chapter_id, end_chapter_id).')

    reviewed_chapters = report.get('reviewed_chapters')
    if not isinstance(reviewed_chapters, list) or not reviewed_chapters:
        raise GateError('Stage review must include nonempty reviewed_chapters list.')

    if stage_range.get('start_chapter_id') != reviewed_chapters[0]:
        raise GateError('stage_range start_chapter_id must match the first chapter in reviewed_chapters.')
    if stage_range.get('end_chapter_id') != reviewed_chapters[-1]:
        raise GateError('stage_range end_chapter_id must match the last chapter in reviewed_chapters.')
    if stage_range.get('chapter_count') is not None and stage_range.get('chapter_count') != len(reviewed_chapters):
        raise GateError('stage_range chapter_count must match length of reviewed_chapters.')

    rep_stage_hash = report.get('stage_script_hash') or stage_range.get('stage_script_hash')
    if not nonempty(rep_stage_hash):
        raise GateError("Stage review requires non-empty 'stage_script_hash' binding reviewed chapters to current script state.")

    if project is None and root and isinstance(root, (str, Path)):
        rpath = Path(root)
        if rpath.is_dir() and (rpath / 'project.json').is_file():
            try:
                project = project_load(rpath)
            except Exception:
                pass

    if project and isinstance(project, dict):
        known_chaps = {c.get('id') for c in project.get('source', {}).get('chapters', []) if isinstance(c, dict)}
        if known_chaps:
            for cid in reviewed_chapters:
                if cid not in known_chaps:
                    raise GateError(f"Stage review reviewed_chapters contains '{cid}' which does not exist in project chapters.")
        exp_hash = stage_script_fingerprint(project, reviewed_chapters)
        if rep_stage_hash != exp_hash:
            raise GateError(f"Stage review stage_script_hash mismatch: report={rep_stage_hash}, current={exp_hash}. Script content for these chapters has changed since review was conducted.")

    # 0. Subagent audit provenance: check if runtime session transcript corroborates conversation IDs.
    # When runtime calling credentials cannot be fully verified, record 'unverified' rather than falsely claiming 100% fraud blocking.
    provenance = report.get('audit_provenance', 'unverified')
    app_data = os.environ.get('ANTIGRAVITY_APP_DATA_DIR')
    conv_id = os.environ.get('ANTIGRAVITY_CONVERSATION_ID')
    if app_data and conv_id and not os.environ.get('PYTEST_CURRENT_TEST') and 'unittest' not in sys.modules:
        transcript_path = Path(app_data) / 'brain' / conv_id / '.system_generated' / 'logs' / 'transcript.jsonl'
        if transcript_path.is_file():
            try:
                transcript_content = transcript_path.read_text(encoding='utf-8', errors='ignore')
                auditor_cids = [a.get('conversation_id') for a in report.get('round_1_initial_audit', {}).get('auditors', []) if a.get('conversation_id')]
                if auditor_cids and all(cid in transcript_content for cid in auditor_cids):
                    report['audit_provenance'] = 'corroborated'
                else:
                    report['audit_provenance'] = 'unverified'
            except Exception:
                report['audit_provenance'] = 'unverified'

    # 1. Round 1 initial audit checks
    r1 = report.get('round_1_initial_audit')
    if not isinstance(r1, dict):
        raise GateError('Stage review requires round_1_initial_audit object representing the first-round audit.')

    auditors = r1.get('auditors')
    if not isinstance(auditors, list) or len(auditors) < 2:
        raise GateError('round_1_initial_audit requires at least 2 independent auditors (dual subagents).')

    BANNED_QUOTE_PLACEHOLDERS = (
        '原文具体', '短引句', '待填', 'placeholder', 'quote here', '具体短引句', '某某段落'
    )

    project_source_units = project.get('source', {}).get('units', []) if project else None
    project_panels = project.get('script', {}).get('panels', []) if project else None

    auditor_ids = []
    auditor_conv_ids = []
    all_round_1_issues = {}
    reviewed_cids_set = set(reviewed_chapters)

    for idx, auditor in enumerate(auditors):
        aid = auditor.get('auditor_id', f'auditor_{idx}')
        auditor_ids.append(aid)
        cid = auditor.get('conversation_id')
        if cid:
            auditor_conv_ids.append(cid)
        scores = auditor.get('scores', {})
        if not isinstance(scores, dict):
            raise GateError(f'Auditor {aid} scores must be an object.')
        total = scores.get('total_score')
        if not isinstance(total, (int, float)):
            raise GateError(f'Auditor {aid} missing numeric total_score in round 1.')
        if not (0 <= total <= 100):
            raise GateError(f'Auditor {aid} total_score ({total}) out of valid range (0-100).')
        for sk, sv in scores.items():
            if isinstance(sv, (int, float)) and not (0 <= sv <= 100):
                raise GateError(f"Auditor {aid} score '{sk}' ({sv}) out of valid range (0-100).")

        deductions = auditor.get('deductions', [])
        issues = auditor.get('issues', [])
        if not isinstance(deductions, list) or not isinstance(issues, list):
            raise GateError(f'Auditor {aid} deductions and issues must be lists.')

        for issue_idx, issue in enumerate(issues):
            if not isinstance(issue, dict):
                raise GateError(f'Auditor {aid} issue {issue_idx} must be an object.')
            iid = issue.get('id')
            if not nonempty(iid):
                raise GateError(f'Auditor {aid} issue {issue_idx} missing unique id.')
            if iid in all_round_1_issues:
                raise GateError(f'Duplicate issue ID in round 1: {iid}')
            all_round_1_issues[iid] = issue

            ch_id = issue.get('chapter_id')
            if not nonempty(ch_id):
                raise GateError(f'Auditor {aid} issue {iid} missing chapter_id.')
            if ch_id not in reviewed_cids_set:
                raise GateError(f"Auditor {aid} issue {iid} cites chapter '{ch_id}' which is not in reviewed_chapters {reviewed_chapters}.")

            quote = issue.get('source_quote')
            if not nonempty(quote):
                raise GateError(f'Auditor {aid} issue {iid} missing source_quote binding to novel text.')

            for banned in BANNED_QUOTE_PLACEHOLDERS:
                if banned in quote:
                    raise GateError(
                        f"Anti-hallucination gate: Auditor {aid} issue {iid} source_quote contains template placeholder '{banned}': '{quote}'. "
                        "Must extract actual verbatim text from novel."
                    )

            if project_source_units is not None:
                ch_units = [u for u in project_source_units if isinstance(u, dict) and u.get('chapter_id') == ch_id]
                if not ch_units:
                    raise GateError(
                        f"Anti-hallucination gate: Auditor {aid} issue {iid} cites chapter '{ch_id}' which has no text in project units."
                    )
                ch_units_text = "".join(u.get('text', '') for u in ch_units)
                clean_quote = re.sub(r'\s+', '', quote)
                clean_text = re.sub(r'\s+', '', ch_units_text)
                if clean_quote not in clean_text:
                    raise GateError(
                        f"Anti-hallucination gate: Auditor {aid} issue {iid} source_quote '{quote}' not found in actual novel text of chapter {ch_id}."
                    )

            if not nonempty(issue.get('problem_description')):
                raise GateError(f'Auditor {aid} issue {iid} missing problem_description.')
            if not nonempty(issue.get('suggested_fix')):
                raise GateError(f'Auditor {aid} issue {iid} missing suggested_fix.')

    if len(auditor_ids) >= 2 and len(set(auditor_ids)) < len(auditor_ids):
        raise GateError('round_1_initial_audit requires distinct auditor IDs.')
    if len(auditor_conv_ids) >= 2 and len(set(auditor_conv_ids)) < len(auditor_conv_ids):
        raise GateError('round_1_initial_audit requires distinct subagent conversation IDs for each auditor.')

    # Check if round 1 passed cleanly with 0 issues
    initial_verdict = r1.get('initial_verdict')
    round_1_clean_pass = (len(all_round_1_issues) == 0 and initial_verdict == 'PASSED')

    revisions = report.get('screenwriter_revisions') or report.get('lead_agent_revisions')
    r2 = report.get('round_2_verification')

    if round_1_clean_pass:
        if r2 and r2.get('stage_approved') is False:
            raise GateError('round_2_verification stage_approved is explicitly False.')
        return True

    # 2. Revisions checks
    if not isinstance(revisions, dict) or not nonempty(revisions.get('revision_summary')):
        raise GateError('Stage review requires screenwriter_revisions (or lead_agent_revisions) with revision_summary documenting revisions applied.')

    applied_fixes = revisions.get('applied_fixes', [])
    adjudications = revisions.get('adjudications', [])
    if not isinstance(applied_fixes, list):
        raise GateError('revisions applied_fixes must be a list.')
    if not isinstance(adjudications, list):
        raise GateError('revisions adjudications must be a list.')

    if len(applied_fixes) == 0 and len(adjudications) == 0 and len(all_round_1_issues) > 0:
        raise GateError('revisions must document applied_fixes or adjudications addressing identified issues.')

    snap_panels = {}
    if applied_fixes:
        snapshot_raw = r1.get('initial_panels_snapshot') or r1.get('initial_snapshot')
        if not snapshot_raw:
            raise GateError("Stage review claims applied_fixes were made, but round_1_initial_audit missing 'initial_panels_snapshot' recording panel states prior to revisions.")
        if isinstance(snapshot_raw, list):
            snap_panels = {p.get('id'): p for p in snapshot_raw if isinstance(p, dict) and nonempty(p.get('id'))}
        elif isinstance(snapshot_raw, dict):
            snap_panels = {pid: p for pid, p in snapshot_raw.items() if isinstance(p, dict)}
        if not snap_panels:
            raise GateError("Stage review round_1_initial_audit.initial_panels_snapshot must contain valid panel objects.")

        initial_stage_hash = r1.get('initial_stage_script_hash') or r1.get('initial_script_hash')
        if not nonempty(initial_stage_hash):
            raise GateError("Stage review claims applied_fixes were made, but round_1_initial_audit missing non-empty 'initial_stage_script_hash'.")

        if project:
            # Deterministically calculate initial stage fingerprint from initial snapshot
            initial_proj = copy.deepcopy(project)
            curr_panels = list(initial_proj.get('script', {}).get('panels', []))
            for idx, p in enumerate(curr_panels):
                if isinstance(p, dict) and p.get('id') in snap_panels:
                    curr_panels[idx] = copy.deepcopy(snap_panels[p['id']])
            known_curr_ids = {p.get('id') for p in curr_panels if isinstance(p, dict)}
            for pid, spanel in snap_panels.items():
                if pid not in known_curr_ids and spanel.get('chapter_id') in set(reviewed_chapters):
                    curr_panels.append(copy.deepcopy(spanel))
            initial_proj.setdefault('script', {})['panels'] = curr_panels
            calc_initial_hash = stage_script_fingerprint(initial_proj, reviewed_chapters)

            if initial_stage_hash != calc_initial_hash:
                raise GateError(f"round_1_initial_audit initial_stage_script_hash mismatch: expected program-calculated {calc_initial_hash}, got {initial_stage_hash}.")
            cur_hash = stage_script_fingerprint(project, reviewed_chapters)
            if calc_initial_hash == cur_hash:
                raise GateError("Stage review claims applied_fixes were made, but project script content is identical to initial snapshot (no actual modifications were made).")
        else:
            if initial_stage_hash == rep_stage_hash:
                raise GateError("Stage review claims applied_fixes were made, but initial_stage_script_hash matches current stage_script_hash. Script content must reflect real modifications.")

    known_panels = {p.get('id'): p for p in project_panels if isinstance(p, dict)} if project_panels else {}

    def _get_nested_field(obj, fpath):
        if not isinstance(obj, dict) or not fpath:
            return None
        parts = fpath.split('.')
        cur = obj
        for part in parts:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return None
        return cur

    for f_idx, fix in enumerate(applied_fixes):
        if not isinstance(fix, dict):
            raise GateError(f'applied_fixes[{f_idx}] must be an object.')
        ref = fix.get('issue_ref')
        if not nonempty(ref):
            raise GateError(f'applied_fixes[{f_idx}] missing issue_ref.')
        if ref not in all_round_1_issues:
            raise GateError(f"applied_fixes[{f_idx}] references unknown issue_ref '{ref}'. Must reference a valid round 1 issue ID.")
        mod_panels = fix.get('modified_panels')
        if not mod_panels or not isinstance(mod_panels, list):
            raise GateError(f'applied_fixes[{f_idx}] missing modified_panels documenting changed panel IDs.')
        before = str(fix.get('before_revision', '')).strip()
        after = str(fix.get('after_revision', '')).strip()
        if not nonempty(after):
            raise GateError(f'applied_fixes[{f_idx}] missing after_revision details.')
        if before and before == after:
            raise GateError(f"applied_fixes[{f_idx}] before_revision and after_revision are identical. Real modifications must introduce actual differences.")

        field_spec = fix.get('field') or fix.get('field_path')
        candidate_fields = [field_spec] if field_spec else ['action', 'shot', 'space', 'expression']

        for pid in mod_panels:
            if pid not in snap_panels:
                raise GateError(f"applied_fixes[{f_idx}] claims panel '{pid}' was modified, but '{pid}' is not found in round_1_initial_audit.initial_panels_snapshot.")
            snap_panel = snap_panels[pid]

            if known_panels:
                if pid not in known_panels:
                    raise GateError(f"applied_fixes[{f_idx}] modified_panels contains '{pid}' which does not exist in script panels.")
                curr_panel = known_panels[pid]

                found_verified = False
                for f in candidate_fields:
                    s_val = _get_nested_field(snap_panel, f)
                    c_val = _get_nested_field(curr_panel, f)
                    if s_val is None or c_val is None:
                        continue
                    s_str = str(s_val).strip()
                    c_str = str(c_val).strip()
                    if (not before or before == s_str or before in s_str) and (after == c_str or after in c_str) and s_str != c_str:
                        found_verified = True
                        break

                if not found_verified:
                    if field_spec:
                        s_val = _get_nested_field(snap_panel, field_spec)
                        c_val = _get_nested_field(curr_panel, field_spec)
                        raise GateError(
                            f"applied_fixes[{f_idx}] panel '{pid}' field '{field_spec}' modification verification failed: "
                            f"initial snapshot has '{s_val}', current script has '{c_val}'. "
                            f"Must verify before_revision matches snapshot, after_revision matches current script, and field actually changed."
                        )
                    else:
                        raise GateError(
                            f"applied_fixes[{f_idx}] panel '{pid}' modification verification failed: "
                            f"neither action, shot, space, nor expression changed from '{before}' to '{after}' "
                            f"between initial snapshot and current script."
                        )
            else:
                if not any(before in str(_get_nested_field(snap_panel, f) or '') for f in candidate_fields):
                    raise GateError(f"applied_fixes[{f_idx}] panel '{pid}' before_revision '{before}' does not match initial snapshot.")

    for a_idx, adj in enumerate(adjudications):
        if not isinstance(adj, dict):
            raise GateError(f'adjudications[{a_idx}] must be an object.')
        ref = adj.get('issue_ref')
        if not nonempty(ref):
            raise GateError(f'adjudications[{a_idx}] missing issue_ref.')
        if ref not in all_round_1_issues:
            raise GateError(f"adjudications[{a_idx}] references unknown issue_ref '{ref}'.")
        if not nonempty(adj.get('reason')):
            raise GateError(f"adjudications[{a_idx}] missing reason for dismissing issue '{ref}'.")

    # 100% complete coverage: every round 1 issue must have an applied_fix or adjudication
    handled_issue_refs = {f.get('issue_ref') for f in applied_fixes if isinstance(f, dict)} | {a.get('issue_ref') for a in adjudications if isinstance(a, dict)}
    unhandled_issues = set(all_round_1_issues.keys()) - handled_issue_refs
    if unhandled_issues:
        raise GateError(f"Stage review incomplete: Round 1 issues {sorted(unhandled_issues)} have neither an applied_fix nor an adjudication. Every identified issue must be explicitly resolved.")

    # 3. Round 2 verification checks
    if not isinstance(r2, dict):
        raise GateError('Stage review requires round_2_verification object.')

    rechecks = r2.get('auditors_recheck')
    if not isinstance(rechecks, list) or len(rechecks) < len(auditors):
        raise GateError('round_2_verification requires auditors_recheck for each round 1 auditor.')

    recheck_aids = [r.get('auditor_id') for r in rechecks]
    if set(recheck_aids) != set(auditor_ids):
        raise GateError(f'Round 2 auditors_recheck must correspond to round 1 auditors: expected {set(auditor_ids)}, got {set(recheck_aids)}.')

    dismissed_refs = {a.get('issue_ref') for a in adjudications}
    for idx, recheck in enumerate(rechecks):
        aid = recheck.get('auditor_id', f'auditor_{idx}')
        final_scores = recheck.get('final_scores', {})
        if not isinstance(final_scores, dict):
            raise GateError(f'Auditor {aid} round 2 missing final_scores object.')
        final_total = final_scores.get('total_score')
        if not isinstance(final_total, (int, float)):
            raise GateError(f'Auditor {aid} round 2 missing numeric total_score.')
        if not (0 <= final_total <= 100):
            raise GateError(f'Auditor {aid} round 2 total_score ({final_total}) out of valid range (0-100).')
        if final_total < 85:
            raise GateError(f'Auditor {aid} round 2 final_score ({final_total}) must be >= 85 to pass.')
        if recheck.get('verdict') != 'PASSED':
            raise GateError(f'Auditor {aid} round 2 verdict must be PASSED.')
        unresolved = recheck.get('unresolved_issue_refs') or []
        blocking_unresolved = [u for u in unresolved if u not in dismissed_refs]
        if blocking_unresolved:
            raise GateError(f'Auditor {aid} round 2 has unresolved issues: {blocking_unresolved}')

    if r2.get('stage_approved') is not True:
        raise GateError('round_2_verification stage_approved must be True to pass.')
    if not nonempty(r2.get('approved_at')):
        raise GateError('round_2_verification missing approved_at timestamp.')

    return True


def art_structure_errors(project):
    art=project.get('art')
    if not isinstance(art,dict) or set(art)!={'references','pages'}:
        return ['art: whole-page ledger must contain only references and pages.']
    errors=[]
    if not isinstance(art['references'],list) or any(not isinstance(r,dict) for r in art['references']):
        errors.append('art.references: expected reference objects.')
    if not isinstance(art['pages'],dict):
        errors.append('art.pages: expected attempts indexed by page ID.')
    else:
        for pid,attempts in art['pages'].items():
            if not nonempty(pid) or not isinstance(attempts,list):
                errors.append('art.pages: invalid page attempt ledger.')
                continue
            for a in attempts:
                if (not isinstance(a,dict) or type(a.get('number')) is not int or
                    a.get('status') not in ('pending','accepted','failed','cancelled','stale') or not nonempty(a.get('render_hash'))):
                    errors.append('art.pages: malformed whole-page attempt.')
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


LAYOUT_STYLE_KEYS = {'width', 'height', 'font_size', 'format',
                     'reading_direction'}


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
    versions = panel.get('appearance_versions', {})
    characters = {c.get('id'): c for c in project['script'].get('characters', []) if isinstance(c, dict)}
    rendered_characters = [character_visual(characters[cid], versions.get(cid, 'base'))
                           for cid in sorted(cast) if cid in characters]
    visual = {key: panel.get(key) for key in ('id', 'scene_id', 'cast', 'prop_ids', 'action', 'shot', 'space', 'expression',
                                               'state_before', 'state_after', 'aspect_ratio')}
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
            'style': visual_style(project),
            'native_lettering': {
                'dialogue': panel.get('dialogue', []),
                'body_size_fraction': project['script']['style'].get('font_size', 54) / project['script']['style'].get('width', 1080),
                'rules': project['script']['style'].get('art_direction', {}).get('lettering')
            }}






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
            'chapter_adaptations': [r for r in script.get('chapter_adaptations', [])
                                    if isinstance(r, dict) and r.get('chapter_id') == chapter_id],
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
        return {'dialogue': [(p.get('id'), p.get('dialogue'))
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
    has_page = bool(getattr(args, 'page', None))
    has_file = bool(getattr(args, 'file', None))
    bindings_file = getattr(args, 'bindings', None)
    character_ids = getattr(args, 'characters', None)
    attempt_number = getattr(args, 'attempt', None)
    if has_reference:
        if has_page or has_file or bindings_file or character_ids or attempt_number is not None:
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
    if has_page:
        if bindings_file or character_ids or has_reference or not has_file or attempt_number is None:
            raise GateError('Whole-page QA needs --page --attempt --file, without reference selection options.')
        from comic_pages import page_qa_inputs
        return page_qa_inputs(root,project,args.page,attempt_number,args.file)
    if not has_file:
        raise GateError('qa-inputs needs --reference, --page, or --file with reference bindings.')
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


def init_book(book_dir, title=None, sources=None, action='copy', description=''):
    """Initialize top-level book project directory, archive novel text (copy and verify by default), create docs folder, and generate README.md."""
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

    from comic_pages import accepted_page
    accepted_pages = [page for page in pages if accepted_page(root,project,page)]
    accepted_panels = sum(len(page['panel_ids']) for page in accepted_pages)
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
        'accepted_panels': accepted_panels, 'accepted_pages': len(accepted_pages),
        'total_panels': len(panels),
        'layout_status': '✅ 整页已归档' if project.get('layout') else '⏳ 待整理整页 (prepare-pages)',
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
        f'🖌️ 整页绘制验收中 ({accepted_panels}/{len(panels)})' if accepted_panels else
        '🔒 剧本已锁定' if lock_valid else
        '⚠️ 剧本锁失效' if script_lock else
        '📝 剧本编制与三轮校验中'
    )

    auto_block = render_template('volume_docs', 'status_block.md', {
        'title': title,
        'volume': volume or '分卷',
        'status_summary': status_summary,
        'lock_display': lock_display,
        'accepted_panels': accepted_panels, 'accepted_pages': len(accepted_pages),
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
    import importlib.util
    dependencies={name:importlib.util.find_spec(name) is not None for name in ('PIL','reportlab','pypdf')}
    result={'ok':all(dependencies.values()),'environment':{'python_version':sys.version.split()[0],'platform':sys.platform},'dependencies':dependencies}
    if root_dir:
        from comic_pages import status
        result['project']=status(Path(root_dir).resolve(),project_load(root_dir))
    return result

def estimate_production_cost(project):
    """Estimate page count, API call budget, and batch delivery breakdown."""
    chapters = project.get('source', {}).get('chapters', [])
    body_chapters = [c for c in chapters if isinstance(c, dict) and c.get('has_body')]
    body_count = len(body_chapters)
    units = project.get('source', {}).get('units', [])
    total_words = sum(len(u.get('text', '')) for u in units if isinstance(u, dict))

    script_panels = project.get('script', {}).get('panels', [])
    script_pages = project.get('script', {}).get('pages', [])

    actual_or_est_panels = len(script_panels) if script_panels else body_count * 60
    actual_or_est_pages = len(script_pages) if script_pages else max(1, (actual_or_est_panels + 2) // 3)

    min_generation_calls = actual_or_est_pages
    max_generation_calls = actual_or_est_pages * 3
    major_characters = [c for c in project.get('script', {}).get('characters', []) if isinstance(c, dict) and c.get('importance') == 'major']
    est_reference_calls = max(2, len(major_characters) * 2)

    stages = stage_batches(project) if body_count else []

    return {
        'ok': True,
        'body_chapters_count': body_count,
        'source_text_characters': total_words,
        'estimated_panels': actual_or_est_panels,
        'estimated_pages': actual_or_est_pages,
        'call_budget': {
            'min_page_renders': min_generation_calls,
            'max_page_renders': max_generation_calls,
            'reference_renders': est_reference_calls,
            'total_max_calls': max_generation_calls + est_reference_calls,
        },
        'delivery_batches': len(stages),
        'stages_breakdown': [
            {'stage': s['stage_index'], 'chapters': f"{s['start_chapter_id']}..{s['end_chapter_id']}", 'count': s['chapter_count']}
            for s in stages
        ],
        'estimated_art_calls': max_generation_calls + est_reference_calls,
        'cost_visibility': {
            'estimated_pages': actual_or_est_pages,
            'max_generation_calls': max_generation_calls,
            'total_max_calls': max_generation_calls + est_reference_calls
        }
    }


def check_typeset_feasibility(project):
    """Check dialogue text capacity, line estimation, and dialogue completeness before script lock."""
    panels = project.get('script', {}).get('panels', [])
    pages = project.get('script', {}).get('pages', [])
    style = project.get('script', {}).get('style', {})
    canvas_w = style.get('width', 1080)
    target_font_size = style.get('font_size', 54)

    issues = []
    total_dialogue_chars = 0
    total_dialogue_items = 0

    MAX_ITEM_CHARS = 120
    MAX_PANEL_CHARS = 200

    for panel in panels:
        pid = panel.get('id', '?')
        dialogue = panel.get('dialogue', [])
        panel_chars = 0
        for dindex, d in enumerate(dialogue):
            text = d.get('text', '')
            trimmed = text.strip()
            if not trimmed:
                issues.append({'panel_id': pid, 'dialogue_index': dindex,
                               'issue': 'Dialogue text is empty.'})
            tlen = len(trimmed)
            panel_chars += tlen
            total_dialogue_chars += tlen
            total_dialogue_items += 1

            if tlen > MAX_ITEM_CHARS:
                chars_per_line = max(8, int((canvas_w * 0.4) / target_font_size))
                est_lines = (tlen + chars_per_line - 1) // chars_per_line
                issues.append({
                    'panel_id': pid,
                    'dialogue_index': dindex,
                    'char_count': tlen,
                    'estimated_lines': est_lines,
                    'issue': f"Dialogue text length ({tlen} chars, est. {est_lines} lines) exceeds readable bubble capacity at {target_font_size}px font size. Split into multiple bubbles/panels or trim narration."
                })

        if panel_chars > MAX_PANEL_CHARS:
            issues.append({
                'panel_id': pid,
                'total_chars': panel_chars,
                'issue': f"Total dialogue in panel {pid} ({panel_chars} chars) exceeds panel typography budget (max {MAX_PANEL_CHARS} chars)."
            })

    cost_estimate = estimate_production_cost(project)

    return {
        'ok': len(issues) == 0,
        'issues': issues,
        'errors': [i['issue'] for i in issues],
        'total_dialogue_items': total_dialogue_items,
        'total_dialogue_chars': total_dialogue_chars,
        'panel_count': len(panels),
        'page_count': len(pages),
        'cost_estimate': cost_estimate
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
                   'art': {'references': [], 'pages': {}},
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
    if command == 'check-stage-review':
        if getattr(args, 'file', None):
            report_path = Path(args.file)
            if not report_path.is_file() and root.is_dir():
                try:
                    report_path = inside(root, args.file)
                except GateError:
                    pass
            if not report_path.is_file():
                raise GateError(f'Stage review file not found: {args.file}')
            report = load_json(report_path)
            proj = project_load(root) if root.is_dir() and (root / 'project.json').is_file() else None
            validate_stage_review(report, root if root.is_dir() else None, project=proj)
            approved = report.get('stage_approved')
            if approved is None:
                approved = report.get('round_2_verification', {}).get('stage_approved')
            if approved is None and report.get('round_1_initial_audit', {}).get('initial_verdict') == 'PASSED':
                approved = True
            return {'ok': True, 'file': str(report_path), 'stage_approved': approved}
        else:
            proj = project_load(root) if root.is_dir() and (root / 'project.json').is_file() else None
            req_stages = stage_batches(proj) if proj else []
            stage_dir = (root / 'reports' / 'stage_reviews') if root.is_dir() else Path('reports/stage_reviews')
            if not stage_dir.is_dir():
                if req_stages:
                    raise GateError(f'No reports/stage_reviews directory found. {len(req_stages)} stage review(s) required.')
                return {'ok': True, 'checked_count': 0, 'message': 'No reports/stage_reviews directory found.'}
            reports = sorted(stage_dir.glob('*.json'))
            if not reports:
                if req_stages:
                    raise GateError(f'No stage review files found in reports/stage_reviews. {len(req_stages)} stage review(s) required.')
                return {'ok': True, 'checked_count': 0, 'message': 'No stage review files found in reports/stage_reviews.'}

            if proj and req_stages:
                report_map = {}
                for rpath in reports:
                    rep = load_json(rpath)
                    validate_stage_review(rep, root if root.is_dir() else None, project=proj)
                    report_map[tuple(rep.get('reviewed_chapters', []))] = (rpath, rep)

                missing_stages = []
                stale_stages = []
                for st in req_stages:
                    key = tuple(st['reviewed_chapters'])
                    if key not in report_map:
                        missing_stages.append(f"Stage {st['stage_index']} ({st['start_chapter_id']}..{st['end_chapter_id']})")
                    else:
                        rpath, rep = report_map[key]
                        exp_hash = stage_script_fingerprint(proj, st['reviewed_chapters'])
                        rep_hash = rep.get('stage_script_hash') or rep.get('stage_range', {}).get('stage_script_hash')
                        if rep_hash and rep_hash != exp_hash:
                            stale_stages.append(f"Stage {st['stage_index']} ({rpath.name})")

                if missing_stages or stale_stages:
                    errs = []
                    if missing_stages:
                        errs.append(f"Missing required stage reviews: {', '.join(missing_stages)}")
                    if stale_stages:
                        errs.append(f"Stale stage reviews: {', '.join(stale_stages)}")
                    raise GateError('; '.join(errs))

            if len(reports) > 1:
                all_issues = []
                all_approved_at = []
                for rpath in reports:
                    rep = load_json(rpath)
                    approved = rep.get('round_2_verification', {}).get('approved_at')
                    if approved:
                        all_approved_at.append(approved)
                    for aud in rep.get('round_1_initial_audit', {}).get('auditors', []):
                        for iss in aud.get('issues', []):
                            desc = iss.get('problem_description', '').strip()
                            fix = iss.get('suggested_fix', '').strip()
                            if desc and fix:
                                all_issues.append((desc, fix, rpath.name))
                if len(all_approved_at) >= 3 and len(set(all_approved_at)) == 1:
                    raise GateError(f'Anti-batch synthesis gate: {len(all_approved_at)} stage reviews share the exact same approved_at timestamp ({all_approved_at[0]}). Reviews must be conducted sequentially, not batch-synthesized.')
                issue_map = {}
                for desc, fix, fname in all_issues:
                    key = (desc, fix)
                    if key in issue_map and issue_map[key] != fname:
                        raise GateError(f'Anti-batch synthesis gate: Identical issue copy-pasted across {issue_map[key]} and {fname}: "{desc[:30]}...". Each stage must identify unique, chapter-specific issues.')
                    issue_map[key] = fname
            verified = []
            for rpath in reports:
                report = load_json(rpath)
                validate_stage_review(report, root if root.is_dir() else None, project=proj)
                verified.append(rpath.name)
            return {'ok': True, 'checked_count': len(verified), 'verified_files': verified}
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

    if command == 'build-prompt':
        assert_script_lock(project, root)
        from comic_pages import build_page_prompt
        notes = ''
        if getattr(args, 'notes', None):
            try:
                np = resolve_file_arg(args.notes, project_dir=root)
                if np.is_file():
                    notes = np.read_text(encoding='utf-8-sig')
                else:
                    notes = str(args.notes)
            except Exception:
                notes = str(args.notes)
        prompt_text = build_page_prompt(root, project, args.page, notes)
        if args.output:
            output = Path(args.output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(prompt_text, encoding='utf-8')
            return {'ok': True, 'page_id': args.page, 'output_path': str(output), 'char_count': len(prompt_text)}
        return {'ok': True, 'page_id': args.page, 'prompt': prompt_text, 'char_count': len(prompt_text)}
    if command == 'preview-page':
        from comic_layout import preview_page
        return preview_page(root, project, args.page, file=getattr(args, 'file', None))
    if command == 'estimate-cost':
        return estimate_production_cost(project)
    if command == 'preflight-typeset':
        return check_typeset_feasibility(project)
    if command == 'qa-inputs':
        return qa_inputs(root, project, args)
    if command == 'chapter':
        return [u for u in project['source']['units'] if not args.chapter or u['chapter_id'] == args.chapter]
    if command == 'script-chapter':
        return script_chapter(project, args.chapter)
    if command == 'check-adaptation':
        draft = load_json(args.file) if getattr(args, 'file', None) else project['script']
        errors = source_errors(project, root)
        errors.extend(adaptation_errors(project['source'], draft, root, args.chapter,
                                        source_index_hash=project.get('source_index_hash'),
                                        current_names=(project.get('volume'), project.get('title'))))
        return {'chapter_id': args.chapter, 'draft_hash': digest(draft), 'errors': errors[:100],
                'error_count': len(errors), 'semantic_review_required': True}
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
        adaptations = fragment.get('chapter_adaptations')
        if (not isinstance(adaptations, list) or len(adaptations) != 1
                or not isinstance(adaptations[0], dict) or adaptations[0].get('chapter_id') != chapter_id):
            raise GateError('Chapter update requires exactly one matching chapter_adaptations record.')
        script['chapter_adaptations'] = [r for r in script.get('chapter_adaptations', [])
                                        if isinstance(r, dict) and r.get('chapter_id') != chapter_id] + adaptations
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
        if root:
            req_stages = stage_batches(project)
            stage_dir = root / 'reports' / 'stage_reviews'
            if req_stages and not stage_dir.is_dir():
                errors.append(f'Missing required stage reviews: project requires {len(req_stages)} stage review(s), but reports/stage_reviews directory does not exist.')
            elif req_stages:
                stage_reports = sorted(stage_dir.glob('*.json'))
                report_map = {}
                for sfile in stage_reports:
                    try:
                        rep = load_json(sfile)
                        validate_stage_review(rep, root, project=project)
                        report_map[tuple(rep.get('reviewed_chapters', []))] = (sfile, rep)
                    except GateError as ge:
                        errors.append(f'Stage review {sfile.name} failed validation: {ge}')
                    except Exception as e:
                        errors.append(f'Stage review {sfile.name} error: {e}')

                for st in req_stages:
                    key = tuple(st['reviewed_chapters'])
                    if key not in report_map:
                        errors.append(f"Missing required stage review for Stage {st['stage_index']} (chapters {st['start_chapter_id']}..{st['end_chapter_id']}, count {st['chapter_count']}).")
                    else:
                        sfile, rep = report_map[key]
                        exp_hash = stage_script_fingerprint(project, st['reviewed_chapters'])
                        rep_hash = rep.get('stage_script_hash') or rep.get('stage_range', {}).get('stage_script_hash')
                        if not nonempty(rep_hash):
                            errors.append(f"Stage review {sfile.name} is missing required stage_script_hash.")
                        elif rep_hash != exp_hash:
                            errors.append(f"Stage review {sfile.name} is stale: script was modified after review (hash mismatch: expected {exp_hash}, got {rep_hash}).")
                        approved = rep.get('stage_approved')
                        if approved is None:
                            approved = rep.get('round_2_verification', {}).get('stage_approved')
                        if approved is None and rep.get('round_1_initial_audit', {}).get('initial_verdict') == 'PASSED':
                            approved = True
                        if not approved:
                            errors.append(f"Stage review {sfile.name} is not approved.")
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
    elif command == 'begin-page':
        from comic_pages import begin_page
        return begin_page(root,project,args.page,args.prompt)
    elif command == 'finish-page':
        from comic_pages import finish_page
        return finish_page(root,project,args.page,args.attempt,args.file,args.qa)
    elif command == 'fail-page':
        from comic_pages import fail_page
        return fail_page(root,project,args.page,args.attempt,args.reason,args.outcome,getattr(args,'file',None),
                         generation_failure=getattr(args, 'generation_failure', False))
    elif command in ('prepare-pages', 'review-layout', 'export', 'verify-export', 'complete'):
        assert_script_lock(project, root)
        from comic_layout import layout_fingerprint, prepare_pages, export, verify_exports
        fingerprint = layout_fingerprint(root,project)
        if command == 'prepare-pages':
            project['layout'] = prepare_pages(root,project)
            project['exports'],project['final_review'] = None,None
        else:
            layout = project.get('layout')
            if not layout or layout['input_hash'] != fingerprint:
                raise GateError('Whole pages missing or stale.')
            for page in layout['pages']:
                path = inside(root, page['path'])
                if not path.is_file() or sha_file(path) != page['sha256']:
                    raise GateError('Whole page missing or changed.')
            if command == 'review-layout':
                report = load_json(args.file)
                validate_qa(report, LAYOUT_CHECKS)
                from comic_layout import validate_phone_reading_report
                validate_phone_reading_report(project, report)
                if report.get('input_hash') != fingerprint or set(report.get('reviewed_page_ids', [])) != {p['id'] for p in layout['pages']}:
                    raise GateError('Visual layout review must bind current hash and every whole page.')
                layout['qa'] = report
            else:
                if not layout.get('qa'):
                    raise GateError('View and review every whole page before export.')
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
    elif command in ('status','preflight'):
        from comic_pages import status
        return status(root,project)
    else:
        raise GateError('Unknown command.')
    save(root, project)
    if command in ('lock-script', 'export', 'complete'):
        write_volume_readme(root, project)
    return {'ok': True, 'command': command, 'script_hash': digest(project['script'])}


def parser():
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subs = p.add_subparsers(dest='command', required=True)
    _orig_add_parser = subs.add_parser
    def add_subparser(*args, **kwargs):
        kwargs.setdefault('allow_abbrev', False)
        return _orig_add_parser(*args, **kwargs)
    subs.add_parser = add_subparser
    book_p = subs.add_parser('init-book')
    book_p.add_argument('--book-dir', '--project', dest='book_dir', required=True,
                        help='Path to top-level book directory')
    book_p.add_argument('--title', help='Book title')
    book_p.add_argument('--source', nargs='*', help='One or more source novel files to archive into source_texts/')
    book_p.add_argument('--action', choices=['copy', 'move'], default='copy',
                        help='Action for source novel files: copy (default, preserve original) or move (explicit only)')
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

    build_prompt_p = subs.add_parser('build-prompt')
    build_prompt_p.add_argument('--project', required=True, help='Volume project directory')
    build_prompt_p.add_argument('--page', required=True, help='Page ID to compile prompt for')
    build_prompt_p.add_argument('--notes', help='Optional notes text or file path')
    build_prompt_p.add_argument('--output', help='Output prompt file path (default stdout)')

    preview_p = subs.add_parser('preview-page')
    preview_p.add_argument('--project', required=True, help='Volume project directory')
    preview_p.add_argument('--page', required=True, help='Page ID to generate phone previews for')
    preview_p.add_argument('--file', help='Candidate image path (defaults to latest attempt or accepted page)')

    cost_p = subs.add_parser('estimate-cost')
    cost_p.add_argument('--project', required=True, help='Volume project directory')

    for name in ('init', 'preflight', 'qa-inputs', 'chapter', 'script-chapter', 'resolve-issue', 'confirm-source',
                 'mark-read', 'set-script', 'set-script-chapter', 'impact', 'check-script', 'check-adaptation',
                 'check-stage-review', 'review',
                 'lock-script', 'assert-art', 'register-reference',
                 'begin-page', 'finish-page', 'fail-page', 'prepare-pages',
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
            sub.add_argument('--page')
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
                    'finish-page', 'review-layout', 'complete'):
            sub.add_argument('--file', required=True)
        if name == 'check-stage-review':
            sub.add_argument('--file', help='Stage review report JSON; defaults to all reports in reports/stage_reviews/')
        if name == 'set-script-chapter':
            sub.add_argument('--chapter', required=True)
        if name == 'check-adaptation':
            sub.add_argument('--chapter', required=True)
            sub.add_argument('--file', help='Chapter draft JSON; defaults to imported volume script.')
        if name == 'review':
            sub.add_argument('--kind', choices=REVIEW_CHECKS, required=True)
        if name == 'register-reference':
            selection = sub.add_mutually_exclusive_group(required=True)
            selection.add_argument('--characters', nargs='+')
            selection.add_argument('--bindings')
        if name in ('register-reference', 'finish-page'):
            sub.add_argument('--qa', required=True)
        if name in ('begin-page','finish-page','fail-page'):
            sub.add_argument('--page',required=True)
        if name in ('finish-page','fail-page'):
            sub.add_argument('--attempt',type=int,required=True)
        if name == 'begin-page':
            sub.add_argument('--prompt',required=True)
        if name == 'fail-page':
            sub.add_argument('--file',help='Archive an actual failed whole-page output')
            sub.add_argument('--reason',required=True)
            sub.add_argument('--outcome',choices=('failed','cancelled','stale'),default='failed')
            sub.add_argument('--generation-failure', action='store_true', help='Mark as render generation failure (allows up to 6 retries)')
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
