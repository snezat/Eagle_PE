"use strict";

const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
const app = document.getElementById("app");
const fatal = document.getElementById("fatal");
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const uid = () => crypto.randomUUID();
const round5 = value => Math.round(value / 5) * 5;
const today = () => {
  const date = new Date();
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 10);
};
const initialState = {sports: [], sportGroups: {}, classGroups: [], athletes: [], assignments: [], prescriptions: [], suggestions: [], liftLibrary: []};
let state = structuredClone(initialState);
let rosterEditing = false;
let selectedAthletes = new Set();
let tvMode = "both";
let saving = false;
let saveQueued = false;

function optionList(select, items, allLabel = null, chosen = null) {
  select.replaceChildren();
  if (allLabel) select.append(new Option(allLabel, "all"));
  items.forEach(item => select.append(new Option(item, item)));
  if ([...select.options].some(option => option.value === chosen)) select.value = chosen;
}

function badge(text, type = "") {
  const element = document.createElement("span");
  element.className = `badge ${type}`;
  element.textContent = text;
  return element;
}

function cell(value) {
  const td = document.createElement("td");
  if (value instanceof Node) td.append(value); else td.textContent = value ?? "";
  return td;
}

function message(id, text) {
  const target = document.getElementById(id);
  if (target) target.textContent = text;
}

async function loadState() {
  const response = await fetch("/api/state", {credentials: "same-origin", headers: {Accept: "application/json"}});
  if (response.status === 401) throw new Error("session");
  if (!response.ok) throw new Error("load");
  state = {...structuredClone(initialState), ...await response.json()};
}

async function saveState() {
  if (saving) { saveQueued = true; return; }
  saving = true;
  $("#save-status").textContent = "Saving securely…";
  try {
    const response = await fetch("/api/state", {
      method: "PUT", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"},
      body: JSON.stringify(state),
    });
    if (response.status === 401) location.href = "/";
    if (!response.ok) throw new Error((await response.json()).error || "Save failed");
    $("#save-status").textContent = `Saved securely · ${new Date().toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})}`;
  } catch (error) {
    $("#save-status").textContent = "Save failed — try again";
    console.error(error);
  } finally {
    saving = false;
    if (saveQueued) { saveQueued = false; saveState(); }
  }
}

function setupNavigation() {
  $$('[data-view]').forEach(button => button.addEventListener("click", () => {
    $$('[data-view]').forEach(item => item.setAttribute("aria-selected", String(item === button)));
    $$('[data-panel]').forEach(panel => panel.hidden = panel.dataset.panel !== button.dataset.view);
    renderAll();
  }));
}

function renderSide() {
  const host = $("#side-groups");
  host.replaceChildren();
  state.classGroups.forEach(group => {
    const row = document.createElement("div");
    row.className = "side-group";
    const detail = document.createElement("span");
    detail.textContent = group.replace("Nonfootball ", "");
    const sports = applicableSports(group).join(" / ");
    if (sports) {
      const small = document.createElement("small");
      small.textContent = sports;
      detail.append(small);
    }
    const count = document.createElement("strong");
    count.textContent = state.athletes.filter(a => a.classGroup === group).length;
    row.append(detail, count);
    host.append(row);
  });
}

function applicableSports(group) {
  return state.sports.filter(sport => state.athletes.some(athlete => athlete.classGroup === group && athlete.sports.includes(sport)));
}

