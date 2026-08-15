const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Request failed: ${res.status}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------- tabs ----------
// A dynamically-opened scene editor tab (see "scene editor" section below)
// gets discarded -- unsaved changes and all -- whenever any OTHER tab is
// activated. Static tabs and the dynamic scene tab both route through here.
// Leaving a static tab that has a collapsible "create" form open (Characters,
// Locations, Scenes) also collapses that form back down, same as Cancel.
function activateTab(tabId) {
  const currentBtn = document.querySelector(".tab.active");
  const currentTabId = currentBtn ? currentBtn.dataset.tab : null;

  if (openSceneEditor && tabId !== openSceneEditor.tabId) {
    closeSceneEditor();  // silently discards any unsaved local edits
  }
  if (currentTabId && currentTabId !== tabId) {
    if (currentTabId === "characters") hideCharacterForm();
    else if (currentTabId === "locations") hideLocationForm();
    else if (currentTabId === "scenes") hideSceneForm();
  }

  $$(".tab").forEach(b => b.classList.remove("active"));
  $$(".tab-panel").forEach(p => p.classList.remove("active"));
  const btn = document.querySelector(`.tab[data-tab="${tabId}"]`);
  const panel = $(`#tab-${tabId}`);
  if (btn) btn.classList.add("active");
  if (panel) panel.classList.add("active");
}

$$(".tab").forEach(btn => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

function parsePropertiesField(form) {
  const raw = form.properties.value.trim();
  if (!raw) return {};
  try { return JSON.parse(raw); }
  catch { alert("Custom properties must be valid JSON (or left blank)."); throw new Error("bad json"); }
}

// ---------- characters: attire sub-editor ----------
function attireRowHtml(attire = {}) {
  const id = attire.id || "";
  const checked = attire.is_default ? "checked" : "";
  return `
    <div class="attire-row" data-attire-id="${id}">
      <input type="hidden" class="attire-id" value="${id}">
      <input type="text" class="attire-label" placeholder="Label (e.g. 'Uniform')" value="${attire.label || ""}">
      <input type="text" class="attire-image" placeholder="Filename in ComfyUI's input/ dir (or an absolute path)" value="${attire.image_path || ""}">
      <input type="text" class="attire-desc" placeholder="Optional description (clothing details for the prompt)" value="${attire.description || ""}">
      <label class="default-toggle"><input type="radio" name="attire_default" ${checked}> Default</label>
      <button type="button" data-remove-attire>×</button>
    </div>`;
}

function addAttireRow(attire = {}) {
  const container = $("#attire-rows");
  const wrapper = document.createElement("div");
  wrapper.innerHTML = attireRowHtml(attire);
  const row = wrapper.firstElementChild;
  row.querySelector("[data-remove-attire]").onclick = () => row.remove();
  container.appendChild(row);
}

function clearAttireRows() {
  $("#attire-rows").innerHTML = "";
}

function collectAttireOptions() {
  return $$(".attire-row", $("#attire-rows")).map(row => ({
    id: row.querySelector(".attire-id").value || undefined,
    label: row.querySelector(".attire-label").value,
    image_path: row.querySelector(".attire-image").value,
    description: row.querySelector(".attire-desc").value,
    is_default: row.querySelector('input[name="attire_default"]').checked,
  }));
}

$("#add-attire-row").addEventListener("click", () => addAttireRow());

// ---------- shared category-dropdown helper (characters, locations each have
// their own independent category namespace, not shared between them) ----------
function makeCategoryPicker(selectEl, newInputEl) {
  function populate(categories, selectedValue = "") {
    const noneSelected = selectedValue === "" ? "selected" : "";
    const opts = categories.map(cat =>
      `<option value="${cat}" ${cat === selectedValue ? "selected" : ""}>${cat}</option>`).join("");
    selectEl.innerHTML = `<option value="" ${noneSelected}>— no category —</option>${opts}<option value="__new__">+ New category…</option>`;
    newInputEl.style.display = "none";
  }
  selectEl.addEventListener("change", () => {
    newInputEl.style.display = selectEl.value === "__new__" ? "block" : "none";
  });
  function resolveValue() {
    return selectEl.value === "__new__" ? newInputEl.value.trim() : selectEl.value;
  }
  return { populate, resolveValue };
}

const characterCategoryPicker = makeCategoryPicker(
  $("#character-category-select"), $("#character-category-new"));
const locationCategoryPicker = makeCategoryPicker(
  $("#location-category-select"), $("#location-category-new"));

// Category groups default collapsed on both tabs -- these track which ones
// the user has explicitly expanded, by category name. Persists across
// refreshCharacters()/refreshLocations() calls (e.g. after add/edit/delete)
// since those rebuild the list from scratch every time.
const expandedCharacterCategories = new Set();
const expandedLocationCategories = new Set();

// ---------- characters ----------
async function refreshCharacters() {
  const list = await api("/characters");
  const categories = [...new Set(list.map(c => c.category).filter(Boolean))].sort();
  characterCategoryPicker.populate(categories, "");

  const container = $("#character-list");
  container.innerHTML = "";

  const grouped = {};
  for (const c of list) {
    const key = c.category || "Uncategorized";
    (grouped[key] = grouped[key] || []).push(c);
  }
  const sortedKeys = Object.keys(grouped).sort((a, b) => {
    if (a === "Uncategorized") return 1;
    if (b === "Uncategorized") return -1;
    return a.localeCompare(b);
  });

  for (const key of sortedKeys) {
    const expanded = expandedCharacterCategories.has(key);

    const group = document.createElement("div");
    group.className = "category-group";

    const heading = document.createElement("div");
    heading.className = "category-heading";
    const caret = document.createElement("span");
    caret.className = "category-caret";
    caret.textContent = expanded ? "▾" : "▸";
    heading.appendChild(caret);
    heading.appendChild(document.createTextNode(" " + key));
    heading.onclick = () => {
      if (expandedCharacterCategories.has(key)) expandedCharacterCategories.delete(key);
      else expandedCharacterCategories.add(key);
      refreshCharacters();
    };
    group.appendChild(heading);

    const body = document.createElement("div");
    body.className = "category-body";
    body.style.display = expanded ? "" : "none";
    group.appendChild(body);

    for (const c of grouped[key]) {
      const card = document.createElement("div");
      card.className = "entity-card";
      const attireNote = c.attire_options.length
        ? `${c.attire_options.length} attire option(s)`
        : "no attire options";
      const faceThumbHtml = `<div class="char-thumb-wrap">${
        c.face_image ? `<img class="char-thumb" src="${mediaUrl(c.face_image)}" alt="" onerror="this.remove()">` : ""
      }</div>`;
      card.innerHTML = `
        <div class="entity-card-main">
          ${faceThumbHtml}
          <div>
            <strong>${c.name}</strong>
            <div class="meta">${c.face_image || "no face image"} · ${c.voice_audio || "no voice"} · ${attireNote}</div>
          </div>
        </div>
        <div>
          <button data-edit>Edit</button>
          <button data-delete class="danger">Delete</button>
        </div>`;
      card.querySelector("[data-edit]").onclick = () => {
        showCharacterForm();
        const f = $("#character-form");
        f.id.value = c.id; f.name.value = c.name;
        f.face_image.value = c.face_image;
        f.voice_audio.value = c.voice_audio;
        f.appearance_description.value = c.appearance_description;
        f.properties.value = JSON.stringify(c.properties, null, 2);
        if (c.category && categories.includes(c.category)) {
          characterCategoryPicker.populate(categories, c.category);
        } else if (c.category) {
          characterCategoryPicker.populate(categories, "__new__");
          f.category_new.value = c.category;
        } else {
          characterCategoryPicker.populate(categories, "");
        }
        clearAttireRows();
        c.attire_options.forEach(a => addAttireRow(a));
      };
      card.querySelector("[data-delete]").onclick = async () => {
        if (confirm(`Delete character "${c.name}"?`)) {
          await api(`/characters/${c.id}`, { method: "DELETE" });
          refreshCharacters(); refreshCastingCharacterSelect();
        }
      };
      body.appendChild(card);
    }

    container.appendChild(group);
  }
}

// ---------- collapsible "new character" form: hidden by default ----------
function showCharacterForm() {
  $("#character-form").style.display = "flex";
  $("#add-character-btn").style.display = "none";
}
function hideCharacterForm() {
  $("#character-form").style.display = "none";
  $("#add-character-btn").style.display = "";
  $("#character-form").reset();
  $("#character-form").id.value = "";
  clearAttireRows();
}
$("#add-character-btn").addEventListener("click", showCharacterForm);
$("#cancel-character-btn").addEventListener("click", hideCharacterForm);

$("#character-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  let properties;
  try { properties = parsePropertiesField(f); } catch { return; }
  const category = characterCategoryPicker.resolveValue();
  const payload = {
    name: f.name.value, face_image: f.face_image.value,
    voice_audio: f.voice_audio.value,
    category,
    appearance_description: f.appearance_description.value,
    attire_options: collectAttireOptions(),
    properties,
  };
  if (f.id.value) await api(`/characters/${f.id.value}`, { method: "PUT", body: JSON.stringify(payload) });
  else await api("/characters", { method: "POST", body: JSON.stringify(payload) });
  hideCharacterForm();
  refreshCharacters(); refreshCastingCharacterSelect();
});

