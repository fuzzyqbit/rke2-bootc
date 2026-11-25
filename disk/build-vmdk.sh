#!/usr/bin/env bash
set -euo pipefail

usage() { echo "usage: $0 <image-ref> <config.toml> <output-dir>" >&2; exit 2; }
[ "$#" -eq 3 ] || usage
ref=$1 config=$2 output=$3

CONTAINER_TOOL=${CONTAINER_TOOL:-podman}
BIB_IMAGE=${BIB_IMAGE:-quay.io/centos-bootc/bootc-image-builder:latest}

die() { echo "error: $*" >&2; exit 2; }

case "$ref" in
  localhost/*) die "image reference '$ref' is local; push it to a registry first" ;;
  */*) ;;
  *) die "image reference '$ref' has no registry host" ;;
esac
host=${ref%%/*}
case "$host" in
  *.* | *:*) ;;
  *) die "image reference '$ref' has no registry host" ;;
esac
last=${ref##*/}
case "$last" in
  *:*) tag=${last##*:} ;;
  *) die "image reference '$ref' has no tag" ;;
esac
[ "$tag" != latest ] || die "image reference '$ref' uses the moving tag 'latest'"
[ -f "$config" ] || die "config file '$config' does not exist"

mkdir -p "$output"
abs_config=$(cd "$(dirname "$config")" && pwd)/$(basename "$config")
abs_output=$(cd "$output" && pwd)

"$CONTAINER_TOOL" pull "$ref"
"$CONTAINER_TOOL" run --rm --privileged --pull=newer --security-opt label=type:unconfined_t \
  -v "$abs_config":/config.toml:ro -v "$abs_output":/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  "$BIB_IMAGE" --type vmdk --rootfs xfs --config /config.toml "$ref"

echo "VMDK written to $abs_output/vmdk/disk.vmdk"
