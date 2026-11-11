/**
 * Mojicast のボタン一式。どれも「押したら /api/deck/action に1回送る」だけで、
 * 違いは送る中身・点灯条件・ボタンに出す名前・設定画面の選択肢の4点。
 */
import streamDeck, {
	action,
	type KeyAction,
	type KeyDownEvent,
	type SendToPluginEvent,
	SingletonAction,
	type WillAppearEvent,
	type WillDisappearEvent,
	type DidReceiveSettingsEvent,
} from "@elgato/streamdeck";
import type { JsonValue } from "@elgato/utils";

import * as mojicast from "./mojicast";
import type { DeckState, Item } from "./mojicast";
import { counterImage } from "./counter-image";

type Settings = {
	id?: string;
	target?: string;
	group?: string;
	op?: string;
	hideName?: boolean;
};

/** 見えているボタン（全アクション合計）。0 になったら状態の読み取りを止める */
const visible = new Set<string>();

abstract class MojicastAction extends SingletonAction<Settings> {
	/** ボタンごとの設定（willAppear と設定変更で更新） */
	private readonly settings = new Map<string, Settings>();
	/** 前回塗った内容。同じなら送らない（キー更新は毎秒10回までの決まりがある） */
	private readonly painted = new Map<string, string>();

	constructor() {
		super();
		mojicast.onChange(() => this.paintAll());
	}

	/** 送る中身。設定が足りない（選択肢が未選択など）なら null */
	protected abstract body(s: Settings): object | null;

	/** 点灯させるか。点灯の概念が無いボタンは null */
	protected isOn(_st: DeckState, _s: Settings): boolean | null {
		return null;
	}

	/** ボタンに出す名前（シーン名など）。出さないボタンは undefined */
	protected label(_st: DeckState, _s: Settings): string | undefined {
		return undefined;
	}

	/** キー画像を状態に合わせて描き直すボタン（カウンターの数など）。描かないボタンは undefined */
	protected image(_st: DeckState, _s: Settings): string | undefined {
		return undefined;
	}

	/** 設定画面のプルダウンの選択肢 */
	protected choices(_st: DeckState): { value: string; label: string; disabled?: boolean }[] {
		return [];
	}

	override onWillAppear(ev: WillAppearEvent<Settings>): void {
		visible.add(ev.action.id);
		this.settings.set(ev.action.id, ev.payload.settings);
		this.painted.delete(ev.action.id);
		mojicast.setPolling(true);
		this.paintAll();
	}

	override onWillDisappear(ev: WillDisappearEvent<Settings>): void {
		visible.delete(ev.action.id);
		this.settings.delete(ev.action.id);
		this.painted.delete(ev.action.id);
		mojicast.setPolling(visible.size > 0);
	}

	override onDidReceiveSettings(ev: DidReceiveSettingsEvent<Settings>): void {
		this.settings.set(ev.action.id, ev.payload.settings);
		this.painted.delete(ev.action.id);
		this.paintAll();
	}

	override async onKeyDown(ev: KeyDownEvent<Settings>): Promise<void> {
		const body = this.body(ev.payload.settings);
		if (!body) {
			await ev.action.showAlert();
			return;
		}
		try {
			await mojicast.run(body);
			// 点灯で結果が見えるボタンは ✓ を出さない（点灯の切替が見えなくなるため）
			if (mojicast.state() && this.isOn(mojicast.state()!, ev.payload.settings) === null) {
				await ev.action.showOk();
			}
		} catch (e) {
			streamDeck.logger.warn(`${this.manifestId}: ${e instanceof Error ? e.message : e}`);
			await ev.action.showAlert();
		}
	}

	override async onSendToPlugin(ev: SendToPluginEvent<JsonValue, Settings>): Promise<void> {
		const payload = ev.payload as { event?: string };
		if (payload?.event !== "getChoices") return;
		await mojicast.refresh();
		const st = mojicast.state();
		const items = st
			? this.choices(st)
			: [{ value: "", label: `（${mojicast.error() ?? "Mojicastに接続できません"}）`, disabled: true }];
		await streamDeck.ui.sendToPropertyInspector({ event: "getChoices", items });
	}

	private paintAll(): void {
		for (const a of this.actions) {
			if (a.isKey()) void this.paint(a);
		}
	}

	private async paint(a: KeyAction<Settings>): Promise<void> {
		const s = this.settings.get(a.id) ?? {};
		const st = mojicast.state();
		const on = st ? this.isOn(st, s) : false;
		const name = st && !s.hideName ? this.label(st, s) : undefined;
		const img = st ? this.image(st, s) : undefined;
		const key = `${on}|${name ?? ""}|${img ?? ""}`;
		if (this.painted.get(a.id) === key) return;
		this.painted.set(a.id, key);
		if (on !== null) await a.setState(on ? 1 : 0);
		if (name !== undefined) await a.setTitle(name);
		if (img !== undefined) await a.setImage(img);
	}
}

