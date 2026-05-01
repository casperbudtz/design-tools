#!/usr/bin/env python3
"""
Design Tools — web server
Serves the Design Tools landing page and routes sub-project requests.

Routes:
    GET    /                                 → index.html (Design Tools landing page)
    GET    /api/mode                         → {"mode": "live"|"work", "port": int}
    GET    /api/version                      → max mtime across watched frontend files

    GET    /optilayer                        → OptilayerIndexer/index.html
    GET    /optilayer/                       → OptilayerIndexer/index.html
    GET    /optilayer/<asset.css|js>         → static asset from OptilayerIndexer/
    GET    /optilayer/api/search             → search index (?q=)
    POST   /optilayer/api/update             → rebuild index from OPTILAYER_DIR

    GET    /recipeeditor                     → RecipeEditor/index.html
    GET    /recipeeditor/                    → RecipeEditor/index.html
    GET    /recipeeditor/<asset.css|js>      → static asset from RecipeEditor/
    GET    /recipeeditor/api/recipes         → list all recipes from RECIPE.csv
    GET    /recipeeditor/api/seq             → SEQ file for a recipe (?name=)
    POST   /recipeeditor/api/seq             → save edited steps (?name=)
    POST   /recipeeditor/api/import          → import LPR file (?name=, ?filename=)
    GET    /recipeeditor/api/layer-names     → list step names from Layer.CSV
    GET    /recipeeditor/api/import-settings → load material→step mapping
    POST   /recipeeditor/api/import-settings → save material→step mapping
    GET    /recipeeditor/api/import-log      → all log entries, or ?name= filtered
    PATCH  /recipeeditor/api/recipe          → rename a recipe (?name=, body new_name)
    DELETE /recipeeditor/api/recipe          → delete a recipe (?name=)

Usage:
    python3 server.py [port]
    Default port: 8081

Configuration:
    OPTILAYER_DIR environment variable — path to the OptiLayer data root.
        export OPTILAYER_DIR="/mnt/server/Data/Film Data/OptiLayer"
        python3 server.py
"""

import http.server
import importlib.util
import json
import os
import sys
import traceback
import urllib.parse
from datetime import datetime
from pathlib import Path

PORT       = 8081
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE       = Path(SCRIPT_DIR)


# ── Load business-logic modules ───────────────────────────────────────────────

def _load_module(name, rel_path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPT_DIR, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

indexer      = _load_module("indexer",      "OptilayerIndexer/indexer.py")
recipe_logic = _load_module("recipe_logic", "RecipeEditor/recipe_logic.py")


# ── Optilayer config ──────────────────────────────────────────────────────────

# Path to the folder containing OptiLayer project subdirs, each holding a
# DESIGNA.DBS index file. Set via env var on the target machine:
#   export OPTILAYER_DIR="/mnt/server/Data/Film Data/OptiLayer"
_optilayer_env = os.environ.get("OPTILAYER_DIR", "").strip()
OPTILAYER_DIR  = Path(_optilayer_env) if _optilayer_env else None


# ── Route handlers ────────────────────────────────────────────────────────────
#
# Each handler takes (h: Handler, parsed: ParseResult) and is responsible for
# sending the response. Static-page handlers are wrapped via _static().

def _static(rel_path):
    fullpath = os.path.join(SCRIPT_DIR, rel_path)
    def handler(h, parsed):
        try:
            with open(fullpath, "rb") as f:
                content = f.read()
        except FileNotFoundError:
            h.send_error(404); return
        h.send_response(200)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Length", str(len(content)))
        h.end_headers()
        h.wfile.write(content)
    return handler


_ASSET_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js":  "application/javascript; charset=utf-8",
}

def _serve_asset(h, subdir, rel):
    """Serve a whitelisted .css/.js asset from `subdir`."""
    if not rel or "/" in rel or rel.startswith("."):
        h.send_error(404); return False
    ext = os.path.splitext(rel)[1].lower()
    content_type = _ASSET_TYPES.get(ext)
    if not content_type:
        return False
    fullpath = os.path.join(SCRIPT_DIR, subdir, rel)
    if not os.path.isfile(fullpath):
        return False
    with open(fullpath, "rb") as f:
        content = f.read()
    h.send_response(200)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(content)))
    h.end_headers()
    h.wfile.write(content)
    return True


