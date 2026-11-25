# tests/test_node_survey.py
from conftest import REPO, run
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
