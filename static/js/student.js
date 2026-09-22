const csrf = document.querySelector('meta[name="csrf-token"]').content;
const state = { dashboard: null, date: localDate() };

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
  return normalized.includes("bench") || normalized.includes("squat") || normalized.includes("power clean");
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
  document.querySelector("#student-meta").textContent = [athlete.grade && `Grade ${athlete.grade}`, athlete.classGroup].filter(Boolean).join(" · ");
  document.querySelector("#today-label").textContent = new Intl.DateTimeFormat(undefined, {weekday: "long", month: "long", day: "numeric"}).format(new Date(`${state.date}T12:00:00`));
  const workouts = document.querySelector("#student-workouts");
  workouts.innerHTML = today.length ? today.map((item, index) => `
    <article class="student-workout-card ${item.submitted ? "complete" : ""}">
      <div class="student-workout-number">${String(index + 1).padStart(2, "0")}</div>
      <div class="student-workout-main"><span>${escapeHtml(item.lift)}</span><strong>${pounds(item.prescribedLoad)}</strong><small>${item.sets} sets × ${item.reps} reps · ${tracksBurnout(item.lift) ? `final-set target ${item.expectedReps}` : "no burnout entry needed"}</small></div>
      <div class="student-workout-status">${tracksBurnout(item.lift) && item.submitted ? `${item.burnoutReps} reps ✓` : `${item.percent}%`}</div>
    </article>`).join("") : '<div class="student-empty"><strong>No lifts assigned today</strong><span>Your coach’s assignments will appear here.</span></div>';
  const burnoutLifts = today.filter(item => tracksBurnout(item.lift));
  const editableBurnoutLifts = burnoutLifts.filter(item => !item.locked);
  const allBurnoutSubmitted = burnoutLifts.length > 0 && burnoutLifts.every(item => item.submitted);
  const openLog = document.querySelector("#open-log");
  openLog.hidden = burnoutLifts.length === 0;
  openLog.textContent = burnoutLifts.every(item => item.locked) ? "View lift results" : allBurnoutSubmitted ? "Review or edit lift results" : burnoutLifts.some(item => item.submitted) ? "Finish logging lifts" : "Log today’s lifts";
  document.querySelector("#student-log-list").innerHTML = burnoutLifts.map(item => `
    <label class="student-log-card">
      <span><strong>${escapeHtml(item.lift)}</strong><small>${pounds(item.prescribedLoad)} · target ${item.expectedReps} reps</small></span>
      <span class="student-reps-input"><input name="${escapeHtml(item.id)}" type="number" min="0" max="100" inputmode="numeric" ${item.locked ? "disabled" : "required"} value="${item.burnoutReps ?? ""}" aria-label="Total burnout reps for ${escapeHtml(item.lift)}"><em>reps</em></span>
    </label>`).join("") || '<div class="student-empty">No lifts to log today.</div>';
  const saveLifts = document.querySelector("#save-lifts");
  saveLifts.hidden = editableBurnoutLifts.length === 0;
  saveLifts.textContent = allBurnoutSubmitted ? "Update results" : "Save results";
  document.querySelector("#student-max-list").innerHTML = maxes.map(item => {
    const delta = item.actual != null && item.projected != null ? item.projected - item.actual : null;
    return `<article class="student-max-card"><label><span>${escapeHtml(item.lift)}</span><small>Recorded max</small><span class="student-max-field"><input data-max-lift="${escapeHtml(item.lift)}" type="number" min="1" max="5000" step="1" inputmode="numeric" value="${item.actual ?? ""}" placeholder="Enter max" aria-label="Recorded max for ${escapeHtml(item.lift)}"><em>lb</em></span></label><div class="student-max-arrow">→</div><div><span>Projected</span><small>${delta == null ? "Awaiting result" : `${delta >= 0 ? "+" : ""}${delta} lb`}</small><strong>${pounds(item.projected)}</strong></div></article>`;
  }).join("") || '<div class="student-empty">No maxes recorded yet.</div>';
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

function openView(name) {
  document.querySelectorAll("[data-student-view]").forEach(view => { view.hidden = view.dataset.studentView !== name; });
  document.querySelectorAll(".student-nav button").forEach(button => button.classList.toggle("active", button.dataset.openView === name));
  window.scrollTo({top: 0, behavior: "smooth"});
  if (name === "maxes" && state.dashboard) {
    loadDashboard().catch(error => { document.querySelector("#student-sports-message").textContent = error.message; });
  }
}

document.addEventListener("click", event => {
  const trigger = event.target.closest("[data-open-view]");
  if (trigger) openView(trigger.dataset.openView);
});
document.querySelector("#open-log").addEventListener("click", () => openView("log"));

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.dashboard) loadDashboard().catch(() => {});
});

document.querySelector("#student-log-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.querySelector("#student-form-message");
  const pending = state.dashboard.today.filter(item => tracksBurnout(item.lift) && !item.locked);
  message.textContent = "Saving…";
  try {
    const results = [];
    for (const item of pending) {
      const value = Number(form.elements[item.id].value);
      const response = await fetch(`/api/student/workouts/${encodeURIComponent(item.id)}`, {
        method: "PUT", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"},
        body: JSON.stringify({burnoutReps: value}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Could not save ${item.lift}`);
      results.push(result);
    }
    document.querySelector("#student-result-list").innerHTML = results.map(result => `<div><span>${escapeHtml(result.lift)}</span><strong>${pounds(result.projectedMax)}</strong></div>`).join("");
    document.querySelector("#student-result").hidden = false;
    message.textContent = "Results saved.";
    await loadDashboard();
  } catch (error) {
    message.textContent = error.message;
  }
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
  message.textContent = "Saving…";
  try {
    const response = await fetch("/api/student/maxes", {
      method: "PUT", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"},
      body: JSON.stringify({maxes}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save your recorded maxes");
    await loadDashboard();
    document.querySelector("#student-max-message").textContent = "Recorded maxes saved and synced with your coach.";
  } catch (error) {
    message.textContent = error.message;
  }
});

document.querySelector("#student-sports-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.querySelector("#student-sports-message");
  const sports = [...form.querySelectorAll('input[name="sports"]:checked')].map(input => input.value);
  message.textContent = "Saving…";
  try {
    const response = await fetch("/api/student/sports", {
      method: "PUT", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json"},
      body: JSON.stringify({sports, date: state.date}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save your sports");
    await loadDashboard();
    message.textContent = "Sports updated. Today’s assignments are refreshed.";
  } catch (error) {
    message.textContent = error.message;
  }
});

loadDashboard().catch(error => {
  document.querySelector("#student-workouts").innerHTML = `<div class="student-empty"><strong>Could not load</strong><span>${escapeHtml(error.message)}</span></div>`;
});
