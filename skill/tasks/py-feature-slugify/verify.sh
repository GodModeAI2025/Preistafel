#!/usr/bin/env bash
# Aufruf: verify.sh <workdir> <out.json>
HERE="$(cd "$(dirname "$0")" && pwd)"
exec bash "$HERE/../verify_common.sh" "$HERE" "$1" "$2"
