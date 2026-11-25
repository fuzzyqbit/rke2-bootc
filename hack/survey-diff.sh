#!/usr/bin/env bash
# Usage: survey-diff.sh <survey-file> <base-packages-file>
# Prints packages in the survey's packages-all section that are not in the base list, sorted.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <survey-file> <base-packages-file>" >&2
  exit 2
fi

survey="$1" base="$2"
for f in "$survey" "$base"; do
  [ -r "$f" ] || { echo "error: cannot read: $f" >&2; exit 2; }
done

awk '/^## /{s = ($0 == "## packages-all"); next} s && NF {print $1}' "$survey" | sort -u |
  comm -23 - <(sort -u "$base")
