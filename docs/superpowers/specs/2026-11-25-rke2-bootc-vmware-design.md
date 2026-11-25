# RKE2 node image (bootc) for VMware ESXi — design

Date: 2026-11-25
Status: draft, awaiting approval

## Intent

Kubernetes (RKE2) nodes on VMware ESXi are built today from VM templates and
configured with the `rancherfederal/rke2-ansible` playbook. The goal is to
replace the hand-maintained template with a bootable container image (bootc):
the operating system and RKE2 are built, versioned and updated together as one
container image, and each node is an immutable copy of it.

Dev clusters run on Rocky Linux, prod clusters on RHEL. Both must be built from
the same recipe so that what is tested in dev is what runs in prod.

Existing clusters are to be moved onto the new nodes; this is not only for
newly built clusters. Today's template also carries site software and
settings (security tools, NTP configuration and similar) that the new nodes
must keep.

## Goals

1. One container build recipe that produces an RKE2 node image on two bases:
   Rocky Linux 9 (dev) and RHEL 9 (prod). Everything above the base is
   identical.
2. A Rocky Linux 9 bootc base image built in this project from Rocky's own
   package repositories, because Rocky publishes no official bootc base.
3. A VMDK disk built from each node image, plus the steps to turn it into a
   vCenter template that is cloned per node.
4. The existing `rke2-ansible` playbook (collection 2.1.0) deploys the cluster
   onto clones of that template without any change to the role.
5. A rolling-update playbook that moves a running cluster to a new image one
   node at a time.
6. One image for both node types. Control-plane ("server") and worker
   ("agent") nodes are clones of the same template; the Ansible inventory
   decides which one a node becomes.
7. The site software and settings today's template carries are part of the
   image, through a site layer the user fills in.
8. A procedure for moving an existing cluster onto bootc nodes by joining new
   nodes and retiring old ones, without downtime for the cluster.

## Non-goals

- Creating or cloning VMs automatically (Terraform, Ansible vSphere modules).
  Clones are made from the template the way they are today.
- Changing, forking or wrapping the `rke2-ansible` role.
- vSphere cloud provider or CSI storage driver setup inside the cluster.
- Preloading container images for clusters with no registry access at all.
  Package and registry locations are configurable so internal mirrors work,
  but a fully offline cluster is not designed for here.
- Version 10 of Rocky or RHEL, and CPU architectures other than x86_64.
- Security hardening beyond what the role already applies (CIS profile).

## Anti-goals

These would make the result a failure even if it technically works:

- A node that reboots or updates on its own. Updates happen only when the
  rolling-update playbook runs.
- A node that drifts from its image, for example RKE2 installed or upgraded on
  the node at deploy time instead of coming from the image.
- Secrets inside the container image: registry pull credentials, Red Hat
  credentials, SSH private keys, cluster join tokens.
- Clones that share an identity (machine ID, SSH host keys, hostname).
- A dev image and a prod image that differ in anything other than the base
  and the differences declared in the per-environment part of the site layer.

## Constraints

- **Read-only operating system.** On a bootc node `/usr` is read-only. Only
  `/etc` and `/var` are writable. Anything installed with `dnf` on a running
  node fails.
- **`/var` comes from the image only once.** Files the image places under
  `/var` are copied at first install and never again on upgrade. Nothing that
  must change with the image may live there.
- **The role always runs an install step.** `rke2-ansible` has no switch to
  skip installation. On RHEL-family systems it adds the Rancher repositories
  under `/etc/yum.repos.d` and runs `dnf install rke2-server-<version>` or
  `rke2-agent-<version>` (see `roles/rke2/tasks/rpm_install.yml` and the
  install condition in `roles/rke2/tasks/main.yml`).
- **RKE2 packages allow both roles on one system.** The `rke2-server` and
  `rke2-agent` RPMs do not conflict; each only adds its own systemd unit and
  both depend on `rke2-common`, which pulls in `rke2-selinux` and `iptables`
  (checked in `rancher/rke2-packaging`).
