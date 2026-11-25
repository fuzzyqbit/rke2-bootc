# Operations

How to build and promote a new node image, roll it out, undo it, and upgrade
RKE2. Commands that build images run on the build host from the repository
root; Ansible commands run on the Ansible control host. Always pass `ENV=dev` or
`ENV=prod` to `make` explicitly (see the README for why).

## Build and promote a new image

Every change to a node (OS updates, site layer, RKE2 version) is a new image
with a new tag. Nodes never follow a moving tag.

1. Make your change and run the static checks:

   ```bash
   make test
   ```

   ```bash
   make lint
   ```

2. Pick a build number not used before for this RKE2 version, build and push
   the dev image, and note its reference:

   ```bash
   sudo make node ENV=dev BUILD=8
   ```

   ```bash
   sudo make push ENV=dev BUILD=8
   ```

   ```bash
   make print-ref ENV=dev BUILD=8
   ```

   The registry does not stop you pushing the same tag twice. Never reuse a
   build number; a reused tag means two different images under one name.

3. Roll the dev clusters to it (next section) and test.
4. Promote: on the subscribed RHEL build host, from the same git commit, build
   and push the prod image. Using the same build number for the same commit
   keeps dev and prod tags easy to match:

   ```bash
   sudo make node ENV=prod BUILD=8
   ```

   ```bash
   sudo make push ENV=prod BUILD=8
   ```

5. Roll the prod clusters to the prod reference
   (`make print-ref ENV=prod BUILD=8`).

Existing nodes do not need a new template; the rolling update moves them. Build
a new VMDK and template (`make vmdk`, `make template`) when new clones should
start from the new image, and always after an RKE2 version change: a clone of
an older template has the older RKE2, and the role's install step will fail on
it once `rke2_install_version` has moved on.

## OS updates

Nodes get operating-system updates only through a new image. To ship them:

1. Rebuild the base.
   - Dev (Rocky): rebuild and push the Rocky base. `make rocky-base` builds
     with `--no-cache --pull=newer`, so it installs the current Rocky packages
     rather than reusing a cached layer. Push it right away: `make node`
     pulls a newer base from the registry, so an unpushed local base would be
     replaced by the older one in the registry.

     ```bash
     sudo make rocky-base
     ```

     ```bash
     sudo make rocky-base-push
     ```

   - Prod (RHEL): nothing to build. `make node` builds with `--pull=newer`
     and pulls Red Hat's newer `RHEL_BASE_IMAGE` when there is one.
2. Rebuild the node image with a new `BUILD` number and push it, as in "Build
   and promote a new image".
3. Roll it out with the rolling update.

Record which base each node image was built from, because the tag does not
say. Right after `make node`, note the base image's digest next to the node
image reference, for example in your change log:

```bash
sudo podman image inspect --format '{{.Digest}}' "$ROCKY_BASE_IMAGE"
```

For prod use `RHEL_BASE_IMAGE`. (Load the names first with
`set -a; . ./versions.env; set +a`.)

## Changes under /etc on updated nodes

