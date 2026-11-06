import assert from "node:assert/strict";
import { it } from "node:test";
import { panelWith } from "./harness.mjs";

function appearancePanel(stored, dark = false, denied = false) {
  const listeners = new Map();
  const media = {matches:dark, addEventListener:(k,v) => listeners.set("media",v), removeEventListener:() => listeners.delete("media")};
  globalThis.window = {localStorage:{getItem:() => stored,setItem:(_,v) => {if(denied) throw new Error(); stored=v;}},
    matchMedia:() => media, addEventListener:(k,v) => listeners.set(k,v), removeEventListener:k => listeners.delete(k)};
  const panel = panelWith(); const themes = [];
  panel.setAttribute = (_,v) => themes.push(v); panel.style = {};
  panel.render = () => {throw new Error("Theme must not replace task DOM");};
  panel._initAppearance();
  return {panel,media,listeners,themes,stored:() => stored};
}

it("defaults missing or invalid preferences to the device system with a light fallback", () => {
  for(const value of [null,"invalid"]) {
    const {panel,themes} = appearancePanel(value);
    assert.equal(panel._appearance,"auto"); assert.equal(themes.at(-1),"light"); panel._stopAppearance();
  }
});
it("keeps an explicit override across system changes, persists it and tears down listeners", () => {
  const {panel,media,listeners,themes,stored} = appearancePanel("dark",false);
  assert.equal(themes.at(-1),"dark");
  panel._setAppearance("light"); media.matches=true; listeners.get("media")();
  assert.equal(themes.at(-1),"light"); assert.equal(stored(),"light");
  listeners.get("storage")({key:"filament_ledger.appearance",newValue:"auto"});
  assert.equal(themes.at(-1),"dark");
  panel._stopAppearance(); assert.equal(listeners.size,0);
});
it("storage denial still applies this session's choice", () => {
  const {panel,themes} = appearancePanel(null,false,true);
  panel._setAppearance("dark"); assert.equal(themes.at(-1),"dark"); assert.equal(panel._appearanceUnsaved,true);
});

it("uses light when media queries are unavailable and survives denied preference reads",()=>{
 globalThis.window={localStorage:{getItem:()=>{throw new Error("Denied");},setItem:()=>{throw new Error("Denied");}},addEventListener(){},removeEventListener(){}};
 const panel=panelWith();let theme;
 panel.setAttribute=(_,value)=>{theme=value;};panel.style={};
 panel._initAppearance();assert.equal(theme,"light");
 panel._setAppearance("dark");assert.equal(theme,"dark");assert.equal(panel._appearanceUnsaved,true);
 panel._stopAppearance();
});
