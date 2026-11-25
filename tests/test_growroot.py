from conftest import REPO, run, calls
S = str(REPO / "node/rootfs/usr/libexec/rke2-node/growroot")

def setup(fake_bin, tmp_path, growpart_body="", partition="3"):
    env, log = fake_bin({"findmnt": "echo /dev/sda3", "lsblk": "echo sda", "growpart": growpart_body,
                         "mount": "", "systemd-growfs": ""})
    sysfs = tmp_path / "sys/class/block/sda3"; sysfs.mkdir(parents=True)
    if partition: (sysfs / "partition").write_text(partition + "\n")
    env["SYSFS_ROOT"] = str(tmp_path / "sys"); env["SYSTEMD_GROWFS"] = "systemd-growfs"
    return env, log

def test_grows_partition_then_filesystem(fake_bin, tmp_path):
    env, log = setup(fake_bin, tmp_path)
    assert run([S], env=env).returncode == 0
    c = calls(log)
    assert "growpart /dev/sda 3" in c
    assert c.index("growpart /dev/sda 3") < c.index("mount -o remount,rw /sysroot") < len(c) - 1
    assert c[-1].endswith("systemd-growfs /sysroot")

def test_nothing_to_grow_is_success(fake_bin, tmp_path):
    env, _ = setup(fake_bin, tmp_path, growpart_body="exit 1")
    assert run([S], env=env).returncode == 0

def test_growpart_error_fails(fake_bin, tmp_path):
    env, _ = setup(fake_bin, tmp_path, growpart_body="exit 2")
    assert run([S], env=env).returncode != 0

def test_non_partition_device_is_left_alone(fake_bin, tmp_path):
    env, log = setup(fake_bin, tmp_path, partition="")
    r = run([S], env=env)
    assert r.returncode == 0 and "/dev/sda3" in r.stderr
    assert not any(l.startswith(("growpart", "mount")) for l in calls(log))
