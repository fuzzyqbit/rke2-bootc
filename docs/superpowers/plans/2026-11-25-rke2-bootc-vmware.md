# RKE2 bootc node image for VMware — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A project that builds a bootc RKE2 node image on Rocky 9 (dev) and RHEL 9 (prod), turns it into a vCenter template, and updates and migrates clusters with Ansible.

**Architecture:** Two container builds (a Rocky bootc base, and one node layer on a base passed as a build argument), with build-time logic in small shell scripts that are unit-tested against fake commands. A site layer adds site packages and files. Shell scripts wrap the disk and template build; Ansible playbooks do rolling updates. Nothing can be built or booted in the authoring environment, so every task is proven by static and stub-based tests, and real-infrastructure proof is a checklist the user runs in dev.

**Tech Stack:** podman/buildah Containerfiles, bash, bootc, bootc-image-builder, govc, Ansible (ansible-core), pytest, shellcheck, yamllint, ansible-lint, GNU make.

**Spec:** `docs/superpowers/specs/2026-11-25-rke2-bootc-vmware-design.md`

## Global Constraints

- Project root is `/home/user/rke2-bootc`. All paths below are relative to it.
- Rocky Linux 9 and RHEL 9 only, x86_64 only.
- The `rancherfederal/rke2-ansible` role (collection 2.1.0) is used unmodified. No task may patch, fork or wrap it.
- No secrets in the container image or in git: no registry credentials, Red Hat credentials, SSH private keys, join tokens, enrolment keys.
- The dev and prod images may differ only in the base image and in `site/dev` versus `site/prod`.
- Nothing the image places under `/var` may be needed after first install. `/usr` is read-only on a node; only `/etc` and `/var` are writable.
- `rke2-server.service` and `rke2-agent.service` are installed and left disabled.
- `bootc-fetch-apply-updates.timer` is masked. No node updates or reboots itself.
- The RKE2 version is defined once, in `versions.env`, in the form `v<major>.<minor>.<patch>+rke2r<n>`.
- Image tags are immutable: `<rke2 tag>.<build>`, for example `v1.34.3-rke2r1.7`. Never `latest`.
- Every shell script starts with `#!/usr/bin/env bash` and `set -euo pipefail`, and passes `shellcheck`.
- Do not run `podman`, `bootc`, `govc` or `ansible-playbook` against real systems in any task; none are available. Tests use the fake-command fixture from Task 1.
- If a step cannot be done as written (a tool behaves differently, a claim in the spec turns out wrong), stop and report. Do not change approach silently.
- Every commit message ends with these two lines:
  ```
  Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01XXthvugMD91mEXzPRybpLS
  ```
- Commits are local only. Do not push and do not open a pull request.

## Review Focus

1. **A secret placed in the site layer** (private key, `auth.json`): the image build must fail and name the file. Pinned in Task 6.
2. **The survey script run on a live node**: its output must never contain a config value such as the join token. Pinned in Task 9.
3. **`rke2-node-generalize` run on a node that has already joined a cluster**: it must refuse and change nothing. Pinned in Task 5.
4. **A disk built from a local-only or `latest` image reference**: nodes would look for updates in the wrong place; `build-vmdk.sh` must refuse. Pinned in Task 8.
5. **A rolling update re-run after it failed mid-node**: a node already on the target image but still cordoned by the earlier run must be finished and uncordoned, not skipped; a node cordoned by an operator must be left alone. Pinned in Task 10.

---

## File Structure

| Path | Responsibility |
|---|---|
| `versions.env` | The one place versions and image names are set |
| `Makefile` | Entry points: `test`, `lint`, `rocky-base`, `node`, `push`, `vmdk`, `template`, `survey-diff` |
| `requirements-dev.txt`, `tests/conftest.py` | Test tooling and the fake-command fixture |
| `node/scripts/rke2-version.sh` | Converts the RKE2 version between its three spellings |
| `base/rocky/Containerfile`, `base/rocky/rocky.repo` | Rocky 9 bootc base build |
| `node/scripts/install-rke2.sh` | Build-time RKE2 install, fact file, repo cleanup |
| `node/rootfs/**` | Files copied verbatim into the node image |
| `node/rootfs/usr/libexec/rke2-node/growroot` | Grows the root filesystem at boot |
| `node/rootfs/usr/sbin/rke2-node-generalize` | Resets identity before templating |
| `node/scripts/apply-site.sh`, `site/**` | Site layer and its build-time applier |
| `node/Containerfile` | The node image build |
| `disk/config.toml.example`, `disk/build-vmdk.sh`, `disk/make-template.sh` | VMDK and vCenter template build |
| `hack/node-survey.sh`, `hack/survey-diff.sh` | Survey of an existing node |
| `ansible/inventory.example/**` | Variables `rke2-ansible` needs with this image |
| `ansible/rolling-update.yml`, `ansible/tasks/update-node.yml` | Rolling update |
| `README.md`, `docs/verification-checklist.md`, `docs/migration.md`, `docs/operations.md`, `site/README.md` | Documentation and runbooks |

---

### Task 1: Scaffold, version helper, test harness

**Files:**
- Create: `.gitignore`, `.yamllint`, `versions.env`, `requirements-dev.txt`, `Makefile`, `node/scripts/rke2-version.sh`, `tests/conftest.py`, `tests/test_rke2_version.py`, `tests/test_makefile_basics.py`

**Interfaces:**
- Produces: `node/scripts/rke2-version.sh <rpm|majmin|tag> <version>` printing one line; exit 2 with a message on stderr for a bad field or version.
- Produces: `versions.env`, plain `KEY=value` lines only (no quotes, no spaces, no comment or blank lines) so both `make` and `bash` can read it:
  ```
  RKE2_VERSION=v1.34.3+rke2r1
  EL_MAJOR=9
  REGISTRY=registry.example.com/platform
  ROCKY_BASE_IMAGE=registry.example.com/platform/rocky-bootc:9
  RHEL_BASE_IMAGE=registry.redhat.io/rhel9/rhel-bootc:latest
  RANCHER_RPM_BASEURL=https://rpm.rancher.io
  RKE2_CHANNEL=stable
  ROCKY_MIRROR=https://dl.rockylinux.org/pub/rocky
  ```
  `RKE2_VERSION` is a placeholder until the node survey supplies the cluster's real version.
