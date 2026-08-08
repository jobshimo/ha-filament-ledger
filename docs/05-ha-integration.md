# 05 — Home Assistant Integration

How the system surfaces inside Home Assistant. This is an adapter layer: everything here
translates between HA concepts and the application layer, and contains no business rules.

---

## 5.1 Integration identity

```
domain:        filament_ledger
name:          Filament Ledger
iot_class:     local_push
config_flow:   true
dependencies:  ["websocket_api", "http"]
after:         ["bambu_lab"]
```

`ha-bambulab` is listed under `after`, not `dependencies`. The integration must load and
function for pure inventory work even if the printer integration is absent or broken —
constraint C4.

## 5.2 Config flow

**Step 1 — Printer.** No step. Every Bambu Lab machine `ha-bambulab` describes is discovered
at setup and followed (§5.8); a household with none gets *manual inventory only*, which is a
fully supported mode rather than a degraded one. **The flow asks nothing here on purpose**: a
picker would make the owner name the machines they already own before the integration told
them anything, and it would have to be revisited every time one is added. Reloading the entry
after a printer appears re-runs discovery, which is the whole of the maintenance.

**Step 2 — Defaults.** Default spool opening weight (1000 g), default core weight (measured
per vendor; Bambu spools ≈ 250 g), and preferred units.

**Step 3 — Behaviour.** Auto-mount on RFID detection (default on). Anomaly threshold (default
15%). Confidence thresholds.

**Options flow** — everything from steps 2 and 3 is editable afterwards. A setting that can
only be chosen during installation is a setting the user will get wrong once and live with.

## 5.3 Entities

Entities exist so that **automations, notifications, history, and voice assistants work
without this project building any of them**. That is the whole argument for a custom
integration over a standalone add-on — see [ADR-0003](adr/0003-custom-integration-over-addon.md).

### Per spool

| Entity | Type | State | Key attributes |
|---|---|---|---|
| `sensor.fl_spool_<slug>` | sensor | grams remaining | `percentage`, `material`, `colour_hex`, `vendor`, `location`, `confidence`, `state`, `opening_weight`, `last_movement_at`, `has_anomaly` |

`device_class: weight`, `unit_of_measurement: g`, `state_class: measurement`.

The state is the balance because that is the number the user cares about. Everything else is
an attribute.

### Per AMS slot

| Entity | Type | State | Key attributes |
|---|---|---|---|
| `sensor.fl_slot_<n>` | sensor | spool label, or `empty` | `spool_id`, `material`, `colour_hex`, `remaining_g`, `percentage`, `confidence`, `tag_uid`, `is_stale` |

`is_stale` is true when the printer is unreachable and the reading is a last-known value. A
stale reading is shown as stale — never silently presented as current.

### Aggregate

| Entity | Type | State | Purpose |
|---|---|---|---|
| `sensor.fl_pending_reviews` | sensor | count | Drives the badge and notifications |
| `sensor.fl_total_stock` | sensor | total grams | Attributes break it down per material |
| `sensor.fl_spools_low` | sensor | count below threshold | Restocking |
| `binary_sensor.fl_anomaly` | binary_sensor | on/off | `problem` device class; attributes list affected spools |
| `binary_sensor.fl_printer_connected` | binary_sensor | on/off | `connectivity` device class |

### Device grouping

One HA device per physical spool, plus one device representing the ledger itself. Spool
devices carry the material and vendor as model information, so the HA device page is already
a usable spool detail view before any custom UI exists.

## 5.4 Services

Every service maps one-to-one to a use case. No service performs two operations, and no use
case is reachable through two services.

