# Verification checklist

Nothing in this repository has been built or booted by its author. This
checklist is how you prove it works, on your build host and your dev vCenter.
Tick each box as it passes. When a step fails, note the exact error and the
command that produced it.

Commands assume the repository root as the working directory, a root-capable
user on the build host, and `govc` configured through `GOVC_URL`,
`GOVC_USERNAME`, `GOVC_PASSWORD` and `GOVC_DATACENTER`. The `govc vm.clone`
commands below may need `-ds`, `-pool` or `-folder` added for your vCenter.
Names such as `datastore1` and `VM Network` are examples. Load the names from
`versions.env` into your shell first:

```bash
set -a; . ./versions.env; set +a
```

Static checks first; these run anywhere:

- [ ] `make test` passes.
- [ ] `make lint` passes with no warnings.

## Three experiments first

The rest of the design rests on these three. Do not rely on anything else
until all three pass.

### 1. Rocky base builds and boots

The Rocky base is built with `bootc-base-imagectl build-rootfs --manifest=standard`
inside a CentOS Stream 9 bootc builder image, with the builder's repository
files replaced by Rocky's BaseOS and AppStream (`base/rocky/rocky.repo`). The
Rocky repository file and GPG key are written only in the builder stage, so
whether the final image has working Rocky repositories is not known.

Build:

```bash
sudo make rocky-base
```

`make rocky-base` runs `podman build` with the privileges that
`build-rootfs` needs inside the build (it runs rpm-ostree and bubblewrap):
`--cap-add=all --security-opt=label=type:container_runtime_t --device /dev/fuse`,
plus `--no-cache --pull=newer`. `make -n rocky-base` shows the full command.

Inspect the built image:

```bash
sudo podman run --rm "$ROCKY_BASE_IMAGE" cat /etc/os-release
```

```bash
sudo podman run --rm "$ROCKY_BASE_IMAGE" rpm -q rocky-release
```

```bash
sudo podman run --rm "$ROCKY_BASE_IMAGE" dnf repolist
```

```bash
sudo podman run --rm "$ROCKY_BASE_IMAGE" rpm -q bootc NetworkManager openssh-server dnf python3
```

```bash
sudo podman run --rm "$ROCKY_BASE_IMAGE" sh -c 'rpm -qa | grep -i centos'
```

Expected:

- [ ] The build finishes, including its last step `bootc container lint`.
- [ ] `/etc/os-release` names Rocky Linux 9.
- [ ] `rpm -q rocky-release` prints a package version (exit 0).
- [ ] `dnf repolist` lists Rocky Linux BaseOS and AppStream repositories.
- [ ] `bootc`, `NetworkManager`, `openssh-server`, `dnf` and `python3` are installed.
- [ ] The `grep -i centos` command prints nothing.

Boot it. The base has no VMware Tools and no login user, so it is booted as the
dev node image, which adds both. Push the base, then build the node image on it
and follow the README from step 6 to a template and one clone:

```bash
sudo make rocky-base-push
```

```bash
sudo make node ENV=dev BUILD=1
```

```bash
sudo make push ENV=dev BUILD=1
```

```bash
sudo make vmdk ENV=dev BUILD=1
```

```bash
make template VMDK=output/dev/vmdk/disk.vmdk NAME=rke2-exp1 DATASTORE=datastore1 NETWORK='VM Network'
```

```bash
govc vm.clone -vm rke2-exp1 -on=true rke2-exp1-clone
```

```bash
govc vm.ip -wait 10m rke2-exp1-clone
```

Log in with the key from `disk/config.toml` (user `ansible` unless you changed
it) and check:

```bash
ssh ansible@<clone-ip> 'cat /etc/os-release; getenforce; findmnt -no FSTYPE /sysroot; sudo bootc status'
```

- [ ] The clone boots and gets an address (DHCP, no customization spec yet).
- [ ] SSH login with the key works and `sudo` works without a password.
- [ ] `/etc/os-release` names Rocky Linux 9, `getenforce` prints `Enforcing`,
      the root filesystem is `xfs`, and `bootc status` shows the booted image.
- [ ] On the clone, `dnf repolist` lists the Rocky repositories.

On failure: stop and report. Do not switch dev to another distribution. Report
the failing command and its full output. In particular:

- If `build-rootfs` fails because the `standard` manifest names packages that
  exist only in CentOS (for example `centos-*` release or repository packages),
  stop and report: the manifest needs a Rocky variant, which is a design
  decision.
- If `build-rootfs` fails with a permission, namespace or mount error even
  with those privileges, stop and report the full output, the podman version
  and whether SELinux is enforcing on the build host.
- If the image builds but `rpm -q rocky-release` fails or `dnf repolist` shows
  no Rocky repositories, stop and report: the base image cannot install
  packages, and the node build (which runs `dnf install`) will fail.

