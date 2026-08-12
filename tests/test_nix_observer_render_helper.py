import hashlib
import io
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tgw import nix_observer_render_helper as helper
from tgw.nix_observer_render_evaluation import OUTPUTS, SCHEMA, canonical


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _local_tool_authority() -> helper.ToolAuthority:
    path = Path("/usr/bin/git")
    metadata = path.stat()
    return helper.ToolAuthority(
        authority_receipt_sha256="sha256:" + "9" * 64,
        path=str(path),
        sha256=_digest(path.read_bytes()),
        bytes=metadata.st_size,
        owner_uid=metadata.st_uid,
        mode=metadata.st_mode & 0o7777,
        require_nix_store=False,
        forbid_owner_write=False,
    )


LOCAL_TOOL_AUTHORITY = _local_tool_authority()


def _authority_literal(authority: helper.ToolAuthority = LOCAL_TOOL_AUTHORITY) -> str:
    return repr(
        {
            "authority_receipt_sha256": authority.authority_receipt_sha256,
            "path": authority.path,
            "sha256": authority.sha256,
            "bytes": authority.bytes,
            "owner_uid": authority.owner_uid,
            "mode": authority.mode,
            "require_nix_store": authority.require_nix_store,
            "forbid_owner_write": authority.forbid_owner_write,
        }
    )


def _a2_authority_literal(authority: helper.A2Authority) -> str:
    return repr(
        {
            "a1_prerequisite_receipt_sha256": authority.a1_prerequisite_receipt_sha256,
            "a2_prerequisite_receipt_sha256": authority.a2_prerequisite_receipt_sha256,
            "nix_path": authority.nix_path,
            "nix_sha256": authority.nix_sha256,
            "nix_store_path": authority.nix_store_path,
            "nix_store_sha256": authority.nix_store_sha256,
            "systemd_analyze_path": authority.systemd_analyze_path,
            "systemd_analyze_sha256": authority.systemd_analyze_sha256,
            "systemd_analyze_version": authority.systemd_analyze_version,
            "systemd_analyze_version_stdout_sha256": authority.systemd_analyze_version_stdout_sha256,
            "systemd_analyze_version_stdout_bytes": authority.systemd_analyze_version_stdout_bytes,
            "input_path": authority.input_path,
            "input_nar_sha256": authority.input_nar_sha256,
            "store_root": authority.store_root,
            "owner_uid": authority.owner_uid,
            "mode": authority.mode,
            "require_nix_store": authority.require_nix_store,
            "allow_mutable_parents": authority.allow_mutable_parents,
            "held_input_path": authority.held_input_path,
            "derivation_store_root": authority.derivation_store_root,
            "scratch_root": authority.scratch_root,
        }
    )


def _source_files() -> dict[str, bytes]:
    return {
        "flake.lock": b'{"nodes":{},"root":"root","version":7}\n',
        "flake.nix": b"{ outputs = _: {}; }\n",
        "nix/nix-input-observer-launcher.nix": b"{ ... }: {}\n",
        "src/native/tgw_nix_input_observer_launcher.c": b"int main(void) { return 0; }\n",
        "src/tgw/nix_input_observation.py": b"# observer source\n",
        "src/tgw/nix_observer_render_evaluation.py": b"# provider source\n",
    }


def _write_source(root: Path, files: dict[str, bytes]) -> None:
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def _tree_hash(root: Path) -> str:
    subprocess.run(["/usr/bin/git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["/usr/bin/git", "add", "-f", "-A"], cwd=root, check=True)
    return subprocess.run(["/usr/bin/git", "write-tree"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _archive(path: Path, files: dict[str, bytes], *, commit: str, mutation: str | None = None) -> None:
    pax = {} if mutation == "missing-pax" else {"comment": "f" * 40 if mutation == "wrong-pax" else commit}
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT, pax_headers=pax) as archive:
        directories = {
            helper.ARCHIVE_ROOT,
            *(
                helper.ARCHIVE_ROOT + "/" + str(parent)
                for name in files
                for parent in Path(name).parents
                if str(parent) != "."
            ),
        }
        for directory in sorted(directories, key=lambda item: (item.count("/"), item)):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for name, raw in sorted(files.items()):
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/" + name)
            info.size = len(raw)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(raw))
        if mutation == "duplicate":
            raw = files["flake.lock"]
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/flake.lock")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
        elif mutation == "dotdot":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/../escape")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        elif mutation == "dot":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/./alias")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        elif mutation == "double-slash":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "//alias")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        elif mutation == "dotgit":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/.git/config")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        elif mutation == "symlink":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
        elif mutation == "other-root":
            info = tarfile.TarInfo("other-root")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)


def _request(files: dict[str, bytes], archive: Path, *, tree: str) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": SCHEMA,
        "plan_commit": "a" * 40,
        "source_commit": "b" * 40,
        "source_tree": tree,
        "artifact_ref": "artifact:" + _digest(archive.read_bytes()),
        "archive_sha256": _digest(archive.read_bytes()),
        "flake_lock_sha256": _digest(files["flake.lock"]),
        "flake_sha256": _digest(files["flake.nix"]),
        "module_sha256": _digest(files["nix/nix-input-observer-launcher.nix"]),
        "launcher_source_sha256": _digest(files["src/native/tgw_nix_input_observer_launcher.c"]),
        "observer_source_sha256": _digest(files["src/tgw/nix_input_observation.py"]),
        "provider_sha256": _digest(files["src/tgw/nix_observer_render_evaluation.py"]),
        "host_identity_receipt_sha256": "sha256:" + "5" * 64,
        "systemd_analyze_sha256": "sha256:" + "6" * 64,
        "systemd_analyze_version_stdout_sha256": "sha256:" + "7" * 64,
        "target": "nix-input-observer-rendered-artifacts",
        "system": "x86_64-linux",
        "network_policy": "offline-no-substituters",
        "allow_ifd": False,
        "activate": False,
        "profile_write": False,
        "home_db_write": False,
        "expected_outputs": list(OUTPUTS),
        "expected_metadata_status": "NON_DEPLOYABLE_RENDER_FIXTURE",
        "input_closure_manifest": [
            {
                "node": "nixpkgs",
                "rev": "ac62194c3917d5f474c1a844b6fd6da2db95077d",
                "lock_nar_hash": "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w=",
                "store_path": "/nix/store/11111111111111111111111111111111-source",
                "nar_sha256": "sha256:" + "8" * 64,
            }
        ],
        "input_closure_path_count": 1,
        "systemd_analyze_version": "systemd 257 (257.10)",
        "systemd_analyze_version_stdout_bytes": 32,
        "max_duration_seconds": 60,
        "max_output_bytes": 1024 * 1024,
    }
    value["input_closure_manifest_sha256"] = _digest(canonical(value["input_closure_manifest"]))
    value["request_sha256"] = _digest(canonical(value))
    return value


