// ── Column definitions ────────────────────────────────────────
const COL_ORDER = [
  "SEQ_StepName", "SEQ_MaterialID", "SEQ_StepTime", "SEQ_QWOT",
  "SEQ_Wavelength", "SEQ_BBM_LayerNo", "SEQ_Testslide", "SEQ_StepType",
  "SEQ_MaxTime", "SEQ_Rounds", "SEQ_RefractiveIndex",
  "SEQ_SettlingTime", "SEQ_GSA", "SEQ_TriggerFunction",
  "SEQ_Slits", "SEQ_Rate", "SEQ_Values", "SEQ_RateReference"
];

const COL_LABELS = {
  "SEQ_StepName":       "Step Name",
  "SEQ_MaterialID":     "Material",
  "SEQ_StepTime":       "Time (s)",
  "SEQ_QWOT":           "QWOT",
  "SEQ_Wavelength":     "λ (nm)",
  "SEQ_BBM_LayerNo":    "Layer #",
  "SEQ_Testslide":      "Testslide",
  "SEQ_StepType":       "Type",
  "SEQ_MaxTime":        "Max Time",
  "SEQ_Rounds":         "Rounds",
  "SEQ_RefractiveIndex":"n",
  "SEQ_SettlingTime":   "Settle",
  "SEQ_GSA":            "GSA",
  "SEQ_TriggerFunction":"Trigger",
  "SEQ_Slits":          "Slits",
  "SEQ_Rate":           "Rate",
  "SEQ_Values":         "RC",
  "SEQ_RateReference":  "Rate Ref",
};

// ── State ─────────────────────────────────────────────────────
let allRecipes = [];
let activeRecipe = null;  // full recipe object {name, seq_name, first_step, last_step, …}

// ── Load recipe list ──────────────────────────────────────────
async function loadRecipes() {
  try {
    const r = await fetch("/recipeeditor/api/recipes");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    allRecipes = (await r.json()).sort((a, b) => a.name.localeCompare(b.name));
    document.getElementById("recipe-count").textContent = allRecipes.length;
    renderSidebar(allRecipes);
  } catch (e) {
    document.getElementById("recipe-list").innerHTML =
      `<div class="recipe-list-empty">Failed to load recipes</div>`;
  }
}

// ── Sidebar filter ────────────────────────────────────────────
document.getElementById("sidebar-search").addEventListener("input", function () {
  const q = this.value.trim().toLowerCase();
  const filtered = q ? allRecipes.filter(r => r.name.toLowerCase().includes(q)) : allRecipes;
  document.getElementById("recipe-count").textContent = filtered.length;
  renderSidebar(filtered);
});

function renderSidebar(recipes) {
  const list = document.getElementById("recipe-list");
  if (!recipes.length) {
    list.innerHTML = `<div class="recipe-list-empty">No recipes match</div>`;
    return;
  }
  list.innerHTML = recipes.map(r => `
    <div class="recipe-item${activeRecipe && r.seq_name === activeRecipe.seq_name ? " active" : ""}"
         onclick="selectRecipe(this, ${JSON.stringify(r).replace(/"/g,'&quot;')})">
      <span class="rname">${esc(r.name)}</span>
      <span class="rdate">${esc(r.change_date) || "—"}</span>
    </div>`).join("");
}

