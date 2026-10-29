// Red Herring front end. All AI text is put on the page with textContent,
// never innerHTML, so nothing the model writes can inject markup.

const $ = (sel) => document.querySelector(sel);

const state = {
  game: null,
  selected: null,
  busy: false,
  token: null, // the encrypted game the server hands back after every action
};

const AVATAR_COLORS = ["#d9a441", "#8fb3a3", "#c98b7a", "#a3a0c9", "#b6c27a"];
const LOADING_LINES = [
  "Writing your case…",
  "Dusting for fingerprints…",
  "Interviewing the butler…",
  "Hiding the murder weapon…",
  "Giving everyone a motive…",
  "Breaking one alibi on purpose…",
];
const QUESTION_CHIPS = [
  "Where were you when it happened?",
  "How did you know the victim?",
  "Did anyone have a reason to want this?",
  "Who do you suspect?",
];

// ------------------------------------------------------------ api

async function api(path, payload, timeoutMs = 45000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let resp;
  try {
    resp = await fetch(path, {
      method: payload === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(state.token && !payload.token ? { ...payload, token: state.token } : payload),
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw new Error("That took too long. Try again.");
    throw new Error("Can't reach the game server. Is app.py still running?");
  } finally {
    clearTimeout(timer);
  }

  let data = null;
  try {
    data = await resp.json();
  } catch {
    // fall through to the generic message below
  }
  if (!resp.ok) throw new Error((data && data.error) || `Server error (${resp.status}).`);
  if (!data) throw new Error("The server sent back something unreadable.");
  if (data.token) saveToken(data.token);
  return data;
}

function saveToken(token) {
  state.token = token;
  try {
    if (token) localStorage.setItem("redherring:token", token);
    else localStorage.removeItem("redherring:token");
  } catch {}
}

// ------------------------------------------------------------ helpers

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

function toast(message, kind = "info", ms = 5000) {
  const t = el("div", { class: `toast ${kind}`, text: message });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

function show(screen) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
  $(`#screen-${screen}`).classList.add("active");
  window.scrollTo(0, 0);
}

function initials(name) {
  return name
    .replace(/^(dr|mr|mrs|ms|miss|prof|professor|sir|lady|lord)\.?\s+/i, "")
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
}

function avatar(suspect, index) {
  const a = el("div", { class: "avatar", text: initials(suspect.name) });
  a.style.background = AVATAR_COLORS[index % AVATAR_COLORS.length];
  return a;
}

function setBusy(on) {
  state.busy = on;
  const over = state.game && state.game.over;
  const left = state.game ? state.game.actions_left : 0;
  $("#ask-input").disabled = on || over || left < 1;
  $("#ask-form button").disabled = on || over || left < 1;
  $("#btn-hint").disabled = on || over || left < state.game?.costs.hint;
  $("#btn-accuse").disabled = on || over;
  document.querySelectorAll(".location").forEach((b) => {
    b.disabled = on || over || (left < 1 && !b.classList.contains("searched"));
  });
}

// ------------------------------------------------------------ start

async function checkStatus() {
  const pill = $("#ai-status");
  try {
    const s = await api("/api/status", undefined, 8000);
    if (s.ai_enabled) {
      pill.textContent = `AI online · Groq ${s.model}`;
      pill.className = "status-pill on";
    } else {
      pill.textContent = "Offline mode · no Groq key, built-in case only";
      pill.className = "status-pill off";
    }
  } catch (err) {
    pill.textContent = err.message;
    pill.className = "status-pill off";
  }
}

$("#theme-select").addEventListener("change", (e) => {
  const custom = $("#theme-custom");
  custom.hidden = e.target.value !== "custom";
  if (!custom.hidden) custom.focus();
});

$("#start-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  let theme = $("#theme-select").value;
  if (theme === "custom") {
    theme = $("#theme-custom").value.trim();
    if (theme.length < 3) {
      toast("Describe your setting in a few words first.", "error");
      $("#theme-custom").focus();
      return;
    }
  }
  const difficulty = document.querySelector("input[name=difficulty]:checked").value;

  show("loading");
  let i = 0;
  $("#loading-text").textContent = LOADING_LINES[0];
  const ticker = setInterval(() => {
    i = (i + 1) % LOADING_LINES.length;
    $("#loading-text").textContent = LOADING_LINES[i];
  }, 1800);

  try {
    // case generation can retry on the server, so give it longer
    const data = await api("/api/new-case", { theme, difficulty }, 120000);
    startGame(data.state);
    if (data.notice) toast(data.notice, "info", 8000);
  } catch (err) {
    show("start");
    toast(err.message, "error");
  } finally {
    clearInterval(ticker);
  }
});

// ------------------------------------------------------------ game

function startGame(game) {
  state.game = game;
  state.selected = null;

  $("#case-title").textContent = game.title;
  const badge = $("#source-badge");
  badge.textContent = game.source === "ai" ? "AI case" : "Offline case";
  badge.className = `badge ${game.source === "ai" ? "ai" : "offline"}`;
  $("#case-intro").textContent = game.intro;
  $("#case-setting").textContent = game.setting;
  $("#case-victim").textContent = `${game.victim.name}, ${game.victim.description}`;
  $("#case-crime").textContent = game.crime;
  $("#hint-box").replaceChildren();
  $("#hint-box").hidden = true;
  $("#brief").open = window.innerWidth > 760;

  $("#chat").hidden = true;
  $("#chat-empty").hidden = false;

  renderSuspects();
  renderLocations();
  renderClues();
  updateActions();
  show("game");

  if (game.over && game.result) showEnding(game.result);
}

function updateActions() {
  const left = state.game.actions_left;
  $("#actions-left").textContent = left;
  $(".meter").classList.toggle("low", left <= 3);
  setBusy(state.busy);
  if (left === 0 && !state.game.over) {
    toast("Out of actions. Time to make your accusation.", "info");
    openAccuse();
  }
}

function renderSuspects() {
  const list = $("#suspect-list");
  list.replaceChildren();
  state.game.suspects.forEach((s, i) => {
    const asked = state.game.transcript[s.id].filter((m) => m.from === "you").length;
    const btn = el(
      "button",
      { class: `suspect${state.selected === s.id ? " selected" : ""}`, onclick: () => selectSuspect(s.id) },
      avatar(s, i),
      el("span", {}, el("span", { class: "name", text: s.name }), el("span", { class: "role", text: s.role })),
      asked ? el("span", { class: "count", text: `${asked} asked` }) : null
    );
    list.append(el("li", {}, btn));
  });
}

function selectSuspect(id) {
  state.selected = id;
  const idx = state.game.suspects.findIndex((s) => s.id === id);
  const s = state.game.suspects[idx];

  $("#chat-empty").hidden = true;
  $("#chat").hidden = false;
  $("#chat-avatar").replaceWith(Object.assign(avatar(s, idx), { id: "chat-avatar" }));
  $("#chat-name").textContent = s.name;
  $("#chat-role").textContent = `${s.role} · ${s.personality}`;

  renderTranscript();
  renderChips();
  renderSuspects();
  if (window.innerWidth <= 760) $("#chat").scrollIntoView({ behavior: "smooth" });
  else $("#ask-input").focus();
}

function renderTranscript() {
  const log = $("#chat-log");
  log.replaceChildren();
  const msgs = state.game.transcript[state.selected];
  let lastMood = "";
  if (!msgs.length) {
    log.append(el("p", { class: "muted small", text: "They're waiting for your first question." }));
  }
  for (const m of msgs) {
    log.append(messageNode(m));
    if (m.mood) lastMood = m.mood;
  }
  setMood(lastMood);
  log.scrollTop = log.scrollHeight;
}

function messageNode(m) {
  const node = el("div", { class: `msg ${m.from}`, text: m.text });
  if (m.scripted) node.append(el("span", { class: "tag", text: "scripted reply (AI unavailable)" }));
  return node;
}

function setMood(mood) {
  const tag = $("#chat-mood");
  tag.textContent = mood || "";
  tag.className = `mood ${mood || ""}`;
}

function renderChips() {
  const chips = $("#chips");
  chips.replaceChildren();
  for (const q of QUESTION_CHIPS) {
    chips.append(el("button", { type: "button", class: "chip", text: q, onclick: () => fillQuestion(q) }));
  }
}

function fillQuestion(text) {
  const input = $("#ask-input");
  input.value = text.slice(0, 300);
  input.dispatchEvent(new Event("input"));
  input.focus();
}

$("#ask-input").addEventListener("input", (e) => {
  const n = e.target.value.length;
  const counter = $("#ask-count");
  counter.textContent = `${n} / 300`;
  counter.classList.toggle("over", n >= 300);
});

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.busy || !state.selected) return;
  const input = $("#ask-input");
  const question = input.value.trim();
  if (!question) {
    toast("Type a question first.", "error", 2500);
    return;
  }

  const suspectId = state.selected;
  const log = $("#chat-log");
  log.querySelector("p.muted")?.remove();
  log.append(messageNode({ from: "you", text: question }));
  const typing = el("div", { class: "msg typing" }, el("span", { text: "●" }), el("span", { text: "●" }), el("span", { text: "●" }));
  log.append(typing);
  log.scrollTop = log.scrollHeight;
  input.value = "";
  input.dispatchEvent(new Event("input"));
  setBusy(true);

  try {
    const data = await api("/api/ask", { case_id: state.game.id, suspect_id: suspectId, question });
    state.game.transcript[suspectId].push(
      { from: "you", text: question },
      { from: "them", text: data.reply, mood: data.mood, scripted: data.scripted }
    );
    state.game.actions_left = data.actions_left;
    if (data.notice) toast(data.notice, "info");
  } catch (err) {
    toast(err.message, "error");
    input.value = question; // give the question back so they can retry
    input.dispatchEvent(new Event("input"));
  } finally {
    typing.remove();
    setBusy(false);
    if (state.selected === suspectId) renderTranscript();
    renderSuspects();
    updateActions();
  }
});