function renderDashboard() {
  const submitted = state.prescriptions.filter(p => p.submitted).length;
  $("#d-submitted").textContent = `${submitted}/${state.prescriptions.length}`;
  $("#d-pending").textContent = state.suggestions.filter(s => s.status === "pending").length;
  $("#d-missing").textContent = state.prescriptions.filter(p => !p.submitted).length;
  $("#d-max").textContent = state.prescriptions.filter(p => !p.projectedMaxUsed).length;
  const groups = $("#d-groups"); groups.replaceChildren();
  state.classGroups.filter(group => group !== "Football").forEach(group => {
    const rows = state.prescriptions.filter(p => p.group === group), done = rows.filter(p => p.submitted).length;
    const line = document.createElement("div"); line.className = "progress";
    const label = document.createElement("span"); label.textContent = group.replace("Nonfootball ", "");
    const bar = document.createElement("div"); bar.className = "bar";
    const fill = document.createElement("span"); fill.style.width = `${rows.length ? done / rows.length * 100 : 0}%`; bar.append(fill);
    const value = document.createElement("strong"); value.textContent = `${done}/${rows.length}`;
    line.append(label, bar, value); groups.append(line);
  });
  const attention = $("#d-attention"); attention.replaceChildren();
  [
    ["No starting max", state.prescriptions.filter(p => !p.projectedMaxUsed).length, "bad"],
    ["Needs coach review", state.prescriptions.filter(p => p.needsReview).length, "warn"],
    ["Extreme burnout entry", state.suggestions.filter(s => s.extreme && s.status === "pending").length, "warn"],
  ].forEach(([label, count, type]) => { const line = document.createElement("p"); line.append(document.createTextNode(`${label}: `), badge(count, type)); attention.append(line); });
}

function setupAssignInputs() {
  const group = $("#a-group"), current = group.value;
  optionList(group, state.classGroups.filter(name => name !== "Football"), null, current);
  if (!group.value) group.value = state.classGroups.find(name => name !== "Football") || "";
  refreshAssignSports();
  $("#a-date").value ||= today();
  const lifts = [...new Set(state.liftLibrary)].sort((a, b) => a.localeCompare(b));
  $("#lifts").replaceChildren(...lifts.map(name => new Option(name)));
  if (!$("#a-lift").value) $("#a-lift").placeholder = "Choose or type a new lift";
}

function refreshAssignSports() {
  const select = $("#a-sport"), chosen = select.value;
  optionList(select, applicableSports($("#a-group").value), "All sports", chosen);
}

function renderAssignments() {
  const body = $("#assignments"); body.replaceChildren();
  let section = "";
  const sorted = [...state.assignments].filter(a => a.group !== "Football").sort((a, b) =>
    a.group.localeCompare(b.group) || (a.sport || "all").localeCompare(b.sport || "all") || b.date.localeCompare(a.date) || b.createdAt - a.createdAt);
  sorted.forEach(assignment => {
    const key = `${assignment.group}|${assignment.sport}`;
    if (key !== section) {
      section = key;
      const row = document.createElement("tr"); row.className = "group-row";
      const td = cell(`${assignment.group.replace("Nonfootball ", "")} - ${assignment.sport === "all" ? "All sports" : assignment.sport}`); td.colSpan = 5; row.append(td); body.append(row);
    }
    const row = document.createElement("tr"); row.className = `assignment-row${assignment.priority ? " priority" : ""}`;
    row.append(cell(assignment.date), cell(`${assignment.lift} - ${assignment.sets} × ${assignment.reps} @ ${assignment.percent}%`), cell(rowsFor(assignment.id).length));
    row.append(cell(badge(assignment.locked ? "Locked" : "Open", assignment.locked ? "warn" : "")));
    const actions = document.createElement("div"); actions.className = "actions";
    const priority = document.createElement("button"); priority.className = "btn mini priority"; priority.setAttribute("aria-pressed", String(Boolean(assignment.priority))); priority.textContent = assignment.priority ? "Priority ★" : "Priority";
    priority.addEventListener("click", async () => { assignment.priority = !assignment.priority; await saveState(); renderAssignments(); renderToday(); });
    const remove = document.createElement("button"); remove.className = "btn mini danger"; remove.textContent = "Delete";
    remove.addEventListener("click", async () => {
      if (!confirm(`Delete ${assignment.lift}? Its athlete prescriptions and results will also be removed.`)) return;
      state.assignments = state.assignments.filter(a => a.id !== assignment.id);
      state.prescriptions = state.prescriptions.filter(p => p.assignmentId !== assignment.id);
      state.suggestions = state.suggestions.filter(s => s.assignmentId !== assignment.id);
      await saveState(); renderAll();
    });
    actions.append(priority, remove); row.append(cell(actions)); body.append(row);
  });
  if (!body.children.length) { const row = document.createElement("tr"), td = cell("No nonfootball workouts assigned yet."); td.colSpan = 5; row.append(td); body.append(row); }
}

function rowsFor(assignmentId) { return state.prescriptions.filter(p => p.assignmentId === assignmentId); }

