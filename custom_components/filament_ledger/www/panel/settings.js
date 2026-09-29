// The Settings tab.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc, readLanguageOverride } from "../i18n.js";

export class SettingsViews {
  // -- settings ----------------------------------------------------------------------

  /**
   * The four config-entry options, plus the per-device language (docs/14 §14.6.4).
   *
   * A non-admin sees the same values read-only with a line explaining why: a hidden tab
   * invites "it's broken", while a labelled read-only one teaches the model.
   */
  settingsView() {
    const t = this._t;
    if (this._settingsLoading && !this._settings) {
      return this.shell("", `<div class="empty">${t("app.loading")}</div>`);
    }
    const admin = Boolean(this._hass?.user?.is_admin);
    // No action row: Save belongs to the form it submits, beside the fields it commits.
    return this.shell(
      "",
      `<section class="stack">
        ${this._settings ? this.settingsForm(this._settings, admin) : ""}
        ${this.languageCard()}
      </section>`,
    );
  }

  settingsForm(settings, admin) {
    const t = this._t;
    const ro = admin ? "" : "disabled";
    const field = (name, label, help, value, attrs) => `
      <label>${label}
        <input name="${name}" type="number" ${attrs} value="${esc(value)}" ${ro} required>
        <small>${help}</small>
      </label>`;
    return `
      <form class="card set-card" data-form="settings">
        <h3 class="pr-h">${t("settings.heading")}</h3>
        ${admin ? "" : `<p class="muted small">${t("settings.readOnly")}</p>`}
        ${field(
          "default_opening_weight",
          t("settings.openingWeight"),
          t("settings.openingWeightHelp"),
          settings.default_opening_weight,
          'min="1" max="10000" step="1"',
        )}
        ${field(
          "default_core_weight",
          t("settings.coreWeight"),
          t("settings.coreWeightHelp"),
          settings.default_core_weight,
          'min="0" max="2000" step="1"',
        )}
        ${field(
          "anomaly_threshold",
          t("settings.anomalyThreshold"),
          t("settings.anomalyThresholdHelp"),
          settings.anomaly_threshold,
          'min="1" max="100" step="1"',
        )}
        <label class="row">
          <input name="auto_mount_on_rfid" type="checkbox" ${settings.auto_mount_on_rfid ? "checked" : ""} ${ro}>
          <span class="small">${t("settings.autoMount")}</span>
        </label>
        <small class="muted">${t("settings.autoMountHelp")}</small>
        ${
          admin
            ? `<p class="muted small">${t("settings.reloadWarning")}</p>
               <div class="actions">
                 <button type="submit" class="primary">${t("settings.save")}</button>
               </div>
               ${this._settingsSaved ? `<p class="saved small">${t("settings.saved")}</p>` : ""}`
            : ""
        }
      </form>`;
  }

  languageCard() {
    const t = this._t;
    const current = readLanguageOverride();
    const option = (value, label) =>
      `<button data-action="set-language" data-lang="${value}"
        class="${(current ?? "") === value ? "primary" : ""}">${label}</button>`;
    return `
      <div class="card set-card">
        <h3 class="pr-h">${t("settings.languageHeading")}</h3>
        <div class="bar">
          ${option("", t("settings.languageAuto"))}
          ${option("en", t("settings.languageEn"))}
          ${option("es", t("settings.languageEs"))}
        </div>
        <small class="muted">${t("settings.languageHelp")}</small>
      </div>`;
  }
}
