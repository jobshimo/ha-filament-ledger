// Home Assistant owns every rule. The ledger keeps only a recoverable creation draft.
import { esc } from "../i18n.js";

export const BLUEPRINT_PATH = "filament_ledger/low_stock_v1.yaml";
export function canonical(value) {
  if (Array.isArray(value)) return JSON.stringify(value.map(v => JSON.parse(canonical(v))));
  if (value && typeof value === "object") return JSON.stringify(Object.fromEntries(Object.keys(value).sort().map(k => [k, JSON.parse(canonical(value[k]))])));
  return JSON.stringify(value);
}

export function notificationDestinations(hass) {
  const services = hass?.services?.notify ?? {};
  const destinations = [];
  for (const [name, service] of Object.entries(services)) {
    if (name === "send_message") continue;
    const unsupported = Object.entries(service.fields ?? {}).some(([key, f]) => f.required && !["message", "title"].includes(key));
    destinations.push({id: `service:notify.${name}`, service: `notify.${name}`, label: name === "notify" ? "notify.notify" : service.name || name, available: !unsupported});
  }
  if (services.send_message) for (const [id, state] of Object.entries(hass.states ?? {})) {
    if (id.startsWith("notify.")) destinations.push({id: `entity:${id}`, service: "notify.send_message", entity: id,
      label: state.attributes?.friendly_name || id, available: !["unavailable", "unknown"].includes(state.state)});
  }
  return destinations;
}

export class Notifications {
  _nativeWS(type, payload = {}) { return this._hass.callWS({type, ...payload}); }
  _notificationKey() { return `filament-ledger:notification:${this._hass?.user?.id ?? "local"}`; }
  _rememberNotification() {
    try { window.sessionStorage.setItem(this._notificationKey(), JSON.stringify(this._notificationDraft)); } catch { /* memory only */ }
  }
  _forgetNotification() {
    try { window.sessionStorage.removeItem(this._notificationKey()); } catch { /* memory only */ }
  }

  async _ensureBlueprint() {
    if (!this._hass?.user?.is_admin) throw new Error(this._t("notifications.admin"));
    const base = new URL("../blueprints/", import.meta.url);
    const responses = await Promise.all([fetch(new URL("low_stock_v1.yaml", base)), fetch(new URL("low_stock_v1.contract.json", base))]);
    if (responses.some(r => !r.ok)) throw new Error(this._t("notifications.assetsUnavailable"));
    const [yaml, contract] = await Promise.all([responses[0].text(), responses[1].json()]);
    let listed = await this._nativeWS("blueprint/list", {domain: "automation"});
    if (!listed[BLUEPRINT_PATH]) {
      try { await this._nativeWS("blueprint/save", {domain: "automation", path: BLUEPRINT_PATH, yaml, allow_override: false}); }
      catch (error) { if (error.code !== "already_exists") throw error; }
      listed = await this._nativeWS("blueprint/list", {domain: "automation"});
    }
    if (canonical(listed[BLUEPRINT_PATH]?.metadata) !== canonical(contract.metadata)) {
      const previousMetadata = structuredClone(contract.metadata);
      delete previousMetadata.input.actions.default;
      const previous = await this._nativeWS("blueprint/substitute", {domain: "automation", path: BLUEPRINT_PATH, input: contract.input});
      if (canonical(listed[BLUEPRINT_PATH]?.metadata) !== canonical(previousMetadata)
        || canonical(previous.substituted_config) !== canonical(contract.substituted_config)) throw new Error(this._t("notifications.blueprintConflict"));
      // Only the unchanged shipped v1 is upgraded; custom blueprint edits stay authoritative.
      await this._nativeWS("blueprint/save", {domain: "automation", path: BLUEPRINT_PATH, yaml, allow_override: true});
      listed = await this._nativeWS("blueprint/list", {domain: "automation"});
      if (canonical(listed[BLUEPRINT_PATH]?.metadata) !== canonical(contract.metadata)) throw new Error(this._t("notifications.blueprintConflict"));
    }
    const substituted = await this._nativeWS("blueprint/substitute", {domain: "automation", path: BLUEPRINT_PATH, input: contract.input});
    if (canonical(substituted.substituted_config) !== canonical(contract.substituted_config)) throw new Error(this._t("notifications.blueprintConflict"));
  }

  async _setupNotification(custom = false) {
    if (this._notificationBusy) return;
    this._notificationBusy = true;
    try {
      await this._ensureBlueprint();
      this._notificationReady = true;
      const registry = await this._nativeWS("config/entity_registry/list");
      this._notificationSpools = registry.filter(e => e.platform === "filament_ledger" && e.entity_id.startsWith("binary_sensor.") && e.unique_id?.endsWith("_low_stock"));
      if (!custom) {
        let saved;
        try { saved = JSON.parse(window.sessionStorage.getItem(this._notificationKey())); } catch { /* memory only */ }
        const defaultDestination = notificationDestinations(this._hass).find(d => d.id === "service:notify.notify" && d.available)?.id ?? "";
        this._notificationDraft = saved?.id ? saved : {id: this._newRequestId(), alias: this._t("notifications.defaultName"), scope: "all", spools: [], destination: defaultDestination, phase: "edit"};
        if (this._notificationPresetSpoolId && !saved?.id) {
          this._notificationDraft.scope = "selected";
          const entity = this._notificationSpools.find(e => e.unique_id === `filament_ledger_${this._notificationPresetSpoolId}_low_stock`);
          this._notificationDraft.spools = entity ? [entity.entity_id] : [];
        }
        this._notificationPresetSpoolId = null;
        this._rememberNotification();
      }
      this._notificationError = null;
      await this._loadNotifications();
    } catch (error) { this._notificationError = error.message || String(error); }
    this._notificationBusy = false;
    this.render();
  }