// ---------- locations ----------
async function refreshLocations() {
  const list = await api("/locations");
  const categories = [...new Set(list.map(loc => loc.category).filter(Boolean))].sort();
  locationCategoryPicker.populate(categories, "");

  const container = $("#location-list");
  container.innerHTML = "";

  const grouped = {};
  for (const loc of list) {
    const key = loc.category || "Uncategorized";
    (grouped[key] = grouped[key] || []).push(loc);
  }
  const sortedKeys = Object.keys(grouped).sort((a, b) => {
    if (a === "Uncategorized") return 1;
    if (b === "Uncategorized") return -1;
    return a.localeCompare(b);
  });

  for (const key of sortedKeys) {
    const expanded = expandedLocationCategories.has(key);

    const group = document.createElement("div");
    group.className = "category-group";

    const heading = document.createElement("div");
    heading.className = "category-heading";
    const caret = document.createElement("span");
    caret.className = "category-caret";
    caret.textContent = expanded ? "▾" : "▸";
    heading.appendChild(caret);
    heading.appendChild(document.createTextNode(" " + key));
    heading.onclick = () => {
      if (expandedLocationCategories.has(key)) expandedLocationCategories.delete(key);
      else expandedLocationCategories.add(key);
      refreshLocations();
    };
    group.appendChild(heading);

    const body = document.createElement("div");
    body.className = "category-body";
    body.style.display = expanded ? "" : "none";
    group.appendChild(body);

    for (const loc of grouped[key]) {
      const card = document.createElement("div");
      card.className = "entity-card";
      card.innerHTML = `
        <div>
          <strong>${loc.name}</strong>
          <div class="meta">${loc.reference_image || "no reference image"}</div>
        </div>
        <div>
          <button data-edit>Edit</button>
          <button data-delete class="danger">Delete</button>
        </div>`;
      card.querySelector("[data-edit]").onclick = () => {
        showLocationForm();
        const f = $("#location-form");
        f.id.value = loc.id; f.name.value = loc.name;
        f.reference_image.value = loc.reference_image;
        f.visual_description.value = loc.visual_description;
        f.soundscape_description.value = loc.soundscape_description;
        f.properties.value = JSON.stringify(loc.properties, null, 2);
        if (loc.category && categories.includes(loc.category)) {
          locationCategoryPicker.populate(categories, loc.category);
        } else if (loc.category) {
          locationCategoryPicker.populate(categories, "__new__");
          f.category_new.value = loc.category;
        } else {
          locationCategoryPicker.populate(categories, "");
        }
      };
      card.querySelector("[data-delete]").onclick = async () => {
        if (confirm(`Delete location "${loc.name}"?`)) {
          await api(`/locations/${loc.id}`, { method: "DELETE" });
          refreshLocations(); refreshSceneLocationSelect();
        }
      };
      body.appendChild(card);
    }

    container.appendChild(group);
  }
}

// ---------- collapsible "new location" form: hidden by default ----------
function showLocationForm() {
  $("#location-form").style.display = "flex";
  $("#add-location-btn").style.display = "none";
}
function hideLocationForm() {
  $("#location-form").style.display = "none";
  $("#add-location-btn").style.display = "";
  $("#location-form").reset();
  $("#location-form").id.value = "";
}
$("#add-location-btn").addEventListener("click", showLocationForm);
$("#cancel-location-btn").addEventListener("click", hideLocationForm);

$("#location-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  let properties;
  try { properties = parsePropertiesField(f); } catch { return; }
  const payload = {
    name: f.name.value, reference_image: f.reference_image.value,
    category: locationCategoryPicker.resolveValue(),
    visual_description: f.visual_description.value,
    soundscape_description: f.soundscape_description.value,
    properties,
  };
  if (f.id.value) await api(`/locations/${f.id.value}`, { method: "PUT", body: JSON.stringify(payload) });
  else await api("/locations", { method: "POST", body: JSON.stringify(payload) });
  hideLocationForm();
  refreshLocations(); refreshSceneLocationSelect();
});

