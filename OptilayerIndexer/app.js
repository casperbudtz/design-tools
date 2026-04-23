let allEntries = [];
let totalCount = 0;
let searchTimer = null;

// ── Init ─────────────────────────────────────────────────────

async function loadAll() {
  setLoading(true);
  try {
    const r = await fetch("/optilayer/api/search?q=");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    allEntries = await r.json();
    totalCount = allEntries.length;
    renderRows(allEntries, "");
  } catch (e) {
    showState("⚠️", "Failed to load index", e.message);
  } finally {
    setLoading(false);
  }
}

// ── Search ───────────────────────────────────────────────────

document.getElementById("search").addEventListener("input", function () {
  const val = this.value;
  document.getElementById("clear-btn").classList.toggle("visible", val.length > 0);
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => doSearch(val), 120);
});

document.getElementById("search-comments").addEventListener("change", function () {
  doSearch(document.getElementById("search").value);
});

function doSearch(q) {
  const term = q.trim().toLowerCase();
  const includeComments = document.getElementById("search-comments").checked;
  if (!term) {
    renderRows(allEntries, "");
    return;
  }
  const filtered = allEntries.filter(e =>
    e.name.toLowerCase().includes(term) ||
    e.folder.toLowerCase().includes(term) ||
    (includeComments && e.comment.toLowerCase().includes(term))
  );
  renderRows(filtered, term);
}

function clearSearch() {
  document.getElementById("search").value = "";
  document.getElementById("clear-btn").classList.remove("visible");
  renderRows(allEntries, "");
  document.getElementById("search").focus();
}

// ── Render ───────────────────────────────────────────────────

function hl(text, term) {
  if (!term || !text) return esc(text);
  const idx = text.toLowerCase().indexOf(term);
  if (idx === -1) return esc(text);
  return esc(text.slice(0, idx)) +
    "<mark>" + esc(text.slice(idx, idx + term.length)) + "</mark>" +
    esc(text.slice(idx + term.length));
}

function esc(s) {
  return (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function renderRows(entries, term) {
  const tbody  = document.getElementById("tbody");
  const stateB = document.getElementById("state-box");
  const count  = document.getElementById("result-count");

  if (entries.length === 0) {
    tbody.innerHTML = "";
    if (term) {
      showState("🔍", "No designs match your search", `Try a different keyword`);
    } else {
      showState("📂", "No designs indexed", "Click 'Update Index' to scan for designs");
    }
    count.innerHTML = "<strong>0</strong> designs";
    return;
  }

  stateB.style.display = "none";

  if (term) {
    count.innerHTML = `<strong>${entries.length}</strong> of <strong>${totalCount}</strong> designs`;
  } else {
    count.innerHTML = `<strong>${entries.length}</strong> design${entries.length !== 1 ? "s" : ""}`;
  }

  tbody.innerHTML = entries.map(e => {
    const mod = e.modified ? e.modified.replace('T', ' ') : null;
    const modDate = e.modified ? e.modified.slice(0, 10) : null;
    return `
    <tr>
      <td class="td-name" title="${esc(e.name)}">${hl(e.name, term)}</td>
      <td class="td-folder" title="${esc(e.folder)}"><span class="folder-badge">${hl(e.folder, term)}</span></td>
      <td class="td-comment" title="${esc(e.comment)}">${hl(e.comment, term) || '<span style="opacity:.35">—</span>'}</td>
      <td class="td-modified" title="${esc(mod)}">${modDate ? esc(modDate) : '<span style="opacity:.35">—</span>'}</td>
    </tr>`;
  }).join("");
}

function showState(icon, msg, hint) {
  const stateB = document.getElementById("state-box");
  stateB.style.display = "block";
  stateB.innerHTML = `
    <div class="state-icon">${icon}</div>
    <p>${esc(msg)}</p>
    ${hint ? `<p class="state-hint">${esc(hint)}</p>` : ""}`;
}

function setLoading(on) {
  document.getElementById("loading-bar").classList.toggle("active", on);
}

// ── Update index ─────────────────────────────────────────────

async function updateIndex() {
  const btn    = document.getElementById("btn-update");
  const status = document.getElementById("update-status");
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Scanning…';
  status.textContent = "";
  status.className = "update-status";

  try {
    const r    = await fetch("/optilayer/api/update", { method: "POST" });
    const data = await r.json();
    if (data.ok) {
      status.textContent = `Updated — ${data.count} designs`;
      status.className = "update-status ok";
      await loadAll();
      setTimeout(() => { status.textContent = ""; status.className = "update-status"; }, 6000);
    } else {
      status.textContent = data.error || "Update failed";
      status.className = "update-status error";
    }
  } catch (e) {
    status.textContent = "Network error";
    status.className = "update-status error";
  } finally {
    btn.disabled = false;
    btn.innerHTML = `
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="1 4 1 10 7 10"/><polyline points="15 12 15 6 9 6"/>
        <path d="M15 6A7 7 0 1 0 13.4 12.4"/><path d="M1 10A7 7 0 1 0 2.6 3.6"/>
      </svg>
      Update Index`;
  }
}

loadAll();
