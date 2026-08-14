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
$$(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    $$(".tab").forEach(b => b.classList.remove("active"));
    $$(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
  });
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
      <input type="text" class="attire-image" placeholder="Absolute path to attire reference image" value="${attire.image_path || ""}">
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

// ---------- characters ----------
async function refreshCharacters() {
  const list = await api("/characters");
  const container = $("#character-list");
  container.innerHTML = "";
  for (const c of list) {
    const card = document.createElement("div");
    card.className = "entity-card";
    const attireNote = c.attire_options.length
      ? `${c.attire_options.length} attire option(s)`
      : "no attire options";
    card.innerHTML = `
      <div>
        <strong>${c.name}</strong>
        <div class="meta">${c.face_image || "no face image"} · ${c.voice_audio || "no voice"} · ${attireNote}</div>
      </div>
      <div>
        <button data-edit>Edit</button>
        <button data-delete class="danger">Delete</button>
      </div>`;
    card.querySelector("[data-edit]").onclick = () => {
      const f = $("#character-form");
      f.id.value = c.id; f.name.value = c.name;
      f.face_image.value = c.face_image;
      f.voice_audio.value = c.voice_audio;
      f.appearance_description.value = c.appearance_description;
      f.properties.value = JSON.stringify(c.properties, null, 2);
      clearAttireRows();
      c.attire_options.forEach(a => addAttireRow(a));
    };
    card.querySelector("[data-delete]").onclick = async () => {
      if (confirm(`Delete character "${c.name}"?`)) {
        await api(`/characters/${c.id}`, { method: "DELETE" });
        refreshCharacters(); refreshCastingCharacterSelect();
      }
    };
    container.appendChild(card);
  }
}

$("#character-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  let properties;
  try { properties = parsePropertiesField(f); } catch { return; }
  const payload = {
    name: f.name.value, face_image: f.face_image.value,
    voice_audio: f.voice_audio.value,
    appearance_description: f.appearance_description.value,
    attire_options: collectAttireOptions(),
    properties,
  };
  if (f.id.value) await api(`/characters/${f.id.value}`, { method: "PUT", body: JSON.stringify(payload) });
  else await api("/characters", { method: "POST", body: JSON.stringify(payload) });
  f.reset(); f.id.value = ""; clearAttireRows();
  refreshCharacters(); refreshCastingCharacterSelect();
});

// ---------- settings ----------
async function refreshSettings() {
  const list = await api("/settings");
  const container = $("#setting-list");
  container.innerHTML = "";
  for (const s of list) {
    const card = document.createElement("div");
    card.className = "entity-card";
    card.innerHTML = `
      <div>
        <strong>${s.name}</strong>
        <div class="meta">${s.reference_image || "no reference image"}</div>
      </div>
      <div>
        <button data-edit>Edit</button>
        <button data-delete class="danger">Delete</button>
      </div>`;
    card.querySelector("[data-edit]").onclick = () => {
      const f = $("#setting-form");
      f.id.value = s.id; f.name.value = s.name;
      f.reference_image.value = s.reference_image; f.ambient_audio.value = s.ambient_audio;
      f.visual_description.value = s.visual_description;
      f.soundscape_description.value = s.soundscape_description;
      f.properties.value = JSON.stringify(s.properties, null, 2);
    };
    card.querySelector("[data-delete]").onclick = async () => {
      if (confirm(`Delete setting "${s.name}"?`)) {
        await api(`/settings/${s.id}`, { method: "DELETE" });
        refreshSettings(); refreshSceneSettingSelect();
      }
    };
    container.appendChild(card);
  }
}

$("#setting-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  let properties;
  try { properties = parsePropertiesField(f); } catch { return; }
  const payload = {
    name: f.name.value, reference_image: f.reference_image.value,
    ambient_audio: f.ambient_audio.value,
    visual_description: f.visual_description.value,
    soundscape_description: f.soundscape_description.value,
    properties,
  };
  if (f.id.value) await api(`/settings/${f.id.value}`, { method: "PUT", body: JSON.stringify(payload) });
  else await api("/settings", { method: "POST", body: JSON.stringify(payload) });
  f.reset(); f.id.value = "";
  refreshSettings(); refreshSceneSettingSelect();
});

