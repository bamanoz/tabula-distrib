/**
 * Gateway session — wraps KernelConnection to manage the driver spawn, turn
 * state, and a typed event stream that the React layer subscribes to.
 */

import { KernelConnection } from "@tabula/skill-sdk/client";
import {
	HOOK_BEFORE_TOOL_CALL,
	HOOK_BLOCK,
	HOOK_PASS,
	MSG_CANCEL,
	MSG_CONNECT,
	MSG_DONE,
	MSG_ERROR,
	MSG_HOOK,
	MSG_HOOK_RESULT,
	MSG_JOIN,
	MSG_MEMBER_JOINED,
	MSG_MESSAGE,
	MSG_STATUS,
	MSG_STREAM_DELTA,
	MSG_STREAM_END,
	MSG_STREAM_START,
	MSG_TOOL_RESULT,
	MSG_TOOL_USE,
	TOOL_PROCESS_KILL,
	TOOL_PROCESS_SPAWN,
	type Envelope,
} from "@tabula/skill-sdk/protocol";

/** Tools that require interactive user approval before executing. */
const APPROVAL_TOOLS = new Set<string>([
	"apply_patch",
	"write",
	"edit",
	"multiedit",
	"shell_exec",
	"process_spawn",
]);

export type ThreadEntry =
	| { kind: "user"; id: string; text: string }
	| { kind: "assistant"; id: string; text: string; streaming: boolean }
	| { kind: "tool_use"; id: string; name: string; input: unknown; status: "pending" | "done" | "error" }
	| { kind: "tool_result"; id: string; for: string; output: string; isError?: boolean }
	| { kind: "status"; id: string; text: string }
	| { kind: "error"; id: string; text: string };

export interface GatewayEvents {
	onEntry?: (entry: ThreadEntry) => void;
	onEntryUpdate?: (id: string, patch: Partial<ThreadEntry>) => void;
	onAssistantDelta?: (id: string, delta: string) => void;
	onTurnState?: (state: TurnState) => void;
	onApproval?: (req: ApprovalRequest) => void;
	onAskUser?: (req: AskUserRequest) => void;
	onDisconnect?: () => void;
}

export type TurnState = "idle" | "waiting" | "streaming";

export interface ApprovalRequest {
	id: string;
	hookId: string;
	toolName: string;
	input: unknown;
	prompt: string;
}

export interface AskUserRequest {
	id: string;
	question: string;
	options: string[];
}

export interface GatewayOpts {
	url: string;
	sessionId: string;
	driverCommand: string;
	agent?: string;
	model?: string;
	driverSpawnTimeoutMs?: number;
}

export class Gateway {
	readonly sessionId: string;
	private readonly url: string;
	private readonly driverCmd: string;
	private activeAgent: string;
	private activeModel?: string;
	private readonly driverSpawnTimeout: number;
	private conn: KernelConnection | null = null;
	private driverPid: number | null = null;
	private events: GatewayEvents = {};
	private turnState: TurnState = "idle";
	private currentAssistantId: string | null = null;
	private nextId = 1;
	private loopStarted = false;
	private pendingInternalSpawns = new Set<string>();

	constructor(opts: GatewayOpts) {
		this.url = opts.url;
		this.sessionId = opts.sessionId;
		this.driverCmd = opts.driverCommand;
		this.activeAgent = opts.agent ?? "build";
		this.activeModel = opts.model;
		this.driverSpawnTimeout = opts.driverSpawnTimeoutMs ?? 15_000;
	}

	setAgent(agent: string): void {
		this.activeAgent = agent;
	}

	setModel(model: string | undefined): void {
		this.activeModel = model;
	}

	on(events: GatewayEvents) {
		this.events = { ...this.events, ...events };
	}

	async start(): Promise<void> {
		this.conn = new KernelConnection(this.url);
		await this.conn.ready();

		await this.conn.send({
			type: MSG_CONNECT,
			name: `tui-${this.sessionId}`,
			sends: [MSG_MESSAGE, MSG_STATUS, MSG_CANCEL, MSG_TOOL_USE, MSG_HOOK_RESULT],
			receives: [
				MSG_STREAM_START,
				MSG_STREAM_DELTA,
				MSG_STREAM_END,
				MSG_DONE,
				MSG_ERROR,
				MSG_TOOL_RESULT,
				MSG_TOOL_USE,
				MSG_STATUS,
				MSG_MEMBER_JOINED,
				MSG_HOOK,
			],
			hooks: [
				// Interactive approval hook. Sits below file-rule hook-approvals
				// (priority 80); only fires for calls that no rule approved.
				// timeout_ms=0 → wait until the user replies or this client dies.
				{ event: HOOK_BEFORE_TOOL_CALL, priority: 10, timeout_ms: 0 },
			],
		});
		await this.conn.recv(5_000); // connected ack

		await this.conn.send({ type: MSG_JOIN, session: this.sessionId });
		await this.conn.recv(5_000); // joined ack

		await this.spawnDriver();
		this.startLoop();
	}

