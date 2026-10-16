const csrf = document.querySelector('meta[name="csrf-token"]').content;
const state = { dashboard: null, date: localDate(), repDrafts: {}, maxDrafts: {}, weightDrafts: {}, reportKey: null, reportId: null };
const GROUP_LABELS = {
  "Nonfootball Group A": "G:A - Track & Cross / Basketball",
  "Nonfootball Group B": "G:B - Baseball / Soccer",
};

function groupLabel(value) { return GROUP_LABELS[value] || value; }

function localDate() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function pounds(value) {
  return value == null ? "—" : `${Number(value).toLocaleString()} lb`;
}

function shortDate(value) {
  return new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", year: "numeric"}).format(new Date(`${value}T12:00:00`));
}

function performance(item) {
  const difference = Number(item.burnoutReps) - Number(item.expectedReps);
  if (difference === 0) return {tone: "met", label: "Met target"};
  if (difference > 0) return {tone: "above", label: `${difference} above target`};
  return {tone: "below", label: `${Math.abs(difference)} below target`};
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function tracksBurnout(lift) {
  const normalized = String(lift || "").trim().toLowerCase().replace(/\s+/g, " ");
  return ["bench", "benchpress", "squat", "backsquat", "powerclean"].includes(normalized.replace(/\s/g, ""));
}

function newReportId() {
  // getRandomValues also works on the trusted-LAN HTTP deployment.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function loadDashboard() {
  const response = await fetch(`/api/student/dashboard?date=${encodeURIComponent(state.date)}`, {headers: {Accept: "application/json"}});
  if (!response.ok) throw new Error((await response.json()).error || "Could not load your workout");
  state.dashboard = await response.json();
  render();
}

function render() {
  const {athlete, today, maxes, history, sports} = state.dashboard;
  document.querySelector("#student-name").textContent = athlete.name;
  document.querySelector("#student-meta").textContent = [athlete.grade && `Grade ${athlete.grade}`, groupLabel(athlete.classGroup)].filter(Boolean).join(" · ");
  document.querySelector("#today-label").textContent = new Intl.DateTimeFormat(undefined, {weekday: "long", month: "long", day: "numeric"}).format(new Date(`${state.date}T12:00:00`));
  const workouts = document.querySelector("#student-workouts");
  workouts.innerHTML = today.length ? today.map((item, index) => `
    <article class="student-workout-card ${item.submitted ? "complete" : ""}">
      <div class="student-workout-number">${String(index + 1).padStart(2, "0")}</div>
      <div class="student-workout-main"><span>${escapeHtml(item.lift)}</span><strong>${pounds(item.prescribedLoad)}</strong><small>${item.sets} sets × ${item.reps} reps · ${tracksBurnout(item.lift) ? `final-set target ${item.expectedReps}` : "no burnout entry needed"}</small></div>
      <div class="student-workout-status">${tracksBurnout(item.lift) && item.submitted ? `${item.burnoutReps} reps ✓` : `${item.percent}%`}</div>
    </article>`).join("") : '<div class="student-empty"><strong>No lifts assigned today</strong><span>Your coach’s assignments will appear here.</span></div>';
  const burnoutLifts = today.filter(item => tracksBurnout(item.lift));
  const allBurnoutSubmitted = burnoutLifts.length > 0 && burnoutLifts.every(item => item.submitted);
  const openLog = document.querySelector("#open-log");
  openLog.hidden = burnoutLifts.length === 0;
  openLog.textContent = burnoutLifts.every(item => item.locked) ? "View lift results" : allBurnoutSubmitted ? "Review or edit lift results" : burnoutLifts.some(item => item.submitted) ? "Finish logging lifts" : "Log today’s lifts";
  document.querySelector("#student-log-list").innerHTML = burnoutLifts.map(item => `
    <form class="student-log-card" data-log-id="${escapeHtml(item.id)}">
      <label><strong>${escapeHtml(item.lift)}</strong><small>${pounds(item.prescribedLoad)} · target ${item.expectedReps} reps</small>
        <span class="student-reps-input"><input name="burnoutReps" type="number" min="0" max="100" step="1" inputmode="numeric" ${item.locked ? "disabled" : "required"} value="${escapeHtml(state.repDrafts[item.id] ?? item.burnoutReps ?? "")}" aria-label="Total burnout reps for ${escapeHtml(item.lift)}"><em>reps</em></span>
      </label>
      <div class="student-lift-actions"><button class="student-secondary-action" type="submit" ${item.locked ? "disabled" : ""}>${item.locked ? "Locked" : item.submitted ? "Update lift" : "Save lift"}</button><span role="status">${item.submitted ? "Saved ✓" : ""}</span></div>
    </form>`).join("") || '<div class="student-empty">No lifts to log today.</div>';
  document.querySelector("#student-max-list").innerHTML = maxes.map(item => {
    const delta = item.actual != null && item.projected != null ? item.projected - item.actual : null;
    return `<article class="student-max-card"><label><span>${escapeHtml(item.lift)}</span><small>Recorded max</small><span class="student-max-field"><input data-max-lift="${escapeHtml(item.lift)}" type="number" min="1" max="5000" step="1" inputmode="numeric" value="${state.maxDrafts[item.lift] ?? item.actual ?? ""}" placeholder="Enter max" aria-label="Recorded max for ${escapeHtml(item.lift)}"><em>lb</em></span></label><div class="student-max-arrow">→</div><div><span>Projected</span><small>${delta == null ? "Awaiting result" : `${delta >= 0 ? "+" : ""}${delta} lb`}</small><strong>${pounds(item.projected)}</strong></div></article>`;
  }).join("") || '<div class="student-empty">No maxes recorded yet.</div>';
  document.querySelector("#student-last-weight-list").innerHTML = (state.dashboard.lastWeights || []).map(item => `
    <label class="student-weight-row"><span>${escapeHtml(item.lift)}</span><span class="student-max-field"><input data-weight-lift="${escapeHtml(item.lift)}" type="number" min="0" max="5000" step="0.5" inputmode="decimal" value="${state.weightDrafts[item.lift] ?? item.weight ?? ""}" placeholder="Weight" aria-label="Last weight used for ${escapeHtml(item.lift)}"><em>lb</em></span></label>`).join("") || '<p>No other lifts in your profile yet.</p>';
  document.querySelector("#student-sport-options").innerHTML = sports.available.length ? sports.available.map(sport => `
    <label><input type="checkbox" name="sports" value="${escapeHtml(sport)}" ${sports.selected.includes(sport) ? "checked" : ""}><span>${escapeHtml(sport)}</span></label>`).join("") : '<p class="student-empty-sports">No sports have been created by the coach yet.</p>';
  document.querySelector("#student-history").innerHTML = history.map(item => {
    const result = performance(item);
    return `<article class="student-history-card">
      <header><div><strong>${escapeHtml(item.lift)}</strong><time datetime="${escapeHtml(item.date)}">${shortDate(item.date)}</time></div><span class="student-history-result ${result.tone}">${result.label}</span></header>
      <div class="student-history-metrics">
        <div><span>Working weight</span><strong>${pounds(item.prescribedLoad)}</strong></div>
        <div><span>Burnout reps</span><strong>${item.burnoutReps}</strong></div>
        <div><span>Target reps</span><strong>${item.expectedReps}</strong></div>
      </div>
    </article>`;
  }).join("") || '<div class="student-empty">Your completed lifts will appear here.</div>';
}

async function openView(name) {
  state.desiredView = name;
  if (name === "maxes" && state.dashboard) {
    try { await loadDashboard(); }
    catch (error) { document.querySelector("#student-max-message").textContent = error.message; }
    if (state.desiredView !== name) return;
  }
  document.querySelectorAll("[data-student-view]").forEach(view => { view.hidden = view.dataset.studentView !== name; });
  document.querySelectorAll(".student-nav button").forEach(button => button.classList.toggle("active", button.dataset.openView === name));
  window.scrollTo({top: 0, behavior: "smooth"});
}

document.addEventListener("click", event => {
  const trigger = event.target.closest("[data-open-view]");
  if (trigger) openView(trigger.dataset.openView);
});
document.querySelector("#open-log").addEventListener("click", () => openView("log"));

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.dashboard && !document.querySelector('[data-student-view="log"]:not([hidden]), [data-student-view="maxes"]:not([hidden])')) loadDashboard().catch(() => {});
});

