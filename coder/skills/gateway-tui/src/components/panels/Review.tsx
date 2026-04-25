/**
 * /review panel — invokes `review_plan` and `diff_preview` from the
 * coder-review skill and renders a "ready to commit?" checklist plus a
 * per-file diff summary suitable for an at-a-glance pre-apply preview.
 */

import { Box, Text, useInput } from "ink";
import { useEffect, useState } from "react";

import { callSkillTool } from "../../tools.ts";

interface ReviewPlan {
	scope: string;
	files: { path: string; status: string; added: number; removed: number; bucket: string }[];
	untracked: string[];
	checklist: { type: string; message: string; path?: string; marker?: string; paths?: string[] }[];
	suggested_commit: string;
}

interface DiffSummary {
	summary: {
		files: number;
		added: number;
		removed: number;
		by_file: { path: string; added: number; removed: number; status: string }[];
	};
}

interface State {
	plan?: ReviewPlan;
	preview?: DiffSummary;
	error?: string;
}

export function Review({ onClose }: { onClose: () => void }) {
	const [state, setState] = useState<State>({});

	useInput((_, key) => {
		if (key.escape) onClose();
	});

	useEffect(() => {
		let cancelled = false;
		(async () => {
			const [planRes, previewRes] = await Promise.all([
				callSkillTool({ skillDir: "review", tool: "review_plan", params: { scope: "both" } }),
				callSkillTool({ skillDir: "review", tool: "diff_preview" }),
			]);
			if (cancelled) return;
			if (!planRes.ok || !previewRes.ok) {
				setState({ error: planRes.error ?? previewRes.error ?? "tool failed" });
				return;
			}
			setState({
				plan: planRes.json as ReviewPlan,
				preview: previewRes.json as DiffSummary,
			});
		})();
		return () => {
			cancelled = true;
		};
	}, []);

	if (state.error) {
		return (
			<Box flexDirection="column" borderStyle="round" paddingX={1}>
				<Text bold>review</Text>
				<Text color="red">error: {state.error}</Text>
				<Text dimColor>press Esc to close</Text>
			</Box>
		);
	}

	if (!state.plan || !state.preview) {
		return (
			<Box flexDirection="column" borderStyle="round" paddingX={1}>
				<Text bold>review</Text>
				<Text dimColor>loading…</Text>
			</Box>
		);
	}

	const { plan, preview } = state;
	const sum = preview.summary;

	return (
		<Box flexDirection="column" borderStyle="round" paddingX={1}>
			<Text bold>review</Text>
			<Text>
				<Text color="cyan">summary: </Text>
				{sum.files} file{sum.files === 1 ? "" : "s"}, +{sum.added}/-{sum.removed}
			</Text>
			{sum.by_file.length === 0 ? (
				<Text dimColor>(no changes)</Text>
			) : (
				<Box flexDirection="column">
					{sum.by_file.slice(0, 20).map((f) => (
						<Text key={f.path}>
							<Text color={statusColor(f.status)}>{statusGlyph(f.status)} </Text>
							{f.path}{" "}
							<Text color="green">+{f.added}</Text>
							<Text color="red"> -{f.removed}</Text>
						</Text>
					))}
					{sum.by_file.length > 20 ? (
						<Text dimColor>… {sum.by_file.length - 20} more</Text>
					) : null}
				</Box>
			)}
			<Text> </Text>
			<Text color="cyan">checklist ({plan.checklist.length}):</Text>
			{plan.checklist.length === 0 ? (
				<Text dimColor>  (clean)</Text>
			) : (
				plan.checklist.slice(0, 10).map((item, i) => (
					<Text key={`${item.type}-${i}`}>
						<Text color={checkColor(item.type)}>  • </Text>
						{item.message}
					</Text>
				))
			)}
			<Text> </Text>
			<Text color="cyan">suggested commit:</Text>
			<Text>{indent(plan.suggested_commit, "  ")}</Text>
			<Text dimColor>press Esc to close</Text>
		</Box>
	);
}

function statusGlyph(s: string): string {
	switch (s) {
		case "added":
			return "+";
		case "deleted":
			return "-";
		case "renamed":
			return "→";
		case "copied":
			return "©";
		default:
			return "M";
	}
}

function statusColor(s: string): string {
	switch (s) {
		case "added":
			return "green";
		case "deleted":
			return "red";
		case "renamed":
			return "yellow";
		default:
			return "white";
	}
}

function checkColor(t: string): string {
	switch (t) {
		case "suspicious_marker":
			return "yellow";
		case "large_change":
			return "magenta";
		case "untracked":
			return "cyan";
		default:
			return "white";
	}
}

function indent(s: string, pad: string): string {
	return s
		.split("\n")
		.map((line) => pad + line)
		.join("\n");
}
