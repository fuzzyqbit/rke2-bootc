# tests/test_node_containerfile.py
from conftest import REPO, run
P = REPO / "node/Containerfile"
CF = P.read_text() if P.exists() else ""

def test_base_is_an_argument_with_no_default():
    assert CF.startswith("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n")

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
    dev = run(["make", "-n", "node", "ENV=dev", "BUILD=7"]).stdout
    assert "--build-arg BASE_IMAGE=registry.example.com/platform/rocky-bootc:9" in dev
    assert "--build-arg RKE2_VERSION=v1.34.3+rke2r1" in dev and "--build-arg SITE_ENV=dev" in dev
    assert "-t registry.example.com/platform/rke2-node-dev:v1.34.3-rke2r1.7" in dev
    assert "-f node/Containerfile" in dev
    prod = run(["make", "-n", "node", "ENV=prod", "BUILD=7"]).stdout
    assert "--build-arg BASE_IMAGE=registry.redhat.io/rhel9/rhel-bootc:latest" in prod
    assert "rke2-node-prod:v1.34.3-rke2r1.7" in prod and "--build-arg SITE_ENV=prod" in prod

def test_make_refuses_missing_build_and_unknown_env():
    r = run(["make", "-n", "node", "ENV=dev"])
    assert r.returncode != 0 and "BUILD is required" in (r.stdout + r.stderr)
    assert run(["make", "-n", "node", "ENV=staging", "BUILD=1"]).returncode != 0

def test_print_ref():
    assert run(["make", "-s", "print-ref", "ENV=dev", "BUILD=7"]).stdout.strip() == \
        "registry.example.com/platform/rke2-node-dev:v1.34.3-rke2r1.7"
