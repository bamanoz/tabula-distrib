/**
 * /diff panel — calls `git_diff` (unstaged) and `git_staged_diff` (staged)
 * tools from the coder-git skill and renders them side by side (vertically).
 */

import { Box, Text, useInput } from "ink";
import { useEffect, useState } from "react";

import { callSkillTool } from "../../tools.ts";

interface DiffState {
	unstaged: string;
	staged: string;
}

export function Diff({ onClose }: { onClose: () => void }) {
	const [state, setState] = useState<DiffState | null>(null);
	const [error, setError] = useState<string | null>(null);

	useInput((_, key) => {
		if (key.escape) onClose();
	});

	useEffect(() => {
		let cancelled = false;
		(async () => {
			const [unstaged, staged] = await Promise.all([
				callSkillTool({ skillDir: "git", tool: "git_diff" }),
				callSkillTool({ skillDir: "git", tool: "git_staged_diff" }),
			]);
			if (cancelled) return;
			if (!unstaged.ok || !staged.ok) {
				setError(unstaged.error ?? staged.error ?? "tool failed");
				return;
			}
			setState({
				unstaged: extractText(unstaged.json) ?? unstaged.stdout,
				staged: extractText(staged.json) ?? staged.stdout,
			});
		})();
		return () => {
			cancelled = true;
		};
	}, []);

	return (
		<Box flexDirection="column" borderStyle="round" paddingX={1}>
			<Text bold>diff</Text>
			{error ? <Text color="red">error: {error}</Text> : null}
			{!error && state === null ? <Text dimColor>loading…</Text> : null}
			{state ? (
				<Box flexDirection="column">
					<Text color="cyan" bold>
						— staged —
					</Text>
					<Text>{clamp(state.staged, 1200) || "(empty)"}</Text>
					<Text color="cyan" bold>
						— unstaged —
					</Text>
					<Text>{clamp(state.unstaged, 1200) || "(empty)"}</Text>
				</Box>
			) : null}
			<Text dimColor>press Esc to close</Text>
		</Box>
	);
}

function extractText(json: unknown): string | null {
	if (json && typeof json === "object" && "text" in (json as Record<string, unknown>)) {
		const v = (json as Record<string, unknown>).text;
		if (typeof v === "string") return v;
	}
	return null;
}

function clamp(s: string, n: number): string {
	return s.length > n ? `${s.slice(0, n)}…\n[truncated]` : s;
}
