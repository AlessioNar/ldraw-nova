#!/usr/bin/env python3
"""Store the bundled reference models compressed, and materialise them on demand.

The repository keeps ``models-packed/``: a handful of deterministic ``.tar.xz``
shards plus a small uncompressed ``manifest.json``. Everything that reads the
models keeps using ``data/models-annotated/*.mpd``; that directory is created by
``unpack`` (setup, image build, tests) and is gitignored.

    tools/models_pack.py pack                 data/models-annotated -> models-packed
    tools/models_pack.py unpack               models-packed -> data/models-annotated
    tools/models_pack.py verify               check shards and the unpacked directory
    tools/models_pack.py add FILE...          add or replace models, re-pack their shards only
    tools/models_pack.py list [PATTERN]       what exists, without unpacking

Python 3 standard library only (tarfile + lzma), so it runs in the image's
build stages and in a fresh clone before any environment exists.

Layout: a model belongs to the shard of its set number, in blocks of
SHARD_DIVISOR (10270-1.mpd -> s10200, 42042-1_Tower-Crane.mpd -> s42000). Packing is
deterministic (sorted members, fixed owner and mtime, fixed xz settings), and a
shard whose content is unchanged is never rewritten, so git stores nothing new.
"""
import argparse
import concurrent.futures
import fnmatch
import hashlib
import io
import json
import lzma
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKED = ROOT / "models-packed"
TARGET = ROOT / "data" / "models-annotated"
MANIFEST = "manifest.json"
FORMAT = 1
SHARD_DIVISOR = 100
MTIME = 946684800  # 2000-01-01: every unpacked file carries it, so mtime-keyed caches are stable
PRESET = 6
LINE_ARITY = {"1": 15, "2": 8, "3": 11, "4": 14, "5": 14}


class PackError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def shard_of(name):
    match = re.match(r"\d+", name)
    return f"s{int(match.group()) // SHARD_DIVISOR * SHARD_DIVISOR:05d}" if match else "misc"


def shard_file(shard):
    return f"{shard}.tar.xz"


# --- parsing and provenance --------------------------------------------------

def header_values(text, keyword):
    """Distinct values of `0 <keyword> ...` meta lines, in order of appearance."""
    found = []
    for line in text.splitlines():
        match = re.match(rf"\s*0\s+{keyword}\s*:?\s*(.*\S)\s*$", line)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def validate_ldraw(name, data):
    """Cheap structural check: UTF-8 text, only LDraw line types with enough fields, at
    least one geometry or sub-file line. (A bare .ldr body without `0 FILE` is valid.)"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackError(f"{name}: not UTF-8 text ({error})") from None
    commands = 0
    for number, line in enumerate(text.splitlines(), 1):
        tokens = line.split()
        if not tokens:
            continue
        kind = tokens[0]
        if kind == "0":
            continue
        if kind not in LINE_ARITY or len(tokens) < LINE_ARITY[kind]:
            raise PackError(f"{name}:{number}: not an LDraw line: {line[:60]!r}")
        commands += 1
    if commands == 0:
        raise PackError(f"{name}: no geometry or sub-file lines, not an LDraw model")


def describe(name, data, licence=None, author=None, strict=True):
    """Manifest entry for one model. A licence comes from the file's own header
    or from an explicit override; there is no silent default. `strict=False` (pack)
    stores what is already in the folder as it is: some shipped models have a
    malformed line, and the manifest must reproduce them byte for byte."""
    if strict:
        validate_ldraw(name, data)
    text = data.decode("utf-8")
    licences = header_values(text, r"!LICENSE")
    authors = header_values(text, "Author")
    if licence is None and not licences:
        raise PackError(f"{name}: no '0 !LICENSE' header; pass --licence (and --author) to record one")
    return {
        "file": name,
        "shard": shard_of(name),
        "sha256": sha256(data),
        "size": len(data),
        "licence": licence if licence is not None else "; ".join(licences),
        "author": author if author is not None else "; ".join(authors),
    }


# --- manifest ----------------------------------------------------------------

def load_manifest(packed):
    path = packed / MANIFEST
    if not path.is_file():
        return {"format": FORMAT, "shard_divisor": SHARD_DIVISOR, "compression": "tar.xz", "shards": {}, "models": []}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise PackError(f"{path}: unsupported manifest format {manifest.get('format')!r}")
    return manifest


def dump_manifest(manifest):
    """One line per shard and per model, so a change is a one-line git diff."""
    def row(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    head = {k: manifest[k] for k in ("format", "shard_divisor", "compression")}
    lines = ["{", *[f'  {json.dumps(k)}: {json.dumps(v)},' for k, v in head.items()]]
    shards = sorted(manifest["shards"].items())
    lines.append('  "shards": {')
    lines += [f"    {json.dumps(k)}: {row(v)}" + ("," if i < len(shards) - 1 else "") for i, (k, v) in enumerate(shards)]
    lines.append("  },")
    models = sorted(manifest["models"], key=lambda m: m["file"])
    lines.append('  "models": [')
    lines += [f"    {row(m)}" + ("," if i < len(models) - 1 else "") for i, m in enumerate(models)]
    lines += ["  ]", "}"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# --- shards ------------------------------------------------------------------

def build_tar(members):
    """members: {name: bytes}. Byte-for-byte reproducible."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(members[name]), MTIME, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(members[name]))
    return buffer.getvalue()


