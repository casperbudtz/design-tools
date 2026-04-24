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
    GET    /api/version                       → Max mtime across all frontend files
    GET    /recipeeditor/api/import-log      → Full log, or filtered by ?name=
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

import csv
import http.server
import io
import json
import os
import shutil
import struct
import sys
import threading
import traceback
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

PORT       = 8081
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE       = Path(SCRIPT_DIR)

# ── Optilayer Indexer ─────────────────────────────────────────────────────────

# Path to the folder containing OptiLayer project subdirs, each holding a
# DESIGNA.DBS index file. Set via env var on the target machine:
#   export OPTILAYER_DIR="/mnt/server/Data/Film Data/OptiLayer"
_optilayer_env = os.environ.get("OPTILAYER_DIR", "").strip()
OPTILAYER_DIR = Path(_optilayer_env) if _optilayer_env else None
OPTILAYER_IDX = BASE / "OptilayerIndexer" / "index.json"

# Guards against concurrent `/optilayer/api/update` calls doing redundant
# network-share scans.
_OPTILAYER_INDEX_LOCK = threading.Lock()

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
    with _OPTILAYER_INDEX_LOCK:
        return _optilayer_build_index_locked()


def _optilayer_build_index_locked():
    if OPTILAYER_DIR is None:
        raise RuntimeError(
            "OPTILAYER_DIR is not set. Set it in the service unit or environment, "
            "e.g. OPTILAYER_DIR=\"/mnt/server/Data/Film Data/OptiLayer\"."
        )
    if not OPTILAYER_DIR.is_dir():
        raise RuntimeError(
            f"OPTILAYER_DIR path does not exist or is not a directory: {OPTILAYER_DIR}"
        )

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
BACKUP_MAX_AGE_DAYS  = 30


def _recipe_editor_dir() -> Path:
    return _get_recipe_dir() / "recipe-editor"

def _backup_dir() -> Path:
    return _recipe_editor_dir() / "backups"

def _import_log_path() -> Path:
    return _recipe_editor_dir() / "import_log.json"

# Serialize all recipe-file writes. HTTPServer is single-threaded today, but
# this also protects against re-entrant calls from shared helpers.
_RECIPE_WRITE_LOCK = threading.Lock()


def _timestamped_backup(path: Path) -> None:
    """Copy `path` into _backup_dir() as `<filename>.<ISO-timestamp>.bak`.

    Backups older than BACKUP_MAX_AGE_DAYS are pruned afterward. The backup
    subdirectory lives inside the recipe dir so it is accessible wherever
    that dir is mounted (local or SMB share).
    """
    if not path.exists():
        return
    bd = _backup_dir()
    bd.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    shutil.copy2(str(path), bd / f"{path.name}.{stamp}.bak")
    _prune_backups()


