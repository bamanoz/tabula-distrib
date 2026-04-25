/**
 * Approval modal. Shown when a tool_use arrives with meta.requires_approval.
 * Keys: a = allow-once, A = allow-always, d = deny-once, D = deny-always,
 * x = abort.
 *
 * Kernel "ask" primitive is not yet wired; the choice is recorded to the
 * transcript and the modal closes. Allow/deny-always would append to
 * config/skills/hook-approvals/rules.json when the primitive lands.
 */

import { Box, Text, useInput } from "ink";

import type { ApprovalRequest } from "../session.ts";

export type ApprovalChoice =
	| "allow-once"
	| "allow-always"
	| "deny-once"
	| "deny-always"
	| "abort";

export interface ApprovalProps {
	request: ApprovalRequest;
	onChoice: (choice: ApprovalChoice) => void;
}

export function Approval({ request, onChoice }: ApprovalProps) {
	useInput((input) => {
		switch (input) {
			case "a":
				onChoice("allow-once");
				break;
			case "A":
				onChoice("allow-always");
				break;
			case "d":
				onChoice("deny-once");
				break;
			case "D":
				onChoice("deny-always");
				break;
			case "x":
			case "\u001b": // Esc
				onChoice("abort");
				break;
			default:
				break;
		}
	});

	const inputSummary = (() => {
		try {
			const s = JSON.stringify(request.input);
			return s.length > 200 ? `${s.slice(0, 200)}…` : s;
		} catch {
			return "";
		}
	})();

	return (
		<Box flexDirection="column" borderStyle="round" borderColor="yellow" paddingX={1}>
			<Text color="yellow" bold>
				approval required: {request.toolName}
			</Text>
			<Text>{request.prompt}</Text>
			{inputSummary ? <Text dimColor>input: {inputSummary}</Text> : null}
			<Box marginTop={1}>
				<Text>
					<Text color="green">a</Text>llow-once  <Text color="green">A</Text>llow-always  {" "}
					<Text color="red">d</Text>eny-once  <Text color="red">D</Text>eny-always  {" "}
					<Text color="red">x</Text>/Esc abort
				</Text>
			</Box>
		</Box>
	);
}
