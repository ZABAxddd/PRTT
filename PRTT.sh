#!/bin/sh
set -eu

TOOL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -d "$TOOL_DIR/PRTT.app" ]; then
    exec open "$TOOL_DIR/PRTT.app" --args "$@"
fi

if [ -x "$TOOL_DIR/PRTT" ]; then
    exec "$TOOL_DIR/PRTT" "$@"
fi

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$TOOL_DIR/prtt.py" "$@"
fi

printf '%s\n' "No se encontró el ejecutable portable de PRTT ni Python 3." >&2
exit 1