- Produces: pytest fixtures in `tests/conftest.py`:
  - `REPO: pathlib.Path` — project root.
  - `fake_bin(commands: dict[str, str]) -> tuple[dict[str, str], pathlib.Path]` — creates one executable per key in a temp dir; each appends `<name> <args>` as one line to a log file, then runs the given shell body (empty string means `exit 0`). Returns a copy of the current environment with that dir first on `PATH`, and the log path. It may be called more than once in a test; each call gets its own directory and log.
  - `run(args: list[str], env: dict | None = None, cwd=REPO) -> subprocess.CompletedProcess` — `text=True`, `capture_output=True`, never raises.
  - `calls(log: pathlib.Path) -> list[str]` — the log's lines.
- Produces: Makefile targets `venv` (creates `.venv`, installs `requirements-dev.txt`), `test` (`.venv/bin/pytest -q`), `lint` (shellcheck on every `*.sh` and every extensionless script under `node/rootfs/usr`, then `yamllint -s .`, then `ansible-lint ansible/` and `ansible-playbook --syntax-check` once `ansible/` exists).

- [ ] **Step 1: Initialise the repository**

Run `git init -b main` in the project root. `.gitignore` contains `.venv/`, `output/`, `disk/config.toml`, `__pycache__/`, `.pytest_cache/`, `*.vmdk`. `requirements-dev.txt` contains `pytest`, `pyyaml`, `yamllint`, `ansible-core`, `ansible-lint`, `shellcheck-py`. `.yamllint` extends `default`, ignores `.venv/`, sets `line-length` max to 160 and disables the `document-start` rule. Commit the existing `docs/` together with this task.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_rke2_version.py
import pytest
from conftest import REPO, run
S = str(REPO / "node/scripts/rke2-version.sh")

@pytest.mark.parametrize("field,expected", [
    ("rpm", "1.34.3~rke2r1"), ("majmin", "1.34"), ("tag", "v1.34.3-rke2r1")])
def test_fields(field, expected):
    r = run([S, field, "v1.34.3+rke2r1"])
    assert (r.returncode, r.stdout.strip()) == (0, expected)

@pytest.mark.parametrize("bad", ["1.34.3+rke2r1", "v1.34+rke2r1", "v1.34.3", "latest", ""])
def test_bad_version_exits_2(bad):
    r = run([S, "rpm", bad])
    assert r.returncode == 2 and "version" in r.stderr.lower()

def test_bad_field_exits_2():
    assert run([S, "nope", "v1.34.3+rke2r1"]).returncode == 2
```

```python
# tests/test_makefile_basics.py
from conftest import REPO, run

def test_versions_env_is_plain_key_value():
    for line in (REPO / "versions.env").read_text().splitlines():
        key, sep, value = line.partition("=")
        assert sep and key.replace("_", "").isalnum() and key == key.upper()
        assert value and " " not in value and '"' not in value

def test_versions_env_rke2_version_is_valid():
    env = dict(l.split("=", 1) for l in (REPO / "versions.env").read_text().split())
    assert run([str(REPO / "node/scripts/rke2-version.sh"), "rpm", env["RKE2_VERSION"]]).returncode == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `make venv && make test`
Expected: FAIL — `rke2-version.sh` does not exist.

- [ ] **Step 4: Implement `conftest.py`, `versions.env`, `rke2-version.sh` and the Makefile targets**

The version must match `^v[0-9]+\.[0-9]+\.[0-9]+\+rke2r[0-9]+$`. `rpm` strips the leading `v` and replaces `+` with `~`; `tag` replaces `+` with `-`.

- [ ] **Step 5: Run tests and lint**

Run: `make test && make lint`
Expected: all tests pass; lint reports nothing.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "chore: scaffold project, version helper and test harness"
```

---

### Task 2: Rocky 9 bootc base

**Files:**
- Create: `base/rocky/Containerfile`, `base/rocky/rocky.repo`, `tests/test_rocky_base.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: `versions.env` (`ROCKY_BASE_IMAGE`, `ROCKY_MIRROR`, `EL_MAJOR`).
- Produces: `make rocky-base` (build, tag `$(ROCKY_BASE_IMAGE)`) and `make rocky-base-push`. `CONTAINER_TOOL ?= podman`.
- Produces: an image that later tasks treat exactly like `rhel-bootc`: `dnf`, Python 3, OpenSSH server, NetworkManager present; SELinux enforcing.