def compress(tar_bytes):
    return lzma.compress(tar_bytes, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=PRESET)


def read_shard(path):
    """{name: bytes} of a shard file, plus its decompressed tar bytes."""
    with lzma.open(path) as stream:
        tar_bytes = stream.read()
    members = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as archive:
        for info in archive:
            if info.isfile():
                members[info.name] = archive.extractfile(info).read()
    return members, tar_bytes


def store_shard(packed, shard, members):
    """Write the shard unless the stored one already holds exactly this content.
    Returns (manifest row, whether bytes changed)."""
    path = packed / shard_file(shard)
    tar_bytes = build_tar(members)
    if path.is_file():
        try:
            _, existing = read_shard(path)
        except (lzma.LZMAError, tarfile.TarError, EOFError):
            existing = None
        if existing == tar_bytes:
            blob = path.read_bytes()
            return {"file": path.name, "sha256": sha256(blob), "size": len(blob)}, False
    blob = compress(tar_bytes)
    write_atomic(path, blob)
    return {"file": path.name, "sha256": sha256(blob), "size": len(blob)}, True


def prune_shards(packed, keep):
    for path in packed.glob("*.tar.xz"):
        if path.name not in keep:
            path.unlink()


# --- commands ----------------------------------------------------------------

def worker_count(jobs):
    return max(1, min(jobs, 8, os.cpu_count() or 1))


def cmd_pack(args):
    source, packed = Path(args.source), Path(args.packed)
    names = sorted(p.name for p in source.glob("*.mpd") if p.is_file())
    if not names:
        raise PackError(f"no .mpd files in {source}")
    old = {m["file"]: m for m in load_manifest(packed)["models"]}
    by_shard, models = {}, []
    for name in names:
        data = (source / name).read_bytes()
        previous = old.get(name)
        # Keep a recorded override while the file is unchanged.
        keep = previous if previous and previous["sha256"] == sha256(data) else None
        entry = describe(name, data, keep["licence"] if keep else None, keep["author"] if keep else None, strict=False)
        models.append(entry)
        by_shard.setdefault(entry["shard"], {})[name] = data
    manifest = {"format": FORMAT, "shard_divisor": SHARD_DIVISOR, "compression": "tar.xz", "shards": {}, "models": models}
    with concurrent.futures.ThreadPoolExecutor(worker_count(len(by_shard))) as pool:  # lzma releases the GIL
        stored = {s: pool.submit(store_shard, packed, s, m) for s, m in by_shard.items()}
    changed = 0
    for shard in sorted(stored):
        manifest["shards"][shard], did = stored[shard].result()
        changed += did
    prune_shards(packed, {s["file"] for s in manifest["shards"].values()})
    write_atomic(packed / MANIFEST, dump_manifest(manifest))
    total = sum(s["size"] for s in manifest["shards"].values())
    print(f"packed {len(models)} models into {len(by_shard)} shards, {total / 2**20:.1f} MiB; {changed} shard(s) rewritten")


