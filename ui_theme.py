"""
Design system for PDF AI Summarizer.

A light "paper" theme built on a real elevation model: every surface sits on a
named layer, lit from above and grounded by a soft blue-grey contact shadow.
The 3D is geometric (perspective + preserve-3d + translateZ), not decorative
blur, so depth stays consistent as things move.

Light-theme discipline, since this is where light designs usually go wrong:
  * no glows — a coloured halo on white reads as a bug, not as depth
  * shadows are tinted with the accent hue, never neutral black, and stay wide
    and low-opacity so they diffuse instead of smudging
  * elevation is carried mostly by the shadow's *spread*, not its darkness
  * a hairline border does the work a bright inset highlight does on dark
"""
import streamlit as st

# ---------------------------------------------------------------- primitives

def _h(markup: str) -> str:
    """Flatten markup so Streamlit's markdown parser never sees an indented
    line and mistake it for a code block."""
    return "".join(line.strip() for line in markup.strip().splitlines())


def _md(markup: str) -> None:
    st.markdown(_h(markup), unsafe_allow_html=True)


# ---------------------------------------------------------------- stylesheet

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

:root {
    /* --- ground plane -------------------------------------------------- */
    --page:      #F4F7FC;
    --page-deep: #E9EFF8;
    --surface-1: #FFFFFF;
    --surface-2: #FBFCFE;
    --surface-3: #F1F5FB;
    --well:      #EDF2F9;

    /* --- edges ---------------------------------------------------------- */
    --line:        #E3EAF4;
    --line-strong: #CFDAEA;
    --hairline:    rgba(29, 78, 216, 0.08);

    /* --- ink ------------------------------------------------------------ */
    --ink:      #0F172A;
    --ink-dim:  #475569;
    --ink-mute: #7C8BA3;

    /* --- accent --------------------------------------------------------- */
    --blue:      #1D4ED8;
    --blue-lo:   #1E40AF;
    --blue-hi:   #3B82F6;
    --blue-pale: #EFF4FF;
    --ok:        #047857;
    --ok-pale:   #ECFDF5;
    --warn:      #B45309;
    --warn-pale: #FFFBEB;
    --bad:       #BE123C;
    --bad-pale:  #FFF1F2;

    /* --- elevation: tinted, wide, low-opacity. no glow. ----------------- */
    --e1: 0 1px 2px rgba(15, 41, 92, .05),
          0 2px 6px -2px rgba(15, 41, 92, .07);
    --e2: 0 1px 2px rgba(15, 41, 92, .05),
          0 8px 20px -6px rgba(15, 41, 92, .11);
    --e3: 0 2px 4px rgba(15, 41, 92, .06),
          0 20px 44px -14px rgba(15, 41, 92, .18);
    --well-shadow: inset 0 1px 3px rgba(15, 41, 92, .08);
    --ring: 0 0 0 3px rgba(59, 130, 246, .16);

    --r-sm: 10px;
    --r-md: 16px;
    --r-lg: 22px;
    --ease: cubic-bezier(.2,.7,.2,1);
}

/* ------------------------------------------------------------ app canvas */

html, body, [class*="css"] { font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif; }

[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(1100px 620px at 12% -8%,  rgba(59,130,246,.10),  transparent 62%),
        radial-gradient(900px  520px at 92% 4%,   rgba(29,78,216,.06),   transparent 60%),
        var(--page);
    color: var(--ink);
}

