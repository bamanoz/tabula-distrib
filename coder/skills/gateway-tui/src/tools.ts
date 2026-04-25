/**
 * Invoke a locally-installed skill tool (subprocess) directly, bypassing the
 * driver. Used by panels that need to read state (todos, subagents, diffs,
 * approval rules) without asking the LLM to call a tool.
 *
 * This only works because skill tool execs are `<python> skills/<skill>/run.py
 * tool <name>` and we can run the same command here.
 */

import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

export interface ToolInvocation {
	skillDir: string; // e.g. "todo" or "subagents" — relative to $TABULA_HOME/skills
	tool: string;
	params?: Record<string, unknown>;
}

const VENV_PYTHON = (() => {
	const home = process.env.TABULA_HOME ?? path.join(process.env.HOME ?? "", ".tabula");
	if (process.platform === "win32") {
		return path.join(home, ".venv", "Scripts", "python.exe");
	}
	return path.join(home, ".venv", "bin", "python3");
})();

const TABULA_HOME = process.env.TABULA_HOME ?? path.join(process.env.HOME ?? "", ".tabula");

export interface ToolResult {
	ok: boolean;
	stdout: string;
	stderr: string;
	json?: unknown;
	error?: string;
}

export async function callSkillTool(inv: ToolInvocation): Promise<ToolResult> {
	const runPy = path.join(TABULA_HOME, "skills", inv.skillDir, "run.py");
	return new Promise((resolve) => {
		const child = spawn(VENV_PYTHON, [runPy, "tool", inv.tool], {
			env: { ...process.env, TABULA_SESSION: process.env.TABULA_SESSION ?? "" },
			stdio: ["pipe", "pipe", "pipe"],
		});
		let stdout = "";
		let stderr = "";
		child.stdout.on("data", (b) => (stdout += b.toString("utf8")));
		child.stderr.on("data", (b) => (stderr += b.toString("utf8")));
		child.on("error", (err) => {
			resolve({ ok: false, stdout, stderr, error: err.message });
		});
		child.on("close", (code) => {
			let json: unknown;
			try {
				json = JSON.parse(stdout);
			} catch {
				json = undefined;
			}
			resolve({
				ok: code === 0,
				stdout,
				stderr,
				json,
				error: code === 0 ? undefined : `exit ${code}`,
			});
		});
		child.stdin.write(JSON.stringify(inv.params ?? {}));
		child.stdin.end();
	});
}

export { VENV_PYTHON, TABULA_HOME };
// Silence unused warning if this import is kept in tsconfig but not used elsewhere.
void fileURLToPath;
