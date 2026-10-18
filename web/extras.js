// MatchMind extras: replay theatre, highlights reel, broadcast view, player cam, timeline markers, goal celebration.
// Everything replays from what this browser recorded during the live session.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const C = () => window.MMCore;
  const esc = (s) => C().esc(s);
  const SVGNS = "http://www.w3.org/2000/svg";
  const KEY_TYPES = new Set(["goal", "big_chance", "card", "momentum_swing", "great_pass", "pressure_spike"]);
  const REEL_TYPES = new Set(["goal", "big_chance", "great_pass", "pressure_spike"]);
  const SPRINT_KMH = 25.2;

  const rec = { events: [], overlays: [], moments: [] };
  let heat = {};
  let heatDirty = false;
  const HEAT_TYPES = new Set(["pass", "carry", "shot", "tackle", "interception", "pressure"]); // on-ball actions, no set pieces
  // home attacks +x in the first half; flip the other cases so every heatmap reads "attacking →"
  function attackRight(ev) {
    const home = ev.team_id === C().meta.home.id;
    const flip = (home && ev.period === 2) || (!home && ev.period === 1);
    return flip ? [105 - ev.x, 68 - ev.y] : [ev.x, ev.y];
  }
  let lastCamRender = 0;

  // ---------------------------------------------------------------- lifecycle hooks (called by app.js)
  function reset() {
    rec.events = []; rec.overlays = []; rec.moments = []; heat = {};
    $("#flowMarks").innerHTML = "";
    $("#heatLayer").innerHTML = "";
    heatDirty = false;
    closeTheatre();
    fillPersonaSelect($("#theatrePersona"));
    fillPersonaSelect($("#bcPersona"));
    renderPlayerCam();
    if (!$("#broadcast").hidden) updateBug("#bbug", null);
  }

  function onEvent(msg) {
    rec.events.push(msg);
    const ev = msg.event;
    if (ev.player_id && ev.x != null && ev.team_id && HEAT_TYPES.has(ev.type)) {
      const p = attackRight(ev);
      (heat[ev.player_id] ||= []).push(p);
      (heat[`team:${ev.team_id}`] ||= []).push(p);
      heatDirty = true;
    }
    if (ev.type === "goal") goalFlash(ev);
    if (!$("#broadcast").hidden) { drawOn("b", ev); updateBug("#bbug", msg); }
    const pid = followedId();
    const now = performance.now();
    if (pid && (ev.player_id === pid || now - lastCamRender > 1500)) { lastCamRender = now; renderPlayerCam(); }
  }

  function onOverlay(ov) {
    rec.overlays.push(ov);
    if (!$("#broadcast").hidden) {
      const persona = $("#bcPersona").value;
      if (ov.kind === "player_tag") { if (ov.persona_id === "*" || ov.persona_id === persona) tagOn("b", ov); }
      else if (ov.persona_id === persona) { lowerThird("b", ov, ov.display_ms); tickerPush(ov); }
    }
  }

  function onMoment(m) {
    rec.moments.push(m);
    if (KEY_TYPES.has(m.type) && (m.type !== "momentum_swing" || m.importance >= 0.55)) addMarker(m);
  }

  function onTab(view) {
    if (view !== "heat") return;
    heatLegend();
    drawMainHeat();
  }
  function heatSubject() {
    const sel = $("#heatWho");
    if (sel && sel.value) return sel.value;
    return followedId() || `team:${C().meta.home.id}`;
  }
  function heatLegend() {
    const { home, away } = C().meta;
    const pid = followedId();
    const cur = $("#heatWho")?.value || (pid ? pid : `team:${home.id}`);
    const opts = [
      pid && [pid, `${C().players[pid].name} (followed)`],
      [`team:${home.id}`, `${home.name} · all players`],
      [`team:${away.id}`, `${away.name} · all players`],
    ].filter(Boolean);
    $("#pitchLegend").innerHTML = `<label class="heat-who">Heatmap of <select id="heatWho">${opts.map(([v, l]) =>
      `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <span>attacking →</span><span class="heat-scale"><i></i>fewer → more touches</span><span id="heatCount"></span>`;
    $("#heatWho").addEventListener("change", drawMainHeat);
  }
  function drawMainHeat() {
    const who = heatSubject();
    renderHeat($("#heatLayer"), who);
    const n = (heat[who] || []).length;
    const c = $("#heatCount");
    if (c) c.textContent = n ? `${n} touches` : "no touches yet: kick off first";
  }
  // keep the main heatmap live while its tab is open
  setInterval(() => { if (heatDirty && $(".tab.active")?.dataset.view === "heat") { heatDirty = false; drawMainHeat(); } }, 1000);

  // ---------------------------------------------------------------- helpers
  function fillPersonaSelect(sel) {
    const cur = sel.value;
    sel.innerHTML = "";
    for (const id of Object.keys(C().panes)) sel.add(new Option(C().readPersona(id).label, id));
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }
  function followedId() {
    for (const id of Object.keys(C().panes)) { const p = C().readPersona(id).focus_player_id; if (p) return p; }
    return null;
  }
  const teamColor = (tid) => (tid === C().meta.home.id ? C().meta.home.color : C().meta.away.color);
  function overlayFor(m, persona) {
    return rec.overlays.find((o) => o.moment_id === m.id && o.persona_id === persona && o.kind !== "player_tag")
      || rec.overlays.find((o) => o.moment_id === m.id && o.kind !== "player_tag");
  }
  function momentTitle(m) {
    const ov = overlayFor(m, Object.keys(C().panes)[0]);
    return ov ? ov.title : m.type.replace(/_/g, " ");
  }

  // ---------------------------------------------------------------- timeline markers
  function addMarker(m) {
    const x = C().flowX({ clock: m.clock, period: m.period });
    const home = m.team_id === C().meta.home.id;
    const g = document.createElementNS(SVGNS, "g");
    g.setAttribute("class", "flow-mark");
    g.dataset.mid = m.id;
    const y = m.team_id ? (home ? 12 : 108) : 60;
    const r = m.type === "goal" ? 8 : 6;
    g.innerHTML = `<circle cx="${x}" cy="${y}" r="${r}" fill="${m.team_id ? teamColor(m.team_id) : "#f5b700"}"></circle>
      <text x="${x}" y="${y + 4}" text-anchor="middle">${C().ICON[m.type] || "•"}</text><title>${esc(m.clock)} · ${esc(momentTitle(m))} · click to replay</title>`;
    g.addEventListener("click", (e) => { e.stopPropagation(); openReplay(m); });
    $("#flowMarks").appendChild(g);
  }
  $("#flow").addEventListener("mousemove", (e) => {
    const box = $("#flow").getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * 960;
    $("#flowCursor").setAttribute("x1", x); $("#flowCursor").setAttribute("x2", x);
  });
  $("#flow").addEventListener("click", (e) => {
    if (!rec.events.length) return;
    const box = $("#flow").getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * 960;
    let best = rec.events[0], bd = Infinity;
    for (const msg of rec.events) {
      const d = Math.abs(C().flowX(msg.event) - x);
      if (d < bd) { bd = d; best = msg; }
    }
    openReplayWindow(best.event.t - 10, best.event.t + 20, `Replay from ${best.event.clock.split(":")[0]}'`, [], 0);
  });

  // ---------------------------------------------------------------- shared pitch drawing (prefix r = replay, b = broadcast)
  function drawOn(prefix, ev) {
    if (ev.x == null) return;
    const ball = $(`#${prefix}ball`);
    ball.setAttribute("cx", ev.end_x ?? ev.x); ball.setAttribute("cy", ev.end_y ?? ev.y);
    if (ev.end_x == null || !["pass", "carry", "shot"].includes(ev.type)) return;
    const ln = document.createElementNS(SVGNS, "line");
    ln.setAttribute("x1", ev.x); ln.setAttribute("y1", ev.y); ln.setAttribute("x2", ev.end_x); ln.setAttribute("y2", ev.end_y);
    ln.setAttribute("stroke", ev.team_id === C().meta.home.id ? "#fff" : "#ffd84d");
    ln.setAttribute("stroke-width", ev.type === "shot" ? ".7" : ".45");
    ln.setAttribute("stroke-linecap", "round");
    if (ev.type === "carry") ln.setAttribute("stroke-dasharray", ".9 .9");
    else if (ev.outcome !== "success" && ev.type !== "shot") ln.setAttribute("stroke-dasharray", ".4 .7");
    const trail = $(`#${prefix}trail`);
    trail.appendChild(ln);
    while (trail.childNodes.length > 12) trail.firstChild.remove();
    [...trail.childNodes].forEach((n, i, arr) => n.setAttribute("opacity", ((i + 1) / arr.length).toFixed(2)));
  }
  function clearPitch(prefix) { $(`#${prefix}trail`).innerHTML = ""; $(`#${prefix}tags`).innerHTML = ""; }
  function tagOn(prefix, ov, ms = 2600) {
    const g = $(`#${prefix}tags`);
    const { x, y } = ov.anchor;
    const sub = [ov.data.speed_kmh != null && `${ov.data.speed_kmh.toFixed(0)} km/h`, `${ov.data.distance_km.toFixed(1)} km`,
      ov.data.xg != null && `xG ${ov.data.xg.toFixed(2)}`, ov.data.ball_speed_kmh && `ball ${ov.data.ball_speed_kmh.toFixed(0)} km/h`].filter(Boolean).join(" · ");
    const w = Math.max(ov.title.length * 1.2 + 6, sub.length * 0.95) + 2;
    const tx = Math.min(Math.max(x - w / 2, 0), 105 - w), ty = Math.max(y - 8.5, 0);
    const node = document.createElementNS(SVGNS, "g");
    node.innerHTML = `<rect x="${tx}" y="${ty}" width="${w}" height="6" rx=".8" fill="rgba(10,22,48,.9)"></rect>
      <rect x="${tx}" y="${ty}" width="4.2" height="6" rx=".8" fill="${teamColor(ov.data.team_id)}"></rect>
      <text class="tag-text" x="${tx + 2.1}" y="${ty + 2.6}" text-anchor="middle">${esc(ov.data.number)}</text>
      <text class="tag-text" x="${tx + 5}" y="${ty + 2.6}">${esc(ov.title)}</text>
      <text class="tag-sub" x="${tx + 5}" y="${ty + 5}">${esc(sub)}</text>`;
    g.appendChild(node);
    while (g.childNodes.length > 3) g.firstChild.remove();
    setTimeout(() => node.remove(), ms);
  }
  function updateBug(sel, msg) {
    const { home, away } = C().meta;
    const sc = msg ? msg.stats.score : { [home.id]: 0, [away.id]: 0 };
    const clock = msg ? msg.event.clock : "00:00";
    $(sel).innerHTML = `<span class="sb-team" style="background:${home.color}">${esc(home.short)}</span>
      <span class="sb-score">${sc[home.id]} – ${sc[away.id]}</span>
      <span class="sb-team" style="background:${away.color}">${esc(away.short)}</span><span class="sb-clock">${esc(clock)}</span>`;
  }
  const ltTimers = {};
  function lowerThird(prefix, ov, ms) {
    const el = $(`#${prefix}lower`);
    el.hidden = false;
    el.classList.toggle("goal", ov.data?.moment_type === "goal");
    el.style.animation = "none"; void el.offsetWidth; el.style.animation = "";
    $(`#${prefix}lowerKicker`).textContent = `${ov.clock.split(":")[0]}' · ${(ov.data?.moment_type || ov.kind).replace(/_/g, " ")}${ov.source === "llm" ? " · AI" : ""}`;
    $(`#${prefix}lowerTitle`).textContent = ov.title;
    $(`#${prefix}lowerBody`).textContent = ov.body;
    el.dir = ov.language === "ar" ? "rtl" : "ltr";
    clearTimeout(ltTimers[prefix]);
    ltTimers[prefix] = setTimeout(() => (el.hidden = true), ms);
  }

  // ---------------------------------------------------------------- replay theatre
  const R = { t0: 0, t1: 0, rt: 0, idx: 0, playing: false, last: 0, shown: new Set(), reel: [], reelPos: -1, raf: 0 };

  function openReplay(m) {
    const persona = $("#theatrePersona").value || Object.keys(C().panes)[0];
    const ov = overlayFor(m, persona);
    openReplayWindow(m.t - 14, m.t + 6, `${m.clock.split(":")[0]}' · ${ov ? ov.title : m.type.replace(/_/g, " ")}`, [], -1);
  }

  function openReplayWindow(t0, t1, title, reel, reelPos) {
    if (!rec.events.length) { alert("Kick off a match first: replays are built from the live session."); return; }
    const first = rec.events[0].event.t, last = rec.events[rec.events.length - 1].event.t;
    Object.assign(R, { t0: Math.max(first, t0), t1: Math.min(last, t1), reel, reelPos });
    fillPersonaSelect($("#theatrePersona"));
    $("#theatreTitle").textContent = title;
    $("#theatre").hidden = false;
    $("#reelCard").hidden = true;
    seek(R.t0);
    play(true);
  }

  function seek(t) {
    R.rt = t; R.shown = new Set();
    clearPitch("r");
    $("#rlower").hidden = true;
    let i = 0;
    while (i < rec.events.length && rec.events[i].event.t < t) i++;
    // draw the few events just before t so the ball and trail have context
    for (let k = Math.max(0, i - 4); k < i; k++) drawOn("r", rec.events[k].event);
    updateBug("#rbug", rec.events[Math.max(0, i - 1)]);
    R.idx = i;
    syncScrub();
  }

  function syncScrub() {
    $("#rScrub").value = Math.round(((R.rt - R.t0) / Math.max(1, R.t1 - R.t0)) * 1000);
    const cur = rec.events[Math.max(0, R.idx - 1)];
    $("#rClock").textContent = cur ? cur.event.clock : "00:00";
  }

  function play(on) {
    R.playing = on;
    $("#rPlay").textContent = on ? "⏸" : "▶";
    cancelAnimationFrame(R.raf);
    if (on) { R.last = performance.now(); R.raf = requestAnimationFrame(tick); }
  }

  function tick(now) {
    const dt = Math.min(0.5, (now - R.last) / 1000);  // keep real time even at low frame rates
    R.last = now;
    R.rt += dt * Number($("#rSpeed").value);
    const persona = $("#theatrePersona").value;
    while (R.idx < rec.events.length && rec.events[R.idx].event.t <= R.rt) {
      const msg = rec.events[R.idx++];
      drawOn("r", msg.event);
      updateBug("#rbug", msg);
    }
    for (const ov of rec.overlays) {
      if (ov.t_match > R.rt || ov.t_match < R.t0 || R.shown.has(ov.id)) continue;
      if (ov.kind === "player_tag") { if (ov.persona_id === "*" || ov.persona_id === persona) { R.shown.add(ov.id); tagOn("r", ov, 2400); } }
      else if (ov.persona_id === persona) { R.shown.add(ov.id); lowerThird("r", ov, 4200); }
    }
    syncScrub();
    if (R.rt >= R.t1) { play(false); if (R.reel.length) setTimeout(nextReel, 1400); return; }
    R.raf = requestAnimationFrame(tick);
  }

  function closeTheatre() { play(false); R.reel = []; $("#theatre").hidden = true; }

  function stepMoment(dir) {
    if (R.reel.length) { R.reelPos = Math.max(-1, Math.min(R.reel.length - 2, R.reelPos + (dir < 0 ? -2 : 0))); nextReel(); return; }
    const centre = (R.t0 + R.t1) / 2;
    const key = rec.moments.filter((m) => KEY_TYPES.has(m.type));
    const m = dir > 0 ? key.find((k) => k.t > centre + 1) : [...key].reverse().find((k) => k.t < centre - 1);
    if (m) openReplay(m);
  }

  // ---------------------------------------------------------------- highlights reel
  function buildReel() {
    const top = rec.moments.filter((m) => REEL_TYPES.has(m.type))
      .sort((a, b) => b.importance - a.importance).slice(0, 8).sort((a, b) => a.t - b.t);
    return top;
  }
  function startReel() {
    const reel = buildReel();
    if (!reel.length) { alert("No highlights yet: kick off and let the match run for a few minutes."); return; }
    R.reel = reel; R.reelPos = -1;
    fillPersonaSelect($("#theatrePersona"));
    $("#theatre").hidden = false;
    nextReel();
  }
  function nextReel() {
    if (!R.reel.length || $("#theatre").hidden) return;
    R.reelPos += 1;
    if (R.reelPos >= R.reel.length) {
      const card = $("#reelCard");
      card.hidden = false;
      card.innerHTML = `End of highlights<small>${R.reel.length} moments · ${esc(C().meta.home.name)} vs ${esc(C().meta.away.name)}</small>`;
      return;
    }
    const m = R.reel[R.reelPos];
    const card = $("#reelCard");
    card.hidden = false;
    card.innerHTML = `${C().ICON[m.type] || ""} ${esc(m.clock.split(":")[0])}' · ${esc(momentTitle(m))}<small>Highlight ${R.reelPos + 1} of ${R.reel.length}</small>`;
    const reel = R.reel, pos = R.reelPos;
    setTimeout(() => {
      if (R.reel !== reel || R.reelPos !== pos) return;
      card.hidden = true;
      Object.assign(R, { t0: m.t - 12, t1: m.t + 5 });
      $("#theatreTitle").textContent = `Highlights ${pos + 1}/${reel.length} · ${m.clock.split(":")[0]}' ${momentTitle(m)}`;
      seek(R.t0); play(true);
    }, 1500);
  }

  $("#highlightsBtn").addEventListener("click", startReel);
  $("#theatreClose").addEventListener("click", closeTheatre);
  $("#rPlay").addEventListener("click", () => { if (R.rt >= R.t1) seek(R.t0); play(!R.playing); });
  $("#rPrev").addEventListener("click", () => stepMoment(-1));
  $("#rNext").addEventListener("click", () => stepMoment(1));
  $("#rScrub").addEventListener("input", (e) => { play(false); seek(R.t0 + (Number(e.target.value) / 1000) * (R.t1 - R.t0)); });
  $("#theatre").addEventListener("click", (e) => { if (e.target.id === "theatre") closeTheatre(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeTheatre(); closeBroadcast(); }
    if (!$("#theatre").hidden && e.key === " ") { e.preventDefault(); $("#rPlay").click(); }
  });

  // ---------------------------------------------------------------- broadcast view
  function openBroadcast() {
    fillPersonaSelect($("#bcPersona"));
    $("#broadcast").hidden = false;
    clearPitch("b");
    updateBug("#bbug", rec.events[rec.events.length - 1] || null);
    $("#bticker").innerHTML = `<span>${esc(C().meta.competition)} · ${esc(C().meta.venue)} · synthetic data · MatchMind</span>`;
    document.documentElement.requestFullscreen?.().catch(() => {});
  }
  function closeBroadcast() {
    if ($("#broadcast").hidden) return;
    $("#broadcast").hidden = true;
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  }
  const tickerItems = [];
  function tickerPush(ov) {
    tickerItems.unshift(`${ov.clock.split(":")[0]}' ${ov.title}`);
    tickerItems.length = Math.min(tickerItems.length, 8);
    $("#bticker").innerHTML = `<span>${tickerItems.map(esc).join("   ◆   ")}</span>`;
  }
  $("#broadcastBtn").addEventListener("click", openBroadcast);
  $("#bcClose").addEventListener("click", closeBroadcast);
  document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement) closeBroadcast(); });

  // ---------------------------------------------------------------- goal celebration
  function goalFlash(ev) {
    const el = $("#goalFlash");
    const p = C().players[ev.player_id];
    el.style.setProperty("--flash", teamColor(ev.team_id));
    $("#goalFlashSub").textContent = `${p ? p.name : ""} · ${ev.clock.split(":")[0]}'`;
    el.hidden = true; void el.offsetWidth; el.hidden = false;
    clearTimeout(goalFlash.t);
    goalFlash.t = setTimeout(() => (el.hidden = true), 2700);
  }

  // ---------------------------------------------------------------- player cam
  const NX = 21, NY = 14, CW = 105 / NX, CH = 68 / NY;
  const RAMP = [[0, [255, 237, 160]], [0.35, [254, 178, 76]], [0.65, [240, 59, 32]], [1, [189, 0, 38]]];
  function ramp(t) {
    for (let i = 1; i < RAMP.length; i++) {
      if (t <= RAMP[i][0]) {
        const [t0, c0] = RAMP[i - 1], [t1, c1] = RAMP[i];
        const f = (t - t0) / (t1 - t0);
        return `rgb(${c0.map((c, k) => Math.round(c + (c1[k] - c) * f)).join(",")})`;
      }
    }
    return "rgb(189,0,38)";
  }
  function renderHeat(g, key) {
    g.innerHTML = "";
    const pts = heat[key];
    if (!key || !pts || !pts.length) return;
    const grid = Array.from({ length: NY }, () => new Float32Array(NX));
    for (const [x, y] of pts) {
      const i = Math.min(NX - 1, Math.max(0, Math.floor(x / CW))), j = Math.min(NY - 1, Math.max(0, Math.floor(y / CH)));
      grid[j][i] += 1;
    }
    // 3x3 smoothing kernel so sparse touches still read as zones
    const K = [[1, 2, 1], [2, 4, 2], [1, 2, 1]];
    const sm = Array.from({ length: NY }, () => new Float32Array(NX));
    let max = 0;
    for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {
      let v = 0;
      for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
        const jj = j + dj, ii = i + di;
        if (jj >= 0 && jj < NY && ii >= 0 && ii < NX) v += grid[jj][ii] * K[dj + 1][di + 1];
      }
      sm[j][i] = v; max = Math.max(max, v);
    }
    const fid = `${g.id || "h"}Blur`;
    let out = `<defs><filter id="${fid}" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="2.2"/></filter></defs><g filter="url(#${fid})">`;
    for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {
      const t = max ? Math.sqrt(sm[j][i] / max) : 0; // sqrt lifts mid-intensity zones
      if (t < 0.12) continue;
      out += `<rect x="${(i * CW).toFixed(2)}" y="${(j * CH).toFixed(2)}" width="${(CW + 0.2).toFixed(2)}" height="${(CH + 0.2).toFixed(2)}" fill="${ramp(t)}" opacity="${(0.25 + 0.65 * t).toFixed(2)}"/>`;
    }
    out += "</g>";
    if (!key.startsWith("team:")) {
      const [x, y] = pts[pts.length - 1];
      out += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="1.5" fill="#fff" stroke="#0a1630" stroke-width=".4"><title>latest touch</title></circle>`;
    }
    g.innerHTML = out;
  }

  function renderPlayerCam() {
    const body = $("#playercamBody");
    const pid = followedId();
    if ($(".tab.active")?.dataset.view === "heat") { heatLegend(); drawMainHeat(); }
    if (!pid || !C().players[pid]) {
      body.className = "pc-empty";
      body.textContent = "No player followed yet. Pick one in the line-ups or a ticker's Follow menu to track them all match.";
      return;
    }
    const p = C().players[pid];
    let passes = 0, done = 0, diffSum = 0, diffN = 0, sprints = 0, top = 0, dist = 0, shots = 0, xg = 0, goals = 0, tk = 0, touches = 0, ballTop = 0;
    for (const { event: e, derived: d } of rec.events) {
      if (e.player_id !== pid) continue;
      touches++;
      if (e.distance_covered_km != null) dist = e.distance_covered_km;
      if (e.player_speed_kmh) { top = Math.max(top, e.player_speed_kmh); if (e.player_speed_kmh >= SPRINT_KMH && ["carry", "pressure", "tackle"].includes(e.type)) sprints++; }
      if (e.type === "pass") { passes++; if (e.outcome === "success") done++; if (d?.pass_difficulty != null) { diffSum += d.pass_difficulty; diffN++; } }
      if (e.type === "shot") { shots++; xg += e.xg || 0; ballTop = Math.max(ballTop, e.ball_speed_kmh || 0); }
      if (e.type === "goal") goals++;
      if (e.type === "tackle" && e.outcome === "won") tk++;
    }
    const stat = (v, l) => `<div class="pc-stat"><b>${v}</b><span>${l}</span></div>`;
    body.className = "pc";
    body.innerHTML = `
      <div class="pc-head">
        <div class="pc-shirt" style="background:${p.team.color}">${p.number}</div>
        <div><div class="pc-name">${esc(p.name)}</div><div class="pc-sub">${esc(p.position)} · ${esc(p.team.name)}</div></div>
        <div class="pc-live"><b>${dist.toFixed(1)}</b><span>km covered</span></div>
      </div>
      <div class="pc-grid">
        ${stat(touches, "touches")}${stat(passes ? `${Math.round((100 * done) / passes)}%` : "—", `pass acc. (${done}/${passes})`)}
        ${stat(diffN ? (diffSum / diffN).toFixed(2) : "—", "avg pass difficulty")}
        ${stat(top ? top.toFixed(1) : "—", "top speed km/h")}${stat(sprints, `sprints ≥ ${SPRINT_KMH}`)}
        ${stat(shots ? `${shots} · ${xg.toFixed(2)}` : "0", "shots · xG")}
        ${stat(goals, "goals")}${stat(tk, "tackles won")}${stat(ballTop ? ballTop.toFixed(0) : "—", "hardest shot km/h")}
      </div>
      <svg class="pc-heat" viewBox="-1 -1 107 70" aria-label="Touch heatmap">
        <rect x="0" y="0" width="105" height="68" fill="#1f6b3a" rx="1"/>
        <g fill="none" stroke="rgba(255,255,255,.6)" stroke-width=".4"><rect x="0" y="0" width="105" height="68"/><line x1="52.5" y1="0" x2="52.5" y2="68"/><circle cx="52.5" cy="34" r="9.15"/>
        <rect x="0" y="13.84" width="16.5" height="40.32"/><rect x="88.5" y="13.84" width="16.5" height="40.32"/></g>
        <g id="pcHeat"></g>
      </svg>`;
    renderHeat($("#pcHeat"), pid);
  }

  // re-render the player cam whenever a viewer changes who they follow
  document.addEventListener("change", (e) => { if (e.target.classList?.contains("p-player") || e.target.classList?.contains("p-team")) setTimeout(renderPlayerCam, 0); });
  $("#lineups").addEventListener("click", () => setTimeout(renderPlayerCam, 0));

  window.MMX = { reset, onEvent, onOverlay, onMoment, onTab, openReplay, startReel, rec };
  // render the empty state once app.js has set up the match
  setTimeout(() => { try { renderPlayerCam(); } catch { /* match not loaded yet */ } }, 800);
})();
