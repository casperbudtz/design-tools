# CLAUDE.md

This file provides guidance to Claude Code when working in the `DesignTools` repository.

## Workspace Structure

- `server.py` — Web server (port 8081). Serves the Design Tools landing page and routes sub-project requests.
- `index.html` — Design Tools landing page. Two cards: Optilayer Indexer and Recipe Editor.
- `RecipeEditor/` — Viewer and editor for deposition machine recipe files. Reads `RecipeEditor/recipe/RECIPE.csv` and the corresponding `SEQ_<name>.CSV` sequence files. See `RecipeEditor/README.md` for full details.
- `OptilayerIndexer/` — Search index for OptiLayer multilayer film designs. Parses `DESIGNA.DBS` binary index files. Incremental: caches mtime and skips unchanged folders. Index stored in `OptilayerIndexer/index.json`.
- `design-tools.service` — systemd service file template (contains `<target-user>` placeholders to fill in on the target machine).
- `restart-server.sh` — Kills the running server on port 8081 and restarts it (logs to `/tmp/design-tools.log`).

## Running

```bash
# Default port 8081:
python3 server.py [port]

# Override OptiLayer data path:
export OPTILAYER_DIR="/mnt/server/Data/Film Data/OptiLayer"
python3 server.py
```

- `http://localhost:8081/` — Design Tools landing page
- `http://localhost:8081/optilayer/` — Optilayer Indexer
- `http://localhost:8081/recipeeditor/` — Recipe Editor

## URL Routes

| Route | Description |
|---|---|
| `GET /` | Design Tools landing page (`index.html`) |
| `GET /optilayer/` | Optilayer Indexer (`OptilayerIndexer/index.html`) |
| `GET /optilayer/api/search` | Search index (`?q=`); empty q returns all |
| `POST /optilayer/api/update` | Rebuild index from `OPTILAYER_DIR` |
| `GET /recipeeditor/` | Recipe Editor (`RecipeEditor/index.html`) |
| `GET /recipeeditor/api/recipes` | List all recipes from `RECIPE.csv` |
| `GET /recipeeditor/api/seq` | SEQ file for a recipe (`?name=`) |
| `POST /recipeeditor/api/seq` | Save edited steps back to SEQ CSV (`?name=`) |
| `POST /recipeeditor/api/import` | Import a Leybold `.LPR` file as a new recipe (`?name=` optional) |
| `GET /recipeeditor/api/layer-names` | List valid step names from `Layer.CSV` |
| `GET /recipeeditor/api/import-settings` | Load material→step mapping (`RecipeEditor/import_settings.json`) |
| `POST /recipeeditor/api/import-settings` | Save material→step mapping |
| `PATCH /recipeeditor/api/recipe` | Rename a recipe (`?name=`, body `{"new_name":"..."}`) |
| `DELETE /recipeeditor/api/recipe` | Delete a recipe and its SEQ file (`?name=`) |

## Architecture Notes

### OptiLayer Indexer

Parses `DESIGNA.DBS` binary index files (one per OptiLayer project folder). The DBS format has a 13-byte header, then records of: uint16 slot, counted-string name, two action blocks (create + modify datetime + username), counted-string comment. Counted strings use a uint32 length prefix where bit 31 = encoding flag (0=UTF-8 byte count, 1=UTF-16-LE char count). The `modified` datetime is taken from the second action block. Incremental indexing caches file mtime.

The `OPTILAYER_DIR` path is configurable via environment variable (see above). The default value in `server.py` is a KIO FUSE mount path specific to the development machine — **this must be updated on the target machine**.

`OptilayerIndexer/indexer.py` is a standalone script that mirrors the same parsing logic and can be run manually or via cron:
```bash
python3 OptilayerIndexer/indexer.py
```

### Recipe Editor

Reads `RecipeEditor/recipe/RECIPE.csv` and `SEQ_*.CSV` files. Both CSVs are **transposed**: parameter names are in column 0, and recipes/steps are in subsequent columns. RECIPE.csv has 4 non-recipe columns (param, type, PREVIEW, TEST); SEQ files have 3 (param, type, LOAD). The `PCOPC_FirstProcStepNo` and `PCOPC_LastProcStepNo` rows in RECIPE.csv define which steps from the SEQ file belong to that recipe.

LPR import reads a Leybold XML process file, takes the first 2 steps from `SEQ_Template.CSV`, and maps each `<ProcessLayer>` to a coating step.

Rename updates **both** `:Names` and `EDIT_RECIPE_NAME` rows in RECIPE.csv and renames the SEQ file — both must always stay in sync.

Delete moves the SEQ file to `.CSV.deleted` rather than removing it outright.

All write operations back up files to `.bak` before modifying.

## UI Conventions

- Sub-pages do not have a back-link. Navigation is handled by the tab bar in the main `index.html` shell.
