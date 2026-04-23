# Recipe Editor

Web-based viewer and editor for deposition machine recipe files. Served at `/recipeeditor/` by the Command Central server.

## File layout

```
RecipeEditor/
  index.html          — Single-page UI
  recipe/
    RECIPE.csv        — Master recipe index (transposed CSV)
    SEQ_<name>.CSV    — Step sequence for each recipe
    SEQ_Template.CSV  — Template used when importing LPR files
```

## CSV format

Both RECIPE.csv and SEQ files are **transposed**: parameter names are rows, entries (recipes / steps) are columns.

| Column | RECIPE.csv | SEQ_*.CSV |
|--------|-----------|-----------|
| 0 | Parameter name | Parameter name |
| 1 | Ingredient type | Ingredient type |
| 2 | PREVIEW column | LOAD column |
| 3 | TEST column | Step 1 |
| 4+ | Recipe columns | Step 2+ |

Key parameters in RECIPE.csv:
- `:Names` — display name of each recipe (also used as the SEQ filename: `SEQ_<name>.CSV`)
- `EDIT_RECIPE_NAME` — same as `:Names`; both are updated on rename
- `PCOPC_FirstProcStepNo` / `PCOPC_LastProcStepNo` — step range from the SEQ file that belongs to this recipe

Key parameters in SEQ files:
- `SEQ_Enable` — 1 = step is active; shown as a checkbox, editable
- `SEQ_StepType` — 1=TIME, 2=RNDS, 3=OMS, 4=RATE; colour-coded dropdown
- `SEQ_TriggerFunction` — 1=OFFSET, 2=ABSOLUTE, 3=BACKWRD, 4=FORWARD; colour-coded dropdown
- `SEQ_RateReference` — checkbox (1/0)
- `SEQ_GSA` — decimal 1–5
- `SEQ_StepTime`, `SEQ_Rounds` — unsigned integer inputs
- `SEQ_Values` — displayed as "RC" column

## LPR import

Leybold `.LPR` files are XML process files. Import:
1. Takes steps 1–2 from `SEQ_Template.CSV` unchanged
2. Maps each `<ProcessLayer>` element to a coating step:

| LPR field | SEQ parameter |
|-----------|--------------|
| `Design[@number]` | `SEQ_BBM_LayerNo` |
| `Design[@material]` | `SEQ_MaterialID` (also derives `SEQ_StepName`) |
| `Design[@optical_thickness]` | `SEQ_QWOT` |
| `Design[@wavelength]` | `SEQ_Wavelength` |
| `OMSParameter[@Rate]` | `SEQ_Rate` |
| `OMSParameter[@EntranceSlit]` | `SEQ_Slits` |
| `OMSParameter[@SettlingTime]` | `SEQ_SettlingTime` |
| `OMSParameter[@GainAverage]` | `SEQ_GSA` |
| `OMSParameter[@TriggerPointFkt]` | `SEQ_TriggerFunction` (OFFSET→1, BACKWARD_2→3) |
| `OMSParameter[@ThicknessControl]` | `SEQ_StepType` (OMS→3, RATE→4) |
| `OMSParameter[@RateContol]` | `SEQ_Values` (OMS steps→1, RATE steps→value) |
| `OMSParameter[@RateControlEnable]` | `SEQ_RateReference` (Yes→1) |
| `dispersionsdata` n at design wavelength | `SEQ_RefractiveIndex` |
| OMS steps: `round(Rounds * 2/3)` | `SEQ_MaxTime` |

3. Adds a new column to RECIPE.csv (copied from the Template column, with name, date, and step range updated)

The process name is taken from the `name` attribute of the root `<process>` element, or from the `?name=` query parameter if provided.

## Safety

All write operations (save, import, rename, delete) create a `.bak` backup of the modified file before writing. Delete moves the SEQ file to `SEQ_<name>.CSV.deleted` rather than removing it.
