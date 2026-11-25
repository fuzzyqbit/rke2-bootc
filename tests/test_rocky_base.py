# tests/test_rocky_base.py
from conftest import REPO, run, versions
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
    v = versions()
    out = run(["make", "-n", "rocky-base"]).stdout
    assert "-f base/rocky/Containerfile" in out
    assert f"-t {v['ROCKY_BASE_IMAGE']}" in out
    assert f"--build-arg ROCKY_MIRROR={v['ROCKY_MIRROR']}" in out
    # build-rootfs runs rpm-ostree/bwrap inside the build and needs these privileges.
    for flag in ("--cap-add=all", "--security-opt=label=type:container_runtime_t", "--device /dev/fuse"):
        assert flag in out
    # A patch build must not reuse a cached build-rootfs layer or a stale builder image.
    assert "--no-cache" in out and "--pull=newer" in out