// ---------- scenes: setting select + character/attire casting rows ----------
async function refreshSceneSettingSelect() {
  const list = await api("/settings");
  const sel = $("#scene-setting-select");
  sel.innerHTML = `<option value="">-- choose a setting --</option>` +
    list.map(s => `<option value="${s.id}">${s.name}</option>`).join("");
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
    card.querySelector("[data-open]").onclick = () => openSceneDetail(s.id);
    card.querySelector("[data-delete]").onclick = async () => {
      if (confirm(`Delete scene "${s.name}"?`)) {
        await api(`/scenes/${s.id}`, { method: "DELETE" });
        if (s.id === currentSceneId) {
          currentSceneId = null;
          currentSceneCharacters = [];
          $("#scene-detail").style.display = "none";
        }
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

const SUMMARY_PREMISE_LIMIT = 250;

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

$("#scene-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  const payload = {
    name: f.name.value, setting_id: f.setting_id.value,
    character_castings: stagedCastings,
    non_diegetic_music: f.non_diegetic_music.value,
    summary_premise: f.summary_premise.value,
    style_preset: f.style_preset.value,
  };
  if (f.style_preset.value === "custom") payload.style_text = f.style_text.value;
  if (f.id.value) await api(`/scenes/${f.id.value}`, { method: "PUT", body: JSON.stringify(payload) });
  else await api("/scenes", { method: "POST", body: JSON.stringify(payload) });
  f.reset(); f.id.value = "";
  $("#scene-style-custom").style.display = "none";
  updateSummaryPremiseCounter();
  stagedCastings = [];
  await refreshCastingCharacterSelect();
  refreshScenes();
});

// ---------- scene detail: sequences + beats ----------
let currentSceneId = null;
let currentSceneCharacters = [];  // cached {id, name, attire_options} list for the open scene
let deliveryOptions = [];         // cached [{key, label}] from the server

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

async function openSceneDetail(sceneId) {
  currentSceneId = sceneId;
  await loadDeliveryOptions();
  const scene = await api(`/scenes/${sceneId}`);
  const allChars = await api("/characters");
  const allSettings = await api("/settings");
  const castedIds = new Set(scene.character_castings.map(c => c.character_id));
  currentSceneCharacters = allChars.filter(c => castedIds.has(c.id));
  const setting = allSettings.find(s => s.id === scene.setting_id);

  $("#scene-detail").style.display = "block";
  $("#scene-detail-title").textContent = `Sequences — ${scene.name}`;

  const attireById = {};
  for (const c of allChars) for (const a of c.attire_options) attireById[a.id] = a;
  const settingLabel = setting ? setting.name : "— none chosen —";
  const charLabels = scene.character_castings.length
    ? scene.character_castings.map(casting => {
        const c = allChars.find(ch => ch.id === casting.character_id);
        if (!c) return "?";
        const attire = casting.attire_id ? attireById[casting.attire_id] : c.attire_options.find(a => a.is_default);
        const attireLabel = attire ? ` (${attire.label || "attire"})` : "";
        return `${c.name}${attireLabel}`;
      }).join(", ")
    : "— none —";
  $("#scene-detail-info").innerHTML = `
    <div><span class="info-label">Setting</span> ${settingLabel}</div>
    <div><span class="info-label">Characters</span> ${charLabels}</div>
    <div><span class="info-label">Premise</span> ${scene.summary_premise || "— none set —"}</div>
    <div><span class="info-label">Visual style</span> ${scene.style_opening || "— none set —"}</div>
  `;

  renderSequences(scene);
}

function beatSummary(beat, charById) {
  const base = beat.kind === "action"
    ? beat.text
    : (() => {
        const name = charById[beat.character_id]?.name || "?";
        const deliveryNote = beat.delivery ? ` (${beat.delivery})` : "";
        return `${name}${deliveryNote}: "${beat.line}"`;
      })();
  return base;
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

function beatFormFieldsHtml(charOptions, beat = null, isFirstShot = false) {
  const kind = beat?.kind || "action";
  const isDialogue = kind === "dialogue";
  const { sec, ms } = parseTimestamp(beat?.timestamp);
  const timestampField = isFirstShot ? "" : `
    <div class="timestamp-widget">
      <span class="timestamp-label">Timestamp into sequence</span>
      <input type="number" name="beat_timestamp_sec" min="0" step="1" placeholder="sec" value="${sec}">
      <span>.</span>
      <input type="number" name="beat_timestamp_ms" min="0" max="999" step="1" placeholder="ms" value="${ms}">
    </div>`;
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
      value="${beat?.delivery_preset === "custom" ? (beat.delivery || "") : ""}"
      style="display:${isDialogue && beat?.delivery_preset === "custom" ? "block" : "none"}">
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
  const timestampSecInput = formEl.querySelector('[name="beat_timestamp_sec"]');  // absent for shot 1
  const timestampMsInput = formEl.querySelector('[name="beat_timestamp_ms"]');

  function sync() {
    const isDialogue = kindSelect.value === "dialogue";
    textArea.style.display = isDialogue ? "none" : "block";
    charSelect.style.display = isDialogue ? "block" : "none";
    lineArea.style.display = isDialogue ? "block" : "none";
    deliverySelect.style.display = isDialogue ? "block" : "none";
    deliveryCustomInput.style.display = (isDialogue && deliverySelect.value === "custom") ? "block" : "none";
  }
  kindSelect.addEventListener("change", sync);
  deliverySelect.addEventListener("change", sync);

  return {
    kindSelect, textArea, charSelect, lineArea, deliverySelect, deliveryCustomInput,
    collectPayload() {
      const kind = kindSelect.value;
      const payload = kind === "action"
        ? { kind: "action", text: textArea.value }
        : {
            kind: "dialogue", character_id: charSelect.value, line: lineArea.value,
            delivery_preset: deliverySelect.value,
          };
      if (kind === "dialogue" && deliverySelect.value === "custom") payload.delivery_text = deliveryCustomInput.value;
      if (timestampSecInput) payload.timestamp = formatTimestamp(timestampSecInput.value, timestampMsInput.value);
      return payload;
    },
  };
}

function renderSequences(scene) {
  const container = $("#sequence-list");
  container.innerHTML = "";
  const charById = Object.fromEntries(currentSceneCharacters.map(c => [c.id, c]));
  const charOptions = currentSceneCharacters.map(c => `<option value="${c.id}">${c.name}</option>`).join("");
  const sorted = [...scene.sequences].sort((a, b) => a.index - b.index);

  for (const seq of sorted) {
    const card = document.createElement("div");
    card.className = "seq-card";

    const beatsHtml = seq.beats.map((b, i) => `
      <div class="beat-row" data-beat-id="${b.id}">
        <span class="beat-shot">Shot ${i + 1}${i > 0 && b.timestamp ? ` · ${b.timestamp}` : ""}</span>
        <span class="beat-kind">${b.kind === "action" ? "action" : "dialogue"}</span>
        <span class="beat-summary">${beatSummary(b, charById)}</span>
        <button data-move-up ${i === 0 ? "disabled" : ""} title="Move up">↑</button>
        <button data-move-down ${i === seq.beats.length - 1 ? "disabled" : ""} title="Move down">↓</button>
        <button data-edit-beat title="Edit">✎</button>
        <button data-remove-beat class="danger" title="Delete">×</button>
      </div>`).join("") || `<div class="meta">No beats yet.</div>`;

    card.innerHTML = `
      <div><strong>#${seq.index}</strong> — ${seq.duration}s
        <span class="status ${seq.status}">${seq.status}</span></div>
      <div class="beats">${beatsHtml}</div>

      <form class="beat-form">
        ${beatFormFieldsHtml(charOptions, null, seq.beats.length === 0)}
        <button type="submit">Add Beat (Shot ${seq.beats.length + 1})</button>
      </form>

      <div class="seq-actions">
        <button data-generate>Generate workflow JSON</button>
        <button data-resolve>Mark rendered / set output path</button>
        <button data-delete-sequence class="danger">Delete sequence</button>
      </div>
      <div class="meta" data-result></div>
    `;

    const addForm = card.querySelector(".beat-form");
    const addCtl = wireBeatFormBehavior(addForm);
    addForm.addEventListener("submit", async e => {
      e.preventDefault();
      await api(`/scenes/${scene.id}/sequences/${seq.id}/beats`, {
        method: "POST", body: JSON.stringify(addCtl.collectPayload()),
      });
      const fresh = await api(`/scenes/${scene.id}`);
      renderSequences(fresh);
    });

    // per-beat row actions
    seq.beats.forEach((b, i) => {
      const row = card.querySelector(`.beat-row[data-beat-id="${b.id}"]`);
      if (!row) return;

      row.querySelector("[data-remove-beat]").onclick = async () => {
        await api(`/scenes/${scene.id}/sequences/${seq.id}/beats/${b.id}`, { method: "DELETE" });
        const fresh = await api(`/scenes/${scene.id}`);
        renderSequences(fresh);
      };

      const moveUpBtn = row.querySelector("[data-move-up]");
      const moveDownBtn = row.querySelector("[data-move-down]");
      const moveBeat = async direction => {
        const ids = seq.beats.map(x => x.id);
        const idx = ids.indexOf(b.id);
        const swapWith = direction === "up" ? idx - 1 : idx + 1;
        if (swapWith < 0 || swapWith >= ids.length) return;
        [ids[idx], ids[swapWith]] = [ids[swapWith], ids[idx]];
        await api(`/scenes/${scene.id}/sequences/${seq.id}/beats/reorder`, {
          method: "POST", body: JSON.stringify({ beat_ids: ids }),
        });
        const fresh = await api(`/scenes/${scene.id}`);
        renderSequences(fresh);
      };
      if (moveUpBtn) moveUpBtn.onclick = () => moveBeat("up");
      if (moveDownBtn) moveDownBtn.onclick = () => moveBeat("down");

      row.querySelector("[data-edit-beat]").onclick = () => {
        // Replace this row's summary with an inline edit form pre-filled from the beat.
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

        editWrap.querySelector("[data-save-beat]").onclick = async () => {
          await api(`/scenes/${scene.id}/sequences/${seq.id}/beats/${b.id}`, {
            method: "PUT", body: JSON.stringify(ctl.collectPayload()),
          });
          const fresh = await api(`/scenes/${scene.id}`);
          renderSequences(fresh);
        };
        editWrap.querySelector("[data-cancel-beat]").onclick = () => {
          editWrap.remove();
          row.style.display = "";
        };
      };
    });

    card.querySelector("[data-generate]").onclick = async () => {
      try {
        const result = await api(`/scenes/${scene.id}/sequences/${seq.id}/generate`, { method: "POST" });
        card.querySelector("[data-result]").textContent = `Written to: ${result.workflow_path}`;
        const fresh = await api(`/scenes/${scene.id}`);
        renderSequences(fresh);
      } catch (err) { alert(err.message); }
    };
    card.querySelector("[data-resolve]").onclick = async () => {
      const path = prompt("Absolute path to the rendered output video for this sequence:", seq.output_video_path || "");
      if (path === null) return;
      await api(`/scenes/${scene.id}/sequences/${seq.id}/resolve_output`, {
        method: "POST", body: JSON.stringify({ output_video_path: path }),
      });
      const fresh = await api(`/scenes/${scene.id}`);
      renderSequences(fresh);
    };
    card.querySelector("[data-delete-sequence]").onclick = async () => {
      const laterCount = scene.sequences.filter(s => s.index > seq.index).length;
      const warning = laterCount > 0
        ? ` This scene has ${laterCount} sequence(s) after it that chain from earlier sequences by ` +
          `order -- deleting this one will renumber them, and any already generated/rendered may need ` +
          `to be regenerated to chain correctly.`
        : "";
      if (!confirm(`Delete Sequence #${seq.index}? This cannot be undone.${warning}`)) return;
      await api(`/scenes/${scene.id}/sequences/${seq.id}`, { method: "DELETE" });
      const fresh = await api(`/scenes/${scene.id}`);
      renderSequences(fresh);
    };
    container.appendChild(card);
  }
}

$("#sequence-form").addEventListener("submit", async e => {
  e.preventDefault();
  if (!currentSceneId) return;
  const f = e.target;
  const payload = { duration: parseFloat(f.duration.value) };
  await api(`/scenes/${currentSceneId}/sequences`, { method: "POST", body: JSON.stringify(payload) });
  f.reset(); f.duration.value = 8;
  const fresh = await api(`/scenes/${currentSceneId}`);
  renderSequences(fresh);
});

// ---------- init ----------
refreshCharacters();
refreshSettings();
refreshScenes();
refreshSceneSettingSelect();
refreshSceneStyleSelect();
refreshCastingCharacterSelect();