### 2. The role's install step is a no-op

The unmodified `rancherfederal/rke2-ansible` role always runs an install step.
On RHEL-family systems it adds the Rancher repositories under
`/etc/yum.repos.d` and runs `dnf install rke2-server-<version>` (or
`rke2-agent-<version>`). On a bootc node `/usr` is read-only, so this only works
if `dnf` finds the exact package already installed and does nothing.

Use one fresh clone of the experiment 1 template, never touched by Ansible:

```bash
govc vm.clone -vm rke2-exp1 -on=true rke2-exp2
```

Make an inventory with that one clone in `rke2_servers` and no agents, and
`rke2_install_version` equal to `RKE2_VERSION` in `versions.env`:

```bash
mkdir -p ~/inventories
```

```bash
cp -r ansible/inventory.example ~/inventories/exp2
```

Replace `~/inventories/exp2/hosts.yml` with this content, using the clone's
address (from `govc vm.ip rke2-exp2`) or its DNS name:

```yaml
all:
  children:
    rke2_servers:
      hosts:
        rke2-exp2:
          ansible_host: 192.0.2.22
    rke2_agents:
      hosts: {}
```

Check `rke2_install_version` in `~/inventories/exp2/group_vars/all.yml`. The
commands below, and the later items that say `<inventory>`, use this
inventory (`~/inventories/exp2/hosts.yml`), extended with the further clones
they need.

The node must reach the Rancher RPM repository, or the mirror set in
`rke2_common_yum_repo` and `rke2_versioned_yum_repo`. `dnf` reads repository
metadata even when it installs nothing. Check from the node first, using the
`baseurl` of `rke2_versioned_yum_repo` from your inventory:

```bash
ansible -i ~/inventories/exp2/hosts.yml all -m ansible.builtin.uri -a 'url=https://rpm.rancher.io/rke2/stable/1.34/centos/9/x86_64/repodata/repomd.xml'
```

Record the installed RKE2 packages:

```bash
ansible -i ~/inventories/exp2/hosts.yml all -m command -a 'rpm -q rke2-server rke2-agent rke2-common'
```

Run the `rke2-ansible` playbook against that inventory the way you run it
today, keeping the full output.

Expected:

- [ ] The role's RPM install task reports `ok`, not `changed`.
- [ ] The playbook completes and the node becomes a Ready single-node cluster:
      `sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml get nodes`.
- [ ] `rpm -q rke2-server rke2-agent rke2-common` prints the same versions as
      before.
- [ ] `sudo bootc status` shows the same booted image as before and no staged
      or locally modified deployment.

Then show that a wrong version fails loudly. Run the same playbook again
against the same node, adding `-e rke2_install_version=<other>` to the command
line. Use a version that exists in the configured `rke2_versioned_yum_repo`
(another patch release of the same minor version), so that `dnf` really tries
to install it rather than failing to find it.

- [ ] The role's RPM install task fails with a clear `dnf` error (for example a
      read-only file system, or the package not being installable on a bootc
      system), and the playbook stops.
- [ ] Afterwards `rpm -q rke2-server` still prints the image's version.

On failure: stop and report; the options are a small change to the role or the
tarball install. This applies both when the matching run reports `changed` or
fails, and when the mismatching run succeeds or changes the installed
packages. Do not work around it.

### 3. Customization spec on a clone

This checks that a clone takes its hostname and static IP from a vSphere guest
customization spec, applied by cloud-init. The image's cloud-init uses the
`OVF` and `VMware` datasources with VMware customization enabled
(`node/rootfs/etc/cloud/cloud.cfg.d/90-rke2-node.cfg`).

The template from `make template` has never been booted, so vCenter has never
seen its VMware Tools running. vCenter may refuse a customization spec for
such a template. That is what the `--boot-once` path is for.

Create a Linux customization spec in vCenter with a fixed hostname and a
static IPv4 address (named `rke2-exp3` here). Then:

**A. Never-booted template**

```bash
govc vm.clone -vm rke2-exp1 -customization rke2-exp3 -on=true rke2-exp3a
```

```bash
ssh ansible@<static-ip> 'hostnamectl hostname; ip -4 addr; cloud-init status --long'
```

- [ ] vCenter accepts the clone with the spec.
- [ ] The hostname and IP address are the ones in the spec.
- [ ] `cloud-init status` reports `done` with no errors.
- [ ] SSH login as the Ansible user works.

**B. Template booted once, then generalized**

Do this if A was refused or the spec was not applied. It creates the VM but does
not mark it as a template:

```bash
make template VMDK=output/dev/vmdk/disk.vmdk NAME=rke2-exp3-tpl DATASTORE=datastore1 NETWORK='VM Network' TEMPLATE_ARGS=--boot-once
```

```bash
govc vm.power -on rke2-exp3-tpl
```

