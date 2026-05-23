/**
 * Honore Persistent Entrypoint
 *
 * Runs as a k8s Deployment (always-on). Subscribes to Redis for
 * incoming messages and starts Claude Code query loops.
 *
 * Unlike the standard entrypoint that reads ContainerInput from stdin,
 * this constructs ContainerInput from the first Redis message and
 * keeps the agent alive for subsequent messages.
 */

import { RedisIPC } from './redis-ipc.js';
import {
  checkBudget,
  isTripped,
  recordRestart,
  resetBreaker,
  loadBreakerConfig,
  loadBudgetConfig,
  resetToken,
} from './budget-guard.js';
import { execFile } from 'child_process';
import fs from 'fs';

const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const AGENT_NAME = process.env.N184_AGENT_NAME || 'honore';
const ASSISTANT_NAME = process.env.ASSISTANT_NAME || 'Honoré';
const GROUP_FOLDER = process.env.N184_GROUP_FOLDER || 'main';
const CHAT_JID = process.env.N184_CHAT_JID || 'telegram';

function log(message: string): void {
  console.error(`[honore-entrypoint] ${message}`);
}

interface ContainerInput {
  prompt: string;
  sessionId?: string;
  groupFolder: string;
  chatJid: string;
  isMain: boolean;
  isScheduledTask?: boolean;
  assistantName?: string;
  contextMode?: 'group' | 'isolated' | 'recovery';
}

function recoveryPrompt(text: string): string {
  return `[RESTART RECOVERY MODE]

Honoré restarted with a persisted session present. Do not dispatch new agents yet.
First inspect current swarm state, queued work, processing work, and recent scan
artifacts. Report what is running or stranded, what would be needed to continue,
and ask the operator before spawning or resuming any sub-agent work.

Operator message:
${text}`;
}

async function main(): Promise<void> {
  const redisIpc = new RedisIPC(REDIS_URL, AGENT_NAME);
  await redisIpc.connect();

  log(`Honore persistent mode started (agent: ${AGENT_NAME})`);
  log(`Subscribing to n184:input:${AGENT_NAME}...`);

  // Restore session
  const sessionId = await redisIpc.getSessionId(AGENT_NAME);
  if (sessionId) {
    log(`Restored session: ${sessionId}`);
  }
  let recoveryPending =
    Boolean(sessionId) && process.env.N184_HONORE_RECOVERY_ON_RESTART !== '0';

  const notify = async (text: string): Promise<void> => {
    try {
      await redisIpc.sendMessage({ chatJid: CHAT_JID, text, sender: ASSISTANT_NAME });
    } catch (e) {
      log(`notify failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // Restart circuit-breaker + loop-safe budget gate. State lives in Redis so it
  // survives the pod restart an OOM/crash triggers; without this a crash loop
  // re-burns the operator's Claude capacity on every restart (the SDK's
  // per-query budget resets each time).
  const breakerConfig = loadBreakerConfig();
  const budgetConfig = loadBudgetConfig();
  const RESET = resetToken();
  const SCAN_ID = process.env.N184_SCAN_ID || undefined;

  let holding = false;
  const tripped = await isTripped(redisIpc, AGENT_NAME);
  if (tripped) {
    holding = true;
    log(`Restart breaker already tripped: ${tripped.reason}`);
    await notify(
      `⏸️ Honoré restarted with the breaker tripped (${tripped.reason}). Holding — send "${RESET}" to resume.`,
    );
  } else {
    const r = await recordRestart(redisIpc, AGENT_NAME, breakerConfig);
    log(`Start #${r.count} within ${breakerConfig.windowSec}s (limit ${breakerConfig.maxRestarts})`);
    if (r.tripped) {
      holding = true;
      await notify(
        `🛑 Honoré restart breaker tripped: ${r.state?.reason}. Holding — no queries will run until you send "${RESET}".`,
      );
    }
  }

  // Wait for first message, then pipe as ContainerInput to main runner
  for await (const msg of redisIpc.subscribe()) {
    if (msg === null) {
      log('Close signal received, exiting');
      break;
    }

    // While the breaker is held, run nothing until the operator sends the reset
    // token. This is what actually stops the restart loop from doing paid work.
    if (holding) {
      if (msg.trim() === RESET) {
        await resetBreaker(redisIpc, AGENT_NAME);
        holding = false;
        log('Breaker reset by operator');
        await notify('✅ Breaker reset — Honoré resuming normal operation.');
      } else {
        log(`Breaker held; ignoring message (send "${RESET}" to resume)`);
      }
      continue;
    }

    // Loop-safe budget gate before spawning a paid query.
    const budget = await checkBudget(redisIpc, { config: budgetConfig, scanId: SCAN_ID });
    if (!budget.allowed) {
      log(`Budget gate: ${budget.reason} — skipping query`);
      await notify(`💸 Budget cap reached: ${budget.reason}. Holding paid work.`);
      continue;
    }

    log(`Received message (${msg.length} chars), starting agent query`);

    const contextMode = recoveryPending ? 'recovery' : 'group';
    const activeSessionId = recoveryPending
      ? undefined
      : (await redisIpc.getSessionId(AGENT_NAME)) || undefined;

    const containerInput: ContainerInput = {
      prompt: recoveryPending ? recoveryPrompt(msg) : msg,
      sessionId: activeSessionId,
      groupFolder: GROUP_FOLDER,
      chatJid: redisIpc.lastChatJid || CHAT_JID,
      isMain: true,
      assistantName: ASSISTANT_NAME,
      contextMode,
    };
    recoveryPending = false;

    const inputPath = '/tmp/honore-input.json';
    fs.writeFileSync(inputPath, JSON.stringify(containerInput));

    // Run the main agent-runner. It will subscribe to Redis for
    // follow-up messages during the query and exit when done.
    try {
      const { spawn } = await import('child_process');
      const child = spawn(
        'bash',
        ['-c', `cat ${inputPath} | node /app/dist/index.js`],
        {
          stdio: ['pipe', 'inherit', 'inherit'],
          env: {
            ...process.env,
            IPC_BACKEND: 'redis',
            REDIS_URL,
            N184_AGENT_NAME: AGENT_NAME,
          },
        },
      );

      await new Promise<void>((resolve, reject) => {
        child.on('exit', (code) => {
          if (code === 0) resolve();
          else reject(new Error(`Agent exited with code ${code}`));
        });
        child.on('error', reject);
      });

      log('Query cycle completed, waiting for next message...');
    } catch (err) {
      log(`Query error: ${err instanceof Error ? err.message : String(err)}`);
      // Don't exit — wait for next message
    }
  }

  await redisIpc.close();
  log('Honore entrypoint exiting');
}

main();
