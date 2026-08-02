/**
 * Filament Ledger — Home Assistant sidebar panel.
 *
 * A plain custom element. No framework, no bundler, no build step: see
 * docs/adr/0006-vanilla-panel.md. It talks to the backend only through the websocket
 * commands in infrastructure/ha/websocket_api.py, and there is no command that sets a
 * balance — changing one requires a movement, and that is the whole design.
 *
 * Styling uses Home Assistant's own CSS custom properties throughout, so light, dark and
 * custom themes work without a line of per-theme code.
 */

const CONFIDENCE = {
  HIGH: { label: "High", cls: "high" },
  MEDIUM: { label: "Medium", cls: "med" },
  LOW: { label: "Low", cls: "low" },
};

const MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU", "PC", "PA", "PVA", "SUPPORT", "OTHER"];

const TABS = [
  { id: "inventory", label: "Inventory" },
  { id: "review", label: "Review" },
  { id: "ams", label: "AMS" },
];

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

const grams = (value) => `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 })} g`;
const signed = (value) => `${value < 0 ? "−" : "+"} ${Math.abs(value).toFixed(1)}`;

function when(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  const days = Math.floor((Date.now() - then.getTime()) / 86400000);
  if (days <= 0) return `Today ${then.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  if (days === 1) return "Yesterday";
  if (days < 30) return `${days} days ago`;
  return then.toLocaleDateString();
}

class FilamentLedgerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "inventory";
    this._spools = [];
    this._stock = null;
    this._detail = null;
    this._error = null;
    this._loading = true;
    this._dialog = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this.refresh();
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `<style>${STYLES}</style><div id="root"></div>`;
    this._root = this.shadowRoot.getElementById("root");
    this._root.addEventListener("click", (event) => this._onClick(event));
    this._root.addEventListener("submit", (event) => this._onSubmit(event));
    this.render();
  }

  async call(type, payload = {}) {
    return this.hass.callWS({ type: `filament_ledger/${type}`, ...payload });
  }

  async refresh() {
    try {
      this._error = null;
      const [spools, stock] = await Promise.all([this.call("spools/list"), this.call("stock")]);
      this._spools = spools;
      this._stock = stock;
      if (this._detail) this._detail = await this.call("spools/get", { spool_id: this._detail.id });
    } catch (error) {
      this._error = error.message || String(error);
    } finally {
      this._loading = false;
      this.render();
    }
  }

  async guarded(work) {
    try {
      await work();
      this._dialog = null;
      await this.refresh();
    } catch (error) {
      this._error = error.message || String(error);
      this.render();
    }
  }

  // -- events ------------------------------------------------------------------------

  _onClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const { action, id, slot } = target.dataset;

    switch (action) {
      case "tab":
        this._tab = id;
        this._detail = null;
        this.render();
        break;
      case "open":
        this.guarded(async () => {
          this._detail = await this.call("spools/get", { spool_id: id });
        });
        break;
      case "back":
        this._detail = null;
        this.render();
        break;
      case "dismiss-error":
        this._error = null;
        this.render();
        break;
      case "dialog":
        this._dialog = { kind: id, spool: this._detail };
        this.render();
        break;
      case "close-dialog":
        this._dialog = null;
        this.render();
        break;
      case "unmount":
        this.guarded(() => this.call("spools/unmount", { spool_id: id }));
        break;
      case "mount-slot":
        this._dialog = { kind: "mount", slot: Number(slot) };
        this.render();
        break;
      default:
        break;
    }
  }

  _onSubmit(event) {
    event.preventDefault();
    const form = event.target;
    const data = Object.fromEntries(new FormData(form).entries());
    const spoolId = this._detail?.id;

    switch (form.dataset.form) {
      case "new-spool":
        this.guarded(() =>
          this.call("spools/create", {
            material: data.material,
            material_other: data.material_other || undefined,
            colour: data.colour,
            opening_weight_g: Number(data.opening_weight_g),
            core_weight_g: Number(data.core_weight_g),
            vendor: data.vendor || null,
            label: data.label || null,
          }),
        );
        break;
      case "weigh":
        this.guarded(() =>
          this.call("spools/reconcile", {
            spool_id: spoolId,
            measured_g: Number(data.measured_g),
            includes_core: data.includes_core === "on",
            note: data.note || null,
          }),
        );
        break;
      case "adjust":
        this.guarded(() =>
          this.call("spools/adjust", {
            spool_id: spoolId,
            amount_g: Number(data.amount_g),
            reason: data.reason,
          }),
        );
        break;
      case "discard":
        this.guarded(() =>
          this.call("spools/discard", {
            spool_id: spoolId,
            mode: data.mode,
            amount_g: data.mode === "partial" ? Number(data.amount_g) : undefined,
            reason: data.reason,
          }),
        );
        break;
      case "mount":
        this.guarded(() =>
          this.call("spools/mount", { spool_id: data.spool_id, slot: this._dialog.slot }),
        );
        break;
      default:
        break;
    }
  }

  // -- rendering ---------------------------------------------------------------------

  render() {
    if (!this._root) return;
    this._root.innerHTML = `
      ${this.header()}
      <main>
        ${this._error ? this.errorBar() : ""}
        ${this._loading ? `<div class="empty">Loading…</div>` : this.body()}
      </main>
      ${this._dialog ? this.dialog() : ""}
    `;
  }

  header() {
    const badge = this._stock?.needs_weighing
      ? `<span class="count">${this._stock.needs_weighing}</span>`
      : "";
    return `
      <header>
        <h1>Filament Ledger</h1>
        <nav>
          ${TABS.map(
            (tab) => `
            <button data-action="tab" data-id="${tab.id}" class="${this._tab === tab.id && !this._detail ? "on" : ""}">
              ${tab.label}${tab.id === "inventory" ? badge : ""}
            </button>`,
          ).join("")}
        </nav>
      </header>`;
  }

  errorBar() {
    return `<div class="error">
      <span>${esc(this._error)}</span>
      <button data-action="dismiss-error">Dismiss</button>
    </div>`;
  }

  body() {
    if (this._detail) return this.detailView();
    if (this._tab === "review") return this.reviewView();
    if (this._tab === "ams") return this.amsView();
    return this.inventoryView();
  }

  // -- inventory ---------------------------------------------------------------------

  inventoryView() {
    if (!this._spools.length) {
      return `
        <div class="empty teach">
          <h2>No spools yet.</h2>
          <p>Register the filament you own, and the ledger starts tracking every gram that leaves it.</p>
          <button class="primary" data-action="dialog" data-id="new-spool">Register your first spool</button>
        </div>`;
    }

    const stat = (key, value, alert) =>
      `<div class="stat"><div class="k">${key}</div><div class="v ${alert ? "alert" : ""}">${value}</div></div>`;

    return `
      <section class="stack">
        <div class="card summary">
          ${stat("Total stock", grams(this._stock?.total_g ?? 0))}
          ${stat("Spools", this._stock?.spool_count ?? 0)}
          ${stat("Need weighing", this._stock?.needs_weighing ?? 0, this._stock?.needs_weighing)}
        </div>
        <div class="bar">
          <button class="primary" data-action="dialog" data-id="new-spool">+ New spool</button>
        </div>
        <div class="grid">${this._spools.map((s) => this.spoolCard(s)).join("")}</div>
      </section>`;
  }

  spoolCard(spool) {
    const conf = CONFIDENCE[spool.confidence];
    const sealed = spool.state === "SEALED";
    const bar = sealed
      ? `<span class="chip">Sealed</span>`
      : `<div class="barline">
           <div class="track"><i style="width:${spool.percentage}%;background:${esc(spool.colour)}"></i></div>
           <span class="pct">${spool.percentage}%</span>
         </div>`;
    return `
      <article class="card spool ${spool.has_anomaly ? "anomaly" : ""}" data-action="open" data-id="${esc(spool.id)}">
        <div class="swatch" style="background:${esc(spool.colour)}"></div>
        <div class="spool-body">
          <div class="name">${esc(spool.name)}</div>
          <div class="sub">${esc(spool.material)}${spool.vendor ? ` · ${esc(spool.vendor)}` : ""}</div>
          <div class="big">${spool.balance_g}<small> g</small></div>
          ${bar}
          <div class="foot">
            <span class="conf ${conf.cls}"><i></i>${conf.label}</span>
            <span class="muted">· ${esc(spool.location.label)}</span>
          </div>
          ${spool.needs_weighing ? `<div class="cta">Weigh this spool</div>` : ""}
        </div>
      </article>`;
  }

  // -- AMS ---------------------------------------------------------------------------

  amsView() {
    const slots = [1, 2, 3, 4].map((slot) => {
      const spool = this._spools.find(
        (s) => s.location.kind === "AMS_SLOT" && s.location.slot === slot,
      );
      if (!spool) {
        return `<div class="card tray empty-tray">
          <div class="n">Slot ${slot}</div>
          <div class="muted">Empty</div>
          <button data-action="mount-slot" data-slot="${slot}">Mount</button>
        </div>`;
      }
      const conf = CONFIDENCE[spool.confidence];
      return `<div class="card tray">
        <div class="n">Slot ${slot}</div>
        <div class="reel" style="background:${esc(spool.colour)}"></div>
        <div class="name">${esc(spool.name)}</div>
        <div class="big">${spool.balance_g}<small> g</small></div>
        <div class="barline">
          <div class="track"><i style="width:${spool.percentage}%;background:${esc(spool.colour)}"></i></div>
          <span class="pct">${spool.percentage}%</span>
        </div>
        <div class="foot"><span class="conf ${conf.cls}"><i></i>${conf.label}</span></div>
        <div class="tray-actions">
          <button data-action="open" data-id="${esc(spool.id)}">Open</button>
          <button data-action="unmount" data-id="${esc(spool.id)}">Unmount</button>
        </div>
      </div>`;
    });

    return `
      <section class="stack">
        <div class="note">
          No printer is connected yet. Slots are assigned by hand — mounting records no
          movement, because moving a spool consumes no filament.
        </div>
        <div class="trays">${slots.join("")}</div>
      </section>`;
  }

  // -- review ------------------------------------------------------------------------

  reviewView() {
    return `
      <div class="empty teach">
        <h2>Nothing to review.</h2>
        <p>
          Cancelled and failed prints will appear here so you can confirm how much filament
          they used. Nothing is ever deducted for them until you say so.
        </p>
        <p class="muted">
          This queue fills up once the printer is connected. Until then every movement in the
          ledger is one you entered yourself.
        </p>
      </div>`;
  }

  // -- spool detail ------------------------------------------------------------------

  detailView() {
    const spool = this._detail;
    const conf = CONFIDENCE[spool.confidence];
    const rows = spool.history
      .map(
        (line) => `
        <tr>
          <td class="when">${when(line.occurred_at)}</td>
          <td class="what">${esc(line.label)}
            <span>${line.note ? `${esc(line.note)} · ` : ""}${esc(line.source_label)}</span>
          </td>
          <td class="amt ${line.amount_g < 0 ? "minus" : "plus"}">${signed(line.amount_g)}</td>
          <td class="bal">${line.balance_after_g}</td>
        </tr>`,
      )
      .join("");

    const sum = spool.history
      .slice()
      .reverse()
      .map((l) => signed(l.amount_g))
      .join(" ")
      .replace(/^\+ /, "");

    return `
      <section class="stack">
        <button class="link" data-action="back">← All spools</button>
        <div class="card detail">
          <div class="reel-big" style="background:${esc(spool.colour)}"></div>
          <div class="meta">
            <h2>${esc(spool.name)}</h2>
            <div class="big">${spool.balance_g}<small> g of ${spool.opening_weight_g} g</small></div>
            <div class="barline">
              <div class="track"><i style="width:${spool.percentage}%;background:${esc(spool.colour)}"></i></div>
              <span class="pct">${spool.percentage}%</span>
            </div>
            <div class="facts">${esc(spool.material)}${spool.vendor ? ` · ${esc(spool.vendor)}` : ""} · ${esc(spool.colour)}</div>
            <div class="facts">${esc(spool.location.label)} · ${esc(spool.state.toLowerCase())}${spool.tag_uid ? ` · tag ${esc(spool.tag_uid)}` : ""}</div>
            <div class="foot"><span class="conf ${conf.cls}"><i></i>${conf.label} confidence</span></div>
          </div>
        </div>

        <div class="bar">
          <button class="primary" data-action="dialog" data-id="weigh">Weigh</button>
          <button data-action="dialog" data-id="adjust">Adjust</button>
          <button data-action="dialog" data-id="discard">Discard</button>
        </div>

        <div class="card ledger-wrap">
          <h3>Movement history</h3>
          <div class="scroll">
            <table class="ledger">
              <thead><tr><th>When</th><th>Entry</th><th class="r">Amount</th><th class="r">Balance</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
          <div class="checksum">${esc(sum)} = <b>${spool.balance_exact_g.toFixed(1)} g</b></div>
          <p class="muted small">
            Read bottom-up it is a derivation, not an assertion. Nothing above can be edited —
            a correction is a new row.
          </p>
        </div>
      </section>`;
  }

  // -- dialogs -----------------------------------------------------------------------

  dialog() {
    const bodies = {
      "new-spool": this.newSpoolForm(),
      weigh: this.weighForm(),
      adjust: this.adjustForm(),
      discard: this.discardForm(),
      mount: this.mountForm(),
    };
    return `
      <div class="scrim" data-action="close-dialog">
        <div class="modal" onclick="event.stopPropagation()">
          ${bodies[this._dialog.kind] || ""}
        </div>
      </div>`;
  }

  newSpoolForm() {
    const defaults = this._stock?.defaults || { opening_weight_g: 1000, core_weight_g: 250 };
    return `
      <form data-form="new-spool">
        <h3>Register a spool</h3>
        <label>Material
          <select name="material">${MATERIALS.map((m) => `<option>${m}</option>`).join("")}</select>
        </label>
        <label>Name if OTHER<input name="material_other" placeholder="Nylon-X"></label>
        <label>Colour<input name="colour" value="#000000" type="color"></label>
        <label>Opening weight (g)
          <input name="opening_weight_g" type="number" step="0.1" min="1" value="${defaults.opening_weight_g}" required>
        </label>
        <label>Empty reel weight (g)
          <input name="core_weight_g" type="number" step="0.1" min="0" value="${defaults.core_weight_g}" required>
          <small>A scale weighs the whole spool. The ledger subtracts this for you.</small>
        </label>
        <label>Vendor<input name="vendor" placeholder="Bambu Lab"></label>
        <label>Label<input name="label" placeholder="Shelf B"></label>
        ${this.formActions("Register")}
      </form>`;
  }

  weighForm() {
    return `
      <form data-form="weigh">
        <h3>Weigh spool</h3>
        <p class="muted">Put the whole spool on a kitchen scale.</p>
        <label>Measured weight (g)<input name="measured_g" type="number" step="0.1" min="0" required autofocus></label>
        <label class="row"><input name="includes_core" type="checkbox" checked> Includes the reel (${this._detail.core_weight_g} g)</label>
        <label>Note<input name="note" placeholder="optional"></label>
        <p class="muted small">This is recorded as a correction. Nothing in your history changes.</p>
        ${this.formActions("Record")}
      </form>`;
  }

  adjustForm() {
    return `
      <form data-form="adjust">
        <h3>Adjust</h3>
        <label>Amount (g)<input name="amount_g" type="number" step="0.1" required autofocus>
          <small>Negative removes, positive adds.</small>
        </label>
        <label>Reason<input name="reason" required placeholder="why"></label>
        <p class="muted small">The reason is required. An unexplained adjustment is indistinguishable from a bug.</p>
        ${this.formActions("Record")}
      </form>`;
  }

  discardForm() {
    return `
      <form data-form="discard">
        <h3>Discard</h3>
        <label>What<select name="mode">
          <option value="partial">Part of this spool</option>
          <option value="whole_spool">The whole spool</option>
        </select></label>
        <label>Amount (g), if partial<input name="amount_g" type="number" step="0.1" min="0"></label>
        <label>Reason<input name="reason" required placeholder="tangled section"></label>
        ${this.formActions("Discard")}
      </form>`;
  }

  mountForm() {
    const available = this._spools.filter((s) => s.location.kind !== "AMS_SLOT");
    if (!available.length) {
      return `<h3>Mount in slot ${this._dialog.slot}</h3>
        <p class="muted">Every spool is already mounted. Unmount one first.</p>
        ${this.formActions(null)}`;
    }
    return `
      <form data-form="mount">
        <h3>Mount in slot ${this._dialog.slot}</h3>
        <label>Spool<select name="spool_id">
          ${available.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} — ${s.balance_g} g</option>`).join("")}
        </select></label>
        ${this.formActions("Mount")}
      </form>`;
  }

  formActions(confirmLabel) {
    return `<div class="actions">
      <button type="button" data-action="close-dialog">Cancel</button>
      ${confirmLabel ? `<button type="submit" class="primary">${confirmLabel}</button>` : ""}
    </div>`;
  }
}

