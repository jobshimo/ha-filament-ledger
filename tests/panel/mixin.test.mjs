import assert from "node:assert/strict";
import { describe, it } from "node:test";

const WWW = new URL("../../custom_components/filament_ledger/www/", import.meta.url);
const { mixIn } = await import(new URL("panel/mixin.js", WWW).href);

describe("installing a view's methods on the panel", () => {
  it("copies methods and accessors, and they see the panel as `this`", () => {
    class Target {
      constructor() {
        this.name = "panel";
      }
    }
    class Views {
      greet() {
        return `hello from ${this.name}`;
      }
      get shout() {
        return this.name.toUpperCase();
      }
    }

    mixIn(Target, Views);
    const target = new Target();

    assert.equal(target.greet(), "hello from panel");
    assert.equal(target.shout, "PANEL");
  });

  it("leaves the target's own constructor alone", () => {
    class Target {}
    class Views {}

    mixIn(Target, Views);

    assert.equal(Target.prototype.constructor, Target);
  });

  it("refuses to overwrite a method the target already has", () => {
    class Target {
      render() {}
    }
    class Views {
      render() {}
    }

    assert.throws(() => mixIn(Target, Views), /render/);
  });

  it("refuses two views defining the same method", () => {
    class Target {}
    class First {
      card() {}
    }
    class Second {
      card() {}
    }

    assert.throws(() => mixIn(Target, First, Second), /card/);
  });
});