def stamp_path(target):
    return target.parent / f".{target.name}.stamp"


def read_stamp(target):
    try:
        return json.loads(stamp_path(target).read_text())
    except (OSError, ValueError):
        return {}


def shard_current(target, shard, row, entries, stamp):
    if stamp.get(shard) != row["sha256"]:
        return False
    for entry in entries:
        path = target / entry["file"]
        try:
            if path.stat().st_size != entry["size"]:
                return False
        except OSError:
            return False
    return True


def extract_shard(packed, target, shard, row, entries):
    blob = (packed / row["file"]).read_bytes()
    if sha256(blob) != row["sha256"]:
        raise PackError(f"{row['file']}: checksum differs from the manifest")
    members, _ = read_shard(packed / row["file"])
    wanted = {e["file"]: e for e in entries}
    for name, entry in wanted.items():
        if name not in members:
            raise PackError(f"{row['file']}: missing {name}")
        if sha256(members[name]) != entry["sha256"]:
            raise PackError(f"{row['file']}: {name} differs from the manifest sha256")
        destination = target / name
        write_atomic(destination, members[name])
        os.utime(destination, (MTIME, MTIME))
    return len(wanted)


def cmd_unpack(args):
    packed, target = Path(args.packed), Path(args.target)
    manifest = load_manifest(packed)
    if not manifest["models"]:
        raise PackError(f"{packed}: empty or missing manifest")
    grouped = {}
    for entry in manifest["models"]:
        grouped.setdefault(entry["shard"], []).append(entry)
    stamp = {} if args.force else read_stamp(target)
    todo = [s for s in sorted(grouped) if not shard_current(target, s, manifest["shards"][s], grouped[s], stamp)]
    target.mkdir(parents=True, exist_ok=True)
    if todo:
        with concurrent.futures.ThreadPoolExecutor(worker_count(len(todo))) as pool:  # lzma releases the GIL
            futures = {s: pool.submit(extract_shard, packed, target, s, manifest["shards"][s], grouped[s]) for s in todo}
            count = sum(f.result() for f in futures.values())
        stamp = {s: manifest["shards"][s]["sha256"] for s in grouped if s not in todo and stamp.get(s)} | \
                {s: manifest["shards"][s]["sha256"] for s in todo}
        write_atomic(stamp_path(target), json.dumps(stamp, sort_keys=True).encode())
        print(f"unpacked {count} models from {len(todo)}/{len(grouped)} shards into {target}")
    else:
        print(f"{target} is current ({len(manifest['models'])} models)")
    known = {e["file"] for e in manifest["models"]}
    extra = sorted(p.name for p in target.glob("*.mpd") if p.name not in known)
    if extra and args.clean:
        for name in extra:
            (target / name).unlink()
        print(f"removed {len(extra)} file(s) not in the manifest")
    elif extra:
        print(f"note: {len(extra)} .mpd file(s) in {target} are not in the manifest (use --clean to remove)", file=sys.stderr)


def cmd_verify(args):
    packed, target = Path(args.packed), Path(args.target)
    manifest = load_manifest(packed)
    problems, checked = [], 0
    grouped = {}
    for entry in manifest["models"]:
        grouped.setdefault(entry["shard"], {})[entry["file"]] = entry
    for shard, entries in sorted(grouped.items()):
        row = manifest["shards"].get(shard)
        path = packed / shard_file(shard)
        if not row or not path.is_file():
            problems.append(f"{shard}: shard file missing")
            continue
        if sha256(path.read_bytes()) != row["sha256"]:
            problems.append(f"{path.name}: sha256 differs from the manifest")
            continue
        members, _ = read_shard(path)
        for name in sorted(set(members) | set(entries)):
            if name not in entries:
                problems.append(f"{path.name}: {name} is not in the manifest")
            elif name not in members:
                problems.append(f"{path.name}: {name} missing")
            elif sha256(members[name]) != entries[name]["sha256"]:
                problems.append(f"{path.name}: {name} differs from the manifest")
            else:
                checked += 1
    for stray in sorted(p.name for p in packed.glob("*.tar.xz") if p.name not in {s["file"] for s in manifest["shards"].values()}):
        problems.append(f"{stray}: not in the manifest")
    if target.is_dir() and not args.packed_only:
        for entry in manifest["models"]:
            path = target / entry["file"]
            if not path.is_file():
                problems.append(f"{target.name}/{entry['file']}: missing (run unpack)")
            elif sha256(path.read_bytes()) != entry["sha256"]:
                problems.append(f"{target.name}/{entry['file']}: differs from the manifest (run unpack --force)")
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        raise PackError(f"verify failed: {len(problems)} problem(s)")
    print(f"ok: {checked} models in {len(grouped)} shards" + ("" if args.packed_only or not target.is_dir() else f", {target} matches"))