def _h_mode(h, parsed):
    mode = "live" if RUNNING_PORT == 8082 else "work"
    h._send_json({"mode": mode, "port": RUNNING_PORT})


def _h_version(h, parsed):
    watched = [
        BASE / "index.html",
        BASE / "RecipeEditor" / "index.html",
        BASE / "RecipeEditor" / "app.js",
        BASE / "RecipeEditor" / "app.css",
        BASE / "OptilayerIndexer" / "index.html",
        BASE / "OptilayerIndexer" / "app.js",
        BASE / "OptilayerIndexer" / "app.css",
    ]
    mtimes = [f.stat().st_mtime for f in watched if f.exists()]
    if not mtimes:
        h._send_json({"error": "No frontend files found"}, 500); return
    mtime = max(mtimes)
    label = "v " + datetime.fromtimestamp(mtime).strftime("%-d %b %Y %H:%M")
    h._send_json({"mtime": mtime, "label": label})


# ── OptiLayer ──

def _h_optilayer_search(h, parsed):
    params  = urllib.parse.parse_qs(parsed.query)
    q       = params.get("q", [""])[0].strip().lower()
    entries = indexer.load_index()
    if q:
        entries = [e for e in entries if
                   q in e["name"].lower() or
                   q in e["comment"].lower() or
                   q in e["folder"].lower()]
    h._send_json(entries)


def _h_optilayer_update(h, parsed):
    try:
        entries = indexer.build_index(OPTILAYER_DIR)
        h._send_json({"ok": True, "count": len(entries)})
    except RuntimeError as e:
        h._send_json({"error": str(e)}, 400)
    except Exception as e:
        traceback.print_exc()
        h._send_json({"error": str(e)}, 500)


# ── Recipe Editor ──

def _h_recipe_list(h, parsed):
    try:
        h._send_json(recipe_logic.recipe_list())
    except FileNotFoundError as e:
        h._send_json({"error": str(e)}, 404)
    except RuntimeError as e:
        h._send_json({"error": str(e)}, 500)


def _h_seq_get(h, parsed):
    name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0].strip()
    data = recipe_logic.seq_data(name)
    if data is None:
        h._send_json({"error": f"SEQ_{name}.CSV not found"}, 404)
    else:
        h._send_json(data)


def _h_seq_save(h, parsed):
    qs   = urllib.parse.parse_qs(parsed.query)
    name = qs.get("name", [""])[0].strip()
    body = h._read_json_body()
    if not name or not body or "steps" not in body:
        h._send_json({"error": "Missing name or steps"}, 400); return
    try:
        result = recipe_logic.seq_save(name, body["steps"])
        h._send_json(result, 200 if result.get("ok") else 400)
    except Exception as e:
        traceback.print_exc()
        h._send_json({"error": str(e)}, 500)


def _h_lpr_import(h, parsed):
    length = int(h.headers.get("Content-Length", 0))
    if length == 0:
        h._send_json({"error": "Empty request body"}, 400); return
    lpr_bytes = h.rfile.read(length)
    qs = urllib.parse.parse_qs(parsed.query)
    recipe_name  = qs.get("name",     [""])[0].strip() or None
    lpr_filename = qs.get("filename", [""])[0].strip() or None
    try:
        result = recipe_logic.lpr_import(lpr_bytes, recipe_name, lpr_filename)
        h._send_json(result, 200 if result.get("ok") else 400)
    except Exception as e:
        traceback.print_exc()
        h._send_json({"error": str(e)}, 500)


def _h_layer_names(h, parsed):
    h._send_json(recipe_logic.layer_names())


def _h_settings_get(h, parsed):
    h._send_json(recipe_logic.settings_load())


def _h_settings_save(h, parsed):
    body = h._read_json_body()
    if not body or "material_steps" not in body or "recipe_dir" not in body:
        h._send_json({"error": "Missing recipe_dir or material_steps"}, 400); return
    err = recipe_logic.validate_settings(body)
    if err:
        h._send_json({"error": err}, 400); return
    try:
        recipe_logic.settings_save(body)
        h._send_json({"ok": True})
    except Exception as e:
        traceback.print_exc()
        h._send_json({"error": str(e)}, 500)


