# ADR-0011 — System-aware light and dark panel themes

Date: 2026-11-06. Status: accepted for implementation by the repository owner in the feature clarification.

## Context

The owner explicitly requires clean, practical phone/desktop workflows and readable filament colours in both light and dark appearances. ADR-0008's fixed-dark constraint conflicts with that request.

## Decision

Supersede ADR-0008's dark-only behaviour, retaining product identity, local assets, vanilla modules and shadow-root isolation. Automatic follows device system appearance; explicit Light/Dark overrides are device-local. Use semantic tokens and bounded true-colour samples with independent text/status. Theme changes update host styling without losing input/focus. Storage failures preserve session usability and explain non-persistence.

## Consequences

Verify every existing surface, native control and overlay in both themes; preserve keyboard/phone accessibility, contrast and EN/ES copy. No framework, bundler, runtime dependency, global preference write or accounting change is authorized by this visual decision.
