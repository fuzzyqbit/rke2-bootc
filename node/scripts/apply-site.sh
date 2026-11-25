#!/usr/bin/env bash
# Build-time application of the site layer: files, CA trust, packages, units.
# Usage: apply-site.sh <site_dir> <env>      env: dev | prod
# Env:   SITE_TARGET_ROOT (default /)
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: apply-site.sh <site_dir> <dev|prod>" >&2
  exit 2
fi
site_dir="$1"
env_name="$2"
root="${SITE_TARGET_ROOT:-/}"
root="${root%/}"
# "/" strips to empty; restore it so later paths ("$root/opt", mkdir, cp target) stay valid.
[[ -n "$root" ]] || root="/"

if [[ "$env_name" != dev && "$env_name" != prod ]]; then
  echo "apply-site.sh: env must be dev or prod, got '$env_name'" >&2
  exit 2
fi
if [[ ! -d "$site_dir/common" || ! -d "$site_dir/$env_name" ]]; then
  echo "apply-site.sh: $site_dir/common and $site_dir/$env_name must exist" >&2
  exit 2
fi

parts=(common "$env_name")

# 1. Secret guard: runs before anything is changed.
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

# 2. /opt marker: make /opt a real directory, keep /opt/cni writable.
if [[ -e "$site_dir/common/opt-in-image" ]]; then
  if [[ -L "$root/opt" ]]; then
    rm "$root/opt"
  fi
  mkdir -p "$root/opt" "$root/usr/lib/tmpfiles.d"
  ln -sfn ../var/opt/cni "$root/opt/cni"
  echo 'd /var/opt/cni 0755 root root -' >> "$root/usr/lib/tmpfiles.d/rke2-node-opt.conf"
fi

# 3. Copy rootfs trees over the image root.
for part in "${parts[@]}"; do
  rootfs="$site_dir/$part/rootfs"
  [[ -d "$rootfs" ]] || continue
  mkdir -p "$root"
  (
    cd "$rootfs"
    find . ! -type d ! -name .gitkeep ! -name '*.example' -print0 \
      | xargs -0 -r cp -a --parents --target-directory="$root"
  )
done

# 4. Refresh the CA bundle once if any part ships an anchor.
anchors=0
for part in "${parts[@]}"; do
  dir="$site_dir/$part/rootfs/etc/pki/ca-trust/source/anchors"
  if [[ -d "$dir" ]] && [[ -n "$(find "$dir" -type f ! -name .gitkeep ! -name '*.example' -print -quit)" ]]; then
    anchors=1
  fi
done
if [[ $anchors -eq 1 ]]; then
  update-ca-trust
fi

# Print the entries of a list file: comments and blank lines removed.
list_entries() {
  [[ -f "$1" ]] || return 0
  sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//' "$1" | grep -v '^$' || true
}

# 5. Packages, one dnf call per part.
for part in "${parts[@]}"; do
  mapfile -t pkgs < <(list_entries "$site_dir/$part/packages.txt")
  if [[ ${#pkgs[@]} -gt 0 ]]; then
    dnf -y install "${pkgs[@]}"
  fi
done

# 6. Units, one systemctl call per part.
for part in "${parts[@]}"; do
  mapfile -t units < <(list_entries "$site_dir/$part/units.txt")
  if [[ ${#units[@]} -gt 0 ]]; then
    systemctl enable "${units[@]}"
  fi
done
