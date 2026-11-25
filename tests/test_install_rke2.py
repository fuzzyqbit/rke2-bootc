import json
from conftest import REPO, run, calls
S = str(REPO / "node/scripts/install-rke2.sh")

def env_for(fake_bin, tmp_path, dnf_body=""):
    env, log = fake_bin({"dnf": dnf_body, "systemctl": ""})
    env.update(RKE2_VERSION="v1.34.3+rke2r1", SITE_ENV="dev", TARGET_ROOT=str(tmp_path / "root"))
    return env, log, tmp_path / "root"

def test_installs_pinned_packages_and_disables_units(fake_bin, tmp_path):
    env, log, root = env_for(fake_bin, tmp_path)
    assert run([S], env=env).returncode == 0
    c = calls(log)
    assert c[0] == ("dnf -y install rke2-server-1.34.3~rke2r1 rke2-agent-1.34.3~rke2r1 "
                    "rke2-common-1.34.3~rke2r1 rke2-selinux")
    assert c[1] == "systemctl disable rke2-server.service rke2-agent.service"

def test_fact_file(fake_bin, tmp_path):
    env, _, root = env_for(fake_bin, tmp_path)
    run([S], env=env)
    fact = json.loads((root / "etc/ansible/facts.d/rke2_image.fact").read_text())
    assert fact == {"rke2_version": "v1.34.3+rke2r1", "site_env": "dev"}

def test_repo_file_removed_on_success_and_on_failure(fake_bin, tmp_path):
    for body in ("", "exit 1"):
        env, _, root = env_for(fake_bin, tmp_path, dnf_body=body)
        run([S], env=env)
        assert not list((root / "etc/yum.repos.d").glob("rancher-rke2*"))

def test_repo_urls_seen_by_dnf(fake_bin, tmp_path):
    env, _, root = env_for(fake_bin, tmp_path, dnf_body=f'cat "$TARGET_ROOT"/etc/yum.repos.d/*.repo > {tmp_path}/seen')
    run([S], env=env)
    seen = (tmp_path / "seen").read_text()
    assert "baseurl=https://rpm.rancher.io/rke2/stable/1.34/centos/9/x86_64" in seen
    assert "baseurl=https://rpm.rancher.io/rke2/stable/common/centos/9/noarch" in seen

def test_missing_site_env_exits_2(fake_bin, tmp_path):
    env, _, _ = env_for(fake_bin, tmp_path); del env["SITE_ENV"]
    assert run([S], env=env).returncode == 2
