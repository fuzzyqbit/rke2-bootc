# tests/test_node_survey.py
from conftest import REPO, run, calls, versions
S, D = str(REPO / "hack/node-survey.sh"), str(REPO / "hack/survey-diff.sh")
SECRET = "K10deadbeef::server:s3cr3t-join-token"

def node(tmp_path):
    root = tmp_path / "root"
    for d in ("etc/rancher/rke2/config.yaml.d", "etc/default", "etc/chrony.d", "opt/falcon", "etc/sysctl.d"):
        (root / d).mkdir(parents=True)
    (root / "etc/os-release").write_text('NAME="Rocky Linux"\nVERSION_ID="9.4"\nPRETTY_NAME="x"\n')
    (root / "etc/rancher/rke2/config.yaml").write_text(
        f"token: {SECRET}\nserver: https://10.0.0.5:9345\ncni: canal\nkubelet-arg:\n  - max-pods=200\n")
    (root / "etc/default/rke2-server").write_text("HTTPS_PROXY=http://user:hunter2@proxy:3128\n")
    (root / "etc/chrony.conf").write_text("server ntp1.corp.example iburst\ndriftfile /var/lib/chrony/drift\n")
    return root

def survey(fake_bin, tmp_path, commands=None):
    env, _ = fake_bin(commands if commands is not None else {
        "rpm": "printf 'bash\\nfalcon-sensor\\n'", "dnf": "echo falcon-sensor", "systemctl": "echo 'auditd.service enabled'",
        "rke2": "echo 'rke2 version v1.31.4+rke2r1 (abc)'", "lsmod": "printf 'Module Size\\nbr_netfilter 1\\n'",
        "getenforce": "echo Enforcing"})
    env["SURVEY_ROOT"] = str(node(tmp_path))
    return run([S], env=env)

def test_sections_in_order(fake_bin, tmp_path):
    out = survey(fake_bin, tmp_path).stdout
    names = [l[3:] for l in out.splitlines() if l.startswith("## ")]
    assert names == ["os-release", "rke2-version", "packages-all", "packages-userinstalled", "enabled-units",
                     "time-sync", "rke2-config-keys", "rke2-env-keys", "kernel-modules", "sysctl-files",
                     "ca-trust-anchors", "opt-contents", "selinux"]

def test_reports_what_the_image_must_match(fake_bin, tmp_path):
    out = survey(fake_bin, tmp_path).stdout
    for expected in ("v1.31.4+rke2r1", "falcon-sensor", "server ntp1.corp.example iburst", "token", "cni",
                     "kubelet-arg", "HTTPS_PROXY", "falcon", "Enforcing"):
        assert expected in out

def test_never_prints_config_values(fake_bin, tmp_path):
    out = survey(fake_bin, tmp_path).stdout
    for leaked in (SECRET, "s3cr3t", "10.0.0.5", "hunter2", "max-pods"):
        assert leaked not in out

def test_survives_missing_commands_and_files(fake_bin, tmp_path):
    env, _ = fake_bin({}); env["SURVEY_ROOT"] = str(tmp_path / "empty")
    r = run([S], env=env)
    assert r.returncode == 0 and r.stdout.count("## ") == 13

def test_survey_diff(fake_bin, tmp_path):
    report = tmp_path / "s.txt"; report.write_text(survey(fake_bin, tmp_path).stdout)
    base = tmp_path / "base.txt"; base.write_text("bash\n")
    assert run([D, str(report), str(base)]).stdout.split() == ["falcon-sensor"]

def test_survey_diff_rejects_empty_base(tmp_path):
    report = tmp_path / "s.txt"; report.write_text("## packages-all\nbash\n")
    base = tmp_path / "base.txt"; base.write_text("")
    r = run([D, str(report), str(base)])
    assert r.returncode != 0 and r.stdout == "" and "empty" in r.stderr

def test_make_survey_diff_fails_when_container_fails(fake_bin, tmp_path):
    report = tmp_path / "s.txt"; report.write_text("## packages-all\nbash\nfalcon-sensor\n")
    env, _ = fake_bin({"podman": "exit 125"}); env["CONTAINER_TOOL"] = "podman"
    r = run(["make", "survey-diff", f"SURVEY={report}"], env=env)
    assert r.returncode != 0 and "falcon-sensor" not in r.stdout and "error" in r.stderr

def test_config_keys_ignore_token_like_lines(fake_bin, tmp_path):
    env, _ = fake_bin({}); root = node(tmp_path); env["SURVEY_ROOT"] = str(root)
    (root / "etc/rancher/rke2/config.yaml.d/x.yaml").write_text("K10deadbeef::server:s3cr3t\nhttps://u:pw@h\nreal-key:\nother: v\n")
    out = run([S], env=env).stdout
    assert "K10deadbeef" not in out and "https" not in out
    assert "real-key" in out and "other" in out and "token" in out

def test_time_sync_allows_leading_whitespace(fake_bin, tmp_path):
    env, _ = fake_bin({}); root = node(tmp_path); env["SURVEY_ROOT"] = str(root)
    (root / "etc/chrony.d/a.conf").write_text("  pool pool.corp.example iburst\n\tsourcedir /etc/chrony.d\n")
    out = run([S], env=env).stdout
    assert "pool pool.corp.example iburst" in out and "sourcedir /etc/chrony.d" in out

def test_userinstalled_uses_cache_only_one_per_line(fake_bin, tmp_path):
    env, log = fake_bin({"dnf": "echo falcon-sensor"}); env["SURVEY_ROOT"] = str(node(tmp_path))
    run([S], env=env)
    line = [l for l in log.read_text().splitlines() if l.startswith("dnf")][0]
    assert " -C " in line and "%{name}\\n" in line

def test_make_survey_diff_base_image_can_be_overridden(fake_bin, tmp_path):
    report = tmp_path / "s.txt"; report.write_text("## packages-all\nbash\nfalcon-sensor\n")
    env, log = fake_bin({"podman": "echo bash"}); env["CONTAINER_TOOL"] = "podman"
    r = run(["make", "-s", "survey-diff", f"SURVEY={report}"], env=env)
    assert r.returncode == 0
    assert calls(log)[0].startswith(f"podman run --rm {versions()['ROCKY_BASE_IMAGE']} ")
    r = run(["make", "-s", "survey-diff", f"SURVEY={report}", "BASE_IMAGE=rhel.example.com/rhel-bootc:9"], env=env)
    assert r.returncode == 0 and calls(log)[1].startswith("podman run --rm rhel.example.com/rhel-bootc:9 ")

def test_rke2_version_falls_back_to_usr_local_bin(fake_bin, tmp_path):
    # Under sudo, /usr/local/bin may not be on PATH; there is no rke2 on PATH here.
    env, _ = fake_bin({}); root = node(tmp_path); env["SURVEY_ROOT"] = str(root)
    assert run(["bash", "-c", "command -v rke2"], env=env).returncode != 0
    b = root / "usr/local/bin"; b.mkdir(parents=True)
    (b / "rke2").write_text("#!/usr/bin/env bash\necho 'rke2 version v1.30.9+rke2r1 (abc)'\n"); (b / "rke2").chmod(0o755)
    out = run([S], env=env).stdout
    section = out.split("## rke2-version\n", 1)[1].split("## ", 1)[0]
    assert section.strip() == "rke2 version v1.30.9+rke2r1 (abc)"
