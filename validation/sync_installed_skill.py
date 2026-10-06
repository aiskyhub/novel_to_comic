"""Back up the installed skills, then copy maintained source files and verify bytes."""
from pathlib import Path
from datetime import datetime
import hashlib, json, shutil

repo = Path(__file__).resolve().parents[1]
source = repo.resolve()
skill_dirs = {'agents', 'assets', 'references', 'scripts', 'tests'}
skill_files = {'SKILL.md', 'VERSION.json'}

targets = [
    Path('C:/Users/xdd66/.codex/skills/novel-to-comic'),
    Path('C:/Users/xdd66/.gemini/config/skills/novel-to-comic')
]

files = [p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts
         and '.pytest_cache' not in p.parts and '.venv' not in p.parts and p.suffix not in ('.pyc', '.pyo')
         and (p.name in skill_files or any(part in skill_dirs for part in p.parts))]
if not files or not (source / 'SKILL.md').is_file():
    raise ValueError('Incomplete maintained source')

reports = []
timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')

for installed_entry in targets:
    installed = installed_entry.resolve()
    backup_root = (installed.parent.parent / 'skill-backups').resolve()
    backup = (backup_root / (f"{timestamp}-{installed.name}")).resolve()
    
    backup_root.mkdir(parents=True, exist_ok=True)
    if installed.exists():
        shutil.copytree(installed, backup)
    installed.mkdir(parents=True, exist_ok=True)

    for path in files:
        dest = (installed / path.relative_to(source)).resolve()
        if not dest.is_relative_to(installed):
            raise ValueError(f'Installed target escapes skill directory: {dest}')
        if source != installed:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

    for path in files:
        if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256((installed / path.relative_to(source)).read_bytes()).digest():
            raise ValueError(f'Installed content mismatch for {installed}: {path}')

    reports.append({
        'backup_path': str(backup),
        'installed_path': str(installed_entry),
        'resolved_installed_path': str(installed),
        'update_mode': 'linked_source' if source == installed else 'copied_source',
        'files_verified': len(files),
        'result': 'passed'
    })

(repo / 'validation/installed-sync-report.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(reports, ensure_ascii=False, indent=2))