const pick = (items: Item[]) => items.map((x) => ({ value: x.id, label: x.name }));
const nameOf = (items: Item[], id?: string) => items.find((x) => x.id === id)?.name ?? "（見つかりません）";

@action({ UUID: "com.ishiki-emo.mojicast.engine" })
export class EngineAction extends MojicastAction {
	protected body() {
		return { action: "engine", op: "toggle" };
	}
	protected override isOn(st: DeckState) {
		return st.engine === "running" || st.engine === "starting";
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.clear" })
export class ClearAction extends MojicastAction {
	protected body() {
		return { action: "clear" };
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.scene" })
export class SceneAction extends MojicastAction {
	protected body(s: Settings) {
		return s.id ? { action: "scene", id: s.id } : null;
	}
	protected override isOn(st: DeckState, s: Settings) {
		const sc = st.scenes.find((x) => x.id === s.id);
		return !!sc && sc.preset === st.preset && sc.box === st.box;
	}
	protected override label(st: DeckState, s: Settings) {
		return s.id ? nameOf(st.scenes, s.id) : undefined;
	}
	protected override choices(st: DeckState) {
		return pick(st.scenes);
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.preset" })
export class PresetAction extends MojicastAction {
	protected body(s: Settings) {
		return s.id ? { action: "preset", id: s.id } : null;
	}
	protected override isOn(st: DeckState, s: Settings) {
		return st.preset === s.id;
	}
	protected override label(st: DeckState, s: Settings) {
		return s.id ? nameOf(st.presets, s.id) : undefined;
	}
	protected override choices(st: DeckState) {
		return pick(st.presets);
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.box" })
export class BoxAction extends MojicastAction {
	protected body(s: Settings) {
		return s.id ? { action: "box", id: s.id } : null;
	}
	protected override isOn(st: DeckState, s: Settings) {
		return st.box === s.id;
	}
	protected override label(st: DeckState, s: Settings) {
		return s.id ? nameOf(st.boxes, s.id) : undefined;
	}
	protected override choices(st: DeckState) {
		return pick(st.boxes);
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.random" })
export class RandomAction extends MojicastAction {
	protected body(s: Settings) {
		const target = ["scene", "preset", "box"].includes(s.target ?? "") ? s.target : "scene";
		return { action: `${target}_random` };
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.fx" })
export class FxAction extends MojicastAction {
	protected body(s: Settings) {
		return s.group ? { action: "fx", group: s.group } : null;
	}
	protected override label(_st: DeckState, s: Settings) {
		return s.group;
	}
	protected override choices(st: DeckState) {
		return st.fx_groups.map((g) => ({ value: g.id, label: g.ready ? g.name : `${g.name}（演出が未設定）` }));
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.counter" })
export class CounterAction extends MojicastAction {
	protected body(s: Settings) {
		const op = ["add", "sub", "reset"].includes(s.op ?? "") ? s.op : "add";
		return s.id ? { action: "counter", id: s.id, op } : null;
	}
	protected override label(st: DeckState, s: Settings) {
		return s.id ? nameOf(st.counters ?? [], s.id) : undefined;
	}
	protected override image(st: DeckState, s: Settings) {
		if (!s.id) return undefined;
		const c = (st.counters ?? []).find((x) => x.id === s.id);
		return counterImage(c ? c.value : null, s.op ?? "add", c ? c.enabled : false);
	}
	protected override choices(st: DeckState) {
		const list = st.counters ?? [];
		if (!list.length)
			return [{ value: "", label: "（カウンターがありません。Mojicastで作ってください）", disabled: true }];
		return list.map((c) => ({ value: c.id, label: c.enabled ? c.name : `${c.name}（OFF）` }));
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.word-fx" })
export class WordFxAction extends MojicastAction {
	protected body() {
		return { action: "word_fx", op: "toggle" };
	}
	protected override isOn(st: DeckState) {
		return st.word_fx;
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.vrchat" })
export class VrchatAction extends MojicastAction {
	protected body() {
		return { action: "vrchat", op: "toggle" };
	}
	protected override isOn(st: DeckState) {
		return st.vrchat;
	}
}

@action({ UUID: "com.ishiki-emo.mojicast.vrchat-source" })
export class VrchatSourceAction extends MojicastAction {
	protected body() {
		return { action: "vrchat_source", value: "toggle" };
	}
	/** 点灯＝訳文を送る */
	protected override isOn(st: DeckState) {
		return st.vrchat_source === "tr";
	}
}
