#!/usr/bin/env bash
# Build-time application of the site layer, in two phases:
#   files     secret guard, /opt marker, rootfs copy, CA trust. Runs before any
#             package is installed, so site repository files and CA anchors are
#             in place for every dnf step of the build.
#   packages  secret guard, packages, rootfs copy and CA trust again (site files win over
#             the image's own files and package defaults at the same path), units.
# Both phases are safe to run more than once.
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

# /opt marker: make /opt a real directory, keep /opt/cni writable. Safe to run again.
apply_opt_marker() {
  [[ -e "$site_dir/common/opt-in-image" ]] || return 0
  local conf="$root/usr/lib/tmpfiles.d/rke2-node-opt.conf" line='d /var/opt/cni 0755 root root -'
  if [[ -L "$root/opt" ]]; then
    rm "$root/opt"
  fi
  mkdir -p "$root/opt" "$root/usr/lib/tmpfiles.d"
  ln -sfn ../var/opt/cni "$root/opt/cni"
  if ! grep -qxF -- "$line" "$conf" 2>/dev/null; then
    echo "$line" >> "$conf"
  fi
}

# Copy rootfs trees over the image root. cp never creates or touches a directory: missing
# target directories are created here, top-down, with mode 0755 (root-owned in the build);
# directories that already exist in the target keep their mode, owner and timestamps (cp
# --parents would re-apply the source directories' modes to them, the target root included).
# Each file is copied into its existing parent without keeping ownership, and group/other
# write bits are cleared on it.
copy_rootfs() {
  local part rootfs f rel dir c
  local -a files comps
  if [[ ! -d "$root" ]]; then
    mkdir -p "$root"
    chmod 0755 "$root"
  fi
  for part in "${parts[@]}"; do
    rootfs="$site_dir/$part/rootfs"
    [[ -d "$rootfs" ]] || continue
    mapfile -d '' -t files < <(cd "$rootfs" && find . ! -type d ! -name .gitkeep ! -name '*.example' -print0)
    for f in "${files[@]}"; do
      rel="${f#./}"
      dir="$root"
      comps=()
      IFS=/ read -r -a comps <<< "$(dirname "$rel")"
      for c in "${comps[@]}"; do
        [[ "$c" == . || -z "$c" ]] && continue
        dir="$dir/$c"
        if [[ ! -d "$dir" ]]; then mkdir -m 0755 "$dir"; fi
      done
      cp -a --no-preserve=ownership --remove-destination "$rootfs/$rel" "$root/$rel"
      if [[ ! -L "$root/$rel" ]]; then chmod go-w "$root/$rel"; fi
    done
  done
}

# Refresh the CA bundle if any part ships an anchor.
refresh_ca_trust() {
  local part dir anchors=0
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

# files: before any package is installed, so repository files and CA anchors serve every dnf step.
apply_files() {
  apply_opt_marker
  copy_rootfs
  refresh_ca_trust
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

  # Copy the site files again: the site layer has the last word over the image's own files
  # (node/rootfs) and over files the packages installed at the same path.
  copy_rootfs
  refresh_ca_trust

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
