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
   * Pop a single task from the Vautrin queue (blocking).
   * Used by vautrin-entrypoint.ts.
   */
  async popVautrinTask(timeoutSeconds: number = 30): Promise<string | null> {
    const result = await this.pub.brpop('n184:vautrin-queue', timeoutSeconds);
    return result ? result[1] : null;
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
