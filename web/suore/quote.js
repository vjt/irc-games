// La corsa in vetta (peluche, #sbiffo 16/10; era "Chi si prende la fetta",
// rimasuglio della quota %, rinominata 24/10): per i primi sei della
// classifica di un canale, le stese cumulate giorno per giorno normalizzate su
// chi era in cima quel giorno ("vs capo": il capo e' un segmento piatto in
// alto finche' comanda, gli altri sotto a distanza di distacco), e in
// alternativa le stese cumulate in numero secco ("colpi", che ha preso il
// posto della quota % sul totale: peluche, 16/10) e il posto in classifica
// giorno per giorno (le rimonte si vedono come incroci). Niente percentuali e
// niente scala in "vs capo" (peluche: «cava via la percentuale che non
// serve»): i numeri veri, stese e distacco, stanno nel tooltip. I dati sono `s.quote` del data.json, fatti
// da `suore_stats._quote()`: serie dal LOG, che parte dal 6 settembre.
//
// Tre serie dietro un selettore (puppy, #sbiffo 19/10): tutte le prese
// (.bang + .bef, come contano nottambuli e soffiate, e di suo si parte da qui),
// le sole stese (com'era, peluche 16/10) e le sole amiche. Stessa forma, dal
// data.json `s.quote_tutte`, `s.quote`, `s.quote_amiche`.
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
  .quote .serie { display: flex; gap: .3rem; margin-top: .4rem; }
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

  let conteggio = 0;
  const gg = iso => iso.slice(8, 10) + "/" + iso.slice(5, 7);

  // Le parole di ogni serie: il dato si chiama sempre `stese`, il testo no.
  const SERIE = [
    ["tutte", "quote_tutte", "tutte", "prese (.bang e .bef)", "prese"],
    ["stese", "quote", "stese", "stese (.bang)", "stese"],
    ["amiche", "quote_amiche", "amiche", "fatte amiche (.bef)", "amiche"],
  ];
  const buona = q => q && q.nick && q.nick.length && q.giorni.length >= 2;

  window.suoreQuote = function (s) {
    const serie = SERIE.filter(([, k]) => s && buona(s[k]));
    if (!serie.length) return null;
    stile();
    let q, righe, n, parole;
    let modo = "capo";
    let scelto = null;
    // `#corsa=posto,stese` apre gia' su quel modo e quella serie (peluche,
    // #sbiffo 25/10): il 🔗 del titolo copia quello che si sta guardando.
    const voluto = /^#corsa=([a-z]+)(?:,([a-z]+))?$/.exec(location.hash) || [];
    if (["capo", "colpi", "posto"].includes(voluto[1])) modo = voluto[1];

    const box = h("div", "quote");
    box.dataset.ancora = "corsa";          // #corsa: la prima visibile
    const hd = h("div", "qhd");
    hd.appendChild(h("h2", null, "La corsa in vetta"));
    const modi = h("div", "modi");
    const bottoni = {};
    const TITOLI = {capo: "in proporzione a chi era in cima quel giorno",
      colpi: "quante fino a quel giorno", posto: "posto in classifica quel giorno"};
    [["capo", "vs capo"], ["colpi", "colpi"], ["posto", "posto"]].forEach(([k, label]) => {
      const b = h("button", k === modo ? "on" : null, label);
      b.type = "button";
      b.title = TITOLI[k];
      b.onclick = () => {
        modo = k;
        Object.keys(bottoni).forEach(x => bottoni[x].classList.toggle("on", x === modo));
        disegna();
        segna();
      };
      bottoni[k] = b;
      modi.appendChild(b);
    });
    hd.appendChild(modi);
    box.appendChild(hd);
    const sel = h("div", "serie");
    const bserie = {};
    serie.forEach(([k, , label]) => {
      const b = h("button", null, label);
      b.type = "button";
      b.onclick = () => { usa(k); disegna(); segna(); };
      bserie[k] = b;
      sel.appendChild(b);
    });
    if (serie.length > 1) box.appendChild(sel);

    const plot = h("div", "plot");
    const svg = nodo("svg", {role: "img"});
    const tip = h("div", "tip");
    plot.appendChild(svg);
    plot.appendChild(tip);
    box.appendChild(plot);

    // Legenda: tocchi un nick e resta acceso solo lui, ritocchi e tornano tutti.
    // Si rifà a ogni cambio di serie: i primi sei non sono gli stessi.
    const leg = h("div", "leg");
    box.appendChild(leg);
    function legenda() {
      leg.textContent = "";
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
    }

    const nota = h("p", "nota");
    const spiega = () => {
      const [, , , lungo, corto] = parole;
      nota.textContent = (modo === "capo"
        ? "Suore " + lungo + " di ognuno fino a quel giorno, in proporzione a chi era in "
          + "cima: il capo sta piatto in alto, quanto gli sta sotto è il distacco."
        : modo === "colpi"
        ? "Suore " + lungo + " di ognuno, sommate giorno per giorno."
        : "Posto in classifica per " + corto + " a fine giornata, fra tutti quelli del registro.")
        + " I primi " + righe.length + " della classifica per " + corto + ". Dal registro "
        + "dei colpi, che parte dal 6 settembre: quello che è successo prima non c'è"
        + (s.discrepanza ? ", e il registro non quadra coi contatori (vedi nota sotto)." : ".");
    };
    box.appendChild(nota);

    const id = ++conteggio;
    let W = 0;
    const H = 230, M = {t: 10, r: 86, b: 22, l: 34};

    function usa(k) {
      parole = serie.find(x => x[0] === k);
      q = s[parole[1]];
      righe = q.nick.map((r, i) => Object.assign({colore: COLORI[i % COLORI.length]}, r));
      n = q.giorni.length;
      scelto = null;
      Object.keys(bserie).forEach(x => bserie[x].classList.toggle("on", x === k));
      legenda();
    }
    usa((serie.find(x => x[0] === voluto[2]) || serie[0])[0]);

    // Un clic su modo o serie scrive lo stato nell'URL (peluche, #sbiffo
    // 25/10), col canale che ha messo il 🔗 del titolo: si copia dalla barra.
    function segna() {
      const a = box.querySelector("a.perma[data-base]");
      const base = a ? a.dataset.base : location.pathname + location.search + "#corsa";
      history.replaceState(null, "", base + "=" + box.dataset.stato);
    }

    function disegna() {
      box.dataset.stato = modo + "," + parole[0];
      spiega();
      W = Math.max(svg.clientWidth || plot.clientWidth || 0, 260);
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      svg.setAttribute("height", H);
      svg.textContent = "";
      const pw = W - M.l - M.r, ph = H - M.t - M.b;
      const x = i => M.l + i / (n - 1) * pw;
      let y, ticks;
      if (modo === "capo") {
        // in cima il capo, in fondo lo zero: due righe di riferimento, niente testo
        y = v => M.t + ph - v / 100 * ph;
        ticks = [[100, ""], [0, ""]];
      } else if (modo === "colpi") {
        // scala sul piu' alto in grafico, a passi tondi, al massimo sei righe
        const max = Math.max(1, ...righe.map(r => r.stese[n - 1]));
        const passo = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500]
          .find(p => max / p <= 5) || 1000;
        const top = Math.ceil(max / passo) * passo;
        y = v => M.t + ph - v / top * ph;
        ticks = [];
        for (let v = 0; v <= top; v += passo) ticks.push([v, String(v)]);
      } else {
        // Solo i posti di chi e' in grafico, 1..6 (peluche, 16/10): chi in un
        // giorno stava piu' giu' esce dal bordo di sotto, tagliato dal clip
        // qui sotto. Il posto vero resta nel tooltip.
        const fondo = Math.max(2, righe.length);
        y = v => M.t + (v - 1) / (fondo - 1) * ph;
        ticks = [];
        for (let v = 1; v <= fondo; v++) ticks.push([v, v + "º"]);
      }
      svg.setAttribute("aria-label", modo === "capo"
        ? "Suore " + parole[4] + " per giorno, in proporzione al primo in classifica"
        : modo === "colpi" ? "Suore " + parole[4] + " cumulate per giorno"
        : "Posto in classifica per giorno");
      // griglia orizzontale, recessiva
      ticks.forEach(([v, label]) => {
        svg.appendChild(nodo("line", {x1: M.l, x2: M.l + pw, y1: y(v), y2: y(v),
          stroke: "var(--line)", "stroke-width": 1}));
        if (label) svg.appendChild(nodo("text", {x: M.l - 6, y: y(v) + 3, "text-anchor": "end"}, label));
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
      const val = r => modo === "capo"
        ? r.stese.map((v, i) => v ? v / q.capo[i].stese * 100 : null)
        : modo === "colpi" ? r.stese : r.posto;
      const etichette = [];
      // le linee stanno dentro l'area del grafico, piu' lo spessore del tratto
      const clip = "quote-clip-" + id;
      const cp = nodo("clipPath", {id: clip});
      cp.appendChild(nodo("rect", {x: M.l - 4, y: M.t - 4, width: pw + 8, height: ph + 8}));
      svg.appendChild(cp);
      const linee = nodo("g", {"clip-path": "url(#" + clip + ")"});
      svg.appendChild(linee);
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
          linee.appendChild(nodo("path", {d, fill: "none", stroke: "var(--panel)",
            "stroke-width": 4, "stroke-linejoin": "round", opacity: spento ? 0 : 1}));
          linee.appendChild(nodo("path", {d, fill: "none", stroke: r.colore,
            "stroke-width": r.nick === scelto ? 2.5 : 2, "stroke-linejoin": "round",
            "stroke-linecap": "round", opacity: spento ? .18 : 1}));
        }
        const ult = vs[n - 1];
        // il pallino in fondo non scende sotto il bordo
        if (ult !== null && !spento) etichette.push({r, y: Math.min(y(ult), M.t + ph), v: ult});
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
        // in fondo alla linea il posto di oggi (peluche), le stese in "colpi"
        t.appendChild(nodo("tspan", {dx: 4, fill: "var(--dim)"},
          modo === "colpi" ? String(e.v) : e.r.posto[n - 1] + "º"));
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
        // si ordina sul posto: stesso ordine in tutte le viste
        const ordinati = righe.slice().sort((a, b) => {
          const va = a.posto[i], vb = b.posto[i];
          if (va === vb) return 0;
          if (va === null) return 1;
          if (vb === null) return -1;
          return va - vb;
        });
        const capo = q.capo[i];
        tip.textContent = "";
        // il capo puo' stare fuori dai sei in grafico: si dice
        const chi = capo.chi.map(c => righe.some(r => r.nick === c) ? c
          : c + " (fuori dal grafico)");
        tip.appendChild(h("b", null, gg(q.giorni[i]) + (modo === "capo"
          ? " · capo " + chi.join(", ") + " con " + capo.stese
          : " · " + q.totale[i] + " " + parole[4] + " in tutto")));
        ordinati.forEach(r => {
          const st = r.stese[i], p = r.posto[i];
          const riga = h("div");
          const c = h("i");
          c.style.background = r.colore;
          riga.appendChild(c);
          riga.appendChild(document.createTextNode(r.nick));
          riga.appendChild(h("span", null, p === null ? "—"
            : modo === "capo" ? st + (st === capo.stese ? " · capo"
              : " · −" + (capo.stese - st) + " dal capo")
            : modo === "colpi" ? String(st) : p + "º"));
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
