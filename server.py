#!/usr/bin/env python3
"""
Design Tools — web server
Serves the Design Tools landing page and routes sub-project requests.

Routes:
    GET    /                            → index.html (Design Tools landing page)
    GET    /optilayer                   → OptilayerIndexer/index.html
    GET    /optilayer/                  → OptilayerIndexer/index.html
    GET    /optilayer/api/search        → Search index (?q=)
    POST   /optilayer/api/update        → Rebuild index
    GET    /recipeeditor                → RecipeEditor/index.html
    GET    /recipeeditor/               → RecipeEditor/index.html
    GET    /recipeeditor/api/recipes    → List all recipes from RECIPE.csv
    GET    /recipeeditor/api/seq        → SEQ file for a recipe (?name=)
    POST   /recipeeditor/api/seq        → Save edited steps (?name=)
    POST   /recipeeditor/api/import          → Import LPR file (?name= optional override)
    GET    /recipeeditor/api/layer-names     → List step names from Layer.CSV
    GET    /recipeeditor/api/import-settings → Load material→step mapping
    POST   /recipeeditor/api/import-settings → Save material→step mapping
    PATCH  /recipeeditor/api/recipe     → Rename a recipe (?name=, body: new_name)
    DELETE /recipeeditor/api/recipe     → Delete a recipe (?name=)

Usage:
    python3 server.py [port]
    Default port: 8081

Configuration:
    OPTILAYER_DIR environment variable overrides the default network share path.
    Set it before starting the server:
        export OPTILAYER_DIR="/path/to/optilayer/data"
        python3 server.py
"""

import http.server
import json
import os
import struct
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

PORT       = 8081
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE       = Path(SCRIPT_DIR)

# ── Optilayer Indexer ─────────────────────────────────────────────────────────

# Override with OPTILAYER_DIR env var on the target machine:
#   export OPTILAYER_DIR="/mnt/server/Data/Film Data/OptiLayer"
_DEFAULT_OPTILAYER_DIR = "/run/user/1000/kio-fuse-XktOfm/smb/ferropermoptics\\casper@server/Data/Film Data/OptiLayer"
OPTILAYER_DIR = Path(os.environ.get("OPTILAYER_DIR", _DEFAULT_OPTILAYER_DIR))
OPTILAYER_IDX = BASE / "OptilayerIndexer" / "index.json"

def _optilayer_load_index():
    if OPTILAYER_IDX.exists():
        with open(OPTILAYER_IDX, encoding="utf-8") as f:
            return json.load(f)
    return []

def _dbs_read_string(raw: bytes, pos: int) -> tuple[str, int]:
    """Read a length-prefixed string. Bit 31 of length = UTF-16-LE, length in chars."""
    length_raw = struct.unpack_from('<I', raw, pos)[0]; pos += 4
    if length_raw & 0x80000000:
        nbytes = (length_raw & 0x7FFFFFFF) * 2
        text = raw[pos:pos+nbytes].decode('utf-16-le', errors='replace')
    else:
        nbytes = length_raw
        text = raw[pos:pos+nbytes].decode('utf-8', errors='replace')
    return text, pos + nbytes

def _optilayer_parse_dbs(path: Path) -> list[dict]:
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:7] != b'OL_DBS\x00':
        return []
    count = struct.unpack_from('<H', raw, 11)[0]
    pos   = 13
    entries = []
    for _ in range(count):
        slot        = struct.unpack_from('<H', raw, pos)[0]; pos += 2
        name, pos   = _dbs_read_string(raw, pos)
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
        comment, pos = _dbs_read_string(raw, pos)
        entries.append({'slot': slot, 'name': name.strip(), 'comment': comment.strip(), 'modified': modified})
    return entries


def _optilayer_build_index():
    existing: dict[str, dict] = {}
    if OPTILAYER_IDX.exists():
        try:
            with open(OPTILAYER_IDX, encoding="utf-8") as f:
                for e in json.load(f):
                    folder = e.get("folder", "")
                    if folder not in existing:
                        existing[folder] = {"mtime": e.get("dbs_mtime", 0), "entries": []}
                    existing[folder]["entries"].append(e)
        except Exception:
            pass

    entries = []
    for dbs_path in sorted(OPTILAYER_DIR.glob("*/DESIGNA.DBS")):
        folder = str(dbs_path.parent.relative_to(OPTILAYER_DIR))
        mtime  = dbs_path.stat().st_mtime
        cached = existing.get(folder)
        if cached and abs(cached["mtime"] - mtime) < 1:
            entries.extend(cached["entries"])
            continue
        for e in _optilayer_parse_dbs(dbs_path):
            entries.append({"name": e["name"], "comment": e["comment"],
                            "folder": folder, "modified": e.get("modified"),
                            "dbs_mtime": mtime})

    entries.sort(key=lambda e: (e["folder"].lower(), e["name"].lower()))
    tmp = str(OPTILAYER_IDX) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    os.replace(tmp, str(OPTILAYER_IDX))
    return entries


