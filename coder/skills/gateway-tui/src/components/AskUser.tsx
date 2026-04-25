/**
 * AskUser modal. Shown when a skill (e.g. ask-user tool) sends an
 * ask_request via MSG_STATUS. Lists 2-5 options; user selects with
 * 1-9 number keys, or Esc to cancel (sends index=-1).
 */

import { Box, Text, useInput } from "ink";

import type { AskUserRequest } from "../session.ts";

export interface AskUserProps {
	request: AskUserRequest;
	onChoice: (choice: string, index: number) => void;
}

export function AskUser({ request, onChoice }: AskUserProps) {
	useInput((input) => {
		if (input === "\u001b") {
			onChoice("", -1);
			return;
		}
		const n = Number.parseInt(input, 10);
		if (!Number.isNaN(n) && n >= 1 && n <= request.options.length) {
			const idx = n - 1;
			onChoice(request.options[idx] ?? "", idx);
		}
	});

	return (
		<Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1}>
			<Text color="cyan" bold>
				skill is asking:
			</Text>
			<Text>{request.question}</Text>
			<Box flexDirection="column" marginTop={1}>
				{request.options.map((opt, i) => (
					<Text key={i}>
						<Text color="green">{i + 1}</Text> {opt}
					</Text>
				))}
			</Box>
			<Box marginTop={1}>
				<Text dimColor>press 1-{request.options.length} to choose, Esc to cancel</Text>
			</Box>
		</Box>
	);
}