function renderLocations() {
  const list = $("#location-list");
  list.replaceChildren();
  for (const loc of state.game.locations) {
    const btn = el(
      "button",
      { class: `location${loc.searched ? " searched" : ""}`, onclick: () => searchLocation(loc.name) },
      el("span", { class: "name" }, el("span", { text: loc.name })),
      el("span", { class: "desc", text: loc.description })
    );
    list.append(el("li", {}, btn));
  }
}

async function searchLocation(name) {
  if (state.busy) return;
  setBusy(true);
  try {
    const data = await api("/api/search", { case_id: state.game.id, location: name });
    state.game.actions_left = data.actions_left;
    const loc = state.game.locations.find((l) => l.name === name);
    if (loc) loc.searched = true;
    state.game.clues.push(...data.new_clues);
    renderLocations();
    renderClues(data.new_clues.map((c) => c.id));
    toast(data.message, "info", 3000);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
    updateActions();
  }
}

function renderClues(newIds = []) {
  const list = $("#clue-list");
  list.replaceChildren();
  for (const c of state.game.clues) {
    const item = el(
      "li",
      {
        class: `clue${newIds.includes(c.id) ? " new" : ""}`,
        title: "Click to ask the current suspect about this",
        onclick: () => {
          if (!state.selected) {
            toast("Pick a suspect first, then click a clue to confront them with it.", "info", 3500);
            return;
          }
          fillQuestion(`Explain this: ${c.text}`);
        },
      },
      el("span", { class: "where", text: c.location }),
      document.createTextNode(c.text)
    );
    list.append(item);
  }
  $("#clue-empty").hidden = state.game.clues.length > 0;
  $("#clue-count").textContent = state.game.clues.length ? `(${state.game.clues.length})` : "";
}