On a bootc update, `/etc` is merged: a file the node has not changed takes
the new image's version, but a file changed on the node keeps the node's
copy. Every node has changed `/etc/passwd`, `/etc/group` and `/etc/shadow`
(its login user was added when the disk was built), so these never take a
new image's version. A user or group that a package creates in its install
script therefore exists on fresh clones of a new template, but not on nodes
updated to that image. That is why each site package that adds users or
groups needs a `sysusers.d` file in the site layer (`site/README.md`, "Users
and groups"), as the image does for `etcd`. The same applies to any other
`/etc` file you edit on a node by hand: from then on, image changes to that
file do not reach the node. Change `/etc` through the image, or through
Ansible consistently on every node.

## Run the rolling update

```bash
ansible-playbook -i ~/inventories/dev/hosts.yml ansible/rolling-update.yml -e target_image=registry.example.com/platform/rke2-node-dev:v1.34.3-rke2r1.8
```

What it does:

1. Preflight on every targeted node: `target_image` is set, has a tag, and the
   tag is not `latest`; the node is a bootc node (`/run/ostree-booted` exists,
   `bootc status` works) built from this image (the
   `/etc/ansible/facts.d/rke2_image.fact` fact exists); there is at least one
   host in `rke2_servers`. It prints the image's RKE2 version next to
   `rke2_install_version`. Any failure stops the run before anything changes.
2. Control-plane nodes (`rke2_servers`), one at a time, then workers
   (`rke2_agents`), one at a time unless you set `agent_batch`.
3. For each node:
   - A node whose booted image already equals `target_image` is skipped,
     unless it still carries the update marker (a node left mid-update by an
     earlier run): then the run waits for it to be Ready, verifies and
     uncordons it.
   - If `registry_auth_json` is set, it is written to `/etc/ostree/auth.json`.
   - `bootc switch <target_image>` downloads and stages the image. The running
     system does not change yet.
   - If the node is already cordoned (by an operator), the playbook records
     that in the node annotation `rke2-bootc.io/was-cordoned`.
   - The node is annotated `rke2-bootc.io/update-in-progress=<target_image>`,
     cordoned and drained (`--ignore-daemonsets --delete-emptydir-data`).
   - It reboots into the new image.
   - It waits until the node reports a new boot ID and is Ready, for up to
     `ready_timeout` seconds; on a control-plane node, also until that node's
     own API server answers `/readyz` (which includes etcd).
   - It checks that `bootc status --format=json` field
     `.status.booted.image.image.image` equals `target_image` exactly.
   - It re-reads the image's fact file and stops the run unless its
     `rke2_version` equals `expected_rke2_version`, which defaults to the
     inventory's `rke2_install_version`. The message names both versions.
     With neither variable set, this check is skipped.
   - It uncordons the node, unless the operator had cordoned it, and removes
     both annotations.

`kubectl` commands run on a control-plane node: for a control-plane node being
updated, another one if there is one.

### Variables

| Variable | Default | Where to set it |
|---|---|---|
| `target_image` | none, required | `-e` only |
| `agent_batch` | `1` | `-e` only, e.g. `-e agent_batch=2` |
| `registry_auth_json` | unset | Ansible Vault (see below) |
| `drain_timeout` | `600` (seconds) | inventory `group_vars`/`host_vars`, or `-e` |
| `ready_timeout` | `600` (seconds) | inventory `group_vars`/`host_vars`, or `-e` |
| `k8s_node_name` | the node's lower-cased host name | inventory `host_vars`, or `-e` |
| `kubectl` | `/var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml` | inventory, or `-e` |
| `expected_rke2_version` | `rke2_install_version` from the inventory; unset: no check | inventory, or `-e` |

Pass `target_image` and `agent_batch` with `-e`, as the playbook's header
says. `agent_batch` has a default in the playbook itself, which outranks the
inventory, so an inventory value for it has no effect. Two waits are fixed: the
reboot may take up to 900 seconds, and the `/readyz` check retries for about
five minutes.

### Mixed clusters

The preflight refuses any node that is not a bootc node built from this image.
While a cluster still has old-style nodes (during a migration), limit the run
to the bootc nodes:

```bash
ansible-playbook -i ~/inventories/dev/hosts.yml ansible/rolling-update.yml --limit new-server-01.example.com,new-server-02.example.com,new-agent-01.example.com -e target_image=registry.example.com/platform/rke2-node-dev:v1.34.3-rke2r1.8
```

`kubectl` may still be run on an old-style control-plane node; it uses the same
paths.

## When a run fails

Any failure on any node stops the whole run, so a bad image stops at the first
node. What is left behind:

- Nodes already done run the new image and are uncordoned.
- The node or nodes being worked on are cordoned and carry the annotation
  `rke2-bootc.io/update-in-progress` (value: the target image): exactly one
  control-plane node, or up to `agent_batch` workers (one with the default
  `agent_batch` of 1). Each may have the new image only staged, or may
  already have rebooted into it.
- Nodes not yet reached are untouched.

Find the marked node:

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,UPDATING:.metadata.annotations.rke2-bootc\.io/update-in-progress'
```

Fix the cause (for example a drain blocked by a PodDisruptionBudget, a drain
refused because of pods not managed by a controller (a bare Pod, not created
by a Deployment, StatefulSet, DaemonSet or Job: delete or move it yourself),
a node slow to become Ready, a registry login, or an image whose RKE2 version
does not match `rke2_install_version`), then re-run with the same
`target_image`. The re-run finishes the marked node (if it already booted the
new image, it waits for Ready, verifies and uncordons it; if not, it drains and
reboots it), then continues with the rest. An operator cordon recorded in the
first run is still respected.

A staged image takes effect at that node's next reboot of any kind. Do not
reboot the marked node by hand unless you mean to move it to the new image.
`sudo bootc status` on the node shows whether an image is staged.

If you give up on the new image instead: roll the marked node back if it booted
the new image (next section), then remove the annotations and uncordon it —
but leave it cordoned if `rke2-bootc.io/was-cordoned` is `true`:

```bash
kubectl annotate node <node> rke2-bootc.io/update-in-progress- rke2-bootc.io/was-cordoned-
```

```bash
kubectl uncordon <node>
```

## Roll a node back

bootc keeps the previous image on disk. To return a node to it:

1. Drain the node from a control-plane node:

   ```bash
   kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
   ```

2. On the node, queue the previous image and reboot:

   ```bash
   sudo bootc rollback
   ```

   ```bash
   sudo systemctl reboot
   ```

3. Check the booted image is the previous reference:

   ```bash
   sudo bootc status
   ```

4. When the node is Ready, uncordon it:

   ```bash
   kubectl uncordon <node>
   ```

A rolling-update run with the bad `target_image` would move the node forward
again. Fix the image, build a new tag, and roll out that tag.

Rollback is meant for changes at the same RKE2 version. Rolling back across an
RKE2 version change is a Kubernetes downgrade; test it in dev before relying on
it.

## Upgrade RKE2

An RKE2 upgrade is a new image. Do not use the role's own upgrade playbook.
During a migration, do not upgrade RKE2 at all until every node is a bootc
node.

RKE2's rules for version changes:

- Control-plane nodes before workers. The rolling update does this.
- One minor version at a time (for example 1.33 to 1.34, never 1.32 to 1.34).
  To go further, do one rolling update per minor version.
- Read the RKE2 release notes for the target version first.

Steps:

1. Set `RKE2_VERSION` in `versions.env` to the new version.
2. In the same change, set `rke2_install_version` in the inventory to the new
   version and point `rke2_versioned_yum_repo` at the new minor version. The
   rolling update checks every updated node's image against
   `rke2_install_version`, so an inventory left at the old version stops the
   run at the first node.
3. Build, push and roll out a new image as above (new tag, since the RKE2 part
   of the tag changes).
4. Do not run `rke2-ansible` until every node runs the new image: its install
   step fails on nodes whose image does not match `rke2_install_version`.
5. Build a new VMDK and template for future clones.

## Registry pull credentials

Nothing in the image or in git holds registry credentials. If nodes must log
in to pull the node image, the rolling-update playbook writes the full content
of `registry_auth_json` to `/etc/ostree/auth.json` (owner root, mode `0600`),
where `bootc` reads pull credentials. The task does not log the content. The
file stays on the node, in `/etc`, across updates.

Keep the value in Ansible Vault, for example in a file passed with `-e @`:

```bash
ansible-vault create ~/inventories/dev/registry-auth.vault.yml
```

with content like:

```yaml
registry_auth_json: |
  {"auths": {"registry.example.com": {"auth": "<base64 of user:password>"}}}
```

and run:

```bash
ansible-playbook -i ~/inventories/dev/hosts.yml ansible/rolling-update.yml -e @~/inventories/dev/registry-auth.vault.yml --ask-vault-pass -e target_image=registry.example.com/platform/rke2-node-dev:v1.34.3-rke2r1.8
```

The build host logs in separately (`sudo podman login`); those credentials are
in the build host's container storage, not in any image.

If your registry uses an internal CA, put the CA certificate in the site layer
(`site/README.md`, "Internal CA certificates") so nodes trust the registry
before their first update.
