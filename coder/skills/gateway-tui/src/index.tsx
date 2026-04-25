/**
 * Entry point — parses argv, resolves driver command, constructs the Gateway,
 * then renders <App/>.
 *
 * Usage:
 *   bun run src/index.tsx [--session <id>] [--provider openai|anthropic]
 *                         [--url ws://host:port/ws]
 */

import { randomBytes } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { render } from "ink";
import { createElement } from "react";

import { App } from "./App.tsx";
import { Gateway } from "./session.ts";

interface CliArgs {
	session: string;
	provider: string;
	agent: string;
	model?: string;
	url: string;
	driver?: string;
	help: boolean;
}

export interface AgentInfo {
	name: string;
	description: string;
	provider?: string;
	model?: string;
}

const PROVIDERS = ["openai", "anthropic"];

function tabulaHome(): string {
	return process.env.TABULA_HOME ?? path.join(process.env.HOME ?? "", ".tabula");
}

function configuredProvider(): string | undefined {
	const configPath = path.join(tabulaHome(), "config", "global.toml");
	if (!existsSync(configPath)) return undefined;
	const text = readFileSync(configPath, "utf8");
	const match = text.match(/^\s*provider\s*=\s*["']([^"']+)["']/m);
	return match?.[1];
}

function defaultProvider(): string {
	const configured = process.env.TABULA_PROVIDER;
	if (configured && PROVIDERS.includes(configured)) return configured;
	const fromConfig = configuredProvider();
	if (fromConfig && PROVIDERS.includes(fromConfig)) return fromConfig;
	return "anthropic";
}

function parseArgs(argv: string[]): CliArgs {
	let session = `sess-${randomBytes(4).toString("hex")}`;
	let provider = defaultProvider();
	let agent = "build";
	let model: string | undefined;
	let url = process.env.TABULA_URL ?? "ws://localhost:8089/ws";
	let driver: string | undefined;
	let help = false;
	for (let i = 2; i < argv.length; i++) {
		const a = argv[i];
		const v = argv[i + 1];
		if (a === "-h" || a === "--help") {
			help = true;
		} else if (a === "--session" && v) {
			session = v;
			i++;
		} else if (a === "--provider" && v) {
			provider = v;
			i++;
		} else if (a === "--agent" && v) {
			agent = v;
			i++;
		} else if (a === "--model" && v) {
			model = v;
			i++;
		} else if (a === "--url" && v) {
			url = v;
			i++;
		} else if (a === "--driver" && v) {
			driver = v;
			i++;
		}
	}
	return { session, provider, agent, model, url, driver, help };
}

function printHelp(): void {
	process.stdout.write(`Usage: tabula-coder [options]

Options:
  --session <id>              Resume or join a specific session
  --provider <name>           anthropic or openai (default: TABULA_PROVIDER or anthropic)
  --agent <name>              Agent name (default: build)
  --model <provider/model>    Per-turn model override
  --url <ws-url>              Kernel WebSocket URL (default: TABULA_URL or ws://localhost:8089/ws)
  --driver <command>          Driver command override
  -h, --help                  Show this help
`);
}

function loadAgents(): AgentInfo[] {
	const home = tabulaHome();
	const dirs = [path.join(home, "distrib", "active", "current", "agents"), path.join(home, "agents")];
	const agents = new Map<string, AgentInfo>();
	for (const dir of dirs) {
		if (!existsSync(dir)) continue;
		for (const file of new Bun.Glob("*.md").scanSync({ cwd: dir })) {
			const fallbackName = file.replace(/\.md$/, "");
			const text = readFileSync(path.join(dir, file), "utf8");
			const frontEnd = text.indexOf("\n---", 3);
			const front = text.startsWith("---") && frontEnd !== -1 ? text.slice(3, frontEnd).trim() : "";
			const get = (key: string) => front.match(new RegExp(`^\\s*${key}\\s*:\\s*["']?([^"'\\n]+)["']?`, "m"))?.[1]?.trim();
			const name = get("name") ?? fallbackName;
			agents.set(name, {
				name,
				description: get("description") ?? "",
				provider: get("provider"),
				model: get("model"),
			});
		}
	}
	if (!agents.has("build")) agents.set("build", { name: "build", description: "Default coding agent" });
	return Array.from(agents.values()).sort((a, b) => a.name.localeCompare(b.name));
}

function driverCommandFor(provider: string): string {
	const home = tabulaHome();
	const python = process.platform === "win32"
		? path.join(home, ".venv", "Scripts", "python.exe")
		: path.join(home, ".venv", "bin", "python3");
	const driverScript = path.join(home, "skills", "driver", "run.py");
	switch (provider) {
		case "openai":
		case "anthropic":
			return `${JSON.stringify(python)} ${JSON.stringify(driverScript)} --provider ${provider}`;
		default:
			throw new Error(`unknown provider: ${provider}`);
	}
}

async function main(): Promise<void> {
	const args = parseArgs(process.argv);
	if (args.help) {
		printHelp();
		return;
	}
	const driverCommand = args.driver ?? driverCommandFor(args.provider);
	const agents = loadAgents();

	const gateway = new Gateway({
		url: args.url,
		sessionId: args.session,
		driverCommand,
		agent: args.agent,
		model: args.model,
	});

	try {
		await gateway.start();
	} catch (err) {
		process.stderr.write(`gateway start failed: ${(err as Error).message}\n`);
		process.exit(1);
	}

	const { waitUntilExit } = render(
		createElement(App, {
			gateway,
			initialProvider: args.provider,
			initialAgent: args.agent,
			initialModel: args.model,
			agents,
			providers: PROVIDERS,
		}),
	);

	const cleanup = async () => {
		try {
			await gateway.stop();
		} catch {
			// ignore
		}
	};

	process.on("SIGINT", () => {
		void cleanup().finally(() => process.exit(130));
	});
	process.on("SIGTERM", () => {
		void cleanup().finally(() => process.exit(143));
	});

	await waitUntilExit();
	await cleanup();
}

void main();
