#!/bin/sh
set -eu

WORKDIR="/workspace/workflows"

if [ ! -d "$WORKDIR" ]; then
  echo "missing workflows directory: $WORKDIR" >&2
  exit 1
fi

n8n import:workflow --separate --input="$WORKDIR"
