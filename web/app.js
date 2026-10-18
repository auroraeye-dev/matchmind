// MatchMind broadcast client: one WebSocket, many personas.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const SVGNS = "http://www.w3.org/2000/svg";

  let ws = null, meta = null, paused = false;
  const players = {};
  const traceCounts = { ok: 0, fallback: 0, skipped: 0, error: 0, llm: 0 };

  const DEFAULT_PERSONAS = [
    { id: "analyst", label: "Studio analyst", mode: "analyst", language: "en", metric: "xg" },
    { id: "fan", label: "Home fan", mode: "casual", language: "en", metric: "possession", favorite_team_id: "__home" },
  ];
  const panes = {};

  // ---------------------------------------------------------------- personas UI
  function buildPersonas(home, away) {
    const host = $("#personas");
    host.innerHTML = "";
    for (const def of DEFAULT_PERSONAS) {
      const p = { ...def };
      if (p.favorite_team_id === "__home") p.favorite_team_id = home.id;
      const el = $("#personaTpl").content.firstElementChild.cloneNode(true);
      $(".p-label", el).value = p.label;
      $(".p-mode", el).value = p.mode;
      $(".p-lang", el).value = p.language;
      $(".p-metric", el).value = p.metric;
      const team = $(".p-team", el);
      for (const t of [home, away]) team.add(new Option(t.name, t.id));
      team.value = p.favorite_team_id || "";
      fillPlayers($(".p-player", el), p.favorite_team_id ? [p.favorite_team_id === home.id ? home : away] : [home, away]);
      el.addEventListener("change", (e) => {
        if (e.target.classList.contains("p-team")) {
          const t = e.target.value;
          fillPlayers($(".p-player", el), t ? [t === home.id ? home : away] : [home, away]);
        }
        sendPersona(p.id);
      });
      $(".p-label", el).addEventListener("input", () => sendPersona(p.id));
      host.appendChild(el);
      panes[p.id] = { el, persona: p, count: 0, hideTimer: null };
    }
  }

  function fillPlayers(sel, teams) {
    const cur = sel.value;
    sel.innerHTML = '<option value="">No player</option>';
    for (const t of teams) for (const pl of t.players) sel.add(new Option(`#${pl.number} ${pl.name} (${t.short})`, pl.id));
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }

  function readPersona(id) {
    const { el, persona } = panes[id];
    return {
      id: persona.id,
      label: $(".p-label", el).value,
      mode: $(".p-mode", el).value,
      favorite_team_id: $(".p-team", el).value || null,
      focus_player_id: $(".p-player", el).value || null,
      language: $(".p-lang", el).value,
      metric: $(".p-metric", el).value,
    };
  }

  function sendPersona(id) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "persona", persona: readPersona(id) }));
  }

  // ---------------------------------------------------------------- stream
  async function start() {
    if (ws) ws.close();
    const seed = Number($("#seed").value) || 7;
    const m = await fetch(`/api/match?seed=${seed}`).then((r) => r.json());
    setupMatch(m);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    ws.onopen = () => {
      ws.send(JSON.stringify({ seed, speed: Number($("#speed").value), personas: Object.keys(panes).map(readPersona) }));
      $("#pauseBtn").disabled = false;
      $("#startBtn").textContent = "Restart";
    };
    ws.onmessage = (e) => handle(JSON.parse(e.data));
    ws.onclose = () => { $("#pauseBtn").disabled = true; };
  }

  function setupMatch(m) {
    meta = m;
    for (const t of [m.home, m.away]) for (const p of t.players) players[p.id] = { ...p, team: t };
    document.documentElement.style.setProperty("--home", m.home.color);
    document.documentElement.style.setProperty("--away", m.away.color);
    $("#homeName").textContent = m.home.short;
    $("#awayName").textContent = m.away.short;
    $("#homeName").title = m.home.name;
    $("#awayName").title = m.away.name;
    $("#thHome").textContent = m.home.name;
    $("#thAway").textContent = m.away.name;
    $("#score").textContent = "0 – 0";
    $("#trail").innerHTML = "";
    $("#tags").innerHTML = "";
    $("#traceList").innerHTML = "";
    Object.keys(traceCounts).forEach((k) => (traceCounts[k] = 0));
    buildPersonas(m.home, m.away);
  }

  function handle(msg) {
    switch (msg.type) {
      case "meta": setLlm(msg.llm); break;
      case "event": onEvent(msg); break;
      case "overlay": onOverlay(msg.overlay); break;
      case "trace": onTrace(msg.trace); break;
      case "end": setLlm(msg.llm); $("#eventLine").textContent = "Full-time. Change the seed and press Restart for a new match."; break;
    }
  }

  function setLlm(s) {
    const chip = $("#llmChip");
    chip.textContent = `LLM: ${s.provider}${s.breaker_open ? " (breaker open)" : ""}`;
    chip.classList.toggle("live", s.provider !== "offline" && !s.breaker_open);
  }

  // ---------------------------------------------------------------- pitch & stats
  function onEvent({ event: ev, derived, stats }) {
    $("#clock").textContent = ev.clock;
    const h = meta.home.id, a = meta.away.id;
    $("#score").textContent = `${stats.score[h]} – ${stats.score[a]}`;
    if (ev.x != null) {
      const ball = $("#ball");
      const x = ev.end_x ?? ev.x, y = ev.end_y ?? ev.y;
      ball.setAttribute("cx", x); ball.setAttribute("cy", y);
      if (ev.end_x != null && (ev.type === "pass" || ev.type === "carry" || ev.type === "shot")) {
        const ln = document.createElementNS(SVGNS, "line");
        ln.setAttribute("x1", ev.x); ln.setAttribute("y1", ev.y); ln.setAttribute("x2", ev.end_x); ln.setAttribute("y2", ev.end_y);
        ln.setAttribute("stroke", ev.team_id === h ? meta.home.color : meta.away.color);
        ln.setAttribute("stroke-dasharray", ev.type === "carry" ? "0.8 0.8" : ev.outcome === "success" || ev.type === "shot" ? "" : "0.4 0.6");
        const trail = $("#trail");
        trail.appendChild(ln);
        while (trail.childNodes.length > 10) trail.firstChild.remove();
        [...trail.childNodes].forEach((n, i, arr) => n.setAttribute("opacity", ((i + 1) / arr.length).toFixed(2)));
      }
    }
    const who = ev.player_id && players[ev.player_id] ? players[ev.player_id].name : "";
    const extra = derived.pass_difficulty != null ? ` · ${derived.pass_length_m}m, difficulty ${derived.pass_difficulty}` : ev.xg != null ? ` · xG ${ev.xg}` : "";
    $("#eventLine").textContent = `${ev.clock}  ${ev.type.replace(/_/g, " ")}${ev.outcome ? " (" + ev.outcome + ")" : ""}  ${who}${extra}`;

    const mom = stats.momentum;
    const mf = $("#momFill");
    mf.style.left = mom >= 0 ? "50%" : `${50 + mom * 50}%`;
    mf.style.width = `${Math.abs(mom) * 50}%`;
    mf.style.background = mom >= 0 ? meta.home.color : meta.away.color;
    $("#momVal").textContent = `${mom >= 0 ? meta.home.short : meta.away.short} ${Math.abs(mom).toFixed(2)}`;
    $("#chaosFill").style.width = `${stats.chaos * 100}%`;
    $("#chaosVal").textContent = stats.chaos < 0.35 ? `controlled ${stats.chaos.toFixed(2)}` : stats.chaos < 0.65 ? `open ${stats.chaos.toFixed(2)}` : `chaotic ${stats.chaos.toFixed(2)}`;
    const ph = stats.possession_pct[h] ?? 50;
    $("#possFill").style.width = `${ph}%`;
    $("#possVal").textContent = `${ph.toFixed(0)} / ${(100 - ph).toFixed(0)}`;

    if (ev.seq % 5 === 0 || ev.type === "goal" || ev.type === "shot") renderStats(stats);
  }

  function renderStats(stats) {
    const H = stats.teams[meta.home.id], A = stats.teams[meta.away.id];
    const rows = [
      ["Goals", H.goals, A.goals], ["xG", H.xg.toFixed(2), A.xg.toFixed(2)], ["Shots (on target)", `${H.shots} (${H.on_target})`, `${A.shots} (${A.on_target})`],
      ["Pass accuracy", `${(H.pass_accuracy * 100).toFixed(0)}%`, `${(A.pass_accuracy * 100).toFixed(0)}%`],
      ["Final-third entries", H.final_third_entries, A.final_third_entries], ["Pressures", H.pressures, A.pressures],
      ["Tackles won", H.tackles_won, A.tackles_won], ["Corners", H.corners, A.corners], ["Fouls / yellows", `${H.fouls} / ${H.yellow_cards}`, `${A.fouls} / ${A.yellow_cards}`],
    ];
    $("#statsTable tbody").innerHTML = rows.map(([l, h, a]) => `<tr><td>${esc(h)}</td><td>${esc(l)}</td><td>${esc(a)}</td></tr>`).join("");
  }

  // ---------------------------------------------------------------- overlays
  function onOverlay(ov) {
    if (ov.kind === "player_tag") return showTag(ov);
    const pane = panes[ov.persona_id];
    if (!pane) return;
    const el = pane.el;
    pane.count += 1;
    $(".p-count", el).textContent = `${pane.count} moments`;

    const onair = $(".onair", el);
    onair.classList.remove("empty", "goal");
    void onair.offsetWidth;
    if (ov.data.moment_type === "goal") onair.classList.add("goal");
    $(".onair-tag", onair).textContent = `ON AIR · ${ov.clock} · ${ov.kind.replace("_", " ")}`;
    $("h4", onair).textContent = ov.title;
    $("p", onair).textContent = ov.body;
    $(".rows", onair).innerHTML = (ov.data.rows || []).map((r) => `<span>${esc(r.home)}</span><span>${esc(r.label)}</span><span>${esc(r.away)}</span>`).join("");
    onair.dir = ov.language === "ar" ? "rtl" : "ltr";
    clearTimeout(pane.hideTimer);
    pane.hideTimer = setTimeout(() => onair.classList.add("empty"), ov.display_ms);

    const li = document.createElement("li");
    li.innerHTML = `<div class="meta"><span>${esc(ov.clock)}</span><span>${esc(ov.data.moment_type || ov.kind)}</span>
      <span class="src ${ov.source}">${ov.source}</span><span>rel ${Number(ov.data.relevance ?? 0).toFixed(2)}</span></div>
      <div class="title">${esc(ov.title)}</div><div class="body">${esc(ov.body)}</div>`;
    $(".feed", el).prepend(li);
    const feed = $(".feed", el);
    while (feed.childNodes.length > 60) feed.lastChild.remove();
  }

  function showTag(ov) {
    const g = $("#tags");
    const key = `${ov.data.player_id}-${ov.t_match}`;
    if (g.querySelector(`[data-key="${CSS.escape(key)}"]`)) return;
    const { x, y } = ov.anchor;
    const color = ov.data.team_id === meta.home.id ? meta.home.color : meta.away.color;
    const sub = [ov.data.speed_kmh != null ? `${ov.data.speed_kmh.toFixed(0)} km/h` : null, `${ov.data.distance_km.toFixed(1)} km`,
      ov.data.xg != null ? `xG ${ov.data.xg.toFixed(2)}` : ov.data.pass_difficulty != null ? `diff ${ov.data.pass_difficulty}` : null].filter(Boolean).join(" · ");
    const w = Math.max(ov.title.length * 1.15, sub.length * 0.92) + 2;
    const tx = Math.min(Math.max(x - w / 2, 0), 105 - w), ty = Math.max(y - 8, 0);
    const node = document.createElementNS(SVGNS, "g");
    node.dataset.key = key;
    node.innerHTML = `<rect class="tag-box" x="${tx}" y="${ty}" width="${w}" height="5.6" fill="rgba(0,0,0,.72)" stroke="${color}"></rect>
      <text class="tag-text" x="${tx + 1}" y="${ty + 2.4}">#${esc(ov.data.number)} ${esc(ov.title)}</text>
      <text class="tag-sub" x="${tx + 1}" y="${ty + 4.7}">${esc(sub)}</text>`;
    g.appendChild(node);
    while (g.childNodes.length > 3) g.firstChild.remove();
    setTimeout(() => node.remove(), ov.display_ms);
  }

  // ---------------------------------------------------------------- trace
  function onTrace(tr) {
    traceCounts[tr.status] = (traceCounts[tr.status] || 0) + 1;
    if (tr.source === "llm") traceCounts.llm += 1;
    $("#traceStats").innerHTML = `<span>ok ${traceCounts.ok}</span><span>llm ${traceCounts.llm}</span><span>fallback ${traceCounts.fallback}</span><span>skipped ${traceCounts.skipped}</span><span>errors ${traceCounts.error}</span>`;
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(tr.moment_id || "")}</span><span>${esc(tr.agent)}</span><span class="st-${tr.status}">${esc(tr.status)}</span>
      <span class="c4">${esc(tr.persona_id || "·")}</span><span class="c5">${tr.ms.toFixed(1)}ms</span><span class="c6">${esc(tr.source)} ${esc(tr.note)}</span>`;
    const list = $("#traceList");
    list.prepend(li);
    while (list.childNodes.length > 80) list.lastChild.remove();
  }

  // ---------------------------------------------------------------- controls
  $("#startBtn").addEventListener("click", start);
  $("#pauseBtn").addEventListener("click", () => {
    paused = !paused;
    ws?.send(JSON.stringify({ type: paused ? "pause" : "resume" }));
    $("#pauseBtn").textContent = paused ? "Resume" : "Pause";
  });
  $("#speed").addEventListener("change", (e) => ws?.send(JSON.stringify({ type: "speed", speed: Number(e.target.value) })));

  fetch("/api/health").then((r) => r.json()).then((h) => setLlm(h.llm)).catch(() => {});
  fetch("/api/match?seed=7").then((r) => r.json()).then(setupMatch).catch(() => {});
})();
