# Site layer

The site layer holds what is specific to your organisation: security tools,
NTP servers, internal CA certificates, repository definitions. It is applied
at image build time by `node/scripts/apply-site.sh <site_dir> <dev|prod>`.
Nothing here is applied to a running node.

## Layout

```
site/
  common/            applied to every image
    rootfs/          files copied verbatim over the image root
    packages.txt     one package per line, installed with dnf
    units.txt        one systemd unit per line, enabled with systemctl
    opt-in-image     optional empty marker, see "Software under /opt"
  dev/               applied only to the dev image, after common
  prod/              applied only to the prod image, after common
```

In `packages.txt` and `units.txt`, `#` starts a comment and blank lines are
ignored. The shipped files contain only comments, so the shipped layer
installs nothing.

Order of application: secret check, `/opt` marker, `rootfs/` of `common` then
the environment, `update-ca-trust` (only if a CA file was added), packages of
`common` then the environment (one `dnf -y install` per part), units of
`common` then the environment (one `systemctl enable` per part). Files in the
environment part overwrite files from `common`.

Files named `.gitkeep` or `*.example` inside `rootfs/` are never copied.

## Rules

- No secrets. The build stops with exit code 3 if any file in `common` or the
  chosen environment is named `auth.json` or `*.key`, or contains
  `PRIVATE KEY-----`. Enrolment keys and tokens for security tools (for
  example an EDR customer ID or install token) must not go here. They are
  applied to nodes at deploy time.
- Only `dev` and `prod` are valid environments, and both directories plus
  `common` must exist, otherwise the script exits 2.
- `/usr` is read-only on a node. Only `/etc` and `/var` are writable.
- Files an RPM installs under `/var` reach a node only at first install and
  are not updated when the node moves to a new image. Do not depend on them.
- Units are only enabled, not started, at build time.
- Do not place `rke2-server.service` or `rke2-agent.service` in `units.txt`.
  They stay disabled in the image.

## NTP servers

`common/rootfs/etc/chrony.d/site.conf.example` is a template. Rename it to
`site.conf` and replace the example hosts:

```
server ntp1.example.com iburst
server ntp2.example.com iburst
```

It is copied to `/etc/chrony.d/site.conf` in the image.

## Internal CA certificates

Put PEM certificates (public certificates only, never keys) in:

```
site/common/rootfs/etc/pki/ca-trust/source/anchors/
```

If any file is present there, `update-ca-trust` runs once during the build.

## Replacing the default repositories

To use an internal mirror, add repository files under
`rootfs/etc/yum.repos.d/`. A file with the same name as a default one
replaces it. Use `common` for all images or `dev`/`prod` for one.

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
