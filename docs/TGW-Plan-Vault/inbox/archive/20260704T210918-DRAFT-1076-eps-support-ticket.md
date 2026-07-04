# DRAFT — eBay Developer Support ticket: EPS call limit increase (todo #1076)

**Not submitted — this is a draft for Dave to review/edit/submit.** eBay
Developer Support tickets go through Dave's account; I can't submit this
on his behalf.

## Suggested ticket text

> **Subject:** Request to increase EPS (image hosting) API call limit
>
> **Account:** DaveBuko-Webkulap
>
> We run an automated inventory pipeline that uploads item photos via
> `UploadSiteHostedPictures` (Trading API / EPS). Our current daily EPS
> call limit (~5,000/day) has proven insufficient during fleet-wide photo
> repair operations. In one recent incident, a 3-day quota exhaustion
> event blocked photo uploads for hundreds of listings across our
> ~55,000-item catalog, forcing us to throttle a ~492-item photo-repair
> backlog to a slow per-day trickle rather than a single clean pass.
>
> We're requesting an increase to 25,000–50,000 EPS calls/day to
> accommodate periodic bulk photo-repair passes without disrupting
> day-to-day new-listing photo uploads. Happy to provide more detail on
> our usage pattern if useful — we track and self-throttle EPS usage
> internally already (a per-pool daily budget with automatic background
> halt at 70% utilization), so this isn't a request to remove guardrails,
> just to raise the ceiling those guardrails operate under.
>
> Separately, we'd also like to follow up on our pending
> `buy.marketplace_insights` (sold-price data) scope request if there's
> any update available.

## Context for Dave

- Current real usage is low right now (14/5,000 today per `tgw health`)
  — this isn't urgent based on today's number. The justification is the
  **peak-load** case: the 3-day EPS exhaustion incident that originally
  motivated PP-PHOTOSYNC-001, and the still-pending 492-item photo-repair
  ramp (P4, pre-authorized 1→5→ramp) that will spike EPS usage when it
  runs at scale.
- Bundling with the `buy.marketplace_insights` follow-up (per the todo's
  own note) makes sense if Dave wants — same support channel, saves a
  second ticket. Optional; drop that paragraph if he'd rather keep this
  ticket narrowly scoped to EPS only.
- No further action from me here — this needs Dave's review/edit and his
  own submission through Developer Support's ticket system.
