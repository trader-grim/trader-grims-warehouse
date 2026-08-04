# Review: Monetization and funding research review

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T20:21Z
**Todo:** #1600

Independent review of the staged monetization/funding research (`inbox/tigwa/TIGWA-RESEARCH-monetization-and-funding-opportunities-2026-07-20.md`), read together with all 7 addenda in `inbox/claude/`. Research review only — no entity formation, application, outreach, public release, or implementation authorized by this reply.

## 1. Three-path separation (commercial / open-public-benefit / career-consulting)

Sound, and worth keeping strict — mixing these has real failure modes (a nonprofit formed partly for commercial-product PR, an open-source release that quietly depends on TGW-private plumbing). One addition: the open-public-benefit and career/consulting paths aren't purely independent — a clean, published Catio reference architecture (path 2's deliverable) is itself the strongest possible evidence artifact for path 3's capability brief. Worth sequencing so effort on the open artifact double-counts toward the consulting portfolio, without letting the consulting motive bend the open-source design (e.g. don't build "impressive to hire me" features into what's supposed to be a minimal generic primitive). Keep the paths separate in *incentive*, not necessarily in *artifact reuse*.

The camera+clipboard "bundle" hypothesis (#3 in the original five) is correctly treated as subordinate — a question about path 1's internal structure, not a fourth path. Agree with that demotion.

## 2. Credible generic/open-source boundary — clipboard-event-server and Catio

This is where the addenda materially change the picture from the base research doc, and the addenda are the stronger evidence:

- **Android's platform constraints make the pivot away from "clipboard manager" necessary, not optional.** Android 12+ requiring reboot re-authorization and Android 15 effectively blocking clipboard history/logging (per Dave's operator experience, still needs source verification per Tigwa's own caveat) means any product built on *ambient clipboard observation* is not viable on current Android. The "explicit, selected-payload, named-device event" model isn't a rebrand of a blocked feature — it's a mechanically different thing (a user-initiated structured event, not passive monitoring), which is exactly why it sidesteps the restriction. This makes "Cryptboard as a new application class" a more defensible framing than it would be as pure marketing language — but see the naming caution below.
- **Wired Ethernet on collection devices is the right scope-narrowing move for a first validation** — it removes NAT/mobile-network/Bluetooth-pairing reliability from the first probe entirely. Agree this is "narrows validation," not "final product constraint" — the addendum already frames it correctly, just reinforcing it shouldn't quietly become an assumed permanent constraint in whatever gets written up for NLnet.
- **The real open-source-boundary question is answerable with code, not more research.** Since the existing TGW tool is explicitly a proof of concept (not just inspiration, per the addendum), the generalizable primitive already runs. The concrete test: try to extract the event-transport mechanism (explicit event → named destination → receipt/expiry/history) into a standalone module with zero imports from TGW-specific code (`ItemData`, `inventory_record`, the `tgw-api` fence, SKU format). If that extraction is clean, the "credible generic primitive" claim is real. If it's deeply coupled, that's itself the finding — the open-source pitch needs more separation work before it's written, not just more research words. Recommend this extraction attempt happen *before* drafting anything for NLnet, not in parallel with it (see probe 1 below).
- **"Cryptboard" is provisional and should stay that way until a real threat/key/recovery model exists.** The name implies a security guarantee (encryption, authentication) that hasn't been designed yet. The concrete risk: if "Cryptboard" appears in any external-facing document (even a funding application) before that model exists, it's an overstated claim the moment someone asks "what's your key management story?" Keep the name internal-only until there's a real answer, not just a UX description of "authenticated events."

## 3. Nonprofit/fiscal-sponsorship traps

The research already correctly flags the fiscal-sponsorship-without-501(c)(3) gap (TechSoup) and warns against forming a nonprofit mainly for discounts. Two additions:

- **Governance, not just eligibility.** A nonprofit that wants to survive scrutiny needs an independent board and real separation from Dave's personal/commercial interests (self-dealing rules for a 501(c)(3) are not a formality). This is a structural commitment, not a paperwork step — worth naming explicitly since "governance" is easy to skip in a hypotheses document and hard to skip in practice.
- **Timeline reality check.** IRS 501(c)(3) determination realistically runs many months to over a year even for a straightforward filing. Every "documented resource after qualifying status" in the research (Anthropic/OpenAI/AWS/Google/GitHub nonprofit programs) is gated behind that timeline. This reinforces the research's own framing (nonprofit as "a third, deliberate decision," not a near-term funding lever) — worth stating the concrete time cost so it's not accidentally treated as parallel-track with the two near-term probes.
- **Smell test worth adding:** would this nonprofit's sole activity, with the commercial motive removed entirely, still be worth doing on its own terms? If the honest answer is no, the eligibility question is moot regardless of formal qualification — the mission itself is the problem, not the paperwork.

## 4. Funding candidates: plausible vs. superficially related

Agree with the research's own gradations on most of these (NLnet as strongest fit contingent on real FOSS boundary; Mozilla Builders correctly demoted to watch-only given unverified current availability; OpenAI Researcher Access Program plausible only if framed as genuine governance/evidence research, not a commercial-build workaround; Anthropic Economic Futures correctly noted as not currently open; Google Cloud/AWS startup credits correctly identified as requiring VC-funded/pre-Series-B status TGW doesn't have).

One place to push back harder: **NSF SBIR/STTR is real but premature, and shouldn't sit alongside the two "minimally conflicting" probes as if it's comparable effort.** It requires a formal for-profit entity, a substantial proposal/budget process, an experienced grant-writing effort, and reviewers expect genuine novel technical R&D rather than "apply AI to an existing workflow." This is appropriately scaled to a company with a real prototype, traction, and dedicated capacity — not a pre-entity, pre-prototype validation stage. Recommend explicitly parking it as "revisit only after path 1 has a real entity and product," not listing it as a live near-term candidate.

## 5. The two proposed validation probes

Both are reasonably scoped and don't conflict with each other. One sequencing fix and one addition:

- **Open-path probe (test NLnet fit):** should be sequenced *after* — or at minimum alongside, not before — the code-extraction test from point 2. Applying to NLnet before confirming the primitive can actually be cleanly separated from TGW-private plumbing risks either overstating the application's premise or discovering post-award that the "narrow FOSS primitive" isn't as separable as hoped. Make the extraction attempt the literal first step of this probe, not a separate future task.
- **Commercial/career probe (capability brief + conversations):** agree with the shape. Worth naming explicitly: this very session is already live evidence for that brief — the branch-per-task contract, adversarial runner-review, and invariant-driven governance used to ship today's `tgw-models.json` config change (todos #1597/#1598) is a real, current instance of "governable agentic operations," not a hypothetical case study. That evidence is accumulating regardless of whether either probe gets pursued — worth Tigwa/Dave knowing it doesn't need to be manufactured separately.

## Net assessment

The three-path structure and both probes hold up. The addenda (Android platform constraints, wired-Ethernet scope-narrowing, existing-tool-as-proof-of-concept, camera HUD detail) sharpen rather than undermine the research — they turn "clipboard event server" from a speculative product idea into a mechanically well-motivated one. The one design risk worth active attention: don't let "Cryptboard" as a name outrun "Cryptboard" as a designed system before anything external gets written. The one funding-candidate correction: treat SBIR/STTR as a later-stage option, not a near-term probe.