/* faint grid floor — gives the depth a reference plane */
[data-testid="stAppViewContainer"]::before {
    content: '';
    position: fixed; inset: 0;
    background-image:
        linear-gradient(rgba(29,78,216,.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(29,78,216,.035) 1px, transparent 1px);
    background-size: 56px 56px;
    mask-image: radial-gradient(ellipse 90% 70% at 50% 0%, #000 20%, transparent 78%);
    pointer-events: none;
    z-index: 0;
}

[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { right: 1rem; }
[data-testid="stMainBlockContainer"] { padding-top: 2.4rem; max-width: 1280px; }
[data-testid="stAppViewContainer"] > .main { position: relative; z-index: 1; }

h1, h2, h3 { font-family: 'Space Grotesk', 'Inter', sans-serif; letter-spacing: -.02em; color: var(--ink); }
p, li, label, span { color: var(--ink-dim); }
a { color: var(--blue); }
hr { border-color: var(--line); }
code { background: var(--blue-pale); color: var(--blue-lo); padding: .1em .35em; border-radius: 5px; }

/* ---------------------------------------------------------------- sidebar */

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, var(--surface-1) 0%, var(--surface-2) 100%);
    border-right: 1px solid var(--line);
    box-shadow: 12px 0 32px -24px rgba(15, 41, 92, .5);
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 { font-size: 1rem; letter-spacing: .01em; }

.sb-brand {
    display: flex; align-items: center; gap: .8rem;
    padding: .3rem 0 1.1rem 0; margin-bottom: 1rem;
    border-bottom: 1px solid var(--line);
}
.sb-mark {
    width: 40px; height: 40px; flex: none;
    border-radius: 12px;
    background: linear-gradient(150deg, var(--blue-hi) 0%, var(--blue) 60%, var(--blue-lo) 100%);
    box-shadow: 0 6px 14px -6px rgba(29,78,216,.6), inset 0 1px 0 rgba(255,255,255,.45);
    display: grid; place-items: center;
    color: #FFFFFF; font-size: 1.15rem;
    transform: rotateX(14deg) rotateY(-14deg);
    transform-style: preserve-3d;
}
.sb-name { font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 1rem; color: var(--ink); line-height: 1.15; }
.sb-role { font-size: .7rem; color: var(--ink-mute); letter-spacing: .14em; text-transform: uppercase; }

.sb-label {
    font-size: .68rem; letter-spacing: .16em; text-transform: uppercase;
    color: var(--ink-mute); font-weight: 600;
    margin: 1.4rem 0 .55rem 0;
}

/* --------------------------------------------------------------- the hero */

.hero {
    position: relative;
    display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, .95fr);
    gap: 2rem; align-items: center;
    padding: 2.6rem 2.6rem 2.4rem;
    border-radius: 26px;
    background:
        linear-gradient(155deg, var(--blue-pale) 0%, transparent 46%),
        var(--surface-1);
    border: 1px solid var(--line);
    box-shadow: var(--e3);
    overflow: hidden;
    margin-bottom: 1.6rem;
}
.hero::after {           /* a crisp top edge catching the light */
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,.9) 20%, rgba(255,255,255,.9) 80%, transparent);
}

.eyebrow {
    display: inline-flex; align-items: center; gap: .5rem;
    font-size: .68rem; letter-spacing: .2em; text-transform: uppercase;
    color: var(--blue); font-weight: 700; margin-bottom: 1rem;
}
.eyebrow::before {
    content: ''; width: 6px; height: 6px; border-radius: 50%;
    background: var(--blue-hi);
    animation: pulse 2.4s ease-in-out infinite;
}
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .25; } }

.hero-title {
    font-family: 'Space Grotesk', sans-serif;
    font-size: clamp(2.1rem, 4.2vw, 3.1rem);
    font-weight: 700; line-height: 1.04; letter-spacing: -.035em;
    margin: 0 0 .85rem 0;
    color: var(--ink);
}
.hero-title em { font-style: normal; color: var(--blue); }
.hero-sub { font-size: 1rem; line-height: 1.65; color: var(--ink-dim); margin: 0 0 1.5rem 0; max-width: 46ch; }

.chip-row { display: flex; flex-wrap: wrap; gap: .55rem; }
.chip {
    display: inline-flex; align-items: center; gap: .42rem;
    padding: .42rem .82rem; border-radius: 999px;
    font-size: .76rem; font-weight: 500; color: var(--ink-dim);
    background: var(--surface-1);
    border: 1px solid var(--line);
    box-shadow: var(--e1);
    transition: transform .3s var(--ease), color .3s, border-color .3s;
}
.chip:hover { transform: translateY(-2px); color: var(--blue); border-color: var(--line-strong); }

/* --------------------------------------------- the 3D document (geometry) */

.stage { perspective: 1400px; perspective-origin: 50% 45%; display: grid; place-items: center; min-height: 260px; }

.rig {
    position: relative; width: 190px; height: 240px;
    transform-style: preserve-3d;
    animation: sway 14s ease-in-out infinite;
}
@keyframes sway {
    0%, 100% { transform: rotateX(12deg) rotateY(-24deg); }
    50%      { transform: rotateX(6deg)  rotateY(24deg); }
}

