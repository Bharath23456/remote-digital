#!/bin/sh
set -eu

# Docker creates a new named volume as root. Repair ownership before dropping
# privileges so both fresh and existing identity volumes remain writable.
mkdir -p /data
chown -R identity:identity /data

exec runuser -u identity -- "$@"
