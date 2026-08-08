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
  > **Superseded on 2026-08-08.** This conclusion was drawn from a single adjacent row
  > and it is wrong: the event time *is* off-beat, because the gap it can land in is
  > every moment between two republishes. See the 2026-08-08 entry below.
- **The missing-figure branch is load-bearing, not theoretical.** A finish can still
  arrive figureless, and [04 UC-04](04-use-cases.md) step 2 — review, never zero — is
  the branch that catches it. Built alongside the happy path, exactly as
  [13](13-phase-2-brief.md) demanded.

**Verdict: per-tray weights populate at event time; UC-04 built as designed.** The
anonymised capture lives in `tests/fixtures/bambu/print_sensors_finished.json`, and the
translation is fixture-tested rather than believed, same as every other shape here.

> **Half of this verdict was superseded on 2026-08-08.** *Per-tray weights populate* holds
> and the fixture is still the reference shape. *At event time* does not: three hours of
> recorder history showed the figure is readable only between republishes, and never yet
> at the moment a job starts. The entry below has the measurements and what they cost.

---

## 2026-08-08 — The per-tray figure cannot be sampled at an instant

Read out of the recorder database on the live instance, over one day of ordinary printing.
This entry supersedes half of the 2026-08-03 verdict above, and it is the measurement that
turned three silently wrong charges into a fix.

**The sensor is republished in bursts, and each burst is a pair of opposite shapes.**
`sensor.…_peso_de_la_impresion` was rewritten about eight times across the three hours of
one 220-layer print (08:42:43, 08:43:27, 09:29:49, 09:32:53, 10:50:51, 11:18:47, 11:44:13
and neighbours). Within a burst, two consecutive rows land **one to four seconds apart**
with the **state value unchanged** and the attributes disagreeing:

```
A   state: 93.71   attributes: {"AMS 1 Tray 1": 31.33, "AMS 1 Tray 3": 62.38, …}
B   state: 93.71   attributes: {state_class, unit_of_measurement, device_class, friendly_name}
```

Shape B carries **no tray key at all**. It is not a correction and it is not a spool that
emptied — it is the same figure, republished without its breakdown. The one-to-four-second
figure is the gap *inside* a pair, never the cadence between bursts; a reader who confuses
the two will conclude the sensor is nearly always fresh, and it is nearly always stale.

**The start event fires before the sensor knows about the job.** A print that began at
13:49 saw its weight sensor update at **13:49:45**. Nothing was republished between
11:44:17 and that moment. So the attributes standing there when `event_print_started`
arrives describe the *previous* print, in full and plausibly.

**What that cost, in the ledger, on real jobs.**

| Observed | Recorded | Why |
| --- | --- | --- |
| 937-layer print | charged 2.1 g | the start captured the previous job's plan, and the ending had no figure to overrule it |
| 220-layer two-colour print, Tray 1 31.33 g + Tray 3 62.38 g | charged nothing | both captures happened to read shape B |
| job running now, AMS 1 Tray 2 / 248.41 g | recorded tray 1 / 12.24 g | a stale plan, on the wrong tray, pointing at the wrong spool |

**Consequences, now built.** The gateway *follows* the sensor by state change and keeps the
last reading that actually carried tray keys; shape B and an unavailable sensor are
non-observations that never overwrite a good one. A start **discards** the held reading, so
no job can inherit its predecessor's. An ending is charged with the last reading seen during
that job — or, when this process was not alive to watch the job begin (a reload, a restart
mid-print), with a live read of the sensor, which by then has been updated during this job.
An ending whose start *was* watched and which saw no republish reports no usage, and
[04 UC-04](04-use-cases.md) step 2 sends it to review rather than inventing a figure.

**Not used, and still not:** `remain` reads 100 on every tray of this machine, as recorded
at the top of this file. Nothing above is an estimate — the figure is the slicer's plan for
the job, which the owner has accepted as the automatic charge, correctable afterwards
through the void, reassign and adjust paths that already exist.
