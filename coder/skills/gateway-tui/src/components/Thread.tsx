/**
 * Renders the conversation thread: user/assistant messages, tool activity,
 * status lines, errors, and a trailing transcript for local log output
 * produced by slash commands.
 */

import { Box, Text } from "ink";

import type { ThreadEntry } from "../session.ts";

export interface ThreadProps {
	entries: ThreadEntry[];
	transcript: string[];
}

export function Thread({ entries, transcript }: ThreadProps) {
	return (
		<Box flexDirection="column" paddingX={1}>
			{entries.map((e) => (
				<EntryView key={e.id} entry={e} />
			))}
			{transcript.map((line, i) => (
				<Text key={`t-${i}`} dimColor>
					{line}
				</Text>
			))}
		</Box>
	);
}

function EntryView({ entry }: { entry: ThreadEntry }) {
	switch (entry.kind) {
		case "user":
			return (
				<Box flexDirection="row">
					<Text color="cyan" bold>
						you{" "}
					</Text>
					<Text>{entry.text}</Text>
				</Box>
			);
		case "assistant":
			return (
				<Box flexDirection="row">
					<Text color="green" bold>
						ai{"  "}
					</Text>
					<Text>
						{entry.text}
						{entry.streaming ? <Text dimColor>▍</Text> : null}
					</Text>
				</Box>
			);
		case "tool_use": {
			const color =
				entry.status === "error" ? "red" : entry.status === "done" ? "gray" : "yellow";
			return (
				<Box flexDirection="row">
					<Text color={color}>↳ {entry.name} </Text>
					<Text dimColor>{summarizeInput(entry.input)}</Text>
				</Box>
			);
		}
		case "tool_result": {
			const text = entry.output.length > 400 ? `${entry.output.slice(0, 400)}…` : entry.output;
			return (
				<Box flexDirection="row">
					<Text color={entry.isError ? "red" : "gray"}>· </Text>
					<Text dimColor>{text}</Text>
				</Box>
			);
		}
		case "status":
			return <Text dimColor>— {entry.text}</Text>;
		case "error":
			return <Text color="red">! {entry.text}</Text>;
	}
}

function summarizeInput(input: unknown): string {
	if (input == null) return "";
	if (typeof input === "string") return input.length > 120 ? `${input.slice(0, 120)}…` : input;
	try {
		const s = JSON.stringify(input);
		return s.length > 120 ? `${s.slice(0, 120)}…` : s;
	} catch {
		return "";
	}
}