async function assignWorkout() {
  const group = $("#a-group").value, sport = $("#a-sport").value, date = $("#a-date").value, lift = $("#a-lift").value.trim();
  const percent = Number($("#a-percent").value), sets = Number($("#a-sets").value), reps = Number($("#a-reps").value), expected = Number($("#a-expected").value);
  if (!group || !date || !lift || percent < 1 || percent > 100 || sets < 1 || reps < 1 || expected < 1) { message("a-message", "Complete all required fields with valid values."); return; }
  const assignment = {id: uid(), group, sport, date, lift, percent, sets, reps, expected, notes: $("#a-notes").value.trim(), locked: false, priority: false, createdAt: Date.now()};
  state.assignments.push(assignment);
  const eligible = state.athletes.filter(a => a.classGroup === group && (sport === "all" || a.sports.includes(sport)));
  eligible.forEach(athlete => {
    const max = Number(athlete.overrides?.[lift] || athlete.maxes?.[lift] || 0);
    state.prescriptions.push({id: uid(), assignmentId: assignment.id, athleteId: athlete.id, athleteName: athlete.name, group, sports: [...athlete.sports], lift, projectedMaxUsed: max || null, prescribedLoad: max ? round5(max * percent / 100) : null, sets, reps, expected, completedLoad: "", burnoutReps: "", note: "", submitted: false, loadMismatch: false, needsReview: false, isIndividualOverride: false});
  });
  if (!state.liftLibrary.some(name => name.toLowerCase() === lift.toLowerCase())) state.liftLibrary.push(lift);
  await saveState();
  message("a-message", `Assigned ${lift} to ${eligible.length} athletes; ${eligible.filter(a => !a.maxes?.[lift]).length} need a starting max.`);
  renderAll();
}

function renderLiftManager() {
  const host = $("#lift-options"); host.replaceChildren();
  [...new Set(state.liftLibrary)].sort((a, b) => a.localeCompare(b)).forEach(name => {
    const chip = document.createElement("span"); chip.className = "chip"; chip.append(document.createTextNode(name));
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×"; remove.setAttribute("aria-label", `Remove ${name}`);
    remove.addEventListener("click", async () => { if (!confirm(`Remove ${name} from the lift list? Past workouts stay unchanged.`)) return; state.liftLibrary = state.liftLibrary.filter(item => item !== name); await saveState(); setupAssignInputs(); renderLiftManager(); });
    chip.append(remove); host.append(chip);
  });
}

function assignmentLabel(a) { return `${a.date} · ${a.group.replace("Nonfootball ", "")} · ${a.lift}`; }

function renderResults() {
  const assignmentSelect = $("#r-assignment"), chosen = assignmentSelect.value;
  assignmentSelect.replaceChildren();
  [...state.assignments].filter(a => a.group !== "Football").sort((a, b) => b.date.localeCompare(a.date) || b.createdAt - a.createdAt).forEach(a => assignmentSelect.append(new Option(assignmentLabel(a), a.id)));
  if (state.assignments.some(a => a.id === chosen)) assignmentSelect.value = chosen;
  const assignment = state.assignments.find(a => a.id === assignmentSelect.value);
  optionList($("#r-sport"), state.sports, "All sports", $("#r-sport").value);
  const body = $("#results"); body.replaceChildren();
  if (!assignment) { $("#r-summary").textContent = "Assign a lift first."; return; }
  let rows = rowsFor(assignment.id), sport = $("#r-sport").value, status = $("#r-status").value;
  rows = rows.filter(p => (sport === "all" || p.sports.includes(sport)) && (status === "all" || status === "missing" && !p.submitted || status === "submitted" && p.submitted || status === "review" && p.needsReview));
  $("#r-summary").textContent = `${assignment.group} · ${assignment.lift} · ${assignment.sets} × ${assignment.reps} @ ${assignment.percent}% · ${rows.filter(p => p.submitted).length}/${rows.length} shown submitted${assignment.locked ? " · LOCKED" : ""}`;
  rows.forEach(p => {
    const row = document.createElement("tr"); row.dataset.id = p.id;
    const who = document.createElement("div"); const name = document.createElement("div"); name.className = "athlete-name"; name.textContent = p.athleteName; who.append(name);
    row.append(cell(who), cell(p.sports.join(", ") || "No sport"), cell(p.projectedMaxUsed ? `${p.projectedMaxUsed} lb` : "No projected max"), cell(p.prescribedLoad ? `${p.sets} × ${p.reps} @ ${p.prescribedLoad} lb` : "Technique only"));
    [["completedLoad", "number"], ["burnoutReps", "number"], ["note", "text"]].forEach(([field, type]) => { const input = document.createElement("input"); input.type = type; input.value = p[field] ?? ""; input.dataset.field = field; input.disabled = assignment.locked; row.append(cell(input)); });
    row.append(cell(badge(p.needsReview ? "Needs review" : p.submitted ? "Submitted" : "Missing", p.needsReview ? "warn" : p.submitted ? "" : "bad"))); body.append(row);
  });
  $("#lock-session").textContent = assignment.locked ? "Unlock session" : "Lock session";
}

