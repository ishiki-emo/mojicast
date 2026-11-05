/**
 * 起動中の Mojicast（http://127.0.0.1:<port>）と話す部分。
 *
 * 使うのは外部操作キー専用の2つの窓口だけ:
 *   GET  /api/deck/state   … ボタンの点灯・選択肢の一覧
 *   POST /api/deck/action  … 1回の操作
 * 字幕の本文は受け取らない（SSE は使わず、ボタンが見えている間だけ数秒おきに状態を読む）。
 */
import streamDeck from "@elgato/streamdeck";

export type Item = { id: string; name: string; preset?: string; box?: string; ready?: boolean };

export type DeckState = {
	engine: string;
	preset: string;
	box: string;
	translate: boolean;
	translate_lang: string;
	word_fx: boolean;
	vrchat: boolean;
	vrchat_source: string;
	vrchat_supported: boolean;
	scenes: Item[];
	presets: Item[];
	boxes: Item[];
	langs: Item[];
	fx_groups: Item[];
};

export type GlobalSettings = { port?: string; key?: string };

const DEFAULT_PORT = 8765;
const POLL_MS = 2000;

let global: GlobalSettings = {};
let current: DeckState | null = null;
let lastError: string | null = null;
let timer: NodeJS.Timeout | undefined;
const listeners = new Set<() => void>();

export class MojicastError extends Error {}

function baseUrl(): string {
	const port = Number(global.port);
	return `http://127.0.0.1:${Number.isInteger(port) && port >= 1024 && port <= 65535 ? port : DEFAULT_PORT}`;
}

async function call<T>(method: "GET" | "POST", path: string, body?: object, timeoutMs = 3000): Promise<T> {
	const key = (global.key ?? "").trim();
	if (!key) throw new MojicastError("接続キーが未設定です");
	let res: Response;
	try {
		res = await fetch(baseUrl() + path, {
			method,
			headers: { Authorization: `Bearer ${key}`, ...(body ? { "Content-Type": "application/json" } : {}) },
			body: body ? JSON.stringify(body) : undefined,
			signal: AbortSignal.timeout(timeoutMs),
		});
	} catch {
		throw new MojicastError("Mojicastに接続できません（起動しているか・ポート番号を確認）");
	}
	const data = (await res.json().catch(() => ({}))) as { message?: string; error?: string };
	if (res.status === 401) throw new MojicastError("接続キーが違います（Mojicastの設定で再発行したキーを貼り直してください）");
	if (!res.ok) throw new MojicastError(data.message || data.error || `HTTP ${res.status}`);
	return data as T;
}

export function state(): DeckState | null {
	return current;
}

export function error(): string | null {
	return lastError;
}

export function onChange(listener: () => void): void {
	listeners.add(listener);
}

function notify(): void {
	for (const listener of listeners) listener();
}

export async function refresh(): Promise<void> {
	try {
		current = await call<DeckState>("GET", "/api/deck/state");
		lastError = null;
	} catch (e) {
		current = null;
		lastError = e instanceof Error ? e.message : String(e);
	}
	notify();
}

/** 1回の操作を送る。成功すれば返ってきた最新の状態でボタンを塗り直す */
export async function run(body: object): Promise<string> {
	// 認識の停止は終わるまで待つので長めに待つ
	const data = await call<{ message: string; state: DeckState }>("POST", "/api/deck/action", body, 20000);
	current = data.state;
	lastError = null;
	notify();
	return data.message;
}

/** ボタンが1つ以上見えている間だけ状態を読みに行く */
export function setPolling(active: boolean): void {
	if (active && !timer) {
		void refresh();
		timer = setInterval(() => void refresh(), POLL_MS);
	} else if (!active && timer) {
		clearInterval(timer);
		timer = undefined;
	}
}

export async function loadGlobalSettings(): Promise<void> {
	global = await streamDeck.settings.getGlobalSettings<GlobalSettings>();
	streamDeck.settings.onDidReceiveGlobalSettings<GlobalSettings>((ev) => {
		global = ev.settings;
		void refresh();
	});
	if (timer) void refresh(); // 設定を読む前に出たボタンを塗り直す
}
