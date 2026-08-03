---
name: Bug report
about: Something the ledger got wrong, or a screen that will not behave
title: ""
labels: bug
assignees: ""
---

## What happened

<!-- One or two sentences. What you expected, and what you got instead. -->

## Versions

| | |
|---|---|
| Home Assistant | <!-- e.g. 2026.7.4 --> |
| Filament Ledger | <!-- the version in Settings → Devices & Services, or the tag you installed --> |
| ha-bambulab | <!-- e.g. 2.2.22, or "not installed" if you run manual-inventory mode --> |
| Installed via | <!-- HACS custom repository / manual copy --> |

## Printer

| | |
|---|---|
| Model | <!-- A1, A1 Mini, P1S, X1C… — the A1 with AMS Lite is the only verified one --> |
| AMS | <!-- AMS Lite / AMS / none --> |
| Connection mode | <!-- hybrid (cloud + local MQTT) / local MQTT / cloud only — see the README warning about LAN-only mode --> |

## What History shows

This is the single most useful thing you can paste. Open the panel's **History** tab (or the
spool's own detail view) around the time of the problem and describe — or screenshot — the rows:
what entries are there, what amounts, whether they are labelled *auto* or *confirmed*, and which
entry you expected that is missing.

If the answer is "nothing appeared", check the **Review** tab too and say what is waiting there.
A print that could not be attributed opens a review rather than deducting; that is designed
behaviour, and knowing whether a card exists tells us which half of the system to look at.

```
paste the relevant history rows here
```

## Steps to reproduce

1.
2.
3.

## Logs

Home Assistant logs filtered to `custom_components.filament_ledger`. Enable debug first if you
can — `logger:` → `custom_components.filament_ledger: debug` in `configuration.yaml`.

```
paste logs here
```

## Anything else

<!-- Screenshots, a hunch, the thing you were doing five minutes earlier. All welcome. -->
