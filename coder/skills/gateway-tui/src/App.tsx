/**
 * Top-level Ink component. Owns thread state, panel visibility, approval
 * request, and wires Gateway events -> React state.
 */

import { Box, Text, useApp } from "ink";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Approval } from "./components/Approval.tsx";
import { AskUser } from "./components/AskUser.tsx";
import { Input } from "./components/Input.tsx";
import { StatusBar } from "./components/StatusBar.tsx";
import { Thread } from "./components/Thread.tsx";
import { Agents } from "./components/panels/Agents.tsx";
import { Diff } from "./components/panels/Diff.tsx";
import { Review } from "./components/panels/Review.tsx";
import { Todo } from "./components/panels/Todo.tsx";
import { findCommand, SLASH_COMMANDS, type PanelId, type SlashContext } from "./slash.ts";
import type { ApprovalRequest, AskUserRequest, Gateway, ThreadEntry, TurnState } from "./session.ts";
import type { AgentInfo } from "./index.tsx";

export interface AppProps {
	gateway: Gateway;
	initialProvider: string;
	initialAgent: string;
	initialModel?: string;
	providers: string[];
	agents: AgentInfo[];
}

export function App({ gateway, initialProvider, initialAgent, initialModel, providers, agents }: AppProps) {
	const { exit } = useApp();
	const [entries, setEntries] = useState<ThreadEntry[]>([]);
	const [turn, setTurn] = useState<TurnState>("idle");
	const [panel, setPanel] = useState<PanelId>(null);
	const [approval, setApproval] = useState<ApprovalRequest | null>(null);
	const [askUser, setAskUser] = useState<AskUserRequest | null>(null);
	const [provider, setProvider] = useState(initialProvider);
	const [agent, setAgentState] = useState(initialAgent);
	const [model, setModelState] = useState<string | undefined>(initialModel);
	const [transcriptLines, setTranscriptLines] = useState<string[]>([]);

	const appendLine = useCallback((line: string) => {
		setTranscriptLines((prev) => [...prev, line]);
	}, []);

	const clearThread = useCallback(() => {
		setEntries([]);
		setTranscriptLines([]);
	}, []);

	const cycleProvider = useCallback((): string => {
		let next = provider;
		setProvider((p) => {
			const idx = providers.indexOf(p);
			next = providers[(idx + 1) % providers.length] ?? p;
			return next;
		});
		return next;
	}, [provider, providers]);

	const setAgent = useCallback((name: string): boolean => {
		if (!agents.some((a) => a.name === name)) return false;
		gateway.setAgent(name);
		setAgentState(name);
		return true;
	}, [agents, gateway]);

	const setModel = useCallback((next: string | undefined): void => {
		const normalized = next?.trim() || undefined;
		gateway.setModel(normalized);
		setModelState(normalized);
		if (normalized?.includes("/")) {
			const [p] = normalized.split("/", 1);
			if (p && providers.includes(p)) setProvider(p);
		}
	}, [gateway, providers]);

	useEffect(() => {
		gateway.on({
			onEntry: (entry) => {
				setEntries((prev) => [...prev, entry]);
			},
			onEntryUpdate: (id, patch) => {
				setEntries((prev) =>
					prev.map((e) => (e.id === id ? ({ ...e, ...patch } as ThreadEntry) : e)),
				);
			},
			onAssistantDelta: (id, delta) => {
				setEntries((prev) =>
					prev.map((e) => {
						if (e.id !== id || e.kind !== "assistant") return e;
						return { ...e, text: e.text + delta };
					}),
				);
			},
			onTurnState: (state) => setTurn(state),
			onApproval: (req) => setApproval(req),
			onAskUser: (req) => setAskUser(req),
			onDisconnect: () => {
				appendLine("[disconnected from kernel]");
			},
		});
	}, [gateway, appendLine]);

	const slashCtx: SlashContext = useMemo(
		() => ({
			gateway,
			showPanel: (p) => setPanel(p),
			clearThread,
			exit: () => {
				void gateway.stop().finally(() => exit());
			},
			cycleProvider,
			setAgent,
			setModel,
			listAgents: () => agents,
			print: appendLine,
		}),
		[gateway, clearThread, cycleProvider, setAgent, setModel, agents, appendLine, exit],
	);

	const handleSubmit = useCallback(
		async (raw: string) => {
			const text = raw.trim();
			if (!text) return;
			if (text.startsWith("/")) {
				const [head, ...rest] = text.slice(1).split(/\s+/);
				const cmd = head ? findCommand(head) : undefined;
				if (!cmd) {
					appendLine(`unknown command: /${head ?? ""}`);
					return;
				}
				try {
					await cmd.run(rest.join(" "), slashCtx);
				} catch (err) {
					appendLine(`command error: ${(err as Error).message}`);
				}
				return;
			}
			await gateway.sendUserMessage(text);
		},
		[gateway, slashCtx, appendLine],
	);

	const handleApproval = useCallback(
		(choice: "allow-once" | "allow-always" | "deny-once" | "deny-always" | "abort") => {
			if (!approval) return;
			const hookId = approval.hookId;
			const toolName = approval.toolName;
			const input = approval.input as Record<string, unknown> | undefined;
			appendLine(`approval: ${choice} for ${toolName}`);
			setApproval(null);
			void (async () => {
				try {
					if (choice === "allow-once" || choice === "allow-always") {
						if (choice === "allow-always") {
							const rule: {
								tool: string;
								path?: string;
								command?: string;
								effect: "allow_always";
							} = { tool: toolName, effect: "allow_always" };
							if (input?.path && typeof input.path === "string") {
								rule.path = input.path;
							}
							if (input?.command && typeof input.command === "string") {
								rule.command = input.command;
							}
							await gateway.sendRule(rule);
						}
						await gateway.replyApproval(hookId, "allow");
					} else if (choice === "deny-once" || choice === "deny-always") {
						if (choice === "deny-always") {
							const rule: {
								tool: string;
								path?: string;
								command?: string;
								effect: "deny_always";
							} = { tool: toolName, effect: "deny_always" };
							if (input?.path && typeof input.path === "string") {
								rule.path = input.path;
							}
							if (input?.command && typeof input.command === "string") {
								rule.command = input.command;
							}
							await gateway.sendRule(rule);
						}
						await gateway.replyApproval(hookId, "deny");
					} else {
						await gateway.replyApproval(hookId, "abort");
						await gateway.cancel();
					}
				} catch (err) {
					appendLine(`approval send error: ${(err as Error).message}`);
				}
			})();
		},
		[approval, appendLine, gateway],
	);

	const handleAskUser = useCallback(
		(choice: string, index: number) => {
			if (!askUser) return;
			const reqId = askUser.id;
			setAskUser(null);
			void gateway.replyAskUser(reqId, choice, index).catch((err) => {
				appendLine(`ask reply error: ${(err as Error).message}`);
			});
		},
		[askUser, appendLine, gateway],
	);

	const panelNode = (() => {
		switch (panel) {
			case "todo":
				return <Todo onClose={() => setPanel(null)} />;
			case "agents":
				return <Agents onClose={() => setPanel(null)} />;
			case "diff":
				return <Diff onClose={() => setPanel(null)} />;
			case "review":
				return <Review onClose={() => setPanel(null)} />;
			case "approvals":
				return (
					<Box flexDirection="column" borderStyle="round" padding={1}>
						<Text bold>approvals</Text>
						<Text dimColor>(see config/skills/hook-approvals/rules.json)</Text>
						<Text dimColor>press Esc to close</Text>
					</Box>
				);
			case "sessions":
				return (
					<Box flexDirection="column" borderStyle="round" padding={1}>
						<Text bold>sessions</Text>
						<Text dimColor>(see state/sessions/*.json)</Text>
						<Text dimColor>press Esc to close</Text>
					</Box>
				);
			default:
				return null;
		}
	})();

	return (
		<Box flexDirection="column">
			<Thread entries={entries} transcript={transcriptLines} />
			{panelNode}
			{approval ? (
				<Approval request={approval} onChoice={handleApproval} />
			) : askUser ? (
				<AskUser request={askUser} onChoice={handleAskUser} />
			) : (
				<Input
					onSubmit={handleSubmit}
					slashHints={SLASH_COMMANDS.map((c) => c.name)}
					disabled={turn !== "idle"}
				/>
			)}
			<StatusBar
				session={gateway.sessionId}
				turn={turn}
				provider={provider}
				agent={agent}
				model={model}
				panel={panel}
			/>
		</Box>
	);
}
