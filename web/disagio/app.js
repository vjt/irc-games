// disagio.sniffo.org: the ⚡ Disagia button and the dataset grid.
// Phrases come from frasi.js (build.py, from love.py DISAGIO_PHRASES).
// Everything goes in with textContent: the nick is whatever the visitor types.
(function () {
  "use strict";
  const FRASI = window.DISAGIO || [];
  const BORDI = ["purple", "cyan", "pink", "emerald", "amber", "indigo"];
  const PAGINA = 12;

  const form = document.getElementById("dis-form");
  const nickIn = document.getElementById("dis-nick");
  const out = document.getElementById("dis-out");
  const conta = document.getElementById("dis-count");
  const grid = document.getElementById("dis-grid");
  const more = document.getElementById("dis-more");
  document.getElementById("dis-total").textContent = FRASI.length;

  // Empty field: the misery lands on "someone", in the page's language.
  const QUALCUNO = {
    it: "qualcuno", en: "someone", es: "alguien", fr: "quelqu'un",
    pt: "alguém", ru: "кто-то", ja: "誰か", zh: "某人",
  }[(document.documentElement.lang || "it").slice(0, 2)] || "qualcuno";

  function nick() {
    return nickIn.value.trim() || QUALCUNO;
  }

  // The visitor's nick, shared with the seggio (ancora-di-piu/voto.js) through
  // this browser's localStorage only, never sent.
  const CHIAVE = "disagio.nick";
  try { nickIn.value = localStorage.getItem(CHIAVE) || ""; } catch (e) { /* no storage */ }
  function salva() {
    try {
      const v = nickIn.value.trim();
      if (v) localStorage.setItem(CHIAVE, v); else localStorage.removeItem(CHIAVE);
    } catch (e) { /* no storage */ }
  }
  nickIn.addEventListener("change", salva);

  // Same rule as love.py's pick(): never the one shown last.
  let ultima = -1;
  function pesca() {
    if (FRASI.length < 2) return 0;
    let i;
    do { i = Math.floor(Math.random() * FRASI.length); } while (i === ultima);
    ultima = i;
    return i;
  }

  let timer = null;
  let generati = 0;
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    salva();
    if (!FRASI.length) return;
    const testo = FRASI[pesca()].replace(/\{to\}/g, nick());
    clearInterval(timer);
    out.textContent = "";
    let k = 0;
    // ~42 token/s, the slop canon.
    timer = setInterval(function () {
      k += 2;
      out.textContent = testo.slice(0, k);
      if (k >= testo.length) clearInterval(timer);
    }, 24);
    conta.textContent = ++generati;
    const r = form.getBoundingClientRect();
    if (window.Slopstrap) window.Slopstrap.sparkleBurst(r.right - 60, r.top + 20, 10);
  });

  document.getElementById("dis-copy").addEventListener("click", function () {
    const t = out.textContent;
    if (navigator.clipboard && t) navigator.clipboard.writeText(t).catch(function () {});
  });

  let mostrate = 0;
  function card(i) {
    const a = document.createElement("article");
    a.className = "slop-card dis-card slop-border-l-" + BORDI[i % BORDI.length];
    const n = document.createElement("div");
    n.className = "dis-num";
    n.textContent = "#" + String(i + 1).padStart(3, "0");
    const p = document.createElement("p");
    p.className = "slop-mt-1";
    p.dataset.i = i;
    p.textContent = FRASI[i].replace(/\{to\}/g, nick());
    a.append(n, p);
    return a;
  }
  function carica() {
    const fino = Math.min(FRASI.length, mostrate + (mostrate ? FRASI.length : PAGINA));
    for (; mostrate < fino; mostrate++) grid.append(card(mostrate));
    more.hidden = mostrate >= FRASI.length;
  }
  more.addEventListener("click", carica);
  nickIn.addEventListener("input", function () {
    grid.querySelectorAll("p[data-i]").forEach(function (p) {
      p.textContent = FRASI[+p.dataset.i].replace(/\{to\}/g, nick());
    });
  });
  carica();
})();