# ── Recipe Editor ─────────────────────────────────────────────────────────────

_DEFAULT_RECIPE_DIR  = str(BASE / "RecipeEditor" / "recipe")
IMPORT_SETTINGS      = BASE / "RecipeEditor" / "import_settings.json"

# Known materials and their default step names (used when no settings file exists)
_DEFAULT_MATERIAL_STEPS = {
    "Ta2O5":  "PVD2_Ta2O5_FAT",
    "SiO2":   "PVD3_SiO2_FO",
    "Nb2O5":  "PVD1_Nb2O5_FAT",
}

def _import_settings_load() -> dict:
    if IMPORT_SETTINGS.exists():
        with open(IMPORT_SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    return {
        "recipe_dir":    _DEFAULT_RECIPE_DIR,
        "material_steps": dict(_DEFAULT_MATERIAL_STEPS),
    }

def _import_settings_save(data: dict):
    tmp = str(IMPORT_SETTINGS) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, str(IMPORT_SETTINGS))

def _get_recipe_dir() -> Path:
    return Path(_import_settings_load().get("recipe_dir", _DEFAULT_RECIPE_DIR))

def _layer_names() -> list[str]:
    import csv as _csv
    layer_csv = _get_recipe_dir() / "Layer.CSV"
    try:
        with open(layer_csv, encoding="utf-8-sig", newline="") as f:
            for row in _csv.reader(f):
                if row and row[0].strip() == ":Names":
                    skip = {"EDIT", "TEST", "INIT", "---", ""}
                    return [v.strip() for v in row[4:] if v.strip() not in skip]
    except FileNotFoundError:
        pass
    return []

def _recipe_list():
    """Return list of {name, seq_name, change_date, first_step, last_step} from RECIPE.csv."""
    import csv as _csv
    recipe_csv = _get_recipe_dir() / "RECIPE.csv"
    with open(recipe_csv, encoding="utf-8-sig") as f:
        rows = list(_csv.reader(f))
    names_row  = next(r for r in rows if r and r[0].strip() == ":Names")
    seq_row    = next(r for r in rows if r and r[0].strip() == "EDIT_RECIPE_NAME")
    date_row   = next((r for r in rows if r and r[0].strip() == "EDIT_RECIPE_CHANGE_DATE"), None)
    first_row  = next((r for r in rows if r and r[0].strip() == "PCOPC_FirstProcStepNo"), None)
    last_row   = next((r for r in rows if r and r[0].strip() == "PCOPC_LastProcStepNo"), None)
    # Recipes start at column 4 (cols 0-3 are param/type/PREVIEW/TEST)
    recipes = []
    for i in range(4, len(names_row)):
        name     = names_row[i].strip()
        seq_name = seq_row[i].strip() if i < len(seq_row) else ""
        date     = date_row[i].strip() if date_row and i < len(date_row) else ""
        first    = int(first_row[i].strip()) if first_row and i < len(first_row) and first_row[i].strip().isdigit() else 1
        last     = int(last_row[i].strip())  if last_row  and i < len(last_row)  and last_row[i].strip().isdigit()  else 9999
        if name:
            recipes.append({"name": name, "seq_name": seq_name, "change_date": date,
                            "first_step": first, "last_step": last})
    return recipes


