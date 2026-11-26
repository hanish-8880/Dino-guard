// Type declarations for dinoguard.mjs (CLAUDE.md §17.9).
export interface Decision { decision: "allow" | "instruct" | "deny"; reason: string | null; policy: string | null }
export interface Options {
  url?: string; sessionId?: string; agentId?: string; environment?: string; harness?: string; cwd?: string;
  token?: string; raiseOnDeny?: boolean; failClosed?: boolean;
}
export interface Usage { tokensIn?: number; tokensOut?: number; cacheRead?: number; cacheWrite?: number; costUsd?: number; latencyMs?: number }
export interface ToolResult { output?: unknown; error?: string | null; durationMs?: number | null; toolUseId?: string | null }

export class DinoGuardDenied extends Error { decision: Decision }

export class DinoGuard {
  constructor(opts?: Options);
  sessionId: string;
  send(events: object[]): Promise<Decision[]>;
  event(event: string, fields?: object): Promise<Decision>;
  start(meta?: object): Promise<Decision>;
  end(status?: string, meta?: object): Promise<Decision>;
  prompt(text: string): Promise<Decision>;
  stop(final?: string): Promise<Decision>;
  modelRequest(model: string, meta?: object): Promise<Decision>;
  modelResponse(model: string, usage?: Usage): Promise<Decision>;
  toolCall(tool: string, input?: object, toolUseId?: string | null): Promise<Decision>;
  toolResult(tool: string, input?: object, result?: ToolResult): Promise<Decision>;
  runTool<T>(tool: string, input: object, fn: () => Promise<T> | T): Promise<T>;
  hookStart(name: string, meta?: object): Promise<Decision>;
  hookEnd(name: string, meta?: object): Promise<Decision>;
  humanWait(reason?: string): Promise<Decision>;
  humanInput(text?: string): Promise<Decision>;
  interruption(reason?: string): Promise<Decision>;
  error(err: unknown): Promise<Decision>;
  pause(reason?: string): Promise<Decision>;
  resume(): Promise<Decision>;
}