This recipe is unproven (spec: "the least proven part"). It follows the Red Hat "Creating bootc images from scratch" pattern. Experiment 1 in `docs/verification-checklist.md` (Task 11) is where it is proven.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest -q tests/test_rocky_base.py`
Expected: FAIL.

- [ ] **Step 3: Write the Containerfile, repo file and Makefile targets**

```dockerfile
# base/rocky/Containerfile
ARG BUILDER_IMAGE=quay.io/centos-bootc/centos-bootc:stream9
FROM ${BUILDER_IMAGE} AS builder
ARG ROCKY_MIRROR
ARG EL_MAJOR=9
COPY base/rocky/rocky.repo /tmp/rocky.repo
RUN rm -f /etc/yum.repos.d/*.repo \
 && sed -e "s|@MIRROR@|${ROCKY_MIRROR}|g" -e "s|@EL@|${EL_MAJOR}|g" /tmp/rocky.repo > /etc/yum.repos.d/rocky.repo \
 && curl -fsSL "${ROCKY_MIRROR}/RPM-GPG-KEY-Rocky-${EL_MAJOR}" -o /etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky-${EL_MAJOR}
RUN /usr/libexec/bootc-base-imagectl build-rootfs --manifest=standard /target-rootfs

FROM scratch
COPY --from=builder /target-rootfs/ /
LABEL containers.bootc=1
LABEL ostree.bootable=1
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
RUN bootc container lint
```

`rocky.repo` defines `[baseos]` and `[appstream]` with `baseurl=@MIRROR@/@EL@/BaseOS/$basearch/os/` (and `AppStream`), `gpgcheck=1`, `gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky-@EL@`. The build context is the project root.

- [ ] **Step 4: Run tests and lint**

Run: `make test && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: Rocky 9 bootc base image build"
```

---

### Task 3: Build-time RKE2 install script

**Files:**
- Create: `node/scripts/install-rke2.sh`, `tests/test_install_rke2.py`

**Interfaces:**
- Consumes: `node/scripts/rke2-version.sh` (same directory).
- Produces: `install-rke2.sh`, no arguments. Environment: `RKE2_VERSION`, `SITE_ENV` (both required, exit 2 if unset), `RANCHER_RPM_BASEURL` (default `https://rpm.rancher.io`), `RKE2_CHANNEL` (default `stable`), `EL_MAJOR` (default `9`), `TARGET_ROOT` (default empty; prefix for every file it writes, used by tests).
- Produces on the image: `/etc/ansible/facts.d/rke2_image.fact` containing exactly `{"rke2_version": "<RKE2_VERSION>", "site_env": "<SITE_ENV>"}`, mode 0644. Ansible reads it as `ansible_local.rke2_image.rke2_version`.

Behaviour, in order: write `$TARGET_ROOT/etc/yum.repos.d/rancher-rke2-build.repo` with repo ids `rancher-rke2-common-build` (`$BASEURL/rke2/$CHANNEL/common/centos/$EL_MAJOR/noarch`) and `rancher-rke2-build` (`$BASEURL/rke2/$CHANNEL/<majmin>/centos/$EL_MAJOR/x86_64`), both `gpgcheck=1`, `gpgkey=$BASEURL/public.key`; run `dnf -y install rke2-server-<rpm> rke2-agent-<rpm> rke2-common-<rpm> rke2-selinux`; run `systemctl disable rke2-server.service rke2-agent.service`; write the fact file; delete the repo file. The repo file is deleted even when `dnf` fails (trap).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_install_rke2.py
import json
from conftest import REPO, run, calls
S = str(REPO / "node/scripts/install-rke2.sh")

def env_for(fake_bin, tmp_path, dnf_body=""):
    env, log = fake_bin({"dnf": dnf_body, "systemctl": ""})
    env.update(RKE2_VERSION="v1.34.3+rke2r1", SITE_ENV="dev", TARGET_ROOT=str(tmp_path / "root"))
    return env, log, tmp_path / "root"

def test_installs_pinned_packages_and_disables_units(fake_bin, tmp_path):
    env, log, root = env_for(fake_bin, tmp_path)
    assert run([S], env=env).returncode == 0
    c = calls(log)
    assert c[0] == ("dnf -y install rke2-server-1.34.3~rke2r1 rke2-agent-1.34.3~rke2r1 "
                    "rke2-common-1.34.3~rke2r1 rke2-selinux")
    assert c[1] == "systemctl disable rke2-server.service rke2-agent.service"

def test_fact_file(fake_bin, tmp_path):
    env, _, root = env_for(fake_bin, tmp_path)
    run([S], env=env)
    fact = json.loads((root / "etc/ansible/facts.d/rke2_image.fact").read_text())
    assert fact == {"rke2_version": "v1.34.3+rke2r1", "site_env": "dev"}

def test_repo_file_removed_on_success_and_on_failure(fake_bin, tmp_path):
    for body in ("", "exit 1"):
        env, _, root = env_for(fake_bin, tmp_path, dnf_body=body)
        run([S], env=env)
        assert not list((root / "etc/yum.repos.d").glob("rancher-rke2*"))

def test_repo_urls_seen_by_dnf(fake_bin, tmp_path):
    env, _, root = env_for(fake_bin, tmp_path, dnf_body=f'cat "$TARGET_ROOT"/etc/yum.repos.d/*.repo > {tmp_path}/seen')
    run([S], env=env)
    seen = (tmp_path / "seen").read_text()
    assert "baseurl=https://rpm.rancher.io/rke2/stable/1.34/centos/9/x86_64" in seen
    assert "baseurl=https://rpm.rancher.io/rke2/stable/common/centos/9/noarch" in seen

def test_missing_site_env_exits_2(fake_bin, tmp_path):
    env, _, _ = env_for(fake_bin, tmp_path); del env["SITE_ENV"]
    assert run([S], env=env).returncode == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest -q tests/test_install_rke2.py` — Expected: FAIL.

- [ ] **Step 3: Implement `install-rke2.sh`**

- [ ] **Step 4: Run tests and lint**

Run: `make test && make lint` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: build-time RKE2 install with version fact"
```

---

### Task 4: Static node configuration and root-filesystem growth

**Files:**
- Create under `node/rootfs/`: `usr/lib/NetworkManager/conf.d/90-rke2-cni.conf`, `usr/lib/sysusers.d/rke2-etcd.conf`, `usr/lib/tmpfiles.d/rke2-node.conf`, `etc/sudoers.d/10-wheel-nopasswd`, `etc/cloud/cloud.cfg.d/90-rke2-node.cfg`, `usr/lib/systemd/system/rke2-node-growroot.service`, `usr/libexec/rke2-node/growroot`
- Test: `tests/test_node_rootfs.py`, `tests/test_growroot.py`

**Interfaces:**
- Produces: `rke2-node-growroot.service`, which Task 7 enables.
- Produces: `growroot`, no arguments. Environment, for tests only: `SYSFS_ROOT` (default `/sys`) and `SYSTEMD_GROWFS` (default `/usr/lib/systemd/systemd-growfs`).

Exact contents:

- `90-rke2-cni.conf`: section `[keyfile]`, one line `unmanaged-devices=interface-name:cali*;interface-name:flannel*;interface-name:vxlan.calico;interface-name:vxlan-v6.calico;interface-name:tunl*;interface-name:cilium*;interface-name:lxc*`
- `rke2-etcd.conf`: `u etcd - "etcd user" - /usr/sbin/nologin`
- `rke2-node.conf`: `d /var/lib/rancher 0755 root root -` and `d /var/lib/rancher/rke2 0755 root root -`
- `10-wheel-nopasswd`: `%wheel ALL=(ALL) NOPASSWD: ALL`
- `90-rke2-node.cfg` (YAML):
  ```yaml
  datasource_list: [OVF, VMware, None]
  disable_vmware_customization: false
  users: []
  disable_root: true
  ssh_pwauth: false
  preserve_hostname: false
  growpart:
    mode: "off"
  resize_rootfs: false
  ```
- `rke2-node-growroot.service`: `Type=oneshot`, `RemainAfterExit=yes`, `MountFlags=slave`, `ExecStart=/usr/libexec/rke2-node/growroot`, `After=local-fs.target`, `Before=rke2-server.service rke2-agent.service`, `WantedBy=multi-user.target`.

`growroot` behaviour: `dev=$(findmnt -vno SOURCE /sysroot)`; parent disk is `lsblk -no PKNAME "$dev"`; partition number is the content of `$SYSFS_ROOT/class/block/<basename dev>/partition`; run `growpart /dev/<parent> <n>`, treating exit status 1 (nothing to grow) as success and any other non-zero as failure; then `mount -o remount,rw /sysroot`; then `"$SYSTEMD_GROWFS" /sysroot`. If the backing device has no `partition` file (for example LVM), print a message naming the device to stderr and exit 0 without changing anything.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_node_rootfs.py
import yaml
from conftest import REPO
R = REPO / "node/rootfs"

def test_cloud_init_config():
    cfg = yaml.safe_load((R / "etc/cloud/cloud.cfg.d/90-rke2-node.cfg").read_text())
    assert cfg["datasource_list"] == ["OVF", "VMware", "None"]
    assert cfg["disable_vmware_customization"] is False
    assert cfg["users"] == [] and cfg["ssh_pwauth"] is False
    assert cfg["growpart"] == {"mode": "off"} and cfg["resize_rootfs"] is False

def test_networkmanager_ignores_cni_interfaces():
    text = (R / "usr/lib/NetworkManager/conf.d/90-rke2-cni.conf").read_text()
    for name in ("cali*", "flannel*", "cilium*", "vxlan.calico"):
        assert f"interface-name:{name}" in text

def test_etcd_user_and_sudo_and_tmpfiles():
    assert (R / "usr/lib/sysusers.d/rke2-etcd.conf").read_text().startswith("u etcd ")
    assert (R / "etc/sudoers.d/10-wheel-nopasswd").read_text().strip() == "%wheel ALL=(ALL) NOPASSWD: ALL"
    assert "d /var/lib/rancher/rke2 0755 root root -" in (R / "usr/lib/tmpfiles.d/rke2-node.conf").read_text()

def test_growroot_unit():
    unit = (R / "usr/lib/systemd/system/rke2-node-growroot.service").read_text()
    for line in ("Type=oneshot", "MountFlags=slave", "ExecStart=/usr/libexec/rke2-node/growroot",
                 "Before=rke2-server.service rke2-agent.service"):
        assert line in unit
```

```python
# tests/test_growroot.py
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
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest -q tests/test_node_rootfs.py tests/test_growroot.py` — Expected: FAIL.

- [ ] **Step 3: Create the files and implement `growroot`**

- [ ] **Step 4: Run tests and lint** — `make test && make lint` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: node configuration files and root filesystem growth"
```

---

### Task 5: Generalize command

**Files:**
- Create: `node/rootfs/usr/sbin/rke2-node-generalize`, `tests/test_generalize.py`

**Interfaces:**
- Produces: `rke2-node-generalize [--no-poweroff]`. Environment `GENERALIZE_ROOT` (default empty; prefix for every path it touches, used by tests).

Behaviour: must run as root (skip this check when `GENERALIZE_ROOT` is set). **Refuse first:** if `$ROOT/var/lib/rancher/rke2/agent` or `$ROOT/var/lib/rancher/rke2/server` exists, print `refusing: this node has already run RKE2` to stderr and exit 1 before changing anything. Otherwise: run `cloud-init clean --logs`; truncate `$ROOT/etc/machine-id` to empty (keep the file); remove `$ROOT/var/lib/dbus/machine-id`, `$ROOT/etc/ssh/ssh_host_*`, `$ROOT/var/lib/systemd/random-seed`, everything in `$ROOT/etc/NetworkManager/system-connections/`, `$ROOT/var/lib/NetworkManager/*.lease`, `$ROOT/root/.bash_history`, `$ROOT/var/home/*/.bash_history`; run `journalctl --rotate` and `journalctl --vacuum-time=1s`; then `systemctl poweroff` unless `--no-poweroff`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest -q tests/test_generalize.py` — Expected: FAIL.
- [ ] **Step 3: Implement `rke2-node-generalize`**
- [ ] **Step 4: Run tests and lint** — `make test && make lint` — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: generalize command for template preparation"
```

---

### Task 6: Site layer

**Files:**
- Create: `node/scripts/apply-site.sh`, `site/README.md`, `site/common/packages.txt`, `site/common/units.txt`, `site/common/rootfs/etc/chrony.d/site.conf.example`, `site/dev/packages.txt`, `site/dev/units.txt`, `site/dev/rootfs/.gitkeep`, `site/prod/packages.txt`, `site/prod/units.txt`, `site/prod/rootfs/.gitkeep`, `tests/test_apply_site.py`

**Interfaces:**
- Produces: `apply-site.sh <site_dir> <env>`. `env` must be `dev` or `prod` and `<site_dir>/<env>` must exist, otherwise exit 2. Environment `SITE_TARGET_ROOT` (default `/`).
- Produces: the site layer layout. Each part (`common`, then the environment) may hold `rootfs/` (copied verbatim over the image root), `packages.txt` (one package per line), `units.txt` (one systemd unit per line). In both text files `#` starts a comment and blank lines are ignored. A file `site/common/opt-in-image` (empty marker) makes `/opt` a real directory in the image.