	private async spawnDriver(): Promise<void> {
		if (!this.conn) throw new Error("not connected");
		const spawnId = "spawn-driver";
		this.pendingInternalSpawns.add(spawnId);
		try {
			await this.conn.send({
				type: MSG_TOOL_USE,
				id: spawnId,
				name: TOOL_PROCESS_SPAWN,
				input: { command: `${this.driverCmd} --session ${this.sessionId}` },
			});

			const deadline = Date.now() + this.driverSpawnTimeout;
			let sawPid = false;
			let sawJoined = false;
			while (Date.now() < deadline) {
				const timeout = Math.max(100, deadline - Date.now());
				let msg: Envelope | null;
				try {
					msg = await this.conn.recv(timeout);
				} catch {
					throw new Error("timeout waiting for driver spawn");
				}
				if (msg === null) throw new Error("connection closed during driver spawn");

				if (msg.type === MSG_TOOL_RESULT && msg.id === spawnId) {
					const output = String(msg.output ?? "");
					const m = output.match(/PID (\d+)/);
					if (!m) throw new Error(`driver spawn failed: ${output}`);
					this.driverPid = Number(m[1]);
					sawPid = true;
					if (sawJoined) return;
				} else if (msg.type === MSG_MEMBER_JOINED) {
					sawJoined = true;
					if (sawPid) return;
				} else if (msg.type === MSG_ERROR) {
					throw new Error(String(msg.text ?? "unknown error during spawn"));
				}
			}
			throw new Error("timeout waiting for driver spawn");
		} finally {
			this.pendingInternalSpawns.delete(spawnId);
		}
	}

	private startLoop(): void {
		if (this.loopStarted || !this.conn) return;
		this.loopStarted = true;
		void this.runLoop();
	}

	private async runLoop(): Promise<void> {
		if (!this.conn) return;
		for await (const msg of this.conn.messages()) {
			this.handleMessage(msg);
		}
		this.events.onDisconnect?.();
	}

	private handleMessage(msg: Envelope): void {
		switch (msg.type) {
			case MSG_STREAM_START: {
				const id = `a-${this.nextId++}`;
				this.currentAssistantId = id;
				this.setTurnState("streaming");
				this.events.onEntry?.({ kind: "assistant", id, text: "", streaming: true });
				break;
			}
			case MSG_STREAM_DELTA: {
				if (!this.currentAssistantId) {
					const id = `a-${this.nextId++}`;
					this.currentAssistantId = id;
					this.events.onEntry?.({ kind: "assistant", id, text: "", streaming: true });
				}
				const delta = String(msg.text ?? "");
				if (delta) {
					this.events.onAssistantDelta?.(this.currentAssistantId, delta);
				}
				break;
			}
			case MSG_STREAM_END: {
				if (this.currentAssistantId) {
					this.events.onEntryUpdate?.(this.currentAssistantId, { streaming: false });
				}
				this.currentAssistantId = null;
				break;
			}
			case MSG_DONE: {
				this.currentAssistantId = null;
				this.setTurnState("idle");
				break;
			}
			case MSG_TOOL_USE: {
				if (msg.id && msg.id === "spawn-driver") break;
				const id = String(msg.id ?? `t-${this.nextId++}`);
				this.events.onEntry?.({
					kind: "tool_use",
					id,
					name: String(msg.name ?? ""),
					input: msg.input ?? {},
					status: "pending",
				});
				break;
			}
			case MSG_TOOL_RESULT: {
				if (msg.id === "spawn-driver") break;
				const id = String(msg.id ?? "");
				const output = typeof msg.output === "string" ? msg.output : JSON.stringify(msg.output);
				this.events.onEntryUpdate?.(id, { status: "done" });
				this.events.onEntry?.({
					kind: "tool_result",
					id: `r-${this.nextId++}`,
					for: id,
					output,
				});
				break;
			}
			case MSG_STATUS: {
				// Check for ask_request in meta
				const meta = msg.meta as Record<string, unknown> | undefined;
				if (meta?.ask_request && typeof meta.ask_request === "object") {
					const req = meta.ask_request as Record<string, unknown>;
					this.events.onAskUser?.({
						id: String(req.id ?? ""),
						question: String(req.question ?? ""),
						options: Array.isArray(req.options) ? req.options.map(String) : [],
					});
					break;
				}
				this.events.onEntry?.({
					kind: "status",
					id: `s-${this.nextId++}`,
					text: String(msg.text ?? ""),
				});
				break;
			}
			case MSG_HOOK: {
				if (msg.name !== HOOK_BEFORE_TOOL_CALL) break;
				this.handleApprovalHook(msg);
				break;
			}
			case MSG_ERROR: {
				this.events.onEntry?.({
					kind: "error",
					id: `e-${this.nextId++}`,
					text: String(msg.text ?? "unknown error"),
				});
				this.setTurnState("idle");
				break;
			}
		}
	}

