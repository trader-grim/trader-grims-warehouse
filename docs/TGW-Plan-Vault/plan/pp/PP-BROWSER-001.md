# PP-BROWSER-001 — TGW browser intake extensions

**Opened:** 2026-07-26 (Dave decision, recorded by Tigwa).  
**Status:** DIRECTION SET; initial design and local prototype exist. No production, browser-profile, credential, or TGW service change is authorised by this PP alone.

## 1. Decision

TGW will treat the local Hindsight link-capture prototype as the beginning of a **TGW-specific Chrome and Firefox extension family**, rather than as a one-off generic browser helper.

The first product is a deliberately invoked browser intake surface. It must present two visibly separate destinations:

1. **Send to Tigwa for research** — retains a selected excerpt or a page/link with source provenance into the Tigwa/Hermes research intake lane. The item is available for Tigwa to recall, assess, compare, and turn into a reviewable research packet when Dave asks.
2. **Save for Dave's later reference** — retains the same bounded capture into a personal-reference lane. It must not appear in the routine Hermes/Tigwa recall context and must not be treated as a request for agent work.

This preserves two different meanings that must never be inferred from one another:

```text
Dave wants Tigwa to consider / research this
!=
Dave wants to keep this personally for later
```

Neither action accepts an item into the TGW Plan Vault, a canonical library, a taskboard, or a production workflow.

## 2. Authority and storage boundaries

### 2.1 Sources of truth

- Original page, file, service, or conversation remains the evidence source.
- Plan Vault and Dave-accepted artifacts remain TGW's shared/canonical authority.
- Hindsight is local, derived, non-authoritative retrieval context.
- The extension is an explicit intake tool; it has no production TGW authority and must not obtain it indirectly.

### 2.2 Initial named lanes

| User action | Initial Hindsight destination | Visibility | Meaning |
|---|---|---|---|
| Send to Tigwa for research | `helicrew-default` with `purpose:research` and `intake:unreviewed` | Available to the active Tigwa/Hermes provider | Dave is asking for potential research/analysis. |
| Save for Dave's later reference | a distinct personal bank, proposed `helicrew-dave-reference` | Not configured as the Hermes provider's normal recall bank | Dave is preserving a reference; no agent attention is implied. |

The personal bank is a separation boundary, not a weaker tag. A tag in the active default bank is insufficient because ordinary Hermes recall could still surface it as working context.

A personal item may only enter the research lane through a future explicit action such as **Promote to research**. That action must retain the source reference, original capture time, and promotion time; it must not silently copy or reclassify the item.

### 2.3 What the extension may capture

The first implementation supports only explicit right-click actions:

- selected text plus page title and canonicalized source URL; or
- page/link title and canonicalized source URL.

It does not automatically read or retain full pages, browsing history, form fields, clipboard content, screenshots, cookies, credentials, customer/private inventory evidence, or background browsing behavior.

Sanitize source URLs by removing fragments, query strings, username, and password before retention unless a later, explicit reviewed design establishes a narrow exception. The capture record must identify:

- selected lane and user-facing action;
- capture time;
- original page title;
- sanitized canonical URL;
- selected excerpt when present;
- extension/browser version; and
- `source:browser-clip`, `intake:unreviewed`, and lane/purpose metadata.

The extension must visibly warn before any design expands beyond that bounded capture model.

### 2.4 Exclusions

The initial extension must not:

- expose local Hindsight beyond loopback;
- use TGW production credentials, cookies, service tokens, or database access;
- send captures to a third-party service;
- create a TGW-specific browser extension for marketplace actions, eBay mutation, listing changes, taskboard actions, queue control, or production administration;
- infer research intent from a personal save, or personal-save intent from a research action;
- become a second Plan Vault, taskboard, canonical library, inbox, or hidden agent command surface.

## 3. Product boundary: a TGW-specific extension family, not arbitrary browser control

This PP establishes a common browser-extension substrate for TGW-scoped, user-visible browser conveniences. Each later capability still needs its own bounded PP/task, permissions review, acceptance evidence, and Dave gate.

