#!/usr/bin/env python3
"""Portable legacy suite: deterministic discovery commands, real filesystem/Git/systemd.

The legacy unit suite assumes an installed Omarchy desktop and at least one
Omarchy package. These command fixtures supply only that discovery surface on
an Arch test VM. They do NOT constitute a real desktop/activation test.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    with tempfile.TemporaryDirectory(prefix='imprint-suite-') as tmp:
        directory = Path(tmp)
        script = f'#!{sys.executable}\n' + '''
import sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
if name == 'hostname': print('imprint-test')
elif name == 'omarchy' and args == ['version']: print('test-fixture')
elif name == 'omarchy' and args == ['plugin', 'list', '--json']: print('[]')
elif name == 'pacman' and args in (['-Qqe'], ['-Qq']): print('omarchy-test\\npython')
'''
        for name in ('omarchy', 'omarchy-shell', 'hyprctl', 'dconf', 'hostname', 'pacman'):
            path = directory / name
            path.write_text(script)
            path.chmod(0o755)
        os.environ['PATH'] = str(directory) + os.pathsep + os.environ['PATH']
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent))
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