const STYLES = `
:host { display: block; height: 100%; background: var(--primary-background-color); }
* { box-sizing: border-box; }
#root { min-height: 100%; color: var(--primary-text-color);
  font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif); }

header { background: var(--app-header-background-color, var(--primary-color));
  color: var(--app-header-text-color, #fff); padding: 12px 20px 0; position: sticky; top: 0; z-index: 5; }
header h1 { margin: 0 0 10px; font-size: 20px; font-weight: 400; }
nav { display: flex; gap: 4px; overflow-x: auto; }
nav button { background: none; border: 0; border-bottom: 2px solid transparent; cursor: pointer;
  color: inherit; opacity: .75; font: inherit; font-size: 14px; padding: 8px 16px 10px; white-space: nowrap; }
nav button.on { opacity: 1; border-bottom-color: currentColor; font-weight: 500; }
nav .count { display: inline-grid; place-items: center; min-width: 18px; height: 18px; padding: 0 5px;
  margin-left: 7px; border-radius: 9px; background: var(--error-color, #c62828); color: #fff; font-size: 11px; font-weight: 700; }

main { padding: 16px; max-width: 1100px; margin: 0 auto; }
.stack { display: flex; flex-direction: column; gap: 14px; }
.card { background: var(--card-background-color, #fff); border-radius: var(--ha-card-border-radius, 12px);
  box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.08)); border: 1px solid var(--divider-color, #e0e0e0); }
.muted { color: var(--secondary-text-color); }
.small { font-size: 12.5px; }

.error { display: flex; gap: 12px; align-items: center; background: var(--error-color, #c62828);
  color: #fff; padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; }
.error button { margin-left: auto; background: rgba(255,255,255,.2); color: #fff; border: 0;
  padding: 5px 12px; border-radius: 6px; cursor: pointer; font: inherit; }

.empty { padding: 56px 20px; text-align: center; color: var(--secondary-text-color); }
.empty.teach h2 { color: var(--primary-text-color); font-weight: 400; margin: 0 0 10px; }
.empty.teach p { max-width: 46ch; margin: 0 auto 14px; line-height: 1.6; }

button { font: inherit; font-size: 14px; padding: 8px 16px; border-radius: 8px;
  border: 1px solid var(--divider-color, #e0e0e0); background: var(--card-background-color, #fff);
  color: var(--primary-text-color); cursor: pointer; }
button.primary { background: var(--primary-color); border-color: var(--primary-color); color: #fff; font-weight: 500; }
button.link { background: none; border: 0; color: var(--primary-color); padding: 0; align-self: flex-start; }
.bar { display: flex; gap: 8px; flex-wrap: wrap; }

.summary { display: flex; flex-wrap: wrap; }
.stat { padding: 14px 20px; flex: 1 1 120px; border-right: 1px solid var(--divider-color, #eee); }
.stat:last-child { border-right: 0; }
.stat .k { font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: var(--secondary-text-color); font-weight: 600; }
.stat .v { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; margin-top: 2px; }
.stat .v.alert { color: var(--error-color, #c62828); }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.spool { display: flex; overflow: hidden; cursor: pointer; }
.spool.anomaly { border-left: 3px solid var(--warning-color, #e07b00); }
.swatch { width: 12px; flex: none; }
.spool-body { padding: 13px 15px; display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.name { font-weight: 500; }
.sub { font-size: 12.5px; color: var(--secondary-text-color); }
.big { font-size: 26px; font-weight: 600; font-variant-numeric: tabular-nums; margin: 6px 0 3px; letter-spacing: -.02em; }
.big small { font-size: 13px; font-weight: 500; color: var(--secondary-text-color); }
.chip { align-self: flex-start; font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
  font-weight: 600; border: 1px solid var(--divider-color, #ddd); border-radius: 999px; padding: 2px 10px;
  color: var(--secondary-text-color); }
.barline { display: flex; align-items: center; gap: 8px; }
.track { flex: 1; height: 6px; border-radius: 3px; background: var(--divider-color, #eee); overflow: hidden; }
.track i { display: block; height: 100%; }
.pct { font-size: 12px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; min-width: 30px; text-align: right; }
.foot { display: flex; gap: 7px; align-items: center; margin-top: 7px; font-size: 12.5px; flex-wrap: wrap; }
.cta { color: var(--error-color, #c62828); font-size: 12.5px; font-weight: 500; }

.conf { display: inline-flex; align-items: center; gap: 6px; font-weight: 500; }
.conf i { width: 8px; height: 8px; border-radius: 50%; }
.conf.high { color: var(--success-color, #2e7d32); } .conf.high i { background: var(--success-color, #2e7d32); }
.conf.med  { color: var(--warning-color, #e07b00); } .conf.med i  { background: var(--warning-color, #e07b00); }
.conf.low  { color: var(--error-color, #c62828); }   .conf.low i  { background: var(--error-color, #c62828); }

.note { background: var(--card-background-color, #fff); border-left: 3px solid var(--primary-color);
  padding: 11px 15px; border-radius: 0 8px 8px 0; font-size: 13.5px; color: var(--secondary-text-color); }

.trays { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.tray { padding: 14px; display: flex; flex-direction: column; gap: 5px; }
.tray .n { font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase; color: var(--secondary-text-color); font-weight: 700; }
.tray .reel { height: 42px; border-radius: 6px; margin: 4px 0; }
.tray.empty-tray { align-items: center; justify-content: center; text-align: center; gap: 10px; border-style: dashed; min-height: 170px; }
.tray-actions { display: flex; gap: 6px; margin-top: 8px; }
.tray-actions button { padding: 5px 10px; font-size: 12.5px; flex: 1; }

.detail { display: flex; gap: 16px; padding: 18px; flex-wrap: wrap; }
.reel-big { width: 60px; height: 60px; border-radius: 10px; flex: none; }
.detail .meta { flex: 1 1 220px; min-width: 0; }
.detail h2 { margin: 0 0 4px; font-size: 19px; font-weight: 500; }
.facts { font-size: 12.5px; color: var(--secondary-text-color); }

.ledger-wrap { padding: 16px 18px 18px; }
.ledger-wrap h3 { margin: 0 0 10px; font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--secondary-text-color); font-weight: 700; }
.scroll { overflow-x: auto; }
table.ledger { width: 100%; border-collapse: collapse; min-width: 460px; }
table.ledger th { text-align: left; font-size: 10.5px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--secondary-text-color); font-weight: 700; padding-bottom: 8px; border-bottom: 1px solid var(--divider-color, #e0e0e0); }
table.ledger th.r, table.ledger td.amt, table.ledger td.bal { text-align: right; }
table.ledger td { padding: 9px 0; border-bottom: 1px solid var(--divider-color, #f0f0f0); vertical-align: top; }
table.ledger td.when { font-size: 12.5px; color: var(--secondary-text-color); white-space: nowrap; padding-right: 14px; }
table.ledger td.what { font-size: 13.5px; }
table.ledger td.what span { display: block; font-size: 12px; color: var(--secondary-text-color); }
table.ledger td.amt, table.ledger td.bal { font-family: ui-monospace, "Roboto Mono", Menlo, monospace;
  font-variant-numeric: tabular-nums; white-space: nowrap; padding-left: 16px; }
table.ledger td.amt { font-weight: 600; }
table.ledger td.amt.minus { color: var(--error-color, #c62828); }
table.ledger td.amt.plus { color: var(--success-color, #2e7d32); }
table.ledger td.bal { color: var(--secondary-text-color); }
.checksum { margin-top: 12px; padding: 10px 13px; border-radius: 8px; background: var(--secondary-background-color, #f5f5f5);
  font-family: ui-monospace, "Roboto Mono", Menlo, monospace; font-size: 12.5px; overflow-x: auto;
  white-space: nowrap; color: var(--secondary-text-color); }
.checksum b { color: var(--primary-text-color); }

.scrim { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: grid; place-items: center;
  padding: 16px; z-index: 20; }
.modal { background: var(--card-background-color, #fff); border-radius: 14px; padding: 20px;
  width: min(420px, 100%); max-height: 86vh; overflow-y: auto; }
.modal h3 { margin: 0 0 14px; font-size: 17px; font-weight: 500; }
.modal form { display: flex; flex-direction: column; gap: 12px; }
.modal label { display: flex; flex-direction: column; gap: 5px; font-size: 13px; color: var(--secondary-text-color); }
.modal label.row { flex-direction: row; align-items: center; gap: 9px; }
.modal input, .modal select { font: inherit; font-size: 15px; padding: 9px 11px; border-radius: 8px;
  border: 1px solid var(--divider-color, #ddd); background: var(--primary-background-color, #fff);
  color: var(--primary-text-color); }
.modal input[type=checkbox] { width: auto; }
.modal input[type=color] { padding: 3px; height: 42px; }
.modal small { color: var(--secondary-text-color); font-size: 12px; }
.modal .actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 6px; }

@media (max-width: 600px) { main { padding: 12px; } .detail { gap: 12px; } }
`;

customElements.define("filament-ledger-panel", FilamentLedgerPanel);