  _notificationAction(test = false) {
    const draft = this._notificationDraft;
    const destination = notificationDestinations(this._hass).find(d => d.id === draft.destination && d.available);
    if (!destination) throw new Error(this._t("notifications.destinationMissing"));
    if (draft.scope === "selected" && (!draft.spools.length || draft.spools.some(id => !this._notificationSpools?.some(e => e.entity_id === id) || [undefined, "unavailable", "unknown"].includes(this._hass.states[id]?.state)))) throw new Error(this._t("notifications.scopeMissing"));
    return {action: destination.service, ...(destination.entity ? {target: {entity_id: destination.entity}} : {}),
      data: {message: this._t(test ? "notifications.testMessage" : "notifications.message"),
        title: this._t("notifications.messageTitle")}};
  }

  _notificationConfig() {
    const draft = this._notificationDraft;
    const action = this._notificationAction();
    return {id: draft.id, alias: draft.alias || this._t("notifications.defaultName"),
      use_blueprint: {path: BLUEPRINT_PATH, input: {spools: draft.scope === "selected" ? draft.spools : [], actions: [action]}}};
  }

  async _readAutomation(id) {
    try { return await this._hass.callApi("GET", `config/automation/config/${encodeURIComponent(id)}`); }
    catch (error) {
      if (error.status_code === 404 || error.status === 404) return null;
      throw error;
    }
  }

  async _saveNotification() {
    if (this._notificationBusy) return;
    this._notificationBusy = true;
    try {
      if (!this._hass.user?.is_admin) throw new Error(this._t("notifications.admin"));
      await this._ensureBlueprint();
      const config = this._notificationConfig();
      this._notificationDraft.phase = "saving";
      this._rememberNotification(); this._paintNotificationStatus();
      const existing = await this._readAutomation(config.id);
      if (existing && canonical(existing) !== canonical(config)) throw new Error(this._t("notifications.configConflict"));
      if (!existing) await this._hass.callApi("POST", `config/automation/config/${encodeURIComponent(config.id)}`, config);
      const readback = await this._readAutomation(config.id);
      if (canonical(readback) !== canonical(config)) throw new Error(this._t("notifications.readbackFailed"));
      this._notificationVerifiedConfig = readback;
      this._notificationDraft.phase = "activating";
      this._notificationError = null;
      this._rememberNotification();
      this._checkNotificationActivation();
      await this._loadNotifications();
    } catch (error) {
      this._notificationDraft.phase = "review";
      this._notificationError = error.message || String(error);
    }
    this._notificationBusy = false;
    this.render();
  }

  _checkNotificationActivation() {
    this._paintNotificationStatus();
    const draft = this._notificationDraft;
    if (draft?.phase !== "activating") return;
    const entity = Object.values(this._hass.states ?? {}).find(s => s.entity_id?.startsWith("automation.") && s.attributes?.id === draft.id && ["on", "off"].includes(s.state));
    if (entity) {
      draft.phase = "active"; this._forgetNotification();
      if (this._notificationVerifiedConfig) this._notificationRules = [
        ...(this._notificationRules ?? []).filter(r => r.config.id !== draft.id),
        {entity_id: entity.entity_id, state: entity.state, config: this._notificationVerifiedConfig}];
    }
    this._paintNotificationStatus();
  }

  _paintNotificationStatus() {
    const status = this._root?.querySelector("[data-notification-status]");
    const rules = this._root?.querySelector(".notification-rules");
    if (rules) rules.innerHTML = this._notificationRulesMarkup();
    if (status) status.textContent = this._notificationError || this._t("notifications.phase." + (this._notificationDraft?.phase ?? "edit"));
  }

  async _testNotification() {
    if (this._notificationBusy) return;
    this._notificationBusy = true;
    try {
      const action = this._notificationAction(true);
      const [domain, service] = action.action.split(".");
      await this._hass.callService(domain, service, action.data, action.target);
      this._notificationError = null;
      this._notificationTestAccepted = true;
    } catch (error) { this._notificationError = error.message || String(error); }
    this._notificationBusy = false;
    this.render();
  }

  async _loadNotifications() {
    if (!this._hass?.user?.is_admin) return;
    const automations = Object.entries(this._hass.states ?? {}).filter(([id, s]) => id.startsWith("automation.") && s.attributes?.id);
    this._notificationRules = (await Promise.all(automations.map(async ([entity_id, state]) => {
      try {
        const config = await this._readAutomation(state.attributes.id);
        if (config?.use_blueprint?.path !== BLUEPRINT_PATH) return null;
        return {entity_id, state: state.state, config};
      } catch { return null; }
    }))).filter(Boolean);
  }

