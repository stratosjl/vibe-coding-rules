"""Install only the native Codex continuation hooks, preserving existing hooks."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

from continuation import save


EVENTS = ('UserPromptSubmit', 'Stop', 'Interrupt', 'SessionStart', 'PostCompact')
TAG = 'VC-ROE continuation'


def install(home: Path):
    home = home.resolve()
    runtime = home / 'vc-roe/runtime/continuation.py'
    hooks_file = home / 'hooks.json'
    for path in (home/'vc-roe', runtime.parent, runtime, hooks_file):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Installation targets must not be links')
    original = hooks_file.read_bytes() if hooks_file.exists() else b'{}'
    hooks = json.loads(original)
    if not isinstance(hooks.get('hooks', {}), dict):
        raise ValueError('Invalid existing hook configuration')
    source = Path(__file__).with_name('continuation.py')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = home/'vc-roe/backups'/stamp
    backup.mkdir(parents=True)
    (backup/'hooks-before.json').write_bytes(original)
    if runtime.exists():
        shutil.copy2(runtime, backup/'continuation-before.py')
    runtime.parent.mkdir(parents=True, exist_ok=True)
    temporary = runtime.with_suffix('.py.new')
    temporary.write_bytes(source.read_bytes())
    os.replace(temporary, runtime)
    if os.name == 'nt':
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        command = '& ' + quote(Path(sys.executable).resolve()) + ' ' + quote(runtime) + ' hook; exit $LASTEXITCODE'
    else:
        command = shlex.quote(sys.executable) + ' ' + shlex.quote(str(runtime)) + ' hook'
    for event in EVENTS:
        groups = hooks.setdefault('hooks', {}).setdefault(event, [])
        retained = []
        for group in groups:
            handlers = [h for h in group.get('hooks', []) if h.get('statusMessage') != TAG]
            if handlers:
                retained.append({**group, 'hooks': handlers})
        retained.append({'hooks': [{'type': 'command', 'command': command,
                                   'timeout': 3 if event == 'Interrupt' else 10, 'statusMessage': TAG}]})
        hooks['hooks'][event] = retained
    if hooks_file.exists() and hooks_file.read_bytes() != original:
        raise ValueError('Hook configuration changed during installation; backup retained')
    save(hooks_file, hooks)
    receipt = {'runtime': str(runtime), 'runtime_sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(),
               'hooks': str(hooks_file), 'backup': str(backup), 'events': EVENTS,
               'trust': 'pending exact hook review; installation does not establish execution'}
    save(backup/'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home()/'.codex')))
    args = parser.parse_args()
    print(json.dumps(install(args.codex_home), indent=2))
