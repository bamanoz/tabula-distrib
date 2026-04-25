/**
 * Thin wrapper around ink-spinner so the import style stays consistent with
 * the rest of the TUI components.
 */

import InkSpinner from "ink-spinner";
import { createElement } from "react";

export function Spinner() {
	return createElement(InkSpinner as unknown as (props: { type: string }) => JSX.Element, {
		type: "dots",
	});
}
