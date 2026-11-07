import assert from "node:assert/strict";
import {it} from "node:test";
import {panelWith} from "./harness.mjs";

it("saved-but-unassigned retries only mount, retaining the entire destination", async()=>{
 const panel=panelWith({_dialog:{kind:"assignment-recovery",savedSpoolId:"saved",printer:"printer-two",ams:2,slot:1,expectedDestination:null}});
 const calls=[];panel.call=async(type,payload)=>{calls.push({type,payload});};panel.refresh=async()=>{};panel.render=()=>{};
 await panel._assignSaved("saved");
 assert.deepEqual(calls,[{type:"spools/mount",payload:{spool_id:"saved",printer:"printer-two",ams:2,slot:1,expected_source:{kind:"STORAGE"},expected_destination_spool_id:null}}]);
 assert.equal(panel._dialog,null);
});
it("not-reported positions never say confirmed empty",()=>{
 const panel=panelWith({_spools:[],_printer:null});
 assert.match(panel.amsView(),/Not reported/);
 assert.doesNotMatch(panel.amsView(),/>Empty</);
});
