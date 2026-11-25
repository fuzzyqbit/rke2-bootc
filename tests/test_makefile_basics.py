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


def test_ansible_tools_are_pinned_to_the_tested_versions():
    reqs = (REPO / "requirements-dev.txt").read_text().split()
    assert any(r.startswith("ansible-core==") for r in reqs)
    assert any(r.startswith("ansible-lint==") for r in reqs)


def test_lint_ignores_site_rootfs_and_output():
    # The owner's site files (vendor YAML, shell snippets) and build output are not ours to lint.
    made = []
    try:
        for d in (REPO / "site/dev/rootfs/etc/zz-lint-check", REPO / "output/zz-lint-check"):
            d.mkdir(parents=True, exist_ok=False); made.append(d)
            (d / "bad.yaml").write_text("a:  b\nkey: [1,2]\n")
            (d / "bad.sh").write_text("#!/usr/bin/env bash\necho $unquoted_var_x $(ls)\n")
        r = run(["make", "lint"])
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        for d in made:
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
        out = REPO / "output"
        if out.exists() and not any(out.iterdir()):
            out.rmdir()
