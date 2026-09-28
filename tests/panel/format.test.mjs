import assert from "node:assert/strict";
import { describe, it } from "node:test";

import "./harness.mjs";

const WWW = new URL("../../custom_components/filament_ledger/www/", import.meta.url);
const { fill, holderWord, hms, round1, signed, typedGrams } = await import(
  new URL("panel/format.js", WWW).href
);
const { spoolRing } = await import(new URL("panel/spool-ring.js", WWW).href);

describe("typed gram fields", () => {
  it("reads blank as zero and a number as itself", () => {
    assert.equal(typedGrams(""), 0);
    assert.equal(typedGrams("12.5"), 12.5);
  });

  it("refuses what is not a non-negative number", () => {
    assert.equal(typedGrams("-1"), null);
    assert.equal(typedGrams("lots"), null);
    assert.equal(typedGrams("Infinity"), null);
  });
});

describe("figures", () => {
  it("rounds to the tenth a movement is known to", () => {
    assert.equal(round1(300 - 10 - 289.9), 0.1);
  });

  it("signs a change with a real minus sign", () => {
    assert.equal(signed(-5), "− 5.0");
    assert.equal(signed(2.25), "+ 2.3");
  });
});

describe("filling a translated template", () => {
  it("fills every occurrence of the token", () => {
    assert.equal(fill("[[a]] and [[a]]", "a", "x"), "x and x");
  });

  it("inserts the value literally, even when it looks like a replacement pattern", () => {
    assert.equal(fill("spool [[name]]", "name", "$& and $`"), "spool $& and $`");
  });
});

describe("HMS codes", () => {
  it("formats a 64-bit decimal string exactly, past the precision of a double", () => {
    assert.equal(hms("18446744073709551615"), "HMS FFFF-FFFF-FFFF-FFFF");
    assert.equal(hms("50348044"), "HMS 0000-0000-0300-400C");
  });

  it("shows anything else as it arrived, never reformatted", () => {
    assert.equal(hms("18446744073709551616"), "18446744073709551616");
    assert.equal(hms(42), "42");
    assert.equal(hms("12ab"), "12ab");
  });
});

describe("naming a holder", () => {
  it("says just 'external' on a machine with one holder", () => {
    assert.equal(holderWord(1, 1), "ams.external");
  });

  it("says left and right on a machine with two", () => {
    assert.equal(holderWord(1, 2), "ams.externalLeft");
    assert.equal(holderWord(2, 2), "ams.externalRight");
  });
});

describe("the spool ring", () => {
  it("clamps the percentage and escapes the colour", () => {
    const empty = spoolRing("card", -20, "#000");
    const full = spoolRing("card", 250, '"><script>');

    assert.match(empty, /stroke-dashoffset="289"/);
    assert.match(full, /stroke-dashoffset="0"/);
    assert.doesNotMatch(full, /<script>/);
  });
});
