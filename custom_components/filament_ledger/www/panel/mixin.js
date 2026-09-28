// How the panel's views join the panel (ADR-0010).
//
// Each tab's rendering lives in its own module as a class whose methods are written exactly
// as they were inside `FilamentLedgerPanel` — same `this`, same calls to each other — and
// `mixIn` installs them on the panel's prototype. Descriptors are copied rather than values,
// so an accessor is installed as an accessor and never run while being copied.
//
// A name the panel or an earlier view already defines is refused, loudly: two views
// answering to one method name would otherwise leave whichever was installed last in charge,
// and nothing on screen would say which.

export function mixIn(target, ...views) {
  for (const view of views) {
    for (const name of Object.getOwnPropertyNames(view.prototype)) {
      if (name === "constructor") continue;
      if (Object.prototype.hasOwnProperty.call(target.prototype, name)) {
        throw new Error(`${view.name}.${name} would overwrite ${target.name}.${name}`);
      }
      Object.defineProperty(
        target.prototype,
        name,
        Object.getOwnPropertyDescriptor(view.prototype, name),
      );
    }
  }
}
