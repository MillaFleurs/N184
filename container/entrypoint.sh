#!/bin/bash
# Standard entrypoint: reads ContainerInput JSON from stdin, runs agent-runner.
# Used by both file-based IPC (NanoClaw compat) and Redis IPC (k8s).
set -e
cat > /tmp/input.json
node /app/dist/index.js < /tmp/input.json