def cmd_add(args):
    packed, target = Path(args.packed), Path(args.target)
    manifest = load_manifest(packed)
    entries = {m["file"]: m for m in manifest["models"]}
    existing = set(entries)
    incoming = {}
    for file in args.files:
        path = Path(file)
        if path.suffix.lower() != ".mpd":
            raise PackError(f"{path}: models are stored as .mpd")
        if path.name in incoming:
            raise PackError(f"{path.name}: given twice")
        data = path.read_bytes()
        incoming[path.name] = (data, describe(path.name, data, args.licence, args.author))
    touched = {}
    for name, (data, entry) in incoming.items():
        touched.setdefault(entry["shard"], {})[name] = data
        entries[name] = entry
    for shard, new in sorted(touched.items()):
        path = packed / shard_file(shard)
        members = read_shard(path)[0] if path.is_file() else {}
        members.update(new)
        manifest["shards"][shard], _ = store_shard(packed, shard, members)
    manifest["models"] = list(entries.values())
    write_atomic(packed / MANIFEST, dump_manifest(manifest))
    for name, (_, entry) in sorted(incoming.items()):
        print(f"{'replaced' if name in existing else 'added'} {name} -> {entry['shard']} ({entry['licence']})")
    print(f"re-packed {len(touched)} shard(s): {', '.join(shard_file(s) for s in sorted(touched))}")
    if not args.no_unpack:
        cmd_unpack(argparse.Namespace(packed=str(packed), target=str(target), force=False, clean=False))


def cmd_list(args):
    manifest = load_manifest(Path(args.packed))
    rows = [m for m in manifest["models"] if not args.pattern or fnmatch.fnmatch(m["file"], args.pattern)]
    for m in rows:
        print(f"{m['file']}\t{m['shard']}\t{m['size']}\t{m['sha256'][:12]}\t{m['licence']}")
    print(f"{len(rows)} of {len(manifest['models'])} models, {len(manifest['shards'])} shards", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--packed", default=str(PACKED), help="folder with the shards and manifest (default: models-packed/)")
    parser.add_argument("--target", default=str(TARGET), help="materialised folder (default: data/models-annotated/)")
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack", help="pack the target folder into shards and a manifest")
    pack.add_argument("--source", help="folder of .mpd files (default: --target)")
    unpack = sub.add_parser("unpack", help="materialise the target folder; skips shards that are already current")
    unpack.add_argument("--force", action="store_true", help="extract every shard again")
    unpack.add_argument("--clean", action="store_true", help="remove .mpd files that are not in the manifest")
    verify = sub.add_parser("verify", help="check shard and file checksums, and the target folder")
    verify.add_argument("--packed-only", action="store_true", help="do not look at the target folder")
    add = sub.add_parser("add", help="add or replace models and re-pack only their shards")
    add.add_argument("files", nargs="+", metavar="FILE")
    add.add_argument("--licence", help="record this licence instead of the file's '0 !LICENSE' header")
    add.add_argument("--author", help="record this author instead of the file's '0 Author:' header")
    add.add_argument("--no-unpack", action="store_true", help="leave the target folder alone")
    lst = sub.add_parser("list", help="list models from the manifest without unpacking")
    lst.add_argument("pattern", nargs="?", help="glob on the file name, e.g. '42*'")
    args = parser.parse_args(argv)
    if args.command == "pack" and not args.source:
        args.source = args.target
    try:
        {"pack": cmd_pack, "unpack": cmd_unpack, "verify": cmd_verify, "add": cmd_add, "list": cmd_list}[args.command](args)
    except PackError as error:
        print(f"models_pack: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