Behaviour, in order:
1. **Secret guard.** Scan every file under `<site_dir>/common` and `<site_dir>/<env>`. If any file contains `PRIVATE KEY-----`, or is named `auth.json`, or is named `*.key`, print `secret-looking file in site layer: <path>` to stderr and exit 3 before changing anything.
2. If `common/opt-in-image` exists: replace the `$ROOT/opt` symlink with a real directory, create the symlink `$ROOT/opt/cni -> ../var/opt/cni`, and append `d /var/opt/cni 0755 root root -` to `$ROOT/usr/lib/tmpfiles.d/rke2-node-opt.conf`. (RKE2's network plugins write to `/opt/cni` at runtime, so that path must stay writable.)
3. For `common` then `<env>`: copy `rootfs/` over `$ROOT` (skip `.gitkeep` and `*.example`).
4. Run `update-ca-trust` once, only if either part's `rootfs/etc/pki/ca-trust/source/anchors/` contains a file.
5. For `common` then `<env>`: if `packages.txt` lists anything, run one `dnf -y install <packages…>`.
6. For `common` then `<env>`: if `units.txt` lists anything, run one `systemctl enable <units…>`.

`site/README.md` explains the layout, the rules from the spec's "Site layer" section, the NTP example (rename `site.conf.example` to `site.conf`, put `server ntp1.example.com iburst` lines in it), internal CA placement, replacing default repository files through `rootfs/etc/yum.repos.d/`, and the `/opt` marker with the warning that software needing to write under `/opt` at runtime needs its own symlink into `/var/opt`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_apply_site.py
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
    assert calls(log)[0] == "update-ca-trust"

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
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest -q tests/test_apply_site.py` — Expected: FAIL.
- [ ] **Step 3: Implement `apply-site.sh` and create the `site/` tree and README**
- [ ] **Step 4: Run tests and lint** — `make test && make lint` — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: site layer with secret guard"
```

---

### Task 7: Node image build

**Files:**
- Create: `node/Containerfile`, `tests/test_node_containerfile.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: `install-rke2.sh` (Task 3), `node/rootfs/` (Tasks 4–5), `apply-site.sh` and `site/` (Task 6).
- Produces: Makefile variables `ENV ?= dev` (`dev` selects `ROCKY_BASE_IMAGE`, `prod` selects `RHEL_BASE_IMAGE`; anything else is an error) and `BUILD` (required for `node`, `push`, `vmdk`; error text `BUILD is required`).
- Produces: `NODE_REF = $(REGISTRY)/rke2-node-$(ENV):<tag>.$(BUILD)`, where `<tag>` is `rke2-version.sh tag $(RKE2_VERSION)`. `make print-ref` prints it. Targets `node` (build `NODE_REF`) and `push`.

Build arguments: `BASE_IMAGE`, `RKE2_VERSION`, `SITE_ENV` (all required, no defaults), `RANCHER_RPM_BASEURL`, `RKE2_CHANNEL`, `EL_MAJOR`. Build context is the project root. The Containerfile, in this order:

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG RKE2_VERSION
ARG SITE_ENV
ARG RANCHER_RPM_BASEURL=https://rpm.rancher.io
ARG RKE2_CHANNEL=stable
ARG EL_MAJOR=9

RUN dnf -y install open-vm-tools cloud-init cloud-utils-growpart chrony sudo \
      python3 python3-libselinux python3-dnf \
 && (dnf -y remove firewalld || true)

RUN --mount=type=bind,source=node/scripts,target=/run/build \
    RKE2_VERSION="${RKE2_VERSION}" SITE_ENV="${SITE_ENV}" RANCHER_RPM_BASEURL="${RANCHER_RPM_BASEURL}" \
    RKE2_CHANNEL="${RKE2_CHANNEL}" EL_MAJOR="${EL_MAJOR}" /run/build/install-rke2.sh

COPY node/rootfs/ /
RUN chmod 0440 /etc/sudoers.d/10-wheel-nopasswd \
 && chmod 0755 /usr/sbin/rke2-node-generalize /usr/libexec/rke2-node/growroot

RUN --mount=type=bind,source=node/scripts,target=/run/build \
    --mount=type=bind,source=site,target=/run/site \
    /run/build/apply-site.sh /run/site "${SITE_ENV}"

RUN systemctl enable vmtoolsd.service sshd.service chronyd.service rke2-node-growroot.service \
 && for u in cloud-init-local cloud-init cloud-init-network cloud-config cloud-final; do \
      if [ -e "/usr/lib/systemd/system/$u.service" ]; then systemctl enable "$u.service"; fi; done \
 && systemctl mask bootc-fetch-apply-updates.timer bootc-generic-growpart.service

RUN dnf clean all && rm -rf /var/cache/* /var/log/* /var/lib/dnf
RUN bootc container lint
```

The cloud-init unit loop exists because the network-stage unit was renamed between cloud-init releases. `bootc-generic-growpart.service` is masked so that only `rke2-node-growroot` resizes the disk.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest -q tests/test_node_containerfile.py` — Expected: FAIL.
- [ ] **Step 3: Write `node/Containerfile` and the Makefile targets**
- [ ] **Step 4: Run tests and lint** — `make test && make lint` — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: RKE2 node image build"
```

---

### Task 8: VMDK and template build

**Files:**
- Create: `disk/config.toml.example`, `disk/build-vmdk.sh`, `disk/make-template.sh`, `tests/test_disk.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: `NODE_REF` from Task 7.
- Produces: `disk/build-vmdk.sh <image-ref> <config.toml> <output-dir>`. Environment `BIB_IMAGE` (default `quay.io/centos-bootc/bootc-image-builder:latest`), `CONTAINER_TOOL` (default `podman`). Writes `<output-dir>/vmdk/disk.vmdk`.
- Produces: `disk/make-template.sh --vmdk PATH --name NAME --datastore DS --network NET [--guest-id ID] [--hw-version V] [--cpus N] [--memory-mb N] [--pool POOL] [--folder F] [--boot-once]`. Defaults: guest id `rhel9_64Guest`, 4 CPUs, 8192 MB. Uses `govc` and its standard `GOVC_*` environment for the connection.
- Produces: Makefile targets `vmdk` (`build-vmdk.sh $(NODE_REF) disk/config.toml output/$(ENV)`) and `template` (passes `VMDK`, `NAME`, `DATASTORE`, `NETWORK`, `GUEST_ID` through).

`build-vmdk.sh` refuses, with exit 2 and a message, an image reference that: has no registry host (first path component has neither `.` nor `:`), starts with `localhost/`, has no tag, or has the tag `latest`. It also exits 2 if the config file does not exist. Otherwise it runs `$CONTAINER_TOOL pull <ref>` and then:

```
$CONTAINER_TOOL run --rm --privileged --pull=newer --security-opt label=type:unconfined_t \
  -v <abs config>:/config.toml:ro -v <abs output>:/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  $BIB_IMAGE --type vmdk --rootfs xfs --config /config.toml <ref>
```

`make-template.sh` runs, in order: `govc import.vmdk -ds DS [-pool POOL] VMDK NAME`; `govc vm.create -on=false -g ID -firmware efi -disk.controller pvscsi -net NET -net.adapter vmxnet3 -c N -m N -ds DS [-pool POOL] [-folder F] [-version V] -disk NAME/<vmdk basename> NAME`; `govc vm.change -vm NAME -e disk.EnableUUID=TRUE`. Without `--boot-once` it then runs `govc vm.markastemplate NAME`. With `--boot-once` it does not mark the template; it prints the remaining manual steps (power on, log in, `sudo rke2-node-generalize`, wait for power-off, `govc vm.markastemplate NAME`). It never powers a VM on itself.

`config.toml.example`:

```toml
[[customizations.user]]
name = "ansible"
key = "ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY ansible"
groups = ["wheel"]

[[customizations.filesystem]]
mountpoint = "/"
minsize = "60 GiB"
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_disk.py
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
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest -q tests/test_disk.py` — Expected: FAIL.
- [ ] **Step 3: Implement both scripts, the example config and the Makefile targets**
- [ ] **Step 4: Run tests and lint** — `make test && make lint` — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: VMDK and vCenter template build"
```

---

### Task 9: Node survey

**Files:**
- Create: `hack/node-survey.sh`, `hack/survey-diff.sh`, `tests/test_node_survey.py`
- Modify: `Makefile`

**Interfaces:**
- Produces: `hack/node-survey.sh`, no arguments, read-only, writes the report to stdout. Environment `SURVEY_ROOT` (default empty; prefix for every file it reads). Must work on an old-style node without bootc. A missing command or file produces an empty section, never a failure; the script always exits 0.
- Produces: report sections, each introduced by a line `## <name>`, in this order: `os-release`, `rke2-version`, `packages-all`, `packages-userinstalled`, `enabled-units`, `time-sync`, `rke2-config-keys`, `rke2-env-keys`, `kernel-modules`, `sysctl-files`, `ca-trust-anchors`, `opt-contents`, `selinux`.
- Produces: `hack/survey-diff.sh <survey-file> <base-packages-file>` printing package names that are in the survey's `packages-all` section but not in the base list, sorted. `make survey-diff SURVEY=<file>` generates the base list with `$(CONTAINER_TOOL) run --rm $(ROCKY_BASE_IMAGE) rpm -qa --qf '%{NAME}\n'` and calls it.

Section sources: `os-release` → `NAME`, `VERSION_ID` lines of `/etc/os-release`. `rke2-version` → `rke2 --version` first line. `packages-all` → `rpm -qa --qf '%{NAME}\n' | sort -u`. `packages-userinstalled` → `dnf repoquery --userinstalled --qf '%{name}'`. `enabled-units` → `systemctl list-unit-files --state=enabled --no-legend`, first column. `time-sync` → lines beginning `server`, `pool` or `sourcedir` from `/etc/chrony.conf` and `/etc/chrony.d/*.conf`. `rke2-config-keys` → **top-level keys only** of `/etc/rancher/rke2/config.yaml` and `/etc/rancher/rke2/config.yaml.d/*.yaml`. `rke2-env-keys` → variable names only from `/etc/default/rke2-*` and `/etc/sysconfig/rke2-*`. `kernel-modules` → first column of `lsmod`. `sysctl-files` → names and contents of `/etc/sysctl.d/*.conf`. `ca-trust-anchors` → file names in `/etc/pki/ca-trust/source/anchors/`. `opt-contents` → names directly under `/opt`. `selinux` → `getenforce`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_node_survey.py
from conftest import REPO, run
S, D = str(REPO / "hack/node-survey.sh"), str(REPO / "hack/survey-diff.sh")
SECRET = "K10deadbeef::server:s3cr3t-join-token"

def node(tmp_path):
    root = tmp_path / "root"
    for d in ("etc/rancher/rke2/config.yaml.d", "etc/default", "etc/chrony.d", "opt/falcon", "etc/sysctl.d"):
        (root / d).mkdir(parents=True)
    (root / "etc/os-release").write_text('NAME="Rocky Linux"\nVERSION_ID="9.4"\nPRETTY_NAME="x"\n')
    (root / "etc/rancher/rke2/config.yaml").write_text(
        f"token: {SECRET}\nserver: https://10.0.0.5:9345\ncni: canal\nkubelet-arg:\n  - max-pods=200\n")
    (root / "etc/default/rke2-server").write_text("HTTPS_PROXY=http://user:hunter2@proxy:3128\n")
    (root / "etc/chrony.conf").write_text("server ntp1.corp.example iburst\ndriftfile /var/lib/chrony/drift\n")
    return root

def survey(fake_bin, tmp_path, commands=None):
    env, _ = fake_bin(commands if commands is not None else {
        "rpm": "printf 'bash\\nfalcon-sensor\\n'", "dnf": "echo falcon-sensor", "systemctl": "echo 'auditd.service enabled'",
        "rke2": "echo 'rke2 version v1.31.4+rke2r1 (abc)'", "lsmod": "printf 'Module Size\\nbr_netfilter 1\\n'",
        "getenforce": "echo Enforcing"})
    env["SURVEY_ROOT"] = str(node(tmp_path))
    return run([S], env=env)

def test_sections_in_order(fake_bin, tmp_path):
    out = survey(fake_bin, tmp_path).stdout
    names = [l[3:] for l in out.splitlines() if l.startswith("## ")]
    assert names == ["os-release", "rke2-version", "packages-all", "packages-userinstalled", "enabled-units",
                     "time-sync", "rke2-config-keys", "rke2-env-keys", "kernel-modules", "sysctl-files",
                     "ca-trust-anchors", "opt-contents", "selinux"]

def test_reports_what_the_image_must_match(fake_bin, tmp_path):
    out = survey(fake_bin, tmp_path).stdout
    for expected in ("v1.31.4+rke2r1", "falcon-sensor", "server ntp1.corp.example iburst", "token", "cni",
                     "kubelet-arg", "HTTPS_PROXY", "falcon", "Enforcing"):
        assert expected in out

def test_never_prints_config_values(fake_bin, tmp_path):
    out = survey(fake_bin, tmp_path).stdout
    for leaked in (SECRET, "s3cr3t", "10.0.0.5", "hunter2", "max-pods"):
        assert leaked not in out

def test_survives_missing_commands_and_files(fake_bin, tmp_path):
    env, _ = fake_bin({}); env["SURVEY_ROOT"] = str(tmp_path / "empty")
    r = run([S], env=env)
    assert r.returncode == 0 and r.stdout.count("## ") == 13

def test_survey_diff(fake_bin, tmp_path):
    report = tmp_path / "s.txt"; report.write_text(survey(fake_bin, tmp_path).stdout)
    base = tmp_path / "base.txt"; base.write_text("bash\n")
    assert run([D, str(report), str(base)]).stdout.split() == ["falcon-sensor"]
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest -q tests/test_node_survey.py` — Expected: FAIL.
- [ ] **Step 3: Implement both scripts and the Makefile target**
- [ ] **Step 4: Run tests and lint** — `make test && make lint` — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: existing-node survey scripts"
```

---

### Task 10: Inventory example and rolling-update playbook

**Files:**
- Create: `ansible/inventory.example/hosts.yml`, `ansible/inventory.example/group_vars/all.yml`, `ansible/rolling-update.yml`, `ansible/tasks/update-node.yml`, `tests/test_ansible.py`

**Interfaces:**
- Consumes: the fact `ansible_local.rke2_image.rke2_version` (Task 3); inventory groups `rke2_servers` and `rke2_agents` (the role's names).
- Produces: `ansible-playbook -i <inventory> ansible/rolling-update.yml -e target_image=<ref>`.
- Playbook variables: `target_image` (required); `registry_auth_json` (optional; the full content of an `auth.json`, from Ansible Vault); `agent_batch` (default `1`); `drain_timeout` (default `600`, seconds); `ready_timeout` (default `600`); `k8s_node_name` (default `{{ ansible_facts.nodename | lower }}`); `kubectl` (default `/var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml`); `update_marker` (fixed: `rke2-bootc.io/update-in-progress`).

`group_vars/all.yml` sets `ansible_user: ansible`, `rke2_install_version` equal to `RKE2_VERSION` in `versions.env` with a comment that it must equal the image's version, and shows the role's `rke2_common_yum_repo` and `rke2_versioned_yum_repo` with their default `rpm.rancher.io` URLs and a commented internal-mirror alternative. `hosts.yml` has three servers and two agents with placeholder names.

`rolling-update.yml` has three plays, all with `become: true`, `any_errors_fatal: true`, `max_fail_percentage: 0`:

1. **Preflight**, `hosts: rke2_servers:rke2_agents`, no changes made. Asserts: `target_image` is defined, contains a tag, and the tag is not `latest`; `/run/ostree-booted` exists and `bootc status --format=json` succeeds on every host (failure message contains `not a bootc node`); `groups['rke2_servers'] | length >= 1`.
2. **Control plane**, `hosts: rke2_servers`, `serial: 1`, includes `tasks/update-node.yml` with `is_server: true`.
3. **Workers**, `hosts: rke2_agents`, `serial: "{{ agent_batch }}"`, includes `tasks/update-node.yml` with `is_server: false`.

`tasks/update-node.yml`, in this order (each bullet is one named task; names are fixed because the test checks them):

1. `Read bootc status` — `bootc status --format=json`; the booted image is `.status.booted.image.image.image`.
2. `Choose kubectl host` — `kube_host` is the first host in `rke2_servers` that is not this host, or this host if there is no other.
3. `Read update marker` — on `kube_host`, read the node annotation `update_marker`.
4. `Write registry credentials` — when `registry_auth_json` is defined: write `/etc/ostree/auth.json`, mode `0600`, `no_log: true`.
5. `Stage target image` — `bootc switch {{ target_image }}`; only when the booted image differs from `target_image`.
6. `Mark and cordon node` — on `kube_host`: annotate the node with `update_marker={{ target_image }}`, then `kubectl cordon`; only when the booted image differs.
7. `Drain node` — on `kube_host`: `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout={{ drain_timeout }}s`; same condition.
8. `Reboot into new image` — `ansible.builtin.reboot`, `reboot_timeout: 900`; same condition.
9. `Wait for node Ready` — on `kube_host`: `kubectl wait --for=condition=Ready node/<node> --timeout={{ ready_timeout }}s`; when the node was updated in this run **or** carries the marker.
10. `Wait for API server and etcd` — on this host: `kubectl get --raw /readyz`, retried; when `is_server` and the condition of step 9.
11. `Verify booted image` — re-read `bootc status`; fail unless the booted image equals `target_image`; when the condition of step 9.
12. `Uncordon and clear marker` — on `kube_host`: `kubectl uncordon`, then remove the annotation; when the condition of step 9.

A node that already runs `target_image` and has no marker is skipped entirely (steps 5–12 do not run). A node that runs `target_image` and still has the marker is finished by steps 9–12. A node cordoned without the marker is never uncordoned by this playbook.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ansible.py
import yaml
from conftest import REPO, run
A = REPO / "ansible"
load = lambda p: yaml.safe_load((A / p).read_text())

def test_inventory_version_matches_versions_env():
    env = dict(l.split("=", 1) for l in (REPO / "versions.env").read_text().split())
    assert load("inventory.example/group_vars/all.yml")["rke2_install_version"] == env["RKE2_VERSION"]

def test_inventory_groups_use_role_names():
    text = (A / "inventory.example/hosts.yml").read_text()
    assert "rke2_servers" in text and "rke2_agents" in text

def test_play_order_and_safety_settings():
    plays = load("rolling-update.yml")
    assert [p["hosts"] for p in plays] == ["rke2_servers:rke2_agents", "rke2_servers", "rke2_agents"]
    assert plays[1]["serial"] == 1 and plays[2]["serial"] == "{{ agent_batch }}"
    for p in plays:
        assert p["any_errors_fatal"] is True and p["max_fail_percentage"] == 0

def test_preflight_refuses_non_bootc_nodes_and_moving_tags():
    text = (A / "rolling-update.yml").read_text()
    assert "not a bootc node" in text and "/run/ostree-booted" in text and "latest" in text

def test_update_node_task_order():
    names = [t["name"] for t in load("tasks/update-node.yml")]
    assert names == ["Read bootc status", "Choose kubectl host", "Read update marker",
                     "Write registry credentials", "Stage target image", "Mark and cordon node", "Drain node",
                     "Reboot into new image", "Wait for node Ready", "Wait for API server and etcd",
                     "Verify booted image", "Uncordon and clear marker"]

def test_credentials_are_not_logged():
    task = next(t for t in load("tasks/update-node.yml") if t["name"] == "Write registry credentials")
    assert task["no_log"] is True and "registry_auth_json is defined" in str(task["when"])

def test_interrupted_node_is_finished_but_operator_cordon_is_respected():
    tasks = {t["name"]: t for t in load("tasks/update-node.yml")}
    for name in ("Wait for node Ready", "Verify booted image", "Uncordon and clear marker"):
        assert "marker" in str(tasks[name]["when"])          # runs for a node left mid-update
    for name in ("Stage target image", "Drain node", "Reboot into new image"):
        assert "marker" not in str(tasks[name]["when"])      # never re-done for a node already on target
    assert "update_marker" in str(tasks["Mark and cordon node"])

def test_syntax_and_lint():
    r = run([str(REPO / ".venv/bin/ansible-playbook"), "--syntax-check", "-i", "ansible/inventory.example/hosts.yml",
             "ansible/rolling-update.yml", "-e", "target_image=r.example.com/x:v1"])
    assert r.returncode == 0, r.stderr
    assert run([str(REPO / ".venv/bin/ansible-lint"), "ansible/"]).returncode == 0
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest -q tests/test_ansible.py` — Expected: FAIL.
- [ ] **Step 3: Write the inventory example, the playbook and the task file**

Use only `ansible.builtin` modules; `kubectl` and `bootc` are called with `ansible.builtin.command`, with `changed_when` set honestly on each.

- [ ] **Step 4: Run tests and lint** — `make test && make lint` — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: inventory example and rolling-update playbook"
```

---

### Task 11: Documentation and runbooks

**Files:**
- Create: `README.md`, `docs/verification-checklist.md`, `docs/migration.md`, `docs/operations.md`, `tests/test_docs.py`

**Interfaces:**
- Consumes: every Makefile target and script path from Tasks 1–10.

Contents:

- `README.md`: what the project is; build host requirements (Linux, rootful podman, `govc`; for prod a subscribed RHEL host logged in to `registry.redhat.io`); the end-to-end flow as commands (`make rocky-base`, `make node ENV=dev BUILD=1`, `make push`, copy and edit `disk/config.toml`, `make vmdk`, `make template`, clone, run `rke2-ansible`); where versions are set; links to the other documents; a plain statement that nothing here was built or booted by its author and that `docs/verification-checklist.md` is the proof.
- `docs/verification-checklist.md`: a checkbox list. It opens with a section titled `## Three experiments first` containing, in this order, `### 1. Rocky base builds and boots`, `### 2. The role's install step is a no-op`, `### 3. Customization spec on a clone`, each with the exact commands, the expected result, and what to do on failure. Experiment 2's failure instruction is "stop and report; the options are a small change to the role or the tarball install". Experiment 3 covers both a never-booted template and the `--boot-once` path, and names per-clone `guestinfo` metadata as the alternative. Then `## Full checklist` with every item from the spec's Verification section, plus: `/opt/cni/bin` is populated and no SELinux denials (`ausearch -m avc -ts boot`), root filesystem grows after enlarging a clone's disk, `systemctl is-enabled bootc-fetch-apply-updates.timer` prints `masked`.
- `docs/migration.md`: the spec's four-step migration procedure as a runbook, including running `hack/node-survey.sh` and `make survey-diff`, setting `RKE2_VERSION` and `rke2_install_version` to the cluster's current version, the one-at-a-time rule, keeping etcd quorum, updating the API address, and the rule that RKE2 upgrades wait until the cluster is all bootc.
- `docs/operations.md`: building and promoting a new image; running the rolling update (with `--limit` for a mixed cluster); what a failed run leaves behind (one node cordoned with the `rke2-bootc.io/update-in-progress` annotation) and that re-running finishes it; rollback with `bootc rollback` and reboot; RKE2 version rules (control plane first, one minor version at a time); where pull credentials live (`/etc/ostree/auth.json`, delivered from Ansible Vault).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_docs.py
import re
from conftest import REPO, run
DOCS = ["README.md", "docs/verification-checklist.md", "docs/migration.md", "docs/operations.md", "site/README.md"]

def test_every_make_target_mentioned_exists():
    targets = set(re.findall(r"^([a-z][a-z-]*):", (REPO / "Makefile").read_text(), re.M))
    for doc in DOCS:
        for t in re.findall(r"\bmake ([a-z][a-z-]*)", (REPO / doc).read_text()):
            assert t in targets, f"{doc}: make {t}"

def test_every_repo_path_mentioned_exists():
    for doc in DOCS:
        for p in re.findall(r"`((?:node|disk|hack|ansible|site|base|docs)/[\w./-]+)`", (REPO / doc).read_text()):
            assert (REPO / p).exists() or p == "disk/config.toml", f"{doc}: {p}"

def test_checklist_opens_with_the_three_experiments():
    text = (REPO / "docs/verification-checklist.md").read_text()
    order = [text.index(h) for h in ("## Three experiments first", "### 1. Rocky base builds and boots",
             "### 2. The role's install step is a no-op", "### 3. Customization spec on a clone", "## Full checklist")]
    assert order == sorted(order)
    assert "stop and report" in text and "guestinfo" in text and "masked" in text

def test_readme_states_what_is_unproven():
    assert "verification-checklist.md" in (REPO / "README.md").read_text()

def test_whole_suite_and_lint_pass():
    assert run(["make", "lint"]).returncode == 0
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest -q tests/test_docs.py` — Expected: FAIL.
- [ ] **Step 3: Write the four documents**
- [ ] **Step 4: Run the full suite** — `make test && make lint` — Expected: PASS, no skips.
- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "docs: README, verification checklist, migration and operations runbooks"
```
