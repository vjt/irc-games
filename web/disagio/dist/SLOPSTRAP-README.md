# ✨ Slopstrap v4.2

> **It’s not just a CSS framework — it’s a complete paradigm shift in synthetic UI.**

**Slopstrap** is a zero-dependency, drop-in frontend library (like Bootstrap) purpose-built to make any website look unmistakably, unapologetically **vibecoded by an LLM in 3.4 seconds**.

Instead of carefully prompting an AI agent to reproduce the exact same dark-mode purple-gradient Tailwind landing page for the ten-millionth time, Slopstrap ships every certified AI design tell out of the box.

---

## ⚡ Features (100% Certified AI Slop)

- **Purple-to-Indigo-to-Cyan Gradients** on headlines, buttons, pills, borders, and ambient orbs
- **Blinking Status Indicators** (`.slop-dot-green`, `.slop-dot-purple`, `.slop-dot-rainbow`) hooked up to absolutely no backend
- **Multicolored 4px Thick Left Borders** (`.slop-border-l-purple`, `.slop-border-l-cyan`, `.slop-border-l-emerald`, `.slop-border-l-amber`, `.slop-border-l-pink`, `.slop-border-l-rainbow`)
- **Rotating Conic Gradient Border Beams** (`.slop-card-beam`) + **Cursor-Following Radial Spotlight Glow**
- **One Arbitrary Italic Serif Word** (`.slop-serif-emphasis`) inside otherwise pure Inter headlines
- **Mandatory Layout Primitives**: Symmetrical 3-Card Feature Grids (`.slop-grid-3`), Bento Grids (`.slop-bento`), and Cards-Inside-Cards (`.slop-card-nested`)
- **Declarative Fake Social Proof**:
  - `data-slop-testimonials="6"` — Instantly summons **Dr. Sarah Chen** (VP of Product at NexusAI), **Marcus Vance** (Founder & CEO at ApexFlow), and friends with 5 gold stars
  - `data-slop-marquee` — Infinite scrolling logo wall of fake startups (`NEXUS`, `APEX`, `VELO`, `QUANTUM`, `ORBIT`, `SYNAPSE`)
  - `data-slop-count="99.99%"` — Animated roll-up stat counters for unprovable metrics
  - `data-slop-prose="saas|ai|fintech|robotics|devices|defense"` — Injects 1–2 paragraphs of extraneous, trope-heavy LLM marketing copy tailored to your industry (`data-slop-paragraphs="1|2"`)
- **One-Click Auto-Slopifier (`Slopstrap.slopify()`)** — Feed it any plain semantic HTML page and watch it convert into a Series B AI SaaS landing page in milliseconds
- **Slop Intensity Levels (`data-slop-level="1|2|3|4"`)** — Scale from *Subtle YC Startup* (`1`) to *CRITICAL SLOP OVERDRIVE* (`4`, with cursor sparkle trails!)

---

## 🚀 Quick Start

Include `dist/slopstrap.css` and `dist/slopstrap.js` in any HTML page:

```html
<!DOCTYPE html>
<html lang="en" data-slop-level="3">
<head>
  <link rel="stylesheet" href="./dist/slopstrap.css" />
</head>
<body class="slop-body slop-grid-bg">
  <div class="slop-orb-layer" aria-hidden="true">
    <div class="slop-orb slop-orb-purple"></div>
    <div class="slop-orb slop-orb-cyan"></div>
  </div>

  <main class="slop-content-layer slop-container slop-section slop-center">
    <span class="slop-pill slop-pill-shimmer">
      <span class="slop-dot slop-dot-green"></span>
      ✨ Introducing v2.0 — Now Live →
    </span>

    <h1 class="slop-h1">
      <span class="slop-text-gradient">Supercharge Your</span>
      <span class="slop-serif-emphasis slop-text-gradient-hot">synergy</span> ✨
    </h1>

    <div data-slop-marquee></div>
    <div data-slop-testimonials="3"></div>
  </main>

  <script src="./dist/slopstrap.js"></script>
</body>
</html>
```

---

## 🎛️ JavaScript API

```js
// Set global slop intensity: 1 (Subtle YC) -> 4 (CRITICAL SLOP OVERDRIVE)
Slopstrap.setLevel(4);

// Automatically convert any plain HTML container into vibecoded slop
Slopstrap.slopify('#my-plain-site');

// Revert it back to boring human-designed HTML
Slopstrap.unslopify('#my-plain-site');

// Trigger a sparkle burst at (x, y)
Slopstrap.sparkleBurst(window.innerWidth / 2, window.innerHeight / 2, 16);
```

---

## 🖥️ Running Locally

```bash
npm run dev
# Open http://localhost:8080/index.html
```

---

## 📜 License

MIT — Zero human designers were consulted in the making of this framework.
