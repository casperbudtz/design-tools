"""
OptiLayer Indexer — business logic.

Library module loaded by the Design Tools server via importlib.
Do not add an HTTP server or `if __name__ == "__main__"` block.

DBS format reverse-engineered: 13-byte header, then records of
{uint16 slot, counted-string name, two action blocks (datetime + username),
counted-string comment}. Counted strings: uint32 length prefix where bit 31 = 1
means UTF-16-LE (length in chars), bit 31 = 0 means UTF-8 (length in bytes).
The `modified` datetime is taken from the second action block.

Incremental indexing caches per-folder mtime so repeat runs only re-parse
DBS files that have actually changed.

Public surface:
    load_index()                   → list[dict]
    build_index(optilayer_dir)     → list[dict] (also writes index.json)
"""

import json
import os
import struct
import threading
from pathlib import Path

_HERE      = Path(__file__).resolve().parent
INDEX_FILE = _HERE / "index.json"

# Guards against concurrent index rebuilds doing redundant network-share scans.
_INDEX_LOCK = threading.Lock()


def load_index() -> list[dict]:
    if INDEX_FILE.exists():
        with open(INDEX_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def _read_string(raw: bytes, pos: int) -> tuple[str, int]:
    """Read a length-prefixed string. Bit 31 of length = UTF-16-LE, length in chars."""
    length_raw = struct.unpack_from('<I', raw, pos)[0]; pos += 4
    if length_raw & 0x80000000:
        nbytes = (length_raw & 0x7FFFFFFF) * 2
        text = raw[pos:pos+nbytes].decode('utf-16-le', errors='replace')
    else:
        nbytes = length_raw
        text = raw[pos:pos+nbytes].decode('utf-8', errors='replace')
    return text, pos + nbytes


def _parse_dbs(path: Path) -> list[dict]:
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:7] != b'OL_DBS\x00':
        return []
    count = struct.unpack_from('<H', raw, 11)[0]
    pos   = 13
    entries = []
    for _ in range(count):
        slot        = struct.unpack_from('<H', raw, pos)[0]; pos += 2
        name, pos   = _read_string(raw, pos)
        modified = None
        for blk in range(2):  # create + modify action blocks
            sec, mnt, hr, day, mon, yr = struct.unpack_from('<6H', raw, pos)
            pos += 18  # datetime (9 × uint16)
            pos += 4 + struct.unpack_from('<I', raw, pos)[0]  # username
            if blk == 1:
                try:
                    modified = f"{yr:04d}-{mon:02d}-{day:02d}T{hr:02d}:{mnt:02d}:{sec:02d}"
                except Exception:
                    pass
        comment, pos = _read_string(raw, pos)
        entries.append({'slot': slot, 'name': name.strip(), 'comment': comment.strip(), 'modified': modified})
    return entries


def build_index(optilayer_dir: Path | None) -> list[dict]:
    with _INDEX_LOCK:
        return _build_index_locked(optilayer_dir)


def _build_index_locked(optilayer_dir: Path | None) -> list[dict]:
    if optilayer_dir is None:
        raise RuntimeError(
            "OPTILAYER_DIR is not set. Set it in the service unit or environment, "
            "e.g. OPTILAYER_DIR=\"/mnt/server/Data/Film Data/OptiLayer\"."
        )
    if not optilayer_dir.is_dir():
        raise RuntimeError(
            f"OPTILAYER_DIR path does not exist or is not a directory: {optilayer_dir}"
        )

    existing: dict[str, dict] = {}
    if INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, encoding="utf-8") as f:
                for e in json.load(f):
                    folder = e.get("folder", "")
                    if folder not in existing:
                        existing[folder] = {"mtime": e.get("dbs_mtime", 0), "entries": []}
                    existing[folder]["entries"].append(e)
        except Exception:
            pass

    entries = []
    for dbs_path in sorted(optilayer_dir.glob("*/DESIGNA.DBS")):
        folder = str(dbs_path.parent.relative_to(optilayer_dir))
        mtime  = dbs_path.stat().st_mtime
        cached = existing.get(folder)
        if cached and abs(cached["mtime"] - mtime) < 1:
            entries.extend(cached["entries"])
            continue
        for e in _parse_dbs(dbs_path):
            entries.append({"name": e["name"], "comment": e["comment"],
                            "folder": folder, "modified": e.get("modified"),
                            "dbs_mtime": mtime})

    entries.sort(key=lambda e: (e["folder"].lower(), e["name"].lower()))
    tmp = str(INDEX_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    os.replace(tmp, str(INDEX_FILE))
    return entries
