/**
 * /todo panel — invokes the `todoread` tool directly (no LLM round-trip).
 * Expects the tool to return a JSON object with an `items` array of
 * `{ text, state, id? }`.
 */

import { Box, Text, useInput } from "ink";
import { useEffect, useState } from "react";

import { callSkillTool } from "../../tools.ts";

interface TodoItem {
	id?: string;
	text: string;
	state?: string;
}

export function Todo({ onClose }: { onClose: () => void }) {
	const [items, setItems] = useState<TodoItem[] | null>(null);
	const [error, setError] = useState<string | null>(null);

	useInput((_, key) => {
		if (key.escape) onClose();
	});

	useEffect(() => {
		let cancelled = false;
		(async () => {
			const res = await callSkillTool({ skillDir: "todo", tool: "todoread" });
			if (cancelled) return;
			if (!res.ok) {
				setError(res.error ?? res.stderr ?? "tool failed");
				return;
			}
			const j = res.json as { items?: TodoItem[] } | undefined;
			setItems(j?.items ?? []);
		})();
		return () => {
			cancelled = true;
		};
	}, []);

	return (
		<Box flexDirection="column" borderStyle="round" paddingX={1}>
			<Text bold>todo</Text>
			{error ? <Text color="red">error: {error}</Text> : null}
			{!error && items === null ? <Text dimColor>loading…</Text> : null}
			{!error && items?.length === 0 ? <Text dimColor>(empty)</Text> : null}
			{items?.map((it, i) => (
				<Text key={it.id ?? i}>
					<Text color={stateColor(it.state)}>{stateGlyph(it.state)} </Text>
					{it.text}
				</Text>
			))}
			<Text dimColor>press Esc to close</Text>
		</Box>
	);
}

function stateGlyph(state?: string): string {
	switch (state) {
		case "completed":
		case "done":
			return "✓";
		case "in_progress":
			return "▸";
		case "cancelled":
		case "deleted":
			return "✗";
		default:
			return "·";
	}
}

function stateColor(state?: string): string {
	switch (state) {
		case "completed":
		case "done":
			return "green";
		case "in_progress":
			return "yellow";
		case "cancelled":
		case "deleted":
			return "red";
		default:
			return "gray";
	}
}
