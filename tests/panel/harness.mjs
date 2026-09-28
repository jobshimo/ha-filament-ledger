// The panel under Node's built-in test runner — no DOM library, no dependencies (ADR-0010).
//
// The panel renders by returning HTML strings from plain methods, so a test needs no real
// DOM: only the three globals the module touches while it is being *imported* — the base
// class, the custom-element registry, and the `document` its font installer writes one
// <style> into — plus the shadow root its constructor attaches. A view method is then called
// on a real instance, starting from the constructor's own defaults, with the state the test
// adds on top.

globalThis.HTMLElement ??= class {
  attachShadow() {
    this.shadowRoot = { innerHTML: "", addEventListener() {}, querySelector: () => null };
    return this.shadowRoot;
  }
};

const registry = new Map();
globalThis.customElements ??= {
  get: (name) => registry.get(name),
  define: (name, constructor) => registry.set(name, constructor),
};

globalThis.document ??= {
  head: { appendChild() {} },
  getElementById: () => null,
  createElement: (tagName) => ({ tagName }),
};

const WWW = new URL("../../custom_components/filament_ledger/www/", import.meta.url);

const { translator } = await import(new URL("i18n.js", WWW).href);
await import(new URL("filament-ledger-panel.js", WWW).href);

export const Panel = customElements.get("filament-ledger-panel");

/** A panel instance holding `state`, speaking `language`, never connected to anything. */
export function panelWith(state = {}, language = "en") {
  const panel = new Panel();
  return Object.assign(panel, { _t: translator(language) }, state);
}

/** What a reader sees: the markup with its tags removed and its whitespace collapsed. */
export function visibleText(html) {
  return html
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** A spool exactly as `serialisers.spool_summary` sends it, with overrides. */
export function aSpool(overrides = {}) {
  return {
    id: "spool-1",
    name: "Galaxy Black",
    label: "Galaxy Black",
    vendor: "Bambu Lab",
    material: "PLA",
    material_kind: "PLA",
    colour: "#1A1A1AFF",
    foreground: "#FFFFFFFF",
    balance_g: 640,
    balance_exact_g: 640.4,
    opening_weight_g: 1000,
    core_weight_g: 250,
    percentage: 64,
    state: "ACTIVE",
    confidence: "HIGH",
    confidence_basis: null,
    needs_weighing: false,
    location: { kind: "SHELF", label: "Shelf" },
    tag_uid: null,
    tag_source: null,
    reel_uid: null,
    deleted_reason: null,
    movement_count: 3,
    last_movement_at: null,
    has_anomaly: false,
    registered_at: "2026-09-01T10:00:00+00:00",
    ...overrides,
  };
}
