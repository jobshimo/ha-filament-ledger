// Registration owns a draft until the server's result is known. Preview is read-only.
import { esc } from "../i18n.js";

export class RegistrationActions {
  trayRegistrationButton(tray, action = "register-tray") {
    if (!tray || !["no_tag", "unknown_tag"].includes(tray.status)) return "";
    const mounted = this._spools.some(({location: l}) => l.kind === "AMS_SLOT"
      && l.printer === tray.printer && (l.ams ?? 1) === (tray.ams ?? 1) && l.slot === tray.slot);
    if (mounted) return "";
    return `<button data-action="${action}" data-printer="${esc(tray.printer)}"
      data-ams="${esc(tray.ams ?? 1)}" data-slot="${esc(tray.slot)}">${this._t("sync.register")}</button>`;
  }

  _registrationKey() {
    return `filament-ledger:registration:${this._hass?.user?.id ?? "local"}`;
  }

  _registrationDraft() {
    if (this._dialog.registration) return this._dialog.registration;
    let saved;
    try { saved = JSON.parse(window.sessionStorage.getItem(this._registrationKey())); } catch { /* memory only */ }
    const hint = this._dialog.prefill ?? {};
    const defaults = this._stock?.defaults ?? { opening_weight_g: 1000, core_weight_g: 250 };
    const material = hint.material_hint ?? "PLA";
    const known = ["PLA", "PETG", "ABS", "ASA", "TPU", "PC", "PA", "PVA", "SUPPORT", "OTHER"].includes(material);
    const draft = saved?.request_id ? saved : {
      request_id: this._newRequestId(), material: known ? material : "OTHER",
      material_other: known ? "" : material, colour: hint.colour_hint_rgba ?? hint.colour_hint ?? "#000000",
      colour_known: String(!this._dialog.prefill || Boolean(hint.colour_hint_rgba ?? hint.colour_hint)),
      opening_condition: "new", opening_basis: "DECLARED_NET",
      opening_weight_g: String(hint.weight_hint_g ?? defaults.opening_weight_g),
      gross_weight_g: "", core_weight_g: String(defaults.core_weight_g),
      vendor: "", label: hint.name_hint ?? "", tag_uid: hint.tag_uid ?? "",
    };
    if (!draft.destination && this._dialog.positionEntry) draft.destination = {
      positionEntry: true, printer: this._dialog.printer, ams: this._dialog.ams,
      slot: this._dialog.slot, external: this._dialog.external, holder: this._dialog.holder,
      expectedDestination: this._dialog.expectedDestination,
    };
    if (draft.destination) Object.assign(this._dialog, draft.destination);
    this._dialog.registration = draft;
    this._preview = null;
    this._previewError = null;
    this._previewRevision = (this._previewRevision ?? 0) + 1;
    clearTimeout(this._previewTimer);
    this._previewTimer = setTimeout(() => this._previewRegistration(), 0);
    return draft;
  }