```bash
govc vm.ip -wait 10m rke2-exp3-tpl
```

Record its identity, then generalize it (the SSH session ends when it powers
off):

```bash
ssh ansible@<dhcp-ip> 'cat /etc/machine-id; ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'
```

```bash
ssh ansible@<dhcp-ip> sudo rke2-node-generalize
```

`rke2-node-generalize` clears cloud-init state and logs, empties
`/etc/machine-id`, removes `/etc/hostname`, SSH host keys, NetworkManager
connections and leases, `ifcfg-*` files under
`/etc/sysconfig/network-scripts`, the random seed and shell histories, rotates
the journal, and powers the VM off. It refuses to run on a node that has already run RKE2. Wait for the
power-off, then:

```bash
govc vm.markastemplate rke2-exp3-tpl
```

```bash
govc vm.clone -vm rke2-exp3-tpl -customization rke2-exp3 -on=true rke2-exp3b
```

- [ ] vCenter accepts the clone with the spec.
- [ ] Hostname and IP come from the spec; SSH login works.
- [ ] The clone's `/etc/machine-id` and SSH host key fingerprints differ from
      those the template had while booted.

**C. Alternative: per-clone `guestinfo` metadata**

If neither A nor B applies the spec, set hostname and network per clone through
`guestinfo` metadata, which cloud-init's VMware datasource reads. Clone without
powering on, set the metadata, then power on. Example `metadata.yaml`
(adjust names and addresses):

```yaml
instance-id: rke2-exp3c
local-hostname: rke2-exp3c
network:
  version: 2
  ethernets:
    nic0:
      match:
        name: "e*"
      addresses: [192.0.2.21/24]
      routes:
        - to: 0.0.0.0/0
          via: 192.0.2.1
      nameservers:
        addresses: [192.0.2.53]
```

```bash
govc vm.clone -vm rke2-exp1 -on=false rke2-exp3c
```

```bash
govc vm.change -vm rke2-exp3c -e guestinfo.metadata="$(base64 -w0 metadata.yaml)" -e guestinfo.metadata.encoding=base64
```

```bash
govc vm.power -on rke2-exp3c
```

- [ ] Hostname and IP come from the metadata; SSH login works.

Tip: if vCenter accepts the clone but the spec is not applied (cloud-init
falls back to DHCP and `/var/log/cloud-init.log` says the customization file
was not found in time), cloud-init may have given up waiting for it. Raise
`vmware_cust_file_max_wait` (seconds, default 15) with a cloud-init drop-in in
the site layer, file `rootfs/etc/cloud/cloud.cfg.d/95-site.cfg` in
`site/common/`, rebuild, and try again:

```yaml
datasource:
  VMware:
    vmware_cust_file_max_wait: 60
  OVF:
    vmware_cust_file_max_wait: 60
```

On failure: if A fails, use B. If B also fails, use C for every clone and
report it, because it changes how clones are made. If none works, stop and
report with the vCenter and ESXi versions and the cloud-init log
(`/var/log/cloud-init.log`).

Run experiment 3 again with a RHEL 9 (prod) clone once a prod template exists.

## Full checklist

### Images

- [ ] The dev image builds: `sudo make node ENV=dev BUILD=<n>`; the final
      `bootc container lint` step passes.
- [ ] The prod image builds on a subscribed RHEL host:
      `sudo make node ENV=prod BUILD=<n>`; `bootc container lint` passes.
- [ ] `make print-ref ENV=dev BUILD=<n>` prints a tag of the form
      `v<x.y.z>-rke2r<n>.<build>`, not `latest`.
- [ ] No credentials in either image. This prints nothing:
      `sudo podman run --rm <image-ref> sh -c 'ls /etc/ostree/auth.json /root/.ssh 2>/dev/null'`.
- [ ] The version fact is present:
      `sudo podman run --rm <image-ref> cat /etc/ansible/facts.d/rke2_image.fact`
      prints `rke2_version` equal to `RKE2_VERSION` and the right `site_env`.

### Disk, template and clones

- [ ] `sudo make vmdk ENV=dev BUILD=<n>` writes `output/dev/vmdk/disk.vmdk`.
- [ ] The prod disk builds with
      `sudo make vmdk ENV=prod BUILD=<n> BIB_IMAGE=registry.redhat.io/rhel9/bootc-image-builder:latest`.
- [ ] `make template ...` imports the VMDK and creates a template with EFI
      firmware, a paravirtual SCSI controller, VMXNET3 and
      `disk.EnableUUID=TRUE` (`govc vm.info -e <template>`).
- [ ] A clone boots, takes its hostname and IP from a customization spec
      (experiment 3), and accepts the Ansible login. With the clone added to
      the experiment 2 inventory (`<inventory>` is
      `~/inventories/exp2/hosts.yml`):
      `ansible -i <inventory> all -m ping` and
      `ansible -i <inventory> all -b -m command -a id`.