def _seq_data(seq_name: str) -> dict:
    """Parse SEQ_<seq_name>.CSV and return transposed step data."""
    import csv as _csv
    path = _get_recipe_dir() / f"SEQ_{seq_name}.CSV"
    if not path.exists():
        return None
    with open(path, encoding="utf-8-sig") as f:
        rows = list(_csv.reader(f))

    names_row = next(r for r in rows if r and r[0].strip() == ":Names")
    param_rows = [r for r in rows if r and not r[0].startswith(":")]

    # Strip quotes from param names
    params = [r[0].strip().strip('"') for r in param_rows]

    # LOAD column is col 2; steps start at col 3
    load_values = {p: r[2].strip() if len(r) > 2 else "" for p, r in zip(params, param_rows)}

    # Build step list — only enabled steps
    enable_row = next((r for r in param_rows if r[0].strip().strip('"') == "SEQ_Enable"), None)
    step_count = len(names_row) - 3  # cols 0,1,2 are non-step
    steps = []
    for i in range(step_count):
        col = i + 3
        enabled = enable_row[col].strip() == "1" if enable_row and col < len(enable_row) else True
        num = names_row[col].strip() if col < len(names_row) else str(i + 1)
        values = {p: (r[col].strip() if col < len(r) else "") for p, r in zip(params, param_rows)}
        steps.append({"num": num, "enabled": enabled, "values": values})

    # Drop SEQ_Enable from visible params (shown as checkbox column separately)
    visible_params = [p for p in params if p != "SEQ_Enable"]

    return {"params": visible_params, "load": load_values, "steps": steps}


