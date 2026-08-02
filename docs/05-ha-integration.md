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

**Step 1 — Printer.** Discover Bambu Lab devices from `ha-bambulab`. The user selects one, or
chooses *"No printer — manual inventory only"*, which is a fully supported mode rather than a
degraded one.

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

filament_ledger.mount_spool:
  fields:
    spool_id: {required: true,  selector: {text: {}}}
    slot:     {required: true,  selector: {number: {min: 1, max: 4}}}

filament_ledger.unmount_spool:
  fields:
    spool_id: {required: true}

filament_ledger.approve_review:
  fields:
    review_id: {required: true}
    amounts:   {required: false, selector: {object: {}}}   # {spool_id: grams} — overrides estimate
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

## 5.5 Events

Domain events, bridged onto the HA event bus with a `filament_ledger_` prefix. The domain
raises them without knowing HA exists; `event_bridge.py` performs the translation.

| HA event | Payload |
|---|---|
| `filament_ledger_review_opened` | `review_id`, `job_name`, `reason`, `estimated_total_g`, `spools` |
| `filament_ledger_review_resolved` | `review_id`, `resolution`, `applied_total_g` |
| `filament_ledger_movement_recorded` | `spool_id`, `type`, `amount_g`, `new_balance_g` |
| `filament_ledger_spool_depleted` | `spool_id`, `label` |
| `filament_ledger_confidence_degraded` | `spool_id`, `from`, `to` |
| `filament_ledger_anomaly_detected` | `spool_id`, `kind`, `detail` |
| `filament_ledger_unknown_spool_detected` | `tag_uid`, `slot`, `material`, `colour_hex` |

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
filament_ledger/spools/get           → SpoolDetail
filament_ledger/spools/movements     → MovementHistory     (UC-12)
filament_ledger/spools/create        → SpoolId             (UC-01)
filament_ledger/spools/update        → ok                  (metadata only — never balance)
filament_ledger/reviews/list         → PendingReview[]
filament_ledger/reviews/approve      → ok                  (UC-06)
filament_ledger/reviews/dismiss      → ok                  (UC-07)
filament_ledger/spools/reconcile     → NewBalance          (UC-08)
filament_ledger/spools/discard       → ok                  (UC-09)
filament_ledger/spools/adjust        → ok                  (UC-10)
filament_ledger/slots/state          → SlotState[]
filament_ledger/subscribe            → live event stream
```

`spools/update` edits metadata — label, vendor, colour — and **cannot alter a balance**. There
is no endpoint that sets a balance directly. Changing a balance requires a movement, and that
is the whole design. An API that could set a balance would make the ledger decorative.

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
