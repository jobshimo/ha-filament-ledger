# 12 — Field Notes

What the reference hardware actually reports, captured from the owner's printer rather than
inferred from documentation. Everything below is a measurement with a date on it.

The point of this document is that [01 §1.6](01-vision.md) is answered with evidence, and
that Phase 2 is written against payloads somebody looked at.

---

## 2026-08-02 — First connection

**Hardware.** Bambu Lab A1 with AMS Lite (reported as `AMS 1`). The serial is written here as
`<SERIAL>`: it identifies one person's machine and adds nothing a reader needs, because every
rule below is about the *shape* of what the printer reports, never about which printer reported
it. Discovery resolves entities through the registry, so no code matches on a serial either.
Home Assistant 2026.7.4 on HAOS. `ha-bambulab` v2.2.22.

**Connection mode: hybrid.** Authenticated against Bambu Cloud, with
*"connect directly to the printer MQTT"* enabled. `sensor.…_modo_de_conexion_mqtt` reads
`local`.

This matters and it was not the first choice. The design originally assumed LAN mode, which
on a Bambu printer **disables the cloud** — and that breaks sending jobs from Bambu Handy and
from Bambu Studio, which is how the owner actually uses the machine. Recommending it was a
mistake: it weighed architectural independence above the owner's daily workflow.

The hybrid mode is better on both counts. Job state and `ams_mapping` arrive over the local
network, so a Bambu outage does not blind us; the cloud session stays alive, so the print
history — which is where the per-tray weights live — remains available. Nothing on the
printer had to be changed.

> **Never ask the user to enable LAN mode on the printer.** It is not a connection setting,
> it is a cloud kill switch.

---

## The founding premise, confirmed

All four trays, including ones visibly used:

```
Tray 1   Bambu PLA Basic   #5E43B7FF   tag 3C45C3DB00000100   remain: 100   remain_enabled: true
Tray 2   Bambu PLA Basic   #00B1B7FF   tag 3CDDA20200000100   remain: 100   remain_enabled: true
Tray 3   Bambu PLA Basic   #000000FF   tag 0000000000000000   remain: 100   remain_enabled: true
Tray 4   Bambu PLA Basic   #FFFFFFFF   tag AC87546600000100   remain: 100   remain_enabled: true
```

**`remain` reads 100 on every tray**, with `remain_enabled` true. The field exists, claims to
be active, and is useless. [01 §1.1](01-vision.md) argued this from documentation and bug
reports; it is now a measurement on the reference machine.

Software accounting is not one option among several. Confirmed.

### Two details worth carrying into Phase 2

**Colour arrives as `RRGGBBAA`** — `#5E43B7FF`. Exactly the storage format
[02 §2.2](02-domain-model.md) chose, so there is no lossy conversion at the boundary. The
leading `#` is present and `Colour.parse` already strips it.

**A spool can have no readable tag.** Tray 3 reports `tag_uid: 0000000000000000` and
`tray_weight: "0"` — a third-party or refilled spool. The tagged trays report their real
figure (`"1000"`), which auto-registration opens the balance with; `"0"` is the tag
declining to say, so it is read as absent and the configured default stands in. Note this
is the reel's weight when **new** — the only thing the tag can know, since `remain` above
is useless and the printer has no scale.

The property belongs to the **spool, not the tray**. A tray is a position; the tag travels
with the reel. Put a Bambu spool in tray 3 tomorrow and it reads fine. This distinction is
not pedantic: automatic recognition follows the spool, so "tray 3 is untagged" would be a
statement that stops being true the moment somebody swaps a reel.

`0000000000000000` must be treated as **absent**, not as a tag value. Sixteen zeros is not an
identity, and matching on it would merge every untagged spool the owner ever buys into one.

---

## Q4 — partially answered, and not yet conclusive

The question: do the per-tray weight figures actually populate?

Captured immediately after connecting, with the previous job already `finish`:

```
sensor.…_peso_de_la_impresion        state: 40.51 g      ← populated
                                     attributes: none    ← no per-tray breakdown
sensor.…_estado_de_la_impresion      state: finish
sensor.…_archivo_gcode_descargado    "381189-Rails for a shelf….gcode"
```