def _lpr_import(lpr_bytes: bytes, recipe_name: str | None = None) -> dict:
    """Parse an LPR (XML) file and create a new SEQ_*.CSV + add a RECIPE.csv column."""
    import csv as _csv, shutil, io

    tree = ET.parse(io.BytesIO(lpr_bytes))
    root = tree.getroot()

    # Process name from root attribute; recipe_name overrides
    proc_name = (recipe_name or root.get("name", "")).strip()
    if not proc_name:
        return {"error": "Could not determine recipe name"}

    seq_name = proc_name  # SEQ file will be SEQ_<proc_name>.CSV

    # Check for collision
    recipe_dir = _get_recipe_dir()
    recipe_csv = recipe_dir / "RECIPE.csv"
    seq_path = recipe_dir / f"SEQ_{seq_name}.CSV"
    if seq_path.exists():
        return {"error": f"SEQ_{seq_name}.CSV already exists"}

    # Build refractive index lookup: material → wavelength → n
    ri_table: dict[str, dict[float, float]] = {}
    dd = root.find(".//dispersionsdata")
    if dd is not None:
        for disp in dd.findall("dispersion"):
            mat = disp.get("material", "")
            crit = disp.find("complex_refractive_index_table")
            if crit is None:
                continue
            wl_map: dict[float, float] = {}
            for row in crit.findall("row"):
                try:
                    wl_map[float(row.get("wavelength", "0"))] = float(row.get("n", "0"))
                except (ValueError, TypeError):
                    pass
            ri_table[mat] = wl_map

    def _get_ri(material: str, wavelength: float) -> str:
        wl_map = ri_table.get(material, {})
        if not wl_map:
            return ""
        # Find closest wavelength
        closest = min(wl_map, key=lambda w: abs(w - wavelength))
        return str(round(wl_map[closest], 6)).rstrip("0").rstrip(".")

    # Parse ProcessLayer elements
    ps = root.find(".//Processspreadsheet")
    if ps is None:
        return {"error": "No Processspreadsheet element found"}
    layers = ps.findall("ProcessLayer")
    if not layers:
        return {"error": "No ProcessLayer elements found"}

    # Trigger mapping
    trigger_map = {"OFFSET": "1", "ABSOLUTE": "2", "BACKWARD_2": "3", "FORWARD": "4"}

    # Step name from saved import settings; fall back to material name
    settings = _import_settings_load()
    mat_steps = settings.get("material_steps", {})

    def _step_name(material: str) -> str:
        if material in mat_steps:
            return mat_steps[material]
        # fuzzy fallback for unrecognised materials
        m = material.lower()
        for key, step in mat_steps.items():
            if key.lower() in m:
                return step
        return f"PVD_{material}"

    # Read SEQ_Template.CSV
    template_path = recipe_dir / "SEQ_Template.CSV"
    if not template_path.exists():
        return {"error": "SEQ_Template.CSV not found"}
    with open(template_path, encoding="utf-8-sig", newline="") as f:
        tmpl_rows = list(_csv.reader(f))

    names_row_idx = next(i for i, r in enumerate(tmpl_rows) if r and r[0].strip() == ":Names")
    # Template has cols: 0=param, 1=type, 2=LOAD, 3=step1, 4=step2, ...
    # We keep steps 1 and 2 from template (cols 3 and 4)

    num_coating = len(layers)
    total_steps = 2 + num_coating  # 2 template + N coating

    # Build new row list: same param/type/LOAD columns, then 2 template steps, then coating steps
    new_rows = []
    for r in tmpl_rows:
        if not r:
            new_rows.append(r)
            continue
        if r[0].strip() == ":Names":
            # Header row: add step numbers 1..total_steps
            new_row = r[:3] + [str(i + 1) for i in range(total_steps)]
            new_rows.append(new_row)
            continue
        if r[0].startswith(":"):
            # Other system rows — copy first 5 cols, pad remainder
            new_rows.append(r[:3] + [r[3] if len(r) > 3 else ""] * total_steps)
            continue

        param = r[0].strip().strip('"')
        tmpl_step1 = r[3] if len(r) > 3 else ""
        tmpl_step2 = r[4] if len(r) > 4 else ""
        load_val   = r[2] if len(r) > 2 else ""

        coating_vals = []
        for pl in layers:
            d = pl.find("Design")
            o = pl.find("OMSParameter")
            tc = o.get("ThicknessControl", "OMS")
            step_type = "3" if tc == "OMS" else "4"
            mat = d.get("material", "")
            wl  = float(d.get("wavelength", "0") or "0")
            rc_raw = o.get("RateContol", "0")
            rc_val = "1" if tc == "OMS" else rc_raw
            rounds = int(o.get("Rounds", "0") or "0")
            max_time = str(round(rounds * 2 / 3)) if tc == "OMS" else "0"
            trig_raw = o.get("TriggerPointFkt", "OFFSET")
            trigger = trigger_map.get(trig_raw, "1")

            v = {
                "SEQ_Enable":          "1",
                "SEQ_StepName":        _step_name(mat),
                "SEQ_MaxTime":         max_time,
                "SEQ_StepType":        step_type,
                "SEQ_StepTime":        "0",
                "SEQ_Rounds":          "0",
                "SEQ_Values":          rc_val,
                "SEQ_QWOT":            d.get("optical_thickness", "0"),
                "SEQ_Wavelength":      d.get("wavelength", "400"),
                "SEQ_BBM_LayerNo":     d.get("number", "0"),
                "SEQ_Testslide":       "1",
                "SEQ_MaterialID":      mat,
                "SEQ_RefractiveIndex": _get_ri(mat, wl),
                "SEQ_SettlingTime":    o.get("SettlingTime", "0"),
                "SEQ_GSA":             o.get("GainAverage", "2"),
                "SEQ_TriggerFunction": trigger,
                "SEQ_Slits":           o.get("EntranceSlit", "0.5"),
                "SEQ_Rate":            o.get("Rate", "0"),
                "SEQ_RateReference":   "1" if o.get("RateControlEnable", "No") == "Yes" else "0",
            }
            coating_vals.append(v.get(param, "0"))

        new_rows.append([r[0], r[1], load_val, tmpl_step1, tmpl_step2] + coating_vals)

    # Write new SEQ file
    with open(seq_path, "w", encoding="utf-8-sig", newline="") as f:
        _csv.writer(f).writerows(new_rows)

    # Update RECIPE.csv — add a new column copied from Template, with updated fields
    with open(recipe_csv, encoding="utf-8-sig", newline="") as f:
        recipe_rows = list(_csv.reader(f))

    recipe_names_idx = next(i for i, r in enumerate(recipe_rows) if r and r[0].strip() == ":Names")
    template_col = next((j for j, v in enumerate(recipe_rows[recipe_names_idx]) if v.strip() == "Template"), None)
    if template_col is None:
        return {"error": "Template column not found in RECIPE.csv"}

    # Field overrides for new recipe column
    recipe_overrides = {
        "EDIT_RECIPE_NAME":        proc_name,
        ":Names":                  proc_name,
        "EDIT_RECIPE_COMMENT":     "---",
        "EDIT_RECIPE_CHANGE_DATE": datetime.now().strftime("%-m/%-d/%Y"),
        "PCOPC_FirstProcStepNo":   "1",
        "PCOPC_LastProcStepNo":    str(total_steps),
    }

    new_recipe_rows = []
    for r in recipe_rows:
        param = r[0].strip() if r else ""
        tmpl_val = r[template_col] if len(r) > template_col else ""
        new_val = recipe_overrides.get(param, tmpl_val)
        new_recipe_rows.append(list(r) + [new_val])

    shutil.copy2(str(recipe_csv), str(recipe_csv) + ".bak")
    tmp = str(recipe_csv) + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        _csv.writer(f).writerows(new_recipe_rows)
    os.replace(tmp, str(recipe_csv))

    return {
        "ok":         True,
        "name":       proc_name,
        "seq_name":   seq_name,
        "steps":      total_steps,
        "layers":     num_coating,
    }