/* real paper: white face, cool edge, shadow cast down and away */
.sheet {
    position: absolute; inset: 0;
    border-radius: 10px;
    background: linear-gradient(160deg, #FFFFFF 0%, #F6F9FE 100%);
    border: 1px solid #DCE6F4;
    box-shadow: 0 18px 34px -14px rgba(15, 41, 92, .30);
    transform-style: preserve-3d;
}
.sheet.s3 { transform: translateZ(-46px) translate( 22px, 20px) rotate( 5deg); }
.sheet.s2 { transform: translateZ(-23px) translate( 11px, 10px) rotate( 2.5deg); }
.sheet.s1 { transform: translateZ(0); padding: 20px 18px; }

.sheet.s1::after {       /* light raking across the front face */
    content: '';
    position: absolute; inset: 0; border-radius: 10px;
    background: linear-gradient(115deg, rgba(255,255,255,.9) 0%, transparent 34%, transparent 66%, rgba(29,78,216,.07) 100%);
    pointer-events: none;
}
.ln { height: 6px; border-radius: 3px; background: #E4EBF6; margin-bottom: 10px; }
.ln.hd { height: 10px; width: 62%; background: linear-gradient(90deg, var(--blue), var(--blue-hi)); margin-bottom: 16px; }
.ln.w80 { width: 80%; } .ln.w95 { width: 95%; } .ln.w60 { width: 60%; } .ln.w70 { width: 70%; }

.scan {                  /* the AI read-through */
    position: absolute; left: 8%; right: 8%; height: 2px;
    background: linear-gradient(90deg, transparent, var(--blue-hi), transparent);
    transform: translateZ(14px);
    animation: scan 3.6s ease-in-out infinite;
}
@keyframes scan { 0%,100% { top: 14%; opacity: 0; } 12% { opacity: 1; } 88% { opacity: 1; } 100% { top: 86%; } }

/* -------------------------------------------------------- section headers */

.sec { display: flex; align-items: center; gap: .85rem; margin: 2.2rem 0 1.1rem; }
.sec-ico {
    width: 34px; height: 34px; flex: none; border-radius: 10px;
    display: grid; place-items: center; font-size: .95rem;
    color: var(--blue);
    background: var(--surface-1);
    border: 1px solid var(--line);
    box-shadow: var(--e1);
}
.sec-txt h3 { margin: 0; font-size: 1.12rem; font-weight: 600; }
.sec-txt p  { margin: .12rem 0 0; font-size: .8rem; color: var(--ink-mute); }
.sec-rule { flex: 1; height: 1px; background: linear-gradient(90deg, var(--line-strong), transparent); }

/* ------------------------------------------------------- tilt / 3D panels */

.tilt { perspective: 1000px; }
.tilt-in {
    position: relative;
    transform-style: preserve-3d;
    transition: transform .55s var(--ease), box-shadow .55s var(--ease), border-color .55s;
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-radius: var(--r-md);
    box-shadow: var(--e2);
    padding: 1.15rem 1.25rem;
    height: 100%;
}
.tilt:hover .tilt-in {
    transform: rotateX(7deg) rotateY(-9deg) translateY(-6px);
    box-shadow: var(--e3);
    border-color: var(--line-strong);
}
.tilt-in > * { transform: translateZ(26px); }          /* content floats above the plate */

.f-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: .7rem; }
.f-ico { font-size: 1.05rem; }
.f-key { font-size: .66rem; letter-spacing: .15em; text-transform: uppercase; color: var(--ink-mute); font-weight: 600; }
.f-val {
    font-family: 'Space Grotesk', sans-serif; font-weight: 600;
    font-size: 1.28rem; color: var(--ink); line-height: 1.25;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.f-sub { font-size: .74rem; color: var(--ink-mute); margin-top: .2rem; }

/* raised stat plate */
.plate {
    position: relative; border-radius: var(--r-md);
    padding: 1.1rem 1.2rem;
    background: var(--surface-1);
    border: 1px solid var(--line);
    box-shadow: var(--e2);
    transition: transform .4s var(--ease), box-shadow .4s var(--ease);
    height: 100%;
}
.plate:hover { transform: translateY(-4px); box-shadow: var(--e3); }
.plate-k { font-size: .66rem; letter-spacing: .15em; text-transform: uppercase; color: var(--ink-mute); font-weight: 600; }
.plate-v {
    font-family: 'Space Grotesk', sans-serif; font-weight: 700;
    font-size: 1.85rem; line-height: 1.15; margin-top: .35rem;
    color: var(--ink);
}
.plate-s { font-size: .74rem; color: var(--ink-mute); margin-top: .18rem; }
.plate-bar { height: 3px; border-radius: 2px; margin-top: .85rem; background: linear-gradient(90deg, var(--blue), var(--blue-hi)); }

/* --------------------------------------------------------------- pipeline */

.pipe { display: flex; flex-direction: column; gap: .1rem; }
.step { display: flex; align-items: center; gap: .9rem; padding: .5rem 0; }
.node {
    width: 30px; height: 30px; flex: none; border-radius: 9px;
    display: grid; place-items: center; font-size: .78rem; font-weight: 700;
    background: var(--surface-1);
    border: 1px solid var(--line);
    box-shadow: var(--e1);
    color: var(--ink-mute);
    transition: all .4s var(--ease);
}
.step.done .node { background: var(--ok-pale); color: var(--ok); border-color: #A7F3D0; }
.step.live .node {
    background: linear-gradient(180deg, var(--blue-hi), var(--blue)); color: #FFFFFF;
    border-color: var(--blue); box-shadow: var(--e2), var(--ring); transform: scale(1.1);
}
.step-t { font-size: .88rem; color: var(--ink-mute); }
.step.done .step-t { color: var(--ink-dim); }
.step.live .step-t { color: var(--ink); font-weight: 600; }
.step-n { font-size: .74rem; color: var(--ink-mute); margin-left: auto; font-variant-numeric: tabular-nums; }

/* ---------------------------------------------------------------- badges  */

.badge {
    display: inline-flex; align-items: center; gap: .4rem;
    padding: .3rem .7rem; border-radius: 8px;
    font-size: .72rem; font-weight: 600; letter-spacing: .03em;
    border: 1px solid var(--line-strong);
    box-shadow: var(--e1);
}
.badge.ok   { background: var(--ok-pale);   color: var(--ok);   border-color: #A7F3D0; }
.badge.info { background: var(--blue-pale); color: var(--blue); border-color: #BFD4FE; }
.badge.warn { background: var(--warn-pale); color: var(--warn); border-color: #FDE68A; }
.badge.bad  { background: var(--bad-pale);  color: var(--bad);  border-color: #FECDD3; }

/* ------------------------------------------------------- prose / readouts */

/* the summary body — a sheet of paper, styled via st.container(key=...) so
   the model's markdown still renders and its text is never injected as HTML */
[class*="st-key-prose"] {
    border-radius: var(--r-md);
    padding: 1.5rem 1.7rem;
    background: var(--surface-1);
    border: 1px solid var(--line);
    box-shadow: var(--e2);
}
[class*="st-key-prose"] p,
[class*="st-key-prose"] li { line-height: 1.75; font-size: .94rem; color: var(--ink-dim); }
[class*="st-key-prose"] h1,
[class*="st-key-prose"] h2,
[class*="st-key-prose"] h3 { font-size: 1.02rem; margin: 1.1rem 0 .5rem; color: var(--ink); }
[class*="st-key-prose"] strong { color: var(--ink); font-weight: 600; }
[class*="st-key-prose"] li::marker { color: var(--blue-hi); }

.caret {                 /* streaming cursor */
    display: inline-block; width: 2px; height: 1em; margin-left: 2px;
    background: var(--blue); vertical-align: text-bottom;
    animation: blink 1s step-end infinite;
}
@keyframes blink { 50% { opacity: 0; } }

.quote {
    position: relative; border-radius: var(--r-sm);
    padding: .95rem 1.1rem .95rem 2.4rem; margin-bottom: .7rem;
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-left: 3px solid var(--blue);
    box-shadow: var(--e1);
    font-size: .9rem; line-height: 1.6; color: var(--ink-dim); font-style: italic;
    transition: transform .35s var(--ease), box-shadow .35s var(--ease);
}
.quote:hover { transform: translateX(5px); box-shadow: var(--e2); }
.quote::before {
    content: '\\201C'; position: absolute; left: .75rem; top: .35rem;
    font-family: Georgia, serif; font-size: 2rem; color: var(--blue-hi); opacity: .6;
}

.pages {                 /* page-range provenance tag */
    display: inline-block; margin-left: .5rem;
    padding: .1rem .45rem; border-radius: 6px;
    font-size: .68rem; font-weight: 600; letter-spacing: .02em;
    background: var(--blue-pale); color: var(--blue-lo);
    border: 1px solid #BFD4FE;
    font-variant-numeric: tabular-nums;
}

.empty {
    border-radius: var(--r-md); padding: 2.2rem 2rem; text-align: center;
    background: var(--surface-1);
    border: 1px solid var(--line);
    box-shadow: var(--e1);
}
.empty-ico { font-size: 1.7rem; opacity: .5; }
.empty h4  { font-family: 'Space Grotesk', sans-serif; margin: .55rem 0 .25rem; font-size: 1rem; color: var(--ink-dim); font-weight: 600; }
.empty p   { font-size: .84rem; color: var(--ink-mute); margin: 0; }

/* --------------------------------------------------------- security panel */

.risk {
    display: flex; align-items: center; gap: 1rem;
    padding: 1.1rem 1.3rem; border-radius: var(--r-md);
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-left: 4px solid var(--ink-mute);
    box-shadow: var(--e2);
    margin-bottom: 1rem;
}
.risk.none   { border-left-color: var(--ok);   }
.risk.info   { border-left-color: var(--blue-hi); }
.risk.low    { border-left-color: var(--blue); }
.risk.medium { border-left-color: var(--warn); }
.risk.high   { border-left-color: var(--bad);  }

.risk-level {
    font-family: 'Space Grotesk', sans-serif; font-weight: 700;
    font-size: .74rem; letter-spacing: .12em; text-transform: uppercase;
    padding: .4rem .75rem; border-radius: 8px; white-space: nowrap;
    border: 1px solid var(--line-strong);
}
.risk.none   .risk-level { background: var(--ok-pale);   color: var(--ok);   border-color: #A7F3D0; }
.risk.info   .risk-level,
.risk.low    .risk-level { background: var(--blue-pale); color: var(--blue); border-color: #BFD4FE; }
.risk.medium .risk-level { background: var(--warn-pale); color: var(--warn); border-color: #FDE68A; }
.risk.high   .risk-level { background: var(--bad-pale);  color: var(--bad);  border-color: #FECDD3; }
.risk-text { font-size: .86rem; color: var(--ink-dim); line-height: 1.55; }

.finding {
    border-radius: var(--r-sm); padding: .85rem 1rem; margin-bottom: .6rem;
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-left: 3px solid var(--ink-mute);
    box-shadow: var(--e1);
}
.finding.info   { border-left-color: var(--blue-hi); }
.finding.low    { border-left-color: var(--blue); }
.finding.medium { border-left-color: var(--warn); }
.finding.high   { border-left-color: var(--bad); }
.finding-top { display: flex; align-items: center; gap: .6rem; margin-bottom: .35rem; }
.finding-title { font-size: .88rem; font-weight: 600; color: var(--ink); }
.finding-sev {
    font-size: .62rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
    padding: .16rem .45rem; border-radius: 5px; margin-left: auto;
}
.finding.info   .finding-sev { background: var(--blue-pale); color: var(--blue); }
.finding.low    .finding-sev { background: var(--blue-pale); color: var(--blue); }
.finding.medium .finding-sev { background: var(--warn-pale); color: var(--warn); }
.finding.high   .finding-sev { background: var(--bad-pale);  color: var(--bad); }
.finding-detail { font-size: .82rem; line-height: 1.6; color: var(--ink-dim); }
.finding-loc {
    font-size: .72rem; color: var(--ink-mute); margin-top: .4rem;
    font-family: ui-monospace, 'Cascadia Code', monospace;
    word-break: break-all;
}

.checklist { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .3rem; }
.checkitem {
    font-size: .72rem; color: var(--ink-mute);
    padding: .25rem .6rem; border-radius: 999px;
    background: var(--surface-3); border: 1px solid var(--line);
}

/* run history rows */
.run {
    display: flex; align-items: center; gap: .7rem;
    padding: .55rem .7rem; margin-bottom: .35rem;
    border-radius: var(--r-sm);
    background: var(--surface-1);
    border: 1px solid var(--line);
    box-shadow: var(--e1);
    transition: transform .3s var(--ease), box-shadow .3s var(--ease);
}
.run:hover { transform: translateX(3px); box-shadow: var(--e2); }
.run-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--ok); }
.run-dot.bad { background: var(--bad); }
.run-name { font-size: .78rem; color: var(--ink); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-meta { font-size: .68rem; color: var(--ink-mute); margin-left: auto; white-space: nowrap; font-variant-numeric: tabular-nums; }

/* =======================================================================
   Streamlit widgets — restyled onto the same elevation model.
   Selectors verified against Streamlit 1.61, which renders these with
   react-aria rather than BaseWeb.
   ===================================================================== */

/* buttons: a shallow extrusion that presses in. On light, the "edge" is a
   tinted rim rather than a black slab. */
[data-testid="stButton"] > button,
[data-testid="stDownloadButton"] > button,
[data-testid="stFormSubmitButton"] > button {
    font-family: 'Inter', sans-serif; font-weight: 600; font-size: .88rem;
    color: var(--ink);
    background: linear-gradient(180deg, #FFFFFF 0%, #F6F9FE 100%);
    border: 1px solid var(--line-strong);
    border-radius: 12px;
    padding: .62rem 1.25rem;
    box-shadow: inset 0 1px 0 #FFFFFF, 0 2px 0 -1px #DCE5F2, 0 6px 14px -6px rgba(15,41,92,.22);
    transform: translateY(0);
    transition: transform .12s var(--ease), box-shadow .12s var(--ease), background .25s;
}
[data-testid="stButton"] > button:hover,
[data-testid="stDownloadButton"] > button:hover {
    background: linear-gradient(180deg, #FFFFFF 0%, #EEF4FD 100%);
    border-color: #B9CCE8;
    transform: translateY(-1px);
    box-shadow: inset 0 1px 0 #FFFFFF, 0 3px 0 -1px #DCE5F2, 0 10px 20px -8px rgba(15,41,92,.28);
}
[data-testid="stButton"] > button:active,
[data-testid="stDownloadButton"] > button:active {
    transform: translateY(2px);
    box-shadow: inset 0 2px 4px rgba(15,41,92,.16);
}
[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(180deg, var(--blue-hi) 0%, var(--blue) 100%);
    border-color: var(--blue-lo);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.3), 0 2px 0 -1px var(--blue-lo),
                0 10px 22px -8px rgba(29,78,216,.5);
}
[data-testid="stButton"] > button[kind="primary"]:hover {
    background: linear-gradient(180deg, #4F8FF7 0%, #2455DE 100%);
}
[data-testid="stButton"] > button[kind="primary"]:active {
    box-shadow: inset 0 2px 5px rgba(0,0,0,.22);
}
[data-testid="stButton"] > button[kind="primary"] p { color: #FFFFFF; }
[data-testid="stButton"] > button p,
[data-testid="stDownloadButton"] > button p { color: var(--ink); font-weight: 600; }

/* uploader: a recessed well you drop into */
[data-testid="stFileUploaderDropzone"] {
    background: var(--well);
    border: 1.5px dashed var(--line-strong);
    border-radius: var(--r-lg);
    padding: 2.1rem 1.5rem;
    box-shadow: var(--well-shadow);
    transition: border-color .35s, background .35s, box-shadow .35s;
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: var(--blue-hi);
    background: var(--blue-pale);
    box-shadow: var(--well-shadow), var(--ring);
}
[data-testid="stFileUploaderDropzone"] small { color: var(--ink-mute); }
[data-testid="stFileUploaderDropzoneInstructions"] span { color: var(--ink-dim); }
[data-testid="stFileUploaderFile"] {
    background: var(--surface-1);
    border: 1px solid var(--line); border-radius: var(--r-sm);
    padding: .6rem .8rem; box-shadow: var(--e1);
}

/* tabs: a segmented control machined out of one block */
[data-testid="stTabs"] [role="tablist"] {
    display: inline-flex; gap: .3rem; padding: .34rem;
    background: var(--well);
    border: 1px solid var(--line);
    border-radius: 14px;
    box-shadow: var(--well-shadow);
}
[data-testid="stTab"] {
    padding: .5rem 1.05rem;
    background: transparent; border: 1px solid transparent; border-radius: 10px;
    transition: all .3s var(--ease);
}
[data-testid="stTab"] p { color: var(--ink-mute); font-weight: 500; font-size: .85rem; margin: 0; }
[data-testid="stTab"]:hover { background: rgba(255,255,255,.7); }
[data-testid="stTab"]:hover p { color: var(--ink-dim); }
[data-testid="stTab"][aria-selected="true"] {
    background: var(--surface-1);
    border-color: var(--line);
    box-shadow: var(--e1);
}
[data-testid="stTab"][aria-selected="true"] p { color: var(--blue); font-weight: 600; }
.react-aria-SelectionIndicator { display: none !important; }

/* segmented control: same machined well, smaller */
[data-testid="stButtonGroup"] [role="radiogroup"] {
    padding: .3rem; gap: .25rem;
    background: var(--well);
    border: 1px solid var(--line);
    border-radius: 12px;
    box-shadow: var(--well-shadow);
}
button[data-variant="segmented_control"] {
    background: transparent !important;
    border: 1px solid transparent !important;
    border-radius: 9px !important;
    box-shadow: none !important;
    transition: all .28s var(--ease);
}
button[data-variant="segmented_control"] p { color: var(--ink-mute); font-size: .82rem; }
button[data-variant="segmented_control"]:hover { background: rgba(255,255,255,.75) !important; }
button[data-variant="segmented_control"][aria-checked="true"] {
    background: var(--surface-1) !important;
    border-color: var(--line) !important;
    box-shadow: var(--e1) !important;
}
button[data-variant="segmented_control"][aria-checked="true"] p { color: var(--blue); font-weight: 600; }

/* inputs: recessed wells */
.stTextInput [role="group"], .stNumberInput [role="group"],
.stSelectbox [role="group"], .stTextArea textarea {
    background: var(--well) !important;
    border: 1px solid var(--line) !important;
    border-radius: var(--r-sm) !important;
    box-shadow: var(--well-shadow);
}
.stTextInput [role="group"]:hover, .stSelectbox [role="group"]:hover { border-color: var(--line-strong) !important; }
[role="listbox"] {
    background: var(--surface-1); border: 1px solid var(--line-strong);
    border-radius: var(--r-sm); box-shadow: var(--e3);
}

/* slider: lift the thumb off the rail */
[data-testid="stSlider"] [role="group"] > div > div:has(> [data-testid="stSliderThumbValue"]) > div:first-child {
    box-shadow: 0 2px 6px -1px rgba(15,41,92,.35), var(--ring);
}
[data-testid="stSliderThumbValue"] p { color: var(--blue); font-weight: 600; font-size: .8rem; }
[data-testid="stSliderTickBar"] p { color: var(--ink-mute); font-size: .7rem; }

/* toggle: seat the track into the surface */
[data-testid="stCheckbox"] label > div:not([data-testid]) {
    box-shadow: inset 0 1px 3px rgba(15,41,92,.16);
}
[data-testid="stWidgetLabel"] p { color: var(--ink-dim); font-size: .86rem; }

/* progress */
[data-testid="stProgressBarTrack"] {
    background: var(--well) !important;
    border-radius: 999px;
    box-shadow: var(--well-shadow);
    overflow: hidden;
}
[data-testid="stProgressBarTrack"] > div {
    background: linear-gradient(90deg, var(--blue), var(--blue-hi)) !important;
    border-radius: 999px;
}

/* expanders */
[data-testid="stExpander"] {
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-radius: var(--r-md);
    box-shadow: var(--e1);
    overflow: hidden;
}
[data-testid="stExpander"] summary { padding: .8rem 1.1rem; font-weight: 500; color: var(--ink-dim); }
[data-testid="stExpander"] summary:hover { color: var(--blue); }

/* native metrics */
[data-testid="stMetric"] {
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-radius: var(--r-md);
    padding: 1rem 1.15rem;
    box-shadow: var(--e2);
}
[data-testid="stMetricLabel"] p { color: var(--ink-mute); font-size: .72rem; letter-spacing: .1em; text-transform: uppercase; }
[data-testid="stMetricValue"] { font-family: 'Space Grotesk', sans-serif; color: var(--ink); }

/* alerts */
[data-testid="stAlertContainer"] {
    border-radius: var(--r-sm);
    border: 1px solid var(--line-strong);
    box-shadow: var(--e1);
}

/* dataframe */
[data-testid="stDataFrame"] {
    border-radius: var(--r-md); overflow: hidden;
    border: 1px solid var(--line); box-shadow: var(--e2);
}

/* spinner */
[data-testid="stSpinner"] > div > div { border-top-color: var(--blue) !important; }

/* scrollbar */
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-track { background: var(--page); }
::-webkit-scrollbar-thumb { background: #C8D5E8; border-radius: 6px; border: 2px solid var(--page); }
::-webkit-scrollbar-thumb:hover { background: #A9BDD8; }

/* entrance */
[data-testid="stMainBlockContainer"] > div { animation: rise .55s var(--ease) both; }
@keyframes rise { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }

#MainMenu, footer { visibility: hidden; }

/* honour reduced-motion: keep the depth, drop the movement */
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition-duration: .01ms !important; }
    .tilt:hover .tilt-in { transform: none; }
}

/* narrow screens: stack the hero, shrink the stage */
@media (max-width: 900px) {
    .hero { grid-template-columns: 1fr; padding: 1.9rem 1.5rem; }
    .stage { min-height: 210px; }
    .rig { transform: scale(.8); }
}
</style>
"""


def inject() -> None:
    """Install the stylesheet. Call once, right after set_page_config."""
    st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- components

def hero(title: str, subtitle: str, eyebrow: str, chips) -> None:
    chip_html = "".join(f'<div class="chip">{c}</div>' for c in chips)
    _md(f"""
    <div class="hero">
      <div class="hero-copy">
        <div class="eyebrow">{eyebrow}</div>
        <h1 class="hero-title">{title}</h1>
        <p class="hero-sub">{subtitle}</p>
        <div class="chip-row">{chip_html}</div>
      </div>
      <div class="stage">
        <div class="rig">
          <div class="sheet s3"></div>
          <div class="sheet s2"></div>
          <div class="sheet s1">
            <div class="ln hd"></div>
            <div class="ln w95"></div><div class="ln w80"></div><div class="ln w95"></div>
            <div class="ln w60"></div><div class="ln w80"></div><div class="ln w70"></div>
            <div class="ln w95"></div><div class="ln w60"></div>
            <div class="scan"></div>
          </div>
        </div>
      </div>
    </div>
    """)


def section(icon: str, title: str, caption: str = "") -> None:
    cap = f"<p>{caption}</p>" if caption else ""
    _md(f"""
    <div class="sec">
      <div class="sec-ico">{icon}</div>
      <div class="sec-txt"><h3>{title}</h3>{cap}</div>
      <div class="sec-rule"></div>
    </div>
    """)


def facet(icon: str, key: str, value: str, sub: str = "") -> None:
    """A tilting 3D card for a single document attribute."""
    subline = f'<div class="f-sub">{sub}</div>' if sub else ""
    _md(f"""
    <div class="tilt"><div class="tilt-in">
      <div class="f-top"><span class="f-key">{key}</span><span class="f-ico">{icon}</span></div>
      <div class="f-val" title="{value}">{value}</div>{subline}
    </div></div>
    """)


def plate(key: str, value: str, sub: str = "") -> None:
    """A raised stat plate."""
    subline = f'<div class="plate-s">{sub}</div>' if sub else ""
    _md(f"""
    <div class="plate">
      <div class="plate-k">{key}</div>
      <div class="plate-v">{value}</div>{subline}
      <div class="plate-bar"></div>
    </div>
    """)


def pipeline(steps, active: int) -> str:
    """Render the processing stepper. `active` is the index currently running;
    pass len(steps) once everything is finished."""
    rows = []
    for i, (label, note) in enumerate(steps):
        if i < active:
            state, glyph = "done", "✓"
        elif i == active:
            state, glyph = "live", str(i + 1)
        else:
            state, glyph = "", str(i + 1)
        rows.append(
            f'<div class="step {state}"><div class="node">{glyph}</div>'
            f'<div class="step-t">{label}</div><div class="step-n">{note}</div></div>'
        )
    return _h(f'<div class="pipe">{"".join(rows)}</div>')


def badge(text: str, kind: str = "info") -> str:
    return f'<span class="badge {kind}">{text}</span>'


def pages_tag(first, last) -> str:
    """Provenance tag for a chunk's source page range."""
    if first is None:
        return ""
    label = f"p. {first}" if first == last else f"pp. {first}–{last}"
    return f'<span class="pages">{label}</span>'


def empty(icon: str, title: str, note: str) -> None:
    _md(f"""
    <div class="empty">
      <div class="empty-ico">{icon}</div>
      <h4>{title}</h4>
      <p>{note}</p>
    </div>
    """)


def sidebar_brand(name: str, role: str, mark: str = "◈") -> None:
    st.sidebar.markdown(_h(f"""
    <div class="sb-brand">
      <div class="sb-mark">{mark}</div>
      <div><div class="sb-name">{name}</div><div class="sb-role">{role}</div></div>
    </div>
    """), unsafe_allow_html=True)


def sidebar_label(text: str) -> None:
    st.sidebar.markdown(f'<div class="sb-label">{text}</div>', unsafe_allow_html=True)


def risk_banner(level: str, label: str, headline: str) -> None:
    _md(f"""
    <div class="risk {level}">
      <div class="risk-level">{label}</div>
      <div class="risk-text">{headline}</div>
    </div>
    """)


def finding_card(title: str, severity: str, detail: str, locations: str = "") -> None:
    loc = f'<div class="finding-loc">{locations}</div>' if locations else ""
    _md(f"""
    <div class="finding {severity}">
      <div class="finding-top">
        <span class="finding-title">{title}</span>
        <span class="finding-sev">{severity}</span>
      </div>
      <div class="finding-detail">{detail}</div>{loc}
    </div>
    """)


def checklist(items) -> None:
    chips = "".join(f'<div class="checkitem">✓ {i}</div>' for i in items)
    _md(f'<div class="checklist">{chips}</div>')


def run_row(name: str, meta: str, ok: bool = True) -> str:
    dot = "run-dot" if ok else "run-dot bad"
    return _h(f"""
    <div class="run">
      <div class="{dot}"></div>
      <div class="run-name">{name}</div>
      <div class="run-meta">{meta}</div>
    </div>
    """)