$("#btn-hint").addEventListener("click", async () => {
  if (state.busy) return;
  setBusy(true);
  try {
    const data = await api("/api/hint", { case_id: state.game.id });
    state.game.actions_left = data.actions_left;
    const box = $("#hint-box");
    box.hidden = false;
    box.append(el("p", {}, el("strong", { text: "Partner: " }), document.createTextNode(data.hint)));
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    if (data.notice) toast(data.notice, "info");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
    updateActions();
  }
});

// ------------------------------------------------------------ accuse

const dialog = $("#accuse-dialog");

function openAccuse() {
  const opts = $("#accuse-options");
  opts.replaceChildren();
  for (const s of state.game.suspects) {
    opts.append(
      el("label", {}, el("input", { type: "radio", name: "accused", value: s.id }), el("span", { text: `${s.name}, ${s.role}` }))
    );
  }
  $("#accuse-error").hidden = true;
  $("#accuse-cancel").hidden = state.game.actions_left === 0;
  if (!dialog.open) dialog.showModal();
}

$("#btn-accuse").addEventListener("click", openAccuse);
$("#accuse-cancel").addEventListener("click", () => dialog.close());
dialog.addEventListener("cancel", (e) => {
  // with no actions left there's nothing to go back to
  if (state.game.actions_left === 0) e.preventDefault();
});

$("#accuse-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const picked = document.querySelector("input[name=accused]:checked");
  const err = $("#accuse-error");
  if (!picked) {
    err.textContent = "Choose who you're accusing.";
    err.hidden = false;
    return;
  }
  const submit = $("#accuse-submit");
  submit.disabled = true;
  submit.textContent = "Confronting…";
  try {
    const data = await api(
      "/api/accuse",
      { case_id: state.game.id, suspect_id: picked.value, reasoning: $("#accuse-reason").value },
      60000
    );
    state.game.over = true;
    state.game.result = data.result;
    dialog.close();
    $("#accuse-reason").value = "";
    if (data.notice) toast(data.notice, "info");
    showEnding(data.result);
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  } finally {
    submit.disabled = false;
    submit.textContent = "Accuse";
  }
});

function showEnding(r) {
  const stamp = $("#end-stamp");
  stamp.textContent = r.correct ? "CASE SOLVED" : "CASE UNSOLVED";
  stamp.className = `stamp ${r.correct ? "win" : "lose"}`;
  $("#end-title").textContent = r.correct ? `You got it. It was ${r.culprit}.` : `It wasn't ${r.accused}.`;
  $("#end-scene").textContent = r.scene;
  $("#end-feedback").textContent = r.feedback;
  $("#end-feedback").hidden = !r.feedback;
  $("#end-score").textContent = r.score;
  $("#end-clues").textContent = `${r.clues_found} / ${r.clues_total}`;
  $("#end-culprit").textContent = r.culprit;
  $("#end-solution").textContent = r.solution;
  show("end");
}

$("#btn-again").addEventListener("click", () => {
  saveToken(null);
  show("start");
  checkStatus();
});

// ------------------------------------------------------------ boot

async function resume() {
  let token = null;
  try { token = localStorage.getItem("redherring:token"); } catch {}
  if (!token) return;
  try {
    const data = await api("/api/resume", { token }, 8000);
    if (!data.state.over) startGame(data.state);
  } catch {
    // the saved case expired or the server's secret changed, just start fresh
    saveToken(null);
  }
}

checkStatus();
resume();