// ---------- scenes: location select + character/attire casting rows ----------
async function refreshSceneLocationSelect() {
  const list = await api("/locations");
  const sel = $("#scene-location-select");
  sel.innerHTML = `<option value="">-- choose a location --</option>` +
    list.map(loc => `<option value="${loc.id}">${loc.name}</option>`).join("");
}

let stagedCastings = [];       // [{character_id, attire_id}] being built for the scene form
let allCharactersCache = [];   // refreshed whenever the casting picker updates

async function refreshCastingCharacterSelect() {
  allCharactersCache = await api("/characters");
  const select = $("#casting-character-select");
  const available = allCharactersCache.filter(c => !stagedCastings.some(sc => sc.character_id === c.id));
  select.innerHTML = available.length
    ? available.map(c => `<option value="${c.id}">${c.name}</option>`).join("")
    : `<option value="">— all characters added —</option>`;
  syncCastingAttireSelect();
  renderStagedCastings();
}

function syncCastingAttireSelect() {
  const charId = $("#casting-character-select").value;
  const attireSelect = $("#casting-attire-select");
  const character = allCharactersCache.find(c => c.id === charId);
  if (!character || !character.attire_options.length) {
    attireSelect.innerHTML = `<option value="">— no attire options —</option>`;
    attireSelect.disabled = true;
    return;
  }
  attireSelect.disabled = false;
  attireSelect.innerHTML = character.attire_options.map(a =>
    `<option value="${a.id}" ${a.is_default ? "selected" : ""}>${a.label || "(untitled)"}${a.is_default ? " (default)" : ""}</option>`
  ).join("");
}
$("#casting-character-select").addEventListener("change", syncCastingAttireSelect);

$("#add-casting-btn").addEventListener("click", () => {
  const charId = $("#casting-character-select").value;
  if (!charId) return;
  const attireId = $("#casting-attire-select").value || "";
  stagedCastings.push({ character_id: charId, attire_id: attireId });
  refreshCastingCharacterSelect();  // rebuilds the available list (now excluding this one) + re-renders staged list
});

$("#reset-castings-btn").addEventListener("click", () => {
  stagedCastings = [];
  refreshCastingCharacterSelect();
});

function renderStagedCastings() {
  const container = $("#staged-castings-list");
  if (!stagedCastings.length) {
    container.innerHTML = `<div class="meta">No characters added yet.</div>`;
    return;
  }
  container.innerHTML = stagedCastings.map((sc, i) => {
    const character = allCharactersCache.find(c => c.id === sc.character_id);
    const name = character ? character.name : "?";
    const attire = character?.attire_options.find(a => a.id === sc.attire_id)
      || character?.attire_options.find(a => a.is_default);
    const attireLabel = attire ? (attire.label || "attire") : "no attire";
    return `
      <div class="staged-casting-row" data-index="${i}">
        <span>${name} <span class="meta">(${attireLabel})</span></span>
        <button type="button" data-remove-casting="${i}">Remove</button>
      </div>`;
  }).join("");
  $$("[data-remove-casting]", container).forEach(btn => {
    btn.onclick = () => {
      stagedCastings.splice(parseInt(btn.dataset.removeCasting, 10), 1);
      refreshCastingCharacterSelect();
    };
  });
}

async function refreshScenes() {
  const list = await api("/scenes");
  const container = $("#scene-list");
  container.innerHTML = "";
  for (const s of list) {
    const card = document.createElement("div");
    card.className = "entity-card";
    card.innerHTML = `
      <div>
        <strong>${s.name}</strong>
        <div class="meta">${s.sequences.length} sequence(s)</div>
      </div>
      <div>
        <button data-open>Open</button>
        <button data-delete class="danger">Delete</button>
      </div>`;
    card.querySelector("[data-open]").onclick = () => openSceneEditorTab(s.id);
    card.querySelector("[data-delete]").onclick = async () => {
      if (confirm(`Delete scene "${s.name}"?`)) {
        await api(`/scenes/${s.id}`, { method: "DELETE" });
        if (openSceneEditor && openSceneEditor.sceneId === s.id) closeSceneEditor();
        refreshScenes();
      }
    };
    container.appendChild(card);
  }
}

let styleOptions = [];  // cached [{key, label}] from the server

async function loadStyleOptions() {
  if (styleOptions.length) return styleOptions;
  styleOptions = await api("/style-options");
  return styleOptions;
}

async function refreshSceneStyleSelect() {
  await loadStyleOptions();
  const sel = $("#scene-style-select");
  const opts = styleOptions.map(o => `<option value="${o.key}">${o.label}</option>`).join("");
  sel.innerHTML = `<option value="">— no style opening set —</option>${opts}<option value="custom">Custom…</option>`;
}

const SUMMARY_PREMISE_LIMIT = 128;

function updateSummaryPremiseCounter() {
  const len = $("#scene-summary-premise").value.length;
  const counter = $("#summary-premise-counter");
  counter.textContent = `${len} / ${SUMMARY_PREMISE_LIMIT}`;
  counter.classList.toggle("over-limit", len > SUMMARY_PREMISE_LIMIT);
}
$("#scene-summary-premise").addEventListener("input", updateSummaryPremiseCounter);

$("#scene-style-select").addEventListener("change", () => {
  $("#scene-style-custom").style.display = $("#scene-style-select").value === "custom" ? "block" : "none";
});

// ---------- collapsible "new scene" form: hidden by default ----------
function showSceneForm() {
  $("#scene-form").style.display = "flex";
  $("#add-scene-btn").style.display = "none";
}
function hideSceneForm() {
  $("#scene-form").style.display = "none";
  $("#add-scene-btn").style.display = "";
  $("#scene-form").reset();
  $("#scene-form").id.value = "";
  $("#scene-style-custom").style.display = "none";
  updateSummaryPremiseCounter();
  stagedCastings = [];
  refreshCastingCharacterSelect();
}
$("#add-scene-btn").addEventListener("click", showSceneForm);
$("#cancel-scene-btn").addEventListener("click", hideSceneForm);

$("#scene-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  const payload = {
    name: f.name.value, location_id: f.location_id.value,
    character_castings: stagedCastings,
    non_diegetic_music: f.non_diegetic_music.value,
    summary_premise: f.summary_premise.value,
    style_preset: f.style_preset.value,
  };
  if (f.style_preset.value === "custom") payload.style_text = f.style_text.value;
  if (f.id.value) await api(`/scenes/${f.id.value}`, { method: "PUT", body: JSON.stringify(payload) });
  else await api("/scenes", { method: "POST", body: JSON.stringify(payload) });
  hideSceneForm();
  refreshScenes();
});

