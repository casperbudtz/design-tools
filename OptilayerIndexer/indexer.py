#!/usr/bin/env python3
"""
Optilayer Indexer — reads DESIGNA.DBS from each OptiLayer project folder
to build a searchable index of design names and comments.

DBS parsing is ~100x faster than unzipping individual DESIGNA.D?? files.
Incremental: stores mtime of the DBS file and skips unchanged folders.

Usage:
    python3 indexer.py          # incremental update
    python3 indexer.py --full   # force full re-index
"""

import argparse
import json
import os
import struct
from pathlib import Path

SCRIPT_DIR  = Path(os.path.dirname(os.path.abspath(__file__)))
OPLAYER_DIR = Path("/run/user/1000/kio-fuse-XktOfm/smb/ferropermoptics\\casper@server/Data/Film Data/OptiLayer")
INDEX_FILE  = SCRIPT_DIR / "index.json"


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
    """Parse DESIGNA.DBS and return list of {slot, name, comment}."""
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:7] != b'OL_DBS\x00':
        return []
    count = struct.unpack_from('<H', raw, 11)[0]
    pos   = 13
    entries = []
    for _ in range(count):
        slot          = struct.unpack_from('<H', raw, pos)[0]; pos += 2
        name, pos     = _read_string(raw, pos)
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
        comment, pos  = _read_string(raw, pos)
        entries.append({'slot': slot, 'name': name.strip(), 'comment': comment.strip(), 'modified': modified})
    return entries


def build_index(base_dir: Path = OPLAYER_DIR, full: bool = False) -> tuple[list[dict], int, int]:
    """Return (entries, folders_parsed, folders_skipped)."""
    existing: dict[str, dict] = {}  # folder → {mtime, entries}
    if not full and INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, encoding='utf-8') as f:
                for e in json.load(f):
                    folder = e.get('folder', '')
                    if folder not in existing:
                        existing[folder] = {'mtime': e.get('dbs_mtime', 0), 'entries': []}
                    existing[folder]['entries'].append(e)
        except Exception:
            pass

    all_entries = []
    parsed = skipped = 0

    for dbs_path in sorted(base_dir.glob("*/DESIGNA.DBS")):
        folder = str(dbs_path.parent.relative_to(base_dir))
        mtime  = dbs_path.stat().st_mtime
        cached = existing.get(folder)

        if cached and abs(cached['mtime'] - mtime) < 1:
            all_entries.extend(cached['entries'])
            skipped += 1
            continue

        dbs_entries = _parse_dbs(dbs_path)
        for e in dbs_entries:
            all_entries.append({
                'name':      e['name'],
                'comment':   e['comment'],
                'folder':    folder,
                'modified':  e.get('modified'),
                'dbs_mtime': mtime,
            })
        parsed += 1

    all_entries.sort(key=lambda e: (e['folder'].lower(), e['name'].lower()))
    return all_entries, parsed, skipped


def save_index(entries: list[dict], path: Path = INDEX_FILE) -> None:
    tmp = str(path) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    os.replace(tmp, str(path))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true', help='Force full re-index')
    args = parser.parse_args()

    print(f"{'Full' if args.full else 'Incremental'} scan of {OPLAYER_DIR} …")
    entries, parsed, skipped = build_index(full=args.full)
    save_index(entries)
    print(f"Done: {len(entries)} designs ({parsed} folders parsed, {skipped} unchanged) → {INDEX_FILE}")
