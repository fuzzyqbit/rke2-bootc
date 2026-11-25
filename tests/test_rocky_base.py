# tests/test_rocky_base.py
from conftest import REPO, run
CF = (REPO / "base/rocky/Containerfile").read_text() if (REPO / "base/rocky/Containerfile").exists() else ""

def test_two_stage_build_from_scratch():
    assert "bootc-base-imagectl build-rootfs" in CF
    assert "FROM scratch" in CF and "COPY --from=builder /target-rootfs/ /" in CF

def test_builder_uses_only_rocky_repos():
    assert "rm -f /etc/yum.repos.d/*.repo" in CF
    repo = (REPO / "base/rocky/rocky.repo").read_text()
    assert "[baseos]" in repo and "[appstream]" in repo and "gpgcheck=1" in repo
    assert "centos" not in repo.lower()

def test_bootc_labels_and_lint():
    assert "containers.bootc" in CF and "ostree.bootable" in CF
    assert CF.rstrip().splitlines()[-1].strip() == "RUN bootc container lint"

def test_make_dry_run():
    out = run(["make", "-n", "rocky-base"]).stdout
    assert "-f base/rocky/Containerfile" in out
    assert "-t registry.example.com/platform/rocky-bootc:9" in out
    assert "--build-arg ROCKY_MIRROR=https://dl.rockylinux.org/pub/rocky" in out