```yaml
filament_ledger.register_spool:
  fields:
    material:        {required: true,  selector: {select: {options: [PLA, PETG, ABS, ASA, TPU, PC, PA, PVA, OTHER]}}}
    colour:          {required: true,  selector: {text: {}}}          # RRGGBB or RRGGBBAA
    vendor:          {required: false, selector: {text: {}}}
    opening_weight:  {required: true,  selector: {number: {min: 1, max: 10000, unit_of_measurement: g}}}
    core_weight:     {required: false, selector: {number: {min: 0, max: 2000, unit_of_measurement: g}}}
    label:           {required: false, selector: {text: {}}}
    tag_uid:         {required: false, selector: {text: {}}}
    confirm_duplicate_tag: {required: false, selector: {boolean: {}}, default: false}

filament_ledger.mount_spool:
  fields:
    spool_id: {required: true,  selector: {text: {}}}
    printer:  {required: false, selector: {text: {}}}      # absent → the one printer followed; refused when several are
    ams:      {required: false, selector: {number: {min: 1}}}   # absent → AMS 1
    slot:     {required: true,  selector: {number: {min: 1, max: 4}}}

filament_ledger.unmount_spool:
  fields:
    spool_id: {required: true}

filament_ledger.approve_review:
  fields:
    review_id: {required: true}
    amounts:   {required: false, selector: {object: {}}}   # [{printer, ams, slot, amount_g}]  — overrides estimate
    assign:    {required: false, selector: {object: {}}}   # [{printer, ams, slot, spool_id}]  — one spool takes the tray whole
    charges:   {required: false, selector: {object: {}}}   # [{printer, ams, slot, charges: [{spool_id, amount_g}]}]
    note:      {required: false}

filament_ledger.dismiss_review:
  fields:
    review_id: {required: true}
    note:      {required: false}

filament_ledger.reconcile_spool:
  fields:
    spool_id:      {required: true}
    measured_g:    {required: true,  selector: {number: {min: 0, max: 10000, unit_of_measurement: g}}}
    includes_core: {required: false, selector: {boolean: {}}, default: true}

filament_ledger.discard_filament:
  fields:
    spool_id: {required: true}
    mode:     {required: true, selector: {select: {options: [whole_spool, partial]}}}
    amount_g: {required: false, selector: {number: {min: 0, max: 10000, unit_of_measurement: g}}}
    reason:   {required: true, selector: {text: {}}}

filament_ledger.adjust_spool:
  fields:
    spool_id: {required: true}
    amount_g: {required: true, selector: {number: {min: -10000, max: 10000, unit_of_measurement: g}}}
    reason:   {required: true, selector: {text: {}}}
```

`reason` is required on discard and adjust, matching UC-09 and UC-10. The requirement is
enforced in the domain as well — a service definition is a UI convenience, not a guarantee.

`core_weight` is optional **in the service**, not in the domain. When it is omitted the
service layer substitutes the configured default from §5.2 before calling
[UC-01](04-use-cases.md); the use case itself refuses a missing value. The substitution
happens in exactly one place, and the SQLite column carries no default of its own
([08 §8.1](08-data-model.md)) so a bug in that path fails loudly instead of quietly writing a
zero into the arithmetic behind every future reconciliation.

`approve_review` takes `amounts`, `assign` and `charges` as **lists of per-tray entries**,
each naming its tray with `printer`, `ams` and `slot` ([02 §2.3](02-domain-model.md)). They
were objects keyed by the tray number until a tray needed three parts to identify it and a
JSON key could only hold one. A tray named twice in one payload is refused rather than
resolved by keeping the last: two answers to one question are not a decision.

`assign` gives a tray whole to the spool it names, which is how a caller resolves a tray the
review recorded as having consumed filament with no spool mounted in it. `charges` states the
split for a tray that fed from more than one spool — a spool that emptied mid-print and was
replaced in the same tray — and each tray's charges must add up to what that tray confirms. A
tray may appear in one of the two, never in both.

**`printer` and `ams` are optional on every tray a caller names**, here and on `mount_spool`,
and their absence means *the tray space this ledger follows*. An automation written before a
tray had three parts therefore keeps working **while there is one machine for it to mean**,
which is right: naming a serial for the only printer in the house would be ceremony rather
than precision. The absence is resolved by the runtime — the machine the gateway discovered —
never by a bare placeholder, because a placeholder would open a *second* tray space in which
every slot looked free ([08 §8.4](08-data-model.md)).

