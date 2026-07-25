# Uh-huh — Thought-Capture and Deferred-Response Tool

**Status:** proposal / staged for review
**Requested by:** Dave Buko
**Date:** 2026-07-25

## Purpose

Uh-huh is a deliberate listening mode for when Dave is working through a complete thought, especially a connected operational, architectural, or personal explanation. Its job is to lower the friction of thinking aloud: the agent stays present, records the thread faithfully, does not interrupt with premature analysis, and responds coherently only when Dave releases the floor.

The behavioral inspiration is attentive-seeming, low-interruption listening—not fake inattentiveness. The tool must never pretend to have lost the thread, sleep, or understand something it did not capture.

## User contract

### Start

Dave can say any clear equivalent of:

- `Uh-huh`
- `start Uh-huh`
- `hold comments while I think this through`
- `listen; I am not done`

The assistant confirms once: `Uh-huh mode is on. I will capture the thread and wait for your release.`

### While active

For each ordinary continuation, the assistant replies only with a minimal acknowledgement such as `uh-huh`, `mm-hm`, or `following`, without questions, interpretation, correction, or plan expansion.

It retains the content and sequence of the thought in the active session. It does **not** invoke tools, alter files, send messages, open tasks, make plans, or delegate merely because content mentions a possible action. It queues those ideas for the eventual response.

### Release / handoff

Dave can end the mode with a clear equivalent of:

- `done`
- `your turn`
- `what do you think?`
- `wrap that`
- `leave Uh-huh mode`

On release, the assistant responds in this order:

1. a concise faithful restatement of the complete thought, preserving important distinctions and uncertainty;
2. explicit decisions or preferences Dave stated;
3. candidate actions/questions, clearly marked as proposals rather than silently executed;
4. only then, requested analysis or tool work.

If the intended end is ambiguous, remain in Uh-huh mode rather than prematurely taking over.

## Safety and truthfulness rules

- Uh-huh is **deferred response**, not inattentive response. The agent must accurately retain and later distinguish what Dave said from its inference.
- Normal safety constraints remain active. The tool cannot accept secrets, authorize unsafe side effects, or conceal urgent safety issues.
- A direct explicit command that requires immediate action overrides the hold; before acting, the assistant briefly states that it is leaving/pausing Uh-huh mode for that command.
- No external sharing, memory write, task creation, or durable transcript export occurs without Dave’s explicit request after release.
- The final synthesis must identify ambiguity, contradiction, or missing context instead of filling it with plausible assumptions.

## Suggested implementation shape

Implement as session-scoped state, not a model prompt trick:

```text
mode = normal | uh_huh
captured_turns = ordered user-message records
started_at / released_at
release_reason
```

Required behavior hooks:

1. Parse explicit start, pause, resume, and release phrases before normal response generation.
2. While `mode=uh_huh`, append user content to `captured_turns`; issue a fixed minimal acknowledgement; suppress normal planning/tool routing.
3. On release, inject the captured turns as a labeled, ordered input to the normal response path; clear or archive the buffer only after the user-facing synthesis is produced.
4. Surface mode visibly in CLI/TUI/desktop/messaging interfaces so Dave can tell whether the agent is listening or acting.
5. Preserve ordinary session retention semantics; any dedicated transcript export must be explicit and provenance-marked.

## Acceptance scenarios

1. **Multi-message thought:** Dave sends six related messages after starting Uh-huh. The agent gives only brief acknowledgements; after `your turn`, it correctly joins all six into one coherent response without treating message three as a final request.
2. **Mid-thought action mention:** Dave says “we should change permissions.” The agent does not make a permission change or open a task until release and explicit direction.
3. **Explicit immediate command:** Dave says “pause Uh-huh and check the host now.” The agent announces the pause and performs the requested read-only check.
4. **Ambiguous ending:** Dave pauses after a sentence without a release phrase. The agent remains in listening mode rather than summarizing prematurely.
5. **Fidelity:** The final response labels Dave’s decisions, unresolved questions, and agent proposals separately.
6. **Cross-surface visibility:** the active state is clear on each supported interface and does not silently carry to an unrelated session.

## Non-goals

- Simulating sleep, distraction, or fake comprehension.
- Recording or exporting a hidden private transcript.
- Replacing a task system, meeting recorder, or durable decision log.
- Performing background work while claiming to only listen.
- Treating a single acknowledgement phrase as consent to execute queued work.

## Open design choices for review

- Exact command grammar and whether slash commands are needed in addition to natural-language triggers.
- Whether the listening buffer should survive a context compaction/session reconnect, and how that persistence is made visible.
- How long an inactive Uh-huh state remains active before asking Dave whether to keep it open.
- Whether a post-release `save this thought` control should create an explicitly user-approved library/intake artifact.