async function saveResults() {
  const assignment = state.assignments.find(a => a.id === $("#r-assignment").value);
  if (!assignment || assignment.locked) { message("r-message", "This session is locked."); return; }
  $$("#results tr[data-id]").forEach(row => {
    const p = state.prescriptions.find(item => item.id === row.dataset.id);
    $$('[data-field]', row).forEach(input => p[input.dataset.field] = input.type === "number" ? (input.value === "" ? "" : Number(input.value)) : input.value);
  });
  rowsFor(assignment.id).forEach(p => {
    p.submitted = Boolean(p.completedLoad !== "" && p.burnoutReps !== "");
    p.loadMismatch = Boolean(p.submitted && p.prescribedLoad && Number(p.completedLoad) !== Number(p.prescribedLoad));
    p.needsReview = Boolean(p.loadMismatch || Number(p.burnoutReps) > p.expected + 15);
    if (!p.submitted) return;
    const athlete = state.athletes.find(a => a.id === p.athleteId), oldMax = Number(athlete?.maxes?.[p.lift] || p.projectedMaxUsed || 0), suggested = round5(Number(p.completedLoad) * (1 + Number(p.burnoutReps) / 30));
    const existing = state.suggestions.find(s => s.prescriptionId === p.id);
    const data = {id: existing?.id || uid(), prescriptionId: p.id, assignmentId: assignment.id, athleteId: p.athleteId, athleteName: p.athleteName, group: p.group, sports: p.sports, lift: p.lift, oldMax, burnoutReps: Number(p.burnoutReps), expected: p.expected, suggestedMax: suggested, manualMax: existing?.manualMax || suggested, extreme: Number(p.burnoutReps) > p.expected + 15, status: existing?.status || "pending"};
    existing ? Object.assign(existing, data) : state.suggestions.push(data);
  });
  await saveState(); message("r-message", "Results saved. Max suggestions are ready for review."); renderAll();
}

function renderReview() {
  optionList($("#v-group"), state.classGroups.filter(g => g !== "Football"), "All groups", $("#v-group").value);
  optionList($("#v-sport"), state.sports, "All sports", $("#v-sport").value);
  optionList($("#v-lift"), [...new Set(state.liftLibrary)].sort(), "All lifts", $("#v-lift").value);
  const group = $("#v-group").value, sport = $("#v-sport").value, lift = $("#v-lift").value, status = $("#v-status").value;
  const visible = state.suggestions.filter(s => (group === "all" || s.group === group) && (sport === "all" || s.sports.includes(sport)) && (lift === "all" || s.lift === lift) && (status === "all" || status === "pending" && s.status === "pending" || status === "extreme" && s.extreme));
  const body = $("#reviews"); body.replaceChildren();
  visible.forEach(s => {
    const row = document.createElement("tr"); row.dataset.id = s.id;
    const manual = document.createElement("input"); manual.type = "number"; manual.step = "5"; manual.value = s.manualMax ?? s.suggestedMax ?? ""; manual.dataset.manual = s.id;
    const actions = document.createElement("div"); actions.className = "actions";
    [["Approve", "approved", "primary"], ["Reject", "rejected", "danger"]].forEach(([label, value, type]) => { const button = document.createElement("button"); button.className = `btn mini ${type}`; button.textContent = label; button.addEventListener("click", () => decideSuggestion(s.id, value, Number(manual.value))); actions.append(button); });
    row.append(cell(s.athleteName), cell(`${s.group.replace("Nonfootball ", "")} / ${s.sports.join(", ")}`), cell(s.lift), cell(`${s.oldMax || 0} lb`), cell(String(s.burnoutReps)), cell(String(s.expected)), cell(manual), cell(actions)); body.append(row);
  });
  if (!visible.length) { const row = document.createElement("tr"), td = cell("No suggestions match these filters."); td.colSpan = 8; row.append(td); body.append(row); }
  return visible;
}