**Amended (v2.0): with more than one machine followed, the absence is refused rather than
resolved.** There is no such thing as *the* printer then, and every rule that would pick one —
the first by serial, the one that printed last, the one with the most trays — is a coin toss
with somebody's spool on the other end. The caller gets a message naming the machines it could
have meant, and an automation written against v1 needs one field added exactly once. The panel
never hits this: the AMS section a mount button was drawn in already knows which machine's
tray was tapped, and sends it ([06 §6.4](06-ui-spec.md)).

## 5.5 Events

Domain events, bridged onto the HA event bus with a `filament_ledger_` prefix. The domain
raises them without knowing HA exists; `event_bridge.py` performs the translation.

| HA event | Payload |
|---|---|
| `filament_ledger_review_opened` | `review_id`, `job_name`, `reason`, `estimated_total_g`, `slots` |
| `filament_ledger_review_resolved` | `review_id`, `resolution`, `applied_total_g` |
| `filament_ledger_movement_recorded` | `spool_id`, `type`, `amount_g`, `new_balance_g` |
| `filament_ledger_spool_depleted` | `spool_id`, `label` |
| `filament_ledger_confidence_degraded` | `spool_id`, `from`, `to` |
| `filament_ledger_anomaly_detected` | `spool_id`, `kind`, `detail` |
| `filament_ledger_unknown_spool_detected` | `tag_uid`, `printer`, `ams`, `slot`, `material`, `colour_hex` |
| `filament_ledger_ambiguous_tag_detected` | `tag_uid`, `printer`, `ams`, `slot`, `candidate_spool_ids` |

Every tray event carries `printer` and `ams` beside `slot`. `slot` keeps its name and its
meaning, so an automation matching on it goes on matching; the two that joined it are what
stop a tray number from ceasing to identify a tray the moment a second machine exists.

`slots` is a list of `{printer, ams, slot, estimated_g, spool_id}`, where `spool_id` is
**null** for a tray the review could not attribute. It is keyed by tray rather than by spool
because a review can legitimately contain a tray with no spool at all
([02 §2.3](02-domain-model.md)) — a payload listing spools could not carry that row, which is
the row most worth notifying about.

These make the interesting automations trivial for the user to write:

```yaml
# Notify when a print is cancelled and needs review
trigger:
  platform: event
  event_type: filament_ledger_review_opened
action:
  service: notify.mobile_app
  data:
    title: "Print cancelled — review needed"
    message: >
      {{ trigger.event.data.job_name }} used approximately
      {{ trigger.event.data.estimated_total_g }} g. Confirm the amount.
    data:
      actions:
        - action: "FL_APPROVE_{{ trigger.event.data.review_id }}"
          title: "Approve estimate"
```

## 5.6 WebSocket API

The panel's only channel to the backend. Read models are served here rather than assembled
from entity attributes, because entity state is a presentation surface, not a query API.

```
filament_ledger/spools/list          → SpoolOverview[]     (UC-11)
filament_ledger/spools/get           → SpoolDetail         (UC-12 — history included)
filament_ledger/spools/create        → SpoolId             (UC-01)
filament_ledger/spools/update        → ok                  (metadata only — never balance)
filament_ledger/reviews/list         → PendingReview[]
filament_ledger/reviews/approve      → ok                  (UC-06)
filament_ledger/reviews/dismiss      → ok                  (UC-07)
filament_ledger/spools/reconcile     → NewBalance          (UC-08)
filament_ledger/spools/discard       → ok                  (UC-09)
filament_ledger/spools/adjust        → ok                  (UC-10)
filament_ledger/movements            → MovementRow[]       (UC-12 across all spools, newest first)
filament_ledger/statistics           → Statistics          (one period's totals — 06 §6.7)
filament_ledger/trays/sync           → TraySyncOutcome     (the startup reconciliation pass, on demand)
filament_ledger/slots/state          → SlotState[]
filament_ledger/subscribe            → live event stream
```

*Per-spool* movement history is not a separate command. `spools/get` returns the full
`SpoolDetail`, history included, because the panel always shows the two together — a second
round-trip would buy latency and nothing else. `movements` is the different question the
History view asks: the newest entries across **every** spool, each joined to its spool's
name and colour and — when `job_id` is set — its print job's name. It carries no running
balances, because no balance is derivable from a cross-spool slice.

