/**
 * /agents panel — calls subagent_list and renders the registry entries.
 * Expects the tool to return { agents: Array<{id, type, status, ...}> }.
 */

import { Box, Text, useInput } from "ink";
import { useEffect, useState } from "react";

import { callSkillTool } from "../../tools.ts";

interface AgentRow {
	id: string;
	type?: string;
	status?: string;
	prompt?: string;
}

export function Agents({ onClose }: { onClose: () => void }) {
	const [rows, setRows] = useState<AgentRow[] | null>(null);
	const [error, setError] = useState<string | null>(null);

	useInput((_, key) => {
		if (key.escape) onClose();
	});

	useEffect(() => {
		let cancelled = false;
		(async () => {
			const res = await callSkillTool({ skillDir: "subagents", tool: "subagent_list" });
			if (cancelled) return;
			if (!res.ok) {
				setError(res.error ?? res.stderr ?? "tool failed");
				return;
			}
			const j = res.json as { agents?: AgentRow[] } | undefined;
			setRows(j?.agents ?? []);
		})();
		return () => {
			cancelled = true;
		};
	}, []);

	return (
		<Box flexDirection="column" borderStyle="round" paddingX={1}>
			<Text bold>subagents</Text>
			{error ? <Text color="red">error: {error}</Text> : null}
			{!error && rows === null ? <Text dimColor>loading…</Text> : null}
			{!error && rows?.length === 0 ? <Text dimColor>(none)</Text> : null}
			{rows?.map((r) => (
				<Box key={r.id} flexDirection="row">
					<Text color={statusColor(r.status)}>{r.status ?? "?"}  </Text>
					<Text>{r.id}</Text>
					<Text dimColor>  [{r.type ?? "?"}]</Text>
					{r.prompt ? (
						<Text dimColor>  {truncate(r.prompt, 60)}</Text>
					) : null}
				</Box>
			))}
			<Text dimColor>press Esc to close</Text>
		</Box>
	);
}

function statusColor(status?: string): string {
	switch (status) {
		case "running":
			return "yellow";
		case "completed":
			return "green";
		case "failed":
			return "red";
		case "killed":
			return "red";
		default:
			return "gray";
	}
}

function truncate(s: string, n: number): string {
	return s.length > n ? `${s.slice(0, n)}…` : s;
}