async function decideSuggestion(id, status, manualMax) {
  const suggestion = state.suggestions.find(s => s.id === id); if (!suggestion) return;
  suggestion.status = status; suggestion.manualMax = manualMax || suggestion.suggestedMax;
  if (status === "approved") {
    const athlete = state.athletes.find(a => a.id === suggestion.athleteId);
    if (athlete) { athlete.maxes ||= {}; athlete.maxes[suggestion.lift] = suggestion.manualMax; }
  }
  await saveState(); renderAll();
}

function renderRoster() {
  optionList($("#f-sport"), state.sports, "All sports", $("#f-sport").value);
  optionList($("#f-class"), state.classGroups, "All groups", $("#f-class").value);
  optionList($("#new-group"), state.classGroups, null, $("#new-group").value);
  const sport = $("#f-sport").value, group = $("#f-class").value, query = $("#f-search").value.trim().toLowerCase();
  const visible = state.athletes.filter(a => (sport === "all" || a.sports.includes(sport)) && (group === "all" || a.classGroup === group) && (!query || a.name.toLowerCase().includes(query)));
  $("#roster-count").textContent = `${visible.length} athletes`;
  $("#roster-title").textContent = [group !== "all" ? group : "All athletes", sport !== "all" ? sport : ""].filter(Boolean).join(" · ");
  $$(".select-column").forEach(el => el.hidden = !rosterEditing); $("#remove-athletes").hidden = !rosterEditing;
  const body = $("#roster"); body.replaceChildren();
  visible.forEach(athlete => {
    const row = document.createElement("tr");
    if (rosterEditing) { const check = document.createElement("input"); check.type = "checkbox"; check.checked = selectedAthletes.has(athlete.id); check.addEventListener("change", () => check.checked ? selectedAthletes.add(athlete.id) : selectedAthletes.delete(athlete.id)); row.append(cell(check)); }
    const who = document.createElement("div"); const name = document.createElement("div"); name.className = "athlete-name"; name.textContent = athlete.name; const meta = document.createElement("div"); meta.className = "athlete-meta"; meta.textContent = [athlete.grade ? `Grade ${athlete.grade}` : "", athlete.teacher].filter(Boolean).join(" · "); who.append(name, meta); row.append(cell(who));
    if (rosterEditing) {
      const groupSelect = document.createElement("select"); optionList(groupSelect, state.classGroups, null, athlete.classGroup); groupSelect.addEventListener("change", async () => { athlete.classGroup = groupSelect.value; await saveState(); renderAll(); }); row.append(cell(groupSelect));
      const checks = document.createElement("div"); checks.className = "checks"; state.sports.forEach(s => { const label = document.createElement("label"), input = document.createElement("input"); input.type = "checkbox"; input.checked = athlete.sports.includes(s); input.addEventListener("change", async () => { athlete.sports = input.checked ? [...new Set([...athlete.sports, s])] : athlete.sports.filter(x => x !== s); await saveState(); renderAll(); }); label.append(input, document.createTextNode(s)); checks.append(label); }); row.append(cell(checks));
      const maxes = document.createElement("div"); maxes.className = "checks"; ["Bench", "Back Squat", "Power Clean", "Deadlift"].forEach(lift => { const label = document.createElement("label"); label.textContent = lift; const input = document.createElement("input"); input.type = "number"; input.step = "5"; input.value = athlete.maxes?.[lift] || ""; input.addEventListener("change", async () => { athlete.maxes ||= {}; input.value === "" ? delete athlete.maxes[lift] : athlete.maxes[lift] = Number(input.value); await saveState(); }); label.append(input); maxes.append(label); }); row.append(cell(maxes));
    } else {
      row.append(cell(athlete.classGroup), cell(athlete.sports.join(", ") || "No sport"));
      const values = Object.entries(athlete.maxes || {}).map(([lift, max]) => `${lift}: ${max}`).join(" · "); row.append(cell(values || "No starting maxes"));
    }
    body.append(row);
  });
}

