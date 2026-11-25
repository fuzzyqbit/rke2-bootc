# RKE2 node images for VMware, built with bootc

This project builds one bootable container image that holds the operating
system and RKE2 together, turns it into a VMware disk and vCenter template, and
updates running nodes to a new image one node at a time.

- Dev nodes run Rocky Linux 9. Prod nodes run RHEL 9. Both come from the same
  recipe; they differ only in the base image and in `site/dev` versus
  `site/prod`.
- Every node, control plane or worker, is a clone of the same template. The
  Ansible inventory decides what a node becomes.
- The cluster is deployed with the `rancherfederal/rke2-ansible` role, used
  unmodified.

## Status: nothing here has been built or booted

The author of this repository has not built any image, booted any VM or run any
playbook against a real system. The code passes its static tests and linters
(`make test` and `make lint`), and that is all that has been checked.

[`docs/verification-checklist.md`](docs/verification-checklist.md) is the proof.
Run its three experiments first and do not rely on anything else here until
they pass.

## Terms

- **bootc**: a way of running Linux where the whole operating system is a
  container image. A node boots from the image, `/usr` is read-only, and only
  `/etc` and `/var` are writable. An update downloads a new image and takes
  effect at the next reboot; the previous image stays on disk for
  `bootc rollback`.
- **Base image**: the bootc image the node image is built on. Rocky publishes
  no official bootc base, so this project builds one (`base/rocky/Containerfile`).
  For RHEL it is Red Hat's `registry.redhat.io/rhel9/rhel-bootc`.
- **Node image**: the base plus RKE2, VMware guest tools, cloud-init and the
  site layer (`node/Containerfile`).
- **Site layer**: your organisation's packages, files and settings, baked into
  the image at build time. See [`site/README.md`](site/README.md).
- **bootc-image-builder**: the tool that turns a node image in a registry into
  a VMDK disk.

## Repository layout

| Path | What it is |
|---|---|
| `versions.env` | The one place versions, image names and mirrors are set |
| `Makefile` | Every build step |
| `base/rocky/Containerfile` | Rocky Linux 9 bootc base image |
| `node/Containerfile` | The node image, built on either base |
| `node/scripts/install-rke2.sh` | Installs the pinned RKE2 RPMs at build time |
| `node/rootfs/` | Files copied into the node image (cloud-init, sudo, NetworkManager, root growth, generalize command) |
| `site/` | The site layer (empty except an NTP example) |
| `disk/build-vmdk.sh` | Builds a VMDK from a pushed node image |
| `disk/make-template.sh` | Imports the VMDK into vCenter and marks it as a template (`govc`) |
| `hack/node-survey.sh` | Read-only survey of an existing node, for migrations |
| `ansible/inventory.example/` | The `rke2-ansible` variables this image needs |
| `ansible/rolling-update.yml` | Moves a running cluster to a new image tag |

## Build host requirements

- Linux on x86_64.
- Rootful `podman` (run the build targets with `sudo`). `disk/build-vmdk.sh`
  runs bootc-image-builder privileged and reads root's container storage.
- `make` and `bash`.
- `govc`, configured for your vCenter (see step 8 below).
- Network access to the Rocky mirror (`ROCKY_MIRROR`), the Rancher RPM
  repository (`RANCHER_RPM_BASEURL`), your container registry (`REGISTRY`),
  and `quay.io` for the Rocky builder image and bootc-image-builder.
- For prod (RHEL) builds: a subscribed RHEL 9 build host, logged in to
  `registry.redhat.io`. The RHEL packages come from the host's subscription.
- Python 3 for `make test` and `make lint` (they create `.venv/`).

## Where versions are set

Everything is in `versions.env`:

