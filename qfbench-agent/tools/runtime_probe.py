"""Assert the applied Linux restrictions inside the actual agent image."""
import json
import os
from pathlib import Path
import resource
import subprocess


def main():
    assert os.getuid() == os.getgid() == 65534
    status = Path('/proc/self/status').read_text()
    assert 'NoNewPrivs:\t1' in status
    assert 'CapEff:\t0000000000000000' in status
    for path in ('/app/forbidden-probe', '/input/forbidden-probe'):
        try:
            Path(path).write_text('must fail')
        except OSError:
            pass
        else:
            raise AssertionError(f'Unexpected write access: {path}')
    limits = {}
    for name, key, expected in [('nofile', resource.RLIMIT_NOFILE, 1024),
                                ('nproc', resource.RLIMIT_NPROC, 256),
                                ('fsize', resource.RLIMIT_FSIZE, 67108864)]:
        limits[name] = resource.getrlimit(key)
        assert limits[name] == (expected, expected), limits
    mounts = [line.split() for line in Path('/proc/mounts').read_text().splitlines()]
    tmp = next(row for row in mounts if row[1] == '/tmp')
    assert tmp[2] == 'tmpfs'
    assert {'rw', 'noexec', 'nosuid', 'nodev'} <= set(tmp[3].split(','))
    assert os.statvfs('/tmp').f_blocks * os.statvfs('/tmp').f_frsize == 67108864
    script = Path('/tmp/probe.sh')
    script.write_text('#!/bin/sh\nexit 0\n')
    script.chmod(0o755)
    try:
        subprocess.run([str(script)], check=True)
    except PermissionError:
        pass
    else:
        raise AssertionError('/tmp execution unexpectedly allowed')
    finally:
        script.unlink()
    target = Path('/app/output/probe.txt')
    target.write_text('dual mount check')
    assert Path('/output/probe.txt').read_text() == 'dual mount check'
    target.unlink()
    cgroup = Path('/sys/fs/cgroup')
    values = {name: (cgroup / name).read_text().strip() for name in
              ('pids.max', 'memory.max', 'memory.swap.max', 'cpu.max')}
    assert values['pids.max'] == '256'
    assert values['memory.swap.max'] == '0'
    assert int(values['memory.max']) == 128 * 1024 ** 3
    quota, period = map(int, values['cpu.max'].split())
    assert quota / period == 16
    print(json.dumps({'passed': True, 'uid': os.getuid(), 'gid': os.getgid(),
                      'limits': limits, 'tmp_mount': tmp, 'cgroup': values,
                      'root_and_input_write_denied': True, 'tmp_execution_denied': True,
                      'dual_output_mount_verified': True}))


if __name__ == '__main__':
    main()
