"""Check traceable adaptation records, not semantic completeness or quality."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def text(value):
    return isinstance(value, str) and bool(value.strip())


def array(value):
    return value if isinstance(value, list) else []


def panel_value(panel, field):
    """Allow evidence only in reader-facing narrative fields, never source IDs."""
    if not isinstance(field, str) or not re.fullmatch(
            r'(?:action|space|expression|dialogue\.\d+\.text)', field):
        return None
    value = panel
    for part in field.split('.'):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value if isinstance(value, str) else None


def adaptation_errors(source, script, root=None, chapter_id=None, *, source_index_hash=None, current_names=()):
    """Validate one chapter draft or every body chapter in an imported script."""
    errors = []
    if not isinstance(source, dict) or not isinstance(script, dict):
        return ['script.chapter_adaptations: source and script must be objects.']
    chapters = {c['id']: c for c in array(source.get('chapters'))
                if isinstance(c, dict) and text(c.get('id')) and c.get('has_body')}
    if chapter_id is not None:
        if not text(chapter_id) or chapter_id not in chapters:
            return ['script.chapter_adaptations: unknown or empty chapter.']
        chapters = {chapter_id: chapters[chapter_id]}
    units = {u['id']: u for u in array(source.get('units'))
             if isinstance(u, dict) and text(u.get('id'))}
    panels = {p['id']: p for p in array(script.get('panels'))
              if isinstance(p, dict) and text(p.get('id'))}
    records = script.get('chapter_adaptations')
    if not isinstance(records, list):
        return ['script.chapter_adaptations: required chapter detail records are missing.']
    by_chapter = {}
    for i, record in enumerate(records):
        base = f'script.chapter_adaptations[{i}]'
        if not isinstance(record, dict) or not text(record.get('chapter_id')):
            errors.append(base + ': expected an object with chapter_id.')
            continue
        cid = record['chapter_id']
        if chapter_id is not None and cid != chapter_id:
            continue
        if cid not in chapters or cid in by_chapter:
            errors.append(base + ': unknown, empty, or duplicate chapter.')
        else:
            by_chapter[cid] = record
    for cid in chapters:
        base = f'script.chapter_adaptations[{cid}]'
        record = by_chapter.get(cid)
        if record is None:
            errors.append(base + ': chapter detail record missing.')
            continue
        if not chapters[cid].get('read') or not text(chapters[cid].get('read_note')):
            errors.append(base + ': chapter must actually be read first.')
        path, expected = record.get('document_path'), record.get('document_sha256')
        if path != f'docs/adaptation/{cid}.md' or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            errors.append(base + '.document_path/document_sha256: require chapter document and SHA256.')
        elif root is not None:
            try:
                target = (Path(root) / path).resolve()
                if not target.is_relative_to(Path(root).resolve()):
                    raise ValueError('outside project')
                content = target.read_bytes() if target.is_file() else b''
                if not content.strip() or hashlib.sha256(content).hexdigest() != expected:
                    errors.append(base + '.document_sha256: chapter document missing or changed.')
            except (OSError, ValueError):
                errors.append(base + '.document_path: invalid chapter document.')
        body = {uid: u for uid, u in units.items() if u.get('chapter_id') == cid and u.get('kind') == 'body'}
        details = record.get('details')
        if not isinstance(details, list) or not details:
            errors.append(base + '.details: require extracted narrative details.')
        detail_map, detail_sources = {}, {}
        for i, detail in enumerate(array(details)):
            db = base + f'.details[{i}]'
            if not isinstance(detail, dict) or not text(detail.get('id')):
                errors.append(db + ': expected a detail object with id.')
                continue
            did = detail['id']
            if did in detail_map:
                errors.append(db + '.id: duplicate detail ID.')
            detail_map[did] = detail
            for key in ('fact', 'narrative_function'):
                if not text(detail.get(key)):
                    errors.append(db + '.' + key + ': require concrete fact and narrative purpose.')
            if detail.get('kind') not in ('action', 'dialogue', 'thought', 'reveal', 'state', 'environment', 'paratext'):
                errors.append(db + '.kind: invalid detail kind.')
            sources = detail.get('sources')
            if not isinstance(sources, list) or not sources:
                errors.append(db + '.sources: require actual source quotes.')
            detail_sources[did] = set()
            for j, src in enumerate(array(sources)):
                sb = db + f'.sources[{j}]'
                uid = src.get('unit_id') if isinstance(src, dict) else None
                quote = src.get('quote') if isinstance(src, dict) else None
                if not text(uid) or uid not in body or not text(quote) or quote not in body[uid].get('text', ''):
                    errors.append(sb + ': quote must occur in this chapter body unit.')
                else:
                    detail_sources[did].add(uid)
            treatment = detail.get('treatment')
            presentations = detail.get('presentations')
            if treatment not in ('shown', 'merged', 'repetition', 'paratext'):
                errors.append(db + '.treatment: invalid or unresolved treatment.')
            if treatment != 'shown' and not text(detail.get('reason')):
                errors.append(db + '.reason: explain every merge or non-drawn item.')
            if not isinstance(presentations, list):
                errors.append(db + '.presentations: expected an array.')
            if treatment in ('shown', 'merged') and not array(presentations):
                errors.append(db + '.presentations: panel IDs alone do not prove retention.')
            if treatment in ('repetition', 'paratext') and array(presentations):
                errors.append(db + '.presentations: use shown/merged for directly presented content.')
            if treatment == 'paratext' and detail.get('kind') != 'paratext':
                errors.append(db + '.treatment: narrative detail cannot be classified as paratext.')
            for j, presentation in enumerate(array(presentations)):
                pb = db + f'.presentations[{j}]'
                pid = presentation.get('panel_id') if isinstance(presentation, dict) else None
                panel = panels.get(pid) if text(pid) else None
                if panel is None or panel.get('chapter_id') != cid:
                    errors.append(pb + '.panel_id: require an actual panel in this chapter.')
                    continue
                value = panel_value(panel, presentation.get('field'))
                excerpt = presentation.get('excerpt')
                if not text(excerpt) or not isinstance(value, str) or excerpt not in value:
                    errors.append(pb + '.excerpt: must occur in the specified narrative field.')
                panel_sources = set()
                for s in array(panel.get('source_unit_ids')):
                    key = s if isinstance(s, str) else None
                    if (isinstance(s, dict) and s.get('volume_id') in current_names
                            and source_index_hash is not None and s.get('source_index_hash') == source_index_hash):
                        key = s.get('unit_id')
                    if text(key):
                        panel_sources.add(key)
                if not detail_sources[did].issubset(panel_sources):
                    errors.append(pb + ': panel must bind the detail source units.')
        for did, detail in detail_map.items():
            if detail.get('treatment') == 'repetition':
                retained_id = detail.get('retained_detail_id')
                retained = detail_map.get(retained_id) if text(retained_id) else None
                if retained is None or retained_id == did or retained.get('treatment') not in ('shown', 'merged'):
                    errors.append(base + f'.details[{did}].retained_detail_id: require a directly retained detail.')
        audits = record.get('unit_audits')
        if not isinstance(audits, list):
            errors.append(base + '.unit_audits: require original-to-detail reread records.')
        audited = set()
        for i, audit in enumerate(array(audits)):
            ab = base + f'.unit_audits[{i}]'
            uid = audit.get('unit_id') if isinstance(audit, dict) else None
            if not text(uid) or uid not in body or uid in audited:
                errors.append(ab + ': unknown, foreign, or duplicate body unit.')
                continue
            audited.add(uid)
            ids = audit.get('detail_ids')
            expected_ids = {did for did, source_ids in detail_sources.items() if uid in source_ids}
            if (not isinstance(ids, list) or not ids or any(not text(did) for did in ids)
                    or len(ids) != len(set(ids)) or set(ids) != expected_ids):
                errors.append(ab + '.detail_ids: list all and only details extracted from this unit.')
            if not text(audit.get('evidence')):
                errors.append(ab + '.evidence: record actual reread findings, not a coverage percentage.')
        for uid in body.keys() - audited:
            errors.append(base + '.unit_audits: original unit has no reread record: ' + uid)
    return errors
