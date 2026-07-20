# Detail: camera HUD batch capture and optional clipboard-event linkage

**State:** `capture-staged` — Dave’s description of existing proof of concept; no productization/release/pricing authority
**Date:** 2026-07-20

## Demonstrated TGW interaction

The existing camera tool is a collection-workflow interface layered directly over the live camera display:

- a heads-up display shows the data being collected while the operator frames/captures;
- action menus support the collection workflow in place;
- the operator can edit the location and title, then advance to the next location/subject;
- a single action creates the JSON/SKU/location record;
- related product photos are saved as a labeled group/folder;
- the workflow then advances to the next subject.

For TGW this binds product data, physical location, SKU record, and images in the act of capture rather than requiring later reconciliation.

## Generalizable interaction hypothesis

The potentially useful non-TGW pattern is: **capture a bounded photo group for one named subject, retain visible metadata while capturing, commit it as a labeled set, and move cleanly to the next subject.**

The external value proposition is not necessarily inventory. It may apply wherever people photograph a series of real-world subjects and need each group named, contextualized, and complete before moving on.

## Product linkage hypothesis

The event-clipboard tool is the possible paid linkage: an explicit event/data-transfer channel integrated with the capture workflow, rather than hidden clipboard monitoring. It could transfer the committed structured record or selected values to a named local destination and retain observable delivery/history semantics.

This is a packaging/revenue hypothesis, not an approved pricing or feature decision. It must be tested separately from the core camera workflow and must preserve the Android-permission-safe, explicit-event boundary.