  _newRequestId() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }

  _rememberRegistration() {
    try { window.sessionStorage.setItem(this._registrationKey(), JSON.stringify(this._dialog.registration)); } catch { /* memory only */ }
  }

  _clearRegistration() {
    clearTimeout(this._previewTimer);
    this._previewRevision = (this._previewRevision ?? 0) + 1;
    this._preview = null;
    try { window.sessionStorage.removeItem(this._registrationKey()); } catch { /* memory only */ }
  }

  _quantityPayload(draft) {
    const payload = { opening_basis: draft.opening_basis };
    const fields = draft.opening_basis === "MEASURED_GROSS"
      ? ["gross_weight_g", "core_weight_g"] : ["opening_weight_g"];
    for (const field of fields) {
      if (draft[field] !== "" && draft[field] != null) payload[field] = Number(draft[field]);
    }
    return payload;
  }

  _syncRegistration(form) {
    if (this._registrationPending) return;
    const data = Object.fromEntries(new FormData(form).entries());
    Object.assign(this._dialog.registration, data, {confirm_duplicate_tag: data.confirm_duplicate_tag || ""});
    this._rememberRegistration();
    this._preview = null;
    this._previewError = null;
    this._previewRevision = (this._previewRevision ?? 0) + 1;
    clearTimeout(this._previewTimer);
    this._paintRegistrationPreview(form);
    this._previewTimer = setTimeout(() => this._previewRegistration(), 250);
  }

  _paintRegistrationPreview(form) {
    if (!form) return;
    const frozen = Boolean(this._registrationPending || this._dialog?.registration?.submitted);
    for (const control of form.querySelectorAll("input, select, textarea")) control.disabled = frozen;
    const cancel = form.querySelector("[data-action='close-dialog']");
    if (cancel) cancel.disabled = Boolean(this._registrationPending);
    const gross = this._dialog?.registration?.opening_basis === "MEASURED_GROSS";
    for (const field of form.querySelectorAll("[data-quantity-mode]")) {
      field.hidden = (field.dataset.quantityMode === "gross") !== gross;
      for (const input of field.querySelectorAll("input")) input.disabled = frozen || field.hidden;
    }
    const submit = form.querySelector("button[type='submit']");
    if (submit) submit.disabled = !this._preview || this._registrationPending;
    const status = form.querySelector("[data-preview]");
    if (status) status.textContent = this._preview
      ? this._t("registration.preview", { grams: this._preview.opening_weight_g })
      : this._previewError || this._t("registration.previewPending");
  }

  async _previewRegistration() {
    if (this._dialog?.kind !== "new-spool") return;
    const revision = this._previewRevision;
    const quantity = this._quantityPayload(this._dialog.registration);
    try {
      const result = await this.call("spools/preview_registration", quantity);
      if (revision !== this._previewRevision || this._dialog?.kind !== "new-spool") return;
      this._preview = { ...result, input: JSON.stringify(quantity) };
      this._previewError = null;
    } catch (error) {
      if (revision !== this._previewRevision || this._dialog?.kind !== "new-spool") return;
      this._preview = null;
      this._previewError = error.message || this._t("registration.previewFailed");
    }
    this._paintRegistrationPreview(this._root?.querySelector("form[data-form='new-spool']"));
  }

  _registrationError(error) {
    if (error.code === "idempotency_conflict") return this._t("registration.retryConflict");
    if (error.code === "invalid_input") return this._t("registration.invalidQuantity");
    if (error.code === "DuplicateTagNotConfirmedError") return this._t("registration.tagConflict");
    return error.message || this._t("registration.saveFailed");
  }

  async _submitRegistration() {
    if (this._registrationPending || !this._preview) return;
    const draft = this._dialog.registration;
    const quantity = this._quantityPayload(draft);
    if (JSON.stringify(quantity) !== this._preview.input) return;
    this._registrationPending = true;
    draft.submitted = true;
    this._rememberRegistration();
    this._paintRegistrationPreview(this._root?.querySelector("form[data-form='new-spool']"));
    try {
      const result = await this.call("spools/create", {
        ...quantity, request_id: draft.request_id,
        material: draft.material, material_other: draft.material_other || undefined,
        colour_known: draft.colour_known === "true", colour: draft.colour,
        core_weight_g: draft.core_weight_g === "" ? undefined : Number(draft.core_weight_g),
        opening_condition: draft.opening_condition,
        vendor: draft.vendor || null, label: draft.label || null,
        tag_uid: draft.tag_uid || undefined, confirm_duplicate_tag: draft.confirm_duplicate_tag === "on",
      });
      if (this._dialog.positionEntry) {
        draft.savedSpoolId = result.spool_id;
        this._rememberRegistration();
        this._dialog.savedSpoolId = result.spool_id;
        await this._assignSaved(result.spool_id);
        return result;
      }
      this._clearRegistration();
      this._dialog = null;
      this._error = null;
      await this.refresh();
      return result;
    } catch (error) {
      if (error.code) draft.submitted = false;
      this._rememberRegistration();
      this._error = this._registrationError(error);
      this.render();
    } finally {
      this._registrationPending = false;
      this._paintRegistrationPreview(this._root?.querySelector("form[data-form='new-spool']"));
    }
  }
}
