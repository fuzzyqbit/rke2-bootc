import pathlib, tomllib, pytest
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

# Fake image builder: on "run", writes a disk where bootc-image-builder would.
WRITES_VMDK = 'if [ "$1" = run ]; then mkdir -p "$OUT/vmdk" && echo disk > "$OUT/vmdk/disk.vmdk"; fi'

def test_build_vmdk_runs_image_builder_on_registry_ref(fake_bin, tmp_path):
    env, log = fake_bin({"podman": WRITES_VMDK}); cfg = tmp_path / "c.toml"; cfg.write_text("")
    env["OUT"] = str(tmp_path / "out")
    r = run([BV, GOOD, str(cfg), str(tmp_path / "out")], env=env)
    assert r.returncode == 0 and f"VMDK written to {tmp_path}/out/vmdk/disk.vmdk" in r.stdout
    c = calls(log)
    assert c[0] == f"podman pull {GOOD}"
    assert "--type vmdk --rootfs xfs --config /config.toml " + GOOD in c[1]
    assert f"-v {cfg}:/config.toml:ro" in c[1]

def test_build_vmdk_fails_when_no_disk_was_written(fake_bin, tmp_path):
    env, _ = fake_bin({"podman": ""}); cfg = tmp_path / "c.toml"; cfg.write_text("")
    r = run([BV, GOOD, str(cfg), str(tmp_path / "out")], env=env)
    assert r.returncode != 0 and "VMDK written" not in r.stdout and "not written" in r.stderr

def test_build_vmdk_missing_config_exits_2(fake_bin, tmp_path):
    env, _ = fake_bin({"podman": ""})
    assert run([BV, GOOD, str(tmp_path / "nope.toml"), str(tmp_path / "out")], env=env).returncode == 2

def args(tmp_path):
    vmdk = tmp_path / "out/vmdk/disk.vmdk"; vmdk.parent.mkdir(parents=True); vmdk.write_text("disk")
    return vmdk, ["--vmdk", str(vmdk), "--name", "rke2-tpl", "--datastore", "ds1", "--network", "VM Network"]

# Fake govc: on import.vmdk, records what the last argument resolves to and whether it is a symlink.
RECORD_IMPORT = ('if [ "$1" = import.vmdk ]; then f="${@: -1}"; '
                 '{ readlink -f "$f"; [ -L "$f" ] && echo symlink; } > "$SEEN"; fi')

def test_template_is_created_without_ever_powering_on(fake_bin, tmp_path):
    env, log = fake_bin({"govc": ""}); _, a = args(tmp_path)
    assert run([MT, *a], env=env).returncode == 0
    c = calls(log)
    assert [l.split()[1] for l in c] == ["import.vmdk", "vm.create", "vm.change", "vm.markastemplate"]
    assert "-on=false" in c[1] and "-firmware efi" in c[1] and "-disk.controller pvscsi" in c[1]
    assert "-net.adapter vmxnet3" in c[1] and "-g rhel9_64Guest" in c[1]
    assert "-link=false" in c[1].split()       # a full disk, not a linked child of the imported one
    assert "disk.EnableUUID=TRUE" in c[2]
    assert not any("vm.power" in l for l in c)

def test_import_uses_a_name_vmdk_symlink_and_no_remote_argument(fake_bin, tmp_path):
    # Works on govc up to 0.55 and from 0.56 on: both write [ds1] rke2-tpl/rke2-tpl.vmdk.
    env, log = fake_bin({"govc": RECORD_IMPORT}); vmdk, a = args(tmp_path)
    env["SEEN"] = str(tmp_path / "seen")
    assert run([MT, *a], env=env).returncode == 0
    imp = calls(log)[0].split()
    assert imp[:3] == ["govc", "import.vmdk", "-ds"] and imp[3] == "ds1" and len(imp) == 5
    local = imp[4]
    assert local.endswith("/rke2-tpl.vmdk") and local != str(vmdk)
    assert (tmp_path / "seen").read_text().split() == [str(vmdk), "symlink"]
    assert not pathlib.Path(local).parent.exists()          # temp directory removed on exit
    create = calls(log)[1]
    assert "-disk rke2-tpl/rke2-tpl.vmdk" in create and create.endswith(" rke2-tpl")

def test_import_passes_pool_and_temp_dir_is_removed_on_failure(fake_bin, tmp_path):
    env, log = fake_bin({"govc": 'if [ "$1" = import.vmdk ]; then exit 1; fi'}); _, a = args(tmp_path)
    r = run([MT, *a, "--pool", "p1"], env=env)
    assert r.returncode != 0
    imp = calls(log)[0].split()
    assert imp[2:6] == ["-ds", "ds1", "-pool", "p1"] and len(imp) == 7
    assert not pathlib.Path(imp[-1]).parent.exists() and len(calls(log)) == 1

def test_boot_once_leaves_vm_unmarked_and_prints_steps(fake_bin, tmp_path):
    env, log = fake_bin({"govc": ""}); _, a = args(tmp_path)
    r = run([MT, *a, "--boot-once", "--guest-id", "rockylinux_64Guest", "--hw-version", "vmx-19"], env=env)
    c = calls(log)
    assert "vm.markastemplate" not in " ".join(c) and "rke2-node-generalize" in r.stdout
    assert "-g rockylinux_64Guest" in c[1] and "-version vmx-19" in c[1]

def test_template_missing_required_argument_exits_2(fake_bin):
    env, log = fake_bin({"govc": ""})
    assert run([MT, "--name", "x"], env=env).returncode == 2 and calls(log) == []

def test_make_template_passes_template_args(tmp_path):
    out = run(["make", "-n", "template", "VMDK=d.vmdk", "NAME=t", "DATASTORE=ds", "NETWORK=net",
               "TEMPLATE_ARGS=--hw-version vmx-19 --boot-once"]).stdout
    assert '--guest-id "rhel9_64Guest" --hw-version vmx-19 --boot-once' in out
    plain = run(["make", "-n", "template", "VMDK=d.vmdk", "NAME=t", "DATASTORE=ds", "NETWORK=net"]).stdout
    line = next(l for l in plain.splitlines() if "--guest-id" in l)   # make under make adds "Entering" lines
    assert line.rstrip().endswith('--guest-id "rhel9_64Guest"')

def test_template_missing_vmdk_exits_2_before_govc(fake_bin, tmp_path):
    env, log = fake_bin({"govc": ""}); _, a = args(tmp_path)
    a[1] = str(tmp_path / "nope.vmdk")
    r = run([MT, *a], env=env)
    assert r.returncode == 2 and "nope.vmdk" in r.stderr and "does not exist" in r.stderr
    assert calls(log) == []
