from conftest import REPO, run

def test_versions_env_is_plain_key_value():
    for line in (REPO / "versions.env").read_text().splitlines():
        key, sep, value = line.partition("=")
        assert sep and key.replace("_", "").isalnum() and key == key.upper()
        assert value and " " not in value and '"' not in value

def test_lint_fails_when_shellcheck_fails():
    bad = REPO / "zz_lint_regression.sh"
    bad.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho $unquoted_var_x $(ls)\n")
    try:
        r = run(["make", "lint"])
        assert r.returncode != 0
    finally:
        bad.unlink()
    assert run(["make", "lint"]).returncode == 0


def test_versions_env_rke2_version_is_valid():
    env = dict(l.split("=", 1) for l in (REPO / "versions.env").read_text().split())
    assert run([str(REPO / "node/scripts/rke2-version.sh"), "rpm", env["RKE2_VERSION"]]).returncode == 0
