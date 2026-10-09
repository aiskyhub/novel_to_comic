"""Mirror the current skill; remove obsolete owned files without backups or migration."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sync(target):
    source = Path(__file__).resolve().parents[1]
    target = target.expanduser().resolve()
    if target.name != 'novel-to-comic' or target == source:
        raise ValueError('Target must be a separate, explicitly named novel-to-comic skill directory.')
    owned_dirs = {'agents','assets','references','scripts','tests','.codex-plugin','validation','README','roles'}
    owned_files = {'SKILL.md','README.md','VERSION.json','.gitignore'}
    # 清理已废弃的 role 目录
    legacy_role_dir = target / 'role'
    if legacy_role_dir.is_dir():
        shutil.rmtree(legacy_role_dir, ignore_errors=True)
    files = [p for p in source.rglob('*') if p.is_file()
             and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')
             and ((p.parent == source and p.name in owned_files)
                  or p.relative_to(source).parts[0] in owned_dirs)]
    desired = {p.relative_to(source) for p in files}
    target.mkdir(parents=True,exist_ok=True)
    removed = []
    for name in owned_dirs:
        directory = (target/name).resolve()
        if not directory.is_relative_to(target):
            raise ValueError('Owned directory escapes the explicit skill root.')
        if not directory.is_dir():
            continue
        for old in list(directory.rglob('*')):
            if old.is_file() and old.relative_to(target) not in desired:
                if not old.resolve().is_relative_to(target):
                    raise ValueError('Obsolete file escapes the explicit skill root.')
                removed.append(old.relative_to(target).as_posix())
                old.unlink()
    for original in files:
        destination = (target/original.relative_to(source)).resolve()
        if not destination.is_relative_to(target):
            raise ValueError('Copy target escapes the explicit skill root.')
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(original,destination)
        if hashlib.sha256(original.read_bytes()).digest()!=hashlib.sha256(destination.read_bytes()).digest():
            raise ValueError('Installed file differs: '+str(destination))
    return {'installed_path':str(target),'files_verified':len(files),'obsolete_files_removed':len(removed)}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',type=Path,default=Path('C:/Users/xdd66/.codex/skills/novel-to-comic'))
    print(json.dumps(sync(parser.parse_args().target),ensure_ascii=False))