`movements` also takes the History view's filters. Every one is optional and independent of
the others:

```
since     ISO-8601, with an offset   entries at or after this instant
until     ISO-8601, with an offset   entries at or before this instant
colours   ["RRGGBB", …]              entries of spools wearing any of these
min_g     number ≥ 0                 entries weighing at least this
max_g     number ≥ 0                 entries weighing at most this
search    string                     free text over the entry's note and job name
```

**They are applied server-side, in SQL** — the rule `statistics` follows, for a stronger
reason: a period's totals are bounded and a history is not. `limit` caps what matched, never
what would have matched had the filters run afterwards.

The offset is required rather than assumed. A bound without one names a wall clock and the
ledger stores instants, so guessing the host's timezone would make one saved filter mean two
different windows on two installations restored from the same backup — and the browser knows
its own offset, so there is nothing to guess. `min_g` and `max_g` are **magnitudes**, because
amounts are signed and a print consumption of −84.1 g is exactly what somebody asking for
entries over 50 g means. `search` covers the note and the print job's name and nothing else:
the movement label is generated and translated in the panel, and the spool's name is a column
of its own with `colours` to narrow it ([04 UC-12](04-use-cases.md)).

Sending none of these keys is the whole history, which is what *clear all* does — the control
removes the payload rather than adding a command. Nothing is written, so like `statistics` the
command does not refresh the coordinator.

`statistics` takes an optional `period` — `30d`, `90d` or `all`, defaulting to `30d` — and
answers with one period's finished figures: grams printed and wasted, print and review
outcomes, consumption grouped by colour and by material, the biggest prints, and measured
print time. **The window is applied server-side**, so the panel never receives the ledger
and never re-implements the visibility rules of [14 §14.4.5](14-corrections-and-trash.md)
in the one layer that has no tests. Nothing is written, so the command does not refresh the
coordinator: looking at a page changes nothing.

`trays/sync` re-runs the same pass `async_setup_entry` runs at startup — `DetectSpool` once
per tray the gateway currently sees — and reports a per-slot outcome: `empty`, `mounted`,
`detected` (auto-mount off), `unknown_tag`, `ambiguous_tag` or `no_tag`, with the tag and
the tray's name/material/colour hints so the panel's register form can pre-fill. A dormant
gateway answers `{dormant: true, slots: []}` — the honest no-printer flag, not four
invented empty slots. The `filament_ledger.sync_trays` service runs the same pass
fire-and-forget.

`spools/update` edits metadata — label, vendor, colour, material, core weight — and **cannot
alter a balance**. There is no endpoint that sets a balance directly. Changing a balance
requires a movement, and that is the whole design. An API that could set a balance would make
the ledger decorative.

## 5.7 Panel registration

Registered as a sidebar panel:

```python
async_register_built_in_panel(
    hass,
    component_name="custom",
    sidebar_title="Filament",
    sidebar_icon="mdi:printer-3d-nozzle",
    frontend_url_path="filament-ledger",
    config={"_panel_custom": {"name": "filament-ledger-panel", "module_url": ...}},
    require_admin=False,
)
```

A badge on the sidebar icon reflects `sensor.fl_pending_reviews`, so pending approvals are
visible without opening the panel. The queue only works if the user knows it has items.

## 5.8 The `ha-bambulab` contract

`BambuLabGateway` is the only place in this project that touches another integration, so what
it is allowed to touch is written down rather than discovered during implementation.

**Permitted — the public surface.**

| What | How |
|---|---|
| Job lifecycle | `bambu_lab_event` on the HA bus, `type` ∈ `event_print_started`, `event_print_finished`, `event_print_canceled`, `event_print_failed`, `event_print_error` |
| Per-tray consumption | Attributes of the printer's `print_weight` sensor, keyed `AMS <n> Tray <m>` and `External Spool` |
| Job progress | The `print_progress`, `current_layer` and `total_layers` sensors |
| Job timing | The `remaining_time` sensor (whole minutes) and the `start_time` / `end_time` timestamp sensors |
| Raw state | The `print_status` sensor (a lowercased `gcode_state`) and the `print_error` sensor |
| Tray identity | Attributes of the AMS tray sensors: `tag_uid`, `type`, `color`, `tray_uuid`, `remain` |
| Connectivity | The printer device's availability |

