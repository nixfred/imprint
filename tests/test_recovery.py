"""Real CLI recovery exercises. Every command runs with a disposable HOME."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
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
        (self.bin / 'python3').symlink_to('/usr/bin/python3')
        self.trace = self.root / 'trace'
        stub = '''#!/usr/bin/python3
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