// ---------- scene editor: dynamic tab, staged local edits, explicit save ----------
// Opening a scene creates a NEW tab (not a section within the Scenes tab).
// All sequence/beat add/edit/delete/reorder actions mutate a local, in-memory
// working copy only -- nothing hits the API until "Save Changes" is clicked.
// Navigating to any other tab discards the whole working copy silently.
let openSceneEditor = null;
// shape: { sceneId, tabId, btn, panel, localScene, savedSnapshot,
//          characters, locationLabel }
let deliveryOptions = [];  // cached [{key, label}] from the server

async function loadDeliveryOptions() {
  if (deliveryOptions.length) return deliveryOptions;
  deliveryOptions = await api("/delivery-options");
  return deliveryOptions;
}

function deliveryOptionsHtml(selectedKey = "") {
  const opts = deliveryOptions.map(o =>
    `<option value="${o.key}" ${o.key === selectedKey ? "selected" : ""}>${o.label}</option>`).join("");
  const noneSelected = selectedKey === "" ? "selected" : "";
  const customSelected = selectedKey === "custom" ? "selected" : "";
  return `<option value="" ${noneSelected}>— no delivery specified —</option>${opts}` +
         `<option value="custom" ${customSelected}>Custom…</option>`;
}

function deliveryPreviewText(beat) {
  // Local unsaved beats never have `delivery` resolved (that only happens
  // server-side on save) -- fall back to the preset's label, or the raw
  // custom text, so the beat list still shows something meaningful.
  if (beat.delivery) return beat.delivery;
  if (beat.delivery_preset === "custom") return beat.delivery_text || "";
  if (beat.delivery_preset) {
    const opt = deliveryOptions.find(o => o.key === beat.delivery_preset);
    return opt ? opt.label : "";
  }
  return "";
}

function beatSummary(beat, charById) {
  if (beat.kind === "action") return beat.text;
  const name = charById[beat.character_id]?.name || "?";
  const delivery = deliveryPreviewText(beat);
  const deliveryNote = delivery ? ` (${delivery})` : "";
  return `${name}${deliveryNote}: "${beat.line}"`;
}

function localId(prefix) {
  return `${prefix}_${Math.random().toString(16).slice(2, 10)}`;
}

// Builds a URL for the /api/media endpoint, which serves a local file by
// absolute path over HTTP -- browsers can't load arbitrary filesystem paths
// directly in <img src>, so reference images route through this instead.
function mediaUrl(path) {
  return path ? `/api/media?path=${encodeURIComponent(path)}` : "";
}

// Shows just the last 2 folders + filename of an output path (Windows or
// POSIX separators) -- full paths are long and not worth showing in full
// inline; the complete path is still available via the element's title
// tooltip on hover.
function shortenOutputPath(path) {
  if (!path) return "";
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 3 ? "…/" + parts.slice(-3).join("/") : path;
}

// ---------- timestamp widget: seconds + milliseconds, stored as "00:SS.mmm" ----------
// Sequences are only ever 5-10s, so minutes are never meaningfully needed --
// any legacy value with real minutes just gets folded into total seconds.
function parseTimestamp(ts) {
  const m = /^(?:(\d+):)?(\d+)(?:\.(\d+))?$/.exec((ts || "").trim());
  if (!m) return { sec: "", ms: "" };
  const minutes = m[1] ? parseInt(m[1], 10) : 0;
  const seconds = parseInt(m[2], 10);
  const ms = m[3] ? parseInt(m[3].padEnd(3, "0").slice(0, 3), 10) : 0;
  return { sec: minutes * 60 + seconds, ms };
}

function formatTimestamp(sec, ms) {
  if (sec === "" && ms === "") return "";  // no timestamp set at all
  const secNum = parseInt(sec, 10) || 0;
  const msNum = parseInt(ms, 10) || 0;
  return `00:${String(secNum).padStart(2, "0")}.${String(msNum).padStart(3, "0")}`;
}

// A beat starts a new shot unless is_new_shot is explicitly false, in which
// case it folds into the shot started by the beat before it. The sequence's
// first beat always starts Shot 1 regardless of its own flag -- mirrors
// engine/prompt_compiler.py's _group_beats_into_shots exactly, so what's
// shown here always matches what actually gets compiled.
function computeShotInfo(beats) {
  const info = {};
  let shotN = 0;
  beats.forEach((b, i) => {
    const startsNew = i === 0 || b.is_new_shot !== false;
    if (startsNew) shotN += 1;
    info[b.id] = { shotN, isStart: startsNew };
  });
  return info;
}

function beatFormFieldsHtml(charOptions, beat = null, isVeryFirstBeat = false) {
  const kind = beat?.kind || "action";
  const isDialogue = kind === "dialogue";
  const isNewShot = isVeryFirstBeat ? true : (beat ? beat.is_new_shot !== false : true);
  const { sec, ms } = parseTimestamp(beat?.timestamp);

  const newShotToggle = isVeryFirstBeat
    ? `<div class="meta">This is the sequence's first beat — it always starts Shot 1.</div>`
    : `<label class="new-shot-toggle">
         <input type="checkbox" name="beat_is_new_shot" ${isNewShot ? "checked" : ""}>
         Starts a new shot
       </label>`;
  const timestampField = isVeryFirstBeat ? "" : `
    <div class="timestamp-widget" style="display:${isNewShot ? "flex" : "none"}">
      <span class="timestamp-label">Timestamp into sequence</span>
      <input type="number" name="beat_timestamp_sec" min="0" step="1" placeholder="sec" value="${sec}">
      <span>.</span>
      <input type="number" name="beat_timestamp_ms" min="0" max="999" step="1" placeholder="ms" value="${ms}">
    </div>`;
  const customDeliveryValue = beat?.delivery_preset === "custom"
    ? (beat.delivery_text || beat.delivery || "") : "";
  return `
    <select name="beat_kind">
      <option value="action" ${!isDialogue ? "selected" : ""}>Descriptive text</option>
      <option value="dialogue" ${isDialogue ? "selected" : ""}>Dialogue</option>
    </select>
    <textarea name="beat_text" placeholder="What happens (character names get auto-tagged)"
      style="display:${isDialogue ? "none" : "block"}">${beat?.text || ""}</textarea>
    <select name="beat_character" style="display:${isDialogue ? "block" : "none"}">${charOptions}</select>
    <textarea name="beat_line" placeholder="What they say"
      style="display:${isDialogue ? "block" : "none"}">${beat?.line || ""}</textarea>
    <select name="beat_delivery" style="display:${isDialogue ? "block" : "none"}">${deliveryOptionsHtml(beat?.delivery_preset || "")}</select>
    <input type="text" name="beat_delivery_custom" placeholder="Describe the delivery (e.g. 'in a low, urgent whisper')"
      value="${customDeliveryValue}"
      style="display:${isDialogue && beat?.delivery_preset === "custom" ? "block" : "none"}">
    ${newShotToggle}
    ${timestampField}
  `;
}