**The total populates.** That is more than the LAN-mode failure reports
([greghesp/ha-bambulab#959](https://github.com/greghesp/ha-bambulab/issues/959)) suggested we
might get, and it comes from the cloud task data rather than an FTP fetch.

**The per-tray breakdown is empty — and that proves nothing yet.** That job finished *before*
the integration existed. `get_print_weights` reads `_ams_print_weights`, which is per-job
state populated while the integration is watching. It never had the chance.

**Q4 stays open.** It closes when a print runs start-to-finish with the integration
connected and the attributes are read again. Until then, no decision that depends on
automatic deduction may be frozen.

*(Closed the next day — see [2026-08-03](#2026-08-03--q4-closed-per-tray-weights-populate) below.)*

---

## Entity names, as actually created

The instance runs in Spanish, so entity ids are localised. Phase 2 must **not** match on
these strings — it resolves entities through the device registry and the integration's own
`unique_id`s, or it breaks for every user who is not running Spanish.

Recorded here as evidence of shape, not as selectors:

| Purpose | Entity on this instance |
|---|---|
| Per-tray filament | `sensor.a1_<serial>_ams_1_bandeja_1` … `_bandeja_4` |
| Per-job weight | `sensor.a1_<serial>_peso_de_la_impresion` |
| Job state | `sensor.a1_<serial>_estado_de_la_impresion` |
| Current / total layer | `sensor.a1_<serial>_capa_actual`, `_cantidad_total_de_capas` |
| Progress | `sensor.a1_<serial>_progreso_de_la_impresion` |
| Sliced file | `sensor.a1_<serial>_archivo_gcode_descargado` |
| Active tray | `sensor.a1_<serial>_bandeja_activa` |
| External spool | `sensor.a1_<serial>_externalspool_bobina_externa` |
| Connection mode | `sensor.a1_<serial>_modo_de_conexion_mqtt` |
| Online | `binary_sensor.a1_<serial>_en_linea` |
| Print error | `binary_sensor.a1_<serial>_error_de_la_impresion` |

Tray attribute shape, verified:

```
active, empty, slot, name, type, color, cols[], filament_id,
tag_uid, tray_uuid, tray_weight, remain, remain_enabled,
nozzle_temp_min, nozzle_temp_max, bed_temp, dry_temp, dry_time, k_value
```

---

## Inventory as loaded

Four spools registered and mounted, one per tray, from the tray data above. Opening weights
are **1000 g placeholders, not measurements** — the owner reconciles each against a kitchen
scale, and that reconciliation is the first real number in the ledger.

Every one starts at `HIGH` confidence, which is correct per [02 §2.6](02-domain-model.md):
the opening balance is a human-confirmed anchor. It degrades as consumption accumulates.

---

## 2026-08-03 — Q4 closed: per-tray weights populate

A print ran start to finish with the integration connected — the condition the
2026-08-02 entry named. Captured off the weight sensor at the moment the job finished:

```
sensor.…_peso_de_la_impresion   state: 296.56                          ← total, grams
                                attributes: {"AMS 1 Tray 4": 296.56}   ← per-tray breakdown
```

**The per-tray breakdown populates, and in exactly the `AMS 1 Tray n` key format the
gateway already translates.** One tray loaded, one key reported, total and tray figure in
agreement. This is the measurement the 2026-08-02 capture could not make: that job had
finished before the integration existed, so `_ams_print_weights` never had the chance to
fill.

**The attributes flicker.** An adjacent recorder row from the same window carried **no
tray key at all** — the same sensor, breakdown present at the event and absent moments
away. Two consequences, both already designed for:

- **Capture at event time, confirmed.** The gateway reads the sensor when the lifecycle
  event fires, not on a poll — a reading taken off-beat can land in a gap and report
  nothing while the figure exists.
- **The missing-figure branch is load-bearing, not theoretical.** A finish can still
  arrive figureless, and [04 UC-04](04-use-cases.md) step 2 — review, never zero — is
  the branch that catches it. Built alongside the happy path, exactly as
  [13](13-phase-2-brief.md) demanded.

**Verdict: per-tray weights populate at event time; UC-04 built as designed.** The
anonymised capture lives in `tests/fixtures/bambu/print_sensors_finished.json`, and the
translation is fixture-tested rather than believed, same as every other shape here.
