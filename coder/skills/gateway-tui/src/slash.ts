/**
 * Slash command registry. Handlers run either against the Gateway session
 * (send something over the wire) or against local skill tools (read state).
 */

import type { Gateway } from "./session.ts";

export type PanelId = "todo" | "agents" | "diff" | "review" | "approvals" | "sessions" | null;

export interface SlashContext {
	gateway: Gateway;
	showPanel: (panel: PanelId) => void;
	clearThread: () => void;
	exit: () => void;
	cycleProvider: () => string;
	setAgent: (agent: string) => boolean;
	setModel: (model: string | undefined) => void;
	listAgents: () => Array<{ name: string; description: string }>;
	print: (line: string) => void;
}

export interface SlashCommand {
	name: string;
	description: string;
	run(args: string, ctx: SlashContext): Promise<void> | void;
}

export const SLASH_COMMANDS: SlashCommand[] = [
	{
		name: "help",
		description: "list slash commands",
		run(_args, ctx) {
			for (const cmd of SLASH_COMMANDS) {
				ctx.print(`  /${cmd.name.padEnd(10)} ${cmd.description}`);
			}
		},
	},
	{
		name: "quit",
		description: "exit the gateway",
		run(_args, ctx) {
			ctx.exit();
		},
	},
	{
		name: "exit",
		description: "exit the gateway",
		run(_args, ctx) {
			ctx.exit();
		},
	},
	{
		name: "clear",
		description: "clear the thread view",
		run(_args, ctx) {
			ctx.clearThread();
		},
	},
	{
		name: "cancel",
		description: "cancel the current turn",
		async run(_args, ctx) {
			await ctx.gateway.cancel();
		},
	},
	{
		name: "todo",
		description: "show the session todo list",
		run(_args, ctx) {
			ctx.showPanel("todo");
		},
	},
	{
		name: "agents",
		description: "list subagents for the current session",
		run(_args, ctx) {
			ctx.showPanel("agents");
		},
	},
	{
		name: "diff",
		description: "show working-tree diff (staged + unstaged)",
		run(_args, ctx) {
			ctx.showPanel("diff");
		},
	},
	{
		name: "review",
		description: "pre-commit review checklist + diff summary",
		run(_args, ctx) {
			ctx.showPanel("review");
		},
	},
	{
		name: "approvals",
		description: "list hook-approvals rules",
		run(_args, ctx) {
			ctx.showPanel("approvals");
		},
	},
	{
		name: "sessions",
		description: "list recent sessions",
		run(_args, ctx) {
			ctx.showPanel("sessions");
		},
	},
	{
		name: "agent",
		description: "show or set active agent",
		run(args, ctx) {
			const name = args.trim();
			if (!name) {
				for (const agent of ctx.listAgents()) {
					ctx.print(`  ${agent.name.padEnd(10)} ${agent.description}`);
				}
				return;
			}
			if (!ctx.setAgent(name)) {
				ctx.print(`unknown agent: ${name}`);
				return;
			}
			ctx.print(`agent: ${name}`);
		},
	},
	{
		name: "model",
		description: "show/set model, or cycle provider with no args",
		run(args, ctx) {
			const model = args.trim();
			if (model) {
				ctx.setModel(model);
				ctx.print(`model: ${model}`);
				return;
			}
			const next = ctx.cycleProvider();
			ctx.setModel(`${next}/`);
			ctx.print(`provider: ${next}`);
		},
	},
];

export function findCommand(name: string): SlashCommand | undefined {
	return SLASH_COMMANDS.find((c) => c.name === name);
}
