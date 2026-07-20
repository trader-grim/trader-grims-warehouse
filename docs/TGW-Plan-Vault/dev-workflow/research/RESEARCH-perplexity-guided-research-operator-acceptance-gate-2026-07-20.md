# Addendum: Guided Perplexity research requires an operator acceptance gate

**Status:** retained design clarification — no implementation authorized
**Owner:** Dave; Tigwa maintains provenance and staged evidence
**Date:** 2026-07-20
**Companion:** `RESEARCH-perplexity-guided-and-governed-research-integration-2026-07-20.md`

## Decision

The target save location for guided Perplexity Research sessions must have an explicit **operator acceptance gate**.

A session, export, link, citation set, or captured report is initially **staged external evidence**. It may be retained with acquisition provenance, but it is not silently promoted into a canonical research shelf, Plan Vault reference, governing PP, or implementation input merely because it was saved or appears useful.

## Required states

1. `guided-session-active` — Dave is steering research; no durable filing implied.
2. `capture-staged` — a selected session/output has been captured with source URL or supplied export/text, retrieval time, citations available, provenance, and integrity hash where bytes are retained.
3. `operator-accepted` — Dave explicitly accepts the captured material into a named target location/category and records its intended role (reference, retained research, decision input, or link-only).
4. `reviewed-synthesis` — Tigwa may create a provenance-linked synthesis after acceptance; it remains advisory unless separately approved.
5. `implementation-authorized` — a separate, explicit decision; acceptance of research never grants this state.

## Gate behavior

- The capture interface/workflow must show the proposed destination, artifact type, retention/provenance metadata, and the consequence of acceptance before filing.
- Dave’s acceptance must be explicit and logged with the rendered session/output instance; a reusable prompt/template is not acceptance of a later run.
- Rejection, deferral, or unaccepted staged capture must remain recoverable and discoverable without becoming canonical evidence by accident.
- An agent may prepare the artifact and recommendation but may not accept, promote, delete, or silently reroute it on Dave’s behalf.

## Scope boundary

This is a future workflow/data-model requirement for guided research integration. It does not authorize Perplexity account integration, API keys, browser automation, capture tooling, Plan Vault schema changes, or any external research action.
