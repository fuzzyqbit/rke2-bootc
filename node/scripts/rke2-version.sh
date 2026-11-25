#!/usr/bin/env bash
# Usage: rke2-version.sh <rpm|majmin|tag> <version>
# Converts an RKE2 version such as v1.34.3+rke2r1 into the form needed elsewhere.
set -euo pipefail

field="${1:-}"
version="${2:-}"

if [[ ! "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+\+rke2r[0-9]+$ ]]; then
  echo "rke2-version.sh: invalid version '${version}' (expected like v1.34.3+rke2r1)" >&2
  exit 2
fi

case "$field" in
  rpm)
    v="${version#v}"
    tilde='~'
    echo "${v/+/$tilde}"
    ;;
  majmin)
    v="${version#v}"
    echo "${v%.*+*}"
    ;;
  tag)
    echo "${version/+/-}"
    ;;
  *)
    echo "rke2-version.sh: unknown field '${field}' (expected rpm, majmin or tag)" >&2
    exit 2
    ;;
esac