function renderNewAthleteSports() {
  const host = $("#new-sports"); host.replaceChildren();
  state.sports.forEach(sport => { const label = document.createElement("label"), input = document.createElement("input"); input.type = "checkbox"; input.value = sport; label.append(input, document.createTextNode(sport)); host.append(label); });
}

async function addAthlete() {
  const name = $("#new-name").value.trim(); if (!name) { message("f-message", "Athlete name is required."); return; }
  const athlete = {id: uid(), name, grade: $("#new-grade").value.trim(), teacher: $("#new-teacher").value.trim(), classGroup: $("#new-group").value, sports: $$('#new-sports input:checked').map(i => i.value), groupBySport: {}, subgroup: "", maxes: {}, overrides: {}};
  state.athletes.push(athlete); await saveState(); $("#athlete-form").hidden = true; ["#new-name", "#new-grade", "#new-teacher"].forEach(id => $(id).value = ""); message("f-message", `${name} added.`); renderAll();
}

async function removeSelectedAthletes() {
  if (!selectedAthletes.size || !confirm(`Remove ${selectedAthletes.size} selected athletes and their associated records?`)) return;
  const removed = new Set(selectedAthletes); state.athletes = state.athletes.filter(a => !removed.has(a.id));
  state.prescriptions = state.prescriptions.filter(p => !removed.has(p.athleteId)); state.suggestions = state.suggestions.filter(s => !removed.has(s.athleteId)); selectedAthletes.clear(); await saveState(); renderAll();
}

function sportClass(sport) { return `sport-${sport.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "all"}`; }

function renderToday() {
  const host = $("#tv-groups"), date = $("#tv-date").value; host.replaceChildren(); host.classList.toggle("single", tvMode !== "both");
  state.classGroups.filter(group => group !== "Football").filter(group => tvMode === "both" || tvMode === "a" && group.endsWith("Group A") || tvMode === "b" && group.endsWith("Group B")).forEach(group => {
    const card = document.createElement("article"); card.className = "tv-group";
    const heading = document.createElement("h2"); heading.textContent = group === "Nonfootball Group A" ? "Nonfootball · Group A" : group === "Nonfootball Group B" ? "Nonfootball · Group B" : group;
    const teams = document.createElement("div"); teams.className = "tv-teams"; teams.textContent = applicableSports(group).join(" / ");
    const count = document.createElement("div"); count.className = "tv-count"; count.textContent = `${state.athletes.filter(a => a.classGroup === group).length} athletes`;
    card.append(heading, teams, count);
    const items = state.assignments.filter(a => a.date === date && a.group === group).sort((a, b) => Number(Boolean(b.priority)) - Number(Boolean(a.priority)) || a.createdAt - b.createdAt);
    if (!items.length) { const empty = document.createElement("div"); empty.className = "tv-empty"; empty.textContent = "No workout assigned"; card.append(empty); }
    items.forEach(item => {
      const workout = document.createElement("div"); workout.className = `tv-workout ${sportClass(item.sport === "all" ? "all" : item.sport)}${item.priority ? " priority-workout" : ""}`;
      const sport = document.createElement("span"); sport.className = "tv-sport"; sport.textContent = item.sport === "all" ? "All sports" : item.sport;
      const lift = document.createElement("div"); lift.className = "tv-lift"; lift.textContent = item.lift;
      if (item.priority) { const star = document.createElement("span"); star.className = "star"; star.textContent = "★"; star.title = "Priority workout"; star.setAttribute("aria-label", "Priority workout"); lift.append(star); }
      const rx = document.createElement("div"); rx.className = "tv-prescription"; rx.textContent = `${item.sets} sets × ${item.reps} reps @ ${item.percent}%`;
      workout.append(sport, lift, rx);
      if (item.notes) { const notes = document.createElement("div"); notes.className = "tv-notes"; notes.textContent = item.notes; workout.append(notes); }
      card.append(workout);
    });
    host.append(card);
  });
}