- **RHEL builds need a Red Hat entitlement.** The prod image can only be built
  on a subscribed RHEL host (or with equivalent mirrored repositories) that is
  logged in to `registry.redhat.io`.
- **ESXi CPU baseline.** Version 9 is chosen because version 10 needs CPU
  features (x86-64-v3) that an older EVC baseline hides from VMs.
- **`/opt` is not part of the image by default.** On bootc `/opt` points into
  `/var`, so software that installs itself under `/opt` (many security agents
  do) is not updated with the image unless the build handles it. Each site
  package must be checked for this.
- **The site software list is not known yet.** The user named "security
  tools, NTP, etc." without specifics, and whether settings are the same in
  dev and prod is unknown. The vCenter and ESXi versions are also unknown.
- **Building and testing happens in the user's dev environment**, on their
  build host and dev vCenter. The authoring environment cannot reach the
  base-image or package hosts.
- **Nothing was built or booted while writing this design.** The authoring
  Every behaviour
  marked "to prove" below is unverified.

## Approaches

Three decisions shaped the design. Each lists what was considered and what was
picked.

### Who installs RKE2

- **A. The image owns RKE2 (picked).** RKE2 packages are in the image. An RKE2
  upgrade is a new image. Ansible only configures and starts it. This gives
  one tested artefact for OS plus RKE2, which is the reason for using bootc.
- **B. The image is only an RKE2-ready OS.** The role installs RKE2 from the
  tarball into `/usr/local`, which stays writable. Smallest change to today's
  workflow, but RKE2 lives outside the image and nodes can drift.

A was picked without modifying the role: with the inventory's
`rke2_install_version` equal to the version in the image, the role's
`dnf install` should find the package present and do nothing.

### Base for dev

- **Self-built Rocky 9 bootc base (picked by the user).** Truly Rocky, at the
  cost of owning the base build.
- **CentOS Stream 9 bootc.** Official and maintained, but slightly ahead of
  RHEL.
- **RHEL with developer subscriptions.** Identical to prod, but dev builds
  need Red Hat credentials.
- **Fedora bootc.** Rejected: not a RHEL rebuild and not an RKE2-supported
  target.

### How nodes are updated

- **1. Ansible rolls updates out (picked by the user).** A playbook drains,
  updates, reboots and uncordons one node at a time. The operator chooses
  when. Nodes need to reach the container registry.
- **2. Nodes update themselves, a cluster component coordinates reboots.**
  Hands-off, but adds a component to every cluster and loosens control over
  timing.
- **3. Replace nodes from a new template.** No registry needed on nodes, but
  replacing control-plane nodes is slow and gives up in-place updates.

## Design

### Pieces

| Piece | What it is | Runs where |
|---|---|---|
| Rocky base build | Container build that produces a Rocky 9 bootc base image | Build host |
| Node image build | Container build that adds the RKE2 node layer to a base given as a build argument | Build host |
| Disk and template build | Turns a pushed node image into a VMDK, then into a vCenter template | Build host, vCenter |
| Site layer | Site packages, settings and trust certificates added to the node image | Build host |
| Node survey script | Collects from an existing node what the site layer and version pin must match | Existing node |
| Inventory example | The `rke2-ansible` variables this image expects | Ansible control host |
| Migration procedure | Runbook for moving an existing cluster onto bootc nodes | Ansible control host |
| Rolling-update playbook | Moves a cluster to a new image tag | Ansible control host |
| Verification checklist | The tests to run on real infrastructure | Dev vSphere |

### Rocky base build

