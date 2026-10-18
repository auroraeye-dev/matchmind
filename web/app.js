// MatchMind match centre: one WebSocket, many personalised tickers.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const SVGNS = "http://www.w3.org/2000/svg";
  const ICON = {
    goal: "⚽", big_chance: "🎯", momentum_swing: "📈", pressure_spike: "🔥", pass_combo: "🔁", great_pass: "✨",
    chaos_phase: "🌪️", control_phase: "🧭", tempo_shift: "⏱️", milestone: "🏅", card: "🟨", substitution: "🔄", stat_check: "📊",
    half_time: "⏸️", full_time: "🏁",
  };
  const DEFAULT_PERSONAS = [
    { id: "analyst", label: "Studio analyst", mode: "analyst", language: "en", metric: "xg" },
    { id: "fan", label: "Home fan", mode: "casual", language: "en", metric: "possession", favorite_team_id: "__home" },
  ];

  let ws = null, meta = null, paused = false, lastScore = "0-0", sessionId = null, lastStats = null;
  let players = {}, pstats = {}, flowPts = [], shots = [], eventCount = 0;
  const panes = {};
  const agentCounts = {};
  const traceCounts = { ok: 0, llm: 0, fallback: 0, skipped: 0, error: 0 };

  // ---------------------------------------------------------------- crests
  function crest(team, flip) {
    const initials = team.short;
    const id = `g${team.id}`;
    return `<svg viewBox="0 0 84 96" aria-label="${esc(team.name)} crest">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${team.color}"/><stop offset="1" stop-color="${shade(team.color, -35)}"/></linearGradient></defs>
      <path d="M42 3 L80 14 V46 C80 70 62 86 42 93 C22 86 4 70 4 46 V14 Z" fill="url(#${id})" stroke="#fff" stroke-width="3"/>
      <path d="M${flip ? "62 14 L80 19 V46 C80 58 75 68 67 76 Z" : "22 14 L4 19 V46 C4 58 9 68 17 76 Z"}" fill="rgba(255,255,255,.18)"/>
      <rect x="4" y="40" width="76" height="12" fill="rgba(255,255,255,.9)"/>
      <text x="42" y="50" text-anchor="middle" font-family="Barlow Condensed, sans-serif" font-weight="800" font-size="12" fill="${team.color}" letter-spacing="1">EST. 2026</text>
      <text x="42" y="34" text-anchor="middle" font-family="Barlow Condensed, sans-serif" font-weight="800" font-size="22" fill="#fff" letter-spacing="1.5">${esc(initials)}</text>
      <circle cx="42" cy="70" r="7" fill="none" stroke="#fff" stroke-width="2.5"/><path d="M42 63 l3 5 -3 3 -3 -3 z" fill="#fff"/>
    </svg>`;
  }
  function shade(hex, pct) {
    const n = parseInt(hex.slice(1), 16);
    const f = (c) => Math.max(0, Math.min(255, Math.round(c + (pct / 100) * 255)));
    return `#${[(n >> 16) & 255, (n >> 8) & 255, n & 255].map(f).map((c) => c.toString(16).padStart(2, "0")).join("")}`;
  }

  // ---------------------------------------------------------------- setup
  function setupMatch(m) {
    meta = m;
    lastStats = null;
    players = {}; pstats = {}; flowPts = []; shots = []; eventCount = 0; lastScore = "0-0";
    for (const t of [m.home, m.away]) for (const p of t.players) {
      players[p.id] = { ...p, team: t };
      pstats[p.id] = { goals: 0, assists: 0, xg: 0, pc: 0, tk: 0, pr: 0, shots: 0, touches: 0 };
    }
    const root = document.documentElement.style;
    root.setProperty("--home", m.home.color);
    root.setProperty("--away", m.away.color);
    $("#competition").textContent = m.competition;
    $("#venue").textContent = m.venue;
    $("#weather").textContent = m.weather;
    $("#homeCrest").innerHTML = crest(m.home, false);
    $("#awayCrest").innerHTML = crest(m.away, true);
    for (const [k, t] of [["home", m.home], ["away", m.away]]) {
      $(`#${k}Name`).textContent = t.name;
      $(`#${k}Scorers`).innerHTML = "";
      $(`#lg${k === "home" ? "Home" : "Away"}`).textContent = t.short;
      $(`#stat${k === "home" ? "Home" : "Away"}`).textContent = t.name;
      $(`#lu${k === "home" ? "Home" : "Away"}`).textContent = t.name;
    }
    $("#homeScore").textContent = "0"; $("#awayScore").textContent = "0";
    $("#clock").textContent = "Pre-match"; $("#liveDot").classList.remove("on");
    $("#xgLine").textContent = "xG 0.00 – 0.00";
    $("#flowHome").setAttribute("d", ""); $("#flowAway").setAttribute("d", ""); $("#flowMarks").innerHTML = "";
    $("#trail").innerHTML = ""; $("#tags").innerHTML = ""; $("#shotLayer").innerHTML = "";
    $("#traceList").innerHTML = "";
    Object.keys(traceCounts).forEach((k) => (traceCounts[k] = 0));
    $$(".stage").forEach((s) => { agentCounts[s.dataset.agent] = 0; $("em", s).textContent = "0"; });
    renderLineups(); renderStats(null); renderPerformers();
    buildTickers();
    legend();
    $("#exportBtn").disabled = true;
    X("reset");
  }

  // ---------------------------------------------------------------- tickers
  function buildTickers() {
    const host = $("#tickers");
    host.innerHTML = "";
    const { home, away } = meta;
    for (const def of DEFAULT_PERSONAS) {
      const p = { ...def };
      if (p.favorite_team_id === "__home") p.favorite_team_id = home.id;
      const el = $("#tickerTpl").content.firstElementChild.cloneNode(true);
      $(".t-label", el).value = p.label;
      $(".p-mode", el).value = p.mode;
      $(".p-lang", el).value = p.language;
      $(".p-metric", el).value = p.metric;
      const team = $(".p-team", el);
      for (const t of [home, away]) team.add(new Option(t.name, t.id));
      team.value = p.favorite_team_id || "";
      fillPlayers($(".p-player", el), team.value);
      el.addEventListener("change", (e) => {
        if (e.target.classList.contains("p-team")) fillPlayers($(".p-player", el), e.target.value);
        if (e.target.classList.contains("p-player")) markFollowed();
        sendPersona(p.id);
      });
      $(".t-label", el).addEventListener("input", () => sendPersona(p.id));
      host.appendChild(el);
      panes[p.id] = { el, persona: p, count: 0, timer: null };
      decorate(p.id);
    }
  }
  function fillPlayers(sel, teamId) {
    const cur = sel.value;
    sel.innerHTML = '<option value="">No player</option>';
    for (const t of [meta.home, meta.away]) {
      if (teamId && t.id !== teamId) continue;
      for (const pl of t.players) sel.add(new Option(`#${pl.number} ${pl.name}`, pl.id));
    }
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }
  function readPersona(id) {
    const { el, persona } = panes[id];
    return {
      id: persona.id, label: $(".t-label", el).value, mode: $(".p-mode", el).value,
      favorite_team_id: $(".p-team", el).value || null, focus_player_id: $(".p-player", el).value || null,
      language: $(".p-lang", el).value, metric: $(".p-metric", el).value,
    };
  }
  function sendPersona(id) {
    decorate(id);
    if (ws?.readyState === 1) {
      panes[id].el.classList.add("applying");
      ws.send(JSON.stringify({ type: "persona", persona: readPersona(id) }));
    }
  }

  // ---------------------------------------------------------------- lineups
  function renderLineups() {
    for (const [k, t] of [["Home", meta.home], ["Away", meta.away]]) {
      const ol = $(`#lu${k}List`);
      const starters = t.players.filter((p) => p.starter), bench = t.players.filter((p) => !p.starter);
      const row = (p, cls = "") => `<li data-pid="${esc(p.id)}" class="${cls}"><span class="num" style="background:${t.color}">${p.number}</span><span>${esc(p.name)}</span><span class="pos">${esc(p.position)}</span></li>`;
      ol.innerHTML = starters.map((p) => row(p)).join("") + `<li class="sep">Substitutes</li>` + bench.map((p) => row(p, "bench")).join("");
    }
    markFollowed();
  }
  $("#lineups").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-pid]");
    if (!li) return;
    const pid = li.dataset.pid;
    for (const id of Object.keys(panes)) {
      const { el } = panes[id];
      const team = $(".p-team", el);
      if (team.value && team.value !== players[pid].team.id) team.value = "";
      fillPlayers($(".p-player", el), team.value);
      $(".p-player", el).value = pid;
      sendPersona(id);
    }
    markFollowed();
  });
  function markFollowed() {
    const followed = new Set(Object.values(panes).map(({ el }) => $(".p-player", el).value).filter(Boolean));
    $$("#lineups li[data-pid]").forEach((li) => li.classList.toggle("followed", followed.has(li.dataset.pid)));
  }

  // ---------------------------------------------------------------- stream
  async function start() {
    if (ws) { ws.onclose = null; ws.close(); }
    const seed = Number($("#seed").value) || 7;
    const m = await fetch(`/api/match?seed=${seed}`).then((r) => r.json());
    setupMatch(m);
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`);
    ws.onopen = () => {
      ws.send(JSON.stringify({ seed, speed: Number($("#speed").value), feed: $("#feed").value, personas: Object.keys(panes).map(readPersona) }));
      $("#pauseBtn").disabled = false;
      $("#startBtn").textContent = "Restart";
      $("#liveDot").classList.add("on");
    };
    ws.onmessage = (e) => handle(JSON.parse(e.data));
    ws.onclose = () => { $("#pauseBtn").disabled = true; $("#liveDot").classList.remove("on"); };
  }
  function handle(msg) {
    if (msg.type === "event") { onEvent(msg); X("onEvent", msg); }
    else if (msg.type === "overlay") { onOverlay(msg.overlay); X("onOverlay", msg.overlay); }
    else if (msg.type === "moment") X("onMoment", msg.moment);
    else if (msg.type === "persona_applied") onPersonaApplied(msg);
    else if (msg.type === "trace") onTrace(msg.trace);
    else if (msg.type === "meta") {
      setLlm(msg.llm);
      if (msg.session_id) { sessionId = msg.session_id; $("#exportBtn").disabled = false; }
    }
    else if (msg.type === "end") { setLlm(msg.llm); $("#clock").textContent = "Full-time"; $("#liveDot").classList.remove("on"); $("#eventLine").textContent = "Full-time. Pick another seed and press Restart."; }
  }
  function setLlm(s) {
    const chip = $("#llmChip");
    chip.textContent = `AI · ${s.provider}${s.breaker_open ? " · breaker open" : ""}`;
    chip.classList.toggle("live", s.provider !== "offline" && !s.breaker_open);
  }

  // ---------------------------------------------------------------- events
  function clockMinutes(clock) {
    const m = /^(\d+)(?:\+(\d+))?:(\d+)/.exec(clock);
    return m ? Number(m[1]) + Number(m[2] || 0) + Number(m[3]) / 60 : 0;
  }
  function onEvent({ event: ev, derived, stats }) {
    eventCount += 1;
    const h = meta.home.id;
    $("#clock").textContent = ev.clock;
    const sc = `${stats.score[h]}-${stats.score[meta.away.id]}`;
    $("#homeScore").textContent = stats.score[h];
    $("#awayScore").textContent = stats.score[meta.away.id];
    if (sc !== lastScore) { lastScore = sc; const s = $(".score"); s.classList.remove("flash"); void s.offsetWidth; s.classList.add("flash"); }
    $("#xgLine").textContent = `xG ${stats.teams[h].xg.toFixed(2)} – ${stats.teams[meta.away.id].xg.toFixed(2)}`;

    track(ev);
    drawBall(ev);
    flow(ev, stats.momentum);
    if (ev.type === "shot") addShot(ev);
    if (ev.type === "goal") addScorer(ev);
    if (ev.type === "substitution") subLineup(ev);

    const who = players[ev.player_id]?.name || "";
    const extra = derived.pass_difficulty != null ? ` · ${derived.pass_length_m} m · difficulty ${derived.pass_difficulty}` : ev.xg != null ? ` · xG ${ev.xg}` : ev.ball_speed_kmh ? ` · ${ev.ball_speed_kmh.toFixed(0)} km/h` : "";
    $("#eventLine").textContent = `${ev.clock}  ${ev.type.replace(/_/g, " ")}${ev.outcome ? ` (${ev.outcome.replace("_", " ")})` : ""} · ${who}${extra}${ev.extra?.detected ? " · auto-detected" : ""}`;

    const chaos = stats.chaos;
    $("#chaosFill").style.left = `calc(${chaos * 100}% - 2px)`;
    $("#chaosVal").textContent = chaos < 0.35 ? `Controlled ${chaos.toFixed(2)}` : chaos < 0.65 ? `Open ${chaos.toFixed(2)}` : `Chaotic ${chaos.toFixed(2)}`;
    $("#rhythmVal").textContent = `${stats.rhythm_epm.toFixed(0)} actions/min`;
    const ph = stats.possession_pct[h] ?? 50, p5 = stats.possession_5m[h] ?? 50;
    $("#possFill").style.width = `${ph}%`; $("#possVal").textContent = `${ph.toFixed(0)} – ${(100 - ph).toFixed(0)}`;
    $("#poss5Fill").style.width = `${p5}%`; $("#poss5Val").textContent = `${p5.toFixed(0)} – ${(100 - p5).toFixed(0)}`;
    lastStats = stats;
    if (eventCount % 6 === 0 || ev.type === "goal" || ev.type === "shot") {
      renderStats(stats);
      for (const id of Object.keys(panes)) metricBar(id, stats);
    }
    if (eventCount % 25 === 0 || ev.type === "goal") renderPerformers();
  }

  function track(ev) {
    const s = pstats[ev.player_id];
    if (!s) return;
    s.touches += 1;
    if (ev.type === "pass" && ev.outcome === "success") s.pc += 1;
    if (ev.type === "shot") { s.shots += 1; s.xg += ev.xg || 0; }
    if (ev.type === "goal") { s.goals += 1; if (pstats[ev.related_player_id]) pstats[ev.related_player_id].assists += 1; }
    if (ev.type === "tackle" && ev.outcome === "won") s.tk += 1;
    if (ev.type === "pressure") s.pr += 1;
  }

  function drawBall(ev) {
    if (ev.x == null) return;
    const ball = $("#ball");
    ball.setAttribute("cx", ev.end_x ?? ev.x); ball.setAttribute("cy", ev.end_y ?? ev.y);
    if (ev.end_x == null || !["pass", "carry", "shot"].includes(ev.type)) return;
    const ln = document.createElementNS(SVGNS, "line");
    ln.setAttribute("x1", ev.x); ln.setAttribute("y1", ev.y); ln.setAttribute("x2", ev.end_x); ln.setAttribute("y2", ev.end_y);
    ln.setAttribute("stroke", ev.team_id === meta.home.id ? "#fff" : "#ffd84d");
    if (ev.type === "carry") ln.setAttribute("stroke-dasharray", ".9 .9");
    else if (ev.outcome !== "success" && ev.type !== "shot") ln.setAttribute("stroke-dasharray", ".4 .7");
    const trail = $("#trail");
    trail.appendChild(ln);
    while (trail.childNodes.length > 10) trail.firstChild.remove();
    [...trail.childNodes].forEach((n, i, arr) => n.setAttribute("opacity", ((i + 1) / arr.length).toFixed(2)));
  }

  // ---------------------------------------------------------------- momentum flow
  function flowX(ev) {
    const mins = clockMinutes(ev.clock);
    return ev.period === 1 ? Math.min(mins, 48) / 48 * 480 : 480 + Math.min(Math.max(mins - 45, 0), 50) / 50 * 480;
  }
  function flow(ev, mom) {
    const x = flowX(ev);
    const last = flowPts[flowPts.length - 1];
    if (last && x - last[0] < 3) { last[1] = mom; } else flowPts.push([x, mom]);
    const area = (sign) => {
      if (!flowPts.length) return "";
      let d = `M${flowPts[0][0].toFixed(1)} 60`;
      for (const [px, m] of flowPts) d += ` L${px.toFixed(1)} ${(60 - sign * Math.max(sign * m, 0) * 56).toFixed(1)}`;
      return d + ` L${flowPts[flowPts.length - 1][0].toFixed(1)} 60 Z`;
    };
    $("#flowHome").setAttribute("d", area(1));
    $("#flowAway").setAttribute("d", area(-1));
  }

  // ---------------------------------------------------------------- shots / scorers / lineup updates
  function addShot(ev) {
    shots.push(ev);
    const c = document.createElementNS(SVGNS, "circle");
    c.setAttribute("cx", ev.x); c.setAttribute("cy", ev.y); c.setAttribute("r", (0.7 + (ev.xg || 0) * 3.2).toFixed(2));
    c.setAttribute("fill", ev.team_id === meta.home.id ? meta.home.color : meta.away.color);
    c.setAttribute("class", `shot ${ev.outcome === "goal" ? "shot-goal" : ev.outcome === "saved" ? "" : "miss"}`);
    const title = document.createElementNS(SVGNS, "title");
    title.textContent = `${ev.clock} ${players[ev.player_id]?.name} · xG ${ev.xg} · ${ev.outcome}`;
    c.appendChild(title);
    $("#shotLayer").appendChild(c);
    legend();
  }
  function addScorer(ev) {
    const side = ev.team_id === meta.home.id ? "home" : "away";
    const li = document.createElement("li");
    li.textContent = `${players[ev.player_id]?.name ?? ""} ${ev.clock.split(":")[0]}'`;
    $(`#${side}Scorers`).appendChild(li);
  }
  function subLineup(ev) {
    $(`#lineups li[data-pid="${CSS.escape(ev.related_player_id)}"]`)?.classList.add("off");
    const on = $(`#lineups li[data-pid="${CSS.escape(ev.player_id)}"]`);
    if (on) { on.classList.remove("bench"); on.querySelector("span:nth-child(2)").textContent = `🔼 ${players[ev.player_id].name}`; }
  }
  function legend() {
    const view = $(".tab.active").dataset.view;
    if (view === "heat") {
      return; // extras.js owns the heatmap legend (it has a selector)
    } else if (view === "shots") {
      const sum = (tid) => shots.filter((s) => s.team_id === tid);
      const h = sum(meta.home.id), a = sum(meta.away.id);
      $("#pitchLegend").innerHTML = `<span><i style="background:${meta.home.color}"></i>${esc(meta.home.short)} ${h.length} shots · ${h.reduce((x, s) => x + s.xg, 0).toFixed(2)} xG</span>
        <span><i style="background:${meta.away.color}"></i>${esc(meta.away.short)} ${a.length} shots · ${a.reduce((x, s) => x + s.xg, 0).toFixed(2)} xG</span><span>Bubble size = chance quality · gold ring = goal</span>`;
    } else {
      $("#pitchLegend").innerHTML = `<span><i style="background:#fff"></i>${esc(meta.home.short)} ball path</span><span><i style="background:#ffd84d"></i>${esc(meta.away.short)} ball path</span><span>Dashed = carry · dotted = failed pass</span>`;
    }
  }
  $$(".tab").forEach((b) => b.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.toggle("active", t === b));
    $("#liveLayer").style.display = b.dataset.view === "live" ? "" : "none";
    $("#shotLayer").style.display = b.dataset.view === "shots" ? "" : "none";
    $("#heatLayer").style.display = b.dataset.view === "heat" ? "" : "none";
    X("onTab", b.dataset.view);
    legend();
  }));

  // ---------------------------------------------------------------- stats & performers
  function renderStats(stats) {
    const empty = { goals: 0, xg: 0, shots: 0, on_target: 0, possession: 50, pass_accuracy: 0, passes: 0, final_third_entries: 0, pressures: 0, tackles_won: 0, interceptions: 0, corners: 0, fouls: 0, yellow_cards: 0 };
    const H = stats ? { ...stats.teams[meta.home.id], possession: stats.possession_pct[meta.home.id] } : empty;
    const A = stats ? { ...stats.teams[meta.away.id], possession: stats.possession_pct[meta.away.id] } : empty;
    const rows = [
      ["Possession", H.possession, A.possession, (v) => `${Number(v).toFixed(0)}%`],
      ["Expected goals (xG)", H.xg, A.xg, (v) => Number(v).toFixed(2)],
      ["Shots", H.shots, A.shots], ["Shots on target", H.on_target, A.on_target],
      ["Pass accuracy", H.pass_accuracy * 100, A.pass_accuracy * 100, (v) => `${Number(v).toFixed(0)}%`],
      ["Passes", H.passes, A.passes], ["Final-third entries", H.final_third_entries, A.final_third_entries],
      ["Pressures", H.pressures, A.pressures], ["Tackles won", H.tackles_won, A.tackles_won],
      ["Interceptions", H.interceptions, A.interceptions], ["Corners", H.corners, A.corners], ["Fouls", H.fouls, A.fouls],
      ["PPDA · lower = harder press", H.ppda ?? 0, A.ppda ?? 0, (v) => Number(v).toFixed(1)],
      ["Fastest shot (km/h)", H.fastest_shot_kmh || 0, A.fastest_shot_kmh || 0, (v) => Number(v).toFixed(0)],
      ["Top sprint (km/h)", H.top_sprint_kmh || 0, A.top_sprint_kmh || 0, (v) => Number(v).toFixed(1)],
      ["Hardest pass completed", H.hardest_pass_difficulty || 0, A.hardest_pass_difficulty || 0, (v) => Number(v).toFixed(2)],
    ];
    $("#statBars").innerHTML = rows.map(([label, h, a, f = (v) => v]) => {
      const tot = Number(h) + Number(a) || 1;
      return `<div class="sb"><div class="sb-top"><b>${f(h)}</b><span>${label}</span><b>${f(a)}</b></div>
        <div class="sb-bars"><div><i style="width:${(100 * h) / tot}%"></i></div><div><i style="width:${(100 * a) / tot}%"></i></div></div></div>`;
    }).join("");
  }
  function rating(s) {
    return Math.min(10, 6 + s.goals * 1.1 + s.assists * 0.7 + s.xg * 0.8 + s.pc * 0.012 + s.tk * 0.18 + s.pr * 0.03);
  }
  function renderPerformers() {
    const list = Object.entries(pstats).filter(([, s]) => s.touches > 5).map(([pid, s]) => [pid, s, rating(s)]).sort((a, b) => b[2] - a[2]).slice(0, 6);
    $("#performers").innerHTML = list.length ? list.map(([pid, s, r]) => {
      const p = players[pid];
      const line = [s.goals && `${s.goals} G`, s.assists && `${s.assists} A`, s.xg > 0.05 && `${s.xg.toFixed(2)} xG`, `${s.pc} passes`, s.tk && `${s.tk} tackles`].filter(Boolean).join(" · ");
      return `<li><div><div class="pname">${esc(p.name)}</div><div class="pteam">#${p.number} ${esc(p.position)} · ${esc(p.team.short)} · ${line}</div></div><span class="rating ${r >= 8 ? "hot" : ""}">${r.toFixed(1)}</span></li>`;
    }).join("") : `<li class="empty">Kick off to see live ratings.</li>`;
  }

  // ---------------------------------------------------------------- overlays
  function onOverlay(ov) {
    if (ov.kind === "player_tag") return showTag(ov);
    const pane = panes[ov.persona_id];
    if (!pane) return;
    const el = pane.el, type = ov.data.moment_type || ov.kind;
    pane.count += 1;
    $(".t-count", el).textContent = pane.count;

    const onair = $(".onair", el);
    onair.classList.remove("idle", "goal"); void onair.offsetWidth;
    if (type === "goal") onair.classList.add("goal");
    $(".onair-meta", onair).textContent = `${ov.clock} · ${type.replace(/_/g, " ")}${ov.source === "llm" ? " · AI" : ""}`;
    $("h4", onair).textContent = ov.title;
    $("p", onair).textContent = ov.body;
    $(".rows", onair).innerHTML = (ov.data.rows || []).map((r) => `<span>${esc(r.home)}</span><span>${esc(r.label)}</span><span>${esc(r.away)}</span>`).join("");
    onair.dir = ov.language === "ar" ? "rtl" : "ltr";
    clearTimeout(pane.timer);
    pane.timer = setTimeout(() => onair.classList.add("idle"), ov.display_ms);

    addFeedItem(pane, ov);
  }

  function addFeedItem(pane, ov) {
    const el = pane.el, type = ov.data.moment_type || ov.kind;
    const p = readPersona(pane.persona.id);
    const li = document.createElement("li");
    li.className = [type === "goal" && "goal", ov.data.favorite && "fav", ov.data.restyled && "restyled"].filter(Boolean).join(" ");
    li.dir = ov.language === "ar" ? "rtl" : "ltr";
    const evidence = p.mode === "analyst" && ov.data.evidence ? `<div class="evidence">${ov.data.evidence.map((e) => `<span>${esc(e)}</span>`).join("")}</div>` : "";
    const langPill = p.language !== "en" && ov.language === "en" ? `<span class="pill warn" title="Full analyst prose in other languages uses Microsoft Foundry or Azure AI Translator">EN text</span>` : "";
    const meta = p.mode === "analyst"
      ? `<span class="pill ${ov.source}">${ov.source === "llm" ? "AI" : "rules"}</span><span class="pill">relevance ${Number(ov.data.relevance ?? 0).toFixed(2)}</span>${langPill}`
      : `${ov.data.favorite ? `<span class="pill fav">★ your club</span>` : ""}${ov.source === "llm" ? `<span class="pill llm">AI</span>` : ""}`;
    li.innerHTML = `<div class="minute">${esc(ov.clock.split(":")[0])}'<span class="ico">${ICON[type] || "•"}</span></div>
      <div><div class="title">${esc(ov.title)}</div><div class="body">${esc(ov.body)}</div>${evidence}
      <div class="tags">${meta}</div></div>`;
    const feed = $(".feed", el);
    feed.prepend(li);
    while (feed.childNodes.length > 80) feed.lastChild.remove();
  }

  // ---------------------------------------------------------------- persona decoration: settings visibly applied
  const METRIC_LABEL = { xg: "xG", possession: "Possession", pass_accuracy: "Pass accuracy", pressure: "Pressing (PPDA)", distance: "Top sprint" };
  const LANG_LABEL = { en: "EN", es: "ES", fr: "FR", pt: "PT", hi: "HI", ar: "AR" };
  function decorate(id) {
    const { el } = panes[id];
    const p = readPersona(id);
    const team = p.favorite_team_id ? [meta.home, meta.away].find((t) => t.id === p.favorite_team_id) : null;
    el.classList.toggle("mode-analyst", p.mode === "analyst");
    el.classList.toggle("mode-casual", p.mode === "casual");
    el.style.setProperty("--accent", team ? team.color : "#0a1630");
    const follow = p.focus_player_id && players[p.focus_player_id];
    $(".t-chips", el).innerHTML = [
      `<span class="chip-mode">${p.mode === "analyst" ? "Analyst view" : "Fan view"}</span>`,
      team ? `<span class="chip-club" style="background:${team.color}">★ ${esc(team.short)}</span>` : `<span>Neutral</span>`,
      `<span>${LANG_LABEL[p.language] || p.language}</span>`,
      `<span>${METRIC_LABEL[p.metric]}</span>`,
      follow ? `<span>👁 #${follow.number} ${esc(follow.name.split(" ").pop())}</span>` : "",
    ].join("");
    if (lastStats) metricBar(id, lastStats);
  }
  function metricBar(id, stats) {
    const { el } = panes[id];
    const p = readPersona(id);
    const H = stats.teams[meta.home.id], A = stats.teams[meta.away.id];
    const fmt = {
      xg: [H.xg, A.xg, (v) => v.toFixed(2)],
      possession: [stats.possession_pct[meta.home.id], stats.possession_pct[meta.away.id], (v) => `${v.toFixed(0)}%`],
      pass_accuracy: [H.pass_accuracy * 100, A.pass_accuracy * 100, (v) => `${v.toFixed(0)}%`],
      pressure: [H.ppda ?? 0, A.ppda ?? 0, (v) => v.toFixed(1)],
      distance: [H.top_sprint_kmh || 0, A.top_sprint_kmh || 0, (v) => `${v.toFixed(1)}`],
    }[p.metric];
    const [h, a, f] = fmt;
    const lowerBetter = p.metric === "pressure";
    const share = h + a ? (lowerBetter ? a / (h + a) : h / (h + a)) * 100 : 50;
    const fav = p.favorite_team_id;
    let follow = "";
    if (p.focus_player_id && pstats[p.focus_player_id]) {
      const pl = players[p.focus_player_id], st = pstats[p.focus_player_id];
      follow = `<div class="t-follow">👁 <b>#${pl.number} ${esc(pl.name)}</b> · ${st.touches} touches · ${st.pc} passes${st.shots ? ` · ${st.shots} shots (${st.xg.toFixed(2)} xG)` : ""}${st.goals ? ` · ⚽×${st.goals}` : ""}</div>`;
    }
    $(".t-metric", el).innerHTML = `<div class="tm-head"><span class="${fav === meta.home.id ? "fav" : ""}">${fav === meta.home.id ? "★ " : ""}${esc(meta.home.short)} ${f(h)}</span>
      <em>${METRIC_LABEL[p.metric]}${lowerBetter ? " · lower = harder press" : ""}</em>
      <span class="${fav === meta.away.id ? "fav" : ""}">${f(a)} ${esc(meta.away.short)}${fav === meta.away.id ? " ★" : ""}</span></div>
      <div class="split small"><i style="width:${share}%"></i></div>${follow}`;
  }
  function onPersonaApplied(msg) {
    const pane = panes[msg.persona.id];
    if (!pane) return;
    const feed = $(".feed", pane.el);
    feed.innerHTML = "";
    for (const ov of msg.overlays) addFeedItem(pane, ov);
    pane.count = msg.overlays.length;
    $(".t-count", pane.el).textContent = pane.count;
    const onair = $(".onair", pane.el);
    onair.classList.remove("idle", "goal"); void onair.offsetWidth;
    $(".onair-meta", onair).textContent = "Settings applied";
    $("h4", onair).textContent = `✓ Now: ${pane.el.querySelector(".chip-mode").textContent}`;
    $("p", onair).textContent = msg.overlays.length
      ? `Re-told the last ${msg.overlays.length} moments for these settings. New moments follow them too.`
      : "New moments will follow these settings.";
    $(".rows", onair).innerHTML = "";
    clearTimeout(pane.timer);
    pane.timer = setTimeout(() => onair.classList.add("idle"), 4000);
    pane.el.classList.remove("applying");
  }
  function tagBadge(d) {
    const t = d.trigger || [];
    if (t.includes("sprint")) return "SPRINT";
    if (t.includes("rocket")) return "ROCKET";
    if (t.includes("distance")) return `${Math.floor(d.distance_km)} KM`;
    if (t.includes("follow")) return "FOLLOW";
    return "";
  }
  function showTag(ov) {
    const g = $("#tags");
    const key = `${ov.data.player_id}-${ov.t_match}`;
    if (g.querySelector(`[data-key="${CSS.escape(key)}"]`)) return;
    const { x, y } = ov.anchor;
    const color = ov.data.team_id === meta.home.id ? meta.home.color : meta.away.color;
    const sub = [ov.data.speed_kmh != null && `${ov.data.speed_kmh.toFixed(0)} km/h`, `${ov.data.distance_km.toFixed(1)} km`,
      ov.data.xg != null ? `xG ${ov.data.xg.toFixed(2)}` : ov.data.pass_difficulty != null && `diff ${ov.data.pass_difficulty}`,
      ov.data.ball_speed_kmh && `ball ${ov.data.ball_speed_kmh.toFixed(0)} km/h`].filter(Boolean).join(" · ");
    const badge = tagBadge(ov.data);
    const w = Math.max(ov.title.length * 1.2 + 6 + badge.length * 1.25, sub.length * 0.95) + 2;
    const tx = Math.min(Math.max(x - w / 2, 0), 105 - w), ty = Math.max(y - 8.5, 0);
    const node = document.createElementNS(SVGNS, "g");
    node.dataset.key = key;
    node.innerHTML = `<rect x="${tx}" y="${ty}" width="${w}" height="6" rx=".8" fill="rgba(10,22,48,.88)"></rect>
      <rect x="${tx}" y="${ty}" width="4.2" height="6" rx=".8" fill="${color}"></rect>
      <text class="tag-text" x="${tx + 2.1}" y="${ty + 2.6}" text-anchor="middle">${esc(ov.data.number)}</text>
      <text class="tag-text" x="${tx + 5}" y="${ty + 2.6}">${esc(ov.title)}</text>
      <text class="tag-sub" x="${tx + 5}" y="${ty + 5}">${esc(sub)}</text>
      ${badge ? `<text class="tag-badge" x="${tx + w - 1}" y="${ty + 2.5}" text-anchor="end" fill="#ffd84d">${esc(badge)}</text>` : ""}`;
    g.appendChild(node);
    while (g.childNodes.length > 3) g.firstChild.remove();
    setTimeout(() => node.remove(), ov.display_ms);
  }

  // ---------------------------------------------------------------- trace
  function onTrace(tr) {
    traceCounts[tr.status] = (traceCounts[tr.status] || 0) + 1;
    if (tr.source === "llm") traceCounts.llm += 1;
    $("#traceStats").innerHTML = Object.entries(traceCounts).map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join("");
    const stage = $(`.stage[data-agent="${tr.agent}"]`);
    if (stage && tr.status !== "skipped") {
      agentCounts[tr.agent] = tr.agent === "vision" ? Number(/^(\d+)/.exec(tr.note)?.[1] || 0) : (agentCounts[tr.agent] || 0) + 1;
      $("em", stage).textContent = agentCounts[tr.agent];
      stage.classList.add("pulse"); clearTimeout(stage._t); stage._t = setTimeout(() => stage.classList.remove("pulse"), 350);
    }
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(tr.moment_id || "")}</span><span>${esc(tr.agent)}</span><span class="st-${tr.status}">${esc(tr.status)}</span>
      <span class="c4">${esc(tr.persona_id || "·")}</span><span class="c5">${tr.ms.toFixed(1)} ms</span><span class="c6">${esc(tr.source)} ${esc(tr.note)}</span>`;
    const list = $("#traceList");
    list.prepend(li);
    while (list.childNodes.length > 100) list.lastChild.remove();
  }

  // ---------------------------------------------------------------- shared with extras.js
  window.MMCore = {
    get meta() { return meta; }, get players() { return players; }, get pstats() { return pstats; },
    panes, readPersona, esc, SVGNS, ICON, clockMinutes, flowX, crest,
  };
  const X = (fn, ...args) => { try { window.MMX?.[fn]?.(...args); } catch (e) { console.error(e); } };

  // ---------------------------------------------------------------- controls
  $("#startBtn").addEventListener("click", start);
  $("#pauseBtn").addEventListener("click", () => {
    paused = !paused;
    ws?.send(JSON.stringify({ type: paused ? "pause" : "resume" }));
    $("#pauseBtn").textContent = paused ? "Resume" : "Pause";
    $("#liveDot").classList.toggle("on", !paused);
  });
  $("#exportBtn").addEventListener("click", () => {
    if (sessionId) window.open(`/api/sessions/${sessionId}/overlays.json?download=true`, "_blank");
  });
  $("#speed").addEventListener("change", (e) => ws?.send(JSON.stringify({ type: "speed", speed: Number(e.target.value) })));
  $("#seed").addEventListener("change", async () => { if (!ws || ws.readyState !== 1) setupMatch(await fetch(`/api/match?seed=${Number($("#seed").value) || 7}`).then((r) => r.json())); });

  fetch("/api/health").then((r) => r.json()).then((h) => setLlm(h.llm)).catch(() => {});
  fetch("/api/match?seed=7").then((r) => r.json()).then(setupMatch).catch(() => {});
})();
