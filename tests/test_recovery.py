"""Real CLI recovery exercises. Every command runs with a disposable HOME."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class RecoveryCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'source'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'python3').symlink_to(sys.executable)
        self.trace = self.root / 'trace'
        stub = f'#!{sys.executable}\n' + '''
import json, os, sys
from pathlib import Path
with open(os.environ['TRACE'], 'a') as f:
    f.write(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]) + '\\n')
if sys.argv[1:] == ['version']: print('test')
if sys.argv[1:] == ['--user', 'list-unit-files', '--state=enabled', '--no-legend']:
    print('demo.service enabled')
'''
        for name in ('omarchy', 'hyprctl', 'systemctl', 'dconf', 'pacman', 'hostname'):
            p = self.bin / name
            p.write_text(stub)
            p.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH=f'{self.bin}:/usr/bin:/bin',
                        TRACE=str(self.trace), XDG_RUNTIME_DIR=str(self.root / 'runtime'),
                        HYPRLAND_INSTANCE_SIGNATURE='isolated', NO_COLOR='1')
        self.archive = self.root / 'backup.tar.zst'

    def cli(self, *args, ok=True):
        result = subprocess.run([str(REPO / 'imprint'), *map(str, args)], env=self.env,
                                text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def put(self, rel, text, mode=0o640):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return path

    def unpack(self):
        import tarfile
        dest = self.root / 'unpacked'
        with tarfile.open(self.archive) as tar:
            tar.extractall(dest, filter='data')
        return dest

    def test_explicit_helpers_travel_with_reference_provenance(self):
        helper = '.local/share/demo/watch.py'
        self.put(helper, '#!/usr/bin/python3\nprint("demo")\n', 0o750)
        self.put('.config/systemd/user/demo.service.d/override.conf',
                 f'[Service]\nExecStart=\nExecStart=/usr/bin/python3 %h/{helper}\n')
        self.put('.hermes/cron/jobs.json', 'private runtime must not travel')
        self.cli('save', '--only', 'services,scripts', '--include-file', helper,
                 '-o', self.archive)
        root = self.unpack()
        meta = json.loads((root / 'categories/scripts/meta.json').read_text())
        self.assertEqual(meta['includedHelpers'][0]['path'], helper)
        self.assertIn('categories/services/files/.config/systemd/user/demo.service.d/override.conf',
                      meta['includedHelpers'][0]['referencedBy'])
        self.assertEqual((root / 'categories/scripts/files' / helper).read_bytes(),
                         (self.home / helper).read_bytes())
        self.assertFalse(any(p.name == 'jobs.json' for p in root.rglob('*')))
        self.cli('save', '--only', 'scripts', '--include-file', '.hermes/cron/jobs.json',
                 '-o', self.root / 'forbidden.tar.zst', ok=False)

    def test_selective_file_restore_rewrites_modes_and_undo(self):
        self.put('bin/demo', f'#!/bin/sh\n# {self.home}/data\nprintf demo\n', 0o750)
        self.put('bin/leave', '#!/bin/sh\nprintf leave\n')
        self.cli('save', '--only', 'scripts', '-o', self.archive)
        target = self.root / 'target'
        (target / 'bin').mkdir(parents=True)
        (target / 'bin/demo').write_text('original\n')
        (target / 'bin/demo').chmod(0o600)
        args = [self.archive, '--only', 'scripts', '--files-only', '--target-home', target,
                '--file', 'bin/demo']
        preview = json.loads(self.cli('preview', *args, '--json').stdout)
        self.assertEqual(preview['files'][0]['destination'], str(target / 'bin/demo'))
        self.assertIn('original', preview['files'][0]['diff'])
        self.trace.write_text('')
        result = json.loads(self.cli('restore', *args).stdout)
        self.assertEqual((target / 'bin/demo').read_text(),
                         f'#!/bin/sh\n# {target}/data\nprintf demo\n')
        self.assertEqual((target / 'bin/demo').stat().st_mode & 0o777, 0o750)
        self.assertFalse((target / 'bin/leave').exists())
        self.assertEqual(self.trace.read_text(), '')
        self.cli('undo', '--target-home', target, '--undo-dir', result['undo'])
        self.assertEqual((target / 'bin/demo').read_text(), 'original\n')
        self.assertEqual((target / 'bin/demo').stat().st_mode & 0o777, 0o600)

    def test_unwritable_later_destination_does_not_abort_rollback(self):
        self.assertNotEqual(os.geteuid(), 0, 'permission regression must run unprivileged')
        for rel in ('bin/a', '.local/bin/z'):
            self.put(rel, '#!/bin/sh\nprintf replacement\n')
        self.cli('save', '--only', 'scripts', '-o', self.archive)
        target = self.root / 'target'
        # scripts iteration visits .local/bin/z before bin/a.
        for rel in ('.local/bin/z', 'bin/a'):
            path = target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('original\n')
            path.chmod(0o600)
        blocked = target / 'bin'
        blocked.chmod(0o500)
        try:
            result = self.cli('restore', self.archive, '--files-only', '--only', 'scripts',
                              '--target-home', target, ok=False)
            self.assertEqual((target / '.local/bin/z').read_text(), 'original\n')
            report = json.loads(result.stdout)
            self.assertEqual(report['rollback'], 'restored original files')
            # A later explicit undo must also continue if one original cannot be restored.
            (target / '.local/bin/z').write_text('changed again\n')
            (blocked / 'a').write_text('changed again\n')
            result = self.cli('undo', '--target-home', target, '--undo-dir', report['undo'], ok=False)
            self.assertEqual((target / '.local/bin/z').read_text(), 'original\n')
            self.assertIn('bin/a', result.stderr)
            self.assertIn('partial rollback', result.stderr)
            self.assertEqual((blocked / 'a').read_text(), 'changed again\n')
        finally:
            blocked.chmod(0o700)

    def test_plan_refuses_baseline_archive_before_output_side_effects(self):
        legacy = self.root / 'legacy-engine.py'
        legacy.write_bytes(subprocess.run(
            ['git', '-C', str(REPO), 'show', 'c91c833:imprint-engine.py'],
            check=True, capture_output=True).stdout)
        self.put('bin/demo', '#!/bin/sh\nprintf legacy\n')
        saved = subprocess.run([sys.executable, str(legacy), 'save', '--only', 'scripts',
                                '-o', str(self.archive)], env=self.env, text=True, capture_output=True)
        self.assertEqual(saved.returncode, 0, saved.stdout + saved.stderr)
        unpacked = self.unpack()
        self.assertNotIn('integrity', json.loads((unpacked / 'manifest.json').read_text()))
        output = self.root / 'plan'
        output.mkdir()
        sentinel = output / 'payload/keep'
        sentinel.parent.mkdir()
        sentinel.write_text('existing plan payload')
        result = self.cli('plan', self.archive, '--only', 'scripts', '-o', output, ok=False)
        self.assertIn('make a new save', result.stderr)
        self.assertEqual(sentinel.read_text(), 'existing plan payload')
        self.assertFalse((output / 'restore.sh').exists())
        self.assertFalse((output / 'plan.json').exists())

    def test_failed_write_rolls_back_prior_files(self):
        import resource
        import signal
        self.put('bin/a', '#!/bin/sh\nprintf changed\n')
        self.put('bin/z', '#!/bin/sh\n#' + 'x' * 20000 + '\n')
        self.cli('save', '--only', 'scripts', '-o', self.archive)
        unpacked = self.unpack()
        target = self.root / 'target'
        (target / 'bin').mkdir(parents=True)
        for name in ('a', 'z'):
            (target / 'bin' / name).write_text('original\n')
        def limit():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (8192, 8192))
        result = subprocess.run([str(REPO / 'imprint'), 'restore', str(unpacked),
                                 '--files-only', '--only', 'scripts', '--target-home', str(target)],
                                env=self.env, text=True, capture_output=True, preexec_fn=limit)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / 'bin/a').read_text(), 'original\n', result.stderr)
        self.assertEqual((target / 'bin/z').read_text(), 'original\n')
        self.assertFalse(list(target.rglob('.imprint-*')))

    def test_system_files_recover_to_explicit_root_with_modes_and_undo(self):
        source = self.root / 'source-root'
        unit = source / 'etc/systemd/system/demo.service.d/override.conf'
        unit.parent.mkdir(parents=True)
        unit.write_text('[Service]\nRestart=on-failure\n')
        unit.chmod(0o600)
        # This fixture's policy is unowned by a package.
        (self.bin / 'pacman').write_text('#!/bin/sh\nexit 1\n')
        self.cli('save', '--only', 'system', '--system-root', source, '-o', self.archive)
        archived = self.unpack()
        meta = json.loads((archived / 'categories/system/meta.json').read_text())
        self.assertEqual(meta['sourceRoot'], str(source))
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertIn(['systemctl', f'--root={source}', 'list-unit-files', '--state=enabled', '--no-legend'], calls)
        target = self.root / 'target-root'
        target.mkdir()
        args = [self.archive, '--only', 'system', '--files-only', '--system-root', target,
                '--file', 'etc/systemd/system/demo.service.d/override.conf']
        self.cli('restore', *args, ok=False)
        self.assertFalse((target / 'etc').exists())
        preview = json.loads(self.cli('preview', *args, '--json').stdout)
        self.assertEqual(preview['files'][0]['destination'],
                         str(target / 'etc/systemd/system/demo.service.d/override.conf'))
        result = json.loads(self.cli('restore', *args, '--allow-system').stdout)
        dest = target / 'etc/systemd/system/demo.service.d/override.conf'
        self.assertEqual(dest.read_bytes(), unit.read_bytes())
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.cli('undo', '--undo-dir', result['undo'], '--system-root', target, '--allow-system')
        self.assertFalse(dest.exists())

    def test_invalid_configuration_is_rejected_before_any_write(self):
        self.put('bin/a', '#!/bin/sh\nprintf new\n')
        self.put('bin/bad.py', '#!/usr/bin/python3\ndef broken(:\n')
        self.cli('save', '--only', 'scripts', '-o', self.archive)
        target = self.root / 'target'
        (target / 'bin').mkdir(parents=True)
        (target / 'bin/a').write_text('original\n')
        self.cli('restore', self.archive, '--only', 'scripts', '--files-only',
                 '--target-home', target, ok=False)
        self.assertEqual((target / 'bin/a').read_text(), 'original\n')
        self.assertFalse((target / 'bin/bad.py').exists())

    def test_shell_restore_merges_only_requested_field_into_live_snapshot(self):
        archived = {'bar': {'layout': {'left': ['old']}}, 'idle': {'enabled': False},
                    'other': 'archived'}
        shell = self.put('.config/omarchy/shell.json', json.dumps(archived))
        self.cli('save', '--only', 'bar', '-o', self.archive)
        live = {'bar': {'layout': {'left': ['new', 'keep']}}, 'idle': {'enabled': True},
                'other': 'latest'}
        shell.write_text(json.dumps(live))
        self.env['LIVE_SHELL'] = str(shell)
        stub = self.bin / 'omarchy'
        stub.write_text(f'#!{sys.executable}\n' + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
live = Path(os.environ['LIVE_SHELL'])
if args[:3] == ['shell', 'config-edit', 'snapshot']:
    Path(args[3]).write_bytes(live.read_bytes())
elif args[:3] == ['shell', 'config-edit', 'apply']:
    assert json.loads(Path(args[3]).read_text()) == json.loads(live.read_text())
    live.write_bytes(Path(args[4]).read_bytes())
with open(os.environ['TRACE'], 'a') as f: f.write(json.dumps(args) + '\\n')
''')
        self.cli('restore', self.archive, '--only', 'bar', '--shell-key', '/idle/enabled')
        expected = dict(live, idle={'enabled': False})
        self.assertEqual(json.loads(shell.read_text()), expected)
        self.assertNotIn('--allow-layout-change', self.trace.read_text())

    def test_service_enablement_does_not_start_or_restart_processes(self):
        self.put('.config/systemd/user/demo.service',
                 '[Unit]\nDescription=Demo\n[Service]\nExecStart=/bin/true\n'
                 '[Install]\nWantedBy=default.target\n')
        self.cli('save', '--only', 'services', '-o', self.archive)
        self.trace.write_text('')
        result = self.cli('restore', self.archive, '--only', 'services')
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertIn(['systemctl', '--user', 'daemon-reload'], calls)
        self.assertIn(['systemctl', '--user', 'enable', 'demo.service'], calls)
        self.assertFalse(any('--now' in call or 'restart' in call for call in calls))
        self.assertNotIn(['hyprctl', 'reload'], calls)
        self.assertIn('restart-required', result.stdout)

    def test_generated_plan_never_copies_whole_shell_or_bypasses_recovery(self):
        self.put('.config/omarchy/shell.json', '{"bar":{"layout":{"left":["old"]}}}')
        self.put('.config/omarchy/extra.json', '{"setting":true}')
        self.cli('save', '--only', 'bar', '-o', self.archive)
        out = self.root / 'plan'
        out.mkdir()
        self.cli('plan', self.archive, '--only', 'bar', '-o', out)
        script = (out / 'restore.sh').read_text()
        self.assertNotIn('cp -a', script)
        self.assertIn('--files-only', script)
        self.assertNotIn('omarchy restart shell', script)

    def test_default_undo_removes_new_files_without_copying_its_journal(self):
        self.put('bin/demo', '#!/bin/sh\nprintf demo\n')
        self.cli('save', '--only', 'scripts', '-o', self.archive)
        target = self.root / 'target'
        target.mkdir()
        self.cli('restore', self.archive, '--only', 'scripts', '--files-only', '--target-home', target)
        self.cli('undo', '--target-home', target)
        self.assertFalse((target / 'bin/demo').exists())
        self.assertFalse((target / 'recovery.json').exists())
        self.assertFalse((self.home / 'recovery.json').exists())

    def test_legacy_undo_refuses_stale_shell_snapshot(self):
        shell = self.put('.config/omarchy/shell.json', '{"latest":true}')
        undo = self.root / 'legacy-undo'
        old = undo / '.config/omarchy/shell.json'
        old.parent.mkdir(parents=True)
        old.write_text('{"stale":true}')
        self.cli('undo', '--undo-dir', undo, ok=False)
        self.assertEqual(shell.read_text(), '{"latest":true}')

    def test_explicit_helper_rejects_embedded_credentials(self):
        self.put('.hermes/scripts/test.py',
                 '#!/usr/bin/python3\napi_key = "synthetic-negative-fixture"\n')
        self.cli('save', '--only', 'scripts', '--include-file', '.hermes/scripts/test.py',
                 '-o', self.archive, ok=False)
        self.assertFalse(self.archive.exists())

    def test_archive_is_private_even_with_permissive_umask(self):
        self.put('bin/demo', '#!/bin/sh\nprintf demo\n')
        result = subprocess.run([str(REPO / 'imprint'), 'save', '--only', 'scripts',
                                 '-o', str(self.archive)], env=self.env, text=True,
                                capture_output=True, umask=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)

    def test_restore_rejects_unknown_schema_before_modifying_target(self):
        self.put('bin/demo', '#!/bin/sh\nprintf demo\n')
        self.cli('save', '--only', 'scripts', '-o', self.archive)
        root = self.unpack()
        path = root / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['schema'] = 999
        path.write_text(json.dumps(manifest))
        target = self.root / 'target'
        target.mkdir()
        self.cli('restore', root, '--only', 'scripts', '--files-only', '--target-home', target, ok=False)
        self.assertEqual(list(target.iterdir()), [])

    def test_json_and_shell_syntax_are_validated_without_execution(self):
        for rel, body, category in [
            ('.config/omarchy/extra.json', '{broken', 'bar'),
            ('bin/demo.sh', '#!/bin/sh\nif then\n', 'scripts'),
        ]:
            with self.subTest(rel=rel):
                self.put(rel, body)
                self.cli('save', '--only', category, '-o', self.archive)
                self.cli('restore', self.archive, '--only', category, '--files-only',
                         '--file', rel, ok=False)

    def test_file_recovery_refuses_unsafe_targets_and_empty_recipe(self):
        self.put('bin/demo', '#!/bin/sh\nprintf demo\n')
        self.cli('save', '--only', 'scripts', '-o', self.archive)
        target = self.root / 'target'
        target.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (target / 'bin').symlink_to(outside, target_is_directory=True)
        self.cli('restore', self.archive, '--only', 'scripts', '--files-only', '--target-home', target, ok=False)
        self.assertEqual(list(outside.iterdir()), [])
        self.cli('restore', self.archive, '--only', 'packages', '--files-only', '--target-home', target, ok=False)

    def test_failed_daemon_reload_blocks_enablement_and_reports_failure(self):
        self.put('.config/systemd/user/demo.service', '[Service]\nExecStart=/bin/true\n')
        self.cli('save', '--only', 'services', '-o', self.archive)
        path = self.bin / 'systemctl'
        path.write_text('#!/bin/sh\nexit 2\n')
        result = self.cli('restore', self.archive, '--only', 'services', ok=False)
        self.assertIn('daemon-reload failed', result.stdout)

    def test_legacy_system_rehearsal_refuses_symlink_escape(self):
        source = self.root / 'source-root'
        policy = source / 'etc/systemd/logind.conf.d/local.conf'
        policy.parent.mkdir(parents=True)
        policy.write_text('[Login]\nHandleLidSwitch=ignore\n')
        (self.bin / 'pacman').write_text('#!/bin/sh\nexit 1\n')
        self.cli('save', '--only', 'system', '--system-root', source, '-o', self.archive)
        target = self.root / 'target-root'
        target.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (target / 'etc').symlink_to(outside, target_is_directory=True)
        self.cli('restore', self.archive, '--only', 'system', '--system-root', target,
                 '--allow-system', ok=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_archive_integrity_checks_payload_and_records_modes(self):
        self.put('bin/demo', '#!/bin/sh\nprintf demo\\n\n', 0o750)
        self.cli('save', '--only', 'scripts', '-o', self.archive)
        self.cli('verify', self.archive)
        root = self.unpack()
        manifest = json.loads((root / 'manifest.json').read_text())
        rel = 'categories/scripts/files/bin/demo'
        record = manifest['integrity'][rel]
        self.assertEqual(record['mode'], 0o750)
        self.assertEqual(record['sha256'], hashlib.sha256((root / rel).read_bytes()).hexdigest())
        (root / rel).write_text('tampered')
        self.cli('verify', root, ok=False)
        self.cli('restore', root, '--only', 'scripts', '--dry-run', ok=False)