Build a Rocky Linux 9 bootc base image from Rocky's package repositories with
the same tooling Fedora and CentOS use for their bootc bases
(`bootc-base-imagectl build-rootfs`, see the Red Hat "Creating bootc images
from scratch" documentation). The result must behave like the official
`centos-bootc` and `rhel-bootc` bases: bootable with bootc, XFS root
filesystem by default, SELinux enforcing, NetworkManager, OpenSSH server,
`dnf` and Python 3 present.

This is the least proven part of the design. Whether the tool builds cleanly
against Rocky repositories, and from which builder image, is to prove. If it
cannot be made to work, stop and report; do not silently switch dev to
another distribution.

### Node image build

One Containerfile. Build arguments:

- the base image reference (the Rocky base above, or
  `registry.redhat.io/rhel9/rhel-bootc`);
- the RKE2 version, pinned exactly and defined in one place in the project.
  For a cluster being migrated it must equal the version that cluster runs
  now;
- the Rancher RPM repository location, defaulting to `rpm.rancher.io`, so an
  internal mirror can be used;
- the environment name (dev or prod), which selects the per-environment part
  of the site layer.

What the layer adds:

- **RKE2 from RPM:** `rke2-server`, `rke2-agent`, `rke2-common`,
  `rke2-selinux`. Both systemd units stay disabled; the role enables the one
  the node needs.
- **No leftover Rancher repository files.** The role writes its own repository
  definitions; the build-time ones are removed so the two do not collide.
- **A version fact for Ansible.** A file under `/etc/ansible/facts.d/` exposes
  the baked RKE2 version in the form the role expects
  (for example `v1.33.5+rke2r1`). Playbooks in this project use it to check
  that a node's image matches the cluster's version before acting.
- **VMware guest support:** `open-vm-tools`.
- **First-boot configuration:** `cloud-init`, configured to apply vSphere
  guest customization specs (hostname, static IP) and to do nothing else
  surprising: it must not create a default user and must not fight
  NetworkManager. With no customization spec the node falls back to DHCP.
- **What Ansible needs on a target:** Python 3 with the SELinux and dnf
  bindings, and `sudo`.
- **Passwordless sudo for the `wheel` group**, so the Ansible login user can
  become root without a password. (Author's call; see "Calls made by the
  author".)
- **Network rules RKE2 documents:** NetworkManager ignores the CNI's
  interfaces (Canal, Calico, Flannel, Cilium). Shipped under `/usr/lib` so it
  does not collide with the file the role writes under `/etc`. `firewalld`
  is not installed.
- **CIS profile support:** the `etcd` user and group exist, so a cluster
  configured with `profile: cis` starts. The role copies the CIS kernel
  settings itself from `/usr/share/rke2`.
- **No self-updating:** bootc's automatic update timer
  (`bootc-fetch-apply-updates.timer`) is masked.
- **A generalize command:** a small script that resets machine ID, SSH host
  keys, cloud-init state and logs and powers the VM off. Needed only if a
  template has to be booted once before it is converted (see "Disk and
  template build").
- **SELinux stays enforcing.** RKE2's directories under `/var/lib/rancher`
  must end up with the labels the `rke2-selinux` policy expects.
- **The build ends with `bootc container lint`** and fails if it reports
  errors.

### Site layer

A directory in the project that the node image build applies on top of the
RKE2 layer. It has a common part and a small per-environment part (dev,
prod):

- a list of packages to install, and the package repositories they come from;
- files to place in the image (for example chrony configuration for the
  site's NTP servers, proxy settings, internal CA certificates added to the
  system trust store);
- systemd units to enable.

Rules:

- Anything that must be the same on every node goes here, not into Ansible.
- Values that differ between dev and prod go in the per-environment part and
  nowhere else.
- No secrets. Enrolment keys or tokens for security tools are applied to nodes
  at deploy time; how each tool is enrolled is decided when the tools are
  named.
- If nodes cannot reach the public Rocky or RHEL repositories, the site layer
  replaces or disables the default repository definitions, because the role's
  `dnf` step consults every enabled repository.
- If the container registry uses an internal CA, that CA goes in the site
  layer so nodes trust the registry before their first update.

The project ships the site layer empty, with one worked example (NTP), and
documents how to add a package that installs under `/opt`. Filling it in is
the user's step, guided by the node survey.

### Node survey script

A read-only script the user runs on one existing node of each kind. It reports
what the new image has to match: installed packages that are not in the base,
enabled services, time-sync configuration, the RKE2 version, the RKE2 config
keys in use (values of secrets left out), and kernel modules and settings the
storage and network plugins rely on. Its output drives the site layer and the
RKE2 version pin.

### Disk and template build

1. Push the node image to the container registry under an immutable version
   tag.
2. Build a VMDK from the **registry reference** (not a local-only image) with
   `bootc-image-builder`. The disk remembers the reference it was built from;
   that is where `bootc` looks for updates later.
3. The disk build's configuration file (not the container image) supplies the
   Ansible login user, its SSH public key and membership of `wheel`, and the
   root disk size. An example file is committed; the real one is not.
4. Import the VMDK into vCenter as a VM with EFI firmware, a paravirtual SCSI
   controller, a VMXNET3 network adapter and `disk.EnableUUID=TRUE`, then mark
   it as a template. A script using `govc` does this; the manual steps are
   documented too.
5. The root filesystem must grow to fill the disk when a clone's disk is
   enlarged in vCenter.

The vCenter and ESXi versions are unknown, so the import script takes the
guest OS type and virtual hardware version as parameters, and the image
accepts first-boot settings two ways: from a vSphere customization spec, and
from per-clone `guestinfo` metadata read by cloud-init. To prove on the user's
vCenter: that a customization spec sets hostname and IP on a Rocky 9 and a
RHEL 9 clone. If it does not, `guestinfo` metadata is the documented
alternative.

Clones must not share an identity. The preferred path is a template that was
never booted, so machine ID and SSH host keys are created on each clone's
first boot. To prove: whether vCenter accepts a guest customization spec for a
clone of a template whose VMware Tools were never seen running. If it does
not, the template is booted once, the generalize command is run, and it is
converted after power-off.

### Deploying a cluster with `rke2-ansible`

The role is used as published. The inventory example shows:

- `rke2_install_version` set once for the cluster, equal to the version the
  image was built with, so the role's install step is a no-op check on bootc
  nodes and still a real install on any remaining old-style nodes;
- the role's two repository variables pointing at a location the nodes can
  reach (the public Rancher repository or an internal mirror), because `dnf`
  reads repository metadata even when it installs nothing;
- nodes placed in `rke2_servers` or `rke2_agents` as today.

To prove on a real node: the role's `dnf` task succeeds without changes when
the package is already present on a read-only `/usr`, and fails loudly when
the inventory version differs from the image. If it does not behave this way,
stop and report with the options (a small change to the role, or the tarball
install); do not change approach silently.

The role's own `upgrade.yml` is not used with this image. RKE2 upgrades are
image upgrades.

### Migrating an existing cluster

A written procedure, using the unmodified role, for a cluster that currently
runs on old-style nodes:

1. Run the node survey; fill in the site layer; build the image with the
   cluster's current RKE2 version.
2. Clone new nodes from the template and add them to the inventory next to
   the old ones. The role already joins nodes to an established cluster.
3. Retire old nodes one at a time: drain, remove from the cluster, remove
   from the inventory, delete the VM.
4. Control-plane nodes are swapped one at a time so etcd keeps its quorum,
   and whatever address nodes use to reach the API (load balancer or virtual
   IP) is updated as members change.

Old and new nodes run the same RKE2 version for the whole migration. RKE2
version upgrades wait until the cluster is entirely on bootc nodes.

### Rolling-update playbook

Inputs: the target image reference (registry, name and version tag) and,
optionally, registry pull credentials taken from Ansible Vault.

Order: control-plane nodes first, one at a time, then workers, one at a time
by default with a configurable batch size.

For each node:

1. Refuse to act on a node that is not a bootc node, and skip a node that
   already runs the target image.
2. Write the pull credentials to the node if provided.
3. Download and stage the target image (`bootc switch` to the target
   reference). Nothing changes on the running system yet.
4. Cordon and drain the node, using `kubectl` on a control-plane node.
5. Reboot into the new image.
6. Wait until the node is Ready, and for a control-plane node until the API
   server and etcd are healthy.
7. Uncordon.
8. Confirm the node now runs the target image.

Any failure stops the whole run, so a bad image stops at the first node.
Undoing an update on a node is `bootc rollback` followed by a reboot; the
documentation covers this, and covers RKE2's rules for version changes
(control plane before workers, one minor version at a time).

### Image tags and promotion

Every build is pushed under an immutable version tag that includes the RKE2
version and a build number. Dev clusters are moved to a tag with the
rolling-update playbook; the same recipe is then built on the RHEL base and
prod clusters are moved to that tag. Nodes never follow a moving tag.

### Verification

Static checks (linting of the Containerfiles, shell scripts and playbooks) run
anywhere. Everything else is run by the user in their dev environment and is
delivered as a checklist.

Three experiments come first, because the rest of the design rests on them.
Do not rely on anything else until they pass:

1. the Rocky 9 bootc base builds and boots;
2. the unmodified role's install step is a no-op on a booted bootc node;
3. a clone of the template takes hostname and IP from a customization spec
   (and whether a never-booted template is accepted).

Then:

- both images build; `bootc container lint` passes;
- the VMDK imports, a clone boots, takes its hostname and IP from a
  customization spec, and accepts the Ansible login;
- two clones have different machine IDs and SSH host keys;
- `rke2-ansible` deploys three control-plane nodes and at least one worker,
  all Ready, with no SELinux denials logged;
- a rolling update to a second image tag completes with the cluster available
  throughout, and `bootc rollback` returns a node to the previous image;
- no node updates or reboots on its own over at least a day;
- a bootc node joins a test cluster of old-style nodes and an old node is
  retired, following the migration procedure.

## Calls made by the author

These were not decided by the user and can be changed:

- Version 9 for both Rocky and RHEL.
- The role is used unmodified, relying on the no-op `dnf` behaviour.
- The inventory pins the RKE2 version once per cluster; the image's fact file
  is used for checks, not as the source of the version.
- Site software and settings are baked into the image through a site layer
  with a per-environment part; the layer ships empty with one example.
- A survey script gathers what existing nodes carry, instead of a list from
  memory.
- Migration is done by joining new nodes and retiring old ones, at an
  unchanged RKE2 version.
- The image supports both customization specs and `guestinfo` metadata,
  because the vCenter version is unknown.
- If the role's install step is not a no-op on bootc, work stops for a
  decision.
- cloud-init handles vSphere customization specs; the older Perl-based
  customization is not supported.
- The Ansible login user is added at disk-build time and the `wheel` group
  gets passwordless sudo.
- Nodes are switched to explicit version tags rather than following a moving
  tag.
- Pull credentials are delivered by the rolling-update playbook from Ansible
  Vault.
- Template creation is scripted with `govc`.
- The project lives in `/home/user/rke2-bootc`.

## Left to the builder

- Repository layout, file names, Makefile or script structure.
- The exact Rocky base build recipe and builder image.
- The exact cloud-init configuration.
- How the root filesystem is grown (what the base already provides, or
  cloud-init, or systemd).
- How RKE2's `/var` directories get their SELinux labels, if anything beyond
  the packages' defaults is needed.
- The placeholder RKE2 version in the project until the survey supplies the
  real one.
- The exact order of control-plane and worker swaps in the migration
  procedure, within the rules above.
- Whether the rolling-update playbook pre-downloads the image on all nodes
  before it starts draining.
- Which lint tools to use.
