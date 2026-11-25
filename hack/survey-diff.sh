#!/usr/bin/env bash
# Usage: survey-diff.sh <survey-file> <base-packages-file>
# Prints packages in the survey's packages-all section that are not in the base list, sorted.
# The base file is read once, so a pipe or process substitution works.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <survey-file> <base-packages-file>" >&2
  exit 2
fi

survey="$1" base="$2"
for f in "$survey" "$base"; do
  [ -r "$f" ] || { echo "error: cannot read: $f" >&2; exit 2; }
done
# Read the base list exactly once: it may be a pipe or a process substitution.
base_list=$(awk 'NF {print $1}' "$base" | sort -u)
[ -n "$base_list" ] || { echo "error: base packages file is empty: $base" >&2; exit 2; }

awk '/^## /{s = ($0 == "## packages-all"); next} s && NF {print $1}' "$survey" | sort -u |
  comm -23 - <(printf '%s\n' "$base_list")
