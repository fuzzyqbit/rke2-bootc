#!/usr/bin/env bash
# Build-time install of RKE2 from the Rancher RPM repository.
# Env: RKE2_VERSION, SITE_ENV (required); RANCHER_RPM_BASEURL, RKE2_CHANNEL, EL_MAJOR, TARGET_ROOT (optional).
set -euo pipefail

for required in RKE2_VERSION SITE_ENV; do
  if [[ -z "${!required:-}" ]]; then
    echo "install-rke2.sh: $required must be set" >&2
    exit 2
  fi
done
baseurl="${RANCHER_RPM_BASEURL:-https://rpm.rancher.io}"
channel="${RKE2_CHANNEL:-stable}"
el_major="${EL_MAJOR:-9}"
root="${TARGET_ROOT:-}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rpm_version="$("$here/rke2-version.sh" rpm "$RKE2_VERSION")"
majmin="$("$here/rke2-version.sh" majmin "$RKE2_VERSION")"

repo_file="$root/etc/yum.repos.d/rancher-rke2-build.repo"
fact_file="$root/etc/ansible/facts.d/rke2_image.fact"

trap 'rm -f "$repo_file"' EXIT

mkdir -p "$(dirname "$repo_file")" "$(dirname "$fact_file")"
cat > "$repo_file" <<REPO
[rancher-rke2-common-build]
name=Rancher RKE2 common (build time)
baseurl=$baseurl/rke2/$channel/common/centos/$el_major/noarch
enabled=1
gpgcheck=1
gpgkey=$baseurl/public.key

[rancher-rke2-build]
name=Rancher RKE2 $majmin (build time)
baseurl=$baseurl/rke2/$channel/$majmin/centos/$el_major/x86_64
enabled=1
gpgcheck=1
gpgkey=$baseurl/public.key
REPO

dnf -y install \
  "rke2-server-$rpm_version" "rke2-agent-$rpm_version" "rke2-common-$rpm_version" rke2-selinux

systemctl disable rke2-server.service rke2-agent.service

printf '{"rke2_version": "%s", "site_env": "%s"}\n' "$RKE2_VERSION" "$SITE_ENV" > "$fact_file"
chmod 0644 "$fact_file"

rm -f "$repo_file"
