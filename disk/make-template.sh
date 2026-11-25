#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --vmdk PATH --name NAME --datastore DS --network NET [--guest-id ID]" >&2
  echo "          [--hw-version V] [--cpus N] [--memory-mb N] [--pool POOL] [--folder F] [--boot-once]" >&2
  exit 2
}

vmdk="" name="" ds="" net="" guest=rhel9_64Guest hwv="" cpus=4 mem=8192 pool="" folder="" boot_once=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --boot-once) boot_once=1; shift; continue ;;
    --vmdk|--name|--datastore|--network|--guest-id|--hw-version|--cpus|--memory-mb|--pool|--folder)
      [ "$#" -ge 2 ] || { echo "error: $1 needs a value" >&2; usage; } ;;
    *) echo "error: unknown argument '$1'" >&2; usage ;;
  esac
  case "$1" in
    --vmdk) vmdk=$2 ;;
    --name) name=$2 ;;
    --datastore) ds=$2 ;;
    --network) net=$2 ;;
    --guest-id) guest=$2 ;;
    --hw-version) hwv=$2 ;;
    --cpus) cpus=$2 ;;
    --memory-mb) mem=$2 ;;
    --pool) pool=$2 ;;
    --folder) folder=$2 ;;
  esac
  shift 2
done

[ -n "$vmdk" ] || { echo "error: --vmdk is required" >&2; usage; }
[ -n "$name" ] || { echo "error: --name is required" >&2; usage; }
[ -n "$ds" ] || { echo "error: --datastore is required" >&2; usage; }
[ -n "$net" ] || { echo "error: --network is required" >&2; usage; }
[ -f "$vmdk" ] || { echo "error: VMDK file '$vmdk' does not exist" >&2; exit 2; }

import_args=(-ds "$ds")
create_args=(-on=false -g "$guest" -firmware efi -disk.controller pvscsi -net "$net"
  -net.adapter vmxnet3 -c "$cpus" -m "$mem" -ds "$ds")
if [ -n "$pool" ]; then
  import_args+=(-pool "$pool")
  create_args+=(-pool "$pool")
fi
[ -z "$folder" ] || create_args+=(-folder "$folder")
[ -z "$hwv" ] || create_args+=(-version "$hwv")

# Import without a remote path argument, from a temporary symlink named <name>.vmdk: govc up
# to 0.55 and from 0.56 on then both write [ds] <name>/<name>.vmdk (0.56 rejects a remote path
# that does not end in .vmdk, so passing "$name" as the remote directory no longer works).
tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' EXIT
vmdk_abs=$(cd "$(dirname "$vmdk")" && pwd)/$(basename "$vmdk")
ln -s "$vmdk_abs" "$tmpdir/$name.vmdk"

govc import.vmdk "${import_args[@]}" "$tmpdir/$name.vmdk"
# -link=false: the template gets its own disk, not a linked child of the imported one.
govc vm.create "${create_args[@]}" -link=false -disk "$name/$name.vmdk" "$name"
govc vm.change -vm "$name" -e disk.EnableUUID=TRUE

if [ "$boot_once" -eq 1 ]; then
  cat <<MSG
VM '$name' is created but not marked as a template. Remaining manual steps:
  1. Power on '$name' and log in.
  2. Run: sudo rke2-node-generalize
  3. Wait for the VM to power off.
  4. Run: govc vm.markastemplate $name
MSG
else
  govc vm.markastemplate "$name"
fi