  async _toggleNotification(entityId) {
    try {
      const state = this._hass.states[entityId];
      await this._hass.callService("automation", state?.state === "on" ? "turn_off" : "turn_on", {}, {entity_id: entityId});
      await this._loadNotifications();
      this._notificationError = null;
    } catch (error) { this._notificationError = error.message || String(error); }
    this.render();
  }

  _notificationInput(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    Object.assign(this._notificationDraft, {alias: data.alias, scope: data.scope, destination: data.destination,
      spools: Array.from(form.querySelector('[name="spools"]').selectedOptions).map(o => o.value)});
    this._notificationTestAccepted = false;
    this._rememberNotification();
  }

  _notificationRulesMarkup() {
    const t = this._t;
    const editor = id => `/config/automation/edit/${encodeURIComponent(id)}`;
    return (this._notificationRules ?? []).map(r => `<li><strong>${esc(r.config.alias || r.entity_id)}</strong> · ${esc(this._hass.states?.[r.entity_id]?.state ?? "unavailable")}
      <button data-action="notification-toggle" data-id="${esc(r.entity_id)}">${t(this._hass.states?.[r.entity_id]?.state === "on" ? "notifications.disable" : "notifications.enable")}</button>
      <a href="${editor(r.config.id)}">${t("notifications.nativeEdit")}</a><small>${t("notifications.advanced")}</small>
      <small>${esc((r.config.use_blueprint?.input?.spools ?? []).map(id => this._hass.states?.[id]?.attributes?.friendly_name || id + " · " + t("notifications.unavailable")).join(", ") || t("notifications.all"))}</small></li>`).join("");
  }

  notificationSettings() {
    const t = this._t;
    if (!this._hass?.user?.is_admin) return `<section><h3>${t("notifications.title")}</h3><p>${t("notifications.admin")}</p></section>`;
    const draft = this._notificationDraft;
    const disabled = this._notificationBusy ? "disabled" : "";
    const options = (values, selected) => values.map(([v,l]) => `<option value="${esc(v)}" ${v === selected ? "selected" : ""}>${esc(l)}</option>`).join("");
    let body = "";
    if (draft) {
      const review = draft.phase !== "edit";
      body = review ? `<p>${esc(draft.alias)} · ${draft.scope === "all" ? t("notifications.all") : esc(draft.spools.join(", "))} · ${esc(draft.destination)}</p>
        <p>${t("notifications.reviewHelp")}</p><button data-action="notification-test" ${disabled}>${t("notifications.test")}</button>
        <button class="primary" data-action="notification-save" ${disabled || draft.phase === "active" ? "disabled" : ""}>${t("notifications.save")}</button>`
        : `<form data-form="notification"><label>${t("dlg.label")}<input name="alias" value="${esc(draft.alias)}" maxlength="200"></label>
          <label>${t("notifications.scope")}<select name="scope">${options([["all",t("notifications.all")],["selected",t("notifications.selected")]],draft.scope)}</select></label>
          <label>${t("notifications.selected")}<select name="spools" multiple>${(this._notificationSpools ?? []).map(e => `<option value="${esc(e.entity_id)}" ${draft.spools.includes(e.entity_id) ? "selected" : ""}>${esc(this._hass.states[e.entity_id]?.attributes?.friendly_name || e.name || e.entity_id)}</option>`).join("")}</select></label>
          <label>${t("notifications.destination")}<select name="destination" required><option value="">${t("notifications.chooseDestination")}</option>${notificationDestinations(this._hass).map(d => `<option value="${esc(d.id)}" ${draft.destination === d.id ? "selected" : ""} ${d.available ? "" : "disabled"}>${esc(d.label)}${d.available ? "" : " · " + t("notifications.unavailable")}</option>`).join("")}</select></label>
          <button type="submit" class="primary" ${disabled}>${t("notifications.review")}</button></form>`;
      body += `<p data-notification-status role="status">${esc(this._notificationError || t("notifications.phase." + draft.phase))}</p>
        ${this._notificationTestAccepted ? `<p role="status">${t("notifications.accepted")}</p>` : ""}
        <button data-action="notification-cancel" ${disabled}>${t("act.cancel")}</button>`;
    }
    return `<section class="notification-settings"><h3>${t("notifications.title")}</h3><p>${t("notifications.intro")}</p>
      ${!draft ? `<button data-action="notification-setup" ${disabled}>${t("notifications.setup")}</button>` : ""}
      <button data-action="notification-custom" ${disabled}>${t("notifications.custom")}</button>
      ${this._notificationReady ? `<a href="/config/blueprint">${t("notifications.customEditor")}</a>` : ""}
      ${!draft && this._notificationError ? `<p role="alert">${esc(this._notificationError)}</p>` : ""}
      ${body}<ul class="notification-rules">${this._notificationRulesMarkup()}</ul></section>`;
  }
}
