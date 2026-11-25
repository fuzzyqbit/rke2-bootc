# tests/test_generalize.py
from conftest import REPO, run, calls
S = str(REPO / "node/rootfs/usr/sbin/rke2-node-generalize")

def tree(tmp_path):
    root = tmp_path / "root"
    for d in ("etc/ssh", "var/lib/dbus", "etc/NetworkManager/system-connections", "var/lib/rancher/rke2"):
        (root / d).mkdir(parents=True)
    (root / "etc/machine-id").write_text("abc123\n")
    (root / "etc/ssh/ssh_host_ed25519_key").write_text("k")
    (root / "etc/ssh/sshd_config").write_text("keep")
    (root / "etc/NetworkManager/system-connections/ens192.nmconnection").write_text("c")
    return root

def test_resets_identity_and_powers_off(fake_bin, tmp_path):
    root = tree(tmp_path)
    env, log = fake_bin({"cloud-init": "", "journalctl": "", "systemctl": ""}); env["GENERALIZE_ROOT"] = str(root)
    assert run([S], env=env).returncode == 0
    assert (root / "etc/machine-id").read_text() == ""
    assert not list((root / "etc/ssh").glob("ssh_host_*")) and (root / "etc/ssh/sshd_config").exists()
    assert not list((root / "etc/NetworkManager/system-connections").iterdir())
    c = calls(log)
    assert c[0] == "cloud-init clean --logs" and c[-1] == "systemctl poweroff"

def test_no_poweroff_flag(fake_bin, tmp_path):
    env, log = fake_bin({"cloud-init": "", "journalctl": "", "systemctl": ""}); env["GENERALIZE_ROOT"] = str(tree(tmp_path))
    run([S, "--no-poweroff"], env=env)
    assert "systemctl poweroff" not in calls(log)

def test_refuses_on_node_that_ran_rke2(fake_bin, tmp_path):
    root = tree(tmp_path); (root / "var/lib/rancher/rke2/agent").mkdir()
    env, log = fake_bin({"cloud-init": "", "journalctl": "", "systemctl": ""}); env["GENERALIZE_ROOT"] = str(root)
    r = run([S], env=env)
    assert r.returncode == 1 and "refusing" in r.stderr
    assert (root / "etc/machine-id").read_text() == "abc123\n"
    assert (root / "etc/ssh/ssh_host_ed25519_key").exists() and calls(log) == []
