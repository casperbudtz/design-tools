# Design Tools

Web server for optical coating design utilities. Provides two tools:

- **Optilayer Indexer** at `/optilayer/` — search index for OptiLayer multilayer film designs
- **Recipe Editor** at `/recipeeditor/` — viewer and editor for PVD deposition machine recipes

## Dependencies

Python 3 standard library only — no pip packages required.

## Configuration

### OptiLayer data path

The indexer reads `DESIGNA.DBS` files from a network share. Set the path before starting:

```bash
export OPTILAYER_DIR="/mnt/server/Data/Film Data/OptiLayer"
python3 server.py
```

Or edit the `_DEFAULT_OPTILAYER_DIR` constant in `server.py` directly.

## Running manually

```bash
python3 server.py [port]
# Default port: 8080
```

Open [http://localhost:8080](http://localhost:8080).

## Running at boot (systemd)

1. Edit `design-tools.service` — replace `<target-user>` and paths with actual values
2. Install and enable:

```bash
sudo cp design-tools.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now design-tools
```

## Restart script

```bash
bash restart-server.sh
```

Kills any process on port 8080 and restarts the server. Logs to `/tmp/design-tools.log`.

## Recipe data

Recipe CSV files live in `RecipeEditor/recipe/`. All write operations (save, import, rename, delete) create `.bak` backups of modified files. See `RecipeEditor/README.md` for full details.
