/**
 * カウンターのキー画像（144x144 の SVG）。今の数を大きく、押したときの動き（＋1・−1・0に戻す）を
 * 右上に小さく描く。配色は make_assets.py のキー画像と同じ（紺の地・シアンの数字）。
 * 名前は Stream Deck のタイトル欄（下寄せ）に出すので、絵の下 1/3 は空けておく。
 */
const NAVY = "#03102d";
const CYAN = "#00e5ff";
const DIM = "#9aa7bb";
const FONT = "'Segoe UI','Helvetica Neue',Arial,sans-serif";

/** value=null はカウンターが見つからない（削除された） */
export function counterImage(value: number | null, op: string, enabled: boolean): string {
	const text = value === null ? "?" : String(value);
	// 桁数が増えたら字を小さくして枠に収める（最大 99999）
	const size = text.length <= 2 ? 64 : text.length === 3 ? 54 : text.length === 4 ? 42 : 34;
	const color = value !== null && enabled ? CYAN : DIM;
	const badge =
		op === "reset"
			? `<path d="M118 22a10 10 0 1 1-9-4" fill="none" stroke="${DIM}" stroke-width="4" stroke-linecap="round"/>` +
				`<path d="M104 12l6 6-7 4" fill="none" stroke="${DIM}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>`
			: `<text x="124" y="30" text-anchor="end" font-family="${FONT}" font-size="20" font-weight="700" fill="${DIM}">${
					op === "sub" ? "-1" : "+1"
				}</text>`;
	const svg =
		`<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" viewBox="0 0 144 144">` +
		`<rect width="144" height="144" fill="${NAVY}"/>${badge}` +
		`<text x="72" y="66" text-anchor="middle" dominant-baseline="central" font-family="${FONT}" ` +
		`font-size="${size}" font-weight="800" fill="${color}">${text}</text></svg>`;
	return `data:image/svg+xml;charset=utf8,${encodeURIComponent(svg)}`;
}