function wireBeatFormBehavior(formEl) {
  const kindSelect = formEl.querySelector('[name="beat_kind"]');
  const textArea = formEl.querySelector('[name="beat_text"]');
  const charSelect = formEl.querySelector('[name="beat_character"]');
  const lineArea = formEl.querySelector('[name="beat_line"]');
  const deliverySelect = formEl.querySelector('[name="beat_delivery"]');
  const deliveryCustomInput = formEl.querySelector('[name="beat_delivery_custom"]');
  const newShotCheckbox = formEl.querySelector('[name="beat_is_new_shot"]');  // absent for the very first beat
  const timestampWidget = formEl.querySelector('.timestamp-widget');          // absent for the very first beat
  const timestampSecInput = formEl.querySelector('[name="beat_timestamp_sec"]');
  const timestampMsInput = formEl.querySelector('[name="beat_timestamp_ms"]');

  function sync() {
    const isDialogue = kindSelect.value === "dialogue";
    textArea.style.display = isDialogue ? "none" : "block";
    charSelect.style.display = isDialogue ? "block" : "none";
    lineArea.style.display = isDialogue ? "block" : "none";
    deliverySelect.style.display = isDialogue ? "block" : "none";
    deliveryCustomInput.style.display = (isDialogue && deliverySelect.value === "custom") ? "block" : "none";
    if (timestampWidget) {
      const isNewShot = newShotCheckbox ? newShotCheckbox.checked : true;
      timestampWidget.style.display = isNewShot ? "flex" : "none";
    }
  }
  kindSelect.addEventListener("change", sync);
  deliverySelect.addEventListener("change", sync);
  if (newShotCheckbox) newShotCheckbox.addEventListener("change", sync);

  return {
    kindSelect, textArea, charSelect, lineArea, deliverySelect, deliveryCustomInput,
    collectPayload() {
      const kind = kindSelect.value;
      const isNewShot = newShotCheckbox ? newShotCheckbox.checked : true;  // forced true for the first beat
      const payload = kind === "action"
        ? { kind: "action", text: textArea.value }
        : {
            kind: "dialogue", character_id: charSelect.value, line: lineArea.value,
            delivery_preset: deliverySelect.value,
          };
      if (kind === "dialogue" && deliverySelect.value === "custom") payload.delivery_text = deliveryCustomInput.value;
      payload.is_new_shot = isNewShot;
      // A beat folding into the previous shot has no [Shot N] header of its
      // own to attach a timestamp to -- keep it blank, matching the
      // server-side rule in app.py's _normalize_shot_fields.
      payload.timestamp = (timestampSecInput && isNewShot)
        ? formatTimestamp(timestampSecInput.value, timestampMsInput.value) : "";
      return payload;
    },
  };
}

// ---------- local (unsaved) mutations ----------
function isSceneEditorDirty() {
  if (!openSceneEditor) return false;
  return JSON.stringify(openSceneEditor.localScene.sequences) !==
         JSON.stringify(openSceneEditor.savedSnapshot.sequences);
}

function addLocalSequence(duration) {
  openSceneEditor.localScene.sequences.push({
    id: localId("seq"), index: openSceneEditor.localScene.sequences.length,
    duration, beats: [], status: "pending", output_video_path: "",
  });
  renderSceneEditorPanel();
}

function deleteLocalSequence(seqId) {
  const seqs = openSceneEditor.localScene.sequences
    .filter(s => s.id !== seqId)
    .sort((a, b) => a.index - b.index);
  seqs.forEach((s, i) => { s.index = i; });
  openSceneEditor.localScene.sequences = seqs;
  openSceneEditor.openBeatForms.delete(seqId);
  renderSceneEditorPanel();
}

function addLocalBeat(seqId, payload) {
  const seq = openSceneEditor.localScene.sequences.find(s => s.id === seqId);
  const isFirstBeat = seq.beats.length === 0;
  seq.beats.push({
    id: localId("beat"),
    kind: payload.kind,
    text: payload.text || "",
    character_id: payload.character_id || "",
    line: payload.line || "",
    language: payload.language || "English",
    delivery_preset: payload.delivery_preset || "",
    delivery: "",  // resolved server-side on save
    delivery_text: payload.delivery_text || "",
    is_new_shot: isFirstBeat ? true : (payload.is_new_shot !== false),
    timestamp: payload.timestamp || "",
  });
  renderSceneEditorPanel();
}

function editLocalBeat(seqId, beatId, payload) {
  const seq = openSceneEditor.localScene.sequences.find(s => s.id === seqId);
  const beat = seq.beats.find(b => b.id === beatId);
  const isFirstBeat = seq.beats[0] === beat;
  beat.kind = payload.kind;
  beat.text = payload.text || "";
  beat.character_id = payload.character_id || "";
  beat.line = payload.line || "";
  beat.delivery_preset = payload.delivery_preset || "";
  beat.delivery = "";  // re-resolved server-side on next save
  beat.delivery_text = payload.delivery_text || "";
  beat.is_new_shot = isFirstBeat ? true : (payload.is_new_shot !== false);
  beat.timestamp = payload.timestamp || "";
  renderSceneEditorPanel();
}

function deleteLocalBeat(seqId, beatId) {
  const seq = openSceneEditor.localScene.sequences.find(s => s.id === seqId);
  seq.beats = seq.beats.filter(b => b.id !== beatId);
  renderSceneEditorPanel();
}

function reorderLocalBeats(seqId, beatIds) {
  const seq = openSceneEditor.localScene.sequences.find(s => s.id === seqId);
  const byId = Object.fromEntries(seq.beats.map(b => [b.id, b]));
  seq.beats = beatIds.map(id => byId[id]);
  renderSceneEditorPanel();
}

