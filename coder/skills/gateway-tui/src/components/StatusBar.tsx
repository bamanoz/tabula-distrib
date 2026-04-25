/**
 * Bottom status bar: session id, turn state (with spinner when busy),
 * provider, and active panel if any.
 */

import { Box, Text } from "ink";

import type { PanelId } from "../slash.ts";
import type { TurnState } from "../session.ts";
import { Spinner } from "./Spinner.tsx";

export interface StatusBarProps {
	session: string;
	turn: TurnState;
	provider: string;
	agent: string;
	model?: string;
	panel: PanelId;
}

export function StatusBar({ session, turn, provider, agent, model, panel }: StatusBarProps) {
	return (
		<Box flexDirection="row" paddingX={1} justifyContent="space-between">
			<Box>
				<Text dimColor>session </Text>
				<Text>{session}</Text>
				<Text dimColor>  provider </Text>
				<Text>{provider}</Text>
				<Text dimColor>  agent </Text>
				<Text>{agent}</Text>
				{model ? (
					<>
						<Text dimColor>  model </Text>
						<Text>{model}</Text>
					</>
				) : null}
				{panel ? (
					<>
						<Text dimColor>  panel </Text>
						<Text>{panel}</Text>
					</>
				) : null}
			</Box>
			<Box>
				<TurnIndicator turn={turn} />
			</Box>
		</Box>
	);
}

function TurnIndicator({ turn }: { turn: TurnState }) {
	if (turn === "idle") return <Text dimColor>idle</Text>;
	return (
		<Box>
			<Spinner />
			<Text> {turn}</Text>
		</Box>
	);
}
