"""Mechanical detail records for single-action fixtures, never semantic approval."""
from pathlib import Path

from comic_sources import sha_file


def attach_adaptations(root, source, script):
    records = []
    for chapter in source['chapters']:
        if not chapter['has_body']:
            continue
        cid = chapter['id']
        body = [u for u in source['units'] if u['chapter_id'] == cid and u['kind'] == 'body']
        details, audits = [], []
        for i, unit in enumerate(body):
            panel = next(p for p in script['panels'] if p['chapter_id'] == cid and unit['id'] in p['source_unit_ids'])
            did = f'd{i+1}'
            details.append({'id': did, 'kind': 'action', 'fact': unit['text'],
                            'narrative_function': 'Mechanical single-action fixture.',
                            'sources': [{'unit_id': unit['id'], 'quote': unit['text']}],
                            'treatment': 'shown', 'presentations': [
                                {'panel_id': panel['id'], 'field': 'action', 'excerpt': panel['action']} ]})
            audits.append({'unit_id': unit['id'], 'detail_ids': [did],
                           'evidence': 'Mechanical fixture accounting, not an actual semantic review.'})
        path = f'docs/adaptation/{cid}.md'
        target = Path(root) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('Mechanical single-action fixture document.\n' + '\n'.join(u['text'] for u in body), encoding='utf-8')
        records.append({'chapter_id': cid, 'document_path': path, 'document_sha256': sha_file(target),
                        'details': details, 'unit_audits': audits})
    script['chapter_adaptations'] = records
    return script