def _prune_backups() -> None:
    """Delete backup files older than BACKUP_MAX_AGE_DAYS."""
    bd = _backup_dir()
    if not bd.exists():
        return
    cutoff = datetime.now().timestamp() - BACKUP_MAX_AGE_DAYS * 86400
    for f in bd.glob("*.bak"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass

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


def _import_log_append(lpr_name: str, recipe_name: str, lpr_filename: str | None, steps: int):
    """Append one record to the import log. Loads, appends, and atomically replaces."""
    log_path = _import_log_path()
    log_path.parent.mkdir(exist_ok=True)
    records = []
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            records = json.load(f)
    records.append({
        "imported_at":     datetime.now().isoformat(timespec="seconds"),
        "lpr_name":        lpr_name,
        "recipe_name":     recipe_name,
        "name_overridden": lpr_name != recipe_name,
        "lpr_filename":    lpr_filename,
        "steps":           steps,
    })
    tmp = str(log_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    os.replace(tmp, str(log_path))


def _validate_import_settings(data: dict) -> str | None:
    """Check settings before saving. Returns an error string, or None if OK."""
    if not isinstance(data, dict):
        return "Settings payload must be a JSON object"

    recipe_dir = data.get("recipe_dir")
    if not isinstance(recipe_dir, str) or not recipe_dir.strip():
        return "recipe_dir must be a non-empty string"
    rd = Path(recipe_dir)
    if not rd.is_dir():
        return f"recipe_dir does not exist or is not a directory: {recipe_dir}"
    if not (rd / "RECIPE.csv").exists():
        return f"RECIPE.csv not found in {recipe_dir}"

    mat_steps = data.get("material_steps")
    if not isinstance(mat_steps, dict):
        return "material_steps must be a JSON object"
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in mat_steps.items()):
        return "material_steps keys and values must be strings"

    # Validate selected step names against Layer.CSV in the *new* recipe_dir.
    # Skip if Layer.CSV is missing — old install without it shouldn't block saves.
    layer_csv = rd / "Layer.CSV"
    if layer_csv.exists():
        skip = {"EDIT", "TEST", "INIT", "---", ""}
        valid: list[str] = []
        with open(layer_csv, encoding="utf-8-sig", newline="") as f:
            for row in csv.reader(f):
                if row and row[0].strip() == ":Names":
                    valid = [v.strip() for v in row[4:] if v.strip() not in skip]
                    break
        unknown = [f"{mat}={step}" for mat, step in mat_steps.items() if step and step not in valid]
        if unknown:
            return f"Unknown step names in Layer.CSV: {', '.join(unknown)}"

    return None

def _get_recipe_dir() -> Path:
    return Path(_import_settings_load().get("recipe_dir", _DEFAULT_RECIPE_DIR))

def _layer_names() -> list[str]:
    layer_csv = _get_recipe_dir() / "Layer.CSV"
    try:
        with open(layer_csv, encoding="utf-8-sig", newline="") as f:
            for row in csv.reader(f):
                if row and row[0].strip() == ":Names":
                    skip = {"EDIT", "TEST", "INIT", "---", ""}
                    return [v.strip() for v in row[4:] if v.strip() not in skip]
    except FileNotFoundError:
        pass
    return []

def _recipe_list():
    """Return list of {name, seq_name, change_date, first_step, last_step} from RECIPE.csv."""
    recipe_csv = _get_recipe_dir() / "RECIPE.csv"
    with open(recipe_csv, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
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
    path = _get_recipe_dir() / f"SEQ_{seq_name}.CSV"
    if not path.exists():
        return None
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

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


def _lpr_import(lpr_bytes: bytes, recipe_name: str | None = None, lpr_filename: str | None = None) -> dict:
    """Parse an LPR (XML) file and create a new SEQ_*.CSV + add a RECIPE.csv column."""
    tree = ET.parse(io.BytesIO(lpr_bytes))
    root = tree.getroot()

    # Process name from root attribute; recipe_name overrides
    lpr_name  = root.get("name", "").strip()
    proc_name = (recipe_name or lpr_name).strip()
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

    ri_warnings: list[str] = []

    def _get_ri(material: str, wavelength: float) -> str:
        wl_map = ri_table.get(material, {})
        if not wl_map:
            return ""
        wls = sorted(wl_map)
        if wavelength <= wls[0]:
            ri_warnings.append(
                f"{material} @ {wavelength:.0f} nm is below dispersion range "
                f"({wls[0]:.0f}–{wls[-1]:.0f} nm); clamped to {wls[0]:.0f} nm"
            )
            n = wl_map[wls[0]]
        elif wavelength >= wls[-1]:
            ri_warnings.append(
                f"{material} @ {wavelength:.0f} nm is above dispersion range "
                f"({wls[0]:.0f}–{wls[-1]:.0f} nm); clamped to {wls[-1]:.0f} nm"
            )
            n = wl_map[wls[-1]]
        else:
            # Find bracketing points and linearly interpolate
            hi = next(w for w in wls if w >= wavelength)
            lo = wls[wls.index(hi) - 1]
            t = (wavelength - lo) / (hi - lo)
            n = wl_map[lo] + t * (wl_map[hi] - wl_map[lo])
        return str(round(n, 6)).rstrip("0").rstrip(".")

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
        tmpl_rows = list(csv.reader(f))

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
            rounds_raw = int(o.get("Rounds", "0") or "0")
            max_time = str(round(rounds_raw * 2 / 3)) if tc == "OMS" else "0"
            trig_raw = o.get("TriggerPointFkt", "OFFSET")
            trigger = trigger_map.get(trig_raw, "1")

            rounds = rounds_raw if step_type == "2" else 0

            v = {
                "SEQ_Enable":          "1",
                "SEQ_StepName":        _step_name(mat),
                "SEQ_MaxTime":         max_time,
                "SEQ_StepType":        step_type,
                "SEQ_StepTime":        "0",
                "SEQ_Rounds":          str(rounds),
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

    with _RECIPE_WRITE_LOCK:
        # Re-check seq file existence under the lock in case of a concurrent import
        if seq_path.exists():
            return {"error": f"SEQ_{seq_name}.CSV already exists"}

        # Write new SEQ file
        with open(seq_path, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(new_rows)

        # Update RECIPE.csv — add a new column copied from Template, with updated fields
        with open(recipe_csv, encoding="utf-8-sig", newline="") as f:
            recipe_rows = list(csv.reader(f))

        recipe_names_idx = next(i for i, r in enumerate(recipe_rows) if r and r[0].strip() == ":Names")
        template_col = next((j for j, v in enumerate(recipe_rows[recipe_names_idx]) if v.strip() == "Template"), None)
        if template_col is None:
            return {"error": "Template column not found in RECIPE.csv"}

        # Field overrides for new recipe column
        now = datetime.now()
        recipe_overrides = {
            "EDIT_RECIPE_NAME":        proc_name,
            ":Names":                  proc_name,
            "EDIT_RECIPE_COMMENT":     "---",
            "EDIT_RECIPE_CHANGE_DATE": f"{now.month}/{now.day}/{now.year}",
            "PCOPC_FirstProcStepNo":   "1",
            "PCOPC_LastProcStepNo":    str(total_steps),
        }

        new_recipe_rows = []
        for r in recipe_rows:
            param = r[0].strip() if r else ""
            tmpl_val = r[template_col] if len(r) > template_col else ""
            new_val = recipe_overrides.get(param, tmpl_val)
            new_recipe_rows.append(list(r) + [new_val])

        _timestamped_backup(recipe_csv)
        tmp = str(recipe_csv) + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(new_recipe_rows)
        os.replace(tmp, str(recipe_csv))

    _import_log_append(lpr_name, proc_name, lpr_filename, total_steps)

    return {
        "ok":         True,
        "name":       proc_name,
        "seq_name":   seq_name,
        "steps":      total_steps,
        "layers":     num_coating,
        "warnings":   ri_warnings,
    }


def _seq_save(seq_name: str, steps_data: list) -> dict:
    """Write edited step values back to SEQ_<seq_name>.CSV."""
    path = _get_recipe_dir() / f"SEQ_{seq_name}.CSV"
    if not path.exists():
        return {"error": f"SEQ_{seq_name}.CSV not found"}

    with _RECIPE_WRITE_LOCK:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))

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

        _timestamped_backup(path)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
        os.replace(tmp, str(path))
        return {"ok": True, "changes": changes}


def _recipe_rename(old_seq_name: str, new_name: str) -> dict:
    """Rename a recipe: renames SEQ file and updates :Names / EDIT_RECIPE_NAME in RECIPE.csv."""
    new_seq_name = new_name.strip()
    if not new_seq_name:
        return {"error": "New name cannot be empty"}

    recipe_dir = _get_recipe_dir()
    recipe_csv = recipe_dir / "RECIPE.csv"
    old_seq_path = recipe_dir / f"SEQ_{old_seq_name}.CSV"
    new_seq_path = recipe_dir / f"SEQ_{new_seq_name}.CSV"

    with _RECIPE_WRITE_LOCK:
        if not old_seq_path.exists():
            return {"error": f"SEQ_{old_seq_name}.CSV not found"}
        if new_seq_path.exists() and old_seq_name != new_seq_name:
            return {"error": f"SEQ_{new_seq_name}.CSV already exists"}

        # Update RECIPE.csv first
        with open(recipe_csv, encoding="utf-8-sig", newline="") as f:
            recipe_rows = list(csv.reader(f))

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

        _timestamped_backup(recipe_csv)
        tmp = str(recipe_csv) + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(new_recipe_rows)
        os.replace(tmp, str(recipe_csv))

        # Rename the SEQ file
        if old_seq_name != new_seq_name:
            os.rename(str(old_seq_path), str(new_seq_path))

        return {"ok": True, "old_seq_name": old_seq_name, "new_seq_name": new_seq_name}


def _recipe_delete(seq_name: str) -> dict:
    """Delete a recipe: removes its SEQ file and column from RECIPE.csv."""
    recipe_dir = _get_recipe_dir()
    recipe_csv = recipe_dir / "RECIPE.csv"
    seq_path   = recipe_dir / f"SEQ_{seq_name}.CSV"

    with _RECIPE_WRITE_LOCK:
        with open(recipe_csv, encoding="utf-8-sig", newline="") as f:
            recipe_rows = list(csv.reader(f))

        names_row = next(r for r in recipe_rows if r and r[0].strip() == ":Names")
        col = next((j for j, v in enumerate(names_row) if v.strip() == seq_name), None)
        if col is None:
            return {"error": f"Recipe '{seq_name}' not found in RECIPE.csv"}

        # Remove that column from every row
        new_recipe_rows = [
            [v for j, v in enumerate(r) if j != col]
            for r in recipe_rows
        ]

        _timestamped_backup(recipe_csv)
        tmp = str(recipe_csv) + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(new_recipe_rows)
        os.replace(tmp, str(recipe_csv))

        # Move deleted SEQ file into the backup dir so the recipe folder stays clean
        if seq_path.exists():
            bd = _backup_dir()
            bd.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            shutil.move(str(seq_path), bd / f"{seq_path.name}.{stamp}.deleted")

        return {"ok": True, "seq_name": seq_name}


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        if path == "/api/mode":
            mode = "live" if RUNNING_PORT == 8082 else "work"
            self._send_json({"mode": mode, "port": RUNNING_PORT})

        elif path == "/":
            self._serve_file(os.path.join(SCRIPT_DIR, "index.html"), "text/html; charset=utf-8")

        elif path in ("/optilayer", "/optilayer/"):
            self._serve_file(os.path.join(SCRIPT_DIR, "OptilayerIndexer", "index.html"), "text/html; charset=utf-8")

        elif path.startswith("/optilayer/") and self._serve_subproject_asset(path, "OptilayerIndexer"):
            pass  # handled

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

        elif path.startswith("/recipeeditor/") and self._serve_subproject_asset(path, "RecipeEditor"):
            pass  # handled

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

        elif path == "/api/version":
            watched = [
                BASE / "index.html",
                BASE / "RecipeEditor" / "index.html",
                BASE / "RecipeEditor" / "app.js",
                BASE / "RecipeEditor" / "app.css",
                BASE / "OptilayerIndexer" / "index.html",
                BASE / "OptilayerIndexer" / "app.js",
                BASE / "OptilayerIndexer" / "app.css",
            ]
            mtime = max(f.stat().st_mtime for f in watched if f.exists())
            label = "v " + datetime.fromtimestamp(mtime).strftime("%-d %b %Y %H:%M")
            self._send_json({"mtime": mtime, "label": label})

        elif path == "/recipeeditor/api/import-log":
            qs   = urllib.parse.parse_qs(parsed.query)
            name = qs.get("name", [""])[0].strip()
            log_path = _import_log_path()
            if not log_path.exists():
                self._send_json({"error": "No import log found"}, 404)
            else:
                with open(log_path, encoding="utf-8") as f:
                    records = json.load(f)
                if name:
                    records = [r for r in records if r.get("recipe_name") == name]
                    if not records:
                        self._send_json({"error": f"No import log entry for recipe '{name}'"}, 404)
                        return
                self._send_json(records)

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

    # Allow .css/.js assets co-located with each subproject's index.html.
    _ASSET_TYPES = {
        ".css": "text/css; charset=utf-8",
        ".js":  "application/javascript; charset=utf-8",
    }

    def _serve_subproject_asset(self, url_path: str, subdir: str) -> bool:
        """Serve a whitelisted static asset from `subdir`. Returns True if handled."""
        # url_path is e.g. "/optilayer/app.css" — strip the prefix
        prefix = "/" + subdir.lower().replace("indexer", "").replace("editor", "") + "/"
        # Simpler: derive from subdir directly
        if subdir == "OptilayerIndexer":
            prefix = "/optilayer/"
        elif subdir == "RecipeEditor":
            prefix = "/recipeeditor/"
        else:
            return False

        rel = url_path[len(prefix):]
        if not rel or "/" in rel or rel.startswith("."):
            return False  # no subdirs, no hidden files
        ext = os.path.splitext(rel)[1].lower()
        content_type = self._ASSET_TYPES.get(ext)
        if not content_type:
            return False
        filepath = os.path.join(SCRIPT_DIR, subdir, rel)
        if not os.path.isfile(filepath):
            return False
        self._serve_file(filepath, content_type)
        return True

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
                traceback.print_exc()
                self._send_json({"error": str(e)}, 500)

        elif path == "/recipeeditor/api/import":
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self._send_json({"error": "Empty request body"}, 400)
                return
            lpr_bytes = self.rfile.read(length)
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            recipe_name  = qs.get("name",     [""])[0].strip() or None
            lpr_filename = qs.get("filename", [""])[0].strip() or None
            try:
                result = _lpr_import(lpr_bytes, recipe_name, lpr_filename)
                self._send_json(result, 200 if result.get("ok") else 400)
            except Exception as e:
                traceback.print_exc()
                self._send_json({"error": str(e)}, 500)

        elif path == "/optilayer/api/update":
            try:
                entries = _optilayer_build_index()
                self._send_json({"ok": True, "count": len(entries)})
            except RuntimeError as e:
                # Configuration errors are user-fixable; surface cleanly.
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                traceback.print_exc()
                self._send_json({"error": str(e)}, 500)

        elif path == "/recipeeditor/api/import-settings":
            body = self._read_json_body()
            if not body or "material_steps" not in body or "recipe_dir" not in body:
                self._send_json({"error": "Missing recipe_dir or material_steps"}, 400)
                return
            err = _validate_import_settings(body)
            if err:
                self._send_json({"error": err}, 400)
                return
            try:
                _import_settings_save(body)
                self._send_json({"ok": True})
            except Exception as e:
                traceback.print_exc()
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
                traceback.print_exc()
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
                traceback.print_exc()
                self._send_json({"error": str(e)}, 500)

        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")


RUNNING_PORT = PORT  # updated at startup before server starts

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    RUNNING_PORT = port
    print(f"Design Tools     →  http://localhost:{port}")
    print(f"Optilayer Indexer →  http://localhost:{port}/optilayer/")
    print(f"Recipe Editor    →  http://localhost:{port}/recipeeditor/")
    print("Press Ctrl+C to stop.\n")
    with http.server.HTTPServer(("", port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
