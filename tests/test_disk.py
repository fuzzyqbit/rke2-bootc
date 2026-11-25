import tomllib, pytest
from conftest import REPO, run, calls
BV, MT = str(REPO / "disk/build-vmdk.sh"), str(REPO / "disk/make-template.sh")
GOOD = "registry.example.com/platform/rke2-node-dev:v1.34.3-rke2r1.7"

def test_config_example_is_valid_and_has_no_private_key():
    text = (REPO / "disk/config.toml.example").read_text()
    cfg = tomllib.loads(text)["customizations"]
    assert cfg["user"][0]["name"] == "ansible" and cfg["user"][0]["groups"] == ["wheel"]
    assert cfg["filesystem"][0] == {"mountpoint": "/", "minsize": "60 GiB"}
    assert "PRIVATE KEY" not in text

@pytest.mark.parametrize("ref", ["localhost/rke2-node-dev:v1.34.3-rke2r1.7", "rke2-node-dev:v1",
                                 "registry.example.com/platform/rke2-node-dev",
                                 "registry.example.com/platform/rke2-node-dev:latest"])
def test_build_vmdk_refuses_local_or_moving_refs(fake_bin, tmp_path, ref):
    env, log = fake_bin({"podman": ""}); cfg = tmp_path / "c.toml"; cfg.write_text("")
    r = run([BV, ref, str(cfg), str(tmp_path / "out")], env=env)
    assert r.returncode == 2 and calls(log) == []

def test_build_vmdk_runs_image_builder_on_registry_ref(fake_bin, tmp_path):
    env, log = fake_bin({"podman": ""}); cfg = tmp_path / "c.toml"; cfg.write_text("")
    assert run([BV, GOOD, str(cfg), str(tmp_path / "out")], env=env).returncode == 0
    c = calls(log)
    assert c[0] == f"podman pull {GOOD}"
    assert "--type vmdk --rootfs xfs --config /config.toml " + GOOD in c[1]
    assert f"-v {cfg}:/config.toml:ro" in c[1]

def test_build_vmdk_missing_config_exits_2(fake_bin, tmp_path):
    env, _ = fake_bin({"podman": ""})
    assert run([BV, GOOD, str(tmp_path / "nope.toml"), str(tmp_path / "out")], env=env).returncode == 2

ARGS = ["--vmdk", "/tmp/disk.vmdk", "--name", "rke2-tpl", "--datastore", "ds1", "--network", "VM Network"]

def test_template_is_created_without_ever_powering_on(fake_bin):
    env, log = fake_bin({"govc": ""})
    assert run([MT, *ARGS], env=env).returncode == 0
    c = calls(log)
    assert [l.split()[1] for l in c] == ["import.vmdk", "vm.create", "vm.change", "vm.markastemplate"]
    assert "-on=false" in c[1] and "-firmware efi" in c[1] and "-disk.controller pvscsi" in c[1]
    assert "-net.adapter vmxnet3" in c[1] and "-g rhel9_64Guest" in c[1]
    assert "disk.EnableUUID=TRUE" in c[2]
    assert not any("vm.power" in l for l in c)

def test_boot_once_leaves_vm_unmarked_and_prints_steps(fake_bin):
    env, log = fake_bin({"govc": ""})
    r = run([MT, *ARGS, "--boot-once", "--guest-id", "rockylinux_64Guest", "--hw-version", "vmx-19"], env=env)
    c = calls(log)
    assert "vm.markastemplate" not in " ".join(c) and "rke2-node-generalize" in r.stdout
    assert "-g rockylinux_64Guest" in c[1] and "-version vmx-19" in c[1]

def test_template_missing_required_argument_exits_2(fake_bin):
    env, log = fake_bin({"govc": ""})
    assert run([MT, "--name", "x"], env=env).returncode == 2 and calls(log) == []
