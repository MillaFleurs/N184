/**
 * Provider registry — TypeScript loader, mirror of controller/providers.py.
 *
 * Reads the same YAML files as the Python loader. Used by the agent-runner
 * MCP server (`list_providers`, `register_provider`) and by the agent-runner
 * runtimes themselves.
 *
 * Like its Python counterpart, this module touches NO API keys. It only reads
 * env-var *names* from the registry.
 */

import fs from 'fs';
import path from 'path';
import YAML from 'yaml';

export type ProviderType = 'anthropic' | 'openai' | 'openai-compat';
export type RuntimeKind = 'claude-sdk' | 'openai-sdk';

export interface Provider {
  name: string;
  type: ProviderType;
  base_url: string;
  api_key_env: string;
  default_model: string;
  runtime: RuntimeKind;
  notes: string;
}

const KNOWN_TYPES: ReadonlySet<ProviderType> = new Set(['anthropic', 'openai', 'openai-compat']);
const KNOWN_RUNTIMES: ReadonlySet<RuntimeKind> = new Set(['claude-sdk', 'openai-sdk']);

const DEFAULT_PATHS = [
  '/etc/n184/providers/registry.yaml',
  '/etc/n184/providers/registry.local.yaml',
  // Fallback for local dev — relative to dist/ at runtime.
  path.resolve(process.cwd(), 'providers', 'registry.yaml'),
  path.resolve(process.cwd(), 'providers', 'registry.local.yaml'),
];

function validate(p: Provider): void {
  if (!KNOWN_TYPES.has(p.type)) {
    throw new Error(`Provider ${p.name}: unknown type "${p.type}"`);
  }
  if (!KNOWN_RUNTIMES.has(p.runtime)) {
    throw new Error(`Provider ${p.name}: unknown runtime "${p.runtime}"`);
  }
  if (!p.base_url) {
    throw new Error(`Provider ${p.name}: base_url is required`);
  }
  if (p.type === 'anthropic' && p.runtime !== 'claude-sdk') {
    throw new Error(`Provider ${p.name}: type=anthropic requires runtime=claude-sdk`);
  }
}

class Registry {
  private providers: Map<string, Provider>;
  // Runtime-only additions made via the register_provider MCP tool. Not persisted.
  private overlays: Map<string, Provider> = new Map();

  constructor(providers: Map<string, Provider>) {
    this.providers = providers;
  }

  static load(paths: string[] = DEFAULT_PATHS): Registry {
    const merged: Record<string, Partial<Provider>> = {};
    let loadedAny = false;

    for (const p of paths) {
      if (!fs.existsSync(p)) continue;
      loadedAny = true;
      const doc = YAML.parse(fs.readFileSync(p, 'utf-8')) || {};
      const entries = (doc.providers || {}) as Record<string, Partial<Provider>>;
      for (const [name, entry] of Object.entries(entries)) {
        merged[name] = { ...(merged[name] || {}), ...entry };
      }
    }

    if (!loadedAny) {
      throw new Error(
        `No provider registry found. Looked in: ${paths.join(', ')}`,
      );
    }

    const providers = new Map<string, Provider>();
    for (const [name, entry] of Object.entries(merged)) {
      const p: Provider = {
        name,
        type: (entry.type ?? '') as ProviderType,
        base_url: entry.base_url ?? '',
        api_key_env: entry.api_key_env ?? '',
        default_model: entry.default_model ?? '',
        runtime: (entry.runtime ?? '') as RuntimeKind,
        notes: entry.notes ?? '',
      };
      validate(p);
      providers.set(name, p);
    }

    if (providers.size === 0) {
      throw new Error('Provider registry is empty');
    }

    return new Registry(providers);
  }

  names(): string[] {
    return Array.from(new Set([...this.providers.keys(), ...this.overlays.keys()])).sort();
  }

  get(name: string): Provider | undefined {
    return this.overlays.get(name) ?? this.providers.get(name);
  }

  list(): Provider[] {
    return this.names().map((n) => this.get(n)!);
  }

  /**
   * Register a provider at runtime (in-memory only). Used by the
   * `register_provider` MCP tool so a Honoré in the field can hot-add
   * a backend without redeploying. Does NOT modify registry.yaml.
   */
  registerOverlay(p: Provider): void {
    validate(p);
    this.overlays.set(p.name, p);
  }
}

let singleton: Registry | null = null;

export function getRegistry(): Registry {
  if (singleton === null) {
    const override = process.env.N184_PROVIDER_REGISTRY_PATHS;
    const paths = override ? override.split(':').filter(Boolean) : DEFAULT_PATHS;
    singleton = Registry.load(paths);
  }
  return singleton;
}

export function resetRegistryForTests(): void {
  singleton = null;
}