Read through `async_track_state_change_event` and `hass.bus.async_listen`. Nothing else.

**Forbidden.** Importing anything from `custom_components.bambu_lab`, reaching into
`coordinator.get_model()`, or reading its config entry data. Those are internals. They change
without notice, they are not covered by any compatibility promise, and depending on them turns
an upstream refactor into a corrupted ledger.

**Consequences that have to be designed for, not discovered.**

- The per-tray attribute keys are **strings in an attribute dictionary**, with no schema and
  no version. Parsing them is a boundary concern and it is fixture-tested against payloads
  captured from a real printer ([09 §9.4](09-testing-strategy.md)) rather than against
  payloads someone believed were correct.
- **That sensor cannot be sampled at an instant, so the gateway tracks it.** Measured on the
  reference machine: upstream republishes it in occasional bursts — about eight across the
  three hours of one print — and each burst is a *pair* of rows one to four seconds apart,
  the state value unchanged, one carrying the breakdown and one carrying no tray key at all;
  and it first updates about three-quarters of a minute *after* the start event fires. The gateway follows the sensor by state change and keeps the last reading that
  actually carried tray keys, discarding it when a print starts so no job inherits the
  previous one's figures. A shape without tray keys is a non-observation and never
  overwrites a real one.
- Tray numbering is *ours* to define. `AMS 1 Tray 1` maps to the tray reference for tray 1
  of AMS 1 **on the machine whose event is being translated**. An `External Spool` figure is
  still dropped with a warning: a spool on the direct feed now has a location that names its
  machine ([02 §2.2](02-domain-model.md)), which is a different question from a consumption
  figure having a tray to be deducted through. The translation lives in the gateway and
  nowhere else.
- **The printer's serial is read off the job sensors' `unique_id`s**, which upstream writes
  as `<serial>_<translation_key>` — so removing the key that matched leaves the serial. That
  is the stable identity `TrayRef` needs ([02 §2.3](02-domain-model.md)), taken from a shape
  captured on the reference instance rather than from a `translation_key` nobody has
  confirmed. The tray sensors' `unique_id`s carry the serial too, behind a model prefix whose
  boundary is written down nowhere, so they are not where it is read from; an AMS whose
  printer sensors did not resolve reports its trays under the reserved `UNIDENTIFIED` serial,
  which is exactly what such a ledger's rows carry.
- **The AMS ordinal comes from the weight attributes, not from the registry.** A tray's
  `unique_id` carries the AMS unit's *serial*, and the only place an ordinal is ever stated is
  the `AMS 1 Tray n` attribute keys. v1 dropped every ordinal but 1 with a warning; the
  gateway names that ordinal instead of leaving it implicit, and still follows **one unit per
  printer** — where a machine's entries describe several, the first by identity wins and a
  warning names the rest.
- **Every machine is followed, since v2.0.** Discovery groups the job sensors by the device
  they hang off, reads each device's serial off a `unique_id`, and builds one printer per
  device. Their trays, their job sensors and their device ids are kept apart, and every tray
  reference carries the serial of the machine that holds it — so two printers' trays live in
  one mapping without colliding and nothing has to decide which machine a tray 1 is on.
- **A tray is attributed to the machine its own `unique_id` mentions.** The tray sensors hang
  off the AMS device rather than the printer, and their `unique_id` reads
  `A1_<serial>_AMS_<ams serial>_tray_1` — the printer's serial is in there, behind a model
  prefix whose boundary is written down nowhere. The gateway does not *parse* it: it asks
  whether a serial the job sensors already resolved **appears in** the string, which needs no
  format to be true and is checked against the same frozen fixture. With one printer every AMS
  is its, without consulting the string at all; with none they take the reserved
  `UNIDENTIFIED` serial, which is exactly what such a ledger's rows carry; with several, an
  AMS naming no discovered machine is dropped with a warning rather than assigned, because
  attributing it would put somebody's spool in a printer it is not in.
