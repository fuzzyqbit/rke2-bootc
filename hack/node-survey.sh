#!/usr/bin/env bash
# Read-only survey of an existing RKE2 node: what a replacement bootc image must match.
# Prints key/variable NAMES for RKE2 config and env files, never their values.
# Env: SURVEY_ROOT (prefix for every file read, default empty). Always exits 0.
set -euo pipefail

R="${SURVEY_ROOT:-}"

section() { printf '## %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

# Print top-level YAML keys (name before the first ':', which must be followed by whitespace or end of line) of the given files.
yaml_keys() {
  local f
  for f in "$@"; do
    [ -f "$f" ] || continue
    sed -n 's/^\([A-Za-z0-9_.-][A-Za-z0-9_.-]*\):\([[:space:]].*\)\{0,1\}$/\1/p' "$f" || true
  done
}

# Print variable names (before the first '=') of the given env files.
env_keys() {
  local f
  for f in "$@"; do
    [ -f "$f" ] || continue
    sed -n 's/^[[:space:]]*\(export[[:space:]][[:space:]]*\)\{0,1\}\([A-Za-z_][A-Za-z0-9_]*\)=.*/\2/p' "$f" || true
  done
}

shopt -s nullglob

section os-release
if [ -f "$R/etc/os-release" ]; then
  grep -E '^(NAME|VERSION_ID)=' "$R/etc/os-release" || true
fi

section rke2-version
# Under sudo, /usr/local/bin (where the tarball install puts rke2) is often not on PATH.
rke2_bin=""
if have rke2; then
  rke2_bin=rke2
elif [ -x "$R/usr/local/bin/rke2" ]; then
  rke2_bin="$R/usr/local/bin/rke2"
fi
if [ -n "$rke2_bin" ]; then
  { "$rke2_bin" --version 2>/dev/null || true; } | head -n 1 || true
fi

section packages-all
if have rpm; then
  { rpm -qa --qf '%{NAME}\n' 2>/dev/null || true; } | sort -u || true
fi

section packages-userinstalled
if have dnf; then
  dnf -C repoquery --userinstalled --qf '%{name}\n' 2>/dev/null || true
fi

section enabled-units
if have systemctl; then
  { systemctl list-unit-files --state=enabled --no-legend 2>/dev/null || true; } | awk '{print $1}' || true
fi

section time-sync
for f in "$R/etc/chrony.conf" "$R"/etc/chrony.d/*.conf; do
  [ -f "$f" ] || continue
  grep -E '^[[:space:]]*(server|pool|sourcedir)([[:space:]]|$)' "$f" || true
done

section rke2-config-keys
yaml_keys "$R/etc/rancher/rke2/config.yaml" "$R"/etc/rancher/rke2/config.yaml.d/*.yaml

section rke2-env-keys
env_keys "$R"/etc/default/rke2-* "$R"/etc/sysconfig/rke2-*

section kernel-modules
if have lsmod; then
  { lsmod 2>/dev/null || true; } | awk 'NR > 1 {print $1}' || true
fi

section sysctl-files
for f in "$R"/etc/sysctl.d/*.conf; do
  [ -f "$f" ] || continue
  printf '# %s\n' "${f#"$R"}"
  cat "$f" 2>/dev/null || true
done

section ca-trust-anchors
for f in "$R"/etc/pki/ca-trust/source/anchors/*; do
  printf '%s\n' "${f##*/}"
done

section opt-contents
for f in "$R"/opt/*; do
  printf '%s\n' "${f##*/}"
done

section selinux
if have getenforce; then
  getenforce 2>/dev/null || true
fi

exit 0
