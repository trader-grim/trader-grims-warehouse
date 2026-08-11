# Server environment cleanup track

Status: **PROPOSED**  
Parent: [TGW environment and agent recovery program](PLAN-environment-cleanup-program.md)  
In scope: `tgw-lib`, `tgw-prod`, repositories, workspaces, instructions, procedures,
and agent context generation

## Target environment

The server environment has two stable roles:

- `tgw-lib`: development/control role, canonical source mirrors, task provisioner,
  tests, and evidence assembly.
- `tgw-prod`: production NixOS role, immutable application releases, PostgreSQL and
  production services, with the flake maintained at `/home/db/tgw-flake` as user
  `db` unless the registry later records an approved migration.

Application source and Nix configuration remain separate repositories. Every coding
task receives an ephemeral worktree and a machine-readable task manifest. An agent
does not choose a checkout based on cwd, memory, or the first path found.

## S0 — inventory and freeze

Produce a read-only inventory with hashes and ownership for:

- active repos, branches, remotes, linked worktrees, dirty files, and unreachable
  commits;
- `/opt/TGW/current`, immutable release roots, controller environments, application
  data, and Nix checkout/current generation;
- systemd units, timers, configuration sources, service users, and deployment
  entrypoints;
- every instruction source automatically surfaced to Claude, Codex, Hermes/Tigwa,
  and other agents;
- every reference to retired hosts, obsolete paths, old virtual environments, and
  procedures that invoke them; and
- canonical Plan Vault locations versus copied or projected plan material.

Acceptance: a signed inventory artifact distinguishes verified current facts,
historical references, conflicts, and unknowns. No cleanup occurs in S0.

## S1 — authoritative environment registry

Create one versioned registry, proposed location
`/opt/TGW/environment/registry.yaml`, with a generated per-session snapshot. It must
contain stable logical IDs rather than free-form host/path strings:

```yaml
schema: tgw-environment/v1
revision: <content hash>
hosts:
  production:
    canonical_name: tgw-prod
    roles: [production, nixos]
  development:
    canonical_name: tgw-lib
    roles: [development, controller]
retired_hosts:
  a1131:
    replacement: tgw-prod
    behavior: fail
repositories:
  application: {host_role: development, path: <verified path>}
  nix_flake: {host_role: production, path: /home/db/tgw-flake, branch: master}
plans:
  canonical_root: <verified URI/path>
```

Requirements:

- schema validation and a content hash;
- no silent fallback for unknown or retired names;
- facts include provenance and verification time;
- secrets are references to the approved secret store, never registry values; and
- agents receive only the relevant resolved subset.

## S2 — deterministic task workspaces

Adopt one task workspace layout, for example:

```text
/opt/TGW/dev/repos/<repo-id>/
/opt/TGW/dev/workspaces/<task-id>/
  TASK.json
  RESOLVED_CONTEXT.json
  source/                 # linked worktree
  evidence/
```

`TASK.json` binds task ID, repo ID, base commit, branch, actor type, allowed paths,
effect class, plan/version, acceptance IDs, and expiry. Provisioning fails if the
repo, base commit, dirty-state policy, or registry revision is ambiguous.

Migrate useful work by commit/bundle after reachability and dirty-diff capture. Do
not copy an entire old working directory into the new root. Retire stale worktrees
only after their commits and untracked files are accounted for.

## S3 — instruction consolidation

Define precedence explicitly:

1. platform safety and user authorization;
2. agent-neutral repository `AGENTS.md`;
3. generated environment snapshot;
4. exact task/packet contract;
5. optional persona style overlay; and
6. historical memory and search results, which never grant authority.

Repository `AGENTS.md` should contain only shared repository constraints and actor
routing. Claude-specific operating rules remain in `CLAUDE.md` and are supplied only
to Claude Code. Hermes/Tigwa contracts live in an actor registry outside application
source and refer to current registry IDs, not host literals. Packets cannot broaden
the authority of their issuer.

Add a linter that reports:

- unregistered or retired hosts and paths;
- contradictory actor routing;
- missing effective dates or owners;
- instructions that treat memory/history as authority;
- duplicated canonical plans; and
- procedure text containing direct mutable deployment commands instead of a
  registered procedure ID.

## S4 — registered procedures

Provide reviewed, versioned wrappers for common operations such as:

- `tgw-app-install` / `tgw-app-rollback`;
- `tgw-nixos-evaluate` / `tgw-nixos-deploy` / rollback;
- database migration preflight and verification;
- task workspace provision/retire; and
- evidence export and acceptance audit.

The procedure implementation must not be selected from the application release it
is replacing. Each invocation records procedure version, actor, registry revision,
inputs, intended effects, outcome, and rollback target. Runbooks describe how to
invoke a procedure; they do not become executable authority by being retrieved.

## S5 — TGW Steward

Construct a clean, narrow agent rather than restoring the old Hermes runtime:

- current facts come from the registry and canonical repositories;
- procedures are allowlisted tools with structured arguments;
- task authority comes from `TASK.json` or an approved plan work unit;
- Plan/Todo intent is read separately from operational history;
- Hindsight lookup is an explicit tool whose results are labelled historical and
  cited;
- durable personal memory contains only reviewed preferences, decisions, and stable
  relationships—not machine paths or operational permissions; and
- executive-assistant, librarian, and issue-management behavior are bounded modes
  with separate stores and permissions.

The conversational name may be Hermes. Its operating identity and tool policy are
TGW Steward. This preserves familiarity without preserving contaminated authority.

Acceptance tests include current host/path questions, retired-name refusal,
instruction-conflict handling, history-versus-current discrimination, source
citations, and attempts to obtain production authority from memory text.

## S6 — migration and retirement

Canary clean workspaces and TGW Steward with read-only tasks first. Introduce
reversible consumers of the new registry one at a time. Preserve old environments as
read-only snapshots during the acceptance window. Only then schedule exact deletion
targets through a separate destructive-change approval.

Track completion requires reproducible workspace creation, registry validation,
clean agent acceptance, registered deployment rollback, and a zero-unresolved-item
inventory for every old worktree and instruction source.