// ── Select recipe ─────────────────────────────────────────────
async function selectRecipe(el, recipe) {
  activeRecipe = recipe;
  document.querySelectorAll(".recipe-item").forEach(i => i.classList.remove("active"));
  if (el) el.classList.add("active");

  const main = document.getElementById("main");
  main.innerHTML = `<div class="spinner-wrap"><div class="spinner"></div></div>`;

  try {
    const r = await fetch(`/recipeeditor/api/seq?name=${encodeURIComponent(recipe.seq_name)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    renderRecipe(recipe, data);
  } catch (e) {
    main.innerHTML = `<div class="empty-state"><p style="color:#ef4444">Failed to load SEQ_${esc(recipe.seq_name)}.CSV</p></div>`;
  }
}

// ── Render recipe table ───────────────────────────────────────
function renderRecipe(recipe, data) {
  const main = document.getElementById("main");

  const first = recipe.first_step ?? 1;
  const last  = recipe.last_step  ?? 9999;

  const rangeSteps = data.steps.filter(s => {
    const n = parseInt(s.num, 10);
    return n >= first && n <= last;
  });

  const presentParams = new Set(data.params);
  const cols = [...COL_ORDER.filter(p => presentParams.has(p)),
                ...data.params.filter(p => !COL_ORDER.includes(p))];

  main.innerHTML = `
    <div class="recipe-view">
      <div class="recipe-toolbar">
        <span class="recipe-name-heading" id="recipe-name-heading" onclick="startRename()" title="Click to rename">${esc(recipe.name)}</span>
        <span class="step-meta">Steps ${first}–${last} &nbsp;·&nbsp; ${rangeSteps.length} step${rangeSteps.length !== 1 ? "s" : ""}</span>
        <span class="save-status" id="save-status"></span>
        <button class="btn-danger" id="btn-delete" onclick="deleteRecipe()">Delete</button>
        <button class="btn-save" id="btn-save" onclick="saveRecipe()">Save</button>
      </div>
      <div class="table-scroll">
        <div class="table-card">
          <table id="seq-table">
            <thead>
              <tr>
                <th class="cell-num">#</th>
                <th title="SEQ_Enable">En</th>
                ${cols.map(p => `<th>${esc(COL_LABELS[p] || p.replace("SEQ_",""))}</th>`).join("")}
              </tr>
            </thead>
            <tbody>${renderTableBody(rangeSteps, cols)}</tbody>
          </table>
        </div>
      </div>
    </div>`;

  main._data = data;
  main._cols = cols;
  main._recipe = recipe;
  main._rangeSteps = rangeSteps;
}

const STEP_TYPE    = {"1":"TIME","2":"RNDS","3":"OMS","4":"RATE"};
const TRIGGER_FUNC = {"1":"OFFSET","2":"ABSOLUTE","3":"BACKWRD","4":"FORWARD"};

function renderTableBody(steps, cols) {
  return steps.map((s, idx) => {
    const enabledVal = s.values["SEQ_Enable"] ?? (s.enabled ? "1" : "0");
    const isEnabled  = enabledVal === "1";
    return `<tr class="${isEnabled ? "" : "disabled-row"}" data-idx="${idx}">
      <td class="cell-num">${esc(s.num)}</td>
      <td class="cell-check">
        <input type="checkbox" ${isEnabled ? "checked" : ""}
               style="accent-color:var(--accent);cursor:pointer;"
               onchange="updateStep(${idx},'SEQ_Enable',this.checked?'1':'0',this)">
      </td>
      ${cols.map(p => renderCell(p, s.values[p] ?? "", idx, s)).join("")}
    </tr>`;
  }).join("");
}

function renderCell(param, value, idx, step) {
  if (param === "SEQ_RateReference") {
    return `<td class="cell-check">
      <input type="checkbox" ${value === "1" ? "checked" : ""}
             style="accent-color:var(--accent);cursor:pointer;"
             onchange="updateStep(${idx},'${param}',this.checked?'1':'0')">
    </td>`;
  }
  if (param === "SEQ_StepType") {
    const opts = Object.entries(STEP_TYPE).map(([v,l]) =>
      `<option value="${v}" ${value===v?"selected":""}>${l}</option>`).join("");
    return `<td><select class="cell-select type-select" data-val="${esc(value)}"
                  onchange="updateStep(${idx},'${param}',this.value,this)">
              ${opts}</select></td>`;
  }
  if (param === "SEQ_TriggerFunction") {
    const opts = Object.entries(TRIGGER_FUNC).map(([v,l]) =>
      `<option value="${v}" ${value===v?"selected":""}>${l}</option>`).join("");
    return `<td><select class="cell-select trigger-select" data-val="${esc(value)}"
                  onchange="updateStep(${idx},'${param}',this.value,this)">
              ${opts}</select></td>`;
  }
  if (param === "SEQ_GSA") {
    return `<td><input type="number" class="cell-input" value="${esc(value)}"
                  min="1" max="5" step="0.1"
                  onchange="updateStep(${idx},'${param}',this.value)"></td>`;
  }
  if (param === "SEQ_StepTime") {
    return `<td><input type="number" class="cell-input" value="${esc(value)}"
                  min="0" step="1"
                  onchange="updateStep(${idx},'${param}',this.value)"></td>`;
  }
  if (param === "SEQ_Rounds") {
    const disabled = (step?.values["SEQ_StepType"] ?? "") !== "2" ? "disabled" : "";
    return `<td><input type="number" class="cell-input wide rounds-input" value="${esc(value)}"
                  min="0" step="1" ${disabled}
                  onchange="updateStep(${idx},'${param}',this.value)"></td>`;
  }
  if (param === "SEQ_MaxTime") {
    return `<td class="maxtime-cell ${cellClass(param, value)}">${esc(value) || '<span class="cell-dash">—</span>'}</td>`;
  }
  const cls = cellClass(param, value);
  return `<td class="${cls}">${esc(value) || '<span class="cell-dash">—</span>'}</td>`;
}

function cellClass(param, value) {
  if (param === "SEQ_StepName") return "cell-name";
  if (param === "SEQ_MaterialID" && value && value !== "----" && value !== "---") return "cell-material";
  if (value === "0" || value === "0.0" || value === "0.00") return "cell-zero";
  return "";
}

function updateStep(idx, param, value, el) {
  const main = document.getElementById("main");
  if (!main._rangeSteps) return;
  const step = main._rangeSteps[idx];
  step.values[param] = value;

  // Also sync enabled flag and row styling for SEQ_Enable
  if (param === "SEQ_Enable") {
    step.enabled = value === "1";
    const row = el.closest("tr");
    row.classList.toggle("disabled-row", value !== "1");
  }

  // Re-colour select after change
  if (el && el.tagName === "SELECT") {
    el.dataset.val = value;
  }

  // Enable/disable Rounds input when Type changes; auto-calculate if switching to RNDS and rounds=0
  if (param === "SEQ_StepType" && el) {
    const row = el.closest("tr");

    const roundsInput = row?.querySelector(".rounds-input");
    if (roundsInput) {
      const isRnds = value === "2";
      roundsInput.disabled = !isRnds;
      if (!isRnds) {
        roundsInput.value = 0;
        step.values["SEQ_Rounds"] = "0";
      } else {
        const v = step.values;
        const n    = parseFloat(v["SEQ_RefractiveIndex"]);
        const qwot = parseFloat(v["SEQ_QWOT"]);
        const wl   = parseFloat(v["SEQ_Wavelength"]);
        const rate = parseFloat(v["SEQ_Rate"]);
        if (n > 0 && qwot > 0 && wl > 0 && rate > 0) {
          const thickness = (qwot * wl) / (4 * n);
          const rounds = Math.round((thickness / rate) * 3);
          roundsInput.value = rounds;
          step.values["SEQ_Rounds"] = String(rounds);
        }
      }
    }

    const isOms = value === "3";
    let maxTime = 0;
    if (isOms) {
      const v = step.values;
      const n    = parseFloat(v["SEQ_RefractiveIndex"]);
      const qwot = parseFloat(v["SEQ_QWOT"]);
      const wl   = parseFloat(v["SEQ_Wavelength"]);
      const rate = parseFloat(v["SEQ_Rate"]);
      if (n > 0 && qwot > 0 && wl > 0 && rate > 0) {
        const thickness = (qwot * wl) / (4 * n);
        maxTime = Math.round((thickness / rate) * 2);
      }
    }
    step.values["SEQ_MaxTime"] = String(maxTime);
    const maxTimeCell = row?.querySelector(".maxtime-cell");
    if (maxTimeCell) {
      maxTimeCell.textContent = String(maxTime);
      maxTimeCell.className = "maxtime-cell" + (maxTime === 0 ? " cell-zero" : "");
    }
  }
}

// ── Save ─────────────────────────────────────────────────────
async function saveRecipe() {
  const main   = document.getElementById("main");
  const btn    = document.getElementById("btn-save");
  const status = document.getElementById("save-status");
  if (!main._rangeSteps || !main._recipe) return;

  btn.disabled = true;
  btn.textContent = "Saving…";
  status.textContent = "";
  status.className = "save-status";

  const payload = {
    steps: main._rangeSteps.map(s => ({ num: s.num, values: s.values }))
  };

  try {
    const r = await fetch(
      `/recipeeditor/api/seq?name=${encodeURIComponent(main._recipe.seq_name)}`,
      { method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload) }
    );
    const data = await r.json();
    if (data.ok) {
      status.textContent = data.changes === 0 ? "No changes" : `Saved — ${data.changes} cell${data.changes !== 1 ? "s" : ""} updated`;
      status.className = "save-status ok";
    } else {
      status.textContent = data.error || "Save failed";
      status.className = "save-status error";
    }
  } catch (e) {
    status.textContent = "Network error";
    status.className = "save-status error";
  } finally {
    btn.disabled = false;
    btn.textContent = "Save";
    setTimeout(() => { status.textContent = ""; status.className = "save-status"; }, 5000);
  }
}

// ── Helpers ───────────────────────────────────────────────────
function esc(s) {
  return (s ?? "").toString()
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ── Rename ───────────────────────────────────────────────────
function startRename() {
  const main    = document.getElementById("main");
  const heading = document.getElementById("recipe-name-heading");
  const btnDel  = document.getElementById("btn-delete");
  const btnSave = document.getElementById("btn-save");
  if (!main._recipe || !heading) return;

  const current = main._recipe.seq_name;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "name-input";
  input.value = current;
  input.maxLength = 200;

  heading.replaceWith(input);
  btnDel.style.display = "none";
  btnSave.disabled = true;
  input.focus();
  input.select();

  input.addEventListener("keydown", e => {
    if (e.key === "Enter") commitRename(input, btnDel, btnSave);
    if (e.key === "Escape") cancelRename(input, btnDel, btnSave, current);
  });
  input.addEventListener("blur", () => commitRename(input, btnDel, btnSave));
}

function cancelRename(input, btnDel, btnSave, original) {
  const span = document.createElement("span");
  span.id = "recipe-name-heading";
  span.className = "recipe-name-heading";
  span.title = "Click to rename";
  span.textContent = original;
  span.onclick = startRename;
  input.replaceWith(span);
  btnDel.style.display = "";
  btnSave.disabled = false;
}

async function commitRename(input, btnDel, btnSave) {
  // Guard against blur firing after Enter already committed
  if (!input.isConnected) return;

  const main    = document.getElementById("main");
  const status  = document.getElementById("save-status");
  const newName = input.value.trim();
  const oldName = main._recipe.seq_name;

  if (!newName || newName === oldName) {
    cancelRename(input, btnDel, btnSave, oldName);
    return;
  }

  // Detach blur so it doesn't fire again during the async call
  input.onblur = null;
  input.disabled = true;

  try {
    const r = await fetch(
      `/recipeeditor/api/recipe?name=${encodeURIComponent(oldName)}`,
      { method: "PATCH", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({new_name: newName}) }
    );
    const data = await r.json();
    if (data.ok) {
      main._recipe.seq_name = newName;
      main._recipe.name     = newName;
      await loadRecipes();
      cancelRename(input, btnDel, btnSave, newName);
      document.querySelectorAll(".recipe-item").forEach(el => {
        if (el.querySelector(".rname")?.textContent === newName)
          el.classList.add("active");
      });
      status.textContent = "Renamed";
      status.className = "save-status ok";
      setTimeout(() => { status.textContent = ""; status.className = "save-status"; }, 3000);
    } else {
      status.textContent = data.error || "Rename failed";
      status.className = "save-status error";
      cancelRename(input, btnDel, btnSave, oldName);
    }
  } catch (e) {
    status.textContent = "Network error";
    status.className = "save-status error";
    cancelRename(input, btnDel, btnSave, oldName);
  }
}

// ── Delete ───────────────────────────────────────────────────
async function deleteRecipe() {
  const main = document.getElementById("main");
  if (!main._recipe) return;
  const name = main._recipe.seq_name;

  if (!confirm(`Delete recipe "${name}"?\n\nThis removes it from RECIPE.csv and renames the SEQ file to SEQ_${name}.CSV.deleted.\nThis cannot be undone from the UI.`))
    return;

  const btnDel = document.getElementById("btn-delete");
  btnDel.disabled = true;
  btnDel.textContent = "Deleting…";

  try {
    const r = await fetch(
      `/recipeeditor/api/recipe?name=${encodeURIComponent(name)}`,
      { method: "DELETE" }
    );
    const data = await r.json();
    if (data.ok) {
      activeRecipe = null;
      document.getElementById("main").innerHTML =
        `<div class="empty-state"><p>Recipe deleted</p></div>`;
      await loadRecipes();
    } else {
      btnDel.disabled = false;
      btnDel.textContent = "Delete";
      alert(`Delete failed:\n${data.error}`);
    }
  } catch (e) {
    btnDel.disabled = false;
    btnDel.textContent = "Delete";
    alert(`Network error:\n${e.message}`);
  }
}

// ── Import LPR ────────────────────────────────────────────────
document.getElementById("lpr-file-input").addEventListener("change", async function () {
  const file = this.files[0];
  if (!file) return;
  this.value = "";  // reset so same file can be re-selected

  const btn = document.querySelector(".btn-import");
  btn.disabled = true;
  btn.textContent = "Importing…";

  try {
    const bytes = await file.arrayBuffer();
    const r = await fetch(
      `/recipeeditor/api/import?name=${encodeURIComponent(file.name.replace(/\.LPR$/i, ""))}`,
      { method: "POST", headers: {"Content-Type": "application/octet-stream"},
        body: bytes }
    );
    const data = await r.json();
    if (data.ok) {
      // Reload recipe list and select the new recipe
      await loadRecipes();
      const newRecipe = allRecipes.find(rec => rec.seq_name === data.seq_name);
      if (newRecipe) {
        const items = document.querySelectorAll(".recipe-item");
        const item  = Array.from(items).find(el => el.querySelector(".rname").textContent === newRecipe.name);
        selectRecipe(item || null, newRecipe);
      }
      let msg = `Imported "${data.name}"\n${data.layers} coating layers → ${data.steps} total steps`;
      if (data.warnings && data.warnings.length > 0) {
        msg += `\n\n⚠️ Refractive index warnings (clamped values used):\n` +
          data.warnings.map(w => `• ${w}`).join("\n");
      }
      alert(msg);
    } else {
      alert(`Import failed:\n${data.error}`);
    }
  } catch (e) {
    alert(`Import error:\n${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Import LPR";
  }
});

loadRecipes();

// ── Settings ──────────────────────────────────────────────────
let _layerNames = [];
let _currentSettings = {};

async function openSettings() {
  const overlay = document.getElementById("settings-overlay");
  overlay.classList.add("open");
  document.getElementById("settings-save-status").textContent = "";
  document.getElementById("settings-save-status").className = "settings-save-status";

  const container = document.getElementById("material-rows");
  container.innerHTML = '<div style="color:var(--muted);font-size:.8rem;padding:12px 0;">Loading…</div>';

  try {
    const [namesRes, settingsRes] = await Promise.all([
      fetch("/recipeeditor/api/layer-names"),
      fetch("/recipeeditor/api/import-settings"),
    ]);
    _layerNames      = await namesRes.json();
    _currentSettings = await settingsRes.json();
    document.getElementById("recipe-dir-input").value = _currentSettings.recipe_dir || "";
    renderMaterialRows();
  } catch (e) {
    container.innerHTML = `<div style="color:#dc2626;font-size:.8rem;padding:12px 0;">Failed to load settings</div>`;
  }
}

function renderMaterialRows() {
  const matSteps = _currentSettings.material_steps || {};
  const container = document.getElementById("material-rows");

  container.innerHTML = Object.entries(matSteps).map(([mat, step]) => `
    <div class="material-row">
      <span class="material-label">${esc(mat)}</span>
      <select class="material-select" data-material="${esc(mat)}">
        ${_layerNames.map(n =>
          `<option value="${esc(n)}"${n === step ? " selected" : ""}>${esc(n)}</option>`
        ).join("")}
      </select>
    </div>
  `).join("");
}

async function saveSettings() {
  const selects = document.querySelectorAll(".material-select");
  const matSteps = {};
  selects.forEach(sel => { matSteps[sel.dataset.material] = sel.value; });
  const recipeDir = document.getElementById("recipe-dir-input").value.trim();

  const status = document.getElementById("settings-save-status");
  status.textContent = "";
  status.className = "settings-save-status";

  try {
    const r = await fetch("/recipeeditor/api/import-settings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ recipe_dir: recipeDir, material_steps: matSteps }),
    });
    const data = await r.json();
    if (data.ok) {
      _currentSettings.recipe_dir = recipeDir;
      _currentSettings.material_steps = matSteps;
      status.textContent = "Saved";
      status.className = "settings-save-status ok";
      setTimeout(() => closeSettings(), 800);
    } else {
      status.textContent = data.error || "Save failed";
      status.className = "settings-save-status error";
    }
  } catch (e) {
    status.textContent = "Network error";
    status.className = "settings-save-status error";
  }
}

function closeSettings() {
  document.getElementById("settings-overlay").classList.remove("open");
}

function overlayClick(e) {
  if (e.target === document.getElementById("settings-overlay")) closeSettings();
}

