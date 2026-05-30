#!/bin/sh
set -eu

warn() {
    printf 'warning: %s\n' "$*" >&2
}

mkdir -p /app/users /app/logs /app/data

if ! chown -R app:app /app/users /app/logs /app/data; then
    warn "unable to chown mounted data directories; continuing with existing ownership"
fi

if [ ! -f /app/data/model_mapping.json ]; then
    if ! cp /app/model_mapping.default.json /app/data/model_mapping.json; then
        warn "unable to initialize /app/data/model_mapping.json; model mapping will be empty until the data directory is writable"
    fi
fi

if [ -f /app/data/model_mapping.json ] && ! chown app:app /app/data/model_mapping.json; then
    warn "unable to chown /app/data/model_mapping.json; continuing with existing ownership"
fi

exec python - "$@" <<'PY'
import os
import pwd
import sys

if os.getuid() == 0:
    user = pwd.getpwnam("app")
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)

os.execvp(sys.argv[1], sys.argv[1:])
PY
