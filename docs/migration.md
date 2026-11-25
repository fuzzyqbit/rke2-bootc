# Migrating an existing cluster onto bootc nodes

This runbook moves a running RKE2 cluster from today's template-built nodes
("old nodes") to clones of the bootc template ("new nodes") without downtime.
New nodes join the cluster next to the old ones, then old nodes are retired one
at a time. The `rke2-ansible` role is used unmodified throughout.

Rules for the whole migration:

- Old and new nodes run the same RKE2 version from start to finish. RKE2
  upgrades wait until the cluster is entirely on bootc nodes.
- One node at a time: add or retire one node, check the cluster, then move on.
- Control-plane nodes are swapped so that etcd keeps its quorum at every step.
- Do not use the role's own upgrade playbook on new nodes.

`kubectl` below means a kubectl with cluster-admin access to this cluster,
for example on a control-plane node:
`sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml`.
`govc` commands assume `GOVC_URL` and the other `GOVC_*` variables are set.

Before you start, the three experiments in
[`docs/verification-checklist.md`](verification-checklist.md) must have passed.
Rehearse this runbook on a test cluster of old-style nodes first; it is the
last item of that checklist.

## Step 1: survey, site layer, image

### Survey an existing node

Run the read-only survey on one existing control-plane node and one existing
worker. It reads RKE2 config files, so it needs root. It prints config key
names, never their values.

```bash
scp hack/node-survey.sh old-server-01:/tmp/node-survey.sh
```

```bash
ssh old-server-01 sudo bash /tmp/node-survey.sh > survey-server.txt
```

```bash
scp hack/node-survey.sh old-agent-01:/tmp/node-survey.sh
```

```bash
ssh old-agent-01 sudo bash /tmp/node-survey.sh > survey-agent.txt
```

The output has one `## <section>` heading per topic: `os-release`,
`rke2-version`, `packages-all`, `packages-userinstalled`, `enabled-units`,
`time-sync`, `rke2-config-keys`, `rke2-env-keys`, `kernel-modules`,
`sysctl-files`, `ca-trust-anchors`, `opt-contents`, `selinux`.

The `sysctl-files` section prints whole files from `/etc/sysctl.d`. Read the
survey output before you share it.

### List packages the base image lacks

`make survey-diff` lists the packages in the survey's `packages-all` section
that are not in the Rocky base image. It runs the base image with `podman`, so
the base must be built or pullable (`make rocky-base`). Use `sudo` if the base
is in root's container storage:

```bash
sudo make survey-diff SURVEY=survey-server.txt
```

```bash
sudo make survey-diff SURVEY=survey-agent.txt
```

The comparison is always against `ROCKY_BASE_IMAGE`, also for a RHEL cluster;
expect Rocky and Red Hat branding packages to show up as differences. Some
listed packages are already added by the node image and need no action:
`open-vm-tools`, `cloud-init`, `cloud-utils-growpart`, `chrony`, `sudo`,
`python3`, `python3-libselinux`, `python3-dnf`, `rke2-server`, `rke2-agent`,
`rke2-common`, `rke2-selinux` and their dependencies. Old RKE2 installs may also
show packages from the old template that you no longer need.

### Fill in the site layer

Use the survey to fill in `site/` as described in
[`site/README.md`](../site/README.md):

- Packages you still need: `site/common/packages.txt`, or
  `site/dev/packages.txt` / `site/prod/packages.txt` for one environment only.
  Their repositories go under `rootfs/etc/yum.repos.d/` in the same part.
- `enabled-units`: units your site software needs go in `units.txt`. Never add
  `rke2-server.service` or `rke2-agent.service`.
- `time-sync`: copy the `server`/`pool` lines into a complete `chrony.conf`
  in `site/common/rootfs/etc/` (start from
  `site/common/rootfs/etc/chrony.conf.example`, renamed to `chrony.conf`).
  It replaces the image's `/etc/chrony.conf`; EL9 reads no `chrony.d` drop-in
  directory by default, so a drop-in file there would be ignored.
- `ca-trust-anchors`: put the same CA certificates under
  `rootfs/etc/pki/ca-trust/source/anchors/`.
- `opt-contents`: if a package you keep installs under `/opt`, create the
  `opt-in-image` marker in `site/common/` (see "Software under /opt" in
  `site/README.md`).
- `kernel-modules` and `sysctl-files`: compare with a new node after step 2;
  settings your storage or network plugins need go in the site layer as files
  under `rootfs/etc/sysctl.d/` or `rootfs/etc/modules-load.d/`.
- `selinux`: the image runs SELinux enforcing. If old nodes are permissive,
  expect denials to surface on new nodes and check for them
  (`sudo ausearch -m avc -ts boot`).
- `rke2-config-keys` and `rke2-env-keys`: these are configured by
  `rke2-ansible`, not by the image. Check that your inventory produces the same
  keys on new nodes.

