/**
 * Prompt input with slash-command autocomplete hints. Uses ink-text-input for
 * editing; shows matching slash commands underneath when the buffer starts
 * with "/".
 */

import { Box, Text } from "ink";
import TextInput from "ink-text-input";
import { useState } from "react";

export interface InputProps {
	onSubmit: (value: string) => void | Promise<void>;
	slashHints: string[];
	disabled?: boolean;
}

export function Input({ onSubmit, slashHints, disabled }: InputProps) {
	const [value, setValue] = useState("");

	const submit = (v: string) => {
		setValue("");
		void onSubmit(v);
	};

	const hints =
		value.startsWith("/")
			? slashHints
				.filter((n) => n.startsWith(value.slice(1)))
				.slice(0, 6)
			: [];

	return (
		<Box flexDirection="column">
			<Box>
				<Text color={disabled ? "gray" : "cyan"} bold>
					{disabled ? "…" : ">"}{" "}
				</Text>
				<TextInput value={value} onChange={setValue} onSubmit={submit} />
			</Box>
			{hints.length > 0 ? (
				<Box paddingLeft={2}>
					<Text dimColor>{hints.map((h) => `/${h}`).join("  ")}</Text>
				</Box>
			) : null}
		</Box>
	);
}