The common substrate may eventually provide:

- explicit context-menu actions;
- visible destination/lane choice;
- source/provenance envelopes;
- permission minimization and browser compatibility checks;
- local-only transport to an approved local service; and
- clear success/failure feedback.

It does **not** create a general “TGW browser extension” authority. In particular, future marketplace/account automation is out of scope unless separately approved, account-scoped, reviewed, and designed around consequential-action gates.

## 4. Technical direction

### 4.1 Shared core

Build one small WebExtensions-compatible core, with Chrome Manifest V3 and a separately validated Firefox manifest/configuration. Share only deterministic capture construction, URL sanitization, local API client behavior, status rendering, and test fixtures.

Keep the lane mapping in a narrow reviewed configuration module, not a generic arbitrary-URL or arbitrary-bank editor:

```text
research -> local Hindsight endpoint + active research bank
personal -> local Hindsight endpoint + isolated personal-reference bank
```

The browser must be able to operate only against the loopback Hindsight endpoint. Any remote endpoint, authentication route, bank-sharing change, or cross-device sync is a separately gated design.

### 4.2 Prototype disposition

The current local proof of concept at `/tmp/hindsight-link-capture` is a **non-installed, non-canonical prototype**. It has one active-bank destination and is not sufficient for this PP. Before any user installation it must be moved into a TGW-controlled source/worktree, renamed around the two-lane model, reviewed, and tested.

No claim is made that the prototype has been loaded into Chrome or captured a real item.

### 4.3 Capture lifecycle

```text
Dave explicitly selects a browser action
  -> extension creates a minimal provenance envelope
  -> selected lane is visible in the action label and payload
  -> local Hindsight accepts/rejects the capture
  -> extension reports exact accepted/failed status
  -> research lane: later recall/review only on request
  -> personal lane: inert reference unless Dave explicitly promotes it
  -> Plan Vault/library/task creation: separate explicit decision
```

## 5. Acceptance criteria for the first implementation

1. **Two-lane correctness**
   - Chrome displays distinct, unambiguous research and personal-reference actions.
   - Each action routes to its intended bank/lane; a fixture proves the payload never crosses lanes.
   - Personal-reference captures do not surface in a normal Hermes research recall test.

2. **Provenance and privacy**
   - Selected-text and page/link fixtures prove exact retained content, title, sanitised URL, capture timestamp, and lane metadata.
   - A fixture proves query/fragment/credential removal.
   - The manifest has no broad website access, browser-history, clipboard, or remote host permission.

3. **Intent and authority**
   - Research captures remain `intake:unreviewed`; a recall result is not represented as Plan Vault or canonical authority.
   - The personal-to-research promotion path, if built, is visibly deliberate and preserves both provenance states.
   - No page capture may create a task, inbox artifact, eBay action, service action, or production mutation.

4. **Local transport and failure visibility**
   - A harmless public fixture verifies successful local Hindsight acceptance in each lane.
   - Offline/refused/malformed responses visibly fail without queuing a hidden retry or losing the selected text silently.
   - The extension sends only to the loopback endpoint in the initial release.

5. **Browser compatibility**
   - Chrome/Chromium manual fixture test passes.
   - Firefox manual fixture test passes with its reviewed manifest/API differences documented.
   - Firefox is not declared supported from Chromium-only tests.

6. **Review and release boundary**
   - Work occurs in a dedicated worktree with focused tests.
   - Independent permission/scope review and code review are recorded.
   - Dave explicitly approves loading the reviewed extension into a real browser profile.

## 6. Sequenced work

1. Create the personal-reference bank only after reviewing its name, retention, backup, and access model; verify it is not the active Hermes provider bank.
2. Move the prototype into a dedicated TGW worktree and replace its one-lane labels/configuration with the two-lane capture contract.
3. Add fixture-driven tests for lane routing, source-envelope construction, privacy stripping, and error paths.
4. Perform a local Hindsight API integration test with harmless synthetic content and inspect each bank independently.
5. Review Chrome MV3 and Firefox compatibility/permissions separately; make no claim of Firefox support until tested.
6. Prepare a short operator instruction and a visible distinction between “research” and “personal reference.”
7. Obtain Dave's installation gate for each browser profile.
8. Observe actual use before proposing broader extensions or any promotion/Plan Vault workflow.

