// Pure helpers the panel's views share: gram figures, typed input, translated templates,
// HMS codes, the holder word, and the one wire sentinel they compare against.
//
// No DOM and no state — every function here is a value in, a string or number out — which is
// what lets tests/panel/format.test.mjs call them directly (ADR-0010).

/**
 * The reserved serial a location carries when the ledger never recorded which machine it
 * meant (`domain/value/identifiers.py`).
 *
 * Mirrored here because the panel has to *label* it — a heading reading `UNIDENTIFIED` over
 * somebody's spools is a code constant leaking onto a screen, and the sentence that belongs
 * there instead is in `i18n.js` like every other. Nothing is ever *sent* as this value: an
 * absent printer travels as an absent field, and the backend resolves it in the one place
 * that owns the sentinel.
 */
const UNIDENTIFIED_PRINTER = "UNIDENTIFIED";

const grams = (value) => `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 })} g`;
const signed = (value) => `${value < 0 ? "−" : "+"} ${Math.abs(value).toFixed(1)}`;

/**
 * Round to the tenth, which is the precision a single movement is known to.
 *
 * Every gram figure the review card compares goes through this first. Binary floating
 * point makes 300 − 10 − 289.9 a hair away from 0.1, and a remainder that reads `0.0 g`
 * while the Approve button stays disabled is a card calling the user a liar.
 */
const round1 = (value) => Math.round(value * 10) / 10;

/** A typed gram field as a number, or `null` when it is not one. Blank reads as zero. */
const typedGrams = (raw) => {
  const value = raw === "" ? 0 : Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
};

/**
 * Which string names one direct feed, given how many the machine has.
 *
 * With one holder the answer is *External spool*, exactly what every card and every
 * label has said since v2.8 — a machine with one holder has no second position to be
 * told apart from. With two, that phrase names neither, so they become left and right:
 * the reader is standing at the machine looking at two holders, and upstream's own
 * indexes (255 and 254) would mean nothing to them.
 *
 * Stated once because three surfaces ask it — the AMS card, the Printer tab's active
 * position, and the review card's row — and three copies is three chances to disagree.
 */
const holderWord = (holder, holderCount) => {
  if (holderCount < 2) return "ams.external";
  return holder === 2 ? "ams.externalRight" : "ams.externalLeft";
};

/**
 * Put an **already-safe** fragment into a `[[token]]` slot of a translated string.
 *
 * `t()` escapes every parameter it substitutes, which is exactly right for raw wire data
 * and exactly wrong for a value that has already been escaped or is deliberately markup —
 * a second pass would print `&amp;` inside somebody's spool name. This is the other door.
 *
 * The replacement is a *function* on purpose: `String.replace` reads `$&`, `` $` `` and
 * friends in a replacement **string** as back-references, and a backtick is not one of
 * the characters `esc()` neutralises. A replacer function has no such syntax.
 *
 * Global, like `t`'s own substitution: a template may name the same value twice, and
 * filling only the first would leave a visible `[[token]]` behind.
 */
const fill = (template, token, value) =>
  template.replace(new RegExp(`\\[\\[${token}\\]\\]`, "g"), () => value);

/**
 * The verbatim `print_error` as the searchable HMS quad — AABB-CCDD-EEFF-GGHH, sixteen
 * hex digits zero-padded from the 64-bit value. HMS codes are searchable; the user
 * diagnosing a failure needs the real string (docs/06 §6.3). The code arrives as a
 * DECIMAL STRING: HMS codes are 64-bit, a JSON number lands in JS as a double, and any
 * value past 2^53 would already be corrupted before BigInt could see it — BigInt(string)
 * is exact at any magnitude. Formatting is display work: the exact decimal string stays
 * untouched in a title attribute. Anything that is not a plain decimal string within 64
 * bits renders as-is — never reformatted, never invented.
 */
function hms(code) {
  if (typeof code !== "string" || !/^[0-9]+$/.test(code)) return String(code);
  const hex = BigInt(code).toString(16).toUpperCase();
  if (hex.length > 16) return code;
  const quad = hex.padStart(16, "0");
  return `HMS ${quad.slice(0, 4)}-${quad.slice(4, 8)}-${quad.slice(8, 12)}-${quad.slice(12, 16)}`;
}

export { UNIDENTIFIED_PRINTER, fill, grams, holderWord, hms, round1, signed, typedGrams };
