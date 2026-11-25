import os
import pathlib
import subprocess
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def run(args, env=None, cwd=REPO):
    return subprocess.run(args, env=env, cwd=cwd, text=True, capture_output=True, check=False)


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def fake_bin(tmp_path):
    """Factory: create fake executables that log their calls, then run a shell body."""
    counter = iter(range(1_000_000))

    def make(commands):
        d = tmp_path / f"bin{next(counter)}"
        d.mkdir()
        log = d / "calls.log"
        log.touch()
        for name, body in commands.items():
            script = d / name
            script.write_text(
                "#!/usr/bin/env bash\n"
                f'echo "{name} $*" >> "{log}"\n'
                f"{body or 'exit 0'}\n"
            )
            script.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
        return env, log

    return make