def _h_import_log(h, parsed):
    name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0].strip()
    log_path = recipe_logic.import_log_path()
    if not log_path.exists():
        h._send_json({"error": "No import log found"}, 404); return
    with open(log_path, encoding="utf-8") as f:
        records = json.load(f)
    if name:
        records = [r for r in records if r.get("recipe_name") == name]
        if not records:
            h._send_json({"error": f"No import log entry for recipe '{name}'"}, 404); return
    h._send_json(records)


def _h_recipe_delete(h, parsed):
    name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0].strip()
    if not name:
        h._send_json({"error": "Missing name"}, 400); return
    try:
        result = recipe_logic.recipe_delete(name)
        h._send_json(result, 200 if result.get("ok") else 400)
    except Exception as e:
        traceback.print_exc()
        h._send_json({"error": str(e)}, 500)


def _h_recipe_rename(h, parsed):
    qs       = urllib.parse.parse_qs(parsed.query)
    name     = qs.get("name", [""])[0].strip()
    body     = h._read_json_body()
    new_name = (body or {}).get("new_name", "").strip()
    if not name or not new_name:
        h._send_json({"error": "Missing name or new_name"}, 400); return
    try:
        result = recipe_logic.recipe_rename(name, new_name)
        h._send_json(result, 200 if result.get("ok") else 400)
    except Exception as e:
        traceback.print_exc()
        h._send_json({"error": str(e)}, 500)


# ── Route table ───────────────────────────────────────────────────────────────

ROUTES = {
    ("GET",    "/"):                                  _static("index.html"),
    ("GET",    "/api/mode"):                          _h_mode,
    ("GET",    "/api/version"):                       _h_version,

    ("GET",    "/optilayer/"):                        _static("OptilayerIndexer/index.html"),
    ("GET",    "/optilayer/api/search"):              _h_optilayer_search,
    ("POST",   "/optilayer/api/update"):              _h_optilayer_update,

    ("GET",    "/recipeeditor/"):                     _static("RecipeEditor/index.html"),
    ("GET",    "/recipeeditor/api/recipes"):          _h_recipe_list,
    ("GET",    "/recipeeditor/api/seq"):              _h_seq_get,
    ("POST",   "/recipeeditor/api/seq"):              _h_seq_save,
    ("POST",   "/recipeeditor/api/import"):           _h_lpr_import,
    ("GET",    "/recipeeditor/api/layer-names"):      _h_layer_names,
    ("GET",    "/recipeeditor/api/import-settings"):  _h_settings_get,
    ("POST",   "/recipeeditor/api/import-settings"):  _h_settings_save,
    ("GET",    "/recipeeditor/api/import-log"):       _h_import_log,
    ("PATCH",  "/recipeeditor/api/recipe"):           _h_recipe_rename,
    ("DELETE", "/recipeeditor/api/recipe"):           _h_recipe_delete,
}

# Allow GET on each sub-page index without the trailing slash too.
for _slash in ("/optilayer/", "/recipeeditor/"):
    ROUTES[("GET", _slash.rstrip("/"))] = ROUTES[("GET", _slash)]


# ── HTTP handler ──────────────────────────────────────────────────────────────

_ASSET_PREFIXES = {
    "/optilayer/":    "OptilayerIndexer",
    "/recipeeditor/": "RecipeEditor",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def _dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        fn = ROUTES.get((method, parsed.path))
        if fn is not None:
            fn(self, parsed)
            return
        # Fallback: static asset for /optilayer/ or /recipeeditor/
        if method == "GET":
            for prefix, subdir in _ASSET_PREFIXES.items():
                if parsed.path.startswith(prefix) and parsed.path != prefix:
                    rel = parsed.path[len(prefix):]
                    if _serve_asset(self, subdir, rel):
                        return
                    break
        self.send_error(404)

    do_GET    = lambda self: self._dispatch("GET")
    do_POST   = lambda self: self._dispatch("POST")
    do_PUT    = lambda self: self._dispatch("PUT")
    do_DELETE = lambda self: self._dispatch("DELETE")
    do_PATCH  = lambda self: self._dispatch("PATCH")

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