document.querySelector("#student-log-list").addEventListener("input", event => {
  const form = event.target.closest("[data-log-id]");
  if (form) state.repDrafts[form.dataset.logId] = event.target.value;
});

document.querySelector("#student-log-list").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const id = form.dataset.logId;
  if (!id || !form.reportValidity()) return;
  const button = form.querySelector("button");
  const message = form.querySelector('[role="status"]');
  const input = form.elements.burnoutReps;
  message.textContent = "Saving…";
  button.disabled = true;
  input.disabled = true;
  try {
    const response = await fetch(`/api/student/workouts/${encodeURIComponent(id)}`, {
      method: "PUT", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"},
      body: JSON.stringify({burnoutReps: Number(input.value)}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save this lift");
    delete state.repDrafts[id];
    document.querySelector("#student-result-list").innerHTML = `<div><span>${escapeHtml(result.lift)}</span><strong>${pounds(result.projectedMax)}</strong></div>`;
    document.querySelector("#student-result").hidden = false;
    await loadDashboard();
  } catch (error) { message.textContent = error.message; }
  finally { button.disabled = false; input.disabled = false; }
});

document.querySelector("#student-max-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.querySelector("#student-max-message");
  if (!form.reportValidity()) return;
  const maxes = {};
  form.querySelectorAll("[data-max-lift]").forEach(input => {
    maxes[input.dataset.maxLift] = input.value === "" ? null : Number(input.value);
  });
  const buttons = [...form.querySelectorAll("button, input")];
  buttons.forEach(button => button.disabled = true);
  message.textContent = "Saving…";
  try {
    const response = await fetch("/api/student/maxes", {
      method: "PUT", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"},
      body: JSON.stringify({maxes}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save your recorded maxes");
    state.maxDrafts = {};
    await loadDashboard();
    document.querySelector("#student-max-message").textContent = "Recorded maxes saved and synced with your coach.";
  } catch (error) {
    message.textContent = error.message;
  } finally { buttons.forEach(button => button.disabled = false); }
});

document.querySelector("#report-maxes").addEventListener("click", async () => {
  const form = document.querySelector("#student-max-form");
  const message = document.querySelector("#student-max-message");
  if (!form.reportValidity()) return;
  const inputs = [...form.querySelectorAll("[data-max-lift]")];
  if (inputs.some(input => input.value === "")) {
    message.textContent = "Enter all three maxes before reporting to your coach.";
    inputs.find(input => input.value === "").focus();
    return;
  }
  const payload = {maxes: Object.fromEntries(inputs.map(input => [input.dataset.maxLift, Number(input.value)])), block: document.querySelector("#student-report-block").value.trim()};
  const key = JSON.stringify(payload);
  if (state.reportKey !== key) { state.reportKey = key; state.reportId = newReportId(); }
  const buttons = [...form.querySelectorAll("button, input")];
  buttons.forEach(button => button.disabled = true);
  message.textContent = "Reporting to your coach…";
  try {
    const response = await fetch("/api/student/max-reports", {
      method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"},
      body: JSON.stringify({...payload, id: state.reportId}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not report your maxes");
    state.maxDrafts = {};
    await loadDashboard();
    message.textContent = "All three maxes reported to your coach and saved to your profile.";
    message.scrollIntoView({block: "center", behavior: "smooth"});
  } catch (error) { message.textContent = error.message; }
  finally { buttons.forEach(button => button.disabled = false); }
});

document.querySelector("#student-last-weight-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const message = document.querySelector("#student-weight-message");
  const button = form.querySelector("button");
  button.disabled = true;
  message.textContent = "Saving…";
  const weights = Object.fromEntries([...form.querySelectorAll("[data-weight-lift]")].map(input => [input.dataset.weightLift, input.value === "" ? null : Number(input.value)]));
  try {
    const response = await fetch("/api/student/last-weights", {
      method: "PUT", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"}, body: JSON.stringify({weights}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save last weights");
    state.weightDrafts = {};
    await loadDashboard();
    message.textContent = "Last weights saved.";
  } catch (error) { message.textContent = error.message; }
  finally { button.disabled = false; }
});

document.addEventListener("input", event => {
  const input = event.target;
  if (input.matches("[data-max-lift]")) state.maxDrafts[input.dataset.maxLift] = input.value;
  if (input.matches("[data-weight-lift]")) state.weightDrafts[input.dataset.weightLift] = input.value;
});

const sportsForm = document.querySelector("#student-sports-form");

sportsForm.addEventListener("change", event => {
  if (!event.target.matches('input[name="sports"]')) return;
  const message = document.querySelector("#student-sports-message");
  message.textContent = "Your sports changed. Submit now to refresh today’s workout.";
  if (window.confirm("Update your sports and refresh today’s workout plan now?")) sportsForm.requestSubmit();
});

sportsForm.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.querySelector("#student-sports-message");
  const button = form.querySelector('button[type="submit"]');
  const sports = [...form.querySelectorAll('input[name="sports"]:checked')].map(input => input.value);
  message.textContent = "Updating sports and today’s workout…";
  button.disabled = true;
  try {
    const response = await fetch("/api/student/sports", {
      method: "PUT", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"},
      body: JSON.stringify({sports, date: state.date}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save your sports");
    await loadDashboard();
    document.querySelector("#student-sports-message").textContent = "Sports updated. Today’s workout is refreshed.";
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

loadDashboard().catch(error => {
  document.querySelector("#student-workouts").innerHTML = `<div class="student-empty"><strong>Could not load</strong><span>${escapeHtml(error.message)}</span></div>`;
});
