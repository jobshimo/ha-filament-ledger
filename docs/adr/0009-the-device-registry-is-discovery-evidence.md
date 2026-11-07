# ADR-0009 — The device registry is discovery evidence

**Status:** Accepted
**Date:** 2026-09-23
**Amends:** [05 §5.8](../05-ha-integration.md) — the permitted-surface table and the AMS
attribution and ordinal bullets

## Context

`BambuLabGateway` has to answer three questions about every AMS unit and every spool holder
it finds in the entity registry: **which machine is this on**, **which unit is it**, and —
since a dual-nozzle printer has two holders — **which holder is it**.

Until now only the first had an answer, and that answer was a substring test. The tray
sensors hang off the AMS device rather than the printer, and their `unique_id` reads
`A1_<printer serial>_AMS_<ams serial>_tray_1`: the printer's serial is in there, behind a
model prefix whose boundary is written down nowhere. v1 refused to *parse* that string and
was right to; it asked instead whether a serial the job sensors had already resolved
**appears in** it, which needs no format to be true.

The second question had no answer at all. The `unique_id` carries the AMS unit's own serial
and never its ordinal, so the gateway followed **one AMS per printer** — the first group by
identity — and dropped the rest with a warning. A household with two units had four trays it
could mount into and four it could not, and every figure the printer reported under
`AMS 2 Tray n` was dropped rather than charged.

Three alternatives were available and each was rejected on the evidence:

- **Parse the ordinal out of the `unique_id`.** It is not in there. What is in there is the
  AMS unit's serial, and no arrangement of string surgery turns one into the other.
- **Infer the ordinal from the weight sensor's attribute keys.** `AMS 2 Tray 1` states an
  ordinal, but only while a print is drawing from it. Discovery runs at setup, on an idle
  machine, and a tray space that appeared only during a print would be a tray space nobody
  could mount into.
- **Sort the AMS serials and number them.** A coin toss with a plausible shape: the order of
  two opaque serials has no relationship to the order a user sees the units in on the bench,
  and being wrong means mounting somebody's spool into the other unit.

Meanwhile Home Assistant's own device registry already holds every answer. Read from the live
host on 2026-09-23 ([12](../12-field-notes.md)): upstream's `coordinator.get_ams_device`
writes `name = f"{device_type}_{serial}_AMS_{index+1}"` with
`identifiers={(DOMAIN, ams_serial)}` and `via_device=(DOMAIN, printer_serial)`, and
`get_virtual_tray_device` writes `identifiers={(DOMAIN, f"{serial}_ExternalSpool{suffix}")}`
with the same `via_device`. The live `core.device_registry` confirms `via_device_id` on both
machines' AMS devices and on both of the X2D's holders.

## Decision

**The device registry is a permitted evidence source, and it is consulted first. The
`unique_id` rules stay as the fallback and are unchanged.**

For every `tray` and `external_spool` entity, discovery resolves through
`homeassistant.helpers.device_registry`:

- **Attribution** — the device's `via_device_id` leads to the printer's device, whose
  `(bambu_lab, …)` identifier *is* the printer's serial.
- **The AMS ordinal** — the trailing `_AMS_<n>` of `device.name`.
- **The holder index** — the `_ExternalSpool<suffix>` tail of the device's own identifier,
  with the empty suffix meaning the first holder.

**`device.name`, never `name_by_user`.** Home Assistant writes a household's rename into
`name_by_user` and leaves `name` exactly as the integration wrote it, so a user who calls
their unit *Downstairs AMS* cannot renumber their own trays. The one place a rename *should*
win is the machine's display name, and that reader prefers `name_by_user` deliberately.

**The registry's attribution is accepted only for a machine this ledger already follows.** A
`via_device` chain can name a printer whose own job sensors resolved no serial, and such a
machine is deliberately not followed ([05 §5.8](../05-ha-integration.md)); taking the name
here would smuggle it back in under a serial no job sensor agrees with.

**Every fallback that exists today survives.** No device entry, or a name that does not end
`_AMS_<digits>`, falls through to the `unique_id` rules: with one printer every AMS is its,
with none they take the reserved `UNIDENTIFIED` serial, and with several an AMS naming no
discovered machine is dropped with a warning. Where the registry numbers none of a machine's
units, the first by identity is followed as AMS 1 — v1's behaviour, verbatim — and the rest
are named in a warning.

## Rationale

**It is a first-party helper, not an upstream internal.** [05 §5.8](../05-ha-integration.md)
draws the boundary at `custom_components.bambu_lab`: importing its coordinator, reading its
config entry, reaching into `get_model()`. `homeassistant.helpers.device_registry` is none of
those. It is the same kind of surface as `entity_registry`, which discovery has read since
v1, and it carries the same compatibility promise Home Assistant makes to every integration.

**§5.8 already said so.** The localisation bullet has read *"Resolve through the device
registry and upstream's `unique_id`s instead"* since it was written. This ADR is that
sentence finally being acted on, not a new permission.

**The shapes are read, not assumed.** Every one of them was captured off two live machines
before it was frozen, which is the discipline that produced `PRINT_SENSOR_KEYS` and the same
discipline that caught `gcode_name` being a key that does not exist
([13 — Traps](../13-phase-2-brief.md)).

**A fallback is cheaper than a crash.** Every reader here answers `None` for a shape it does
not recognise, and `None` routes to the rule that was already there. An upstream that
renames its devices tomorrow costs this ledger exactly the precision the registry was adding
— a household back to one followed AMS per machine — and costs it nothing else.

## Consequences

- Every AMS unit a machine has is followed, and its trays are mountable. `TRACKED_AMS` — the
  constant that said *one unit per printer* — is replaced by `FIRST_AMS`, which now means
  only *the ordinal a caller that names none is resolved to*.
- Both holders of a dual-nozzle printer are discovered, which is what makes
  `HolderIndex` resolvable from the outside rather than only representable inside
  ([02 §2.2](../02-domain-model.md)).
- The permitted-surface table in [05 §5.8](../05-ha-integration.md) gains a device-topology
  row, and the AMS attribution and ordinal bullets are rewritten.
- A test harness now plants a device registry as well as an entity registry. Most of the
  suite deliberately plants **no** device registry, because that is the shape every fallback
  exists for and a suite that always planted one would stop exercising them.
- An AMS HT reports an ordinal of 128 and up. `AmsIndex` has no ceiling, so its trays are
  followed; upstream's sixteen-slot weight loop never names it, so it reports no consumption
  and a print fed from one opens a review rather than being charged an invented figure.
