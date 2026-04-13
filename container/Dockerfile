# N184 Agent Container
# Runs Claude Agent SDK in isolated Linux VM with browser automation.
# Supports Redis IPC for Kubernetes deployment (IPC_BACKEND=redis).

FROM node:22-slim

# Install system dependencies for Chromium and Python
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-liberation \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    libgbm1 \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libasound2 \
    libpangocairo-1.0-0 \
    libcups2 \
    libdrm2 \
    libxshmfence1 \
    curl \
    git \
    python3 \
    python3-pip \
    python3-venv \
    redis-tools \
    && rm -rf /var/lib/apt/lists/*

# Set Chromium path for agent-browser
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# Install N184 Memory Palace Python dependencies in a venv
RUN python3 -m venv /opt/n184-venv && \
    /opt/n184-venv/bin/pip install --no-cache-dir chromadb
ENV PATH="/opt/n184-venv/bin:$PATH"

# Copy N184 Memory Palace package
COPY n184_memory_palace/ /opt/n184/n184_memory_palace/
ENV PYTHONPATH="/opt/n184:$PYTHONPATH"

# Copy N184 CLI wrapper
COPY n184_palace_cli.py /opt/n184/n184_palace_cli.py
RUN printf '#!/bin/bash\nexec python3 /opt/n184/n184_palace_cli.py "$@"\n' > /usr/local/bin/n184-palace && \
    chmod +x /usr/local/bin/n184-palace

# Install agent-browser and claude-code globally
RUN npm install -g agent-browser @anthropic-ai/claude-code

# Create app directory
WORKDIR /app

# Copy agent-runner package files first for better caching
COPY agent-runner/package*.json ./

# Install dependencies (includes ioredis for Redis IPC)
RUN npm install

# Copy agent-runner source code
COPY agent-runner/ ./

# Build TypeScript
RUN npm run build

# Create workspace directories
RUN mkdir -p /workspace/group /workspace/global /workspace/extra \
    /workspace/ipc/messages /workspace/ipc/tasks /workspace/ipc/input

# Copy entrypoint scripts
COPY entrypoint.sh /app/entrypoint.sh
COPY k8s-entrypoint.sh /app/k8s-entrypoint.sh
RUN chmod +x /app/entrypoint.sh /app/k8s-entrypoint.sh

# Set ownership to node user (non-root)
RUN chown -R node:node /workspace && chmod 777 /home/node

# Default environment for k8s mode
ENV IPC_BACKEND=redis
ENV REDIS_URL=redis://redis.n184.svc.cluster.local:6379

# Switch to non-root user
USER node

# Set working directory to group workspace
WORKDIR /workspace/group

# Default entrypoint reads JSON from stdin, outputs JSON to stdout
ENTRYPOINT ["/app/entrypoint.sh"]
