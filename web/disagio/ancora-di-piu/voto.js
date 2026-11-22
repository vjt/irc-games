// disagio.sniffo.org/ancora-di-piu/: blind vote on the candidate phrases.
// Talks to disagio_api.py: /api/candidate, /api/voto, /api/classifica.
// Everything goes in with textContent: the phrases come from the server, the
// nick from whoever types it.
(function () {
  "use strict";
  const $ = function (id) { return document.getElementById(id); };
  const form = $("v-form");
  const nickIn = $("v-nick");
  const out = $("v-frase");
  const stato = $("v-stato");
  const conta = $("v-votate");
  const resta = $("v-restanti");
  const btnSu = $("v-su");
  const btnGiu = $("v-giu");
  const btnSalta = $("v-salta");
  const sezCl = $("classifica");
  const listaCl = $("v-cl");
  const fine = $("v-fine");

  function nuovoId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return Array.from(b, function (x) { return x.toString(16).padStart(2, "0"); }).join("");
  }

  // The voter is a random id in localStorage, so this browser is not served
  // again what it already voted. No storage (private mode, blocked): a fresh
  // id per page load, and the session still works.
  const VOTANTE = (function () {
    let id = null;
    try { id = localStorage.getItem("disagio.votante"); } catch (e) { /* no storage */ }
    if (!/^[A-Za-z0-9-]{16,64}$/.test(id || "")) {
      id = nuovoId();
      try { localStorage.setItem("disagio.votante", id); } catch (e) { /* no storage */ }
    }
    return id;
  })();

  // Whatever name the visitor types becomes an IRC-ish nick: accents off,
  // runs of spaces to one "_", anything else a nick can't hold dropped.
  // Kept in this browser's localStorage (shared with the homepage), never sent.
  function normalizza(s) {
    return s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim()
      .replace(/\s+/g, "_").replace(/[^A-Za-z0-9_\-\[\]\\`^{|}]/g, "").slice(0, 30);
  }
  function nick() {
    return normalizza(nickIn.value);
  }
  const CHIAVE = "disagio.nick";
  try {
    const salvato = normalizza(localStorage.getItem(CHIAVE) || "");
    if (salvato) { nickIn.value = salvato; nickIn.blur(); }
  } catch (e) { /* no storage */ }
  function salva() {
    try {
      if (nick()) localStorage.setItem(CHIAVE, nick()); else localStorage.removeItem(CHIAVE);
    } catch (e) { /* no storage */ }
  }
  // Same substitution as app.js; split/join so a "$&" in the nick stays as typed.
  function rendi(testo) {
    return testo.split("{to}").join(nick() || "qualcuno");
  }

  // coda[0] is on screen. «salta» sends it to the back; a vote drops it.
  // viste: every id queued this session, so a voted one never comes back
  // while its vote is still on the way.
  let coda = [];
  const viste = new Set();
  let votate = 0;
  let restanti = null;
  let occupato = false;
  let pronto = false; // first batch in: until then the card says it's loading

  async function carica() {
    const r = await fetch("/api/candidate?n=20&votante=" + encodeURIComponent(VOTANTE),
                          { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    votate = d.votate;
    restanti = d.restanti;
    d.candidate.forEach(function (f) {
      if (!viste.has(f.id)) { viste.add(f.id); coda.push(f); }
    });
  }

  function bottoni(acceso) {
    btnSu.disabled = btnGiu.disabled = btnSalta.disabled = !acceso;
  }

  function mostra() {
    conta.textContent = votate;
    resta.textContent = restanti === null ? "…" : Math.max(0, restanti);
    fine.hidden = coda.length > 0;
    if (!coda.length) {
      out.textContent = "Hai votato tutto, torna più tardi.";
      bottoni(false);
      return;
    }
    if (!nick()) {
      out.textContent = "Prima dimmi come ti chiami, qui sopra: le frasi parlano di te.";
      bottoni(false);
      return;
    }
    out.textContent = rendi(coda[0].testo);
    bottoni(!occupato);
  }

  async function vota(voto) {
    const f = coda[0];
    if (!f || occupato) return;
    occupato = true;
    bottoni(false);
    stato.textContent = "";
    try {
      const r = await fetch("/api/voto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: f.id, voto: voto, votante: VOTANTE }),
      });
      if (r.ok) {
        coda.shift();
        votate++;
        if (restanti) restanti--;
        if (voto > 0 && window.Slopstrap) {
          const b = btnSu.getBoundingClientRect();
          window.Slopstrap.sparkleBurst(b.left + b.width / 2, b.top, 8);
        }
      } else if (r.status === 404) {
        coda.shift(); // no longer a candidate: approved or thrown out meanwhile
      } else if (r.status === 429) {
        stato.textContent = "Piano: troppi voti da qui. Riprova fra qualche minuto.";
      } else {
        stato.textContent = "Il server ha risposto " + r.status + ". Riprova.";
      }
    } catch (e) {
      stato.textContent = "Niente rete, o niente server. Riprova.";
    }
    if (coda.length < 3) await ricarica();
    occupato = false;
    mostra();
  }

  async function ricarica() {
    try {
      await carica();
    } catch (e) {
      if (!coda.length) stato.textContent = "Non riesco a caricarne altre: riprova più tardi.";
    }
  }

  function salta() {
    if (occupato || !coda.length) return;
    coda.push(coda.shift());
    mostra();
    if (coda.length < 3) ricarica().then(mostra);
  }

  btnSu.addEventListener("click", function () { vota(1); });
  btnGiu.addEventListener("click", function () { vota(-1); });
  btnSalta.addEventListener("click", salta);
  function fissaNick() {
    nickIn.value = nick();
    salva();
  }
  nickIn.addEventListener("change", fissaNick);
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    fissaNick();
    nickIn.blur();
  });

  // --- classifica

  let ultimaCl = null;
  function disegnaCl() {
    listaCl.textContent = "";
    if (!ultimaCl.length) {
      listaCl.textContent = "Ancora nessuna candidata.";
      return;
    }
    ultimaCl.forEach(function (f) {
      const li = document.createElement("li");
      li.className = "slop-card";
      const s = document.createElement("div");
      s.className = "v-saldo";
      s.textContent = (f.saldo > 0 ? "+" : "") + f.saldo;
      const corpo = document.createElement("div");
      const p = document.createElement("p");
      p.textContent = rendi(f.testo);
      const c = document.createElement("div");
      c.className = "v-conti";
      c.textContent = "👍 " + f.su + " · 👎 " + f.giu;
      corpo.append(p, c);
      li.append(s, corpo);
      listaCl.append(li);
    });
  }

  async function classifica() {
    sezCl.hidden = false;
    listaCl.textContent = "carico…";
    try {
      const r = await fetch("/api/classifica", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      ultimaCl = (await r.json()).classifica;
      disegnaCl();
    } catch (e) {
      listaCl.textContent = "Classifica non disponibile, riprova più tardi.";
    }
  }

  document.querySelectorAll(".v-cl-link").forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      classifica().then(function () { sezCl.scrollIntoView({ behavior: "smooth" }); });
    });
  });

  nickIn.addEventListener("input", function () {
    if (pronto) mostra();
    if (ultimaCl && !sezCl.hidden) disegnaCl();
  });

  if (location.hash === "#classifica") classifica();
  carica().then(function () { pronto = true; mostra(); }, function () {
    out.textContent = "Il seggio non risponde. Riprova più tardi.";
  });
})();