No secrets in the site layer: enrolment keys and tokens for security tools are
applied to nodes at deploy time, not built in.

### Pin the RKE2 version

Take the version from the survey's `rke2-version` section (`rke2 --version`
prints it, for example `rke2 version v1.34.3+rke2r1 (...)`). All old nodes must
run that same version; check:

```bash
kubectl get nodes -o wide
```

Set `RKE2_VERSION` in `versions.env` to that version, in the form
`v<major>.<minor>.<patch>+rke2r<n>`. In the inventory, set
`rke2_install_version` to the same value, and check that
`rke2_versioned_yum_repo` points at that minor version (the example points at
`1.34`). Your existing inventory probably already sets
`rke2_install_version` to the cluster's version; the image must match it, not
the other way round.

### Build the image and template

Follow the README from building the node image to creating the template. On
one clone, confirm the image's version before going further:

```bash
cat /etc/ansible/facts.d/rke2_image.fact
```

## Step 2: add new nodes next to the old ones

1. Clone new nodes from the template as you do today, each with its hostname
   and IP (customization spec or `guestinfo` metadata, whichever experiment 3
   proved).
2. Add them to the inventory you use for this cluster, next to the old nodes:
   new control-plane nodes in `rke2_servers`, new workers in `rke2_agents`.
   Keep `ansible_user` matching the login user from `disk/config.toml`; if old
   nodes use a different user, set `ansible_user` per host.
3. Run the `rke2-ansible` playbook with that inventory, as you do today. The
   role joins new nodes to the established cluster. On new nodes its install
   step finds the RPMs already present; on old nodes it behaves as it always
   has. Read the run's output: on new nodes the install task must report `ok`.
4. Check that each new node is Ready and runs the same version as the others:

   ```bash
   kubectl get nodes -o wide
   ```

Add workers before you retire old workers, so capacity never drops. For the
control plane, follow step 4.

## Step 3: retire old workers, one at a time

For each old worker:

1. Drain it, and check that only DaemonSet pods are left on it:

   ```bash
   kubectl drain old-agent-01 --ignore-daemonsets --delete-emptydir-data
   ```

   ```bash
   kubectl get pods -A -o wide --field-selector spec.nodeName=old-agent-01
   ```

2. Stop RKE2 on it so it cannot re-register, by powering it off:

   ```bash
   govc vm.power -s old-agent-01
   ```

3. Remove it from the cluster:

   ```bash
   kubectl delete node old-agent-01
   ```

4. Remove it from the inventory.
5. Delete the VM, once you are satisfied the cluster is healthy:

   ```bash
   govc vm.destroy old-agent-01
   ```

6. Before the next node, check that the remaining nodes are Ready and your
   workloads are running:

   ```bash
   kubectl get nodes
   ```

## Step 4: swap control-plane nodes, one at a time

etcd needs a majority of its members to work. With three members it survives
the loss of one. Never let the number of healthy members drop below a
majority, and never remove a member while another one is unhealthy.

The safe order with three old servers is: add one new server (four members),
remove one old server (three members), and repeat three times. Each round:

1. Clone and add one new server to `rke2_servers` and run `rke2-ansible` as in
   step 2. Wait until it is Ready.
2. Check etcd has the new member and all members are healthy, from any
   control-plane node:

   ```bash
   sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml -n kube-system get pods -l component=etcd -o wide
   ```

   ```bash
   sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml -n kube-system exec etcd-<server-node-name> -- etcdctl --cacert /var/lib/rancher/rke2/server/tls/etcd/server-ca.crt --cert /var/lib/rancher/rke2/server/tls/etcd/server-client.crt --key /var/lib/rancher/rke2/server/tls/etcd/server-client.key member list -w table
   ```

3. Update the API address. Whatever address nodes and clients use to reach the
   Kubernetes API (load balancer or virtual IP), add the new server to it now
   and take the old server you are about to retire out of it. If the inventory
   or the role names one particular server for others to join through, check it
   is a server that stays, and change it before retiring that server.
4. Retire one old server: drain it, power it off, `kubectl delete node`, remove
   it from the inventory, as in step 3.
5. Check etcd again with the `member list` command above. Deleting the node
   object should also remove its etcd member; if the old member is still
   listed, remove it with `etcdctl member remove <member-id>` before the next
   round.
6. Check the API through its shared address and that all nodes are Ready:

   ```bash
   kubectl get --raw /readyz
   ```

Delete old server VMs only after the last round, when the cluster has been
healthy on bootc control-plane nodes for a while.

## After the migration

- The cluster is now all bootc nodes. From here on, OS and RKE2 changes are
  image updates with `ansible/rolling-update.yml`; see
  [`docs/operations.md`](operations.md).
- Only now may RKE2 be upgraded, following the rules in `docs/operations.md`.
- During the migration, a rolling update must be limited to the new nodes with
  `--limit`; its preflight refuses non-bootc nodes.
