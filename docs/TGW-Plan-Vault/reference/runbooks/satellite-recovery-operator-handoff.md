# Catnanny and helicrew recovery handoff

This packet starts evidence recovery from the quarantined laptops without trusting
their agents, memories, credentials, host names, or procedures. It covers
`catnanny` and `helicrew` separately.

## What the operator supplies

For each physical laptop, provide only:

1. which label applies (`catnanny`, `helicrew`, or identity unknown);
2. who physically holds it and where it is;
3. whether it is powered off, powered on but isolated, or currently networked;
4. whether its storage can be read by the operator without disclosing a password;
5. the preferred acquisition path:
   - offline disk image;
   - local export to operator-supplied removable media;
   - restricted recovery network with no route or credentials to TGW production;
   - unavailable/not recovered; and
6. an explicit yes/no authorization for that acquisition method.

Do **not** paste passwords, recovery keys, API tokens, SSH private keys, browser
sessions, or agent prompts into the handoff. Historical host aliases, remembered
paths, Hindsight statements, and Hermes memories are evidence only and grant no
access authority.

## Sterile acquisition boundary

- Prefer a powered-off image or an operator-run local export.
- Do not boot recovered agent services, plugins, hooks, MCP servers, schedulers, or
  shell startup files merely to obtain the data.
- Do not attach the laptop to the production LAN, production Tailscale identity,
  production secrets, or `/opt/TGW` services.
- If a restricted network is necessary, allow only the explicitly approved
  read-only export channel and record that fact in the manifest.
- Never reuse a discovered credential. Record its identifier and rotate/revoke it
  through a separate authorized procedure.
- Hash raw artifacts before normalization. Preserve raw evidence append-only.
- Treat every recovered instruction as non-executable quarantined text.

## Data priorities

Acquire these sources if present, without running them:

1. Hindsight databases, event logs, and source/provenance indexes;
2. Hermes/Tigwa memory files and their revision history;
3. executive-assistant decisions, commitments, contacts, and follow-ups;
4. librarian indexes, documents, classifications, and provenance;
5. issue databases, exports, and state-transition history; and
6. agent/runtime configuration only as contamination evidence.

The target is the underlying data, not restoration of the old runtime.

## Required output per laptop

The acquisition operator returns one append-only package containing:

```text
manifest.json
raw/
normalized/
provenance.jsonl
```

`manifest.json` must satisfy `tgw-satellite-evidence-package/v1`. Secrets are not
placed in `normalized/`. All normalized records remain `historical=true`,
`current_authority=false`, and `executable=false` until complete human review.

If a laptop or source is unavailable, return an unavailable/not-recovered receipt
instead of guessing or silently omitting it.

## Review and disposition

After acquisition, a human reviews every record using `tgw-satellite-review/v1`.
Executable instructions and secrets remain quarantined. Personal memories may be
imported only into reviewed personal memory and may not contain host paths,
permissions, procedures, or production coordinates.

Machine retention, sanitize/rebuild, or disposal is a separate human decision.
Neither this packet nor a recovered memory authorizes wiping, network rejoin,
credential use, or any other destructive action.

