/**
 * Redis IPC adapter for N184 Agent Runner
 *
 * Replaces file-based IPC with Redis pub/sub and lists.
 * Toggled via IPC_BACKEND=redis environment variable.
 *
 * Channels:
 *   n184:input:{agentName}    — Controller/other agents publish messages here
 *   n184:messages:{chatJid}   — Agent publishes outbound messages here
 *   n184:tasks                — Agent pushes task commands (schedule, pause, cancel)
 *   n184:vautrin-queue        — Honore pushes Vautrin work items (KEDA watches)
 *   n184:close:{agentName}    — Controller publishes close signal
 *   n184:job-input:{jobName}  — Controller stores ContainerInput for Jobs
 *   n184:sessions             — Hash of agent → sessionId
 */

import { Redis as IORedis } from 'ioredis';

export class RedisIPC {
  private pub: IORedis;
  private sub: IORedis;
  private agentName: string;
  private closed = false;
  /**
   * chat_jid of the most recent inbound message (e.g. "tg:8553719108"). The
   * persistent Honoré loop reads this to reply to the chat the message came
   * from, rather than a static env default — otherwise replies route to the
   * wrong jid and the controller's relay drops them.
   */
  lastChatJid: string | null = null;

  constructor(redisUrl: string, agentName: string) {
    this.agentName = agentName;
    this.pub = new IORedis(redisUrl, { lazyConnect: true, maxRetriesPerRequest: 3 });
    this.sub = new IORedis(redisUrl, { lazyConnect: true, maxRetriesPerRequest: 3 });
  }

  async connect(): Promise<void> {
    await this.pub.connect();
    await this.sub.connect();
  }

  /**
   * Subscribe to the agent's input channel and yield messages.
   * Yields message text strings. Yields null when close signal received.
   */
  async *subscribe(): AsyncGenerator<string | null> {
    const inputChannel = `n184:input:${this.agentName}`;
    const closeChannel = `n184:close:${this.agentName}`;

    const messageQueue: (string | null)[] = [];
    let resolve: (() => void) | null = null;

    const push = (item: string | null) => {
      messageQueue.push(item);
      resolve?.();
    };

    this.sub.subscribe(inputChannel, closeChannel);

    this.sub.on('message', (channel: string, message: string) => {
      if (channel === closeChannel) {
        push(null);
      } else if (channel === inputChannel) {
        try {
          const data = JSON.parse(message);
          if (data.chat_jid || data.chatJid) {
            this.lastChatJid = data.chat_jid || data.chatJid;
          }
          if (data.text) {
            push(data.text);
          } else if (typeof data === 'string') {
            push(data);
          }
        } catch {
          // Raw string message
          push(message);
        }
      }
    });

    while (!this.closed) {
      while (messageQueue.length > 0) {
        const item = messageQueue.shift()!;
        yield item;
        if (item === null) return;
      }
      await new Promise<void>((r) => {
        resolve = r;
      });
      resolve = null;
    }
  }

  /**
   * Send a message to a target (chat or agent).
   * The controller subscribes to n184:messages:* and routes accordingly.
   */
  async sendMessage(data: object): Promise<void> {
    const payload = JSON.stringify(data);
    // Publish to a channel the controller watches
    const chatJid = (data as Record<string, string>).chatJid || 'broadcast';
    await this.pub.publish(`n184:messages:${chatJid}`, payload);
  }

  /**
   * Push a task command (schedule_task, pause_task, cancel_task, etc.)
   * to the tasks list. The controller BRPOP's this list.
   */
  async pushTask(data: object): Promise<void> {
    await this.pub.lpush('n184:tasks', JSON.stringify(data));
  }

  /**
   * Push a Vautrin work item to the scaling queue.
   * KEDA watches this list to create ScaledJob instances.
   */
  async pushVautrinTask(data: object): Promise<void> {
    await this.pub.lpush('n184:vautrin-queue', JSON.stringify(data));
  }

  /**
   * Get the ContainerInput for a Job from Redis.
   * Used by k8s-entrypoint.sh wrapper.
   */
  async getJobInput(jobName: string): Promise<string | null> {
    return this.pub.get(`n184:job-input:${jobName}`);
  }

  /**
   * Claim a single task from the Vautrin queue (blocking).
   *
   * The task is moved to a processing list before execution. A crashed
   * worker therefore does not lose the task, but it also is not automatically
   * replayed into a fresh pod after an OOM.
   * Used by vautrin-entrypoint.ts.
   */
  async claimVautrinTask(timeoutSeconds: number = 30): Promise<string | null> {
    return this.pub.brpoplpush(
      'n184:vautrin-queue',
      'n184:vautrin-processing',
      timeoutSeconds,
    );
  }

  async ackVautrinTask(taskJson: string): Promise<void> {
    await this.pub.lrem('n184:vautrin-processing', 1, taskJson);
  }

  /**
   * Get/set session ID for an agent.
   */
  async getSessionId(agent: string): Promise<string | null> {
    return this.pub.hget('n184:sessions', agent);
  }

  async setSessionId(agent: string, sessionId: string): Promise<void> {
    await this.pub.hset('n184:sessions', agent, sessionId);
  }

  async getValue(key: string): Promise<string | null> {
    return this.pub.get(key);
  }

  // ── KVStore surface (see budget-guard.ts) ──────────────────────────
  // Backs the loop-safe budget cap + restart breaker. State lives in Redis
  // so it outlives a crash-restart of the agent pod.

  async get(key: string): Promise<string | null> {
    return this.pub.get(key);
  }

  async set(key: string, value: string): Promise<void> {
    await this.pub.set(key, value);
  }

  async del(key: string): Promise<void> {
    await this.pub.del(key);
  }

  async incr(key: string): Promise<number> {
    return this.pub.incr(key);
  }

  async incrByFloat(key: string, delta: number): Promise<number> {
    const result = await this.pub.incrbyfloat(key, delta);
    return Number.parseFloat(result);
  }

  /**
   * Set a TTL only if the key currently has none, so a rolling counter's
   * window is fixed at first write rather than extended on every bump.
   * Implemented via TTL+EXPIRE (two commands) for portability across Redis
   * versions rather than EXPIRE ... NX (Redis 7+ only).
   */
  async expireIfNew(key: string, seconds: number): Promise<void> {
    const ttl = await this.pub.ttl(key); // -2 = no key, -1 = no expiry, >=0 = has expiry
    if (ttl === -1) {
      await this.pub.expire(key, seconds);
    }
  }

  /**
   * Read the current tasks list (for list_tasks MCP tool).
   */
  async getCurrentTasks(): Promise<string> {
    const tasks = await this.pub.get('n184:current_tasks');
    return tasks || '[]';
  }

  async close(): Promise<void> {
    this.closed = true;
    await this.sub.unsubscribe();
    this.sub.disconnect();
    this.pub.disconnect();
  }
}
