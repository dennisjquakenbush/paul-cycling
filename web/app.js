/* Paul Quakenbush cycling dashboard - renders window.PAUL_DATA. No dependencies. */
(function () {
  const D = window.PAUL_DATA;
  const $ = (id) => document.getElementById(id);
  const el = (t, c, html) => { const e = document.createElement(t); if (c) e.className = c; if (html != null) e.innerHTML = html; return e; };
  const fmt = (n, d = 0) => (n == null ? "-" : Number(n).toFixed(d));
  const NS = "http://www.w3.org/2000/svg";
  const svg = (w, h) => { const s = document.createElementNS(NS, "svg"); s.setAttribute("viewBox", `0 0 ${w} ${h}`); return s; };
  const node = (name, attrs, txt) => { const n = document.createElementNS(NS, name); for (const k in attrs) n.setAttribute(k, attrs[k]); if (txt != null) n.textContent = txt; return n; };
  const daysBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 86400000);
  const today = D.generated_at.slice(0, 10);

  /* imperial unit helpers */
  const miles = (km) => (km == null ? null : km * 0.621371);
  const feet = (m) => (m == null ? null : m * 3.28084);
  const lbs = (kg) => (kg == null ? null : kg * 2.20462);

  /* ---- plain-English glossary shown in hover tooltips ---- */
  const GLOSSARY = {
    readiness: "A quick verdict on how fresh Paul is today, from his form (TSB) and recovery signals.",
    ctl: "Fitness (CTL): a 42-day weighted average of daily training stress. Rises slowly as he trains consistently.",
    atl: "Fatigue (ATL): a 7-day weighted average of training stress. Spikes after hard days, drops with rest.",
    tsb: "Form (TSB) = Fitness minus Fatigue. Positive means fresh and race-ready; very negative means tired and needing recovery.",
    ramp: "How fast fitness is climbing per week. For a 16-year-old, more than about +5/week risks burnout or injury.",
    ftp: "Functional Threshold Power: the highest power he could hold for roughly an hour. The anchor for zones and training load. Here it is an estimate, not a tested number.",
    wkg: "Watts per kilogram: power relative to body weight. The number that matters most on climbs.",
    vo2: "VO2max: the size of the aerobic engine (max oxygen use), estimated by Garmin from his rides. 64 is very strong for his age.",
    maxhr: "The highest heart rate seen in his rides - used as a reference for heart-rate effort.",
    np: "Normalized Power: an average that weights hard surges more heavily, so it reflects the real cost of a spiky mountain-bike ride better than plain average power.",
    tss: "Training Stress Score: one number for how hard a ride was, combining intensity and duration. About 100 equals a hard steady hour at threshold.",
    decoupling: "How much power-to-heart-rate drifts apart over a ride. Under 5% is good aerobic durability; higher means he faded or started tired.",
    powercurve: "His best power for every duration across the season - a fingerprint of his strengths, from a 5-second sprint to a 60-minute grind.",
    load: "Fitness, fatigue and form over the whole season. Watch fitness (blue) climb steadily while form (the bars) dips after hard blocks and rises into races.",
    tiz: "How his riding time splits across intensity zones. A polarized plan is mostly easy riding with the hard days genuinely hard.",
    weekly: "Total training stress per week - a simple view of how much work he is doing and whether it is jumping around.",
    hrv: "Heart Rate Variability: beat-to-beat variation measured overnight. Higher and steady is good; a drop can mean fatigue or illness.",
    resting_hr: "Resting heart rate. A rise of several beats above his baseline often means fatigue, stress, or getting sick.",
    sleep: "Hours asleep. Juniors need 8-10 hours - it is when training actually turns into fitness.",
    fueling: "How much to eat and drink to fuel training and racing. For a 16-year-old this is always generous - never a diet.",
    calendar: "The DINO and NICA races. Turn a race off with the switch if he is not doing it - the next-race and taper advice update to match.",
    thisweek: "A suggested week of training shaped by his current form, how fast he is ramping, and the next race. A smart default, not a fixed prescription.",
    riders: "Riders from official DINO results to train with and race against. Based on the rounds Paul has actually raced, so it sharpens as the season goes on.",
  };
  const tip = (key) => {
    const t = GLOSSARY[key]; if (!t) return null;
    const s = el("span", "tip", "?");
    s.appendChild(el("span", "tt", t));
    return s;
  };
  const header = (key, html) => { const h = el("h2", null, html); const t = tip(key); if (t) h.appendChild(t); return h; };

  /* ---- static Indiana race calendar (from PAUL_DATA, falls back to constant) ---- */
  const RACES = (D.race_calendar && D.race_calendar.length) ? D.race_calendar.map(r => ({ name: r.name, date: r.date, series: r.series })) : [];
  const raceKey = (r) => `${r.date}|${r.name}`;
  let excluded = new Set(D.excluded_races || []);   // updated live from /api/config

  const upcoming = () => RACES.filter(r => r.date >= today && !excluded.has(raceKey(r))).sort((a, b) => a.date < b.date ? -1 : 1);

  /* ================= header ================= */
  const p = D.profile;
  $("subhead").textContent = `Mountain bike racer - DINO & Indiana NICA  ·  age ${p.age}  ·  ${Math.round(lbs(p.weight_kg))} lb`;
  $("stamp").innerHTML = `updated ${new Date(D.generated_at).toLocaleString()}<br>${D.data_quality.total_rides} rides analyzed`;

  /* ================= readiness ================= */
  (function () {
    const r = D.readiness, s = $("readiness");
    const vClass = { "fresh": "fresh", "normal": "normal", "watch recovery": "watch",
      "fatigued": "fatigued", "dig-a-hole territory": "dig", "unknown": "" }[r.verdict] || "normal";
    s.appendChild(header("readiness", "Readiness <span class='sub'>&mdash; today at a glance</span>"));
    const head = el("div", "ready-head");
    head.appendChild(el("div", "verdict " + vClass, r.verdict));
    s.appendChild(head);

    const pmc = D.pmc[D.pmc.length - 1] || {};
    const m = el("div", "ready-metrics");
    const met = (n, l, key) => { const d = el("div", "metric");
      d.appendChild(el("div", "n", n));
      const lab = el("div", "l"); lab.appendChild(document.createTextNode(l)); const t = tip(key); if (t) lab.appendChild(t);
      d.appendChild(lab); return d; };
    m.appendChild(met(fmt(pmc.ctl), "Fitness (CTL)", "ctl"));
    m.appendChild(met(fmt(pmc.atl), "Fatigue (ATL)", "atl"));
    m.appendChild(met((pmc.tsb > 0 ? "+" : "") + fmt(pmc.tsb), "Form (TSB)", "tsb"));
    if (r.ramp != null) m.appendChild(met((r.ramp > 0 ? "+" : "") + fmt(r.ramp), "Ramp / wk", "ramp"));
    s.appendChild(m);

    const ul = el("ul", "reasons");
    (r.why || []).forEach(w => ul.appendChild(el("li", null, w)));
    s.appendChild(ul);

    if (r.actions && r.actions.length) {
      const a = el("div", "actions");
      a.appendChild(el("div", "lab", "What to do"));
      const au = el("ul"); r.actions.forEach(x => au.appendChild(el("li", null, x)));
      a.appendChild(au); s.appendChild(a);
    }
  })();

  /* ================= fitness ================= */
  (function () {
    const f = D.fitness, s = $("fitness");
    s.appendChild(header("ftp", "Fitness profile"));
    const g = el("div", "stats");
    const info = f.ftp_info || {};
    const range = info.range ? ` <small>(${info.range[0]}&ndash;${info.range[1]})</small>` : "";
    const stat = (n, l, key) => { const d = el("div", "stat"); d.appendChild(el("div", "n", n));
      const lab = el("div", "l"); lab.appendChild(document.createTextNode(l)); const t = tip(key); if (t) lab.appendChild(t);
      d.appendChild(lab); return d; };
    g.appendChild(stat(`${f.ftp}<small> W</small>${range}`, "Estimated FTP", "ftp"));
    g.appendChild(stat(`${fmt(f.ftp_wkg, 2)}<small> W/kg</small>`, "FTP power-to-weight", "wkg"));
    g.appendChild(stat(`${f.vo2max_cycling || "-"}`, "VO2max (cycling)", "vo2"));
    g.appendChild(stat(`${f.max_hr || "-"}<small> bpm</small>`, "Max HR seen", "maxhr"));
    s.appendChild(g);
    const rt = el("div", "note");
    rt.innerHTML = `<span class="pill ${info.confidence || ''}">${(info.confidence || 'est').toUpperCase()} confidence</span> ` +
      `Rider type: <strong style="color:var(--accent)">${f.rider_type}</strong>. ${f.rider_note}`;
    s.appendChild(rt);
    if (info.note) s.appendChild(el("div", "note", info.note));
  })();

  /* ================= next race ================= */
  function renderNextRace() {
    const s = $("nextrace"); s.innerHTML = "";
    s.appendChild(el("h2", null, "Next race"));
    const up = upcoming();
    if (!up.length) { s.appendChild(el("div", "note", "No further races selected.")); return; }
    const nr = up[0];
    const dleft = daysBetween(today, nr.date);
    const big = el("div");
    big.appendChild(el("div", "countdown", `${dleft} days`));
    big.appendChild(el("div", "race-name", nr.name));
    big.appendChild(el("div", "race-series", `${nr.series} series  ·  ${new Date(nr.date + "T00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}`));
    s.appendChild(big);
    const g = el("div", "note");
    if (dleft <= 3) g.textContent = "Race week: short sharpeners only, extra sleep, top up carbs. Legs over fitness now.";
    else if (dleft <= 10) g.textContent = "Begin the taper: hold intensity, cut volume ~30-40%. Arrive fresh, not fatigued.";
    else g.textContent = "Build phase: this is the window for hard, race-specific work before the taper.";
    s.appendChild(g);
  }

  /* ================= power curve ================= */
  (function () {
    const s = $("powercurve");
    s.appendChild(header("powercurve", "Power curve <span class='sub'>&mdash; best efforts across the season</span>"));
    const data = D.power_curve.filter(d => d.watts);
    if (!data.length) { s.appendChild(el("div", "note", "No power data.")); return; }
    const W = 1000, H = 320, PL = 46, PR = 46, PT = 16, PB = 40;
    const g = svg(W, H);
    const maxW = Math.max(...data.map(d => d.watts));
    const x = i => PL + (i / (data.length - 1)) * (W - PL - PR);
    const y = w => PT + (1 - w / (maxW * 1.05)) * (H - PT - PB);
    for (let k = 0; k <= 4; k++) {
      const wv = maxW * 1.05 * k / 4;
      g.appendChild(node("line", { x1: PL, x2: W - PR, y1: y(wv), y2: y(wv), class: "gridline" }));
      g.appendChild(node("text", { x: PL - 8, y: y(wv) + 3, class: "axis-txt", "text-anchor": "end" }, Math.round(wv)));
    }
    let dpath = "", apath = `M ${x(0)} ${y(0)}`;
    data.forEach((d, i) => { const cmd = i ? "L" : "M"; dpath += `${cmd} ${x(i)} ${y(d.watts)} `; apath += `L ${x(i)} ${y(d.watts)} `; });
    apath += `L ${x(data.length - 1)} ${y(0)} Z`;
    const grad = node("linearGradient", { id: "pcg", x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.appendChild(node("stop", { offset: "0%", "stop-color": "var(--accent)", "stop-opacity": .35 }));
    grad.appendChild(node("stop", { offset: "100%", "stop-color": "var(--accent)", "stop-opacity": 0 }));
    const defs = node("defs", {}); defs.appendChild(grad); g.appendChild(defs);
    g.appendChild(node("path", { d: apath, fill: "url(#pcg)" }));
    g.appendChild(node("path", { d: dpath, fill: "none", stroke: "var(--accent)", "stroke-width": 2.5 }));
    data.forEach((d, i) => {
      g.appendChild(node("circle", { cx: x(i), cy: y(d.watts), r: 4, fill: "var(--accent)", stroke: "var(--bg)", "stroke-width": 2 }));
      g.appendChild(node("text", { x: x(i), y: y(d.watts) - 12, class: "axis-txt", "text-anchor": "middle", fill: "var(--text)", "font-size": 11 }, `${Math.round(d.watts)}w`));
      g.appendChild(node("text", { x: x(i), y: y(d.watts) - 24, class: "axis-txt", "text-anchor": "middle" }, `${fmt(d.wkg, 1)} w/kg`));
      g.appendChild(node("text", { x: x(i), y: H - PB + 18, class: "axis-txt", "text-anchor": "middle", fill: "var(--text)" }, d.label));
    });
    s.appendChild(g);
  })();

  /* ================= training load (PMC) ================= */
  (function () {
    const s = $("load");
    s.appendChild(header("load", "Training load <span class='sub'>&mdash; Fitness (CTL), Fatigue (ATL) and Form (TSB)</span>"));
    const pmc = D.pmc;
    if (!pmc.length) return;
    const W = 1000, H = 300, PL = 40, PR = 40, PT = 14, PB = 34;
    const g = svg(W, H);
    const n = pmc.length;
    const maxV = Math.max(...pmc.map(d => Math.max(d.ctl, d.atl))) * 1.1;
    const minTsb = Math.min(...pmc.map(d => d.tsb)), maxTsb = Math.max(...pmc.map(d => d.tsb));
    const x = i => PL + (i / (n - 1)) * (W - PL - PR);
    const y = v => PT + (1 - v / maxV) * (H - PT - PB);
    const tspan = Math.max(Math.abs(minTsb), Math.abs(maxTsb), 1);
    const yT = v => PT + (H - PT - PB) / 2 - (v / tspan) * ((H - PT - PB) / 2);
    for (let k = 0; k <= 4; k++) { const vv = maxV * k / 4;
      g.appendChild(node("line", { x1: PL, x2: W - PR, y1: y(vv), y2: y(vv), class: "gridline" }));
      g.appendChild(node("text", { x: PL - 6, y: y(vv) + 3, class: "axis-txt", "text-anchor": "end" }, Math.round(vv))); }
    const line = (key, color, w) => { let d = ""; pmc.forEach((pt, i) => d += `${i ? "L" : "M"} ${x(i)} ${y(pt[key])} `);
      g.appendChild(node("path", { d, fill: "none", stroke: color, "stroke-width": w })); };
    pmc.forEach((pt, i) => { if (i % 2) return; const yy = yT(pt.tsb), y0 = yT(0);
      g.appendChild(node("line", { x1: x(i), x2: x(i), y1: y0, y2: yy, stroke: pt.tsb >= 0 ? "var(--green)" : "var(--pink)", "stroke-width": 1, opacity: .25 })); });
    line("ctl", "var(--blue)", 2.5);
    line("atl", "var(--pink)", 1.8);
    let lastM = "";
    pmc.forEach((pt, i) => { const mo = pt.date.slice(0, 7); if (mo !== lastM) { lastM = mo;
      g.appendChild(node("text", { x: x(i), y: H - 8, class: "axis-txt", "text-anchor": "middle" },
        new Date(pt.date + "T00:00").toLocaleDateString(undefined, { month: "short" }))); } });
    s.appendChild(g);
    s.appendChild(el("div", "legend",
      `<span><i style="background:var(--blue)"></i>Fitness (CTL)</span>
       <span><i style="background:var(--pink)"></i>Fatigue (ATL)</span>
       <span><i style="background:var(--green)"></i>Form +ve (fresh)</span>
       <span><i style="background:var(--pink);opacity:.5"></i>Form -ve (tired)</span>`));
  })();

  /* ================= time in zone ================= */
  (function () {
    const s = $("zones");
    s.appendChild(header("tiz", "Time in zone <span class='sub'>&mdash; last 6 weeks (power)</span>"));
    const tiz = D.time_in_zone;
    if (!tiz) { s.appendChild(el("div", "note", "Not enough power data for zone distribution.")); return; }
    const colors = { Z1: "var(--z1)", Z2: "var(--z2)", Z3: "var(--z3)", Z4: "var(--z4)", Z5: "var(--z5)", Z6: "var(--z6)", Z7: "var(--z7)" };
    const zoneW = D.zones || [];
    const maxPct = Math.max(...tiz.map(z => z.pct));
    tiz.forEach(z => {
      const row = el("div"); row.style.margin = "9px 0";
      const zdef = zoneW.find(zz => zz.zone.startsWith(z.zone));
      const label = zdef ? `${zdef.zone} · ${zdef.lo}-${zdef.hi}w` : z.zone;
      row.innerHTML = `<div style="display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:3px">
        <span>${label}</span><span>${z.pct}%</span></div>
        <div style="height:9px;background:rgba(255,255,255,.05);border-radius:6px;overflow:hidden">
        <div style="height:100%;width:${(z.pct / maxPct * 100).toFixed(1)}%;background:${colors[z.zone]};border-radius:6px"></div></div>`;
      s.appendChild(row);
    });
    const easy = tiz.filter(z => ["Z1", "Z2"].includes(z.zone)).reduce((a, z) => a + z.pct, 0);
    s.appendChild(el("div", "note", `${easy.toFixed(0)}% of riding time is easy (Z1-Z2). A polarized junior plan wants roughly 75-80% easy with hard days genuinely hard.`));
  })();

  /* ================= weekly TSS ================= */
  (function () {
    const s = $("weekly");
    s.appendChild(header("weekly", "Weekly load <span class='sub'>&mdash; TSS per week</span>"));
    const wk = D.weekly_tss.slice(-16);
    if (!wk.length) return;
    const W = 480, H = 220, PL = 30, PR = 10, PT = 12, PB = 40;
    const g = svg(W, H);
    const maxV = Math.max(...wk.map(d => d.tss), 1);
    const bw = (W - PL - PR) / wk.length * .68;
    const x = i => PL + (i + .5) / wk.length * (W - PL - PR);
    const y = v => PT + (1 - v / (maxV * 1.1)) * (H - PT - PB);
    for (let k = 0; k <= 3; k++) { const vv = maxV * 1.1 * k / 3;
      g.appendChild(node("line", { x1: PL, x2: W - PR, y1: y(vv), y2: y(vv), class: "gridline" }));
      g.appendChild(node("text", { x: PL - 5, y: y(vv) + 3, class: "axis-txt", "text-anchor": "end" }, Math.round(vv))); }
    wk.forEach((d, i) => {
      g.appendChild(node("rect", { x: x(i) - bw / 2, y: y(d.tss), width: bw, height: y(0) - y(d.tss), rx: 3, fill: "var(--accent)", opacity: .85 }));
      if (i % 2 === 0) g.appendChild(node("text", { x: x(i), y: H - 8, class: "axis-txt", "text-anchor": "middle" },
        new Date(d.week + "T00:00").toLocaleDateString(undefined, { month: "numeric", day: "numeric" })));
    });
    s.appendChild(g);
  })();

  /* ================= recovery ================= */
  (function () {
    const s = $("recovery");
    s.appendChild(header("hrv", "Recovery <span class='sub'>&mdash; from Apple Health (Apple Watch)</span>"));
    const rec = D.recovery;
    const has = rec && rec.source && (rec.hrv.length || rec.resting_hr.length || rec.sleep_h.length);
    if (!has) {
      const e = el("div", "empty");
      e.innerHTML = `<div class="big">Connect Apple Health for hands-off recovery tracking</div>
        <p>Paul records rides on Garmin but wears an <strong>Apple Watch</strong> for sleep and heart rate,
        so his resting HR, HRV and sleep live in Apple Health - Garmin has none of it. Set this up once and it
        updates every day on its own, no uploading:</p>
        <ol class="steps">
          <li><strong>The free, hands-off way (iOS Shortcut):</strong> on Paul's iPhone open the <strong>Shortcuts</strong> app &rarr;
          Automation &rarr; new <strong>Personal Automation</strong> &rarr; <strong>Time of Day, 8:00am, daily</strong>.
          Add actions to <em>Find Health Samples</em> for Resting Heart Rate, HRV (SDNN) and Sleep, build a dictionary
          <code>{date, resting_hr, hrv, sleep_h}</code>, and <em>Save File</em> to an iCloud Drive folder.</li>
          <li>Put that iCloud folder path in <code>config.json</code> under <code>apple_health_icloud_dir</code>. The noon job reads it automatically.</li>
          <li><strong>Or the app way:</strong> install <strong>Health Auto Export</strong> and point its daily JSON export at that iCloud folder.</li>
          <li><strong>Or one-off:</strong> Health app &rarr; profile &rarr; <strong>Export All Health Data</strong>, drop <code>export.zip</code> into <code>apple_health/</code>.</li>
        </ol>
        <p style="margin-top:10px">Ask Claude to "set up the Apple Health shortcut for Paul" and it will build the exact recipe and wire the folder for you.</p>`;
      s.appendChild(e);
      return;
    }
    const g = el("div", "stats"); g.style.gridTemplateColumns = "repeat(3,1fr)";
    const spark = (points, color, unit, label, trend, goodDown, key) => {
      const d = el("div", "stat");
      const last = points.length ? points[points.length - 1].v : null;
      let tHtml = "";
      if (trend != null) { const good = goodDown ? trend < 0 : trend > 0;
        tHtml = `<span style="color:${good ? 'var(--green)' : 'var(--amber)'};font-size:12px">${trend > 0 ? "+" : ""}${trend}</span>`; }
      d.appendChild(el("div", "n", `${last == null ? "-" : last}<small> ${unit}</small> ${tHtml}`));
      const lab = el("div", "l"); lab.appendChild(document.createTextNode(label)); const t = tip(key); if (t) lab.appendChild(t); d.appendChild(lab);
      if (points.length > 2) {
        const W = 240, H = 46; const sv = svg(W, H); sv.style.marginTop = "8px";
        const vs = points.map(p => p.v), mn = Math.min(...vs), mx = Math.max(...vs) || 1;
        const x = i => (i / (points.length - 1)) * W, y = v => 4 + (1 - (v - mn) / (mx - mn || 1)) * (H - 8);
        let path = ""; points.forEach((pt, i) => path += `${i ? "L" : "M"} ${x(i)} ${y(pt.v)} `);
        sv.appendChild(node("path", { d: path, fill: "none", stroke: color, "stroke-width": 2 }));
        d.appendChild(sv);
      }
      return d;
    };
    g.appendChild(spark(rec.hrv, "var(--blue)", "ms", "HRV (SDNN)", rec.hrv_trend, false, "hrv"));
    g.appendChild(spark(rec.resting_hr, "var(--pink)", "bpm", "Resting HR", rec.rhr_trend, true, "resting_hr"));
    g.appendChild(spark(rec.sleep_h, "var(--green)", "h", "Sleep", rec.sleep_trend, false, "sleep"));
    s.appendChild(g);
    s.appendChild(el("div", "note", `Source: Apple Health. Rising resting HR or falling HRV alongside high load are early fatigue signals.`));
  })();

  /* ================= fueling ================= */
  (function () {
    const f = D.fueling, s = $("fueling");
    if (!f) { s.style.display = "none"; return; }
    s.appendChild(header("fueling", "Fueling &amp; hydration <span class='sub'>&mdash; scaled to his size, always generous</span>"));
    const dc = f.daily_carbs_g;
    const daily = el("div", "fuel-card");
    daily.innerHTML = `<h3>Daily carbs (${f.weight_kg} kg)</h3>
      <div class="carb-pills">
        <div class="carb-pill">Rest / easy<br><b>${dc.rest_easy[0]}-${dc.rest_easy[1]}g</b></div>
        <div class="carb-pill">Moderate<br><b>${dc.moderate[0]}-${dc.moderate[1]}g</b></div>
        <div class="carb-pill">Hard / long<br><b>${dc.hard_or_long[0]}-${dc.hard_or_long[1]}g</b></div>
      </div><p>${dc.note}</p>`;

    const during = el("div", "fuel-card");
    let rows = f.during_ride.map(b => `<tr><td>${b.band}</td><td><b>${b.carbs_g_per_h} g/h</b></td><td>${b.fluid_oz_per_h} oz/h</td></tr>`).join("");
    during.innerHTML = `<h3>During the ride</h3>
      <table class="fuel-table" style="margin-top:4px"><tbody>${rows}</tbody></table>
      <p style="margin-top:8px">${f.during_ride[2].how}</p>`;

    const rd = f.race_day;
    const race = el("div", "fuel-card");
    race.innerHTML = `<h3>Race day</h3>
      <p><b>Night before:</b> ${rd.night_before}</p>
      <p><b>Breakfast:</b> ${rd.breakfast}</p>
      <p><b>Pre-start:</b> ${rd.pre_start}</p>
      <p><b>During:</b> ${rd.during}</p>
      <p><b>After:</b> ${rd.after}</p>`;

    const hy = f.hydration;
    const hyd = el("div", "fuel-card");
    hyd.innerHTML = `<h3>Hydration</h3>
      <p><b>On the bike:</b> ${hy.baseline_oz_per_h} oz/h.</p>
      <p><b>Heat:</b> ${hy.heat_note}</p>
      <p><b>Daily:</b> ${hy.daily}</p>`;

    const grid = el("div", "fuel-grid");
    [daily, during, race, hyd].forEach(c => grid.appendChild(c));
    s.appendChild(grid);
  })();

  /* ================= races (with toggles) ================= */
  function renderRaces() {
    const s = $("races"); s.innerHTML = "";
    s.appendChild(header("calendar", "Season calendar <span class='sub'>&mdash; DINO into NICA · switch off races he is skipping</span>"));
    const rideDates = new Set(D.rides.map(r => r.date));
    const up = upcoming();
    RACES.forEach(r => {
      const key = raceKey(r);
      const isExcluded = excluded.has(key);
      const past = r.date < today;
      const dleft = daysBetween(today, r.date);
      const isNext = !past && !isExcluded && up.length && raceKey(up[0]) === key;
      const row = el("div", "race-row" + (isNext ? " next" : "") + (isExcluded ? " excluded" : ""));
      const left = el("div");
      left.appendChild(el("div", "race-name", r.name + (rideDates.has(r.date) ? "  <span class='tag race'>ride file</span>" : "")));
      left.appendChild(el("div", "race-series", `${r.series}  ·  ${new Date(r.date + "T00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })}`));
      row.appendChild(left);

      const right = el("div"); right.style.cssText = "display:flex;align-items:center;gap:14px";
      const status = el("div"); status.style.textAlign = "right";
      if (past) status.innerHTML = `<span class="done">done</span>`;
      else if (isExcluded) status.innerHTML = `<span class="race-when">skipping</span>`;
      else status.innerHTML = `<span class="countdown">${dleft}d</span>`;
      right.appendChild(status);

      if (!past) {
        const lab = el("label", "race-toggle");
        const cb = el("input"); cb.type = "checkbox"; cb.checked = !isExcluded;
        cb.addEventListener("change", () => toggleRace(key, cb.checked));
        lab.appendChild(cb);
        lab.appendChild(el("span", "switch"));
        right.appendChild(lab);
      }
      row.appendChild(right);
      s.appendChild(row);
    });
  }

  function toggleRace(key, doing) {
    if (doing) excluded.delete(key); else excluded.add(key);
    renderRaces(); renderNextRace();
    const arr = Array.from(excluded);
    // Always keep a local copy (works on static GitHub hosting)...
    try { localStorage.setItem("excluded_races", JSON.stringify(arr)); } catch (e) {}
    // ...and persist to config.json via the server API when running locally.
    fetch("/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ excluded_races: arr }),
    }).catch(() => {});
  }

  /* ================= riders / rivals ================= */
  (function () {
    const s = $("riders");
    const ri = D.race_intel;
    if (!ri) { s.style.display = "none"; return; }
    s.appendChild(header("riders", "Ride partners &amp; rivals <span class='sub'>&mdash; who to chase and train with</span>"));
    const cat = el("div"); cat.style.cssText = "font-size:14px;margin:-4px 0 12px";
    cat.innerHTML = `Category: <strong style="color:var(--accent)">${ri.category}</strong>`;
    s.appendChild(cat);

    if (ri.garmin_flag) {
      const fl = el("div", "flag");
      fl.innerHTML = `<strong>Heads up:</strong> ${ri.garmin_flag}`;
      s.appendChild(fl);
    }

    const cols = el("div", "rider-cols");
    const col = (cls, title, list, showGap) => {
      if (!list || !list.length) return;
      const c = el("div", "rider-col " + cls);
      c.appendChild(el("h3", null, title));
      list.forEach(r => {
        const d = el("div", "rider");
        d.innerHTML = `<div class="rn">${r.name}${showGap && r.gap ? `<span class="gap">${r.gap}</span>` : ""}</div>
          <div class="rd">${r.detail}</div>`;
        c.appendChild(d);
      });
      cols.appendChild(c);
    };
    col("chase", "Chase these wheels", ri.chase, true);
    col("peers", "Train-with peers", ri.peers, false);
    col("asp", "Aspirational", ri.aspirational, false);
    s.appendChild(cols);

    if (ri.upcoming) s.appendChild(el("div", "note", `<strong>Upcoming:</strong> ${ri.upcoming}`));
    if (ri.nica) s.appendChild(el("div", "note", `<strong>NICA:</strong> ${ri.nica}`));
    if (ri.confidence_note) s.appendChild(el("div", "note", `<em>${ri.confidence_note}</em>`));
  })();

  /* ================= rides table ================= */
  (function () {
    const s = $("rides");
    s.appendChild(header("np", "Recent rides <span class='sub'>&mdash; last 12</span>"));
    const rows = D.rides.slice(0, 12);
    const t = el("table");
    t.innerHTML = `<thead><tr><th>Date</th><th>Ride</th><th>Type</th><th>Dur</th><th>mi</th><th>Elev</th><th>NP</th><th>Avg HR</th><th>TSS</th><th>Decouple</th></tr></thead>`;
    const tb = el("tbody");
    rows.forEach(r => {
      const tr = el("tr");
      const mins = Math.round((r.duration_s || 0) / 60);
      const typ = r.type === "road_biking" ? "road" : (r.type === "mountain_biking" ? "mtb" : r.type.replace("_", " "));
      const tc = r.type === "road_biking" ? "road" : "";
      const dec = r.decoupling == null ? "-" : `${r.decoupling}%`;
      const decColor = r.decoupling != null && r.decoupling > 5 ? "color:var(--amber)" : "";
      tr.innerHTML = `<td>${r.date.slice(5)}</td>
        <td>${(r.name || "").slice(0, 26)}</td>
        <td><span class="tag ${tc}">${typ}</span></td>
        <td>${mins}m</td><td>${fmt(miles(r.distance_km), 1)}</td><td>${Math.round(feet(r.elev_gain_m))} ft</td>
        <td>${r.np == null ? "-" : Math.round(r.np)}</td>
        <td>${r.avg_hr || "-"}</td><td>${fmt(r.tss, 0)}</td>
        <td style="${decColor}">${dec}</td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    s.appendChild(t);
  })();

  /* ================= quality footer ================= */
  (function () {
    const q = D.data_quality;
    $("quality").innerHTML =
      `Garmin: ${q.total_rides} rides (${q.rides_with_power} with power, ${q.rides_with_hr} with HR) from ${q.date_range[0]} to ${q.date_range[1]}.
       ${q.dropped_recording_errors} recording-error activities filtered out.
       Recovery source: ${q.recovery_source || "none yet - connect Apple Health"}.<br>
       Generated ${new Date(D.generated_at).toLocaleString()} · analysis is informational, not a substitute for a coach or medical advice.`;
  })();

  /* ---- initial render, then reconcile excluded set from the best available source ---- */
  // localStorage first (instant, works on static hosting), baked data as the base.
  try {
    const ls = JSON.parse(localStorage.getItem("excluded_races") || "null");
    if (Array.isArray(ls)) excluded = new Set(ls);
  } catch (e) {}
  renderNextRace();
  renderRaces();
  // If a local config server is present, it is authoritative - sync from it.
  fetch("/api/config").then(r => r.ok ? r.json() : null).then(cfg => {
    if (cfg && Array.isArray(cfg.excluded_races)) {
      excluded = new Set(cfg.excluded_races);
      try { localStorage.setItem("excluded_races", JSON.stringify(cfg.excluded_races)); } catch (e) {}
      renderNextRace(); renderRaces();
    }
  }).catch(() => {});
})();
