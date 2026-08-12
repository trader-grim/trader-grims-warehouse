"""Plan graph builder and source-backed retrieval.

This module deliberately performs only deterministic textual extraction.  Its
outputs are navigation aids; the Markdown files remain authoritative.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

SOURCE_REVISION = "90c0288ea11e660380f0f23ec8e28a164c971ce1"
SCHEMA = "tgw-plan-graph-pilot-v3"
WARNING = (
    "Derived navigation context only; retrieve and read the cited canonical "
    "Plan Vault source before conclusions or action."
)
SNAPSHOT_WARNING = (
    "The frozen Taskboard is a snapshot, not live runtime state; live "
    "PostgreSQL work state remains authoritative."
)
PP_RE = re.compile(r"\bPP-[A-Z0-9-]+-\d{3}\b", re.I)
TODO_REF_RE = re.compile(r"(?<!\w)#(\d{3,})\b")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
INVARIANT_TOKEN_RE = re.compile(r"^(?:C|E)\d+[A-Z]?$", re.I)
INVARIANT_REF_RE = re.compile(r"\b(?:C|E)\d+[A-Z]?\b", re.I)
TODO_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|")
TOKEN_RE = re.compile(r"[a-z0-9]+")
REFERENCE_WORDS = ("audit", "runbook", "reference", "inventory", "boundary", "review")
ACTOR_HEADING_RE = re.compile(r"^(.+?)\s+\((\d+)\s+open\)$", re.I)
ASSIGNMENT_RE = re.compile(r"\b(?:assign(?:ed|ment)?|owner|actor|container|open tasks?)\b", re.I)
STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "b", "be", "between", "by",
        "for", "from", "in", "is", "it", "of", "on", "or", "set", "that",
        "the", "this", "to", "with",
    }
)
TERM_ALIASES = {
    "facts": ("attributes",),
    "marketplace": ("ebay",),
    "separate": ("boundary",),
    "specifics": ("aspects",),
    "transfer": ("apply", "boundary", "migration"),
    "reviewed": ("operator", "review"),
}
BRIEF_CAPS = {
    "candidates": 8,
    "master_plan_sections": 4,
    "detailed_pp_documents": 4,
    "linked_todos": 6,
    "referenced_invariants": 6,
    "linked_reference_documents": 6,
    "relationship_paths": 24,
}
IDENTITY_SCORES = {
    "exact_identifier": 10000,
    "mentions_identifier": 8000,
}
ENVELOPE_KEYS = frozenset(
    {
        "schema", "authority_role", "authority_locator", "observed_host", "observed_root",
        "head", "tree", "status_bytes", "status_sha256", "allowlist_bytes",
        "allowlist_sha256", "records_sha256", "record_count", "observed_at", "errors",
        "exclusions", "records", "envelope_sha256",
    }
)
ENVELOPE_STRING_KEYS = ENVELOPE_KEYS - {
    "status_bytes", "allowlist_bytes", "record_count", "errors", "exclusions", "records",
}
RECORD_KEYS = frozenset({"path", "type", "mode", "bytes", "sha256"})


class SourcePreconditionError(Exception):
    """A typed, machine-readable refusal at the controller source boundary."""

    def __init__(self, code: str, path: str | None = None,
                 expected: Any = None, observed: Any = None) -> None:
        self.code, self.path = code, path
        self.expected, self.observed = expected, observed
        super().__init__(code if path is None else f"{code}: {path}")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _descriptor_bytes(path: Path, rel: str) -> tuple[bytes, os.stat_result]:
    try:
        before = os.lstat(path)
    except FileNotFoundError as exc:
        raise SourcePreconditionError("source_absent", rel) from exc
    if not stat.S_ISREG(before.st_mode):
        raise SourcePreconditionError("source_type_mismatch", rel, "regular")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise SourcePreconditionError("source_replaced", rel) from exc
    except OSError as exc:
        code = "source_type_mismatch" if exc.errno in (getattr(os, "ELOOP", 40),) else "source_unreadable"
        raise SourcePreconditionError(code, rel) from exc
    try:
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise SourcePreconditionError("source_replaced", rel)
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > opened.st_size:
                raise SourcePreconditionError("source_changed", rel)
            chunks.append(chunk)
        after = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise SourcePreconditionError("source_changed", rel)
        return b"".join(chunks), opened
    except SourcePreconditionError:
        raise
    except OSError as exc:
        raise SourcePreconditionError("source_unreadable", rel) from exc
    finally:
        os.close(fd)


def _validate_sources(corpus: Path, allowlist: Path, envelope: Any) -> tuple[str, dict[str, bytes]]:
    if type(envelope) is not dict or set(envelope) != ENVELOPE_KEYS:
        raise SourcePreconditionError("envelope_invalid")
    if any(type(envelope[key]) is not str for key in ENVELOPE_STRING_KEYS):
        raise SourcePreconditionError("envelope_invalid")
    for key in ("status_bytes", "allowlist_bytes", "record_count"):
        if type(envelope[key]) is not int or envelope[key] < 0:
            raise SourcePreconditionError("envelope_invalid")
    for key in ("errors", "exclusions", "records"):
        if type(envelope[key]) is not list:
            raise SourcePreconditionError("envelope_invalid")
    for record in envelope["records"]:
        if type(record) is not dict or set(record) != RECORD_KEYS:
            raise SourcePreconditionError("envelope_invalid")
        if any(type(record[key]) is not str for key in ("path", "type", "mode", "sha256")):
            raise SourcePreconditionError("envelope_invalid")
        if type(record["bytes"]) is not int or record["bytes"] < 0:
            raise SourcePreconditionError("envelope_invalid")
    supplied = envelope["envelope_sha256"]
    unsigned = dict(envelope)
    unsigned.pop("envelope_sha256")
    if (
        envelope["schema"] != "tgw-plan-source-envelope-v1"
        or supplied != _sha(_canonical(unsigned))
        or envelope["authority_locator"] != "TGW_PLAN_VAULT_STANDALONE"
        or envelope["errors"]
    ):
        raise SourcePreconditionError("envelope_invalid")
    if envelope["authority_role"] != "standalone-plan-vault":
        raise SourcePreconditionError(
            "source_role_mismatch", expected="standalone-plan-vault",
            observed=envelope["authority_role"],
        )
    try:
        allow_raw, _ = _descriptor_bytes(allowlist, str(allowlist))
        paths = [line.strip() for line in allow_raw.decode("utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    except (UnicodeDecodeError, ValueError) as exc:
        raise SourcePreconditionError("envelope_invalid", str(allowlist)) from exc
    if len(paths) != len(set(paths)) or envelope.get("allowlist_bytes") != len(allow_raw) or envelope.get("allowlist_sha256") != _sha(allow_raw):
        raise SourcePreconditionError("envelope_invalid", str(allowlist))
    records = envelope["records"]
    if envelope["records_sha256"] != _sha(_canonical(records)) or envelope["record_count"] != len(records):
        raise SourcePreconditionError("envelope_invalid")
    by_path: dict[str, dict[str, Any]] = {}
    for record in records:
        rel = record["path"]
        if rel in by_path or Path(rel).is_absolute() or ".." in Path(rel).parts or Path(rel).as_posix() != rel:
            raise SourcePreconditionError("envelope_invalid", rel)
        by_path[rel] = record
    if sorted(paths) != sorted(by_path):
        raise SourcePreconditionError("envelope_invalid")
    source_bytes: dict[str, bytes] = {}
    for rel in sorted(paths):
        record = by_path[rel]
        if record.get("type") != "regular":
            raise SourcePreconditionError("source_type_mismatch", rel, "regular", record.get("type"))
        raw, observed = _descriptor_bytes(corpus / rel, rel)
        source_bytes[rel] = raw
        actual = {"mode": f"{stat.S_IMODE(observed.st_mode):04o}", "bytes": len(raw), "sha256": _sha(raw)}
        for field in ("mode", "bytes", "sha256"):
            if record.get(field) != actual[field]:
                raise SourcePreconditionError("source_changed", rel, record.get(field), actual[field])
    return supplied, source_bytes


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _paths(allowlist: Path) -> list[str]:
    values = [
        line.strip()
        for line in allowlist.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(values) != len(set(values)):
        raise ValueError("allowlist contains duplicate paths")
    return sorted(values)


def _citation(
    path: str, line: int, heading: str, digest: str, source_revision: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "line": line,
        "heading": heading,
        "source_sha256": digest,
        "source_revision": source_revision,
    }


def _add_node(nodes: dict[str, dict[str, Any]], node_id: str, **attrs: Any) -> None:
    node = nodes.setdefault(node_id, {"id": node_id})
    for key, value in attrs.items():
        if value not in (None, "", []):
            node[key] = value


def _node_type(rel: str) -> str:
    low = rel.lower()
    if "/pp-" in low or low.startswith("pp/"):
        return "detailed-pp"
    if "audit" in low:
        return "audit"
    if "runbook" in low:
        return "runbook"
    if "reference/" in low:
        return "reference"
    return "document"


def _terms(value: str, aliases: bool = True) -> list[str]:
    """Normalize bounded word tokens; aliases bridge ordinary domain wording."""
    terms = [token for token in TOKEN_RE.findall(value.lower()) if len(token) > 1 and token not in STOP_WORDS]
    if aliases:
        terms.extend(alias for term in terms for alias in TERM_ALIASES.get(term, ()))
    return sorted(set(terms))


def _concept_span(words: list[str], concepts: dict[str, tuple[str, ...]]) -> int:
    """Return the smallest word window covering every query concept."""
    occurrences = sorted(
        (position, concept)
        for position, word in enumerate(words)
        for concept, variants in concepts.items()
        if word in variants
    )
    needed = len(concepts)
    counts: Counter[str] = Counter()
    covered = 0
    left = 0
    best = len(words)
    for right, (position, concept) in enumerate(occurrences):
        if counts[concept] == 0:
            covered += 1
        counts[concept] += 1
        while covered == needed:
            best = min(best, position - occurrences[left][0])
            left_concept = occurrences[left][1]
            counts[left_concept] -= 1
            if counts[left_concept] == 0:
                covered -= 1
            left += 1
    return best


def _actor_heading(rel: str, title: str) -> tuple[str | None, int | None]:
    if rel != "plan/TGW-Taskboard.md":
        return None, None
    match = ACTOR_HEADING_RE.fullmatch(title.strip())
    return (match.group(1).lower(), int(match.group(2))) if match else (None, None)


def _parse_document(rel: str, raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{rel}: malformed UTF-8: {exc}") from exc
    lines = text.splitlines()
    digest = _sha(raw)
    sections: list[dict[str, Any]] = []
    stack: list[tuple[int, int]] = []
    current = -1
    fence: str | None = None
    malformed: list[str] = []
    for pos, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
        match = HEADING_RE.match(line) if fence is None else None
        if match:
            level, title = len(match.group(1)), match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else None
            current = len(sections)
            sections.append(
                {"title": title, "level": level, "line": pos, "end_line": len(lines), "parent": parent}
            )
            if parent is not None:
                sections[parent]["end_line"] = pos - 1
            stack.append((level, current))
    if fence:
        malformed.append("unclosed fenced code block")
    for index, section in enumerate(sections):
        later = [s["line"] for s in sections[index + 1 :] if s["level"] <= section["level"]]
        section["end_line"] = min(later) - 1 if later else len(lines)
    return {
        "path": rel,
        "raw": raw,
        "text": text,
        "lines": lines,
        "sha256": digest,
        "sections": sections,
        "malformed": malformed,
    }


def _source_docs(corpus: Path, allowlist: Path,
                 bound: dict[str, bytes] | None = None) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    paths = _paths(allowlist)
    docs, missing = [], []
    for rel in paths:
        path = corpus / rel
        if bound is not None and rel in bound:
            docs.append(_parse_document(rel, bound[rel]))
        elif not path.is_file():
            missing.append(rel)
        else:
            docs.append(_parse_document(rel, path.read_bytes()))
    return paths, docs, missing


def _build_unchecked(corpus: Path, allowlist: Path, output: Path,
                     envelope_digest: str | None = None,
                     source_bytes: dict[str, bytes] | None = None,
                     source_revision: str = SOURCE_REVISION) -> dict[str, Any]:
    """Build deterministic manifest, graph, search index, and coverage ledger."""
    paths, docs, missing = _source_docs(corpus, allowlist, source_bytes)
    nodes: dict[str, dict[str, Any]] = {}
    edges: set[tuple[str, str, str, str]] = set()
    index_entries: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    parsed: list[str] = []
    unreviewed: list[dict[str, str]] = []

    def edge(source: str, relation: str, target: str, anchor: str) -> None:
        if source != target:
            edges.add((source, relation, target, anchor))

    for doc in docs:
        rel, lines, digest = doc["path"], doc["lines"], doc["sha256"]
        doc_id = f"doc:{rel}"
        manifest.append(
            {
                "path": rel,
                "canonical_path": str((corpus / rel).resolve()),
                "sha256": digest,
                "bytes": len(doc["raw"]),
                "lines": len(lines),
                "source_revision": source_revision,
                "parsed": not doc["malformed"],
                "indexed": True,
            }
        )
        if not doc["malformed"]:
            parsed.append(rel)
        for problem in doc["malformed"]:
            unreviewed.append({"path": rel, "reason": problem})
        _add_node(
            nodes,
            doc_id,
            type="document",
            document_kind=_node_type(rel),
            path=rel,
            sha256=digest,
            source_revision=source_revision,
            derived=False,
        )
        document_identity = (
            Path(rel).stem.upper() if _node_type(rel) == "detailed-pp" else None
        )
        if document_identity and PP_RE.fullmatch(document_identity):
            index_entries.append(
                {
                    "id": doc_id,
                    "type": "document",
                    "title": document_identity,
                    "identity": document_identity,
                    "title_normalized": " ".join(TOKEN_RE.findall(document_identity.lower())),
                    "normalized": " ".join(TOKEN_RE.findall(doc["text"].lower())),
                    "token_count": len(TOKEN_RE.findall(doc["text"].lower())),
                    "structural_container": False,
                    "citation": _citation(rel, 1, document_identity, digest, source_revision),
                }
            )
        heading_nodes: dict[int, str] = {}
        for idx, section in enumerate(doc["sections"]):
            node_id = f"heading:{rel}#{section['line']}"
            heading_nodes[idx] = node_id
            cite = _citation(rel, section["line"], section["title"], digest, source_revision)
            actor, open_count = _actor_heading(rel, section["title"])
            _add_node(
                nodes,
                node_id,
                type="heading",
                title=section["title"],
                level=section["level"],
                citation=cite,
                structural_container=bool(actor),
                assigned_actor=actor,
                open_count=open_count,
            )
            edge(doc_id, "CONTAINS", node_id, f"{rel}:{section['line']}")
            if section["parent"] is not None:
                edge(heading_nodes[section["parent"]], "PARENT_OF", node_id, f"{rel}:{section['line']}")
            token = section["title"].split(maxsplit=1)[0].strip("—:-`[]()") if section["title"] else ""
            if INVARIANT_TOKEN_RE.fullmatch(token):
                inv_id = f"invariant:{token.upper()}"
                _add_node(nodes, inv_id, type="invariant", name=token.upper(), title=section["title"])
                nodes[inv_id].setdefault("citations", [])
                if cite not in nodes[inv_id]["citations"]:
                    nodes[inv_id]["citations"].append(cite)
                edge(node_id, "DEFINES", inv_id, f"{rel}:{section['line']}")
            next_heading = (
                doc["sections"][idx + 1]["line"] - 1
                if idx + 1 < len(doc["sections"])
                else len(lines)
            )
            chunk = "\n".join(lines[section["line"] - 1 : next_heading])
            heading_identity_match = PP_RE.match(section["title"])
            index_entries.append(
                {
                    "id": node_id,
                    "type": "heading",
                    "title": section["title"],
                    "title_normalized": " ".join(TOKEN_RE.findall(section["title"].lower())),
                    "normalized": " ".join(TOKEN_RE.findall(chunk.lower())),
                    "token_count": len(TOKEN_RE.findall(chunk.lower())),
                    "structural_container": bool(actor),
                    "assigned_actor": actor,
                    "identity": (
                        heading_identity_match.group(0).upper()
                        if heading_identity_match
                        else None
                    ),
                    "identifiers_mentioned": sorted(
                        set(x.upper() for x in PP_RE.findall(chunk))
                    ),
                    "citation": cite,
                }
            )

        current_heading = doc_id
        current_actor: str | None = None
        heading_by_line = {s["line"]: heading_nodes[i] for i, s in enumerate(doc["sections"])}
        title_by_line = {s["line"]: s["title"] for s in doc["sections"]}
        current_title = "(document)"
        for line_no, line in enumerate(lines, 1):
            if line_no in heading_by_line:
                current_heading = heading_by_line[line_no]
                current_title = title_by_line[line_no]
                current_actor, _ = _actor_heading(rel, current_title)
            cite = _citation(rel, line_no, current_title, digest, source_revision)
            pps_on_line = sorted(set(x.upper() for x in PP_RE.findall(line)))
            invs_on_line = sorted(set(x.upper() for x in INVARIANT_REF_RE.findall(line)))
            todo_match = TODO_ROW_RE.match(line) if rel.endswith("TGW-Taskboard.md") else None
            anchor_id = f"anchor:{rel}:{line_no}"
            if pps_on_line or invs_on_line or todo_match:
                _add_node(nodes, anchor_id, type="source-anchor", citation=cite)
                edge(current_heading, "CONTAINS", anchor_id, f"{rel}:{line_no}")
            for pp in pps_on_line:
                pp_id = f"pp:{pp}"
                _add_node(nodes, pp_id, type="pp", pp=pp)
                nodes[pp_id].setdefault("citations", [])
                if cite not in nodes[pp_id]["citations"]:
                    nodes[pp_id]["citations"].append(cite)
                edge(anchor_id, "MENTIONS", pp_id, f"{rel}:{line_no}")
                if _node_type(rel) == "detailed-pp":
                    edge(doc_id, "DEFINES", pp_id, f"{rel}:{line_no}")
            for inv in invs_on_line:
                inv_id = f"invariant:{inv}"
                if inv_id in nodes:
                    edge(anchor_id, "MENTIONS", inv_id, f"{rel}:{line_no}")
            if todo_match:
                todo = todo_match.group(1)
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                todo_id = f"todo:{todo}"
                _add_node(
                    nodes,
                    todo_id,
                    type="todo",
                    todo_id=int(todo),
                    row=cells,
                    citations=[cite],
                    assigned_actor=current_actor,
                )
                edge(doc_id, "CONTAINS", todo_id, f"{rel}:{line_no}")
                edge(anchor_id, "DEFINES", todo_id, f"{rel}:{line_no}")
                for pp in sorted(set(x.upper() for x in PP_RE.findall(line))):
                    edge(todo_id, "GOVERNED_BY", f"pp:{pp}", f"{rel}:{line_no}")
                index_entries.append(
                    {
                        "id": todo_id,
                        "type": "todo",
                        "title": f"Todo #{todo}",
                        "title_normalized": f"todo {todo}",
                        "normalized": " ".join(TOKEN_RE.findall(line.lower())),
                        "token_count": len(TOKEN_RE.findall(line.lower())),
                        "structural_container": False,
                        "assigned_actor": current_actor,
                        "identifiers_mentioned": sorted(
                            set(x.upper() for x in PP_RE.findall(line))
                        ),
                        "citation": cite,
                    }
                )

    for node in nodes.values():
        if "citations" in node:
            node["citations"] = sorted(node["citations"], key=lambda x: (x["path"], x["line"]))
    graph = {
        "schema": SCHEMA,
        "derived": True,
        "authority_warning": WARNING,
        "source_revision": source_revision,
        "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
        "edges": [
            {"source": s, "relation": r, "target": t, "anchor": a}
            for s, r, t, a in sorted(edges)
        ],
    }
    index = {
        "schema": "tgw-plan-search-pilot-v3",
        "derived": True,
        "authority_warning": WARNING,
        "source_revision": source_revision,
        "entries": sorted(index_entries, key=lambda x: x["id"]),
    }
    node_counts = Counter(n["type"] for n in graph["nodes"])
    edge_counts = Counter(e["relation"] for e in graph["edges"])
    ledger = {
        "schema": "tgw-plan-coverage-pilot-v3",
        "derived": True,
        "source_revision": source_revision,
        "allowlisted": paths,
        "present": sorted(d["path"] for d in docs),
        "parsed": sorted(parsed),
        "indexed": sorted(d["path"] for d in docs),
        "missing": missing,
        "changed_since_build": [],
        "unreviewed": sorted(unreviewed, key=lambda x: (x["path"], x["reason"])),
        "stale": False,
        "node_counts": dict(sorted(node_counts.items())),
        "edge_counts": dict(sorted(edge_counts.items())),
        "limitations": [
            "Pilot corpus is allowlisted, not the complete Plan Vault.",
            "Edges are deterministic textual relationships, not inferred semantic claims.",
            SNAPSHOT_WARNING,
            "No live services, databases, model extraction, embeddings, or canonical-source mutation occurred.",
        ],
    }
    if envelope_digest:
        graph["source_envelope"] = envelope_digest
        index["source_envelope"] = envelope_digest
        ledger["source_envelope"] = envelope_digest
        for row in manifest:
            row["source_envelope"] = envelope_digest
        for value in (graph, index):
            for entry in value.get("entries", []) + value.get("nodes", []):
                citation = entry.get("citation")
                if citation:
                    citation["source_envelope"] = envelope_digest
                for citation in entry.get("citations", []):
                    citation["source_envelope"] = envelope_digest
    output.mkdir(parents=True, exist_ok=True)
    _json(output / "source-manifest.json", manifest)
    _json(output / "plan-graph.json", graph)
    _json(output / "search-index.json", index)
    _json(output / "coverage-ledger.json", ledger)
    return {
        "output": str(output),
        "manifest": len(manifest),
        "missing": missing,
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "stale": False,
        "source_envelope": envelope_digest,
    }


def build(corpus: Path, allowlist: Path, output: Path,
          source_envelope: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate all sources, build privately, then atomically publish a v3 set."""
    if source_envelope is None:
        raise SourcePreconditionError("envelope_invalid")
    digest, source_bytes = _validate_sources(corpus, allowlist, source_envelope)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.build-", dir=output.parent))
    backup: Path | None = None
    try:
        result = _build_unchecked(
            corpus, allowlist, temporary, digest, source_bytes,
            source_revision=source_envelope["head"],
        )
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{output.name}.old-", dir=output.parent))
            backup.rmdir()
            os.replace(output, backup)
        try:
            os.replace(temporary, output)
        except BaseException:
            if backup is not None:
                os.replace(backup, output)
            raise
        if backup is not None:
            shutil.rmtree(backup)
        result["output"] = str(output)
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _load(output: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    try:
        graph = json.loads((output / "plan-graph.json").read_text(encoding="utf-8"))
        index = json.loads((output / "search-index.json").read_text(encoding="utf-8"))
        manifest = json.loads((output / "source-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"missing or invalid build artifacts: {exc}") from exc
    return graph, index, manifest


def _preflight_artifacts(output: Path, digest: str) -> None:
    try:
        graph, index, manifest = _load(output)
        ledger = json.loads((output / "coverage-ledger.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SourcePreconditionError("artifact_schema_unsupported") from exc
    if (graph.get("schema") != SCHEMA or index.get("schema") != "tgw-plan-search-pilot-v3"
            or not isinstance(ledger, dict) or ledger.get("schema") != "tgw-plan-coverage-pilot-v3"):
        raise SourcePreconditionError("artifact_schema_unsupported")
    bindings = [graph.get("source_envelope"), index.get("source_envelope"), ledger.get("source_envelope")]
    bindings.extend(row.get("source_envelope") for row in manifest if isinstance(row, dict))
    if not manifest or any(value != digest for value in bindings):
        raise SourcePreconditionError("artifact_envelope_mismatch", expected=digest)


def coverage(corpus: Path, allowlist: Path, output: Path,
             source_envelope: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compare current sources with the build manifest."""
    if source_envelope is None:
        raise SourcePreconditionError("envelope_invalid")
    digest, source_bytes = _validate_sources(corpus, allowlist, source_envelope)
    _preflight_artifacts(output, digest)
    paths, docs, missing = _source_docs(corpus, allowlist, source_bytes)
    manifest_path = output / "source-manifest.json"
    built: dict[str, dict[str, Any]] = {}
    artifact_error = ""
    try:
        built = {x["path"]: x for x in json.loads(manifest_path.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        artifact_error = str(exc)
    current = {d["path"]: d for d in docs}
    changed = sorted(
        rel for rel in set(current) & set(built) if current[rel]["sha256"] != built[rel].get("sha256")
    )
    unreviewed = [
        {"path": d["path"], "reason": reason} for d in docs for reason in d["malformed"]
    ]
    if artifact_error:
        unreviewed.append({"path": "artifacts", "reason": f"no valid build manifest: {artifact_error}"})
    not_in_build = sorted(set(current) - set(built))
    unreviewed.extend({"path": x, "reason": "present but absent from build manifest"} for x in not_in_build)
    stale = bool(missing or changed or unreviewed or set(paths) != set(built))
    return {
        "schema": "tgw-plan-coverage-check-v3",
        "derived": True,
        "source_revision": source_envelope["head"],
        "allowlisted": paths,
        "present": sorted(current),
        "parsed": sorted(d["path"] for d in docs if not d["malformed"]),
        "indexed": sorted(set(current) & set(built)),
        "missing": missing,
        "changed_since_build": changed,
        "unreviewed": sorted(unreviewed, key=lambda x: (x["path"], x["reason"])),
        "stale": stale,
        "taskboard_warning": SNAPSHOT_WARNING,
        "authority_warning": WARNING,
        "source_envelope": digest,
    }


def _kind(term: str) -> tuple[str, str]:
    value = term.strip()
    if PP_RE.fullmatch(value):
        return "pp", value.upper()
    todo = re.fullmatch(r"(?:todo\s*)?#?(\d+)", value, re.I)
    if todo:
        return "todo", todo.group(1)
    if INVARIANT_TOKEN_RE.fullmatch(value):
        return "invariant", value.upper()
    if ASSIGNMENT_RE.search(value):
        return "assignment", value
    if len(value.split()) > 1:
        return "phrase", value
    return "keywords", value


def _excerpt(corpus: Path, cite: dict[str, Any], radius: int = 2,
             source_bytes: dict[str, bytes] | None = None) -> str:
    path = corpus / cite["path"]
    raw = source_bytes[cite["path"]] if source_bytes is not None else path.read_bytes()
    if _sha(raw) != cite["source_sha256"]:
        raise ValueError(f"source changed since build: {cite['path']}")
    lines = raw.decode("utf-8").splitlines()
    line = int(cite["line"])
    return "\n".join(lines[max(0, line - radius - 1) : min(len(lines), line + radius)]).strip()


def _paths_from(graph: dict[str, Any], starts: Iterable[str], depth: int = 1) -> list[dict[str, Any]]:
    """Return bounded typed evidence edges without crossing containment hubs."""
    adjacency: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    nodes = {node["id"]: node for node in graph["nodes"]}
    for edge in graph["edges"]:
        if edge["relation"] not in {"DEFINES", "MENTIONS", "GOVERNED_BY", "PARENT_OF"}:
            continue
        if nodes.get(edge["source"], {}).get("structural_container"):
            continue
        adjacency[edge["source"]].append((edge["target"], edge["relation"], edge["anchor"]))
        adjacency[edge["target"]].append((edge["source"], f"INVERSE_{edge['relation']}", edge["anchor"]))
    found = []
    for source in sorted(set(starts)):
        for target, relation, anchor in sorted(adjacency[source]):
            found.append(
                {
                    "nodes": [source, target],
                    "edges": [{"source": source, "relation": relation, "target": target, "anchor": anchor}],
                }
            )
    return found


def _ranked_candidates(
    index: dict[str, Any], term: str
) -> tuple[str, list[tuple[int, str, dict[str, Any], dict[str, Any]]]]:
    """Rank the complete relevant pool without applying a presentation cap."""
    kind, needle = _kind(term)
    tokens = _terms(needle, aliases=False)
    actor_tokens = set(_terms(needle, aliases=False))
    candidates: list[tuple[int, str, dict[str, Any], dict[str, Any]]] = []
    for entry in index["entries"]:
        if entry["type"] == "document" and kind != "pp":
            continue
        structural = bool(entry.get("structural_container"))
        if structural and kind != "assignment":
            continue
        words = entry["normalized"].split()
        counts = Counter(words)
        title_words = set(entry.get("title_normalized", "").split())
        length = max(1, int(entry.get("token_count", len(words))))
        score = 0
        evidence: dict[str, Any] = {}
        if kind == "todo" and entry["id"] == f"todo:{needle}":
            score = IDENTITY_SCORES["exact_identifier"]
            evidence = {"match": "exact_identifier"}
        elif kind == "pp" and entry.get("identity") == needle:
            score = IDENTITY_SCORES["exact_identifier"]
            evidence = {"match": "exact_identifier"}
        elif (
            kind == "pp"
            and not entry.get("identity")
            and needle in entry.get("identifiers_mentioned", [])
        ):
            score = IDENTITY_SCORES["mentions_identifier"]
            evidence = {"match": "mentions_identifier", "identifier": needle}
        elif kind == "invariant" and needle.lower() in words:
            first_title_word = entry.get("title_normalized", "").split()[:1]
            score = 8000 + (500 if first_title_word == [needle.lower()] else 0)
            evidence = {"match": "exact_identifier"}
        elif kind == "assignment":
            actor = entry.get("assigned_actor")
            actor_match = bool(actor and actor in actor_tokens)
            if actor_match:
                score = 7000 + (500 if structural else 0)
                evidence = {"match": "assignment", "actor": actor}
        elif tokens and kind in {"phrase", "keywords"}:
            concepts = {
                token: (token,) + TERM_ALIASES.get(token, ())
                for token in tokens
            }
            matched = [
                token for token, variants in concepts.items()
                if any(counts[variant] for variant in variants)
            ]
            # Default retrieval has a documented relevance floor: every
            # significant concept in a multi-token query must be present in a
            # bounded lexical window, not merely somewhere in a long section.
            proximity_span = (
                _concept_span(words, concepts)
                if len(matched) == len(tokens)
                else length
            )
            proximity_limit = max(12, 4 * len(tokens))
            bounded_section = length <= 500 or all(
                any(variant in title_words for variant in concepts[token])
                for token in tokens
            )
            if (
                len(matched) == len(tokens)
                and proximity_span <= proximity_limit
                and bounded_section
            ):
                coverage_score = len(matched) / len(tokens)
                density = sum(
                    min(sum(counts[v] for v in concepts[token]), 3)
                    for token in matched
                ) / length
                title_hits = sum(
                    any(v in title_words for v in concepts[token])
                    for token in matched
                )
                score = int(1000 * coverage_score + 600 * density + 120 * title_hits)
                score += 500
                evidence = {
                    "match": "strong_normalized_terms",
                    "matched_terms": matched,
                    "query_term_count": len(tokens),
                    "document_token_count": length,
                    "relevance_floor": "all_significant_query_concepts",
                    "proximity_span": proximity_span,
                    "proximity_limit": proximity_limit,
                    "section_token_limit": 500,
                }
        if score:
            candidates.append((score, entry["id"], entry, evidence))
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return kind, candidates


def _render_candidate(
    corpus: Path,
    graph: dict[str, Any],
    candidate: tuple[int, str, dict[str, Any], dict[str, Any]],
    source_bytes: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    score, _, entry, evidence = candidate
    return {
        "score": score,
        "node_id": entry["id"],
        "type": entry["type"],
        "title": entry["title"],
        "citation": entry["citation"],
        "excerpt": _excerpt(corpus, entry["citation"], source_bytes=source_bytes),
        "structural_container": bool(entry.get("structural_container")),
        "assigned_actor": entry.get("assigned_actor"),
        "score_evidence": evidence,
        "relationship_paths": _paths_from(graph, [entry["id"]])[:6],
    }


def query(corpus: Path, output: Path, term: str, limit: int = 10,
          source_envelope: dict[str, Any] | None = None,
          allowlist: Path | None = None) -> dict[str, Any]:
    """Return deterministic capped results, re-reading cited source excerpts."""
    if source_envelope is None:
        raise SourcePreconditionError("envelope_invalid")
    digest, source_bytes = _validate_sources(
        corpus, allowlist or corpus.parent / "allowlist.txt", source_envelope,
    )
    _preflight_artifacts(output, digest)
    graph, index, _ = _load(output)
    kind, candidates = _ranked_candidates(index, term)
    public_candidates = candidates[: max(0, limit)]
    results = [_render_candidate(corpus, graph, candidate, source_bytes) for candidate in public_candidates]
    top = candidates[0][0] if candidates else None
    ambiguity = bool(
        kind not in {"pp", "todo", "invariant"}
        and candidates
        and sum(score == top for score, *_ in candidates) > 1
    )
    boundary = len(public_candidates)
    cutoff_score = public_candidates[-1][0] if public_candidates else None
    omitted_equal = [
        candidate[1]
        for candidate in candidates[boundary:]
        if cutoff_score is not None and candidate[0] == cutoff_score
    ]
    return {
        "schema": "tgw-plan-query-pilot-v3",
        "derived": True,
        "authority_warning": WARNING,
        "source_revision": source_envelope["head"],
        "source_envelope": digest,
        "query": term,
        "query_type": kind,
        "ambiguous": ambiguity,
        "results": results,
        "coverage": {
            "candidate_count": len(candidates),
            "returned": len(results),
            "limit": limit,
            "omitted": max(0, len(candidates) - len(results)),
            "truncated": len(candidates) > len(results),
            "stop_words": sorted(STOP_WORDS),
            "relevance_floor": "all significant query concepts; identifiers use identity or literal mention",
            "low_confidence_expansion": "not enabled",
            "tie_cut": {
                "cut": bool(omitted_equal),
                "cutoff_score": cutoff_score,
                "omitted_equal_score_count": len(omitted_equal),
                "omitted_identities": omitted_equal,
                "ordering_note": (
                    "Node-ID ordering is deterministic presentation only, not semantic relevance."
                ),
            },
        },
    }


def brief(corpus: Path, output: Path, task: str, limit: int = 12,
          source_envelope: dict[str, Any] | None = None,
          allowlist: Path | None = None) -> dict[str, Any]:
    """Produce a capped brief from ranked sources and explicit typed edges."""
    if source_envelope is None:
        raise SourcePreconditionError("envelope_invalid")
    digest, source_bytes = _validate_sources(
        corpus, allowlist or corpus.parent / "allowlist.txt", source_envelope,
    )
    _preflight_artifacts(output, digest)
    graph, index, manifest = _load(output)
    q = query(
        corpus, output, task, limit,
        source_envelope=source_envelope, allowlist=allowlist,
    )
    _, full_candidates = _ranked_candidates(index, task)
    full_results = [_render_candidate(corpus, graph, candidate, source_bytes) for candidate in full_candidates]
    nodes = {n["id"]: n for n in graph["nodes"]}
    seed_ids = [r["node_id"] for r in full_results]
    paths = _paths_from(graph, seed_ids)
    categories: dict[str, list[dict[str, Any]]] = {
        "master_plan_sections": [],
        "detailed_pp_documents": [],
        "linked_todos": [],
        "referenced_invariants": [],
        "linked_reference_documents": [],
    }
    scored: dict[str, list[tuple[int, dict[str, Any]]]] = {key: [] for key in categories}

    def add(category: str, score: int, item: dict[str, Any]) -> None:
        scored[category].append((score, item))

    for result in full_results:
        cite = result["citation"]
        score = result["score"]
        item = {
            "node_id": result["node_id"],
            "title": result["title"],
            "relevance_score": score,
            "citation": cite,
            "excerpt": result["excerpt"],
            "relevance_evidence": result["score_evidence"],
        }
        if cite["path"] == "plan/TGW-Master-Plan.md":
            add("master_plan_sections", score, item)
        result_doc = nodes.get(f"doc:{cite['path']}", {})
        identifier_identity = (
            q["query_type"] == "pp"
            and result["score_evidence"].get("match") == "exact_identifier"
        )
        if result_doc.get("document_kind") == "detailed-pp" and (
            q["query_type"] != "pp" or identifier_identity
        ):
            add(
                "detailed_pp_documents",
                score,
                {
                    "node_id": result_doc["id"],
                    "path": result_doc["path"],
                    "sha256": result_doc["sha256"],
                    "relevance_score": score,
                    "source_candidate": result["node_id"],
                    "relevance_evidence": result["score_evidence"],
                },
            )
        elif result_doc.get("document_kind") in ("reference", "audit", "runbook"):
            add(
                "linked_reference_documents",
                score,
                {
                    "node_id": result_doc["id"],
                    "path": result_doc["path"],
                    "kind": result_doc["document_kind"],
                    "sha256": result_doc["sha256"],
                    "relevance_score": score,
                    "source_candidate": result["node_id"],
                    "relevance_evidence": result["score_evidence"],
                }
            )
        node = nodes.get(result["node_id"], {})
        if node.get("type") == "todo":
            for cite in node.get("citations", []):
                add(
                    "linked_todos",
                    score,
                    {
                        "node_id": node["id"],
                        "title": f"Todo #{node['todo_id']}",
                        "relevance_score": score,
                        "assigned_actor": node.get("assigned_actor"),
                        "citation": cite,
                        "excerpt": _excerpt(corpus, cite),
                        "relevance_evidence": result["score_evidence"],
                    },
                )
        invariant_name = result["title"].split(maxsplit=1)[0].strip("—:-`[]()").upper()
        invariant = nodes.get(f"invariant:{invariant_name}", {})
        if invariant:
            for invariant_cite in invariant.get("citations", [])[:1]:
                add(
                    "referenced_invariants",
                    score,
                    {
                        "node_id": invariant["id"],
                        "title": invariant.get("title", invariant["id"]),
                        "relevance_score": score,
                        "citation": invariant_cite,
                        "excerpt": _excerpt(corpus, invariant_cite),
                        "relevance_evidence": result["score_evidence"],
                    },
                )

    category_coverage: dict[str, dict[str, Any]] = {}
    for key, values in scored.items():
        unique: dict[str, tuple[int, dict[str, Any]]] = {}
        for score, item in values:
            identity = item.get("node_id", item.get("path", json.dumps(item, sort_keys=True)))
            if identity not in unique or score > unique[identity][0]:
                unique[identity] = (score, item)
        ranked = sorted(unique.values(), key=lambda pair: (-pair[0], pair[1].get("node_id", "")))
        cap = BRIEF_CAPS[key]
        categories[key] = [item for _, item in ranked[:cap]]
        cutoff_score = ranked[cap - 1][0] if len(ranked) >= cap else None
        omitted_equal = [
            item.get("node_id", item.get("path", ""))
            for score, item in ranked[cap:]
            if cutoff_score is not None and score == cutoff_score
        ]
        category_coverage[key] = {
            "available": len(ranked),
            "returned": len(categories[key]),
            "cap": cap,
            "omitted": max(0, len(ranked) - cap),
            "truncated": len(ranked) > cap,
            "pool": "full_ranked_candidates_with_direct_typed_projection",
            "relevance_floor": "strong default evidence only; no positive-score backfill",
            "tie_cut": {
                "cut": bool(omitted_equal),
                "cutoff_score": cutoff_score,
                "omitted_equal_score_count": len(omitted_equal),
                "omitted_identities": omitted_equal,
                "ordering_note": "Node-ID ordering is deterministic presentation only.",
            },
            "omitted_by_stage": {
                "query_candidate_cap": 0,
                "category_cap": max(0, len(ranked) - cap),
                "typed_expansion_boundary": 0,
            },
        }

    gaps = []
    if not categories["master_plan_sections"]:
        gaps.append("No matching Master Plan section was established.")
    if not categories["detailed_pp_documents"]:
        gaps.append("No linked detailed PP document was established in the allowlisted corpus.")
    if not categories["linked_todos"]:
        gaps.append("No linked Todo was established in the frozen Taskboard snapshot.")
    if not categories["referenced_invariants"]:
        gaps.append("No invariant relationship was established by explicit textual edges.")
    if q["ambiguous"] or not q["results"]:
        gaps.append("Input is ambiguous or unmatched; multiple candidates are retained and no single interpretation was selected.")
    gaps.extend(
        [
            "The allowlisted 14-document corpus is incomplete relative to the full Plan Vault.",
            SNAPSHOT_WARNING,
            "Absence from this brief means unknown in this corpus, not absent from canonical or live state.",
        ]
    )
    candidates = [
        {
            "node_id": r["node_id"],
            "title": r["title"],
            "score": r["score"],
            "citation": r["citation"],
            "score_evidence": r["score_evidence"],
        }
        for r in q["results"][: BRIEF_CAPS["candidates"]]
    ]
    selected_paths = paths[: BRIEF_CAPS["relationship_paths"]]
    truncation = q["coverage"]["truncated"] or any(x["truncated"] for x in category_coverage.values()) or len(paths) > len(selected_paths)
    category_cap_omissions = sum(
        row["omitted_by_stage"]["category_cap"] for row in category_coverage.values()
    )
    return {
        "schema": "tgw-plan-brief-pilot-v3",
        "derived": True,
        "authority_warning": WARNING,
        "source_revision": source_envelope["head"],
        "source_envelope": digest,
        "task": task,
        "status": "ambiguous" if q["ambiguous"] else "matched" if q["results"] else "unknown",
        "candidates": candidates,
        **categories,
        "relationship_paths": selected_paths,
        "retrieval_coverage": {
            "query": q["coverage"],
            "categories": category_coverage,
            "relationship_paths": {
                "available": len(paths),
                "returned": len(selected_paths),
                "cap": BRIEF_CAPS["relationship_paths"],
                "omitted": max(0, len(paths) - len(selected_paths)),
                "truncated": len(paths) > len(selected_paths),
                "pool": "full_ranked_candidate_direct_typed_edges",
                "omitted_by_stage": {
                    "query_candidate_cap": 0,
                    "relationship_path_cap": max(0, len(paths) - len(selected_paths)),
                    "typed_expansion_boundary": 0,
                },
            },
            "omitted_by_stage": {
                "public_query_candidate_cap": q["coverage"]["omitted"],
                "category_caps": category_cap_omissions,
                "relationship_path_cap": max(0, len(paths) - len(selected_paths)),
                "typed_expansion_boundary": 0,
            },
            "truncated": truncation,
            "gap_policy": "Omitted or absent material is unknown beyond this allowlisted snapshot.",
        },
        "corpus_gaps_and_unknowns": gaps,
        "source_hashes": {x["path"]: x["sha256"] for x in manifest},
        "human_summary": _human_brief(task, q, categories, gaps),
    }


def _human_brief(task: str, q: dict[str, Any], categories: dict[str, list[dict[str, Any]]], gaps: list[str]) -> str:
    status = "AMBIGUOUS—do not silently choose" if q["ambiguous"] else "MATCHED" if q["results"] else "UNKNOWN"
    candidate_text = "; ".join(f"{r['title']} ({r['citation']['path']}:{r['citation']['line']})" for r in q["results"][:5]) or "none"
    return (
        f"Task-start brief: {task}\nStatus: {status}\nCandidates: {candidate_text}\n"
        f"Master Plan matches: {len(categories['master_plan_sections'])}; detailed PPs: "
        f"{len(categories['detailed_pp_documents'])}; Todos: {len(categories['linked_todos'])}; "
        f"invariants: {len(categories['referenced_invariants'])}; references/audits/runbooks: "
        f"{len(categories['linked_reference_documents'])}.\nUnknowns: {' '.join(gaps)}"
    )
