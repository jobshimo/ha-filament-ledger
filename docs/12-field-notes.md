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

---

## 2026-08-08 — The bus event is not delivered when it matters most

The same afternoon, further down the same failure. The per-tray fix above closed the *wrong
figure* hole; this is the *no figure at all* hole underneath it, and it cost 248.41 g.

**What happened.** A seven-hour print started at 13:49:21 UTC and finished. The status sensor
recorded it:

| UTC | `print_status` |
| --- | --- |
| 20:59:14 | `offline` |
| 21:08:13 | `unavailable` |
| 21:12:29 | `finish` |

The ledger never heard. Its row stayed `RUNNING` with `consumption_recorded = 0` until the
integration was restarted hours later.

**Why.** The recorder's `events` table holds every `bambu_lab_event` this instance ever saw,
unfiltered — `recorder:` carries only `purge_keep_days: 60`. The last one that day is
`event_print_started` at 13:49:21. **No terminal event was ever fired.** Upstream's own
source says why (`pybambu/models.py`):

```python
if (
    previous_gcode_state != "unknown"
    and previous_gcode_state != "FINISH"
    and self.gcode_state == "FINISH"
):
    self._client.callback("event_print_finished")
```

`gcode_state` is initialised to `"unknown"`, so a connection that resets across the ending
suppresses the callback. The guard is deliberate — it stops spurious events at startup — and
its cost lands squarely on the one moment this ledger charges money.

**How rare is it.** Not rare, and that is the point. Every one of the 25 closed jobs in the
ledger between 08-04 and 08-08 correlates to a bus event at the same second: 25 endings, 25
events, no exceptions. The mechanism had never failed. What failed was the timing — the
sensor went `unavailable` seven times in those five days, and only this one landed on an
ending. The other six fell overnight with nothing finishing.

**Consequence, now built.** A second, independent ending path reads the status sensor:
`finish` and `failed` only, never `offline` or `pause`, which are facts about the connection
and the machine rather than about the job. Two shapes of it — the *transition*, watched live,
and the *level*, read once by a startup pass for the machine that stopped while nothing was
listening.

**An inferred ending may only close a job, never open one**, and that limit is the whole
safety argument: an idle machine rests in `finish` for hours, and it bounced
`finish → offline → finish` five times in the ten minutes after this print stopped. A path
that trusted the level would mint a phantom print on every restart and every dropout, and
charge the last plan again each time.

**And the second signal must not charge twice.** Both routes report a healthy ending within
the same second; whichever loses the race arrives to find no `RUNNING` row, which is exactly
the shape of the restart-mid-print case that opens a new row. `consumption_recorded` does not
cover it — that flag guards a *row*, and the duplicate is a different row. So an ending is
also refused when the machine's newest job is terminal, carries the same name, and stopped
within five minutes.

## 2026-08-11 — The start is not delivered either, and a print went unrecorded

The mirror of the entry above, found by asking why the ledger had stopped agreeing with the
machine. It had not stopped working. It had never been able to see this case at all.

**What was measured.** At 22:57 local the reference machine was 68 % through a 291.42 g
print — `2585574-0.28mm layer, 2 walls, 15% infill.gcode`, progress climbing once a
minute — and `filament_ledger.db` held **no row for it**. The newest job in the ledger had
ended at 2026-08-10T22:38:59Z. Nothing was open. Nothing was pending.

The recorder says what happened:

```
22:38:28  estado_de_la_impresion  unavailable
22:38:40  estado_de_la_impresion  running
22:46:33  (Home Assistant restarts)
```

A 12.3-second dropout, and the machine returned already printing. No `event_print_started`
was ever fired.

**Why.** `pybambu` guards the start with the same `previous_gcode_state != "unknown"` it
guards the finish with, and a reconnection resets exactly that. Everything the 08-08 entry
says about the ending is true of the start, word for word — it had simply never been looked
for, because a missed ending leaves a visible open row and a missed start leaves nothing at
all.

**And a missed start is the more expensive of the two.** With no `RUNNING` row, the ending —
whenever it arrives, by whichever path — finds nothing to close and is discarded as an
inference about a print already recorded. The whole job disappears: no row, no deduction, no
review, no trace. The `MANUAL_ADJUSTMENT` of −248.41 g dated 2026-08-08 in the reference
ledger is this hole, paid for by hand.

**The scale of the dropouts.** Fourteen `unavailable` windows in seven days on
`estado_de_la_impresion`: mostly 8–13 s, but also 207 s, 256 s, 372 s, 471 s and one of
**4440 s** on 08-08. Any of them can land on a start.

**`offline` is still not one of these.** It remains a fact about the connection: on 08-10 the
machine went `running → offline → running` ten times between 21:11 and 21:47 while printing
perfectly well. It is `unavailable` — the integration itself dropping — that costs edges.

## 2026-08-11 — `start_time` is the job identity upstream never documented

Reading a level tells you a print is running. It does not tell you *which* print, and
without that an inferred start cannot know whether the open row is this job or the last one.

`sensor.…_tiempo_de_inicio` answers it. Measured across both failures in one evening:

```
22:38:28  unavailable
22:38:40  2026-08-11T16:38:23+00:00
22:46:33  2026-08-11T16:38:23+00:00   (after the Home Assistant restart)
```

The value did not move — across a dropout *and* a restart — while the status sensor, both
name sensors and the bus events all reset. Distinct per job, too: the prints before it read
`2026-08-10T22:07:37Z`, `2026-08-10T17:55:05Z`, `2026-08-10T06:32:35Z`.

**It cannot be compared by equality.** Upstream publishes it truncated to the minute and
corrects it seconds later, sometimes by more than a minute:

