import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import tgw.application_bootstrap_runtime as runtime
from tgw.application_bootstrap_bundle import _bundle_from_archive
from tgw.application_bootstrap_entrypoint import _elf_closure
from tgw.application_deployment_contract import PROJECTION_PATH


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _receipt(value):
    return {**value, "receipt_sha256": runtime._digest(_canonical(value))}


def _file_binding(path: Path):
    binding, fd = runtime._binding(path, directory=False, trusted_uid=os.getuid())
    os.close(fd)
    return binding


def _protected_dir(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _real_launcher_source_receipt(
    tmp_path: Path,
    *,
    launcher_source: bytes | None = None,
    controller_bundle: Path | None = None,
) -> tuple[dict, dict]:
    source_path = tmp_path / "w09-controller-launcher.c"
    source_path.write_bytes(
        launcher_source
        if launcher_source is not None
        else Path("src/tgw/w09_controller_launcher.c").read_bytes()
    )
    source_path.chmod(0o400)
    source_binding = _file_binding(source_path)
    bundle = controller_bundle if controller_bundle is not None else source_path
    source = _receipt(
        {
            "schema": runtime.SOURCE_SCHEMA,
            "controller_source": {"commit": "a" * 40, "tree": "b" * 40},
            "controller_bundle": {
                "path": str(bundle),
                "sha256": runtime._digest(bundle.read_bytes()),
            },
            "controller_launcher_source": {
                "archive_path": "src/tgw/w09_controller_launcher.c",
                "materialized_path": str(source_path),
                "sha256": source_binding["sha256"],
                "size": source_binding["size"],
                "identity": [
                    source_binding[name]
                    for name in ("dev", "ino", "uid", "gid", "mode", "nlink", "size")
                ],
                "build_contract": "static-elf-no-interp-no-needed@1",
            },
            "application_candidate": {
                "commit": "c" * 40,
                "tree": "d" * 40,
                "archive_sha256": "sha256:" + "5" * 64,
                "projection_sha256": "sha256:" + "6" * 64,
            },
            "materialization": {"producer": "real-build-test"},
        }
    )
    receipt_path = tmp_path / "controller-source.json"
    receipt_path.write_bytes(_canonical(source))
    receipt_path.chmod(0o400)
    return _file_binding(receipt_path), source


def _real_launcher_build(
    tmp_path: Path,
    *,
    source_binding: dict,
    source: dict,
    binding_path: Path,
    stem: str = "real",
    occupied_output: bool = False,
) -> dict:
    compiler = Path(shutil.which("cc")).resolve()
    tracer = Path(shutil.which("strace")).resolve()
    scratch = _protected_dir(tmp_path / f"{stem}-scratch")
    environment = {
        "PATH": "/usr/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TMPDIR": str(scratch),
    }
    discovery_root = _protected_dir(tmp_path / f"{stem}-discovery")
    discovery = runtime.discover_launcher_build_inputs(
        launcher_source=Path(source["controller_launcher_source"]["materialized_path"]),
        compiler_path=compiler,
        tracer_path=tracer,
        output_root=discovery_root,
        binding_path=binding_path,
        environment=environment,
        trusted_uid=os.getuid(),
    )
    assert discovery["inputs"]
    environment_root = _protected_dir(tmp_path / f"{stem}-environment")
    environment_receipt = runtime.issue_build_environment_manifest(
        compiler_path=compiler,
        tracer_path=tracer,
        discovery=discovery,
        environment=environment,
        output_root=environment_root,
        trusted_uid=os.getuid(),
    )
    build_root = _protected_dir(tmp_path / f"{stem}-build")
    if occupied_output:
        output_name = (
            "launcher-"
            + source["controller_launcher_source"]["sha256"].removeprefix("sha256:")
            + "-"
            + runtime.hashlib.sha256(str(binding_path).encode()).hexdigest()
        )
        neighbor = build_root / output_name
        neighbor.write_bytes(b"neighbor")
        neighbor.chmod(0o400)
    return runtime.produce_launcher_build(
        controller_source_receipt=source_binding,
        build_environment_receipt=_file_binding(Path(environment_receipt["path"])),
        output_root=build_root,
        binding_path=binding_path,
        trusted_uid=os.getuid(),
    )


def _planned_runtime_config(
    output_root: Path,
    *,
    python_path: Path,
    native_files=(),
    runtime_trees=(),
    import_roots=(),
) -> Path:
    held = []
    try:
        files = []
        raw_by_path = {}
        for path in sorted({Path(python_path), *map(Path, native_files)}, key=str):
            binding, fd = runtime._binding(
                path,
                directory=False,
                trusted_uid=os.getuid(),
            )
            held.append(fd)
            files.append(binding)
            raw_by_path[str(path)] = os.pread(fd, binding["size"] + 1, 0)
        runtime._resolved_elf(files, raw_by_path)
        trees = []
        for path in sorted(set(map(Path, runtime_trees)), key=str):
            binding, fd = runtime._binding(
                path,
                directory=True,
                trusted_uid=os.getuid(),
            )
            held.append(fd)
            trees.append(binding)
        unsigned = {
            "schema": runtime.RUNTIME_SCHEMA,
            "files": files,
            "trees": trees,
            "import_roots": sorted(set(map(str, import_roots))),
        }
        manifest_hash = runtime._digest(_canonical(unsigned))
        return output_root / f"runtime-{manifest_hash.removeprefix('sha256:')}.fds"
    finally:
        for fd in held:
            os.close(fd)


def test_real_launcher_build_producer_pins_discovered_environment_and_static_output(
    tmp_path,
):
    source_binding, source = _real_launcher_source_receipt(tmp_path)
    build = _real_launcher_build(
        tmp_path,
        source_binding=source_binding,
        source=source,
        binding_path=Path("/etc/tgw/w09-controller-runtime.fds"),
    )
    assert build["source_sha256"] == source["controller_launcher_source"]["sha256"]
    assert build["compiler"]["sha256"] == _file_binding(
        Path(shutil.which("cc")).resolve()
    )["sha256"]
    assert build["executed_argv_sha256"] == runtime._digest(
        _canonical(build["executed_argv"])
    )
    assert build["launcher"]["elf"]["pt_interp"] is None
    assert build["launcher"]["elf"]["needed"] == []


def test_real_launcher_build_rejects_source_swap_after_compiler_use(
    tmp_path,
    monkeypatch,
):
    source_binding, source = _real_launcher_source_receipt(tmp_path)
    source_path = Path(source["controller_launcher_source"]["materialized_path"])
    real_run = runtime._run_build_trace
    calls = 0

    def run_then_swap(**kwargs):
        nonlocal calls
        result = real_run(**kwargs)
        calls += 1
        if calls == 2:
            replaced = source_path.with_suffix(".replaced")
            source_path.rename(replaced)
            source_path.write_bytes(replaced.read_bytes())
            source_path.chmod(0o400)
        return result

    monkeypatch.setattr(runtime, "_run_build_trace", run_then_swap)
    with pytest.raises(runtime.ControllerRuntimeError, match="changed during use"):
        _real_launcher_build(
            tmp_path,
            source_binding=source_binding,
            source=source,
            binding_path=Path("/etc/tgw/w09-controller-runtime.fds"),
            stem="swap",
        )
    assert list((tmp_path / "swap-build").iterdir()) == []


def test_discovery_never_overwrites_existing_output_and_closes_held_inputs(tmp_path):
    source_binding, source = _real_launcher_source_receipt(tmp_path)
    del source_binding
    output = _protected_dir(tmp_path / "occupied-discovery")
    occupied = output / "launcher-discovery"
    occupied.write_bytes(b"neighbor")
    occupied.chmod(0o400)
    scratch = _protected_dir(tmp_path / "occupied-scratch")
    before = len(list(Path("/proc/self/fd").iterdir()))
    with pytest.raises(FileExistsError):
        runtime.discover_launcher_build_inputs(
            launcher_source=Path(source["controller_launcher_source"]["materialized_path"]),
            compiler_path=Path(shutil.which("cc")).resolve(),
            tracer_path=Path(shutil.which("strace")).resolve(),
            output_root=output,
            binding_path=Path("/etc/tgw/w09-controller-runtime.fds"),
            environment={
                "PATH": "/usr/bin",
                "LANG": "C",
                "LC_ALL": "C",
                "TMPDIR": str(scratch),
            },
            trusted_uid=os.getuid(),
        )
    assert occupied.read_bytes() == b"neighbor"
    assert len(list(Path("/proc/self/fd").iterdir())) == before


def test_real_launcher_build_never_removes_preexisting_neighbor(tmp_path):
    source_binding, source = _real_launcher_source_receipt(tmp_path)
    binding_path = Path("/etc/tgw/w09-controller-runtime.fds")
    with pytest.raises(FileExistsError):
        _real_launcher_build(
            tmp_path,
            source_binding=source_binding,
            source=source,
            binding_path=binding_path,
            stem="occupied-build",
            occupied_output=True,
        )
    output_name = (
        "launcher-"
        + source["controller_launcher_source"]["sha256"].removeprefix("sha256:")
        + "-"
        + runtime.hashlib.sha256(str(binding_path).encode()).hexdigest()
    )
    assert (tmp_path / "occupied-build-build" / output_name).read_bytes() == b"neighbor"


@pytest.mark.parametrize(
    "trace",
    [
        b'openat(AT_FDCWD, "relative-input.h", O_RDONLY) = 3\n',
        b'openat(AT_FDCWD, "/etc/ld.so"..., O_RDONLY) = 3\n',
        b'mystery_file_call("/etc/passwd") = 0\n',
        b'[pid 7] openat(AT_FDCWD, "/etc/passwd", <unfinished ...>\n',
    ],
)
def test_build_trace_parser_rejects_relative_truncated_unknown_or_unfinished(
    tmp_path,
    trace,
):
    with pytest.raises(runtime.ControllerRuntimeError, match="compiler trace"):
        runtime._parse_trace_accesses(
            trace,
            scratch=tmp_path,
            excluded=set(),
        )


def test_build_trace_parser_decodes_escapes_and_rejoins_resumed_records(tmp_path):
    trace = (
        b'[pid 7] openat(AT_FDCWD, "/etc/ld\\056so\\056cache", <unfinished ...>\n'
        b'[pid 7] <... openat resumed>O_RDONLY|O_CLOEXEC) = 3\n'
    )
    inputs, accesses = runtime._parse_trace_accesses(
        trace,
        scratch=tmp_path,
        excluded=set(),
    )
    assert Path("/etc/ld.so.cache") in inputs
    assert "/etc/ld.so.cache" in accesses


def _fixture(tmp_path):
    source_binding, source = _real_launcher_source_receipt(tmp_path)
    tree = tmp_path / "site-packages"
    tree.mkdir(mode=0o700)
    (tree / "yaml.py").write_bytes(b"SAFE = True\n")
    (tree / "yaml.py").chmod(0o400)
    tree.chmod(0o500)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    python = Path(sys.executable).resolve()
    native = _native_python_closure(python)
    binding_path = _planned_runtime_config(
        output,
        python_path=python,
        native_files=native,
        runtime_trees=[tree],
        import_roots=[tree],
    )
    build = _real_launcher_build(
        tmp_path,
        source_binding=source_binding,
        source=source,
        binding_path=binding_path,
        stem="final",
    )
    launcher = Path(build["launcher"]["path"])
    return {
        "launcher": launcher,
        "python": python,
        "native": native,
        "source": source_binding,
        "build": _file_binding(Path(build["receipt_path"])),
        "tree": tree,
        "output": output,
    }


def test_materializer_binds_static_launcher_source_application_and_import_tree(tmp_path):
    fixture = _fixture(tmp_path)
    receipt = runtime.materialize_controller_runtime(
        controller_source_receipt=fixture["source"],
        launcher_build_receipt=fixture["build"],
        python_path=fixture["python"],
        native_files=fixture["native"],
        runtime_trees=[fixture["tree"]],
        import_roots=[fixture["tree"]],
        output_root=fixture["output"],
        trusted_uid=os.getuid(),
    )
    assert receipt["schema"] == runtime.SCHEMA
    assert receipt["application_candidate"]["commit"] == "c" * 40
    assert receipt["manifest"]["import_roots"] == [str(fixture["tree"])]
    assert receipt["launcher"]["sha256"] == runtime._digest(fixture["launcher"].read_bytes())
    assert Path(receipt["closure"]["path"]).is_file()


def test_materializer_removes_prior_outputs_when_later_atomic_write_fails(
    tmp_path,
    monkeypatch,
):
    fixture = _fixture(tmp_path)
    real_write = runtime._write_once
    calls = 0

    def fail_second(root_fd, name, raw, mode):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected closure write failure")
        return real_write(root_fd, name, raw, mode)

    monkeypatch.setattr(runtime, "_write_once", fail_second)
    with pytest.raises(OSError, match="closure write"):
        runtime.materialize_controller_runtime(
            controller_source_receipt=fixture["source"],
            launcher_build_receipt=fixture["build"],
            python_path=fixture["python"],
            native_files=fixture["native"],
            runtime_trees=[fixture["tree"]],
            import_roots=[fixture["tree"]],
            output_root=fixture["output"],
            trusted_uid=os.getuid(),
        )
    assert list(fixture["output"].iterdir()) == []


def _native_python_closure(python: Path, extra_files=()):
    paths = {python, *map(Path, extra_files)}
    for executable in tuple(paths):
        elf = _elf_closure(executable.read_bytes())
        if elf is None:
            continue
        if elf["pt_interp"]:
            paths.add(Path(elf["pt_interp"]).resolve())
        for line in subprocess.check_output(["ldd", executable], text=True).splitlines():
            text = line.strip()
            candidate = None
            if " => /" in text:
                candidate = text.split(" => ", 1)[1].split(" ", 1)[0]
            elif text.startswith("/"):
                candidate = text.split(" ", 1)[0]
            if candidate:
                paths.add(Path(candidate).resolve())
    return sorted(paths, key=str)


def _actual_controller_bundle(path: Path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:") as archive:
        sources = [
            item
            for item in Path("src/tgw").iterdir()
            if item.is_file() and item.suffix in {".py", ".c"}
        ]
        sources.append(Path(PROJECTION_PATH))
        for source in sorted(sources, key=str):
            raw = source.read_bytes()
            info = tarfile.TarInfo(source.as_posix())
            info.size = len(raw)
            info.mode = 0o444
            archive.addfile(info, io.BytesIO(raw))
    bundle, _projection, _launcher_source, _launcher_raw = _bundle_from_archive(output.getvalue())
    path.write_bytes(bundle)
    path.chmod(0o400)


def test_actual_controller_bundle_imports_under_compiled_held_runtime(tmp_path):
    python = Path(sys.executable).resolve()
    bundle = tmp_path / "actual-controller.pyz"
    _actual_controller_bundle(bundle)
    site = tmp_path / "site-packages"
    site.mkdir(mode=0o700)
    for module_name in (
        "yaml",
        "cryptography",
        "psycopg2",
        "fastapi",
        "starlette",
        "pydantic",
        "pydantic_core",
        "annotated_types",
        "annotated_doc",
        "anyio",
        "idna",
        "typing_extensions",
        "typing_inspection",
    ):
        imported = Path(__import__(module_name).__file__)
        installed = imported.parent
        target = site / module_name
        if imported.name == "__init__.py":
            shutil.copytree(installed, target, symlinks=False)
        else:
            shutil.copy2(imported, site / imported.name)
        if module_name == "psycopg2":
            shutil.copytree(
                installed.parent / "psycopg2_binary.libs",
                site / "psycopg2_binary.libs",
                symlinks=False,
            )
    cffi_backend = Path(__import__("_cffi_backend").__file__)
    shutil.copy2(cffi_backend, site / cffi_backend.name)
    for bytecode in site.rglob("*.py[co]"):
        bytecode.unlink()
    for item in sorted(site.rglob("*"), reverse=True):
        if not item.is_symlink():
            item.chmod(0o500 if item.is_dir() else 0o400)
    site.chmod(0o500)
    stdlib = Path(f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}")
    runtime_files = [path for path in stdlib.rglob("*") if path.is_file() and not path.is_symlink()]
    runtime_files.extend(path for path in site.rglob("*") if path.is_file() and path.suffix == ".so")
    native_closure = _native_python_closure(python, runtime_files)

    probe_source = (
        f"#define TGW_TRUSTED_UID {os.getuid()}\n"
        "#define TGW_RUNTIME_IMPORT_PROBE 1\n"
    ).encode() + Path("src/tgw/w09_controller_launcher.c").read_bytes()
    source_binding, source = _real_launcher_source_receipt(
        tmp_path,
        launcher_source=probe_source,
        controller_bundle=bundle,
    )
    final_output = tmp_path / "final-output"
    final_output.mkdir(mode=0o700)
    final_config = _planned_runtime_config(
        final_output,
        python_path=python,
        native_files=native_closure,
        runtime_trees=[site],
        import_roots=[site],
    )
    build = _real_launcher_build(
        tmp_path,
        source_binding=source_binding,
        source=source,
        binding_path=final_config,
        stem="probe",
    )
    launcher = Path(build["launcher"]["path"])
    final = runtime.materialize_controller_runtime(
        controller_source_receipt=source_binding,
        launcher_build_receipt=_file_binding(Path(build["receipt_path"])),
        python_path=python,
        native_files=native_closure,
        runtime_trees=[site],
        import_roots=[site],
        output_root=final_output,
        trusted_uid=os.getuid(),
    )
    assert final["launcher_config"]["path"] == str(final_config)
    result = subprocess.run([launcher], check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schema"] == "tgw-w09-runtime-probe/v1"
