"""Evidence-based continuation for explicitly registered Codex work."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import time
import unicodedata


PREFIX = 'VC-ROE continuation check: '
MODES = {'active', 'stopped', 'interrupted'}


def state_path(session: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session):
        raise ValueError('Invalid session identifier')
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()
    return home / 'vc-roe/continuation' / (session + '.json')


@contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    # An abandoned lock stays visible; never steal a live writer's lock.
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        lock.unlink()


def save(path: Path, state: dict):
    fd, name = tempfile.mkstemp(prefix=path.stem + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, indent=2, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def artifact(root: Path, name: str) -> Path:
    rel = PurePosixPath(name)
    if not name or rel.is_absolute() or '..' in rel.parts or '\\' in name or ':' in name:
        raise ValueError('Evidence paths must be relative and inside the workspace')
    path = root.joinpath(*rel.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Evidence path escapes the workspace')
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Evidence paths cannot traverse links')
    return path


def digest(path: Path):
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(state: dict):
    if state.get('version') != 1 or state.get('mode') not in MODES:
        raise ValueError('Invalid contract version or mode')
    if not isinstance(state.get('objective'), str) or not state['objective'].strip():
        raise ValueError('An objective is required')
    root = Path(state['cwd'])
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('A current absolute workspace is required')
    tasks = state['tasks']
    ids = [t['id'] for t in tasks]
    if (not tasks or len(set(ids)) != len(ids)
            or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key) for key in ids)):
        raise ValueError('Task identifiers must be unique')
    questions = state.get('questions', {})
    for key, value in questions.items():
        if not key or not value.get('text') or value.get('status') not in ('pending', 'answered'):
            raise ValueError('Each question needs text and a valid status')
        if value['status'] == 'answered' and not value.get('answer'):
            raise ValueError('An answered question requires its answer')
    for task in tasks:
        if not task.get('action') or not task.get('artifacts'):
            raise ValueError('Each task needs an action and expected artifacts')
        for item in task['artifacts']:
            artifact(root, item['path'])
            if type(item.get('min_bytes', 1)) is not int or item.get('min_bytes', 1) < 1:
                raise ValueError('Artifacts require a positive minimum byte count')
            if item.get('sha256') and not re.fullmatch('[a-f0-9]{64}', item['sha256']):
                raise ValueError('Invalid expected artifact hash')
        if any(dep not in ids for dep in task.get('depends_on', [])):
            raise ValueError('Unknown dependency')
        if any(q not in questions for q in task.get('wait_for', [])):
            raise ValueError('Unknown question')
        if task.get('external_block') is not None and not task['external_block'].strip():
            raise ValueError('An external blocker requires a concrete reason')
        checks = task.get('checks', [])
        if len({c['id'] for c in checks}) != len(checks):
            raise ValueError('Check identifiers must be unique within a task')
        for check in checks:
            if not check.get('argv') or not all(isinstance(a, str) for a in check['argv']):
                raise ValueError('Checks require an argument array')
            for name in check.get('inputs', []):
                artifact(root, name)
    visiting, visited = set(), set()
    lookup = {t['id']: t for t in tasks}
    def visit(key):
        if key in visiting:
            raise ValueError('Dependency cycle')
        if key in visited:
            return
        visiting.add(key)
        for dep in lookup[key].get('depends_on', []):
            visit(dep)
        visiting.remove(key)
        visited.add(key)
    for key in ids:
        visit(key)


def snapshot(state, task, check):
    names = sorted(set(check.get('inputs', []) + [a['path'] for a in task['artifacts']]))
    return {'argv': check['argv'], 'files': {n: digest(artifact(Path(state['cwd']), n)) for n in names}}


def evaluate(state):
    validate(state)
    root = Path(state['cwd'])
    done, defects = set(), {}
    for task in state['tasks']:
        missing = []
        for item in task['artifacts']:
            path = artifact(root, item['path'])
            observed = digest(path)
            if observed is None or path.stat().st_size < item.get('min_bytes', 1):
                missing.append('missing or empty artifact: ' + item['path'])
            elif item.get('sha256') and observed != item['sha256']:
                missing.append('artifact hash differs: ' + item['path'])
        for check in task.get('checks', []):
            receipt = task.get('receipts', {}).get(check['id'], {})
            current = snapshot(state, task, check)
            if (receipt.get('exit_code') != 0 or receipt.get('snapshot') != current
                    or any(v is None for v in current['files'].values())):
                missing.append('missing, failed or stale check: ' + check['id'])
        if missing:
            defects[task['id']] = missing
        else:
            done.add(task['id'])
    # Completed files cannot satisfy an unresolved prerequisite.
    changed = True
    while changed:
        changed = False
        for task in state['tasks']:
            if task['id'] not in done:
                continue
            if (task.get('external_block') or any(state.get('questions', {})[q]['status'] == 'pending'
                for q in task.get('wait_for', [])) or any(d not in done for d in task.get('depends_on', []))):
                done.remove(task['id'])
                defects.setdefault(task['id'], []).append('unresolved prerequisite')
                changed = True
    ready, waiting, blocked = [], {}, {}
    for task in state['tasks']:
        if task['id'] in done:
            continue
        questions = [q for q in task.get('wait_for', []) if state['questions'][q]['status'] == 'pending']
        if questions:
            waiting[task['id']] = questions
        elif task.get('external_block'):
            blocked[task['id']] = task['external_block']
        elif all(d in done for d in task.get('depends_on', [])):
            ready.append(task['id'])
    status = 'complete' if len(done) == len(state['tasks']) else ('continue' if ready else 'waiting')
    if state['mode'] != 'active':
        status = state['mode']
    return {'status': status, 'done': sorted(done), 'ready': ready, 'waiting': waiting,
            'questions': {q: state['questions'][q]['text'] for values in waiting.values() for q in values},
            'blocked': blocked, 'defects': defects}


def explicit_stop(prompt):
    text = ''.join(c for c in unicodedata.normalize('NFD', prompt.lower().strip())
                   if unicodedata.category(c) != 'Mn')
    pattern = (r'^(?:please\s+)?(?:stop(?:\s+(?:everything(?:\s+else)?|all work|the task|now))?'
               r'|cancel(?:\s+(?:everything|all work|the task))?'
               r'|close (?:the )?session|do not continue|don\x27t continue'
               r'|σταματα(?:\s+(?:τα παντα|ολα|την εργασια|ολες τις εργασιες))?'
               r'|σταματησε(?:\s+(?:τα παντα|ολα|την εργασια|ολες τις εργασιες))?'
               r'|μην? συνεχισεις|κλεισε τη συνεδρια)'
               r'(?:[.!]\s*|$)')
    return re.match(pattern, text) is not None


def handle(event):
    path = state_path(event['session_id'])
    if not path.exists() or event.get('agent_id'):
        if event['hook_event_name'] == 'UserPromptSubmit' and not event.get('agent_id'):
            return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext':
                    'For authorized work with multiple deliverables, register its objective, expected artifacts, '
                    'required checks, dependencies and missing-input questions with the installed VC-ROE '
                    'continuation.py init command in CODEX_HOME/vc-roe/runtime. Keep runtime state in Codex storage '
                    'and shared project facts in their existing records. Use evidence, preserve the objective '
                    'across side questions, and keep small edits proportionate. Single-step replies need no contract. '
                    'No contract is registered for this session, so continuation enforcement does not yet cover it.'}}
        return {}
    name = event['hook_event_name']
    observed = json.loads(path.read_text(encoding='utf-8'))
    if Path(event['cwd']).resolve() != Path(observed['cwd']).resolve():
        return {'systemMessage': 'VC-ROE contract belongs to a different workspace; continuation was not started.'}
    marker = path.with_suffix('.halt.json')
    # Stop evidence has its own atomic file so a check holding the contract lock
    # cannot lose an interruption. This does not terminate unrelated processes.
    if name == 'Interrupt' or (name == 'UserPromptSubmit' and explicit_stop(event.get('prompt', ''))):
        save(marker, {'mode': 'interrupted' if name == 'Interrupt' else 'stopped',
                      'event': name, 'turn_id': event.get('turn_id')})
    if name == 'Interrupt':
        return {'systemMessage': 'VC-ROE preserved unfinished work after interruption. No process termination is inferred.'}
    with locked(path):
        state = json.loads(path.read_text(encoding='utf-8'))
        validate(state)
        if Path(event['cwd']).resolve() != Path(state['cwd']).resolve():
            return {'systemMessage': 'VC-ROE contract belongs to a different workspace; continuation was not started.'}
        if marker.exists():
            state['control_evidence'] = json.loads(marker.read_text(encoding='utf-8'))
            state['mode'] = state['control_evidence']['mode']
        if name == 'UserPromptSubmit':
            prompt = event.get('prompt', '')
            if explicit_stop(prompt):
                state['mode'] = 'stopped'
                state['control_evidence'] = {'event': name, 'turn_id': event.get('turn_id'), 'prompt': prompt}
            elif prompt != state.get('pending_reason'):
                state['continuations'] = 0
                state['pending_reason'] = None
            result = evaluate(state)
            context = 'Registered work: ' + state['objective'].rstrip('. ') + '. State: ' + json.dumps(result)
            if state['mode'] != 'active':
                context += ' Do not resume this contract without an explicit user instruction.'
            output = {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': context}}
        elif name == 'Stop':
            result = evaluate(state)
            state['last_evaluation'] = result
            if result['status'] in ('stopped', 'interrupted'):
                output = {'continue': False, 'stopReason': 'Registered work is ' + result['status']}
            elif result['status'] == 'complete':
                output = {}
            elif result['status'] == 'waiting':
                output = {'systemMessage': 'VC-ROE: work remains incomplete and needs the recorded inputs. ' + json.dumps(result)}
            elif state.get('continuations', 0) >= 3:
                output = {'continue': False, 'stopReason': 'VC-ROE continuation limit reached; work remains incomplete.',
                          'systemMessage': 'Three continuation attempts ended with executable work remaining. Inspect the contract before resuming.'}
            else:
                state['continuations'] = state.get('continuations', 0) + 1
                tasks = {t['id']: t for t in state['tasks']}
                reason = PREFIX + 'The registered objective is incomplete. Continue the authorized executable work: '
                reason += '; '.join(k + ': ' + tasks[k]['action'] for k in result['ready'])
                reason += '. Required evidence: ' + json.dumps(result['defects'])
                reason += '. Preserve explicit stops and required decisions. Do not claim completion from a finished subtask.'
                state['pending_reason'] = reason
                output = {'decision': 'block', 'reason': reason}
        elif name == 'PostCompact':
            output = {'systemMessage': 'VC-ROE retained the contract after compaction: '
                      + state['objective'].rstrip('. ') + '. ' + json.dumps(evaluate(state))}
        elif name == 'SessionStart':
            output = {'hookSpecificOutput': {'hookEventName': name,
                      'additionalContext': 'Registered work survives context changes: ' + state['objective'].rstrip('. ') + '. ' + json.dumps(evaluate(state))}}
        else:
            return {}
        save(path, state)
        return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['init', 'revise', 'status', 'check', 'answer', 'resume', 'hook'])
    parser.add_argument('--session', default=os.environ.get('CODEX_THREAD_ID'))
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--task')
    parser.add_argument('--check')
    parser.add_argument('--question')
    parser.add_argument('--answer')
    parser.add_argument('--user-instruction')
    args = parser.parse_args()
    if args.operation == 'hook':
        try:
            print(json.dumps(handle(json.load(sys.stdin)), ensure_ascii=True))
            return 0
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(json.dumps({'continue': False, 'stopReason': 'VC-ROE state could not be verified; work remains incomplete.',
                              'systemMessage': 'VC-ROE continuation check failed: ' + type(exc).__name__}))
            return 0
    if not args.session:
        parser.error('--session or CODEX_THREAD_ID is required')
    path = state_path(args.session)
    with locked(path):
        if args.operation == 'init':
            if path.exists():
                raise ValueError('A contract already exists; preserve it before creating another')
            state = json.loads(args.plan.read_text(encoding='utf-8'))
            state.update(version=1, mode='active', continuations=0)
            for task in state.get('tasks', []):
                task.pop('receipts', None)
            validate(state)
        else:
            state = json.loads(path.read_text(encoding='utf-8'))
            validate(state)
        marker = path.with_suffix('.halt.json')
        if marker.exists():
            state['mode'] = json.loads(marker.read_text(encoding='utf-8'))['mode']
        if args.operation == 'revise':
            revised = json.loads(args.plan.read_text(encoding='utf-8'))
            removed = {t['id'] for t in state['tasks']} - {t['id'] for t in revised['tasks']}
            if (removed or revised['objective'] != state['objective']) and not args.user_instruction:
                raise ValueError('Removing work or replacing the objective requires the user instruction')
            state.setdefault('prior_plans', []).append({k: state.get(k) for k in ('objective', 'tasks', 'questions')})
            old = {t['id']: t for t in state['tasks']}
            for task in revised['tasks']:
                task['receipts'] = old.get(task['id'], {}).get('receipts', {})
            state.update({k: revised[k] for k in ('objective', 'tasks')})
            state['questions'] = revised.get('questions', {})
            validate(state)
        elif args.operation == 'check':
            if state['mode'] != 'active':
                raise ValueError('Stopped or interrupted work cannot run checks before explicit resumption')
            task = next(t for t in state['tasks'] if t['id'] == args.task)
            check = next(c for c in task.get('checks', []) if c['id'] == args.check)
            before = snapshot(state, task, check)
            result = subprocess.run(check['argv'], cwd=state['cwd'], capture_output=True,
                                    timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            after = snapshot(state, task, check)
            task.setdefault('receipts', {})[check['id']] = {
                'exit_code': result.returncode, 'snapshot': after if before == after else None,
                'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
                'stderr_sha256': hashlib.sha256(result.stderr).hexdigest(), 'checked_at': time.time()}
        elif args.operation == 'answer':
            if not args.answer or args.question not in state.get('questions', {}):
                raise ValueError('A known question and its actual answer are required')
            state['questions'][args.question].update(status='answered', answer=args.answer)
        elif args.operation == 'resume':
            if not args.user_instruction:
                raise ValueError('Record the explicit user instruction before resuming')
            state.update(mode='active', continuations=0, resume_instruction=args.user_instruction)
            marker.unlink(missing_ok=True)
        save(path, state)
        result = evaluate(state)
        print(json.dumps(result, ensure_ascii=True))
        if args.operation == 'check':
            return 0 if task['receipts'][check['id']]['exit_code'] == 0 and before == after else 2
    return 0


if __name__ == '__main__':
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