// ---------- opening / closing the tab ----------
async function openSceneEditorTab(sceneId) {
  closeSceneEditor();  // discard anything previously open first, same-scene or not

  await loadDeliveryOptions();
  const scene = await api(`/scenes/${sceneId}`);
  const allChars = await api("/characters");
  const allLocations = await api("/locations");
  const castedIds = new Set(scene.character_castings.map(c => c.character_id));
  const characters = allChars.filter(c => castedIds.has(c.id));
  const location = allLocations.find(loc => loc.id === scene.location_id);

  const attireById = {};
  for (const c of allChars) for (const a of c.attire_options) attireById[a.id] = a;
  const locationLabel = location ? location.name : "— none chosen —";
  const charLabels = scene.character_castings.length
    ? scene.character_castings.map(casting => {
        const c = allChars.find(ch => ch.id === casting.character_id);
        if (!c) return "?";
        const attire = casting.attire_id ? attireById[casting.attire_id] : c.attire_options.find(a => a.is_default);
        const attireLabel = attire ? ` (${attire.label || "attire"})` : "";
        return `${c.name}${attireLabel}`;
      }).join(", ")
    : "— none —";

  const tabId = `scene-${scene.id}`;
  const btn = document.createElement("button");
  btn.className = "tab";
  btn.dataset.tab = tabId;
  btn.textContent = scene.name;
  btn.addEventListener("click", () => activateTab(tabId));
  $("nav").appendChild(btn);

  const panel = document.createElement("section");
  panel.id = `tab-${tabId}`;
  panel.className = "tab-panel";
  $("main").appendChild(panel);

  openSceneEditor = {
    sceneId: scene.id, tabId, btn, panel,
    localScene: JSON.parse(JSON.stringify(scene)),
    savedSnapshot: JSON.parse(JSON.stringify(scene)),
    characters, locationLabel, charLabels,
    runStatus: null, runPollTimer: null,
    openBeatForms: new Set(),  // seq ids whose "add beat" form is currently expanded
    promptFormat: "lean",  // live choice only -- not saved anywhere, sent fresh with
                            // each Generate/Run request; see index.html item 4 in the
                            // punch list for why this isn't a Scene/Sequence field
  };

  await refreshRunStatus();
  renderSceneEditorPanel();
  activateTab(tabId);
}

function closeSceneEditor() {
  if (!openSceneEditor) return;
  if (openSceneEditor.runPollTimer) clearInterval(openSceneEditor.runPollTimer);
  openSceneEditor.btn.remove();
  openSceneEditor.panel.remove();
  openSceneEditor = null;
}

// ---------- scene run: submit every un-rendered sequence to ComfyUI in
// order, chaining each sequence's real output into the next one. Runs as a
// background job on the server; this just polls its status. ----------
async function refreshRunStatus() {
  const ed = openSceneEditor;
  if (!ed) return;
  ed.runStatus = await api(`/scenes/${ed.sceneId}/run/status`);
  if (ed.runStatus.state === "running") {
    startRunPolling();
  } else if (ed.runPollTimer) {
    clearInterval(ed.runPollTimer);
    ed.runPollTimer = null;
  }
}

function startRunPolling() {
  const ed = openSceneEditor;
  if (!ed || ed.runPollTimer) return;
  ed.runPollTimer = setInterval(async () => {
    if (!openSceneEditor || openSceneEditor.sceneId !== ed.sceneId) {
      clearInterval(ed.runPollTimer);
      return;
    }
    await refreshRunStatus();
    if (ed.runStatus.state !== "running") {
      // run just finished (or errored/cancelled) -- pull the fresh scene
      // so sequence status/output paths reflect what the server recorded
      const fresh = await api(`/scenes/${ed.sceneId}`);
      ed.localScene = JSON.parse(JSON.stringify(fresh));
      ed.savedSnapshot = JSON.parse(JSON.stringify(fresh));
    }
    renderSceneEditorPanel();
  }, 2500);
}

// ---------- rendering the panel from local state ----------
function runSummaryText(run) {
  if (run.state === "none") return "Not started — Run Scene submits every un-rendered sequence to ComfyUI in order.";
  if (run.state === "running") {
    const active = (run.sequences || []).find(s => s.state && !["pending", "rendered"].includes(s.state));
    return active ? `Running — sequence #${active.index} (${active.state})…` : "Running…";
  }
  if (run.state === "done") return "Run complete — every sequence rendered.";
  if (run.state === "error") return `Run stopped: ${run.error || "unknown error"}`;
  if (run.state === "cancelled") return "Run cancelled.";
  return "";
}