- [ ] Two clones have different machine IDs and SSH host keys. On each:
      `cat /etc/machine-id` and `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
- [ ] cloud-init created no default user: `getent passwd rocky cloud-user`
      prints nothing.
- [ ] On a fresh clone, `systemctl is-enabled rke2-server rke2-agent` prints
      `disabled` twice.
- [ ] `systemctl is-enabled bootc-fetch-apply-updates.timer` prints `masked`.
- [ ] `getenforce` prints `Enforcing`; `rpm -q firewalld` reports it is not
      installed.
- [ ] bootc reports the image reference exactly as it was given. On a fresh
      clone this prints exactly the output of `make print-ref` for that build,
      with no added transport prefix, digest or tag change:

      ```bash
      sudo bootc status --format=json | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"]["booted"]["image"]["image"]["image"])'
      ```

      The rolling-update playbook compares this field to `target_image` as an
      exact string. If they differ, the playbook will never treat a node as up
      to date and its verify step will fail: stop and report.
- [ ] The root filesystem grows after enlarging a clone's disk. Growth runs at
      boot (`rke2-node-growroot.service`). On the clone, note the size and
      power off:

      ```bash
      df -h /sysroot
      ```

      ```bash
      sudo systemctl poweroff
      ```

      Enlarge the disk in vCenter (or with `govc`), then power on:

      ```bash
      govc vm.disk.change -vm <clone> -disk.label 'Hard disk 1' -size 80G
      ```

      ```bash
      govc vm.power -on <clone>
      ```

      On the clone again:

      ```bash
      df -h /sysroot
      ```

      ```bash
      systemctl status rke2-node-growroot.service
      ```

      Expected: `/sysroot` is larger and the unit finished without error.

### Cluster deployment

- [ ] `rke2-ansible` deploys three control-plane nodes and at least one worker
      from clones, with `rke2_install_version` equal to the image's version.
- [ ] All nodes are Ready: `kubectl get nodes -o wide`.
- [ ] `/opt/cni/bin` is populated on every node: `ls /opt/cni/bin`.
- [ ] No SELinux denials on any node since boot:
      `sudo ausearch -m avc -ts boot` prints `<no matches>`. If `ausearch` is
      not installed (it comes with the `audit` package, which the image does
      not add), use `sudo journalctl -b --grep 'avc: +denied'`; it prints
      `-- No entries --`.
- [ ] Time sync uses the site's servers: `chronyc sources` lists the servers
      from the site layer's `chrony.conf`, one of them marked `^*`.
- [ ] The CNI interfaces are not managed by NetworkManager:
      `nmcli device status` shows them as `unmanaged`.

### Rolling update and rollback

- [ ] Build and push a second tag (`BUILD=<n+1>`), then run the rolling update
      (see `docs/operations.md`). It completes; control-plane nodes go first,
      one at a time, then workers.
- [ ] The cluster stays available throughout, for example a loop of
      `kubectl get --raw /readyz` from outside the cluster and a test
      workload with more than one replica that keeps answering.
- [ ] After the run, every node's booted image (the `bootc status` command
      above) equals the new `target_image`, and no node carries the
      `rke2-bootc.io/update-in-progress` annotation.
- [ ] A second run with the same `target_image` changes nothing.
- [ ] A node an operator cordoned before the run is still cordoned after it.
- [ ] An interrupted run (stop it during a drain, with the default
      `agent_batch` of 1) leaves exactly one node cordoned with the
      `rke2-bootc.io/update-in-progress` annotation, and a re-run with the
      same `target_image` finishes that node and the rest.
- [ ] The RKE2 version check stops a mismatch: a run with
      `-e expected_rke2_version=<a different version>` stops at the first
      node, after it rebooted, with a message naming both versions. A re-run
      without that option finishes the marked node and the rest.
- [ ] A package that creates a system user reaches updated nodes. Add a
      package to the site layer that creates a user in its install script,
      with a `sysusers.d` file for that user as `site/README.md` ("Users and
      groups") requires, build a new tag and roll it out. On a node that was
      updated (not a fresh clone), `getent passwd <user>` prints the user.
- [ ] `bootc rollback` and a reboot return a node to the previous image
      (see `docs/operations.md`); `bootc status` shows the previous reference
      as booted.
- [ ] With `registry_auth_json` set from Ansible Vault, `/etc/ostree/auth.json`
      exists on each node with mode `0600`, and pulls from a registry that
      needs authentication work.

### No self-updates

- [ ] No node updates or reboots on its own over at least a day. Compare before
      and after: `journalctl --list-boots`, `sudo bootc status`,
      `systemctl list-timers --all | grep -i bootc`.

### Migration

- [ ] A bootc node joins a test cluster of old-style nodes and an old node is
      retired, following `docs/migration.md`.