- **A machine has to be nameable to be followed.** `UNIDENTIFIED` denotes *the one machine
  this ledger has always talked to*, so it is handed to a printer whose serial did not resolve
  only when that printer is the only one there is. With several, an unnamed machine is passed
  over — two live machines answering to one name would share a single tray space and collide
  slot for slot — and it is counted on the Printer tab rather than only logged
  ([14 §14.5](14-corrections-and-trash.md)). No machine this project has evidence of writes a
  `unique_id` without its serial, which is why the tab asks for a report.
- **A job event is resolved to its machine by device id, and the serial travels inward.** The
  bus carries `bambu_lab_event` for the whole house and the payload names only a device, so
  discovery's device-to-serial map is what says who spoke. Every figure on the translated
  event is then read from *that* machine's sensors, and the event carries the serial because
  the use case receiving it correlates an ending against the running job of that machine and
  cannot recover from anywhere else which one it was ([02 §2.3](02-domain-model.md)).
- Every one of these values can be absent. The gateway returns "unknown", never a zero. See
  [03 §3.8](03-architecture.md).
- **Entity ids are localised.** On a Spanish instance the tray sensor is
  `sensor.a1_<serial>_ams_1_bandeja_1`, not `..._ams_1_tray_1`. Matching on entity id strings
  breaks for every user not running the language the developer happened to use. Resolve
  through the device registry and upstream's `unique_id`s instead. Real names captured in
  [12 — Field Notes](12-field-notes.md).
- **`tag_uid: "0000000000000000"` means absent, not a tag.** A third-party or refilled spool
  reports sixteen zeros. Treating that as an identity would merge every untagged spool the
  user owns into one — and the identity belongs to the *spool*, which moves between trays,
  not to the tray.
- **The printer keeps its own clock, and it is not this one.** `start_time` and `end_time`
  are the machine's answer to when a print ran, and they are a better measure of a print's
  duration than the moments Home Assistant processed the lifecycle events — a restart or a
  busy bus lands in the second pair and none of it happened to the print. They are stored
  in `print_job.printer_started_at` / `printer_ended_at`, **beside** the ledger's own two
  and never over them. UC-04 derives every consumption movement's `occurred_at` from
  `ended_at`, and the ledger orders itself by `occurred_at`
  ([04 UC-04](04-use-cases.md)); a foreign clock reaching that column would reorder prints
  against entries this integration stamped itself. The two printer columns are read by one
  subtraction and by nothing else, so no ordering can see them.
- **`end_time` is a prediction until the print ends.** Upstream computes it from the time
  remaining, so a job cancelled forty minutes in still reports the end it was heading for.
  At a *finish* the prediction has converged on the present and is a measurement; anywhere
  else it is not one, and only a `FINISHED` job's pair is used as a duration
  ([06 §6.7](06-ui-spec.md)). Both values are recorded whatever the outcome, because the
  job record keeps the printer's claims verbatim.
- **`remaining_time` reads zero between prints.** Upstream parks the sensor there when
  nothing is running, so zero is translated as *no job* rather than as *about to finish*.
  The cost is the final sub-minute of a real print, which shows a dash — the same
  under-claim `total_layers` makes for a file that is not sliced yet.

### Connection mode

The integration is used in **hybrid** mode: authenticated against Bambu Cloud, with local
MQTT enabled. Job state and `ams_mapping` arrive over the local network; the print history,
which carries the per-tray weights, comes from the cloud session.

**Never instruct a user to enable LAN mode on the printer.** On a Bambu machine that is not a
connection preference — it disables the cloud, and with it Bambu Handy and remote printing
from Bambu Studio. The design's preference for local transport does not outrank how somebody
uses their printer every day. See [12 — Field Notes](12-field-notes.md).

This section is the price of [ADR-0002](adr/0002-reject-spoolman-as-foundation.md) and
[ADR-0003](adr/0003-custom-integration-over-addon.md) being right: the integration is small
because it consumes `ha-bambulab` instead of reimplementing MQTT, and the cost of that is one
carefully drawn boundary. Drawing it here, once, is cheaper than drawing it accidentally in
nine places.
