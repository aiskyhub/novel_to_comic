from pathlib import Path
import ast,json,re,yaml
repo_root = Path(__file__).resolve().parents[1]
root = repo_root
counts={'json':0,'yaml':0,'python':0,'markdown_links':0}
excluded_parts = {'__pycache__', '.pytest_cache', '.git', '.codex-plugin', 'validation', '.venv'}
for path in root.rglob('*'):
    if not path.is_file() or any(p in path.parts for p in excluded_parts):continue
    text=path.read_text(encoding='utf-8-sig')
    if path.suffix=='.json':json.loads(text);counts['json']+=1
    elif path.suffix=='.yaml':yaml.safe_load(text);counts['yaml']+=1
    elif path.suffix=='.py':ast.parse(text,filename=str(path));counts['python']+=1
    elif path.suffix=='.md':
        is_template = any(part in ('README', 'templates') for part in path.parts)
        for ref in re.findall(r'\[[^\]]*\]\(([^)]+)\)',text):
            if '://' in ref or ref.startswith('#'):continue
            ref=ref.split('#',1)[0]
            if ref and not is_template and not (path.parent/ref).is_file():raise ValueError(f'{path}: missing link {ref}')
            counts['markdown_links']+=1

# Validate schema_version consistency
schema_m = re.search(r'SCHEMA_VERSION\s*=\s*(\d+)', (root / 'scripts/comic_pipeline.py').read_text(encoding='utf-8'))
if not schema_m: raise ValueError("Could not find SCHEMA_VERSION in comic_pipeline.py")
expected_schema = int(schema_m.group(1))
for md_path in root.rglob('*.md'):
    if any(p in md_path.parts for p in excluded_parts): continue
    for sm in re.finditer(r'(?:schema_version[ =:]+|schema\s+v|Schema\s+v)(\d+)', md_path.read_text(encoding='utf-8'), re.I):
        if int(sm.group(1)) != expected_schema:
            raise ValueError(f"Schema version mismatch in {md_path}: found {sm.group(1)}, expected {expected_schema}")
counts['schema_verified'] = expected_schema

print(json.dumps({'package_validation':'passed','checked':counts},ensure_ascii=False))
