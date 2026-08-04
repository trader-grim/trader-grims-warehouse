# Perplexity guided-research prompt: monetization of existing proof-of-concept tools

**State:** prompt template / staged research input; Perplexity output must be captured as staged evidence and reviewed by Dave before any action

```text
Act as a skeptical product-strategy and funding researcher. Research current information as of today. Cite every material claim with direct links, prioritize primary sources (Android developer docs, Google Play policy, official vendor/program pages, grantmaker pages, company pages, and reputable market research). Clearly label facts, inference, and unknowns. Do not make up competitors, policy constraints, pricing, user demand, legal conclusions, or program eligibility.

Context: we already operate two internal proof-of-concept tools in a real collection workflow. This is not ideation from zero. The research has two equally valid outcomes: (a) identify a safely generalizable commercial/open-source/hosted opportunity, or (b) improve the internal tools even if no standalone product is pursued. Do not assume a startup, nonprofit, public release, or fundraising is required.

TOOL A — camera-guided subject capture
- A live HUD overlays structured collection data on the camera preview.
- In-place action menus let the operator edit title and location and advance to the next subject/location.
- A commit action creates a JSON/SKU/location record, saves related product photos as one labeled group/folder, then advances to the next subject.
- It binds photos, subject identity, metadata, and location at the moment of capture rather than relying on later reconciliation.
- Generalized interaction hypothesis: capture a complete, labeled photo group for one named real-world subject, with visible contextual metadata; commit it; move cleanly to the next subject.

TOOL B — Android/local-network event transfer and history
- This arose from real collection-device pain: around Android 12, in our operator experience, clipboard access requires re-authorization after reboot; on Android 15, Clipper is almost useless for the workflow because clipboard logging/history is not allowed.
- Treat these Android statements as reported operator evidence that must be independently verified by Android version/API/OEM/distribution-policy sources. Do not treat them as universal facts unless sources support them.
- Devices have wired Ethernet, so the first useful boundary is local-LAN, not unreliable mobile/Bluetooth/public-internet transport.
- We do NOT want ambient OS clipboard capture, hidden monitoring, broad Accessibility-service abuse, broadcast mirroring, or a permission bypass.
- Proposed alternative: explicit selection of a value/structured record; named recipient/device/workspace; authenticated/encrypted transfer; visible delivery/receipt; bounded retention/expiry; searchable/recoverable event history.
- Working concept name: “Cryptboard.” This name is provisional and un-cleared. Do not assume it is available.
- The camera tool could optionally emit the committed structured record to a named local destination through this event layer. The camera workflow must remain useful without a paid service; the event-transfer capability may be an optional paid link only if users value it.

Research questions:

1. Product/category reality
   - Find the closest existing products and open-source projects for: (i) structured photo-batch/subject capture with live metadata overlay and next-subject flow; (ii) cross-device/local-network clipboard alternatives, event handoff, secure small-payload transfer, receipt/history; and (iii) encrypted/shared event boards or equivalent primitives.
   - For each, identify what it actually does, platforms, deployment model, pricing, and where it does NOT meet the described workflow. Link to primary product/docs pages. Avoid vague category lists.
   - Is “Cryptboard” a defensible distinct interaction category, or just a combination of established categories? State the honest answer.

2. Android/platform feasibility
   - Verify the reported Android clipboard constraints precisely: Android versions, relevant APIs, foreground/background restrictions, user-visible notifications/toasts, reboot behavior if any, OEM differences, accessibility restrictions, and Google Play policy implications.
   - Identify compliant ways an Android app can support explicit user-initiated capture/share and durable app-owned event history without reading the global clipboard in the background.
   - Separate what works on Android from iOS and desktop. Do not propose a workaround that violates policy or user trust.

3. User and buyer segments
   - Identify 3–5 concrete user segments where the camera “subject batch + metadata + commit + next” workflow is materially better than an ordinary camera/scanner/file-manager flow. Include examples such as resale/inventory only if evidence supports it, but look beyond resale.
   - Identify 3–5 segments for explicit local event transfer/history. Rank them by pain, willingness to adopt, deployment friction, and privacy/security sensitivity.
   - Distinguish the internal TGW use case from generic external demand. Explain the fastest ethically valid way to test demand without exposing private TGW data or claiming product maturity.

4. Monetization models
   Evaluate separately, with concrete precedents:
   - free/core camera application plus paid Cryptboard/event-transfer capability;
   - local-first/self-hosted open-source Cryptboard with paid hosted relay, team controls, history/retention, device management, or support;
   - paid standalone professional app;
   - consulting/employment portfolio built around governed agent systems, evidence/recovery, state-machine enforcement, and workflow tooling.
   For each, describe likely customer, value metric, pricing precedent (not invented pricing), support/security burden, and the smallest validation experiment.

5. Funding and resources
   Research only active or clearly labeled historical programs. For each candidate, state eligibility, size/type of support, deadlines/current status, requirements, and fit/risk:
   - open-source/public-benefit funding for a truly FOSS, local-first/privacy-preserving Cryptboard component;
   - startup cloud/AI credits and non-dilutive funding;
   - research credits/grants for responsible AI/human-in-the-loop/agent governance research;
   - nonprofit resources only if a genuine public-benefit Catio/open-infrastructure mission exists.
   Include official sources for NLnet, NSF SBIR/STTR, AWS nonprofit/Activate, Google nonprofit/startup programs, GitHub nonprofits, OpenAI nonprofit/research programs, and Anthropic nonprofit/research programs only where currently active/accurately described.
   Explain why forming a nonprofit merely for discounts is a poor fit, and what mission/governance facts would be required for a legitimate nonprofit path.

6. Risks and boundaries
   - privacy/security/key custody/recovery/metadata exposure for Cryptboard;
   - platform-policy and app-store risk;
   - “new category”/branding overclaim risk;
   - separation of private internal workflow/data from generalizable public product behavior;
   - regulatory or accessibility concerns if relevant;
   - name/trademark/domain checks that should be performed later by qualified sources, without giving legal advice.

Deliverable format:
A. Executive conclusion: rank the top 3 paths by near-term evidence, strategic value, and effort; state confidence and why.
B. A competitor/parity table with direct sources.
C. Android feasibility matrix: claim, verified status, source, implication, open question.
D. Monetization matrix: model, buyer, evidence, smallest test, principal risk.
E. Funding matrix: program, current status, fit, eligibility blocker, official URL.
F. A 30-day research/validation sequence with no build, launch, fundraising, public release, data exposure, or credential use assumed.
G. Source list with direct URLs and retrieval dates.

Be candid. If the distinct opportunity is weak, say so. If the strongest result is simply a better internal tool, say so. Do not recommend contacting funders, forming an entity, publishing code, or making security claims without a separate decision.
```
