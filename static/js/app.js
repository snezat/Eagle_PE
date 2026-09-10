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
const initialState = {sports: [], sportGroups: {}, classGroups: [], athletes: [], assignments: [], prescriptions: [], suggestions: [], attendance: [], liftLibrary: [], revision: 0};
let state = structuredClone(initialState);
let rosterEditing = false;
let selectedAthletes = new Set();
let tvMode = "both";
let attendanceGroup = "Nonfootball Group A";
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
  if (response.status === 401) { location.href = "/"; throw new Error("session"); }
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
    if (response.status === 401) { location.href = "/"; return false; }
    const result = await response.json();
    if (response.status === 409) {
      await loadState(); renderAll();
      $("#save-status").textContent = "Another screen changed the planner — reloaded latest data";
      return false;
    }
    if (!response.ok) throw new Error(result.error || "Save failed");
    state.revision = Number(result.revision ?? state.revision);
    $("#save-status").textContent = `Saved securely · ${new Date().toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})}`;
    return true;
  } catch (error) {
    $("#save-status").textContent = "Save failed — try again";
    console.error(error);
  } finally {
    saving = false;
    if (saveQueued) { saveQueued = false; saveState(); }
  }
}

async function saveAttendance(date, group, athleteId, present) {
  const response = await fetch("/api/attendance", {
    method: "PUT", credentials: "same-origin",
    headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"},
    body: JSON.stringify({date, group, athleteId, present}),
  });
  if (response.status === 401) { location.href = "/"; throw new Error("session"); }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Attendance save failed");
  state.revision = Math.max(Number(state.revision || 0), Number(result.revision || 0));
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

function allLiftNames() {
  return [...new Set(state.liftLibrary.map(name => String(name || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function renderLiftDropdown() {
  const host = $("#lift-dropdown"); host.replaceChildren();
  const names = allLiftNames();
  names.forEach(name => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = name;
    button.addEventListener("click", () => {
      $("#a-lift").value = name;
      host.hidden = true;
      $("#open-lifts").setAttribute("aria-expanded", "false");
      $("#a-lift").focus();
    });
    host.append(button);
  });
  if (!names.length) {
    const empty = document.createElement("div"); empty.className = "empty-lifts"; empty.textContent = "No saved lifts yet. Type a new lift name to add one."; host.append(empty);
  }
}

function toggleLiftDropdown(force) {
  const host = $("#lift-dropdown"), open = force ?? host.hidden;
  if (open) renderLiftDropdown();
  host.hidden = !open;
  $("#open-lifts").setAttribute("aria-expanded", String(open));
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
  const sportSelect = $("#f-sport"), chosenSport = sportSelect.value || "all";
  sportSelect.replaceChildren(new Option("All athletes", "all"), new Option("No sport assigned", "unassigned"), ...state.sports.map(sport => new Option(sport, sport)));
  sportSelect.value = [...sportSelect.options].some(option => option.value === chosenSport) ? chosenSport : "all";
  optionList($("#f-class"), state.classGroups, "All groups", $("#f-class").value);
  optionList($("#new-group"), state.classGroups, null, $("#new-group").value);
  const sport = sportSelect.value;
  const subgroupNames = sport === "all" ? [...new Set(Object.values(state.sportGroups || {}).flat())].sort((a, b) => a.localeCompare(b)) : sport === "unassigned" ? [] : (state.sportGroups[sport] || []);
  optionList($("#f-sub"), subgroupNames, "All training groups", $("#f-sub").value);
  const visible = visibleRosterAthletes();
  $("#roster-count").textContent = `${visible.length} shown · ${state.athletes.length} total`;
  $("#roster-title").textContent = sport === "all" ? "All athletes" : sport === "unassigned" ? "No sport assigned" : `${sport} roster`;
  $$(".select-column").forEach(el => el.hidden = !rosterEditing); $("#remove-athletes").hidden = !rosterEditing;
  $("#remove-athletes").textContent = sport !== "all" && sport !== "unassigned" ? `Remove from ${sport}` : "Remove selected athletes";
  const body = $("#roster"); body.replaceChildren();
  visible.forEach(athlete => {
    const row = document.createElement("tr");
    if (rosterEditing) { const check = document.createElement("input"); check.type = "checkbox"; check.checked = selectedAthletes.has(athlete.id); check.addEventListener("change", () => check.checked ? selectedAthletes.add(athlete.id) : selectedAthletes.delete(athlete.id)); row.append(cell(check)); }
    const who = document.createElement("div"); const name = document.createElement("div"); name.className = "athlete-name"; name.textContent = athlete.name; const meta = document.createElement("div"); meta.className = "athlete-meta"; meta.textContent = [athlete.grade ? `Grade ${athlete.grade}` : "", athlete.teacher].filter(Boolean).join(" · "); who.append(name, meta); row.append(cell(who));
    if (rosterEditing) {
      const groupSelect = document.createElement("select"); optionList(groupSelect, state.classGroups, null, athlete.classGroup); groupSelect.addEventListener("change", async () => { athlete.classGroup = groupSelect.value; await saveState(); renderAll(); }); row.append(cell(groupSelect));
      const checks = document.createElement("div"); checks.className = "checks"; state.sports.forEach(s => { const label = document.createElement("label"), input = document.createElement("input"); input.type = "checkbox"; input.checked = athlete.sports.includes(s); input.addEventListener("change", async () => { athlete.groupBySport ||= {}; athlete.sports = input.checked ? [...new Set([...athlete.sports, s])] : athlete.sports.filter(x => x !== s); if (!input.checked) delete athlete.groupBySport[s]; await saveState(); renderAll(); }); label.append(input, document.createTextNode(s)); checks.append(label); }); row.append(cell(checks));
      const subgroups = document.createElement("div"); subgroups.className = "subgroup-editor"; athlete.groupBySport ||= {};
      athlete.sports.forEach(s => { const label = document.createElement("label"), select = document.createElement("select"); label.append(document.createTextNode(s)); select.append(new Option("Unassigned", ""), ...(state.sportGroups[s] || []).map(name => new Option(name, name))); select.value = athlete.groupBySport[s] || ""; select.addEventListener("change", async () => { select.value ? athlete.groupBySport[s] = select.value : delete athlete.groupBySport[s]; await saveState(); renderRoster(); }); label.append(select); subgroups.append(label); }); row.append(cell(subgroups));
      const maxes = document.createElement("div"); maxes.className = "checks"; ["Bench", "Back Squat", "Power Clean", "Deadlift"].forEach(lift => { const label = document.createElement("label"); label.textContent = lift; const input = document.createElement("input"); input.type = "number"; input.step = "5"; input.value = athlete.maxes?.[lift] || ""; input.addEventListener("change", async () => { athlete.maxes ||= {}; input.value === "" ? delete athlete.maxes[lift] : athlete.maxes[lift] = Number(input.value); await saveState(); }); label.append(input); maxes.append(label); }); row.append(cell(maxes));
    } else {
      const sports = document.createElement("div"); sports.className = "roster-sports"; (athlete.sports.length ? athlete.sports : ["No sport"]).forEach(value => { const tag = document.createElement("span"); tag.className = "badge"; tag.textContent = value; sports.append(tag); });
      const subgroups = document.createElement("div"); subgroups.className = "roster-subgroups"; athlete.sports.forEach(s => { const line = document.createElement("span"); line.textContent = `${s}: ${athlete.groupBySport?.[s] || "Unassigned"}`; subgroups.append(line); });
      row.append(cell(athlete.classGroup), cell(sports), cell(subgroups));
      const values = Object.entries(athlete.maxes || {}).map(([lift, max]) => `${lift}: ${max}`).join(" · "); row.append(cell(values || "No starting maxes"));
    }
    body.append(row);
  });
  if (!visible.length) { const row = document.createElement("tr"), td = cell("No athletes match these filters."); td.colSpan = rosterEditing ? 7 : 6; td.className = "empty-row"; row.append(td); body.append(row); }
}

function visibleRosterAthletes() {
  const sport = $("#f-sport").value || "all", group = $("#f-class").value || "all", subgroup = $("#f-sub").value || "all", query = $("#f-search").value.trim().toLowerCase();
  return state.athletes.filter(athlete =>
    (sport === "all" || sport === "unassigned" && !athlete.sports.length || athlete.sports.includes(sport)) &&
    (group === "all" || athlete.classGroup === group) &&
    (subgroup === "all" || sport !== "all" && sport !== "unassigned" && athlete.groupBySport?.[sport] === subgroup || sport === "all" && Object.values(athlete.groupBySport || {}).includes(subgroup)) &&
    (!query || athlete.name.toLowerCase().includes(query))
  );
}

function renderRosterSetup() {
  const renderChips = (host, values) => { host.replaceChildren(); values.forEach(value => { const chip = document.createElement("span"); chip.className = "chip"; chip.textContent = value; host.append(chip); }); };
  renderChips($("#class-options"), state.classGroups);
  renderChips($("#sport-options"), state.sports);
  optionList($("#sub-sport"), state.sports, null, $("#sub-sport").value);
  const sport = $("#sub-sport").value;
  renderChips($("#sub-options"), state.sportGroups?.[sport] || []);
}

async function addClassGroup() {
  const name = $("#new-class").value.trim();
  if (!name || state.classGroups.some(value => value.toLowerCase() === name.toLowerCase())) { message("f-message", name ? "That class group already exists." : "Enter a class group name."); return; }
  state.classGroups.push(name); $("#new-class").value = ""; await saveState(); renderAll();
}

async function addSport() {
  const name = $("#new-sport").value.trim();
  if (!name || state.sports.some(value => value.toLowerCase() === name.toLowerCase())) { message("f-message", name ? "That sport already exists." : "Enter a sport name."); return; }
  state.sports.push(name); state.sportGroups ||= {}; state.sportGroups[name] = []; $("#new-sport").value = ""; await saveState(); renderAll();
}

async function addSportGroup() {
  const sport = $("#sub-sport").value, name = $("#new-sub").value.trim(); state.sportGroups ||= {};
  if (!sport) { message("f-message", "Add a sport before creating a sport training group."); return; }
  const groups = state.sportGroups[sport] ||= [];
  if (!name || groups.some(value => value.toLowerCase() === name.toLowerCase())) { message("f-message", !name ? "Enter a sport training group name." : "That sport training group already exists."); return; }
  groups.push(name); $("#new-sub").value = ""; await saveState(); renderAll();
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
  if (!selectedAthletes.size) { message("f-message", "Select athletes first."); return; }
  const sport = $("#f-sport").value;
  if (sport !== "all" && sport !== "unassigned") {
    if (!confirm(`Remove ${selectedAthletes.size} selected athletes from ${sport}? Their athlete records and other sports will remain.`)) return;
    state.athletes.forEach(athlete => { if (selectedAthletes.has(athlete.id)) { athlete.sports = athlete.sports.filter(value => value !== sport); if (athlete.groupBySport) delete athlete.groupBySport[sport]; } });
    message("f-message", `Removed ${selectedAthletes.size} athletes from ${sport}.`);
  } else {
    if (!confirm(`Remove ${selectedAthletes.size} selected athletes and their associated records?`)) return;
    const removed = new Set(selectedAthletes); state.athletes = state.athletes.filter(a => !removed.has(a.id));
    state.prescriptions = state.prescriptions.filter(p => !removed.has(p.athleteId)); state.suggestions = state.suggestions.filter(s => !removed.has(s.athleteId)); state.attendance = state.attendance.filter(record => !removed.has(record.athleteId));
    message("f-message", `Removed ${selectedAthletes.size} athletes.`);
  }
  selectedAthletes.clear(); await saveState(); renderAll();
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

function renderAttendance() {
  const dateInput = $("#attendance-date");
  dateInput.value ||= today();
  const date = dateInput.value;
  const athletes = state.athletes.filter(a => a.classGroup === attendanceGroup).sort((a, b) => {
    const last = name => name.trim().split(/\s+/).at(-1) || name;
    return last(a.name).localeCompare(last(b.name)) || a.name.localeCompare(b.name);
  });
  const columns = window.innerWidth < 900 ? 3 : 4;
  const grid = $("#attendance-grid");
  grid.style.setProperty("--attendance-rows", String(Math.max(1, Math.ceil(athletes.length / columns))));
  grid.replaceChildren();
  const present = new Set(state.attendance.filter(record => record.date === date && record.group === attendanceGroup).map(record => record.athleteId));
  athletes.forEach(athlete => {
    const person = document.createElement("label"); person.className = `attendance-person${present.has(athlete.id) ? " checked" : ""}`;
    const check = document.createElement("input"); check.type = "checkbox"; check.checked = present.has(athlete.id); check.setAttribute("aria-label", `${athlete.name} present`);
    const name = document.createElement("span"); name.textContent = athlete.name;
    check.addEventListener("change", async () => {
      check.disabled = true;
      state.attendance = state.attendance.filter(record => !(record.date === date && record.athleteId === athlete.id));
      if (check.checked) state.attendance.push({date, group: attendanceGroup, athleteId: athlete.id, checkedAt: new Date().toISOString()});
      person.classList.toggle("checked", check.checked);
      const count = athletes.filter(item => state.attendance.some(record => record.date === date && record.group === attendanceGroup && record.athleteId === item.id)).length;
      $("#attendance-count").textContent = `${count} / ${athletes.length} present`;
      try {
        await saveAttendance(date, attendanceGroup, athlete.id, check.checked);
        $("#save-status").textContent = `Attendance saved · ${new Date().toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})}`;
      } catch (error) {
        await loadState(); renderAttendance(); $("#save-status").textContent = "Attendance save failed — board reloaded"; console.error(error);
      } finally {
        check.disabled = false;
      }
    });
    person.append(check, name); grid.append(person);
  });
  if (!athletes.length) { const empty = document.createElement("div"); empty.className = "attendance-empty"; empty.textContent = "No athletes are assigned to this group."; grid.append(empty); }
  $("#attendance-count").textContent = `${present.size} / ${athletes.length} present`;
  $$('[data-attendance-group]').forEach(button => button.classList.toggle("active", button.dataset.attendanceGroup === attendanceGroup));
}

function renderAll() {
  renderSide(); renderDashboard(); setupAssignInputs(); renderAssignments(); renderLiftManager(); renderResults(); renderReview(); renderRosterSetup(); renderRoster(); renderNewAthleteSports(); renderToday(); renderAttendance(); updateClock();
}

function bindEvents() {
  setupNavigation();
  $("#refresh").addEventListener("click", renderAll); $("#a-group").addEventListener("change", refreshAssignSports); $("#assign-workout").addEventListener("click", assignWorkout);
  $("#manage-lifts").addEventListener("click", () => $("#lift-manager").hidden = !$("#lift-manager").hidden); $("#close-lifts").addEventListener("click", () => $("#lift-manager").hidden = true);
  $("#open-lifts").addEventListener("click", event => { event.stopPropagation(); toggleLiftDropdown(); });
  $("#a-lift").addEventListener("keydown", event => { if (event.key === "ArrowDown" && $("#lift-dropdown").hidden) { event.preventDefault(); toggleLiftDropdown(true); } if (event.key === "Escape") toggleLiftDropdown(false); });
  document.addEventListener("click", event => { if (!event.target.closest(".lift-picker")) toggleLiftDropdown(false); });
  ["#r-assignment", "#r-sport", "#r-status"].forEach(id => $(id).addEventListener("change", renderResults)); $("#save-results").addEventListener("click", saveResults);
  $("#lock-session").addEventListener("click", async () => { const a = state.assignments.find(x => x.id === $("#r-assignment").value); if (a) { a.locked = !a.locked; await saveState(); renderAll(); } });
  ["#v-group", "#v-sport", "#v-lift", "#v-status"].forEach(id => $(id).addEventListener("change", renderReview));
  $("#approve-visible").addEventListener("click", async () => { const visible = renderReview().filter(s => s.status === "pending"); visible.forEach(s => { const input = $(`[data-manual="${CSS.escape(s.id)}"]`); s.manualMax = Number(input?.value || s.suggestedMax); s.status = "approved"; const athlete = state.athletes.find(a => a.id === s.athleteId); if (athlete) { athlete.maxes ||= {}; athlete.maxes[s.lift] = s.manualMax; } }); await saveState(); renderAll(); });
  $("#add-athlete").addEventListener("click", () => $("#athlete-form").hidden = !$("#athlete-form").hidden); $("#save-athlete").addEventListener("click", addAthlete);
  $("#add-class").addEventListener("click", addClassGroup); $("#add-sport").addEventListener("click", addSport); $("#add-sub").addEventListener("click", addSportGroup); $("#sub-sport").addEventListener("change", renderRosterSetup);
  $("#edit-roster").addEventListener("click", () => { rosterEditing = !rosterEditing; selectedAthletes.clear(); $("#edit-roster").textContent = rosterEditing ? "Done editing" : "Edit roster"; renderRoster(); });
  $("#remove-athletes").addEventListener("click", removeSelectedAthletes); $("#select-all").addEventListener("change", event => { visibleRosterAthletes().forEach(a => event.target.checked ? selectedAthletes.add(a.id) : selectedAthletes.delete(a.id)); renderRoster(); $("#select-all").checked = event.target.checked; });
  $("#f-sport").addEventListener("change", () => { $("#f-sub").value = "all"; selectedAthletes.clear(); renderRoster(); }); ["#f-class", "#f-sub"].forEach(id => $(id).addEventListener("change", () => { selectedAthletes.clear(); renderRoster(); })); $("#f-search").addEventListener("input", renderRoster);
  $("#tv-date").addEventListener("change", renderToday); $("#tv-refresh").addEventListener("click", renderToday);
  $("#attendance-date").value = today(); $("#attendance-date").addEventListener("change", renderAttendance);
  $$('[data-attendance-group]').forEach(button => button.addEventListener("click", () => { attendanceGroup = button.dataset.attendanceGroup; renderAttendance(); }));
  $$('[data-tv-mode]').forEach(button => button.addEventListener("click", () => { tvMode = button.dataset.tvMode; $$('[data-tv-mode]').forEach(b => b.classList.toggle("active", b === button)); renderToday(); }));
  $("#tv-fullscreen").addEventListener("click", async () => { const panel = $('[data-panel="today"]'); document.fullscreenElement ? await document.exitFullscreen() : await panel.requestFullscreen(); });
  document.addEventListener("fullscreenchange", () => { const panel = $('[data-panel="today"]'); panel.classList.toggle("tv-board-full", Boolean(document.fullscreenElement)); $("#tv-fullscreen").textContent = document.fullscreenElement ? "Exit TV mode" : "Enter TV mode"; });
  setInterval(updateClock, 30000);
  window.addEventListener("resize", renderAttendance);
}

loadState().then(() => { $("#tv-date").value = today(); bindEvents(); renderAll(); app.hidden = false; $("#save-status").textContent = "Encrypted server data loaded"; }).catch(() => { fatal.hidden = false; });