	private setTurnState(state: TurnState): void {
		if (this.turnState === state) return;
		this.turnState = state;
		this.events.onTurnState?.(state);
	}

	async sendUserMessage(text: string): Promise<void> {
		if (!this.conn) throw new Error("not connected");
		const id = `u-${this.nextId++}`;
		this.events.onEntry?.({ kind: "user", id, text });
		this.setTurnState("waiting");
		await this.conn.send({
			type: MSG_MESSAGE,
			text,
			meta: {
				agent: this.activeAgent,
				...(this.activeModel ? { model: this.activeModel } : {}),
			},
		});
	}

	async cancel(): Promise<void> {
		if (!this.conn) return;
		await this.conn.send({ type: MSG_CANCEL });
		this.setTurnState("idle");
	}

	/**
	 * Send an approval verdict to the kernel hook engine. The TUI is itself
	 * the before_tool_call hook subscriber (timeout_ms=0), so the verdict
	 * goes out as a MSG_HOOK_RESULT keyed by the hook ID.
	 */
	async replyApproval(hookId: string, choice: "allow" | "deny" | "abort"): Promise<void> {
		if (!this.conn) return;
		const action = choice === "allow" ? HOOK_PASS : HOOK_BLOCK;
		const reason =
			choice === "abort"
				? "aborted by user"
				: choice === "deny"
					? "denied by user"
					: undefined;
		await this.conn.send({
			type: MSG_HOOK_RESULT,
			id: hookId,
			action,
			...(reason ? { reason } : {}),
		});
	}

	/**
	 * Reply to an ask_user request from a skill. The response is sent as status
	 * metadata so it does not start a competing user turn while the driver is
	 * waiting for the ask_user tool result.
	 */
	async replyAskUser(requestId: string, choice: string, index: number): Promise<void> {
		if (!this.conn) return;
		await this.conn.send({
			type: MSG_STATUS,
			text: "",
			meta: {
				ask_response: {
					id: requestId,
					choice,
					index,
				},
			},
		});
	}

	/**
	 * Persist an approval rule via MSG_MESSAGE meta. The hook-approvals skill
	 * declares receives_global: ["message"], so it receives this message from
	 * any session and writes to rules.json.
	 */
	async sendRule(rule: {
		tool: string;
		path?: string;
		command?: string;
		effect: "allow_always" | "deny_always";
	}): Promise<void> {
		if (!this.conn) return;
		await this.conn.send({
			type: MSG_MESSAGE,
			text: "",
			meta: { rule_add: rule },
		});
	}

	/**
	 * before_tool_call fires here. Auto-pass for already-approved or
	 * non-sensitive tools; otherwise surface an ApprovalRequest for the UI.
	 */
	private handleApprovalHook(msg: Envelope): void {
		const hookId = String(msg.id ?? "");
		if (!hookId) return;
		const payload = (msg.payload ?? {}) as Record<string, unknown>;
		const tool = String(payload.tool ?? "");
		const inputRaw = payload.input;
		let input: Record<string, unknown> = {};
		if (inputRaw && typeof inputRaw === "object") {
			input = inputRaw as Record<string, unknown>;
		} else if (typeof inputRaw === "string") {
			try {
				const parsed = JSON.parse(inputRaw);
				if (parsed && typeof parsed === "object") input = parsed;
			} catch {
				// leave as {}
			}
		}

		if (input.approved === true || !APPROVAL_TOOLS.has(tool) || this.pendingInternalSpawns.has(String(payload.id ?? ""))) {
			void this.conn
				?.send({ type: MSG_HOOK_RESULT, id: hookId, action: HOOK_PASS })
				.catch(() => {});
			return;
		}

		this.events.onApproval?.({
			id: `ap-${this.nextId++}`,
			hookId,
			toolName: tool,
			input,
			prompt: `Approve ${tool}?`,
		});
	}

	async stop(): Promise<void> {
		if (this.driverPid !== null && this.conn) {
			try {
				await this.conn.send({
					type: MSG_TOOL_USE,
					id: "kill-driver",
					name: TOOL_PROCESS_KILL,
					input: { pid: this.driverPid },
				});
			} catch {
				// ignore
			}
		}
		this.conn?.close();
	}
}
