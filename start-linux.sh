#!/bin/sh
set -eu
cd "$(dirname "$0")"
docker compose up -d --build
printf 'BetterHoster is available at http://127.0.0.1:8000/\n'
