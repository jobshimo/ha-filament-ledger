import { esc } from "../i18n.js";

export class InventoryFilters {
  _inventoryPayload() {
    const f = this._inventoryFilters ?? {};
    return {view: "stock", ...Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "" && v !== false))};
  }

  async _loadInventory() {
    const revision = this._inventoryRevision = (this._inventoryRevision ?? 0) + 1;
    this._inventoryLoading = true;
    try {
      const rows = await this.call("spools/list", this._inventoryPayload());
      if (revision !== this._inventoryRevision) return;
      this._inventoryResults = rows;
      this._inventoryError = null;
    } catch (error) {
      if (revision !== this._inventoryRevision) return;
      this._inventoryError = error.message || String(error);
    }
    this._inventoryLoading = false;
    this._patchInventory();
  }

  _patchInventory() {
    const results = this._root?.querySelector("[data-inventory-results]");
    if (results) results.innerHTML = this.inventoryResults();
  }

  _changeInventory(control) {
    this._inventoryFilters = {...this._inventoryFilters,
      [control.dataset.inventory]: control.type === "checkbox" ? control.checked : control.value};
    clearTimeout(this._inventoryTimer);
    this._inventoryRevision = (this._inventoryRevision ?? 0) + 1;
    this._inventoryLoading = true;
    this._patchInventory();
    this._inventoryTimer = setTimeout(() => this._loadInventory(), 150);
  }

  _resetInventory() {
    this._inventoryFilters = {};
    clearTimeout(this._inventoryTimer);
    for (const control of this._root?.querySelectorAll("[data-inventory]") ?? []) {
      if (control.type === "checkbox") control.checked = false;
      else control.value = "";
    }
    this._loadInventory();
  }

  inventoryFilters() {
    const t = this._t, f = this._inventoryFilters ?? {};
    const select = (name, values) => `<label>${t("discovery." + name)}<select data-inventory="${name}"><option value="">${t("discovery.all")}</option>${values.map(([value,label]) => `<option value="${value}" ${f[name] === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>`;
    return `<details class="inventory-filters"><summary>${t("discovery.filters")}</summary><div class="filter-fields">
      <label>${t("discovery.search")}<input type="search" data-inventory="search" value="${esc(f.search || "")}" maxlength="120"></label>
      ${select("material", ["PLA", "PETG", "ABS", "ASA", "TPU", "PC", "PA", "PVA", "SUPPORT", "OTHER"].map(v => [v,v]))}
      ${select("location", ["STORAGE", "AMS_SLOT", "EXTERNAL_SPOOL"].map(v => [v,t("discovery.location." + v)]))}
      ${select("sort", ["remaining", "name"].map(v => [v,t("discovery.sort." + v)]))}
      <label class="check"><input type="checkbox" data-inventory="low_stock_only" ${f.low_stock_only ? "checked" : ""}>${t("discovery.lowOnly")}</label>
      <button data-action="inventory-reset">${t("discovery.reset")}</button>
    </div></details>`;
  }

  inventoryResults() {
    const rows = this._inventoryResults ?? this._spools;
    return `<p role="status" aria-live="polite">${this._inventoryLoading ? this._t("app.loading") : this._t("discovery.count", {count: rows.length})}</p>
      ${this._inventoryError ? `<p role="alert">${esc(this._inventoryError)}</p>` : ""}
      ${rows.length ? `<div class="grid">${rows.map(s => this.spoolCard(s)).join("")}</div>` : `<div class="empty">${this._t("discovery.noResults")}</div>`}`;
  }
}