| Variable | Meaning |
|---|---|
| `RKE2_VERSION` | RKE2 version baked into the image, form `v1.34.3+rke2r1` |
| `EL_MAJOR` | `9` |
| `REGISTRY` | Where node images are pushed, e.g. `registry.example.com/platform` |
| `ROCKY_BASE_IMAGE` | Name of the self-built Rocky base image |
| `RHEL_BASE_IMAGE` | Red Hat's RHEL bootc base image |
| `RANCHER_RPM_BASEURL` | Rancher RPM repository or your mirror |
| `RKE2_CHANNEL` | Rancher RPM channel, `stable` |
| `ROCKY_MIRROR` | Rocky package mirror used to build the base |

The shipped `RKE2_VERSION` is a placeholder. For a cluster you are migrating it
must be the version that cluster runs now (see
[`docs/migration.md`](docs/migration.md)). `rke2_install_version` in your
Ansible inventory must always equal it.

Node images are tagged `<registry>/rke2-node-<env>:<rke2 tag>.<build>`, for
example `registry.example.com/platform/rke2-node-dev:v1.34.3-rke2r1.7`. The
build number `BUILD` is yours to choose. Never reuse one: tags are meant to be
immutable, and the registry will not stop you overwriting a tag. To see the
reference a build will get:

```bash
make print-ref ENV=dev BUILD=1
```

## Pass `ENV` explicitly

`make` takes `ENV` from the command line and also from the environment. Some
shells export an unrelated `ENV` variable (for example `ENV=/etc/profile`);
then the node targets stop with `ENV must be dev or prod`. Always pass
`ENV=dev` or `ENV=prod` on the command line, as every command here does.

## End to end, for dev

Run these from the repository root.

1. Run the static checks.

   ```bash
   make test
   ```

   ```bash
   make lint
   ```

2. Edit `versions.env`: set `REGISTRY` and `ROCKY_BASE_IMAGE` to your
   registry, and `RKE2_VERSION` to the version you need.

3. Log in to your registry as root.

   ```bash
   sudo podman login registry.example.com
   ```

4. Build the Rocky base image, and push it so other hosts can use it.

   ```bash
   sudo make rocky-base
   ```

   ```bash
   sudo make rocky-base-push
   ```

5. Fill in the site layer as described in [`site/README.md`](site/README.md).
   It ships empty; an empty site layer builds.

6. Build and push the node image. `BUILD` is required.

   ```bash
   sudo make node ENV=dev BUILD=1
   ```

   ```bash
   sudo make push ENV=dev BUILD=1
   ```

   The build ends with `bootc container lint` and fails if lint reports errors.

7. Create the disk configuration and build the VMDK. The configuration holds
   the Ansible login user, its SSH public key and the root disk size; it is
   ignored by git and never goes into the image.

   ```bash
   cp disk/config.toml.example disk/config.toml
   ```

   Edit `disk/config.toml`: replace the SSH public key, and change the user name
   (default `ansible`) and root size (default `60 GiB`) if needed. Then:

   ```bash
   sudo make vmdk ENV=dev BUILD=1
   ```

   `make vmdk` builds from the registry reference (it pulls it first), because
   the disk remembers where it came from and `bootc` looks there for updates.
   It refuses `localhost/` references, references without a tag, and `latest`.
   The disk is written to `output/dev/vmdk/disk.vmdk`.

8. Create the vCenter template. `govc` reads its connection from the
   environment:

   ```bash
   export GOVC_URL=vcenter.example.com GOVC_USERNAME=user@vsphere.local GOVC_PASSWORD='...' GOVC_DATACENTER=dc1
   ```

   ```bash
   make template VMDK=output/dev/vmdk/disk.vmdk NAME=rke2-node-dev-v1.34.3-rke2r1.1 DATASTORE=datastore1 NETWORK='VM Network'
   ```

   This imports the disk, creates a powered-off VM with EFI firmware, a
   paravirtual SCSI controller, a VMXNET3 adapter, 4 CPUs, 8192 MB of memory
   and `disk.EnableUUID=TRUE`, and marks it as a template. The template is
   never booted, so each clone creates its own machine ID and SSH host keys on
   first boot. `GUEST_ID` defaults to `rhel9_64Guest`; pass `GUEST_ID=...` to
   change it.

   For the options `make template` does not pass (`--hw-version`, `--cpus`,
   `--memory-mb`, `--pool`, `--folder`, `--boot-once`), run
   `disk/make-template.sh` directly; run with no arguments, it prints its
   usage. `--boot-once` is for a vCenter that will not customize a template
   whose VMware Tools never ran; see experiment 3 in the checklist.

