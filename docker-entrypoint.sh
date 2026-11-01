#!/bin/sh
set -eu
mkdir -p /app/servers /app/.local-editor/profiles
chown betterhoster:betterhoster /app/servers /app/.local-editor/profiles
exec gosu betterhoster python /app/server.py
