# Site layer

The site layer holds what is specific to your organisation: security tools,
NTP servers, internal CA certificates, repository definitions. It is applied
at image build time by `node/scripts/apply-site.sh <site_dir> <dev|prod> <files|packages>`,
in two phases (see "Order of application"). Nothing here is applied to a
running node.

## Layout

```
site/
  common/            applied to every image
    rootfs/          files copied over the image root
    packages.txt     one package per line, installed with dnf
    units.txt        one systemd unit per line, enabled with systemctl
    opt-in-image     optional empty marker, see "Software under /opt"
  dev/               applied only to the dev image, after common
  prod/              applied only to the prod image, after common
```

In `packages.txt` and `units.txt`, `#` starts a comment and blank lines are
ignored. The shipped files contain only comments, so the shipped layer
installs nothing.

## Order of application

The node build (`node/Containerfile`) runs the script twice:

1. **`files` phase, before any package is installed** (before the node image's
   own `dnf install` and before RKE2 is installed): secret check, `/opt`
   marker, `rootfs/` of `common` then the environment, and `update-ca-trust`
   (only if a CA file was added). Because this runs first, your repository
   files and CA certificates are in place for every `dnf` step of the build.
2. **`packages` phase, after RKE2 and the image's own files**: secret check
   again, packages of `common` then the environment (one `dnf -y install` per
   part), then `rootfs/` of `common` then the environment copied again, and
   `update-ca-trust` again (only if a CA file was added), then units of
   `common` then the environment (one `systemctl enable` per part).

Files in the environment part overwrite files from `common`. Files named
`.gitkeep` or `*.example` inside `rootfs/` are never copied.

The site layer has the last word: because `rootfs/` is copied again after the
image's own files (`node/rootfs/`: cloud-init, sudo, NetworkManager settings)
and after your packages are installed, a file you ship replaces whatever the
image or a package put at the same path. Replacing an image file means you
now own its whole content; for a small change a drop-in with a later name
(for example `rootfs/etc/cloud/cloud.cfg.d/95-site.cfg`) is usually easier to
keep up to date. Unit files you ship are in place before units are enabled.
Both phases can be run more than once with the same result.

Copied files and directories are owned by root (ownership in your checkout
is not kept), and group and other write permission is removed from them.
Executable bits are kept.

## Rules

- No secrets. The build stops with exit code 3 if any file in `common` or the
  chosen environment is named `auth.json` or `*.key`, or contains
  `PRIVATE KEY-----`. Enrolment keys and tokens for security tools (for
  example an EDR customer ID or install token) must not go here. They are
  applied to nodes at deploy time.
- Public GPG keys for package repositories are not secrets, but a file named
  `*.key` stops the build all the same. Rename such a key, for example to
  `RPM-GPG-KEY-vendor.gpg` or `vendor.asc`, and point the repository's
  `gpgkey=` at the new name.
- Only `dev` and `prod` are valid environments, only `files` and `packages`
  are valid phases, and both environment directories plus `common` must
  exist; otherwise the script exits 2.
- `/usr` is read-only on a node. Only `/etc` and `/var` are writable.
- Files an RPM installs under `/var` reach a node only at first install and
  are not updated when the node moves to a new image. Do not depend on them.
- Units are only enabled, not started, at build time.
- Do not place `rke2-server.service` or `rke2-agent.service` in `units.txt`.
  They stay disabled in the image.
- A site package that creates users or groups needs a `sysusers.d` file in the
  site layer (see "Users and groups").

## NTP servers

`common/rootfs/etc/chrony.conf.example` is a complete `chrony.conf`. Rename
it to `chrony.conf` and replace the example servers:

```
server ntp1.example.com iburst
server ntp2.example.com iburst
```

It replaces `/etc/chrony.conf` in the image. It must be a complete file:
EL9's default `chrony.conf` reads no drop-in directory, so a file under
`/etc/chrony.d` would be ignored. Keep the other lines of the example
(drift file, `makestep`, `rtcsync`, key file, leap second zone, log
directory); they are the EL9 defaults.

## Internal CA certificates

Put PEM certificates (public certificates only, never keys) in:

```
site/common/rootfs/etc/pki/ca-trust/source/anchors/
```

If any file is present there, `update-ca-trust` runs once during the `files`
phase, before any package is installed, so `dnf` already trusts a mirror or
registry signed by that CA.

## Replacing the default repositories

If your build host or your nodes cannot reach the public Rocky or RHEL
mirrors, the site layer must replace the repository definitions: add files
under `rootfs/etc/yum.repos.d/` whose names are the same as the image's
default `*.repo` files (`sudo podman run --rm <base image> ls /etc/yum.repos.d`
lists them), so each default file is overwritten, or set `enabled=0` in a
replacement. Use `common` for all images or `dev`/`prod` for one. These files
are applied in the `files` phase, before any package is installed, so every
`dnf` step of the node build uses them; they also stay in the image, which
matters because the `rke2-ansible` role's `dnf` step consults every enabled
repository on the node.

`ROCKY_MIRROR` in `versions.env` is not a substitute: it only serves the Rocky
base build. Its repository file exists only inside that build's builder
stage, and the finished base image carries the repository files that the
`rocky-release` package ships (the public Rocky mirrors).

## Users and groups

On a bootc update, a file under `/etc` that was changed on the node keeps the
node's copy; only unchanged files take the new image's version.
`/etc/passwd`, `/etc/group` and `/etc/shadow` are changed on every node (the
Ansible login user from `disk/config.toml` is added to them when the disk is
built), so they never take a new image's version.
A user or group that a package creates in its install script is therefore
present on fresh clones of a new template, but missing on nodes updated to
that image.

So for every site package that creates users or groups, add a `sysusers.d`
file under `rootfs/usr/lib/sysusers.d/` that declares them (as the image does
for `etcd` in `node/rootfs/usr/lib/sysusers.d/rke2-etcd.conf`). systemd
creates the declared users and groups at boot on every node, fresh or updated.
Find what a package creates with `rpm -q --scripts <package>` (look for
`useradd` and `groupadd`) on an existing node. For example:

```
u vendoragent - "Vendor agent" - -
```

## Software under /opt

On a bootc system `/opt` is a symlink to `/var/opt`. Anything an RPM installs
under `/opt` ends up in `/var`, which is copied to a node only at first
install and is not updated with the image. Software that installs under
`/opt` therefore needs the marker:

```
touch site/common/opt-in-image
```

With the marker, `/opt` is a real directory inside the image, so the RPM
contents are part of the image and update with it. The build also creates
`/opt/cni` as a symlink to `../var/opt/cni`, and adds
`d /var/opt/cni 0755 root root -` to
`/usr/lib/tmpfiles.d/rke2-node-opt.conf`. RKE2 network plugins write to
`/opt/cni` at runtime and that path must stay writable.

Warning: `/opt` is now read-only on the node. If your software writes under
`/opt` at runtime (logs, state, caches), give it its own symlink into
`/var/opt`, for example `/opt/vendor/data -> ../../var/opt/vendor/data`, and
a matching `d` line in a tmpfiles.d file under `rootfs/usr/lib/tmpfiles.d/`.
