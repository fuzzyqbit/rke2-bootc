import pytest
from conftest import REPO, run, calls
S = str(REPO / "node/scripts/apply-site.sh")

def site(tmp_path):
    s = tmp_path / "site"
    for part in ("common", "dev", "prod"):
        (s / part / "rootfs/etc").mkdir(parents=True)
    (s / "common/packages.txt").write_text("# agents\nfalcon-sensor\n\naudit\n")
    (s / "dev/packages.txt").write_text("devtool\n")
    (s / "common/units.txt").write_text("auditd.service\n")
    (s / "common/rootfs/etc/common.conf").write_text("c")
    (s / "dev/rootfs/etc/env.conf").write_text("dev")
    (s / "prod/rootfs/etc/env.conf").write_text("prod")
    return s

def go(fake_bin, tmp_path, s, env_name="dev"):
    env, log = fake_bin({"dnf": "", "systemctl": "", "update-ca-trust": ""})
    root = tmp_path / "root"; (root / "usr/lib/tmpfiles.d").mkdir(parents=True)
    (root / "var/opt").mkdir(parents=True); (root / "opt").symlink_to("var/opt")
    env["SITE_TARGET_ROOT"] = str(root)
    return run([S, str(s), env_name], env=env), log, root

def test_applies_common_then_env(fake_bin, tmp_path):
    r, log, root = go(fake_bin, tmp_path, site(tmp_path))
    assert r.returncode == 0
    assert (root / "etc/common.conf").read_text() == "c" and (root / "etc/env.conf").read_text() == "dev"
    assert calls(log) == ["dnf -y install falcon-sensor audit", "dnf -y install devtool",
                          "systemctl enable auditd.service"]

def test_ca_anchor_triggers_trust_update(fake_bin, tmp_path):
    s = site(tmp_path); a = s / "common/rootfs/etc/pki/ca-trust/source/anchors"; a.mkdir(parents=True)
    (a / "corp.crt").write_text("-----BEGIN CERTIFICATE-----")
    _, log, _ = go(fake_bin, tmp_path, s)
    assert calls(log)[0].strip() == "update-ca-trust"

@pytest.mark.parametrize("name,content", [("id_ed25519", "-----BEGIN OPENSSH PRIVATE KEY-----"),
                                          ("auth.json", "{}"), ("tls.key", "x")])
def test_secret_in_site_layer_fails_build(fake_bin, tmp_path, name, content):
    s = site(tmp_path); (s / "dev/rootfs/etc" / name).write_text(content)
    r, log, root = go(fake_bin, tmp_path, s)
    assert r.returncode == 3 and name in r.stderr
    assert calls(log) == [] and not (root / "etc/common.conf").exists()

@pytest.mark.parametrize("bad", ["production", "", "common"])
def test_unknown_env_exits_2(fake_bin, tmp_path, bad):
    r, _, _ = go(fake_bin, tmp_path, site(tmp_path), env_name=bad)
    assert r.returncode == 2

def test_opt_marker_makes_opt_real_and_keeps_cni_writable(fake_bin, tmp_path):
    s = site(tmp_path); (s / "common/opt-in-image").write_text("")
    _, _, root = go(fake_bin, tmp_path, s)
    assert (root / "opt").is_dir() and not (root / "opt").is_symlink()
    assert (root / "opt/cni").is_symlink()
    assert "d /var/opt/cni 0755 root root -" in (root / "usr/lib/tmpfiles.d/rke2-node-opt.conf").read_text()

def test_shipped_site_layer_is_empty_and_valid(fake_bin, tmp_path):
    for e in ("dev", "prod"):
        r, log, _ = go(fake_bin, tmp_path / e, REPO / "site", env_name=e)
        assert r.returncode == 0 and calls(log) == []
