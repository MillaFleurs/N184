#!/bin/bash
# k8s Job entrypoint: fetches ContainerInput from Redis, pipes to agent-runner.
# The controller writes the input to Redis before creating the Job.
set -e

if [ -z "$JOB_NAME" ]; then
  echo "[k8s-entrypoint] ERROR: JOB_NAME not set" >&2
  exit 1
fi

if [ -z "$REDIS_URL" ]; then
  echo "[k8s-entrypoint] ERROR: REDIS_URL not set" >&2
  exit 1
fi

# Parse Redis URL for redis-cli (format: redis://host:port)
REDIS_HOST=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):?.*|\1|')
REDIS_PORT=$(echo "$REDIS_URL" | sed -E 's|redis://[^:]+:?([0-9]+)?.*|\1|')
REDIS_PORT=${REDIS_PORT:-6379}

echo "[k8s-entrypoint] Fetching input from n184:job-input:${JOB_NAME}" >&2

# Wait up to 30 seconds for the input key to appear
for i in $(seq 1 30); do
  INPUT=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" GET "n184:job-input:${JOB_NAME}")
  if [ -n "$INPUT" ] && [ "$INPUT" != "(nil)" ]; then
    break
  fi
  sleep 1
done

if [ -z "$INPUT" ] || [ "$INPUT" = "(nil)" ]; then
  echo "[k8s-entrypoint] ERROR: No input found for job ${JOB_NAME}" >&2
  exit 1
fi

echo "[k8s-entrypoint] Input received (${#INPUT} chars), starting agent" >&2

# Clean up the Redis key
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" DEL "n184:job-input:${JOB_NAME}" > /dev/null

# Pipe to the standard entrypoint
echo "$INPUT" | node /app/dist/index.js
