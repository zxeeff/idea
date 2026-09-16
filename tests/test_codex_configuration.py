from __future__ import annotations

import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from idea.domain import AgentProfile, Effort, Provider
from idea.providers import build_invocation


class CodexConfigurationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve() / 'repo.v1 "한글😀"' / '.idea-swarm' / 'peer'
        self.workspace.mkdir(parents=True)
        self.bridge = self.workspace / '.idea-peer'
        self.bridge.mkdir()

    def invocation(self, session_id=None):
        return build_invocation(
            profile=AgentProfile('test-peer', Provider.OPENAI, 'gpt-5.6-luna', Effort.LOW),
            system_prompt='Configuration check only.', task_prompt='Configuration check only.',
            workspace=self.workspace, state_dir=self.bridge, bridge_dir=self.bridge,
            run_id='test-run', agent={'id': 'test-peer', 'name': 'test-peer'},
            resume_session_id=session_id,
        )

    @staticmethod
    def configuration(invocation):
        return [invocation.argv[index + 1] for index, arg in enumerate(invocation.argv[:-1])
                if arg == '--config']

    def test_literal_workspace_path_is_a_toml_map_key_for_start_and_resume(self):
        for session in (None, '00000000-0000-4000-8000-000000000000'):
            with self.subTest(session=session):
                invocation = self.invocation(session)
                projects = next(value for value in self.configuration(invocation) if value.startswith('projects='))
                self.assertEqual(
                    {str(self.workspace): {'trust_level': 'untrusted'}},
                    tomllib.loads(projects)['projects'],
                )
                self.assertFalse(any(value.startswith('projects.') for value in self.configuration(invocation)))
                self.assertIn('--strict-config', invocation.argv)

    def test_start_and_resume_do_not_reintroduce_file_or_network_restrictions(self):
        for session in (None, '00000000-0000-4000-8000-000000000000'):
            with self.subTest(session=session):
                invocation = self.invocation(session)
                self.assertIn('--dangerously-bypass-approvals-and-sandbox', invocation.argv)
                self.assertNotIn('--sandbox', invocation.argv)
                for setting in self.configuration(invocation):
                    self.assertFalse(setting.startswith(('permissions.', 'default_permissions=', 'sandbox_')))
                self.assertIn('approval_policy="never"', self.configuration(invocation))

    @unittest.skipUnless(shutil.which('codex'), 'Codex CLI is not installed')
    def test_installed_cli_accepts_all_generated_settings_before_deliberate_parse_stop(self):
        # Strictly reject this unknown key before session lookup or model startup.
        # Unlike --help, this exercises the real config loader without an API call.
        sentinel = 'zz_idea_config_probe'
        for session in (None, '00000000-0000-4000-8000-000000000000'):
            with self.subTest(session=session):
                invocation = self.invocation(session)
                result = subprocess.run(
                    [*invocation.argv[:-1], '--config', f'{sentinel}=true', invocation.argv[-1]],
                    cwd=invocation.cwd, env=invocation.env, input='', text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn(f'unknown configuration field `{sentinel}`', result.stderr)
                self.assertNotIn('unknown configuration field `projects', result.stderr)
                self.assertEqual('', result.stdout.strip(), 'No session or model event should be emitted')


if __name__ == '__main__':
    unittest.main()
