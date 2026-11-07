import { esc } from "../i18n.js";

export function colourValue(spool) {
  if (spool.colour_known === false) return "var(--fl-ink-dim)";
  return spool.colour_rgba ?? spool.colour ?? "var(--fl-ink-dim)";
}

/** The nearest preset's word for a spool's colour ("Red"), so the card can say it, not only show it. */
export function colourName(spool, t) {
  const m = /^#?([0-9a-f]{6})/i.exec(String(spool.colour_rgba ?? spool.colour ?? ""));
  if (spool.colour_known === false || !m) return t("colour.unknown");
  const rgb = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
  let best = null, bestD = Infinity;
  for (const [name, hex] of COLOUR_PRESETS) {
    const d = [1, 3, 5].reduce((a, i, k) => a + (parseInt(hex.slice(i, i + 2), 16) - rgb[k]) ** 2, 0);
    if (d < bestD) { bestD = d; best = name; }
  }
  return t("colour." + best);
}

export function colourSample(spool, t) {
  const known = spool.colour_known !== false;
  const label = known ? spool.colour_rgba ?? spool.colour : t("colour.unknown");
  return `<span class="colour-sample ${known ? "" : "colour-unknown"}" style="--filament-colour:${esc(colourValue(spool))}" role="img" aria-label="${esc(label)}" title="${esc(label)}">${known ? "" : "?"}</span>`;
}


const COLOUR_PRESETS = [
  ["black", "#000000"], ["white", "#ffffff"], ["grey", "#808080"],
  ["red", "#ff0000"], ["orange", "#ff8000"], ["yellow", "#ffff00"],
  ["green", "#008000"], ["blue", "#0000ff"], ["purple", "#800080"],
  ["pink", "#ffc0cb"], ["brown", "#804000"], ["beige", "#f5f5dc"],
];

function presetFor(value) {
  value = value.trim();
  if (!value) return "";
  if (!value.startsWith("#")) value = "#" + value;
  const opaque = value.length === 9 && value.toLowerCase().endsWith("ff") ? value.slice(0, 7).toLowerCase() : value.toLowerCase();
  return COLOUR_PRESETS.find(([, hex]) => hex === opaque)?.[1] ?? "custom";
}

export function colourEditor(value, known, t) {
  const colour = known === false ? "" : value ?? "";
  const selected = presetFor(colour);
  const rgb = /^#?[0-9a-f]{6}([0-9a-f]{2})?$/i.test(colour) ? "#" + colour.replace(/^#/, "").slice(0, 6) : "#000000";
  const options = [["", t("colour.unknown")], ...COLOUR_PRESETS.map(([name, hex]) => [hex, t("colour." + name)]), ["custom", t("colour.custom")]];
  return `<div class="colour-editor">
    <div class="colour-controls">
      <label>${t("dlg.colour")}<select data-colour-preset>${options.map(([hex, label]) => `<option value="${hex}" ${hex === selected ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label>${t("colour.picker")}<input type="color" data-colour-picker value="${esc(rgb)}"></label>
    </div>
    <label>${t("colour.code")}<input name="colour" value="${esc(colour)}" placeholder="#RRGGBB / #RRGGBBAA" pattern="#?[0-9a-fA-F]{6}([0-9a-fA-F]{2})?" spellcheck="false" autocapitalize="off"></label>
    <input type="hidden" name="colour_known" value="${colour ? "true" : "false"}">
  </div>`;
}

// Patch the shared editor before registration captures FormData; never repaint a typed code.
export function syncColourEditor(control) {
  const editor = control.closest(".colour-editor");
  if (!editor) return;
  const code = editor.querySelector("[name='colour']");
  const preset = editor.querySelector("[data-colour-preset]");
  const picker = editor.querySelector("[data-colour-picker]");
  if (control === preset) code.value = preset.value === "custom" ? code.value || "#000000" : preset.value;
  if (control === picker) {
    // Native pickers vary in alpha support; editing RGB preserves an existing alpha byte.
    const alpha = /^#?[0-9a-f]{8}$/i.test(code.value) ? code.value.slice(-2) : "";
    code.value = picker.value + alpha;
  }
  const value = code.value.trim();
  editor.querySelector("[name='colour_known']").value = value ? "true" : "false";
  if (control !== preset || preset.value !== "custom") preset.value = presetFor(value);
  if (/^#?[0-9a-f]{6}([0-9a-f]{2})?$/i.test(value)) picker.value = (value.startsWith("#") ? value : "#" + value).slice(0, 7);
}
