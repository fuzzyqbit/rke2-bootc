import os
import stat

import pytest
from conftest import REPO, run, calls
S = str(REPO / "node/scripts/apply-site.sh")
FAKES = {"dnf": "", "systemctl": "", "update-ca-trust": ""}

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

def empty_site(tmp_path):
    """A site layer with nothing to apply: the shape of the shipped one, but a temp fixture."""
    s = tmp_path / "empty-site"
    for part in ("common", "dev", "prod"):
        (s / part / "rootfs").mkdir(parents=True)
        (s / part / "rootfs/.gitkeep").write_text("")
        (s / part / "packages.txt").write_text("# comment only\n")
        (s / part / "units.txt").write_text("# comment only\n")
    return s

def make_root(tmp_path):
    root = tmp_path / "root"; (root / "usr/lib/tmpfiles.d").mkdir(parents=True)
    (root / "var/opt").mkdir(parents=True); (root / "opt").symlink_to("var/opt")
    return root

def go(fake_bin, tmp_path, s, env_name="dev", phases=("files", "packages")):
    env, log = fake_bin(FAKES)
    root = make_root(tmp_path)
    env["SITE_TARGET_ROOT"] = str(root)
    r = None
    for phase in phases:
        r = run([S, str(s), env_name, phase], env=env)
        if r.returncode != 0:
            break
    return r, log, root

def test_applies_common_then_env(fake_bin, tmp_path):
    r, log, root = go(fake_bin, tmp_path, site(tmp_path))
    assert r.returncode == 0
    assert (root / "etc/common.conf").read_text() == "c" and (root / "etc/env.conf").read_text() == "dev"
    assert calls(log) == ["dnf -y install falcon-sensor audit", "dnf -y install devtool",
                          "systemctl enable auditd.service"]

def test_files_phase_copies_files_and_installs_nothing(fake_bin, tmp_path):
    r, log, root = go(fake_bin, tmp_path, site(tmp_path), phases=("files",))
    assert r.returncode == 0
    assert (root / "etc/common.conf").read_text() == "c" and (root / "etc/env.conf").read_text() == "dev"
    assert calls(log) == []

def test_packages_phase_installs_and_copies_nothing(fake_bin, tmp_path):
    s = site(tmp_path); (s / "common/opt-in-image").write_text("")
    r, log, root = go(fake_bin, tmp_path, s, phases=("packages",))
    assert r.returncode == 0
    assert not (root / "etc/common.conf").exists() and (root / "opt").is_symlink()
    assert calls(log) == ["dnf -y install falcon-sensor audit", "dnf -y install devtool",
                          "systemctl enable auditd.service"]

@pytest.mark.parametrize("args", [["dev"], ["dev", "all"], ["dev", ""], ["dev", "files", "extra"]])
def test_missing_or_unknown_phase_exits_2(fake_bin, tmp_path, args):
    env, log = fake_bin(FAKES); env["SITE_TARGET_ROOT"] = str(make_root(tmp_path))
    r = run([S, str(site(tmp_path)), *args], env=env)
    assert r.returncode == 2 and calls(log) == []

def test_ca_anchor_triggers_trust_update_in_files_phase(fake_bin, tmp_path):
    s = site(tmp_path); a = s / "common/rootfs/etc/pki/ca-trust/source/anchors"; a.mkdir(parents=True)
    (a / "corp.crt").write_text("-----BEGIN CERTIFICATE-----")
    _, log, _ = go(fake_bin, tmp_path, s, phases=("files",))
    assert [c.strip() for c in calls(log)] == ["update-ca-trust"]

@pytest.mark.parametrize("phase", ["files", "packages"])
@pytest.mark.parametrize("name,content", [("id_ed25519", "-----BEGIN OPENSSH PRIVATE KEY-----"),
                                          ("auth.json", "{}"), ("tls.key", "x")])
def test_secret_in_site_layer_fails_build(fake_bin, tmp_path, name, content, phase):
    s = site(tmp_path); (s / "dev/rootfs/etc" / name).write_text(content)
    r, log, root = go(fake_bin, tmp_path, s, phases=(phase,))
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
    tmpfiles = (root / "usr/lib/tmpfiles.d/rke2-node-opt.conf").read_text()
    assert tmpfiles.count("d /var/opt/cni 0755 root root -") == 1

def test_copy_does_not_keep_checkout_ownership_or_group_write(fake_bin, tmp_path):
    text = (REPO / "node/scripts/apply-site.sh").read_text()
    assert "--no-preserve=ownership" in text
    s = site(tmp_path)
    d = s / "common/rootfs/etc/newdir"; d.mkdir(); d.chmod(0o775)
    f = d / "agent.conf"; f.write_text("x"); f.chmod(0o664)
    (s / "common/rootfs/etc/common.conf").chmod(0o666)
    if os.geteuid() == 0:
        os.chown(f, 1000, 1000); os.chown(d, 1000, 1000)
    r, _, root = go(fake_bin, tmp_path, s, phases=("files",))
    assert r.returncode == 0
    for p in (root / "etc/newdir/agent.conf", root / "etc/newdir", root / "etc/common.conf"):
        mode = p.stat().st_mode
        assert not mode & (stat.S_IWGRP | stat.S_IWOTH), p
    assert stat.S_IMODE((root / "etc/newdir/agent.conf").stat().st_mode) == 0o644
    if os.geteuid() == 0:
        assert (root / "etc/newdir/agent.conf").stat().st_uid == 0
        assert (root / "etc/newdir").stat().st_uid == 0

def test_existing_target_directories_keep_their_mode(fake_bin, tmp_path):
    s = site(tmp_path); t = s / "common/rootfs/var/tmp"; t.mkdir(parents=True); (t / "x").write_text("x")
    env, log = fake_bin(FAKES); root = make_root(tmp_path)
    (root / "var/tmp").mkdir(); (root / "var/tmp").chmod(0o1777)
    env["SITE_TARGET_ROOT"] = str(root)
    assert run([S, str(s), "dev", "files"], env=env).returncode == 0
    assert stat.S_IMODE((root / "var/tmp").stat().st_mode) == 0o1777

@pytest.mark.parametrize("e", ["dev", "prod"])
def test_shipped_site_layer_applies_cleanly_into_a_temp_root(fake_bin, tmp_path, e):
    # Whatever the owner has put in site/, it must apply; only a temp root is ever written.
    r, _, root = go(fake_bin, tmp_path, REPO / "site", env_name=e)
    assert r.returncode == 0, r.stderr
    assert not list(root.rglob("*.example")) and not list(root.rglob(".gitkeep"))

@pytest.mark.parametrize("phase", ["files", "packages"])
def test_default_root_slash_works(fake_bin, tmp_path, phase):
    # SITE_TARGET_ROOT=/ and unset (the real build default) must not break. Only a temp site layer
    # with nothing to apply is used, never the repository's site/, so nothing is written to /.
    env, log = fake_bin(FAKES); s = empty_site(tmp_path)
    for value in ("/", None):
        e = dict(env)
        e.pop("SITE_TARGET_ROOT", None)
        if value:
            e["SITE_TARGET_ROOT"] = value
        r = run([S, str(s), "dev", phase], env=e)
        assert r.returncode == 0, r.stderr
    assert calls(log) == []

def test_trailing_slash_root_still_works(fake_bin, tmp_path):
    env, log = fake_bin(FAKES)
    root = tmp_path / "root"; root.mkdir()
    env["SITE_TARGET_ROOT"] = str(root) + "/"
    r = run([S, str(site(tmp_path)), "dev", "files"], env=env)
    assert r.returncode == 0 and (root / "etc/common.conf").read_text() == "c"
