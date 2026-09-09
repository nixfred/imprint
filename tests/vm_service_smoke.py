#!/usr/bin/env python3
"""Opt-in real systemd integration, ONLY on the disposable imprint-lab guest."""
import json
import os
from pathlib import Path
import socket
import subprocess
import uuid

from test_recovery import RecoveryCLI


def main():
    if os.environ.get('IMPRINT_DEDICATED_VM') != '1' or socket.gethostname() != 'imprint-lab':
        raise SystemExit('refused: this smoke test requires the dedicated imprint-lab VM')
    case = RecoveryCLI()
    case.setUp()
    name = 'imprint-proof-' + uuid.uuid4().hex[:10]
    unit = name + '.service'
    helper = f'.local/share/{name}/proof.py'
    unit_rel = f'.config/systemd/user/{unit}'
    dropin = unit_rel + '.d/60-recovery.conf'
    live_home = Path.home()
    undo = None
    calls = []
    def command(*args):
        result = subprocess.run(args, text=True, capture_output=True)
        calls.append({'command': list(args), 'rc': result.returncode,
                      'stdout': result.stdout.strip(), 'stderr': result.stderr.strip()})
        if result.returncode:
            raise RuntimeError(calls[-1])
        return result.stdout.strip()
    try:
        case.put(helper, '#!/usr/bin/python3\nprint("IMPRINT_PROOF_READY", flush=True)\n', 0o750)
        case.put(unit_rel, '[Unit]\nDescription=Disposable Imprint recovery proof\n'
                 f'[Service]\nType=oneshot\nExecStart=/usr/bin/python3 {case.home}/{helper}\n'
                 'RemainAfterExit=yes\n[Install]\nWantedBy=default.target\n', 0o640)
        case.put(dropin, '[Service]\nTimeoutStartSec=10\n', 0o600)
        case.cli('save', '--only', 'services,scripts', '--include-file', helper,
                 '-o', case.archive)
        case.cli('verify', case.archive)
        result = json.loads(case.cli('restore', case.archive, '--only', 'services,scripts',
                                     '--files-only', '--target-home', live_home).stdout)
        undo = result['undo']
        assert (live_home / helper).stat().st_mode & 0o777 == 0o750
        assert (live_home / dropin).stat().st_mode & 0o777 == 0o600
        command('systemd-analyze', '--user', 'verify', str(live_home / unit_rel))
        command('systemctl', '--user', 'daemon-reload')
        before = command('systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--value')
        assert before == 'inactive', before
        command('systemctl', '--user', 'enable', unit)
        assert command('systemctl', '--user', 'is-enabled', unit) == 'enabled'
        assert command('systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--value') == 'inactive'
        command('systemctl', '--user', 'start', unit)
        assert command('systemctl', '--user', 'is-active', unit) == 'active'
        output = command('journalctl', '--user', '-u', unit, '--no-pager', '-o', 'cat')
        assert 'IMPRINT_PROOF_READY' in output
    finally:
        subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True)
        subprocess.run(['systemctl', '--user', 'disable', unit], capture_output=True)
        if undo:
            case.cli('undo', '--undo-dir', undo, '--target-home', live_home)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True)
        case.doCleanups()
    assert not (live_home / unit_rel).exists()
    assert not (live_home / helper).exists()
    print(json.dumps({'ok': True, 'realSystemd': True, 'commands': calls,
                      'cleanup': 'unit stopped/disabled; payload undone; manager reloaded'}, indent=2))


if __name__ == '__main__':
    main()