function updateClock() {
  const now = new Date(); $("#tv-time").textContent = now.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}); $("#tv-day").textContent = now.toLocaleDateString([], {weekday: "long", month: "long", day: "numeric", year: "numeric"});
}

function renderAll() {
  renderSide(); renderDashboard(); setupAssignInputs(); renderAssignments(); renderLiftManager(); renderResults(); renderReview(); renderRoster(); renderNewAthleteSports(); renderToday(); updateClock();
}

function bindEvents() {
  setupNavigation();
  $("#refresh").addEventListener("click", renderAll); $("#a-group").addEventListener("change", refreshAssignSports); $("#assign-workout").addEventListener("click", assignWorkout);
  $("#manage-lifts").addEventListener("click", () => $("#lift-manager").hidden = !$("#lift-manager").hidden); $("#close-lifts").addEventListener("click", () => $("#lift-manager").hidden = true);
  ["#r-assignment", "#r-sport", "#r-status"].forEach(id => $(id).addEventListener("change", renderResults)); $("#save-results").addEventListener("click", saveResults);
  $("#lock-session").addEventListener("click", async () => { const a = state.assignments.find(x => x.id === $("#r-assignment").value); if (a) { a.locked = !a.locked; await saveState(); renderAll(); } });
  ["#v-group", "#v-sport", "#v-lift", "#v-status"].forEach(id => $(id).addEventListener("change", renderReview));
  $("#approve-visible").addEventListener("click", async () => { const visible = renderReview().filter(s => s.status === "pending"); visible.forEach(s => { const input = $(`[data-manual="${CSS.escape(s.id)}"]`); s.manualMax = Number(input?.value || s.suggestedMax); s.status = "approved"; const athlete = state.athletes.find(a => a.id === s.athleteId); if (athlete) { athlete.maxes ||= {}; athlete.maxes[s.lift] = s.manualMax; } }); await saveState(); renderAll(); });
  $("#add-athlete").addEventListener("click", () => $("#athlete-form").hidden = !$("#athlete-form").hidden); $("#save-athlete").addEventListener("click", addAthlete);
  $("#edit-roster").addEventListener("click", () => { rosterEditing = !rosterEditing; selectedAthletes.clear(); $("#edit-roster").textContent = rosterEditing ? "Done editing" : "Edit roster"; renderRoster(); });
  $("#remove-athletes").addEventListener("click", removeSelectedAthletes); $("#select-all").addEventListener("change", event => { const sport = $("#f-sport").value, group = $("#f-class").value, query = $("#f-search").value.trim().toLowerCase(); state.athletes.filter(a => (sport === "all" || a.sports.includes(sport)) && (group === "all" || a.classGroup === group) && (!query || a.name.toLowerCase().includes(query))).forEach(a => event.target.checked ? selectedAthletes.add(a.id) : selectedAthletes.delete(a.id)); renderRoster(); });
  ["#f-sport", "#f-class"].forEach(id => $(id).addEventListener("change", renderRoster)); $("#f-search").addEventListener("input", renderRoster);
  $("#tv-date").addEventListener("change", renderToday); $("#tv-refresh").addEventListener("click", renderToday);
  $$('[data-tv-mode]').forEach(button => button.addEventListener("click", () => { tvMode = button.dataset.tvMode; $$('[data-tv-mode]').forEach(b => b.classList.toggle("active", b === button)); renderToday(); }));
  $("#tv-fullscreen").addEventListener("click", async () => { const panel = $('[data-panel="today"]'); document.fullscreenElement ? await document.exitFullscreen() : await panel.requestFullscreen(); });
  document.addEventListener("fullscreenchange", () => { const panel = $('[data-panel="today"]'); panel.classList.toggle("tv-board-full", Boolean(document.fullscreenElement)); $("#tv-fullscreen").textContent = document.fullscreenElement ? "Exit TV mode" : "Enter TV mode"; });
  setInterval(updateClock, 30000);
}

loadState().then(() => { $("#tv-date").value = today(); bindEvents(); renderAll(); app.hidden = false; $("#save-status").textContent = "Encrypted server data loaded"; }).catch(() => { fatal.hidden = false; });