9. Clone one VM per node from the template, as you do today, with a vSphere
   guest customization spec for hostname and IP. Without one, the node uses
   DHCP. Per-clone `guestinfo` metadata is the alternative (see the
   checklist, experiment 3).

10. Deploy the cluster with `rke2-ansible`. Copy the example inventory, put the
    clones in `rke2_servers` and `rke2_agents`, and set
    `rke2_install_version` to the image's `RKE2_VERSION`:

    ```bash
    mkdir -p ~/inventories
    ```

    ```bash
    cp -r ansible/inventory.example ~/inventories/dev
    ```

    Edit `~/inventories/dev/hosts.yml` and `~/inventories/dev/group_vars/all.yml`,
    then run the
    `rke2-ansible` playbook with that inventory the way you run it today. The
    role's install step runs `dnf install rke2-server-<version>` (or agent);
    because the package is already in the image at that version, it should
    change nothing. The nodes must reach the Rancher RPM repository, or the
    mirror set in `rke2_common_yum_repo` and `rke2_versioned_yum_repo`, because
    `dnf` reads repository metadata even when it installs nothing. Do not use
    the role's own upgrade playbook; RKE2 upgrades are image upgrades.

## Prod (RHEL)

The same steps with `ENV=prod`, on a subscribed RHEL 9 host:

```bash
sudo podman login registry.redhat.io
```

```bash
sudo make node ENV=prod BUILD=1
```

```bash
sudo make push ENV=prod BUILD=1
```

Build the disk with Red Hat's image builder instead of the CentOS one that
`disk/build-vmdk.sh` uses by default:

```bash
sudo make vmdk ENV=prod BUILD=1 BIB_IMAGE=registry.redhat.io/rhel9/bootc-image-builder:latest
```

The prod build does not use `make rocky-base`. The output is
`output/prod/vmdk/disk.vmdk`.

## What is and is not in the image

In the image: RKE2 server and agent RPMs at the pinned version (both systemd
units disabled), `open-vm-tools`, `cloud-init`, `chrony`, `sudo`, Python 3 with
the SELinux and dnf bindings, passwordless sudo for `wheel`, the `etcd` user for
the CIS profile, NetworkManager rules that ignore CNI interfaces, root
filesystem growth at boot, the `rke2-node-generalize` command, and a fact file
`/etc/ansible/facts.d/rke2_image.fact` holding the RKE2 version. `firewalld` is
removed. `bootc-fetch-apply-updates.timer` is masked: no node updates or
reboots on its own. SELinux stays enforcing.

Never in the image: registry pull credentials, Red Hat credentials, SSH private
keys, cluster tokens. The login user and its public key come from
`disk/config.toml` at disk build time. Registry pull credentials are written to
`/etc/ostree/auth.json` by the rolling-update playbook from
`registry_auth_json`, which you keep in Ansible Vault. The site layer build
stops if it finds a file named `auth.json` or `*.key`, or a private key.

## Other documents

- [`docs/verification-checklist.md`](docs/verification-checklist.md): what to
  run on real infrastructure to prove this works. Start here.
- [`docs/migration.md`](docs/migration.md): moving an existing cluster onto
  bootc nodes.
- [`docs/operations.md`](docs/operations.md): building and promoting images,
  rolling updates, rollback, RKE2 upgrades, pull credentials.
- [`site/README.md`](site/README.md): filling in the site layer.
- [`docs/superpowers/specs/2026-11-25-rke2-bootc-vmware-design.md`](docs/superpowers/specs/2026-11-25-rke2-bootc-vmware-design.md):
  the design and its reasons.
