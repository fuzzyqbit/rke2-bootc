#!/usr/bin/env bash
# Build-time application of the site layer, in two phases:
#   files     secret guard, /opt marker, rootfs copy, CA trust. Runs before any
#             package is installed, so site repository files and CA anchors are
#             in place for every dnf step of the build.
#   packages  secret guard, packages, units.
# Usage: apply-site.sh <site_dir> <env> <phase>    env: dev | prod   phase: files | packages
# Env:   SITE_TARGET_ROOT (default /)
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: apply-site.sh <site_dir> <dev|prod> <files|packages>" >&2
  exit 2
fi
site_dir="$1"
env_name="$2"
phase="$3"

# Normalise the target root: strip one trailing slash; "/" strips to empty, so restore it
# to keep later paths ("$root/opt", mkdir, cp target) valid.
normalise_root() {
  local r="${1%/}"
  printf '%s\n' "${r:-/}"
}
root="$(normalise_root "${SITE_TARGET_ROOT:-/}")"

if [[ "$env_name" != dev && "$env_name" != prod ]]; then
  echo "apply-site.sh: env must be dev or prod, got '$env_name'" >&2
  exit 2
fi
if [[ "$phase" != files && "$phase" != packages ]]; then
  echo "apply-site.sh: phase must be files or packages, got '$phase'" >&2
  exit 2
fi
if [[ ! -d "$site_dir/common" || ! -d "$site_dir/$env_name" ]]; then
  echo "apply-site.sh: $site_dir/common and $site_dir/$env_name must exist" >&2
  exit 2
fi

parts=(common "$env_name")

# Secret guard: runs in both phases, before anything is changed.
for part in "${parts[@]}"; do
  while IFS= read -r -d '' f; do
    echo "secret-looking file in site layer: $f" >&2
    exit 3
  done < <(find "$site_dir/$part" -type f \( -name auth.json -o -name '*.key' \) -print0)
  while IFS= read -r f; do
    echo "secret-looking file in site layer: $f" >&2
    exit 3
  done < <(grep -rlF -e 'PRIVATE KEY-----' "$site_dir/$part" || true)
done

# Print the entries of a list file: comments and blank lines removed.
list_entries() {
  [[ -f "$1" ]] || return 0
  sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//' "$1" | grep -v '^$' || true
}

apply_files() {
  # /opt marker: make /opt a real directory, keep /opt/cni writable.
  if [[ -e "$site_dir/common/opt-in-image" ]]; then
    if [[ -L "$root/opt" ]]; then
      rm "$root/opt"
    fi
    mkdir -p "$root/opt" "$root/usr/lib/tmpfiles.d"
    ln -sfn ../var/opt/cni "$root/opt/cni"
    echo 'd /var/opt/cni 0755 root root -' >> "$root/usr/lib/tmpfiles.d/rke2-node-opt.conf"
  fi

  # Copy rootfs trees over the image root. Ownership is not preserved (the build runs as
  # root, so files become root:root, not the checkout owner's UID), and group/other write
  # bits are cleared on every copied file and on every directory the copy created.
  local part rootfs d
  local -a new_dirs files
  for part in "${parts[@]}"; do
    rootfs="$site_dir/$part/rootfs"
    [[ -d "$rootfs" ]] || continue
    mkdir -p "$root"
    mapfile -d '' -t files < <(cd "$rootfs" && find . ! -type d ! -name .gitkeep ! -name '*.example' -print0)
    [[ ${#files[@]} -gt 0 ]] || continue
    new_dirs=()
    while IFS= read -r -d '' d; do
      [[ -e "$root/$d" ]] || new_dirs+=("$root/$d")
    done < <(cd "$rootfs" && find . -mindepth 1 -type d -print0)
    (cd "$rootfs" && cp -a --no-preserve=ownership --parents --target-directory="$root" "${files[@]}")
    for d in "${new_dirs[@]}"; do
      if [[ -d "$d" ]]; then chmod go-w "$d"; fi
    done
    for d in "${files[@]}"; do
      if [[ ! -L "$root/$d" ]]; then chmod go-w "$root/$d"; fi
    done
  done

  # Refresh the CA bundle once if any part ships an anchor.
  local anchors=0 dir
  for part in "${parts[@]}"; do
    dir="$site_dir/$part/rootfs/etc/pki/ca-trust/source/anchors"
    if [[ -d "$dir" ]] && [[ -n "$(find "$dir" -type f ! -name .gitkeep ! -name '*.example' -print -quit)" ]]; then
      anchors=1
    fi
  done
  if [[ $anchors -eq 1 ]]; then
    update-ca-trust
  fi
}

apply_packages() {
  local part
  local -a pkgs units
  # Packages, one dnf call per part.
  for part in "${parts[@]}"; do
    mapfile -t pkgs < <(list_entries "$site_dir/$part/packages.txt")
    if [[ ${#pkgs[@]} -gt 0 ]]; then
      dnf -y install "${pkgs[@]}"
    fi
  done

  # Units, one systemctl call per part.
  for part in "${parts[@]}"; do
    mapfile -t units < <(list_entries "$site_dir/$part/units.txt")
    if [[ ${#units[@]} -gt 0 ]]; then
      systemctl enable "${units[@]}"
    fi
  done
}

case "$phase" in
  files) apply_files ;;
  packages) apply_packages ;;
esac
