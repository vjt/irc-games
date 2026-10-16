// Chi si prende la fetta (peluche, #sbiffo 16/10): per i primi sei della
// classifica di un canale, la quota cumulata delle suore stese giorno per
// giorno, e in alternativa il posto in classifica giorno per giorno (le
// rimonte si vedono come incroci). I dati sono `s.quote` del data.json, fatti
// da `suore_stats._quote()`: serie dal LOG, che parte dal 6 settembre.
//
// Sta in un file suo, caricato dalla pagina prima dello script principale:
// `renderScope` chiama `suoreQuote(s)` e appende quello che torna. Niente
// librerie, SVG a mano come il resto della pagina. Il CSS lo porta con se'
// (un <style> iniettato una volta: la CSP accetta gli stili inline, non i
// fogli esterni).
//
// Colori: sei tinte categoriali validate contro il pannello scuro (#17181c),
// sempre nello stesso ordine, che e' l'ordine della classifica. Il colore non
// e' mai da solo: ogni linea ha il nick in fondo, e la legenda sotto.
(function () {
  "use strict";
  const COLORI = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"];
  const NS = "http://www.w3.org/2000/svg";
  const CSS = `
  .quote { margin-top: 1.6rem; }
  .quote .qhd { display: flex; flex-wrap: wrap; align-items: baseline; gap: .5rem; }
  .quote .qhd h2 { margin: 0; }
  .quote .modi { margin-left: auto; display: flex; gap: .3rem; }
  .quote button { font: inherit; font-size: .68rem; color: var(--dim); background: none; border: 1px solid var(--line); border-radius: 99px; padding: .1rem .55rem; cursor: pointer; }
  .quote button:hover { color: var(--ink); }
  .quote button.on { color: var(--gold); border-color: var(--gold); }
  .quote .plot { position: relative; margin-top: .6rem; }
  .quote svg { display: block; width: 100%; overflow: visible; touch-action: pan-y; }
  .quote svg text { font: .66rem var(--mono); fill: var(--dim); }
  .quote svg text.nm { font-size: .7rem; fill: var(--ink); }
  .quote .tip { position: absolute; top: 0; pointer-events: none; display: none; background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: .3rem .5rem; font-size: .7rem; white-space: nowrap; z-index: 2; }
  .quote .tip b { display: block; color: var(--dim); font-weight: 600; margin-bottom: .1rem; }
  .quote .tip i, .quote .leg i { display: inline-block; width: .65rem; height: .2rem; border-radius: 1px; vertical-align: middle; margin-right: .35rem; }
  .quote .tip span { float: right; margin-left: 1rem; }
  .quote .leg { display: flex; flex-wrap: wrap; gap: .3rem; margin-top: .5rem; }
  .quote .leg button.off { opacity: .4; }
  .quote .nota { color: var(--dim); font-size: .72rem; margin: .5rem 0 0; }
  `;

  function stile() {
    if (document.getElementById("quote-css")) return;
    const st = document.createElement("style");
    st.id = "quote-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function nodo(tag, attrs, txt) {
    const n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(k => n.setAttribute(k, attrs[k]));
    if (txt !== undefined) n.textContent = txt;
    return n;
  }

  function h(tag, cls, txt) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined) n.textContent = txt;
    return n;
  }

  const gg = iso => iso.slice(8, 10) + "/" + iso.slice(5, 7);

  window.suoreQuote = function (s) {
    const q = s && s.quote;
    if (!q || !q.nick || !q.nick.length || q.giorni.length < 2) return null;
    stile();
    const righe = q.nick.map((r, i) => Object.assign({colore: COLORI[i % COLORI.length]}, r));
    let modo = "quota";
    let scelto = null;

    const box = h("div", "quote");
    const hd = h("div", "qhd");
    hd.appendChild(h("h2", null, "Chi si prende la fetta"));
    const modi = h("div", "modi");
    const bottoni = {};
    [["quota", "quota %"], ["posto", "posto"]].forEach(([k, label]) => {
      const b = h("button", k === modo ? "on" : null, label);
      b.type = "button";
      b.title = k === "quota" ? "quota delle stese fino a quel giorno"
        : "posto in classifica quel giorno";
      b.onclick = () => {
        modo = k;
        Object.keys(bottoni).forEach(x => bottoni[x].classList.toggle("on", x === modo));
        disegna();
      };
      bottoni[k] = b;
      modi.appendChild(b);
    });
    hd.appendChild(modi);
    box.appendChild(hd);

    const plot = h("div", "plot");
    const svg = nodo("svg", {role: "img"});
    const tip = h("div", "tip");
    plot.appendChild(svg);
    plot.appendChild(tip);
    box.appendChild(plot);

    // Legenda: tocchi un nick e resta acceso solo lui, ritocchi e tornano tutti.
    const leg = h("div", "leg");
    righe.forEach(r => {
      const b = h("button");
      b.type = "button";
      const i = h("i");
      i.style.background = r.colore;
      b.appendChild(i);
      b.appendChild(document.createTextNode(r.nick));
      b.onclick = () => {
        scelto = scelto === r.nick ? null : r.nick;
        leg.querySelectorAll("button").forEach((x, j) =>
          x.classList.toggle("off", scelto !== null && righe[j].nick !== scelto));
        disegna();
      };
      leg.appendChild(b);
    });
    box.appendChild(leg);

    const nota = h("p", "nota");
    const coda = " I primi " + righe.length + " della classifica per stese. Dal registro "
      + "dei colpi, che parte dal 6 settembre: quello che è successo prima non c'è"
      + (s.discrepanza ? ", e il registro non quadra coi contatori (vedi nota sotto)." : ".");
    const spiega = () => {
      nota.textContent = (modo === "quota"
        ? "Suore stese (.bang) di ognuno sul totale stese del canale, fino a quel giorno."
        : "Posto in classifica per stese (.bang) a fine giornata, fra tutti quelli del registro.")
        + coda;
    };
    box.appendChild(nota);

    let W = 0;
    const H = 230, M = {t: 10, r: 86, b: 22, l: 34};
    const n = q.giorni.length;

    function disegna() {
      spiega();
      W = Math.max(svg.clientWidth || plot.clientWidth || 0, 260);
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      svg.setAttribute("height", H);
      svg.textContent = "";
      const pw = W - M.l - M.r, ph = H - M.t - M.b;
      const x = i => M.l + i / (n - 1) * pw;
      let y, ticks;
      if (modo === "quota") {
        const top = Math.max(10, Math.ceil(Math.max(...righe.map(r => Math.max(...r.quota))) / 10) * 10);
        const passo = top > 60 ? 20 : 10;
        y = v => M.t + ph - v / top * ph;
        ticks = [];
        for (let v = 0; v <= top; v += passo) ticks.push([v, v + "%"]);
      } else {
        const fondo = Math.max(2, ...righe.map(r => Math.max(0, ...r.posto.filter(v => v !== null))));
        y = v => M.t + (v - 1) / (fondo - 1) * ph;
        const passo = fondo > 12 ? 5 : fondo > 6 ? 2 : 1;
        ticks = [[1, "1º"]];
        for (let v = passo === 1 ? 2 : passo; v <= fondo; v += passo) ticks.push([v, v + "º"]);
      }
      svg.setAttribute("aria-label", modo === "quota"
        ? "Quota cumulata delle suore stese per giorno" : "Posto in classifica per giorno");
      // griglia orizzontale, recessiva
      ticks.forEach(([v, label]) => {
        svg.appendChild(nodo("line", {x1: M.l, x2: M.l + pw, y1: y(v), y2: y(v),
          stroke: "var(--line)", "stroke-width": 1}));
        svg.appendChild(nodo("text", {x: M.l - 6, y: y(v) + 3, "text-anchor": "end"}, label));
      });
      // un'etichetta a settimana, il lunedi', come le barre; sul telefono una
      // ogni due, o le date si toccano
      let ultimaX = -Infinity;
      q.giorni.forEach((d, i) => {
        if (new Date(d + "T00:00:00Z").getUTCDay() !== 1) return;
        if (x(i) - ultimaX < 46) return;
        ultimaX = x(i);
        svg.appendChild(nodo("line", {x1: x(i), x2: x(i), y1: M.t + ph, y2: M.t + ph + 4,
          stroke: "var(--line)"}));
        svg.appendChild(nodo("text", {x: x(i), y: H - 6, "text-anchor": "middle"}, gg(d)));
      });
      // le linee, l'ultima della classifica sotto e la prima sopra
      const val = r => modo === "quota" ? r.quota : r.posto;
      const etichette = [];
      righe.slice().reverse().forEach(r => {
        const vs = val(r);
        let d = "", su = false;
        vs.forEach((v, i) => {
          if (v === null) { su = false; return; }
          d += (su ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1) + " ";
          su = true;
        });
        const spento = scelto !== null && r.nick !== scelto;
        if (d) {
          // anello del colore del pannello sotto la linea: dove si incrociano
          // si capisce quale passa sopra
          svg.appendChild(nodo("path", {d, fill: "none", stroke: "var(--panel)",
            "stroke-width": 4, "stroke-linejoin": "round", opacity: spento ? 0 : 1}));
          svg.appendChild(nodo("path", {d, fill: "none", stroke: r.colore,
            "stroke-width": r.nick === scelto ? 2.5 : 2, "stroke-linejoin": "round",
            "stroke-linecap": "round", opacity: spento ? .18 : 1}));
        }
        const ult = vs[n - 1];
        if (ult !== null && !spento) etichette.push({r, y: y(ult), v: ult});
      });
      // nick in fondo alla linea, spinti in giu' se si accavallano
      etichette.sort((a, b) => a.y - b.y);
      let prima = -Infinity;
      etichette.forEach(e => {
        e.ly = Math.max(e.y, prima + 12);
        prima = e.ly;
      });
      const sfora = prima - (M.t + ph + 4);
      if (sfora > 0) etichette.forEach(e => { e.ly -= sfora; });
      etichette.forEach(e => {
        svg.appendChild(nodo("circle", {cx: x(n - 1), cy: e.y, r: 3, fill: e.r.colore,
          stroke: "var(--panel)", "stroke-width": 1.5}));
        const t = nodo("text", {class: "nm", x: x(n - 1) + 8, y: e.ly + 4});
        t.appendChild(nodo("tspan", {}, e.r.nick.length > 9 ? e.r.nick.slice(0, 8) + "…" : e.r.nick));
        t.appendChild(nodo("tspan", {dx: 4, fill: "var(--dim)"},
          modo === "quota" ? Math.round(e.v) + "%" : e.v + "º"));
        svg.appendChild(t);
      });
      // mirino per l'hover, nascosto finche' non serve
      const croce = nodo("line", {y1: M.t, y2: M.t + ph, stroke: "var(--dim)",
        "stroke-width": 1, "stroke-dasharray": "2 3", visibility: "hidden"});
      svg.appendChild(croce);
      const area = nodo("rect", {x: M.l, y: 0, width: pw, height: H, fill: "transparent"});
      svg.appendChild(area);
      const muovi = ev => {
        const rr = svg.getBoundingClientRect();
        const px = (ev.clientX - rr.left) / rr.width * W;
        const i = Math.round(Math.min(Math.max((px - M.l) / pw, 0), 1) * (n - 1));
        croce.setAttribute("x1", x(i));
        croce.setAttribute("x2", x(i));
        croce.setAttribute("visibility", "visible");
        const ordinati = righe.slice().sort((a, b) => {
          const va = val(a)[i], vb = val(b)[i];
          if (va === vb) return 0;
          if (va === null) return 1;
          if (vb === null) return -1;
          return modo === "quota" ? vb - va : va - vb;
        });
        tip.textContent = "";
        tip.appendChild(h("b", null, gg(q.giorni[i]) + " · " + q.totale[i] + " stese in tutto"));
        ordinati.forEach(r => {
          const v = val(r)[i];
          const riga = h("div");
          const c = h("i");
          c.style.background = r.colore;
          riga.appendChild(c);
          riga.appendChild(document.createTextNode(r.nick));
          riga.appendChild(h("span", null, v === null ? "—"
            : modo === "quota" ? v.toFixed(1) + "%" : v + "º"));
          tip.appendChild(riga);
        });
        tip.style.display = "block";
        const sx = x(i) / W * rr.width;
        const tw = tip.offsetWidth;
        tip.style.left = Math.max(0, Math.min(sx > rr.width / 2 ? sx - tw - 10 : sx + 10,
          rr.width - tw)) + "px";
      };
      area.addEventListener("pointermove", muovi);
      area.addEventListener("pointerdown", muovi);
      area.addEventListener("pointerleave", () => {
        croce.setAttribute("visibility", "hidden");
        tip.style.display = "none";
      });
    }

    // Si disegna alla larghezza vera, e si ridisegna quando cambia: in un
    // viewBox fisso il testo diventerebbe illeggibile sul telefono.
    if (window.ResizeObserver) {
      let ultima = 0;
      new ResizeObserver(() => {
        const w = svg.clientWidth;
        if (w && Math.abs(w - ultima) > 1) { ultima = w; disegna(); }
      }).observe(svg);
    } else {
      setTimeout(disegna, 0);
    }
    return box;
  };
})();
