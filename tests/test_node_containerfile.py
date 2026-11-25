# tests/test_node_containerfile.py
from conftest import REPO, run, versions, rke2_tag
P = REPO / "node/Containerfile"
CF = P.read_text() if P.exists() else ""

def test_base_is_an_argument_with_no_default():
    assert CF.startswith("ARG BASE_IMAGE\n")
    froms = [l for l in CF.splitlines() if l.startswith("FROM ")]
    assert froms == ["FROM scratch AS ctx", "FROM ${BASE_IMAGE}"]

def test_scripts_and_site_are_mounted_from_the_ctx_stage():
    assert "COPY node/scripts /scripts" in CF and "COPY site /site" in CF
    mounts = [l for l in CF.splitlines() if "--mount=type=bind" in l]
    assert mounts and all("from=ctx," in l for l in mounts)
    assert "source=node/scripts" not in CF and "source=site," not in CF

def test_build_fails_if_image_carries_an_identity():
    assert "test ! -s /etc/machine-id" in CF and "/etc/ssh/ssh_host_*" in CF
    assert CF.index("test ! -s /etc/machine-id") < CF.index("RUN bootc container lint")

def test_order_of_layers():
    i = [CF.index(s) for s in ("install-rke2.sh", "COPY node/rootfs/ /", "apply-site.sh",
                               "systemctl mask bootc-fetch-apply-updates.timer", "RUN bootc container lint")]
    assert i == sorted(i)

def test_no_self_update_and_no_rke2_enable():
    assert "mask bootc-fetch-apply-updates.timer" in CF
    assert "rke2-server" not in CF and "rke2-agent" not in CF and "enable --now" not in CF

def test_ansible_prerequisites_and_sudoers_mode():
    for pkg in ("python3-libselinux", "python3-dnf", "sudo", "open-vm-tools", "cloud-init"):
        assert pkg in CF
    assert "chmod 0440 /etc/sudoers.d/10-wheel-nopasswd" in CF

def test_no_secrets_or_credentials_arguments():
    for word in ("PASSWORD", "TOKEN", "auth.json", "subscription-manager register"):
        assert word not in CF

def test_make_node_dev_and_prod():
    v = versions(); tag = rke2_tag(v["RKE2_VERSION"])
    dev = run(["make", "-n", "node", "ENV=dev", "BUILD=7"]).stdout
    assert f"--build-arg BASE_IMAGE={v['ROCKY_BASE_IMAGE']}" in dev
    assert f"--build-arg RKE2_VERSION={v['RKE2_VERSION']}" in dev and "--build-arg SITE_ENV=dev" in dev
    assert f"-t {v['REGISTRY']}/rke2-node-dev:{tag}.7" in dev
    assert "-f node/Containerfile" in dev
    assert "--pull=newer" in dev            # a rebuilt base in the registry is picked up
    prod = run(["make", "-n", "node", "ENV=prod", "BUILD=7"]).stdout
    assert f"--build-arg BASE_IMAGE={v['RHEL_BASE_IMAGE']}" in prod
    assert f"rke2-node-prod:{tag}.7" in prod and "--build-arg SITE_ENV=prod" in prod

def test_make_refuses_missing_build_and_unknown_env():
    r = run(["make", "-n", "node", "ENV=dev"])
    assert r.returncode != 0 and "BUILD is required" in (r.stdout + r.stderr)
    assert run(["make", "-n", "node", "ENV=staging", "BUILD=1"]).returncode != 0

def test_print_ref():
    v = versions()
    assert run(["make", "-s", "print-ref", "ENV=dev", "BUILD=7"]).stdout.strip() == \
        f"{v['REGISTRY']}/rke2-node-dev:{rke2_tag(v['RKE2_VERSION'])}.7"

def test_print_ref_with_placeholder_values_on_the_command_line():
    out = run(["make", "-s", "print-ref", "ENV=dev", "BUILD=7", "REGISTRY=r.example.com/p",
               "RKE2_VERSION=v1.34.3+rke2r1"]).stdout.strip()
    assert out == "r.example.com/p/rke2-node-dev:v1.34.3-rke2r1.7"

def test_containerignore_keeps_the_context_small():
    entries = (REPO / ".containerignore").read_text().split()
    for e in (".git", ".venv", ".superpowers", "output", "tests", "docs", "*.vmdk"):
        assert e in entries
    for needed in ("node", "site", "base"):
        assert needed not in entries