function renderSceneEditorPanel() {
  const ed = openSceneEditor;
  if (!ed) return;
  const scene = ed.localScene;
  const dirty = isSceneEditorDirty();
  const charById = Object.fromEntries(ed.characters.map(c => [c.id, c]));
  const charOptions = ed.characters.map(c => `<option value="${c.id}">${c.name}</option>`).join("");
  const sorted = [...scene.sequences].sort((a, b) => a.index - b.index);

  const run = ed.runStatus || { state: "none" };
  const running = run.state === "running";
  const runSeqById = Object.fromEntries((run.sequences || []).map(s => [s.id, s]));
  // Only trust the run-status overlay while a run is actually in progress --
  // once it finishes (or was never started), it's a stale snapshot from
  // whenever it was last fetched and shouldn't override the sequence's
  // actual current status (e.g. after a manual status reset + save).
  const badgeFor = seq => {
    if (!running) return seq.status;
    const entry = runSeqById[seq.id];
    return (entry && entry.state && entry.state !== "pending") ? entry.state : seq.status;
  };

  const sequencesHtml = sorted.map(seq => {
    const shotInfo = computeShotInfo(seq.beats);

    const beatsHtml = seq.beats.map((b, i) => {
      const info = shotInfo[b.id];
      const shotLabel = info.isStart ? `Shot ${info.shotN}` : `Shot ${info.shotN} (cont.)`;
      const tsLabel = info.isStart && info.shotN > 1 && b.timestamp ? ` · ${b.timestamp}` : "";
      return `
      <div class="beat-row" data-beat-id="${b.id}">
        <span class="beat-shot">${shotLabel}${tsLabel}</span>
        <span class="beat-kind">${b.kind === "action" ? "action" : "dialogue"}</span>
        <span class="beat-summary">${beatSummary(b, charById)}</span>
        <button data-move-up ${i === 0 ? "disabled" : ""} title="Move up">↑</button>
        <button data-move-down ${i === seq.beats.length - 1 ? "disabled" : ""} title="Move down">↓</button>
        <button data-edit-beat title="Edit">✎</button>
        <button data-remove-beat class="danger" title="Delete">×</button>
      </div>`;
    }).join("") || `<div class="meta">No beats yet.</div>`;

    const badge = badgeFor(seq);
    const runEntry = running ? runSeqById[seq.id] : null;
    const outputPath = (runEntry && runEntry.output_video_path) || seq.output_video_path;

    const beatFormOpen = ed.openBeatForms.has(seq.id);
    const addBeatHtml = beatFormOpen
      ? `<form class="beat-form" data-seq-id="${seq.id}">
           ${beatFormFieldsHtml(charOptions, null, seq.beats.length === 0)}
           <div class="beat-form-actions">
             <button type="submit" ${running ? "disabled" : ""}>Add Beat${seq.beats.length ? "" : " (Shot 1)"}</button>
             <button type="button" data-cancel-add-beat>Cancel</button>
           </div>
         </form>`
      : `<button type="button" class="add-beat-btn" data-open-add-beat ${running ? "disabled" : ""}>+ Add Beat</button>`;

    return `
      <div class="seq-card" data-seq-id="${seq.id}">
        <div><strong>#${seq.index}</strong> — ${seq.duration}s
          <span class="status ${badge}">${badge}</span></div>
        <div class="beats">${beatsHtml}</div>

        ${addBeatHtml}

        <div class="seq-actions">
          <button data-generate ${running ? "disabled" : ""}>Generate workflow JSON</button>
          <button data-resolve ${running ? "disabled" : ""}>Mark rendered / set output path</button>
          <button data-delete-sequence class="danger" ${running ? "disabled" : ""}>Delete sequence</button>
        </div>
        <div class="meta output-path" data-result title="${outputPath || ""}">${outputPath ? `Output: ${shortenOutputPath(outputPath)}` : ""}</div>
      </div>`;
  }).join("");

  ed.panel.innerHTML = `
    <h2>${scene.name}</h2>
    <div class="scene-info">
      <div><span class="info-label">Location</span> ${ed.locationLabel}</div>
      <div><span class="info-label">Characters</span> ${ed.charLabels}</div>
      <div><span class="info-label">Premise</span> ${scene.summary_premise || "— none set —"}</div>
      <div><span class="info-label">Visual style</span> ${scene.style_opening || "— none set —"}</div>
    </div>

    <div class="save-bar prompt-format-bar">
      <span class="save-bar-status">Prompt format used for Generate / Run Scene:</span>
      <select id="scene-editor-prompt-format">
        <option value="lean" ${ed.promptFormat === "lean" ? "selected" : ""}>Lean (base-guide style)</option>
        <option value="full" ${ed.promptFormat === "full" ? "selected" : ""}>Full (six-section rewrite-guide style)</option>
      </select>
    </div>

    <div class="save-bar ${dirty ? "dirty" : ""}">
      <span class="save-bar-status">${running
        ? "A render run is in progress — saving is disabled until it finishes."
        : (dirty ? "Unsaved changes — switching tabs will discard them." : "All changes saved.")}</span>
      <button data-save-changes ${dirty && !running ? "" : "disabled"}>Save Changes</button>
      <button data-discard-changes ${dirty && !running ? "" : "disabled"}>Discard Changes</button>
    </div>

    <div class="save-bar run-bar ${running ? "dirty" : ""} ${run.state === "error" ? "run-error" : ""}">
      <span class="save-bar-status">${runSummaryText(run)}</span>
      ${running
        ? `<button data-cancel-run>Cancel Run</button>`
        : `<button data-run-scene ${dirty || !sorted.length ? "disabled" : ""}
             title="${dirty ? "Save changes first" : ""}">Run Scene</button>`}
    </div>

    <div class="save-bar reset-status-bar">
      <span class="save-bar-status">Clears every sequence's generated/rendered status and forgets their output paths, so they can be regenerated from scratch. Stays staged until you Save Changes.</span>
      <button data-reset-sequence-status ${running || !sorted.some(s => s.status !== "pending" || s.output_video_path) ? "disabled" : ""}>Reset All Sequence Status</button>
    </div>

    <form id="sequence-form" class="sequence-add-form">
      <label class="field-label" for="new-sequence-duration">Duration (sec)</label>
      <input type="number" id="new-sequence-duration" name="duration" min="5" max="10" step="0.5" value="8">
      <button type="submit" ${running ? "disabled" : ""}>Add Sequence</button>
    </form>
    <div id="sequence-list">${sequencesHtml}</div>
  `;

  ed.panel.querySelector("[data-reset-sequence-status]")?.addEventListener("click", () => {
    if (!confirm(
      "Reset generated/rendered status for every sequence in this scene? " +
      "Each sequence goes back to \"pending\" and forgets its recorded output path. " +
      "This only affects Scene Forge's bookkeeping -- it doesn't delete any files already written to disk."
    )) return;
    ed.localScene.sequences.forEach(s => {
      s.status = "pending";
      s.output_video_path = "";
    });
    renderSceneEditorPanel();
  });

  ed.panel.querySelector("#scene-editor-prompt-format").addEventListener("change", e => {
    ed.promptFormat = e.target.value;  // live only, no re-render needed
  });

  // save bar
  ed.panel.querySelector("[data-save-changes]").onclick = async () => {
    try {
      const saved = await api(`/scenes/${ed.sceneId}/sequences_bulk`, {
        method: "PUT", body: JSON.stringify({ sequences: ed.localScene.sequences }),
      });
      ed.localScene = JSON.parse(JSON.stringify(saved));
      ed.savedSnapshot = JSON.parse(JSON.stringify(saved));
      ed.btn.textContent = saved.name;
      renderSceneEditorPanel();
    } catch (err) { alert(err.message); }
  };
  ed.panel.querySelector("[data-discard-changes]").onclick = () => {
    if (!confirm("Discard all unsaved changes to this scene's sequences?")) return;
    ed.localScene = JSON.parse(JSON.stringify(ed.savedSnapshot));
    renderSceneEditorPanel();
  };

  // run bar
  const runBtn = ed.panel.querySelector("[data-run-scene]");
  if (runBtn) {
    runBtn.onclick = async () => {
      try {
        await api(`/scenes/${ed.sceneId}/run`, {
          method: "POST", body: JSON.stringify({ prompt_format: ed.promptFormat }),
        });
        await refreshRunStatus();
        renderSceneEditorPanel();
      } catch (err) { alert(err.message); }
    };
  }
  const cancelRunBtn = ed.panel.querySelector("[data-cancel-run]");
  if (cancelRunBtn) {
    cancelRunBtn.onclick = async () => {
      try {
        await api(`/scenes/${ed.sceneId}/run/cancel`, { method: "POST" });
        await refreshRunStatus();
        renderSceneEditorPanel();
      } catch (err) { alert(err.message); }
    };
  }

  // add-sequence form
  ed.panel.querySelector("#sequence-form").addEventListener("submit", e => {
    e.preventDefault();
    const f = e.target;
    addLocalSequence(parseFloat(f.duration.value));
  });

  // per-sequence cards
  for (const seq of sorted) {
    const card = ed.panel.querySelector(`.seq-card[data-seq-id="${seq.id}"]`);
    const beatFormOpen = ed.openBeatForms.has(seq.id);

    if (beatFormOpen) {
      const addForm = card.querySelector(".beat-form");
      const addCtl = wireBeatFormBehavior(addForm);
      addForm.addEventListener("submit", e => {
        e.preventDefault();
        ed.openBeatForms.delete(seq.id);  // recollapse back to the "+ Add Beat" button
        addLocalBeat(seq.id, addCtl.collectPayload());
      });
      addForm.querySelector("[data-cancel-add-beat]").onclick = () => {
        ed.openBeatForms.delete(seq.id);
        renderSceneEditorPanel();
      };
    } else {
      const openBtn = card.querySelector("[data-open-add-beat]");
      if (openBtn) {
        openBtn.onclick = () => {
          ed.openBeatForms.add(seq.id);
          renderSceneEditorPanel();
        };
      }
    }

    seq.beats.forEach((b, i) => {
      const row = card.querySelector(`.beat-row[data-beat-id="${b.id}"]`);
      if (!row) return;

      row.querySelector("[data-remove-beat]").onclick = () => deleteLocalBeat(seq.id, b.id);

      const moveUpBtn = row.querySelector("[data-move-up]");
      const moveDownBtn = row.querySelector("[data-move-down]");
      const moveBeat = direction => {
        const ids = seq.beats.map(x => x.id);
        const idx = ids.indexOf(b.id);
        const swapWith = direction === "up" ? idx - 1 : idx + 1;
        if (swapWith < 0 || swapWith >= ids.length) return;
        [ids[idx], ids[swapWith]] = [ids[swapWith], ids[idx]];
        reorderLocalBeats(seq.id, ids);
      };
      if (moveUpBtn) moveUpBtn.onclick = () => moveBeat("up");
      if (moveDownBtn) moveDownBtn.onclick = () => moveBeat("down");

      row.querySelector("[data-edit-beat]").onclick = () => {
        if (row.querySelector(".beat-edit-form")) return;  // already editing
        const editWrap = document.createElement("div");
        editWrap.className = "beat-edit-form beat-form";
        editWrap.innerHTML = `${beatFormFieldsHtml(charOptions, b, i === 0)}
          <div class="beat-edit-actions">
            <button type="button" data-save-beat>Save</button>
            <button type="button" data-cancel-beat>Cancel</button>
          </div>`;
        row.after(editWrap);
        row.style.display = "none";

        const ctl = wireBeatFormBehavior(editWrap);
        if (b.kind === "dialogue") ctl.charSelect.value = b.character_id;

        editWrap.querySelector("[data-save-beat]").onclick = () => {
          editLocalBeat(seq.id, b.id, ctl.collectPayload());
        };
        editWrap.querySelector("[data-cancel-beat]").onclick = () => {
          editWrap.remove();
          row.style.display = "";
        };
      };
    });

    card.querySelector("[data-generate]").onclick = async () => {
      if (isSceneEditorDirty()) {
        alert("You have unsaved changes. Save Changes before generating a workflow.");
        return;
      }
      try {
        const result = await api(`/scenes/${ed.sceneId}/sequences/${seq.id}/generate`, {
          method: "POST", body: JSON.stringify({ prompt_format: ed.promptFormat }),
        });
        card.querySelector("[data-result]").textContent = `Written to: ${result.workflow_path}`;
        const fresh = await api(`/scenes/${ed.sceneId}`);
        ed.localScene = JSON.parse(JSON.stringify(fresh));
        ed.savedSnapshot = JSON.parse(JSON.stringify(fresh));
        renderSceneEditorPanel();
      } catch (err) { alert(err.message); }
    };
    card.querySelector("[data-resolve]").onclick = async () => {
      if (isSceneEditorDirty()) {
        alert("You have unsaved changes. Save Changes before marking a sequence as rendered.");
        return;
      }
      const path = prompt("Absolute path to the rendered output video for this sequence:", seq.output_video_path || "");
      if (path === null) return;
      await api(`/scenes/${ed.sceneId}/sequences/${seq.id}/resolve_output`, {
        method: "POST", body: JSON.stringify({ output_video_path: path }),
      });
      const fresh = await api(`/scenes/${ed.sceneId}`);
      ed.localScene = JSON.parse(JSON.stringify(fresh));
      ed.savedSnapshot = JSON.parse(JSON.stringify(fresh));
      renderSceneEditorPanel();
    };
    card.querySelector("[data-delete-sequence]").onclick = () => {
      const laterCount = scene.sequences.filter(s => s.index > seq.index).length;
      const warning = laterCount > 0
        ? ` This scene has ${laterCount} sequence(s) after it that chain from earlier sequences by ` +
          `order -- deleting this one will renumber them, and any already generated/rendered may need ` +
          `to be regenerated to chain correctly.`
        : "";
      if (!confirm(`Delete Sequence #${seq.index}? This will apply once you Save.${warning}`)) return;
      deleteLocalSequence(seq.id);
    };
  }
}

