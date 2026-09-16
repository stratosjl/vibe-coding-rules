import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('continuation', Path(__file__).with_name('continuation.py'))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'codex-home')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.plan = {'version': 1, 'mode': 'active', 'cwd': str(self.root), 'objective': 'Finish both files',
                     'tasks': [{'id': 'a', 'action': 'Write a.txt', 'artifacts': [{'path': 'a.txt'}]},
                               {'id': 'b', 'action': 'Write b.txt', 'artifacts': [{'path': 'b.txt'}]}]}
        self.path = gate.state_path('test-session')
        self.path.parent.mkdir(parents=True)
        gate.save(self.path, self.plan)

    def event(self, name='Stop', **extra):
        return gate.handle({'session_id': 'test-session', 'cwd': str(self.root),
                            'hook_event_name': name, 'turn_id': 'turn-1', **extra})

    def test_a_done_b_pending_requires_continuation(self):
        (self.root / 'a.txt').write_text('done')
        out = self.event()
        self.assertEqual(out['decision'], 'block')
        self.assertIn('b: Write b.txt', out['reason'])
        self.assertNotIn('a: Write a.txt', out['reason'])

    def test_side_question_preserves_objective(self):
        self.event('UserPromptSubmit', prompt='Why did this take so long?')
        self.assertEqual(json.loads(self.path.read_text())['objective'], self.plan['objective'])
        self.assertEqual(self.event()['decision'], 'block')

    def test_waiting_task_does_not_block_independent_work(self):
        self.plan['questions'] = {'choice': {'text': 'Which date should the report use?', 'status': 'pending'}}
        self.plan['tasks'][0]['wait_for'] = ['choice']
        gate.save(self.path, self.plan)
        self.assertEqual(gate.evaluate(self.plan)['ready'], ['b'])
        (self.root / 'b.txt').write_text('done')
        out = self.event()
        self.assertNotIn('decision', out)
        self.assertIn('incomplete', out['systemMessage'])

    def test_all_external_blockers_remain_incomplete_without_retry(self):
        for task in self.plan['tasks']:
            task['external_block'] = 'The required service is unavailable'
        gate.save(self.path, self.plan)
        self.assertEqual(gate.evaluate(self.plan)['status'], 'waiting')
        self.assertNotIn('decision', self.event())

    def test_stop_takes_precedence(self):
        self.event('UserPromptSubmit', prompt='stop everything else. reply to the question')
        self.assertFalse(self.event()['continue'])
        self.event('UserPromptSubmit', prompt='Why did that happen?')
        self.assertFalse(self.event()['continue'])

    def test_stop_grammar_does_not_parse_quoted_or_negative_text(self):
        for text in ['Do not stop.', 'The archive says "stop everything".', 'Explain the Stop hook.', 'stop using that library']:
            self.assertFalse(gate.explicit_stop(text), text)
        for text in ['stop', 'Stop everything else. Explain.', 'cancel the task', 'close session',
                     'do not continue', '\u03a3\u03c4\u03b1\u03bc\u03ac\u03c4\u03b1 \u03cc\u03bb\u03b1.']:
            self.assertTrue(gate.explicit_stop(text), text)

    def test_interrupt_preserves_pending_state(self):
        self.event('Interrupt')
        self.assertFalse(self.event()['continue'])
        state = json.loads(self.path.read_text())
        self.assertEqual(state['mode'], 'interrupted')
        self.assertEqual(len(state['tasks']), 2)
        self.assertNotIn('processes_terminated', state)

    def test_interrupt_is_recorded_while_check_holds_lock(self):
        with gate.locked(self.path):
            self.event('Interrupt')
        self.assertFalse(self.event()['continue'])

    def test_real_successful_check_allows_completion(self):
        for name in ('a.txt', 'b.txt'):
            (self.root / name).write_text('done')
        self.plan['tasks'][1]['checks'] = [{'id': 'quality', 'argv': [sys.executable, '-c', 'pass']}]
        gate.save(self.path, self.plan)
        result = subprocess.run([sys.executable, str(Path(gate.__file__)), 'check', '--session', 'test-session',
                                 '--task', 'b', '--check', 'quality'], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.event(), {})

    def test_failed_required_check_prevents_completion(self):
        for name in ('a.txt', 'b.txt'):
            (self.root / name).write_text('done')
        self.plan['tasks'][1]['checks'] = [{'id': 'quality', 'argv': [sys.executable, '-c', 'raise SystemExit(1)']}]
        gate.save(self.path, self.plan)
        result = subprocess.run([sys.executable, str(Path(gate.__file__)), 'check', '--session', 'test-session',
                                 '--task', 'b', '--check', 'quality'], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.event()['decision'], 'block')

    def test_check_becomes_stale_after_artifact_change(self):
        for name in ('a.txt', 'b.txt'):
            (self.root / name).write_text('done')
        task = self.plan['tasks'][1]
        check = {'id': 'quality', 'argv': [sys.executable, '-c', 'pass']}
        task['checks'] = [check]
        task['receipts'] = {'quality': {'exit_code': 0, 'snapshot': gate.snapshot(self.plan, task, check)}}
        self.assertEqual(gate.evaluate(self.plan)['status'], 'complete')
        (self.root / 'b.txt').write_text('later change')
        self.assertEqual(gate.evaluate(self.plan)['status'], 'continue')

    def test_compaction_and_resume_recover_contract(self):
        out = self.event('PostCompact')
        self.assertIn('Finish both files', out['systemMessage'])
        self.assertNotIn('hookSpecificOutput', out)
        out = self.event('SessionStart')
        self.assertIn('Finish both files', out['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.event()['decision'], 'block')

    def test_done_flag_cannot_replace_artifact(self):
        self.plan['tasks'][1]['done'] = True
        self.plan['tasks'][1]['status'] = 'complete'
        self.assertNotIn('b', gate.evaluate(self.plan)['done'])

    def test_changed_acceptance_constraint_invalidates_completion(self):
        for name in ('a.txt', 'b.txt'):
            (self.root / name).write_text('done')
        self.assertEqual(gate.evaluate(self.plan)['status'], 'complete')
        self.plan['tasks'][1]['artifacts'][0]['sha256'] = hashlib.sha256(b'new content').hexdigest()
        self.assertEqual(gate.evaluate(self.plan)['status'], 'continue')

    def test_continuations_are_bounded_even_on_reentry(self):
        for _ in range(3):
            reason = self.event(stop_hook_active=True)['reason']
            self.event('UserPromptSubmit', prompt=reason)
        out = self.event(stop_hook_active=True)
        self.assertFalse(out['continue'])
        self.assertIn('incomplete', out['stopReason'])

    def test_missing_contract_does_not_claim_coverage(self):
        self.path.unlink()
        self.assertEqual(self.event(), {})

    def test_other_workspace_cannot_start_continuation(self):
        other = self.root / 'other'
        other.mkdir()
        out = gate.handle({'session_id': 'test-session', 'cwd': str(other), 'hook_event_name': 'Stop'})
        self.assertNotIn('decision', out)
        gate.handle({'session_id': 'test-session', 'cwd': str(other), 'hook_event_name': 'Interrupt'})
        self.assertFalse(self.path.with_suffix('.halt.json').exists())

    def test_dependency_cycle_and_path_escape_rejected(self):
        bad = copy.deepcopy(self.plan)
        bad['tasks'][0]['depends_on'] = ['b']
        bad['tasks'][1]['depends_on'] = ['a']
        with self.assertRaises(ValueError):
            gate.validate(bad)

    def test_empty_artifact_requirement_is_rejected(self):
        self.plan['tasks'][0]['artifacts'][0]['min_bytes'] = 0
        with self.assertRaises(ValueError):
            gate.validate(self.plan)
        bad = copy.deepcopy(self.plan)
        bad['tasks'][0]['artifacts'][0]['path'] = '../outside.txt'
        with self.assertRaises(ValueError):
            gate.validate(bad)

    def test_negative_control_detects_removed_gate(self):
        (self.root / 'a.txt').write_text('done')
        with patch.object(gate, 'handle', return_value={}):
            with self.assertRaises(AssertionError):
                self.assertEqual(self.event().get('decision'), 'block')

    def test_installer_preserves_existing_hooks_and_is_idempotent(self):
        from install_continuation import install, EVENTS, TAG
        home = self.root/'install-home'
        home.mkdir()
        existing = {'hooks': {'PreToolUse': [{'matcher': 'apply_patch', 'hooks': [
            {'type': 'command', 'command': 'existing-writer-check'}]}]}}
        (home/'hooks.json').write_text(json.dumps(existing))
        first = install(home)
        second = install(home)
        observed = json.loads((home/'hooks.json').read_text())
        self.assertEqual(observed['hooks']['PreToolUse'], existing['hooks']['PreToolUse'])
        for event in EVENTS:
            self.assertEqual(sum(h.get('statusMessage') == TAG for g in observed['hooks'][event] for h in g['hooks']), 1)
        self.assertEqual(first['runtime_sha256'], second['runtime_sha256'])
        self.assertFalse((home/'config.toml').exists())

    def test_check_cannot_run_after_interrupt(self):
        self.plan['tasks'][1]['checks'] = [{'id': 'quality', 'argv': [sys.executable, '-c', 'pass']}]
        gate.save(self.path, self.plan)
        self.event('Interrupt')
        result = subprocess.run([sys.executable, str(Path(gate.__file__)), 'check', '--session', 'test-session',
                                 '--task', 'b', '--check', 'quality'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_revise_preserves_objective_and_rejects_silent_removal(self):
        plan = copy.deepcopy(self.plan)
        plan['tasks'] = plan['tasks'][:1]
        draft = self.root/'revised.json'
        draft.write_text(json.dumps(plan))
        result = subprocess.run([sys.executable, str(Path(gate.__file__)), 'revise', '--session', 'test-session',
                                 '--plan', str(draft)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(json.loads(self.path.read_text())['tasks']), 2)


if __name__ == '__main__':
    unittest.main()
