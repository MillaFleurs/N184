/**
 * Vautrin Queue Consumer Entrypoint
 *
 * Used by KEDA ScaledJob pods. Pops a single task from the
 * n184:vautrin-queue Redis list, runs the analysis, stores
 * results in the Memory Palace, and exits.
 */

import { RedisIPC } from './redis-ipc.js';
import { getRegistry } from './providers.js';

const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const AGENT_NAME = process.env.N184_AGENT_NAME || 'vautrin';
const QUEUE_TIMEOUT = 30; // seconds to wait for a task

function log(message: string): void {
  console.error(`[vautrin-entrypoint] ${message}`);
}

async function main(): Promise<void> {
  const redisIpc = new RedisIPC(REDIS_URL, AGENT_NAME);
  await redisIpc.connect();

  log('Waiting for task from n184:vautrin-queue...');
  const taskJson = await redisIpc.claimVautrinTask(QUEUE_TIMEOUT);

  if (!taskJson) {
    log('No task received within timeout, exiting');
    await redisIpc.close();
    process.exit(0);
  }

  log(`Received task (${taskJson.length} chars)`);

  // The task carries the provider/model Honoré chose. Resolve it against the
  // registry so the worker runs on the RIGHT runtime: claude-sdk (index.js)
  // for Anthropic, openai-sdk (openai-entrypoint.js) for DeepSeek/Ollama/
  // OpenAI/etc. This used to always exec index.js, so a non-Anthropic model
  // name was handed to the Anthropic API and every multi-model Vautrin failed.
  const fs = await import('fs');
  const { execFileSync } = await import('child_process');

  const task = JSON.parse(taskJson) as { provider?: string | null; model?: string | null };
  const reg = getRegistry();
  const providerName = task.provider || 'anthropic';
  const provider = reg.get(providerName) ?? reg.get('anthropic');
  if (!provider) {
    log('No provider resolvable (is anthropic missing from the registry?) — aborting');
    await redisIpc.close();
    process.exit(1);
  }
  const model = task.model || provider.default_model;

  // Non-secret routing env the runtime reads (mirror of
  // controller/providers.py Resolved.env_overrides). The API key itself is
  // already in this worker's env (forwarded by the controller); the runtime
  // reads it by the name in N184_PROVIDER_API_KEY_ENV.
  const routeEnv: Record<string, string> = {
    N184_PROVIDER: provider.name,
    N184_MODEL: model,
    N184_PROVIDER_TYPE: provider.type,
    N184_PROVIDER_BASE_URL: provider.base_url,
    N184_PROVIDER_API_KEY_ENV: provider.api_key_env,
  };
  if (provider.type === 'anthropic') routeEnv.ANTHROPIC_BASE_URL = provider.base_url;

  const runtimeCmd =
    provider.runtime === 'openai-sdk'
      ? 'node /app/dist/openai-entrypoint.js'
      : 'node /app/dist/index.js';

  log(`Routing → provider=${provider.name} runtime=${provider.runtime} model=${model}`);

  const inputPath = '/tmp/vautrin-input.json';
  fs.writeFileSync(inputPath, taskJson);

  try {
    // Pipe the task on stdin to the resolved runtime.
    execFileSync('bash', ['-c', `cat ${inputPath} | ${runtimeCmd}`], {
      stdio: ['pipe', 'inherit', 'inherit'],
      env: {
        ...process.env,
        ...routeEnv,
        IPC_BACKEND: 'redis',
        REDIS_URL,
        N184_AGENT_NAME: AGENT_NAME,
      },
      timeout: 3600_000, // 1 hour max
    });
    await redisIpc.ackVautrinTask(taskJson);
    log('Vautrin task completed');
  } catch (err) {
    log(`Vautrin task failed: ${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  } finally {
    await redisIpc.close();
  }
}

main();