// ---------- config ----------
async function refreshConfig() {
  const cfg = await api("/config");
  const f = $("#config-form");
  f.comfyui_url.value = cfg.comfyui_url || "";
  f.comfyui_output_dir.value = cfg.comfyui_output_dir || "";
  f.comfyui_input_dir.value = cfg.comfyui_input_dir || "";
  f.poll_interval.value = cfg.poll_interval ?? "";
  f.timeout_seconds.value = cfg.timeout_seconds ?? "";
}

$("#config-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  const statusEl = $("#config-status");
  const payload = {
    comfyui_url: f.comfyui_url.value,
    comfyui_output_dir: f.comfyui_output_dir.value,
    comfyui_input_dir: f.comfyui_input_dir.value,
    poll_interval: parseFloat(f.poll_interval.value),
    timeout_seconds: parseFloat(f.timeout_seconds.value),
  };
  try {
    await api("/config", { method: "PUT", body: JSON.stringify(payload) });
    statusEl.textContent = "Saved.";
    statusEl.classList.remove("error-text");
  } catch (err) {
    statusEl.textContent = err.message;
    statusEl.classList.add("error-text");
  }
});

// ---------- init ----------
refreshCharacters();
refreshLocations();
refreshScenes();
refreshSceneLocationSelect();
refreshSceneStyleSelect();
refreshCastingCharacterSelect();
refreshConfig();
