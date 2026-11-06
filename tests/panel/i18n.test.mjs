import assert from "node:assert/strict";
import { it } from "node:test";
import { panelWith } from "./harness.mjs";
import {
  LANGUAGE_STORAGE_KEY, SUPPORTED_LANGUAGES, resolveLanguage, translator, writeLanguageOverride,
} from "../../custom_components/filament_ledger/www/i18n.js";

it("uses Turkish profiles, persists a manual choice, and returns to the profile in Auto", () => {
  const original = globalThis.window;
  const storage = new Map();
  globalThis.window = {localStorage: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
  }};
  try {
    assert.ok(SUPPORTED_LANGUAGES.includes("tr"));
    assert.equal(resolveLanguage({locale: {language: "tr-TR"}}), "tr");
    assert.equal(resolveLanguage({language: "tr"}), "tr");
    writeLanguageOverride("tr");
    assert.equal(storage.get(LANGUAGE_STORAGE_KEY), "tr");
    assert.equal(resolveLanguage({locale: {language: "en"}}), "tr");
    const panel = panelWith({_hass: {locale: {language: "en"}}});
    panel._applyLanguage();
    assert.equal(panel.lang, "tr");
    assert.equal(panel._t("tab.inventory"), "Envanter");
    writeLanguageOverride(null);
    assert.equal(resolveLanguage({locale: {language: "es-ES"}}), "es");
    assert.equal(resolveLanguage({locale: {language: "unknown"}}), "en");
  } finally { globalThis.window = original; }
});

it("escapes substituted values in Turkish while retaining intentional markup and automation tokens", () => {
  const t = translator("tr");
  assert.equal(t("dlg.finishTitle", {name: '<img src="x">'}), "&lt;img src=&quot;x&quot;&gt; bitti olarak işaretlensin mi?");
  assert.equal(t("review.total", {grams: 42}), "toplam <b>42</b> g");
  assert.match(t("notifications.message"), /\{\{ spool_name \}\}/);
  assert.equal(t("colour.red"), "Kırmızı");
  assert.match(panelWith({}, "tr").languageCard(), /data-lang="tr"[\s\S]*?Türkçe/);
});