def _seq_save(seq_name: str, steps_data: list) -> dict:
    """Write edited step values back to SEQ_<seq_name>.CSV."""
    import csv as _csv, shutil
    path = _get_recipe_dir() / f"SEQ_{seq_name}.CSV"
    if not path.exists():
        return {"error": f"SEQ_{seq_name}.CSV not found"}

    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(_csv.reader(f))

    names_row = next(r for r in rows if r and r[0].strip() == ":Names")
    num_to_col = {names_row[col].strip(): col for col in range(3, len(names_row))}

    param_to_row_idx = {}
    for i, r in enumerate(rows):
        if r and not r[0].startswith(":"):
            param_to_row_idx[r[0].strip().strip('"')] = i

    changes = 0
    for step in steps_data:
        col = num_to_col.get(str(step["num"]))
        if col is None:
            continue
        for param, value in step["values"].items():
            ri = param_to_row_idx.get(param)
            if ri is None:
                continue
            while len(rows[ri]) <= col:
                rows[ri].append("")
            if rows[ri][col] != str(value):
                rows[ri][col] = str(value)
                changes += 1

    if changes == 0:
        return {"ok": True, "changes": 0}

    shutil.copy2(str(path), str(path) + ".bak")
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        _csv.writer(f).writerows(rows)
    os.replace(tmp, str(path))
    return {"ok": True, "changes": changes}


def _recipe_rename(old_seq_name: str, new_name: str) -> dict:
    """Rename a recipe: renames SEQ file and updates :Names / EDIT_RECIPE_NAME in RECIPE.csv."""
    import csv as _csv, shutil
    new_seq_name = new_name.strip()
    if not new_seq_name:
        return {"error": "New name cannot be empty"}

    recipe_dir = _get_recipe_dir()
    recipe_csv = recipe_dir / "RECIPE.csv"
    old_seq_path = recipe_dir / f"SEQ_{old_seq_name}.CSV"
    new_seq_path = recipe_dir / f"SEQ_{new_seq_name}.CSV"

    if not old_seq_path.exists():
        return {"error": f"SEQ_{old_seq_name}.CSV not found"}
    if new_seq_path.exists() and old_seq_name != new_seq_name:
        return {"error": f"SEQ_{new_seq_name}.CSV already exists"}

    # Update RECIPE.csv first
    with open(recipe_csv, encoding="utf-8-sig", newline="") as f:
        recipe_rows = list(_csv.reader(f))

    names_row = next(r for r in recipe_rows if r and r[0].strip() == ":Names")
    col = next((j for j, v in enumerate(names_row) if v.strip() == old_seq_name), None)
    if col is None:
        return {"error": f"Recipe '{old_seq_name}' not found in RECIPE.csv"}

    new_recipe_rows = []
    for r in recipe_rows:
        row = list(r)
        if not row:
            new_recipe_rows.append(row)
            continue
        param = row[0].strip()
        if param in (":Names", "EDIT_RECIPE_NAME") and col < len(row):
            row[col] = new_seq_name
        new_recipe_rows.append(row)

    shutil.copy2(str(recipe_csv), str(recipe_csv) + ".bak")
    tmp = str(recipe_csv) + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        _csv.writer(f).writerows(new_recipe_rows)
    os.replace(tmp, str(recipe_csv))

    # Rename the SEQ file
    if old_seq_name != new_seq_name:
        os.rename(str(old_seq_path), str(new_seq_path))

    return {"ok": True, "old_seq_name": old_seq_name, "new_seq_name": new_seq_name}