```
01:04:34  2026-08-09T23:04:00+00:00
01:06:41  2026-08-09T23:06:37+00:00   ← same print
```

So the comparison is a tolerance, and five minutes is both far wider than any correction
observed and far narrower than the eight minutes between upstream's two announced starts for
one job on 08-08 — which must keep resolving to two rows, as they always have.

## 2026-08-11 — Two frozen `translation_key`s were transcribed, not captured

Read from `/config/.storage/core.entity_registry` on the reference instance. The serial
appears **uppercase** in every `unique_id` (`03900D640729564_stage`) and lowercase in the
entity id, which is why a first pass matching on the entity id's spelling found nothing.

| Sensor (localised entity id) | Frozen in code | Actually |
| --- | --- | --- |
| `sensor.…_nombre_del_gcode` | `gcode_name` | **`gcode_file`** |
| `sensor.…_modo_de_conexion_mqtt` | `connection_mode` | **`mqtt_mode`** |
| `binary_sensor.…_en_linea` | `online` | `online` ✓ |
| `sensor.…_bandeja_activa` | `active_tray` | `active_tray` ✓ |

**`gcode_name` cost a shipped fix.** v2.5 added it as `_job_name`'s fallback for the restart
gap and froze a fixture row to match, so the tests agreed with themselves while production
resolved nothing — every restart mid-print still wrote `unknown print`, which is what job
`3e752c9c` in the reference ledger is called. The fixture had been written from the entity
id rather than read from the registry. This is precisely the trap `docs/13 — Traps` names,
sprung inside the module whose own comments warn about it.

`connection_mode` never existed at all, and was still unfrozen — the caution paid off there.

**Two keys worth having, found in the same read.** `stage` carries a 70-option enum
(`printing`, `idle`, `offline`, and the whole `paused_*` family) and is the more specific
answer to *is this machine printing*; `print_status` speaks `gcode_state`'s coarser words.
They go unavailable at different moments, so both are watched and either is enough.
`subtask_name` gives the job name without the `NNNNNN-` prefix, and parks on the literal
`unknown` between prints — which a reader must refuse rather than pass through.

## 2026-08-15 — `start_time` is stale for the first minute of every print

The identity the entry above trusts is **wrong at exactly the moment the inferred start
reads it**. At the instant the stage sensor turns to `printing`, `start_time` has not been
republished yet and still names the print *before*. It corrects itself within a minute — but
the first row of every job was already opened carrying the stale figure.

Read off the reference ledger, six consecutive prints. Each row's stored
`printer_started_at` is the previous print's, exactly:

| Row opened (ledger) | `printer_started_at` stored | Belongs to |
| --- | --- | --- |
| 08-14 21:14:58 | 08-14 15:51:13 | the print before |
| 08-14 21:46:35 | 08-14 21:15:02 | the print before |
| 08-14 22:43:48 | 08-14 21:46:44 | the print before |
| 08-15 01:39:42 | 08-14 22:43:58 | the print before |
| 08-15 09:22:41 | 08-15 01:40:06 | the print before |
| 08-15 17:26:04 | 08-15 14:43:00 | the print before |

The announced start follows 16–56 s later with the corrected value. `_same_print` compared
the two, found *hours* of disagreement, and answered "a different print" — so every print
opened a second row, and v2.6's orphan detection sent the first one to the review queue
carrying the very grams the surviving row went on to deduct automatically.

**The two populations do not overlap.** Age of the open row at the moment the queue was
told, across all twelve `UNCLASSIFIED` reviews this instance has ever opened:

```
16.4  22.5  25.6  32.6  43.6  49.6  52.3  55.9   seconds   ← corrections, all false
17475  (4 h 51 m)                                          ← the one genuine loss
```

Two orders of magnitude, so the row's own age is the discriminator and five minutes sits
between them with 5x and 58x of margin. Widening `JOB_IDENTITY_TOLERANCE` would have had to
reach past four hours and would have blinded the detection that found the real one.

**One review in twelve was true.** Three of the false ones were repeats: a phantom row stays
`RUNNING`, so it keeps answering `_running_job`, and every later print re-detects it and
queues it again — the unique index only blocks a second *pending* card, so resolving one
brings it back on the next print.

**The correction is adopted, not only tolerated.** A row left holding the stale value reports
`printer_ended_at - printer_started_at` as the machine's elapsed time, and a five-hour print
would read as ten. Only a *later* reading is taken: a stale value names an earlier print and
upstream's truncation rounds down, so corrections only ever move forward.

## 2026-09-03 — A print was named after the file before it

Read off the recorder at the start event of a print at 15:17:33Z, with `ha-bambulab`'s
debug log beside it:

| Sensor | At `event_print_started` | Rewritten |
| --- | --- | --- |
| `subtask_name` | `Professional lab_Smart print AMS lite spool adapter PLA_PETG` | about 2 s *before* the event, by the cloud task fetch |
| `gcode_file` | `Professional lab_Smart print AMS lite spool adapter PLA_PETG.3mf` | with the task; reads `unknown` between prints |
| `gcode_file_downloaded` | `696790-P1 -TIE avenger.gcode` — the *previous* print | 15:17:51Z, after the FTP thread parsed the new 3MF |

pybambu fires `event_print_started` synchronously while it processes the MQTT message,
before any entity is refreshed, so at the bus event every sensor still holds the previous
message's values; `subtask_name` is current only because it was written two seconds
earlier. `_job_name` preferred the downloaded file, so every job was stored under the name
of the print before it. `subtask_name` sometimes arrives in the `Project/Name` slash form.

The numeric prefix on the downloaded-file sensor (`696790-`) is the cached 3MF's byte
size, not a cloud task id as `display_job_name`'s docstring claimed; the stripping is
unchanged.
