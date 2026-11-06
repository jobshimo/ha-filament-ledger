import assert from "node:assert/strict";
import { it } from "node:test";
import { aSpool,panelWith } from "./harness.mjs";

it("only the newest server list replaces results and full AMS inventory remains intact", async () => {
  const all=[aSpool(),aSpool({id:"other"})];
  const panel=panelWith({_spools:all,_inventoryFilters:{search:"first"}});
  const replies=[];
  panel.call=(command,payload)=>new Promise(resolve=>replies.push({resolve,payload}));
  const first=panel._loadInventory();
  panel._inventoryFilters.search="second";
  const second=panel._loadInventory();
  replies[1].resolve([all[1]]);await second;
  replies[0].resolve([all[0]]);await first;
  assert.deepEqual(panel._inventoryResults,[all[1]]);
  assert.equal(panel._spools,all);
  assert.equal(replies[1].payload.search,"second");
});
