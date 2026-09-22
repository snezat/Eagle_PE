import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const port = Number(process.env.ARC_PREVIEW_PORT || 8765);
const today = new Date();
const date = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
const actual = {Bench: 200, "Back Squat": 300, "Power Clean": 185};
const projected = {...actual};
const availableSports = ["Football", "Basketball", "Baseball", "Track & Field"];
let selectedSports = ["Football"];
const workouts = [
  {id: "preview-bench", date, lift: "Bench", percent: 75, sets: 2, reps: 5, expectedReps: 8, prescribedLoad: 150, projectedMaxUsed: 200, burnoutReps: null, submitted: false, locked: false, notes: "Final set is the burnout set."},
  {id: "preview-squat", date, lift: "Back Squat", percent: 75, sets: 2, reps: 5, expectedReps: 8, prescribedLoad: 225, projectedMaxUsed: 300, burnoutReps: null, submitted: false, locked: false, notes: "Final set is the burnout set."},
  {id: "preview-clean", date, lift: "Power Clean", percent: 65, sets: 2, reps: 5, expectedReps: 8, prescribedLoad: 120, projectedMaxUsed: 185, burnoutReps: null, submitted: false, locked: false, notes: "Final set is the burnout set."},
  {id: "preview-row", date, lift: "Dumbbell Row", percent: 60, sets: 3, reps: 10, expectedReps: 10, prescribedLoad: 50, projectedMaxUsed: null, burnoutReps: null, submitted: false, locked: false, notes: "Accessory lift."},
];

function html(file) {
  return fs.readFileSync(path.join(root, "templates", file), "utf8")
    .replaceAll("{{ csrf }}", "preview-token")
    .replace(/\{\{ url_for\('static', filename='([^']+)'\) \}\}/g, "/static/$1")
    .replace("{{ url_for('logout') }}", "/logout");
}

function loginPage(error = "") {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · ARCA Strength</title><link rel="stylesheet" href="/static/css/app.css"></head><body class="auth-page"><main class="auth-shell"><section class="auth-brand"><span class="eyebrow">Athletic PE planning</span><h1>ARCA Strength</h1><p>Secure strength programming and fast workout logging for your weight room.</p><div class="auth-lock">Coach and student access</div></section><section class="auth-card"><h2>Sign in</h2><p class="muted">Use the temporary student account.</p>${error ? `<div class="alert">${error}</div>` : ""}<form method="post" action="/login"><input type="hidden" name="csrf_token" value="preview-token"><label>Username<input name="username" autocomplete="username" required></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button class="btn primary full" type="submit">Sign in</button></form><p class="privacy-note">Preview server · student / test</p></section></main></body></html>`;
}

function send(response, status, body, type = "text/html; charset=utf-8", headers = {}) {
  response.writeHead(status, {"Content-Type": type, "Cache-Control": "no-store", ...headers});
  response.end(body);
}

function dashboard() {
  return {
    athlete: {name: "Student Test", grade: "Test", classGroup: "Student Test Group"},
    sports: {available: availableSports, selected: selectedSports},
    maxes: Object.keys(actual).map(lift => ({lift, actual: actual[lift], projected: projected[lift]})),
    today: workouts,
    history: workouts.filter(item => item.submitted),
  };
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url, `http://${request.headers.host}`);
  if (request.method === "GET" && url.pathname === "/") return send(response, 200, loginPage());
  if (request.method === "POST" && url.pathname === "/login") {
    let body = "";
    request.on("data", chunk => body += chunk);
    request.on("end", () => {
      const form = new URLSearchParams(body);
      if (form.get("username") === "student" && form.get("password") === "test") {
        return send(response, 302, "", "text/plain", {Location: "/student"});
      }
      return send(response, 401, loginPage("The username or password is not correct."));
    });
    return;
  }
  if (request.method === "POST" && url.pathname === "/logout") return send(response, 302, "", "text/plain", {Location: "/"});
  if (request.method === "GET" && url.pathname === "/student") return send(response, 200, html("student.html"));
  if (request.method === "GET" && url.pathname === "/api/student/dashboard") return send(response, 200, JSON.stringify(dashboard()), "application/json");
  if (request.method === "PUT" && url.pathname === "/api/student/sports") {
    let body = "";
    request.on("data", chunk => body += chunk);
    request.on("end", () => {
      const requested = JSON.parse(body || "{}").sports;
      if (!Array.isArray(requested) || requested.some(name => !availableSports.includes(name))) return send(response, 400, JSON.stringify({error: "Select only available sports."}), "application/json");
      selectedSports = [...new Set(requested)];
      return send(response, 200, JSON.stringify({ok: true, sports: selectedSports}), "application/json");
    });
    return;
  }
  if (request.method === "PUT" && url.pathname.startsWith("/api/student/workouts/")) {
    let body = "";
    request.on("data", chunk => body += chunk);
    request.on("end", () => {
      const id = decodeURIComponent(url.pathname.split("/").pop());
      const workout = workouts.find(item => item.id === id);
      const reps = Number(JSON.parse(body || "{}").burnoutReps);
      if (!workout || !Number.isInteger(reps) || reps < 0 || reps > 100) return send(response, 400, JSON.stringify({error: "Enter total burnout reps between 0 and 100."}), "application/json");
      workout.burnoutReps = reps;
      workout.submitted = true;
      projected[workout.lift] = Math.round(workout.prescribedLoad * (1 + reps / 30) / 5) * 5;
      return send(response, 200, JSON.stringify({ok: true, lift: workout.lift, actualMax: actual[workout.lift], projectedMax: projected[workout.lift]}), "application/json");
    });
    return;
  }
  if (request.method === "GET" && url.pathname.startsWith("/static/")) {
    const relative = url.pathname.slice("/static/".length);
    const file = path.resolve(root, "static", relative);
    const staticRoot = path.resolve(root, "static") + path.sep;
    if (!file.startsWith(staticRoot) || !fs.existsSync(file)) return send(response, 404, "Not found", "text/plain");
    const type = file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "text/javascript" : file.endsWith(".png") ? "image/png" : "application/octet-stream";
    return send(response, 200, fs.readFileSync(file), type);
  }
  return send(response, 404, "Not found", "text/plain");
});

server.listen(port, "127.0.0.1", () => console.log(`Student preview: http://127.0.0.1:${port}`));
