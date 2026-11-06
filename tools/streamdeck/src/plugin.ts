import streamDeck from "@elgato/streamdeck";

import {
	BoxAction,
	ClearAction,
	EngineAction,
	FxAction,
	PresetAction,
	RandomAction,
	SceneAction,
	VrchatAction,
	VrchatSourceAction,
	WordFxAction,
} from "./actions";
import { loadGlobalSettings } from "./mojicast";

streamDeck.logger.setLevel("info");

for (const a of [
	new EngineAction(),
	new ClearAction(),
	new SceneAction(),
	new PresetAction(),
	new BoxAction(),
	new RandomAction(),
	new FxAction(),
	new WordFxAction(),
	new VrchatAction(),
	new VrchatSourceAction(),
]) {
	streamDeck.actions.registerAction(a);
}

await streamDeck.connect();
await loadGlobalSettings();
