/**
 * Vautrin Queue Consumer Entrypoint
 *
 * Used by KEDA ScaledJob pods. Pops a single task from the
 * n184:vautrin-queue Redis list, runs the analysis, stores
 * results in the Memory Palace, and exits.
 */

import { RedisIPC } from './redis-ipc.js';

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
  const taskJson = await redisIpc.popVautrinTask(QUEUE_TIMEOUT);

  if (!taskJson) {
    log('No task received within timeout, exiting');
    await redisIpc.close();
    process.exit(0);
  }

  log(`Received task (${taskJson.length} chars)`);

  // Parse the task as ContainerInput and pipe it to stdin of the main
  // agent-runner process. We do this by writing to a temp file and
  // exec'ing the main entrypoint.
  const fs = await import('fs');
  const { execFileSync } = await import('child_process');

  const inputPath = '/tmp/vautrin-input.json';
  fs.writeFileSync(inputPath, taskJson);

  try {
    // Run the main agent-runner with the task as stdin
    execFileSync('bash', ['-c', `cat ${inputPath} | node /tmp/dist/index.js`], {
      stdio: ['pipe', 'inherit', 'inherit'],
      env: {
        ...process.env,
        IPC_BACKEND: 'redis',
        REDIS_URL,
        N184_AGENT_NAME: AGENT_NAME,
      },
      timeout: 3600_000, // 1 hour max
    });
    log('Vautrin task completed');
  } catch (err) {
    log(`Vautrin task failed: ${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  } finally {
    await redisIpc.close();
  }
}

main();