def _recipe_delete(seq_name: str) -> dict:
    """Delete a recipe: removes its SEQ file and column from RECIPE.csv."""
    import csv as _csv, shutil
    recipe_dir = _get_recipe_dir()
    recipe_csv = recipe_dir / "RECIPE.csv"
    seq_path   = recipe_dir / f"SEQ_{seq_name}.CSV"

    # Find column in RECIPE.csv
    with open(recipe_csv, encoding="utf-8-sig", newline="") as f:
        recipe_rows = list(_csv.reader(f))

    names_row = next(r for r in recipe_rows if r and r[0].strip() == ":Names")
    col = next((j for j, v in enumerate(names_row) if v.strip() == seq_name), None)
    if col is None:
        return {"error": f"Recipe '{seq_name}' not found in RECIPE.csv"}

    # Remove that column from every row
    new_recipe_rows = [
        [v for j, v in enumerate(r) if j != col]
        for r in recipe_rows
    ]

    shutil.copy2(str(recipe_csv), str(recipe_csv) + ".bak")
    tmp = str(recipe_csv) + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        _csv.writer(f).writerows(new_recipe_rows)
    os.replace(tmp, str(recipe_csv))

    # Delete the SEQ file (move to .deleted for safety)
    if seq_path.exists():
        os.rename(str(seq_path), str(seq_path) + ".deleted")

    return {"ok": True, "seq_name": seq_name}


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        if path == "/":
            self._serve_file(os.path.join(SCRIPT_DIR, "index.html"), "text/html; charset=utf-8")

        elif path in ("/optilayer", "/optilayer/"):
            self._serve_file(os.path.join(SCRIPT_DIR, "OptilayerIndexer", "index.html"), "text/html; charset=utf-8")

        elif path == "/optilayer/api/search":
            params = urllib.parse.parse_qs(parsed.query)
            q      = params.get("q", [""])[0].strip().lower()
            entries = _optilayer_load_index()
            if q:
                entries = [e for e in entries if
                           q in e["name"].lower() or
                           q in e["comment"].lower() or
                           q in e["folder"].lower()]
            self._send_json(entries)

        elif path in ("/recipeeditor", "/recipeeditor/"):
            self._serve_file(os.path.join(SCRIPT_DIR, "RecipeEditor", "index.html"), "text/html; charset=utf-8")

        elif path == "/recipeeditor/api/recipes":
            self._send_json(_recipe_list())

        elif path == "/recipeeditor/api/seq":
            qs   = urllib.parse.parse_qs(parsed.query)
            name = qs.get("name", [""])[0].strip()
            data = _seq_data(name)
            if data is None:
                self._send_json({"error": f"SEQ_{name}.CSV not found"}, 404)
            else:
                self._send_json(data)

        elif path == "/recipeeditor/api/layer-names":
            self._send_json(_layer_names())

        elif path == "/recipeeditor/api/import-settings":
            self._send_json(_import_settings_load())

        else:
            self.send_error(404)

    def _serve_file(self, filepath, content_type):
        try:
            with open(filepath, "rb") as f:
                content = f.read()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, data, status=200):
        content = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        return json.loads(self.rfile.read(length))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        if path == "/recipeeditor/api/seq":
            qs      = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name    = qs.get("name", [""])[0].strip()
            body    = self._read_json_body()
            if not name or not body or "steps" not in body:
                self._send_json({"error": "Missing name or steps"}, 400)
                return
            try:
                result = _seq_save(name, body["steps"])
                self._send_json(result, 200 if result.get("ok") else 400)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/recipeeditor/api/import":
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self._send_json({"error": "Empty request body"}, 400)
                return
            lpr_bytes = self.rfile.read(length)
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            recipe_name = qs.get("name", [""])[0].strip() or None
            try:
                result = _lpr_import(lpr_bytes, recipe_name)
                self._send_json(result, 200 if result.get("ok") else 400)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/optilayer/api/update":
            try:
                entries = _optilayer_build_index()
                self._send_json({"ok": True, "count": len(entries)})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/recipeeditor/api/import-settings":
            body = self._read_json_body()
            if not body or "material_steps" not in body or "recipe_dir" not in body:
                self._send_json({"error": "Missing recipe_dir or material_steps"}, 400)
                return
            try:
                _import_settings_save(body)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        else:
            self.send_error(404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        if path == "/recipeeditor/api/recipe":
            qs   = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = qs.get("name", [""])[0].strip()
            if not name:
                self._send_json({"error": "Missing name"}, 400)
                return
            try:
                result = _recipe_delete(name)
                self._send_json(result, 200 if result.get("ok") else 400)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        else:
            self.send_error(404)

    def do_PATCH(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        if path == "/recipeeditor/api/recipe":
            qs      = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name    = qs.get("name", [""])[0].strip()
            body    = self._read_json_body()
            new_name = (body or {}).get("new_name", "").strip()
            if not name or not new_name:
                self._send_json({"error": "Missing name or new_name"}, 400)
                return
            try:
                result = _recipe_rename(name, new_name)
                self._send_json(result, 200 if result.get("ok") else 400)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    print(f"Design Tools     →  http://localhost:{port}")
    print(f"Optilayer Indexer →  http://localhost:{port}/optilayer/")
    print(f"Recipe Editor    →  http://localhost:{port}/recipeeditor/")
    print("Press Ctrl+C to stop.\n")
    with http.server.HTTPServer(("", port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