## 7. Open decisions

- Exact retention/backup and user-access policy for `helicrew-dave-reference`.
- Whether personal-reference retrieval is a future read-only Dave UI, a named explicit Hermes request, or both.
- Whether research intake should use the existing default bank with rigorous tags or a dedicated `helicrew-research` bank plus an explicit provider/integration change. The first implementation must prove the chosen option does not hide routine Hermes session context or disconnect necessary research recall.
- How a reviewed promotion retains an immutable link to the original personal capture without making the personal bank generally visible to Hermes.
- The browser-profile installation/update model and signed-extension/release process, after the local fixture proves useful.

## 8. Non-authorisations

This decision authorises creation of this canonical PP and later scoped planning/review. It does not authorise installing browser extensions, creating the personal bank, changing Hindsight configuration, exposing Hindsight, modifying a flake, granting browser access to TGW systems, sending data outside the laptop, or building marketplace/production browser automation.

## 9. Cross-interface action contract — Dave decision, 2026-07-26

The two-lane Hindsight intake model is not browser-exclusive. It is the first instance of a wider **contextual TGW action-surface contract**: browser extensions, window-manager context menus, and later approved operator interfaces should expose the same named, bounded TGW actions where the relevant context is already present.

A surface may make an action discoverable because it knows a selected excerpt, focused URL, file, SKU, window, or other explicit local context. Context selects which actions are relevant; it does not grant authority, widen access, or infer Dave's intent.

Every later surface must preserve these rules:

- Actions are explicitly invoked by Dave; no ambient clipboard, browsing, focus, or window observation may create work or retain content.
- Each label states its destination and effect, for example **Send to Tigwa for research**, **Save for Dave's later reference**, or a separately approved read-only TGW lookup.
- A research/personal distinction remains bank-level, provenance-preserving, and non-authoritative regardless of source surface.
- Context data is minimized to what the selected action needs and carries a source-surface/action/version envelope.
- Consequential TGW actions remain behind their own named approval/confirmation contract. A convenient menu item cannot turn an inspect/prepare action into a hidden execute action.
- Unsupported or unavailable context renders a disabled/explained action, never guessed values or a generic agent command box.

`PP-WM-001` is the first non-browser participant: it will add explicit window-manager context menus using this contract. It must not be implemented by extending the legacy clipboard watcher into ambient capture or by wiring direct re-enqueue/marketplace/production actions into a desktop menu. Browser and WM work remain separate implementation packets with shared semantic/permission review, not a mandate to build a universal extension framework now.

### Desktop-native command-console principle

This direction follows TGW's existing desktop integration pattern: shell/tab completion provides typed action discovery and argument resolution; the macroboard supplies a physical context trigger (`PP-MACRO-001`); the floatable outbox/clipboard surface supplies a focused operator interaction (`PP-OUTBOX-001`); and browser/WM menus supply graphical, context-aware entry points. These are complementary faces of **one integrated command console**, not a collection of launchers that merely open a separate TGW app.

The common goal is that an operator can encounter relevant context in the normal desktop workflow and discover the bounded TGW verb there. Each surface must resolve only the context and action it can prove, show an explained unavailable state when it cannot, and then invoke the same reviewed capability/confirmation contract as the CLI or another approved surface. Tab completion/menu presence/macro availability may improve discovery; none may expand the action's authority, manufacture arguments, or bypass its human gate.

Launching an application remains a useful fallback, but is not the integration target. The target is a coherent TGW command console whose actions are available where they matter, retain their provenance and boundaries, and lead to the same truthful read/prepare/execute semantics across typed, physical, browser, and WM interfaces.
