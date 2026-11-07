const KEY = "filament_ledger.appearance";
const valid = value => ["auto", "light", "dark"].includes(value);

export class Appearance {
  _initAppearance() {
    this._appearance = "auto";
    try {
      const stored = window.localStorage.getItem(KEY);
      if (valid(stored)) this._appearance = stored;
    } catch { this._appearanceUnsaved = true; }
    this._systemTheme = window.matchMedia?.("(prefers-color-scheme: dark)") ?? null;
    this._systemThemeChanged = () => this._applyAppearance();
    this._appearanceStorage = event => {
      if (event.key === KEY || event.key === null) {
        this._appearance = valid(event.newValue) ? event.newValue : "auto";
        this._applyAppearance();
      }
    };
    this._systemTheme?.addEventListener("change", this._systemThemeChanged);
    window.addEventListener("storage", this._appearanceStorage);
    this._applyAppearance();
  }

  _applyAppearance() {
    const mode = this._appearance ?? "auto";
    const theme = mode === "auto" ? (this._systemTheme?.matches ? "dark" : "light") : mode;
    this.setAttribute("data-theme", theme);
    this.style.colorScheme = theme;
    const select = this._root?.querySelector("[data-appearance]");
    if (select) select.value = mode;
    const message = this._root?.querySelector("[data-appearance-status]");
    if (message) message.textContent = this._appearanceUnsaved ? this._t("appearance.unsaved") : "";
  }

  _setAppearance(value) {
    if (!valid(value)) return;
    this._appearance = value;
    try {
      window.localStorage.setItem(KEY, value);
      this._appearanceUnsaved = false;
    } catch { this._appearanceUnsaved = true; }
    this._applyAppearance();
  }

  _stopAppearance() {
    this._systemTheme?.removeEventListener("change", this._systemThemeChanged);
    window.removeEventListener("storage", this._appearanceStorage);
  }

  appearanceSettings() {
    const mode = this._appearance ?? "auto";
    return `<section class="appearance-settings"><h3>${this._t("appearance.title")}</h3>
      <label>${this._t("appearance.mode")}<select data-appearance>${["auto", "light", "dark"].map(v => `<option value="${v}" ${mode === v ? "selected" : ""}>${this._t("appearance." + v)}</option>`).join("")}</select></label>
      <p class="muted small">${this._t("appearance.help")}</p>
      <p data-appearance-status role="status">${this._appearanceUnsaved ? this._t("appearance.unsaved") : ""}</p></section>`;
  }
}