@pytest.fixture
def source_case(tmp_path):
    files = _source_files()
    source = tmp_path / "source"
    source.mkdir()
    _write_source(source, files)
    tree = _tree_hash(source)
    archive = tmp_path / "source.tar"
    _archive(archive, files, commit="b" * 40)
    request = _request(files, archive, tree=tree)
    helper_source = Path(helper.__file__).read_bytes()
    tool_descriptor = helper.describe_tool(request, _test_tool_authority=LOCAL_TOOL_AUTHORITY)
    wire = helper.packet(
        helper_source,
        request,
        archive,
        tool_descriptor=tool_descriptor,
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    return files, archive, request, helper_source, wire, binding, tool_descriptor


def _run_bootstrap(
    wire: bytes,
    *,
    env: dict[str, str] | None = None,
    tool_authority: helper.ToolAuthority | None = LOCAL_TOOL_AUTHORITY,
    a2_authority: helper.A2Authority | None = None,
    scratch_root: Path | None = None,
    cleanup_failure: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    preamble = "" if tool_authority is None else "_TEST_ONLY_TOOL_AUTHORITY=" + _authority_literal(tool_authority) + "\n"
    if a2_authority is not None:
        preamble += "_TEST_ONLY_A2_AUTHORITY=" + _a2_authority_literal(a2_authority) + "\n"
    if scratch_root is not None:
        preamble += "_TEST_ONLY_SCRATCH_ROOT=" + repr(str(scratch_root)) + "\n"
    if cleanup_failure:
        preamble += "def fail_cleanup(*args,**kwargs): raise OSError('injected cleanup failure')\n_TEST_ONLY_CLEANUP_TREE=fail_cleanup\n"
    return subprocess.run(
        [sys.executable, "-I", "-c", preamble + helper.BOOTSTRAP],
        input=wire,
        capture_output=True,
        check=False,
        env=env,
    )


def _helper_frame(request, tool_descriptor, archive):
    request_raw = canonical(request)
    tool_raw = canonical(tool_descriptor)
    return struct.pack("!Q", len(request_raw)) + request_raw + struct.pack("!Q", len(tool_raw)) + tool_raw + archive.read_bytes()


def _raw_wire(helper_source, request, tool_descriptor, archive):
    request_raw = canonical(request)
    tool_raw = canonical(tool_descriptor)
    archive_raw = archive.read_bytes()
    prefix = helper.PREFIX.pack(
        helper.MAGIC,
        helper.VERSION,
        len(helper_source),
        len(request_raw),
        len(tool_raw),
        len(archive_raw),
        bytes.fromhex(request["request_sha256"].removeprefix("sha256:")),
        hashlib.sha256(helper_source).digest(),
        hashlib.sha256(tool_raw).digest(),
        hashlib.sha256(archive_raw).digest(),
    )
    return prefix + helper_source + request_raw + tool_raw + archive_raw


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o555)


def _make_a2_case(tmp_path: Path, *, mode: str = "success") -> dict[str, object]:
    store = tmp_path / "store"
    tools = tmp_path / "held-tools"
    store.mkdir()
    tools.mkdir()
    held_input_path = store / "11111111111111111111111111111111-source"
    input_path = "/nix/store/11111111111111111111111111111111-source"
    output_path = store / "22222222222222222222222222222222-render"
    drv = "/nix/store/33333333333333333333333333333333-render.drv"
    held_input_path.mkdir()
    output_path.mkdir()
    for name in OUTPUTS[:-1]:
        path = output_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((name + "\n").encode())
    metadata_files = [
        {"path": name, "sha256": _digest((output_path / name).read_bytes())}
        for name in OUTPUTS[:-1]
    ]
    (output_path / "verifier-metadata.json").write_bytes(
        canonical(
            {
                "schema": "tgw-nix-input-observer-render/v1",
                "system": "x86_64-linux",
                "descriptor_status": "NON_DEPLOYABLE_RENDER_FIXTURE",
                "activation": False,
                "units": list(OUTPUTS[9:12]),
                "files": metadata_files,
            }
        )
    )
    input_nar = "a" * 64
    output_nar = "b" * 64
    nix = tools / "nix"
    nix_store = tools / "nix-store"
    systemd_analyze = tools / "systemd-analyze"
    call_log = tmp_path / "tool-calls.jsonl"
    nix_failure = mode == "nix-failure"
    lock_write = mode == "lock-write"
    output_flood = mode == "output-flood"
    _write_executable(
        nix,
        "#!/usr/bin/python3\n"
        "import json,os,sys\n"
        f"INPUT={input_path!r}\nOUT={str(output_path)!r}\nDRV={drv!r}\nLOG={str(call_log)!r}\n"
        f"INPUT_NAR={input_nar!r}\nOUT_NAR={output_nar!r}\nFAIL_BUILD={nix_failure!r}\n"
        f"LOCK_WRITE={lock_write!r}\nOUTPUT_FLOOD={output_flood!r}\n"
        "a=sys.argv[1:]\n"
        "with open(LOG,'a') as f: f.write(json.dumps("
        "{'tool':'nix','argv':a,'nix_remote':os.environ.get('NIX_REMOTE'),"
        "'nix_config':os.environ.get('NIX_CONFIG'),'path':os.environ.get('PATH'),"
        "'home':os.environ.get('HOME'),'tmpdir':os.environ.get('TMPDIR')})+'\\n')\n"
        "if OUTPUT_FLOOD and 'flake' in a: sys.stdout.write('x'*(1024*1024+1)); raise SystemExit()\n"
        "if 'flake' in a and 'metadata' in a:\n"
        " print(json.dumps({'locks':{'nodes':"
        "{'root':{'inputs':{'nixpkgs':'nixpkgs'}},"
        "'nixpkgs':{'locked':"
        "{'rev':'ac62194c3917d5f474c1a844b6fd6da2db95077d',"
        "'narHash':'sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w='}}}}}))\n"
        "elif 'eval' in a and a[-1].endswith('inputIdentities.nixpkgs.outPath'): print(INPUT,end='')\n"
        "elif 'eval' in a and a[-1].endswith('.drvPath'): print(DRV,end='')\n"
        "elif 'derivation' in a and 'show' in a: print(json.dumps({DRV:{'outputs':{'out':{'path':OUT}}}}))\n"
        "elif 'build' in a:\n"
        "  if FAIL_BUILD: print('bounded nix failure',file=sys.stderr); raise SystemExit(9)\n"
        "  if LOCK_WRITE:\n"
        "   with open('flake.lock','a') as f: f.write('mutated')\n"
        "  print(OUT)\n"
        "elif 'hash' in a and 'path' in a: print(INPUT_NAR if a[-1]==INPUT else OUT_NAR)\n"
        "else: print('unexpected nix argv',a,file=sys.stderr); raise SystemExit(8)\n",
    )
    _write_executable(
        nix_store,
        "#!/usr/bin/python3\n"
        "import json,os,sys\n"
        f"INPUT={input_path!r}\nOUT={str(output_path)!r}\nLOG={str(call_log)!r}\n"
        "a=sys.argv[1:]\n"
        "with open(LOG,'a') as f: f.write(json.dumps({'tool':'nix-store','argv':a,'nix_remote':os.environ.get('NIX_REMOTE'),'path':os.environ.get('PATH')})+'\\n')\n"
        "if '--references' in a and a[-1]==INPUT: pass\n"
        "elif '--requisites' in a and a[-1]==OUT: print(OUT)\n"
        "else: print('unexpected nix-store argv',a,file=sys.stderr); raise SystemExit(8)\n",
    )
    version_stdout = b"systemd 257 (257.10)\nfeatures\n"
    verifier_failure = mode == "verifier-failure"
    _write_executable(
        systemd_analyze,
        "#!/usr/bin/python3\n"
        "import json,os,sys\n"
        f"VERSION={version_stdout!r}\nFAIL_VERIFY={verifier_failure!r}\nLOG={str(call_log)!r}\n"
        "a=sys.argv[1:]\n"
        "with open(LOG,'a') as f: f.write(json.dumps({'tool':'systemd-analyze','argv':a,'nix_remote':os.environ.get('NIX_REMOTE'),'path':os.environ.get('PATH')})+'\\n')\n"
        "if a==['--version']: sys.stdout.buffer.write(VERSION)\n"
        "elif a and a[0]=='verify':\n"
        "  if FAIL_VERIFY: print('bounded verifier failure',file=sys.stderr); raise SystemExit(7)\n"
        "elif True: print('unexpected verifier argv',a,file=sys.stderr); raise SystemExit(8)\n",
    )

    files = _source_files()
    files["src/tgw/nix_observer_render_evaluation.py"] = Path("src/tgw/nix_observer_render_evaluation.py").read_bytes()
    source = tmp_path / "source"
    source.mkdir()
    _write_source(source, files)
    tree = _tree_hash(source)
    archive = tmp_path / "source.tar"
    _archive(archive, files, commit="b" * 40)
    request = _request(files, archive, tree=tree)
    request.update(
        {
            "host_identity_receipt_sha256": helper.AUTHORIZED_RENDER_RECEIPT_SHA256,
            "systemd_analyze_sha256": _digest(systemd_analyze.read_bytes()),
            "systemd_analyze_version_stdout_sha256": _digest(version_stdout),
            "systemd_analyze_version_stdout_bytes": len(version_stdout),
            "input_closure_manifest": [
                {
                    "node": "nixpkgs",
                    "rev": "ac62194c3917d5f474c1a844b6fd6da2db95077d",
                    "lock_nar_hash": "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w=",
                    "store_path": input_path,
                    "nar_sha256": "sha256:" + input_nar,
                }
            ],
        }
    )
    request["input_closure_manifest_sha256"] = _digest(canonical(request["input_closure_manifest"]))
    request.pop("request_sha256")
    request["request_sha256"] = _digest(canonical(request))
    authority = helper.A2Authority(
        a1_prerequisite_receipt_sha256=LOCAL_TOOL_AUTHORITY.authority_receipt_sha256,
        a2_prerequisite_receipt_sha256=helper.AUTHORIZED_RENDER_RECEIPT_SHA256,
        nix_path=str(nix),
        nix_sha256=_digest(nix.read_bytes()),
        nix_store_path=str(nix_store),
        nix_store_sha256=_digest(nix_store.read_bytes()),
        systemd_analyze_path=str(systemd_analyze),
        systemd_analyze_sha256=_digest(systemd_analyze.read_bytes()),
        systemd_analyze_version="systemd 257 (257.10)",
        systemd_analyze_version_stdout_sha256=_digest(version_stdout),
        systemd_analyze_version_stdout_bytes=len(version_stdout),
        input_path=input_path,
        input_nar_sha256="sha256:" + input_nar,
        store_root=str(store),
        owner_uid=os.getuid(),
        mode=0o555,
        require_nix_store=False,
        allow_mutable_parents=True,
        held_input_path=str(held_input_path),
        scratch_root=str(tmp_path / "scratch"),
    )
    helper_source = Path(helper.__file__).read_bytes()
    tool_descriptor = helper.describe_tool(request, _test_tool_authority=LOCAL_TOOL_AUTHORITY)
    wire = helper.packet(
        helper_source,
        request,
        archive,
        tool_descriptor=tool_descriptor,
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    return {
        "request": request,
        "authority": authority,
        "tool_descriptor": tool_descriptor,
        "wire": wire,
        "binding": helper.parse_prefix(wire[: helper.PREFIX.size]),
        "scratch": tmp_path / "scratch",
        "output": output_path,
        "call_log": call_log,
    }


def test_actual_subprocess_frame_does_not_accept_ambient_executor(source_case):
    _, _, request, _, wire, binding, tool_descriptor = source_case
    completed = _run_bootstrap(wire, env={**os.environ, "TGW_NIX_RENDER_EXECUTOR": "ambient-command"})
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert receipt["schema"] == helper.A2_FAILURE_SCHEMA
    assert receipt["outcome"] == "FAILED"
    assert receipt["request_sha256"] == request["request_sha256"] == binding.request_sha256
    assert receipt["cleanup"] == "removed"
    assert receipt["effects"] == helper._a2_effects(False)
    assert "marker" not in receipt


def test_actual_framed_helper_subprocess_executes_offline_render_and_provider(tmp_path):
    case = _make_a2_case(tmp_path)
    completed = _run_bootstrap(
        case["wire"],
        a2_authority=case["authority"],
        scratch_root=case["scratch"],
        env={
            **os.environ,
            "NIX_REMOTE": "ssh://ambient-builder",
            "NIX_CONFIG": "builders = @/etc/nix/machines\nbuilders-use-substitutes = true\n",
        },
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    receipt = json.loads(completed.stdout)
    assert (
        helper.validate_a2_terminal(
            receipt,
            binding=case["binding"],
            request=case["request"],
            tool_descriptor=case["tool_descriptor"],
            authority=case["authority"],
        )
        == receipt
    )
    assert receipt["schema"] == helper.SUCCESS_SCHEMA
    assert receipt["provider_receipt"]["metadata_status"] == "NON_DEPLOYABLE_RENDER_FIXTURE"
    assert [item["path"] for item in receipt["provider_receipt"]["files"]] == list(OUTPUTS)
    assert receipt["provider_receipt"]["systemd_verify"]["exit_code"] == 0
    assert receipt["closure_path_count"] == 1
    assert receipt["effects"] == helper._a2_effects(True)
    assert not case["scratch"].exists()
    calls = [json.loads(line) for line in case["call_log"].read_text().splitlines()]
    nix_calls = [call for call in calls if call["tool"] == "nix"]
    assert nix_calls and all(call["nix_remote"] == "local" and call["path"] == "/no-ambient-path" for call in calls)
    policy_calls = [call for call in nix_calls if "--offline" in call["argv"]]
    assert all(
        call["argv"][: len(helper.NIX_ARGV_PREFIX)] == list(helper.NIX_ARGV_PREFIX)
        for call in policy_calls
    )
    assert len(policy_calls) == len(nix_calls)
    assert all("builders =\n" in call["nix_config"] and "builders-use-substitutes = false" in call["nix_config"] for call in nix_calls)
    assert all(
        Path(call["home"]).parent == Path(call["tmpdir"]).parent
        and Path(call["home"]).name == "nix-home"
        and Path(call["tmpdir"]).name == "tmp"
        for call in nix_calls
    )
    build_calls = [call["argv"] for call in nix_calls if "build" in call["argv"]]
    assert len(build_calls) == 1
    assert build_calls[0][-4:] == [
        "build",
        "--no-link",
        "--print-out-paths",
        "/nix/store/33333333333333333333333333333333-render.drv^out",
    ]


@pytest.mark.parametrize(
    ("mode", "stage", "diagnostic_code", "subprocess_step", "return_code"),
    [
        ("nix-failure", "a2-build", "SUBPROCESS_FAILED", "drv-build", 9),
        ("verifier-failure", "provider", "SUBPROCESS_FAILED", "provider-systemd-verify", 7),
        ("lock-write", "a2-verified", "IDENTITY_MISMATCH", None, None),
        ("output-flood", "a2-input", "BOUND_EXCEEDED", "flake-metadata", None),
    ],
)
def test_actual_framed_helper_subprocess_emits_closed_a2_failures(
    tmp_path, mode, stage, diagnostic_code, subprocess_step, return_code
):
    case = _make_a2_case(tmp_path, mode=mode)
    completed = _run_bootstrap(
        case["wire"],
        a2_authority=case["authority"],
        scratch_root=case["scratch"],
    )
    assert completed.returncode == 1
    receipt = json.loads(completed.stdout)
    assert (
        helper.validate_a2_terminal(
            receipt,
            binding=case["binding"],
            request=case["request"],
            tool_descriptor=case["tool_descriptor"],
            authority=case["authority"],
        )
        == receipt
    )
    assert receipt["schema"] == helper.A2_FAILURE_SCHEMA
    assert receipt["stage"] == stage
    assert receipt["diagnostic_code"] == diagnostic_code
    assert receipt["subprocess_step"] == subprocess_step
    assert receipt["return_code"] == return_code
    assert receipt["effects"]["network"] is False
    assert not case["scratch"].exists()


def test_actual_framed_helper_maps_missing_a2_tool_to_closed_a2_tool_failure(tmp_path):
    case = _make_a2_case(tmp_path)
    authority_values = vars(case["authority"]).copy()
    authority_values["nix_path"] = str(tmp_path / "held-tools" / "missing-nix")
    authority = helper.A2Authority(**authority_values)
    completed = _run_bootstrap(
        case["wire"],
        a2_authority=authority,
        scratch_root=case["scratch"],
    )
    assert completed.returncode == 1
    receipt = json.loads(completed.stdout)
    assert (
        helper.validate_a2_terminal(
            receipt,
            binding=case["binding"],
            request=case["request"],
            tool_descriptor=case["tool_descriptor"],
            authority=authority,
        )
        == receipt
    )
    assert receipt["stage"] == "a2-tool"
    assert receipt["diagnostic_code"] == "IDENTITY_MISMATCH"
    assert receipt["subprocess_step"] is receipt["return_code"] is None
    assert receipt["cleanup"] == "removed"


def test_actual_framed_helper_cleanup_failure_overrides_a2_success(tmp_path):
    case = _make_a2_case(tmp_path)
    completed = _run_bootstrap(
        case["wire"],
        a2_authority=case["authority"],
        scratch_root=case["scratch"],
        cleanup_failure=True,
    )
    assert completed.returncode == 2
    receipt = json.loads(completed.stdout)
    assert (
        helper.validate_a2_terminal(
            receipt,
            binding=case["binding"],
            request=case["request"],
            tool_descriptor=case["tool_descriptor"],
            authority=case["authority"],
        )
        == receipt
    )
    assert receipt["outcome"] == "AMBIGUOUS"
    assert receipt["original_stage"] == "a2-verified"
    assert receipt["cleanup"] == "failed"
    shutil.rmtree(case["scratch"])


@pytest.mark.parametrize(
    "mutation",
    [
        "provider-content",
        "provider-hash",
        "provider-time",
        "provider-stale",
        "provider-verify-shape",
        "closure",
        "prerequisite",
        "policy",
        "policy-home",
        "policy-tmpdir",
        "policy-env-extra",
    ],
)
def test_nested_a2_success_mutations_are_rejected(tmp_path, mutation):
    case = _make_a2_case(tmp_path)
    completed = _run_bootstrap(
        case["wire"],
        a2_authority=case["authority"],
        scratch_root=case["scratch"],
    )
    receipt = json.loads(completed.stdout)
    changed = deepcopy(receipt)
    if mutation == "provider-content":
        changed["provider_receipt"]["files"][0]["size"] += 1
        inner = dict(changed["provider_receipt"])
        inner.pop("receipt_sha256")
        changed["provider_receipt"]["receipt_sha256"] = _digest(canonical(inner))
        changed["provider_receipt_sha256"] = changed["provider_receipt"]["receipt_sha256"]
    elif mutation == "provider-hash":
        changed["provider_receipt_sha256"] = "sha256:" + "0" * 64
    elif mutation == "provider-time":
        changed["provider_receipt"]["systemd_verify"]["observed_at"] = "not-a-time"
        inner = dict(changed["provider_receipt"])
        inner.pop("receipt_sha256")
        changed["provider_receipt"]["receipt_sha256"] = _digest(canonical(inner))
        changed["provider_receipt_sha256"] = changed["provider_receipt"]["receipt_sha256"]
    elif mutation == "provider-stale":
        changed["provider_receipt"]["systemd_verify"]["observed_at"] = "2000-01-01T00:00:00Z"
        inner = dict(changed["provider_receipt"])
        inner.pop("receipt_sha256")
        changed["provider_receipt"]["receipt_sha256"] = _digest(canonical(inner))
        changed["provider_receipt_sha256"] = changed["provider_receipt"]["receipt_sha256"]
    elif mutation == "provider-verify-shape":
        changed["provider_receipt"]["systemd_verify"] = []
        inner = dict(changed["provider_receipt"])
        inner.pop("receipt_sha256")
        changed["provider_receipt"]["receipt_sha256"] = _digest(canonical(inner))
        changed["provider_receipt_sha256"] = changed["provider_receipt"]["receipt_sha256"]
    elif mutation == "closure":
        changed["closure_manifest"][0]["nar_sha256"] = "sha256:" + "0" * 64
    elif mutation == "prerequisite":
        changed["a2_prerequisite_receipt_sha256"] = "sha256:" + "0" * 64
    elif mutation == "policy":
        changed["execution_policy"]["remote_builders"] = True
        changed["execution_policy_sha256"] = _digest(canonical(changed["execution_policy"]))
    elif mutation == "policy-home":
        changed["execution_policy"]["environment"]["HOME"] = "/attacker/run-00000000000000000000000000000000/nix-home"
        changed["execution_policy_sha256"] = _digest(canonical(changed["execution_policy"]))
    elif mutation == "policy-tmpdir":
        changed["execution_policy"]["environment"]["TMPDIR"] = "/tmp"
        changed["execution_policy_sha256"] = _digest(canonical(changed["execution_policy"]))
    else:
        changed["execution_policy"]["environment"]["NIX_BUILDERS"] = "ssh://attacker"
        changed["execution_policy_sha256"] = _digest(canonical(changed["execution_policy"]))
    changed.pop("receipt_sha256")
    changed["receipt_sha256"] = _digest(canonical(changed))
    with pytest.raises(helper.RenderHelperError):
        helper.validate_a2_terminal(
            changed,
            binding=case["binding"],
            request=case["request"],
            tool_descriptor=case["tool_descriptor"],
            authority=case["authority"],
        )


@pytest.mark.parametrize(
    "mutation",
    ["stage-code", "step", "return-code", "huge-return-code", "effects", "cleanup", "not-created"],
)
def test_impossible_a2_failure_relationship_is_rejected(tmp_path, mutation):
    case = _make_a2_case(tmp_path, mode="nix-failure")
    completed = _run_bootstrap(
        case["wire"],
        a2_authority=case["authority"],
        scratch_root=case["scratch"],
    )
    changed = json.loads(completed.stdout)
    if mutation == "stage-code":
        changed.update(
            stage="a2-tool",
            original_stage="a2-tool",
            subprocess_step="a2-tool",
            effects=helper._a2_effects(False),
        )
    elif mutation == "step":
        changed["subprocess_step"] = "closure-hash"
    elif mutation == "return-code":
        changed["return_code"] = 0
    elif mutation == "huge-return-code":
        changed["return_code"] = 10**100
    elif mutation == "effects":
        changed["effects"] = helper._a2_effects(False)
    elif mutation == "cleanup":
        changed["cleanup"] = "failed"
    else:
        changed["cleanup"] = "not-created"
    changed.pop("receipt_sha256")
    changed["receipt_sha256"] = _digest(canonical(changed))
    with pytest.raises(helper.RenderHelperError, match="failure envelope"):
        helper.validate_a2_terminal(
            changed,
            binding=case["binding"],
            request=case["request"],
            tool_descriptor=case["tool_descriptor"],
            authority=case["authority"],
        )


def test_non_subprocess_a2_tool_bound_failure_is_a_valid_closed_terminal(tmp_path):
    case = _make_a2_case(tmp_path, mode="nix-failure")
    completed = _run_bootstrap(
        case["wire"],
        a2_authority=case["authority"],
        scratch_root=case["scratch"],
    )
    changed = json.loads(completed.stdout)
    changed.update(
        stage="a2-tool",
        diagnostic_code="BOUND_EXCEEDED",
        original_stage="a2-tool",
        original_diagnostic_code="BOUND_EXCEEDED",
        subprocess_step=None,
        return_code=None,
        effects=helper._a2_effects(False),
    )
    changed.pop("receipt_sha256")
    changed["receipt_sha256"] = _digest(canonical(changed))
    assert (
        helper.validate_a2_terminal(
            changed,
            binding=case["binding"],
            request=case["request"],
            tool_descriptor=case["tool_descriptor"],
            authority=case["authority"],
        )
        == changed
    )


@pytest.mark.parametrize(
    "field",
    ["a2-receipt", "systemd", "systemd-version", "systemd-version-digest", "systemd-version-bytes", "input-path", "input-nar"],
)
def test_a2_validator_rejects_request_authority_cross_binding_mutations(tmp_path, field):
    case = _make_a2_case(tmp_path)
    completed = _run_bootstrap(
        case["wire"],
        a2_authority=case["authority"],
        scratch_root=case["scratch"],
    )
    receipt = json.loads(completed.stdout)
    authority_values = vars(case["authority"]).copy()
    if field == "a2-receipt":
        authority_values["a2_prerequisite_receipt_sha256"] = "sha256:" + "0" * 64
    elif field == "systemd":
        authority_values["systemd_analyze_sha256"] = "sha256:" + "0" * 64
    elif field == "systemd-version":
        authority_values["systemd_analyze_version"] = "systemd 0"
    elif field == "systemd-version-digest":
        authority_values["systemd_analyze_version_stdout_sha256"] = "sha256:" + "0" * 64
    elif field == "systemd-version-bytes":
        authority_values["systemd_analyze_version_stdout_bytes"] = 1
    elif field == "input-path":
        authority_values["input_path"] = "/nix/store/00000000000000000000000000000000-source"
    else:
        authority_values["input_nar_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(helper.RenderHelperError, match="request authority composition"):
        helper.validate_a2_terminal(
            receipt,
            binding=case["binding"],
            request=case["request"],
            tool_descriptor=case["tool_descriptor"],
            authority=helper.A2Authority(**authority_values),
        )


def test_flake_render_metadata_uses_the_canonical_observer_unit_list():
    source = Path("flake.nix").read_text()
    expected = (
        'observerUnitNames = [ "tgw-nix-input-observer.slice" '
        '"tgw-nix-input-observer.socket" "tgw-nix-input-observer@.service" ];'
    )
    assert expected in source
    assert 'python3 - "$out" ${builtins.concatStringsSep " " observerUnitNames}' in source
    assert '"units":sys.argv[2:]' in source
    assert '"units":["tgw-nix-input-observer.socket"' not in source


def test_fixed_prefix_binds_all_exact_lengths_and_hashes(source_case):
    _, archive, request, helper_source, _, binding, tool_descriptor = source_case
    assert binding.request_sha256 == request["request_sha256"]
    assert binding.helper_sha256 == _digest(helper_source)
    assert binding.tool_descriptor_sha256 == _digest(canonical(tool_descriptor))
    assert binding.archive_sha256 == request["archive_sha256"]
    assert binding.request_bytes == len(canonical(request))
    assert binding.helper_bytes == len(helper_source)
    assert binding.tool_descriptor_bytes == len(canonical(tool_descriptor))
    assert binding.archive_bytes == archive.stat().st_size


def test_packet_opens_archive_once_and_streams_that_held_inode(source_case, monkeypatch):
    _, archive, request, helper_source, _, _, tool_descriptor = source_case
    original_open = os.open
    archive_opens = []

    def tracked_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if Path(path) == archive:
            archive_opens.append((descriptor, os.fstat(descriptor).st_ino))
        return descriptor

    monkeypatch.setattr(helper.os, "open", tracked_open)
    wire = helper.packet(
        helper_source,
        request,
        archive,
        tool_descriptor=tool_descriptor,
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    assert len(archive_opens) == 1
    assert wire.endswith(archive.read_bytes())


def test_packet_rejects_archive_changed_between_pre_and_post_fstat(source_case, monkeypatch):
    _, archive, request, helper_source, _, _, tool_descriptor = source_case
    original_open = os.open
    original_fstat = os.fstat
    archive_fd = -1
    observations = 0

    def tracked_open(path, flags, *args, **kwargs):
        nonlocal archive_fd
        descriptor = original_open(path, flags, *args, **kwargs)
        if Path(path) == archive:
            archive_fd = descriptor
        return descriptor

    def changed_fstat(descriptor):
        nonlocal observations
        value = original_fstat(descriptor)
        if descriptor != archive_fd:
            return value
        observations += 1
        if observations == 1:
            return value
        return SimpleNamespace(
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_size=value.st_size,
            st_mode=value.st_mode,
            st_mtime_ns=value.st_mtime_ns + 1,
            st_ctime_ns=value.st_ctime_ns,
        )

    monkeypatch.setattr(helper.os, "open", tracked_open)
    monkeypatch.setattr(helper.os, "fstat", changed_fstat)
    with pytest.raises(helper.RenderHelperError, match="changed while read"):
        helper.packet(
            helper_source,
            request,
            archive,
            tool_descriptor=tool_descriptor,
            _test_tool_authority=LOCAL_TOOL_AUTHORITY,
        )


def test_resolved_regular_tool_descriptor_works_and_symlink_is_rejected(tmp_path, source_case):
    _, _, request, _, wire, _, tool_descriptor = source_case
    assert tool_descriptor["path"] == "/usr/bin/git"
    completed = _run_bootstrap(wire)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["stage"] == "a2-tool"

    symlink = tmp_path / "git-link"
    symlink.symlink_to("/usr/bin/git")
    symlink_authority = helper.ToolAuthority(
        authority_receipt_sha256=LOCAL_TOOL_AUTHORITY.authority_receipt_sha256,
        path=str(symlink),
        sha256=LOCAL_TOOL_AUTHORITY.sha256,
        bytes=LOCAL_TOOL_AUTHORITY.bytes,
        owner_uid=os.geteuid(),
        mode=0o555,
        require_nix_store=False,
    )
    with pytest.raises(helper.RenderHelperError, match="tool path"):
        helper.describe_tool(request, _test_tool_authority=symlink_authority)

    hostile = {**tool_descriptor, "path": str(symlink)}
    helper_source = Path(helper.__file__).read_bytes()
    archive = source_case[1]
    hostile_wire = _raw_wire(helper_source, request, hostile, archive)
    binding = helper.parse_prefix(hostile_wire[: helper.PREFIX.size])
    failed = _run_bootstrap(hostile_wire)
    receipt = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert helper.validate_terminal(receipt, binding=binding, request=request, tool_descriptor=hostile) == receipt
    assert receipt["stage"] == "tool" and receipt["cleanup"] == "not-created"


def test_production_tool_manifest_is_exactly_authorized_and_request_composed(source_case):
    _, archive, request, helper_source, _, _, local_tool = source_case
    admitted = helper._expected_tool_descriptor(request["request_sha256"], helper.PRODUCTION_GIT_AUTHORITY)
    assert admitted == {
        "schema": helper.TOOL_DESCRIPTOR_SCHEMA,
        "name": "git",
        "request_sha256": request["request_sha256"],
        "authority_receipt_sha256": helper.AUTHORIZED_TOOL_RECEIPT_SHA256,
        "path": helper.AUTHORIZED_GIT_PATH,
        "sha256": helper.AUTHORIZED_GIT_SHA256,
        "bytes": 4_373_016,
        "owner_uid": 0,
        "mode": "0555",
    }
    assert helper._validate_tool_descriptor(
        admitted,
        request_sha256=request["request_sha256"],
        authority=helper.PRODUCTION_GIT_AUTHORITY,
    ) == admitted
    with pytest.raises(helper.RenderHelperError, match="admitted Git identity"):
        helper.make_prefix(
            helper_source=helper_source,
            request=request,
            tool_descriptor=local_tool,
            archive_raw=archive.read_bytes(),
        )


def test_production_git_authority_matches_frozen_self_hashed_receipt():
    receipt_path = Path("agent-services/receipts/tgw-prod-nix-observer-prerequisites-20260812.json")
    receipt = json.loads(receipt_path.read_bytes())
    claimed = receipt.pop("self_hash")
    assert claimed == _digest(canonical(receipt)) == helper.AUTHORIZED_TOOL_RECEIPT_SHA256
    assert receipt["tools"]["git"] == {
        "path": helper.AUTHORIZED_GIT_PATH,
        "sha256": helper.AUTHORIZED_GIT_SHA256,
        "version": "git version 2.50.1",
    }


def test_production_a2_authority_cross_binds_both_frozen_prerequisite_receipts():
    a1 = json.loads(Path("agent-services/receipts/tgw-prod-nix-observer-prerequisites-20260812.json").read_bytes())
    claimed_a1 = a1.pop("self_hash")
    assert claimed_a1 == _digest(canonical(a1)) == helper.AUTHORIZED_TOOL_RECEIPT_SHA256
    assert a1["tools"]["nix"] == {
        "path": helper.AUTHORIZED_NIX_PATH,
        "sha256": helper.AUTHORIZED_NIX_SHA256,
        "version": "nix (Nix) 2.28.5",
    }
    assert a1["tools"]["nix_store"] == {
        "path": helper.AUTHORIZED_NIX_STORE_PATH,
        "sha256": helper.AUTHORIZED_NIX_STORE_SHA256,
        "version": "nix-store (Nix) 2.28.5",
    }
    a2 = json.loads(Path("agent-services/receipts/tgw-prod-observer-render-prerequisites-20260812.json").read_bytes())
    claimed_a2 = a2.pop("receipt_sha256")
    assert claimed_a2 == _digest(canonical(a2)) == helper.AUTHORIZED_RENDER_RECEIPT_SHA256
    assert a2["nixpkgs_input"]["store_path"] == helper.AUTHORIZED_INPUT_PATH
    assert a2["nixpkgs_input"]["nar_sha256"] == helper.AUTHORIZED_INPUT_NAR_SHA256
    assert a2["systemd_analyze"]["path"] == helper.AUTHORIZED_SYSTEMD_ANALYZE_PATH
    assert a2["systemd_analyze"]["executable_sha256"] == helper.AUTHORIZED_SYSTEMD_ANALYZE_SHA256
    assert a2["systemd_analyze"]["version_first_line"] == helper.AUTHORIZED_SYSTEMD_ANALYZE_VERSION
    assert a2["systemd_analyze"]["version_stdout_sha256"] == helper.AUTHORIZED_SYSTEMD_ANALYZE_VERSION_STDOUT_SHA256
    assert a2["systemd_analyze"]["version_stdout_bytes"] == helper.AUTHORIZED_SYSTEMD_ANALYZE_VERSION_STDOUT_BYTES
    assert helper.PRODUCTION_A2_AUTHORITY.a1_prerequisite_receipt_sha256 == claimed_a1
    assert helper.PRODUCTION_A2_AUTHORITY.a2_prerequisite_receipt_sha256 == claimed_a2


def _assert_tool_refused_before_subprocess(
    *,
    tmp_path: Path,
    monkeypatch,
    source_case,
    descriptor: dict[str, object],
    authority: helper.ToolAuthority,
) -> dict[str, object]:
    _, archive, request, helper_source, _, _, _ = source_case
    wire = _raw_wire(helper_source, request, descriptor, archive)
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    subprocess_calls: list[object] = []

    def forbidden_subprocess(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        raise AssertionError("tool refusal reached subprocess")

    monkeypatch.setattr(helper.subprocess, "run", forbidden_subprocess)
    receipt = helper.execute_packet(
        io.BytesIO(_helper_frame(request, descriptor, archive)),
        binding=binding,
        scratch_root=tmp_path / "scratch",
        _test_tool_authority=authority,
    )
    assert subprocess_calls == []
    assert receipt["schema"] == helper.FAILURE_SCHEMA
    assert receipt["stage"] == "tool"
    assert receipt["diagnostic_code"] == "IDENTITY_MISMATCH"
    assert receipt["cleanup"] == "not-created"
    assert not (tmp_path / "scratch").exists()
    return receipt


def test_self_consistent_alternate_regular_executable_is_not_admitted(tmp_path, monkeypatch, source_case):
    request = source_case[2]
    alternate = Path("/usr/bin/true")
    metadata = alternate.stat()
    alternate_authority = helper.ToolAuthority(
        authority_receipt_sha256="sha256:" + "4" * 64,
        path=str(alternate),
        sha256=_digest(alternate.read_bytes()),
        bytes=metadata.st_size,
        owner_uid=metadata.st_uid,
        mode=metadata.st_mode & 0o7777,
        require_nix_store=False,
        forbid_owner_write=False,
    )
    descriptor = helper._expected_tool_descriptor(request["request_sha256"], alternate_authority)
    _assert_tool_refused_before_subprocess(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        source_case=source_case,
        descriptor=descriptor,
        authority=LOCAL_TOOL_AUTHORITY,
    )


def test_user_owned_exact_content_copy_is_not_admitted(tmp_path, monkeypatch, source_case):
    request = source_case[2]
    copied = tmp_path / "git"
    shutil.copyfile("/usr/bin/git", copied)
    copied.chmod(0o555)
    claimed_root_authority = helper.ToolAuthority(
        authority_receipt_sha256="sha256:" + "3" * 64,
        path=str(copied),
        sha256=LOCAL_TOOL_AUTHORITY.sha256,
        bytes=LOCAL_TOOL_AUTHORITY.bytes,
        owner_uid=0,
        mode=0o555,
        require_nix_store=False,
    )
    descriptor = helper._expected_tool_descriptor(request["request_sha256"], claimed_root_authority)
    assert copied.stat().st_uid != descriptor["owner_uid"]
    _assert_tool_refused_before_subprocess(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        source_case=source_case,
        descriptor=descriptor,
        authority=claimed_root_authority,
    )


def test_root_owned_but_owner_writable_exact_digest_is_not_admitted(tmp_path, monkeypatch, source_case):
    request = source_case[2]
    immutable_claim = helper.ToolAuthority(
        authority_receipt_sha256=LOCAL_TOOL_AUTHORITY.authority_receipt_sha256,
        path=LOCAL_TOOL_AUTHORITY.path,
        sha256=LOCAL_TOOL_AUTHORITY.sha256,
        bytes=LOCAL_TOOL_AUTHORITY.bytes,
        owner_uid=0,
        mode=0o555,
        require_nix_store=False,
    )
    descriptor = helper._expected_tool_descriptor(request["request_sha256"], immutable_claim)
    assert Path(immutable_claim.path).stat().st_uid == 0
    assert Path(immutable_claim.path).stat().st_mode & 0o200
    _assert_tool_refused_before_subprocess(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        source_case=source_case,
        descriptor=descriptor,
        authority=immutable_claim,
    )


@pytest.mark.parametrize("write_bit", [0o200, 0o020, 0o002])
def test_every_writable_mode_class_is_rejected_before_execution(monkeypatch, write_bit):
    subprocess_calls = []
    monkeypatch.setattr(helper.subprocess, "run", lambda *args, **kwargs: subprocess_calls.append((args, kwargs)))
    metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o555 | write_bit, st_uid=0)
    with pytest.raises(helper.RenderHelperError, match="ownership or mode"):
        helper._validate_tool_component(metadata, final=True, authority=helper.PRODUCTION_GIT_AUTHORITY)
    assert subprocess_calls == []


def test_production_policy_rejects_non_store_path_before_open(monkeypatch):
    opens = []
    monkeypatch.setattr(helper.os, "open", lambda *args, **kwargs: opens.append((args, kwargs)))
    with pytest.raises(helper.RenderHelperError, match="outside the immutable Nix store"):
        helper._open_resolved_regular(Path("/usr/bin/git"), authority=helper.PRODUCTION_GIT_AUTHORITY)
    assert opens == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bytes", LOCAL_TOOL_AUTHORITY.bytes + 1),
        ("sha256", "sha256:" + "0" * 64),
        ("path", "/usr/bin/true"),
        ("authority_receipt_sha256", "sha256:" + "0" * 64),
    ],
)
def test_wrong_tool_manifest_identity_is_refused_before_subprocess(
    tmp_path, monkeypatch, source_case, field, value
):
    descriptor = dict(source_case[6])
    descriptor[field] = value
    _assert_tool_refused_before_subprocess(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        source_case=source_case,
        descriptor=descriptor,
        authority=LOCAL_TOOL_AUTHORITY,
    )


@pytest.mark.parametrize("mutation", ["bad-magic", "bad-helper-hash", "bad-archive-hash", "trailing"])
def test_production_bootstrap_emits_bound_phase1_failures(source_case, mutation):
    _, _, _, _, original, binding, _ = source_case
    wire = bytearray(original)
    if mutation == "bad-magic":
        wire[:8] = b"BADMAGIC"
    elif mutation == "bad-helper-hash":
        offset = helper.PREFIX.size + binding.helper_bytes // 2
        wire[offset] ^= 1
    elif mutation == "bad-archive-hash":
        wire[-1] ^= 1
    else:
        wire.extend(b"x")
    completed = _run_bootstrap(bytes(wire))
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert helper.validate_phase1_failure(receipt, binding=binding) == receipt
    assert receipt["cleanup"] == "not-created"


@pytest.mark.parametrize("mutation", ["missing-pax", "wrong-pax", "duplicate", "dotdot", "dot", "double-slash", "dotgit", "symlink", "other-root"])
def test_malformed_archive_fails_closed_after_verified_cleanup(tmp_path, source_case, mutation):
    files, _, request, helper_source, _, _, tool_descriptor = source_case
    archive = tmp_path / (mutation + ".tar")
    _archive(archive, files, commit=request["source_commit"], mutation=mutation)
    changed = dict(request)
    changed["archive_sha256"] = _digest(archive.read_bytes())
    changed["artifact_ref"] = "artifact:" + changed["archive_sha256"]
    changed.pop("request_sha256")
    changed["request_sha256"] = _digest(canonical(changed))
    tool_descriptor = helper.describe_tool(changed, _test_tool_authority=LOCAL_TOOL_AUTHORITY)
    wire = helper.packet(
        helper_source,
        changed,
        archive,
        tool_descriptor=tool_descriptor,
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    completed = _run_bootstrap(wire)
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 1
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    assert (
        helper.validate_terminal(
            receipt,
            binding=binding,
            request=changed,
            tool_descriptor=tool_descriptor,
            _test_tool_authority=LOCAL_TOOL_AUTHORITY,
        )
        == receipt
    )
    assert receipt["schema"] == helper.FAILURE_SCHEMA
    assert receipt["stage"] == "archive"
    assert receipt["outcome"] == "FAILED"
    assert receipt["cleanup"] == "removed"


def test_reconstructed_tree_mismatch_is_a_source_failure(source_case):
    _, archive, request, helper_source, _, _, tool_descriptor = source_case
    changed = dict(request)
    changed["source_tree"] = "0" * 40
    changed.pop("request_sha256")
    changed["request_sha256"] = _digest(canonical(changed))
    tool_descriptor = helper.describe_tool(changed, _test_tool_authority=LOCAL_TOOL_AUTHORITY)
    wire = helper.packet(
        helper_source,
        changed,
        archive,
        tool_descriptor=tool_descriptor,
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    completed = _run_bootstrap(wire)
    receipt = json.loads(completed.stdout)
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    assert (
        helper.validate_terminal(
            receipt,
            binding=binding,
            request=changed,
            tool_descriptor=tool_descriptor,
            _test_tool_authority=LOCAL_TOOL_AUTHORITY,
        )
        == receipt
    )
    assert completed.returncode == 1
    assert receipt["stage"] == "source"
    assert receipt["diagnostic_code"] == "IDENTITY_MISMATCH"
    assert receipt["cleanup"] == "removed"


@pytest.mark.parametrize("field,path", sorted(helper.SOURCE_DIGEST_PATHS.items()))
def test_each_bound_source_file_digest_is_verified(tmp_path, source_case, field, path):
    original_files, _, original_request, helper_source, _, _, tool_descriptor = source_case
    changed_files = dict(original_files)
    changed_files[path] += b"# changed candidate source\n"
    source = tmp_path / "changed-source"
    source.mkdir()
    _write_source(source, changed_files)
    tree = _tree_hash(source)
    archive = tmp_path / "changed-source.tar"
    _archive(archive, changed_files, commit=original_request["source_commit"])
    request = _request(changed_files, archive, tree=tree)
    request[field] = original_request[field]
    request.pop("request_sha256")
    request["request_sha256"] = _digest(canonical(request))
    tool_descriptor = helper.describe_tool(request, _test_tool_authority=LOCAL_TOOL_AUTHORITY)
    wire = helper.packet(
        helper_source,
        request,
        archive,
        tool_descriptor=tool_descriptor,
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    completed = _run_bootstrap(wire)
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert (
        helper.validate_terminal(
            receipt,
            binding=binding,
            request=request,
            tool_descriptor=tool_descriptor,
            _test_tool_authority=LOCAL_TOOL_AUTHORITY,
        )
        == receipt
    )
    assert receipt["stage"] == "source"
    assert receipt["diagnostic_code"] == "IDENTITY_MISMATCH"
    assert receipt["cleanup"] == "removed"


def test_cleanup_failure_overrides_verified_source_and_never_emits_hold(tmp_path, source_case):
    _, archive, request, _, _, binding, tool_descriptor = source_case
    reconstructed = io.BytesIO(_helper_frame(request, tool_descriptor, archive))

    def fail_cleanup(_path, **_kwargs):
        raise OSError("injected cleanup failure")

    receipt = helper.execute_packet(
        reconstructed,
        binding=binding,
        scratch_root=tmp_path / "scratch",
        cleanup_tree=fail_cleanup,
        _test_executor=lambda *_: "source-only-cleanup-test",
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    assert receipt["schema"] == helper.FAILURE_SCHEMA
    assert receipt["outcome"] == "AMBIGUOUS"
    assert receipt["stage"] == "cleanup"
    assert receipt["diagnostic_code"] == "CLEANUP_FAILED"
    assert receipt["original_stage"] == "source-verified"
    assert receipt["cleanup"] == "failed"
    assert (
        helper.validate_terminal(
            receipt,
            binding=binding,
            request=request,
            tool_descriptor=tool_descriptor,
            _test_tool_authority=LOCAL_TOOL_AUTHORITY,
        )
        == receipt
    )


@pytest.mark.parametrize("state", ["residue", "symlink"])
def test_untrusted_scratch_root_is_refused_before_archive_write(tmp_path, source_case, state):
    _, archive, request, _, _, binding, tool_descriptor = source_case
    scratch = tmp_path / "scratch"
    if state == "residue":
        scratch.mkdir(mode=0o700)
        (scratch / "unowned-state").write_text("residue")
    else:
        target = tmp_path / "target"
        target.mkdir()
        scratch.symlink_to(target, target_is_directory=True)
    framed = io.BytesIO(_helper_frame(request, tool_descriptor, archive))
    receipt = helper.execute_packet(
        framed,
        binding=binding,
        scratch_root=scratch,
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    assert receipt["schema"] == helper.FAILURE_SCHEMA
    assert receipt["stage"] == "scratch"
    assert receipt["diagnostic_code"] == "IDENTITY_MISMATCH"
    assert receipt["cleanup"] == "not-created"
    assert (
        helper.validate_terminal(
            receipt,
            binding=binding,
            request=request,
            tool_descriptor=tool_descriptor,
            _test_tool_authority=LOCAL_TOOL_AUTHORITY,
        )
        == receipt
    )


def test_only_explicit_prebootstrap_injection_can_emit_test_marker(source_case):
    _, _, request, _, wire, binding, tool_descriptor = source_case
    program = (
        "def injected(source,request): return 'prebootstrap-marker'\n"
        "_TEST_ONLY_EXECUTOR=injected\n"
        "_TEST_ONLY_TOOL_AUTHORITY="
        + _authority_literal()
        + "\n"
        + helper.BOOTSTRAP
    )
    completed = subprocess.run([sys.executable, "-I", "-c", program], input=wire, capture_output=True, check=False)
    assert completed.stdout, completed.stderr.decode(errors="replace")
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert (
        helper.validate_terminal(
            receipt,
            binding=binding,
            request=request,
            tool_descriptor=tool_descriptor,
            allow_test_marker=True,
            _test_tool_authority=LOCAL_TOOL_AUTHORITY,
        )
        == receipt
    )
    assert receipt["schema"] == helper.TEST_MARKER_SCHEMA
    assert receipt["marker"] == "prebootstrap-marker"


def test_request_field_cannot_activate_test_executor(source_case):
    _, archive, request, helper_source, _, _, tool_descriptor = source_case
    hostile = dict(request)
    hostile["test_executor"] = "emit-marker"
    hostile.pop("request_sha256")
    hostile["request_sha256"] = _digest(canonical(hostile))
    completed = _run_bootstrap(_raw_wire(helper_source, hostile, tool_descriptor, archive))
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert receipt["schema"] == helper.PHASE1_FAILURE_SCHEMA
    assert receipt["outcome"] == "FAILED"
    assert receipt["stage"] == "phase1-bootstrap"
    assert receipt["diagnostic_code"] == "TOOL_REQUEST_BINDING_MISMATCH"


def test_archive_content_cannot_activate_test_executor(tmp_path):
    files = {**_source_files(), "executor-trigger.py": b"_TEST_ONLY_EXECUTOR=lambda *_: 'archive-marker'\n"}
    source = tmp_path / "source"
    source.mkdir()
    _write_source(source, files)
    tree = _tree_hash(source)
    archive = tmp_path / "source.tar"
    _archive(archive, files, commit="b" * 40)
    request = _request(files, archive, tree=tree)
    helper_source = Path(helper.__file__).read_bytes()
    tool_descriptor = helper.describe_tool(request, _test_tool_authority=LOCAL_TOOL_AUTHORITY)
    wire = helper.packet(
        helper_source,
        request,
        archive,
        tool_descriptor=tool_descriptor,
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    completed = _run_bootstrap(wire)
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert receipt["schema"] == helper.A2_FAILURE_SCHEMA
    assert receipt["stage"] == "a2-tool"
    assert "marker" not in receipt


def test_environment_and_module_globals_cannot_activate_normal_production_main(source_case, monkeypatch):
    _, archive, request, _, _, binding, tool_descriptor = source_case
    for field, value in {
        "_BOOTSTRAP_REQUEST_BYTES": binding.request_bytes,
        "_BOOTSTRAP_HELPER_BYTES": binding.helper_bytes,
        "_BOOTSTRAP_TOOL_BYTES": binding.tool_descriptor_bytes,
        "_BOOTSTRAP_ARCHIVE_BYTES": binding.archive_bytes,
        "_BOOTSTRAP_REQUEST_SHA256": binding.request_sha256,
        "_BOOTSTRAP_HELPER_SHA256": binding.helper_sha256,
        "_BOOTSTRAP_TOOL_SHA256": binding.tool_descriptor_sha256,
        "_BOOTSTRAP_ARCHIVE_SHA256": binding.archive_sha256,
        "_TEST_ONLY_EXECUTOR": lambda *_: "module-marker",
        "_TEST_ONLY_TOOL_AUTHORITY": LOCAL_TOOL_AUTHORITY,
        "PRODUCTION_GIT_AUTHORITY": LOCAL_TOOL_AUTHORITY,
        "_test_executor": lambda *_: "module-marker",
        "test_executor": lambda *_: "module-marker",
    }.items():
        monkeypatch.setattr(helper, field, value, raising=False)
    monkeypatch.setenv("TGW_NIX_RENDER_EXECUTOR", "environment-marker")
    output = io.BytesIO()
    return_code = helper.main(input_stream=io.BytesIO(_helper_frame(request, tool_descriptor, archive)), output_stream=output)
    receipt = json.loads(output.getvalue())
    assert return_code == 1
    assert receipt["schema"] == helper.FAILURE_SCHEMA
    assert receipt["stage"] == "tool"
    assert "marker" not in receipt


@pytest.mark.parametrize("bound", ["members", "unpacked"])
def test_archive_member_and_unpacked_bounds_are_enforced(tmp_path, source_case, monkeypatch, bound):
    files, _, request, helper_source, _, _, tool_descriptor = source_case
    archive = tmp_path / "bounded.tar"
    _archive(archive, files, commit=request["source_commit"])
    changed = dict(request)
    changed["archive_sha256"] = _digest(archive.read_bytes())
    changed["artifact_ref"] = "artifact:" + changed["archive_sha256"]
    changed.pop("request_sha256")
    changed["request_sha256"] = _digest(canonical(changed))
    tool_descriptor = helper.describe_tool(changed, _test_tool_authority=LOCAL_TOOL_AUTHORITY)
    wire = helper.packet(
        helper_source,
        changed,
        archive,
        tool_descriptor=tool_descriptor,
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    framed = io.BytesIO(_helper_frame(changed, tool_descriptor, archive))
    if bound == "members":
        monkeypatch.setattr(helper, "MAX_ARCHIVE_MEMBERS", 1)
    else:
        monkeypatch.setattr(helper, "MAX_UNPACKED_BYTES", 1)
    receipt = helper.execute_packet(
        framed,
        binding=binding,
        scratch_root=tmp_path / "scratch",
        _test_tool_authority=LOCAL_TOOL_AUTHORITY,
    )
    assert receipt["stage"] == "archive"
    assert receipt["diagnostic_code"] == "BOUND_EXCEEDED"
    assert receipt["cleanup"] == "removed"
    assert (
        helper.validate_terminal(
            receipt,
            binding=binding,
            request=changed,
            tool_descriptor=tool_descriptor,
            _test_tool_authority=LOCAL_TOOL_AUTHORITY,
        )
        == receipt
    )


def test_source_helper_contains_no_transport_or_ambient_executor():
    source = Path(helper.__file__).read_text()
    assert "ssh" not in source.lower()
    assert "TGW_NIX_RENDER_EXECUTOR" not in source
    assert '"NIX_REMOTE": "local"' in source
    assert '"--offline"' in source
    assert '"substituters"' in source
    assert '"trusted-public-keys"' in source
    assert '"allow-import-from-derivation"' in source
    assert '"--no-write-lock-file"' in source
    assert 'drv + "^out"' in source
    assert shutil.which("git") is not None
