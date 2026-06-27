// rhadios — static reader. Fetches docs/<id>.json and renders with
// pre-computed gloss offsets. No matching logic here; the merger did it all.

const app = document.getElementById("app");
const titleEl = document.getElementById("title");
const authorEl = document.getElementById("author");
const toggleEl = document.getElementById("theme-toggle");

// Theme toggle: flip data-theme on <html>, persist, sync button glyph.
function syncToggle(){
  const t = document.documentElement.getAttribute("data-theme");
  toggleEl.textContent = t === "dark" ? "☀" : "☾";
  toggleEl.setAttribute("aria-label", t === "dark" ? "Switch to light mode" : "Switch to dark mode");
}
toggleEl.addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("rhadios-theme", next);
  syncToggle();
});
syncToggle();

function esc(s){return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}

function renderTextWithGlosses(text, glosses){
  // glosses: [{start,end,gloss}] sorted, non-overlapping (merger guarantees this).
  // Sparse by design: only rare one-off words. Key terms are taught up front in
  // the vocab box, not re-glossed inline, so recurring words carry no tooltip.
  let out = "", pos = 0;
  for(const g of glosses){
    if(g.start < pos) continue;
    out += esc(text.slice(pos, g.start));
    out += `<span class="gloss" tabindex="0">${esc(text.slice(g.start, g.end))}` +
           `<span class="tip">${esc(g.gloss)}</span></span>`;
    pos = g.end;
  }
  out += esc(text.slice(pos));
  return out;
}

async function showWorks(){
  titleEl.textContent = "ῥᾳδίως";
  authorEl.textContent = "";
  let works = [];
  try{
    works = await (await fetch("works.json")).json();
  }catch(e){}
  if(!works.length){ app.innerHTML = `<p class="loading">No works built yet. Run the pipeline first.</p>`; return; }
  app.innerHTML = `<ul class="works">` + works.map(w =>
    `<li><a href="?w=${encodeURIComponent(w.id)}">${esc(w.title)}` +
    `<small>${esc(w.author||"")}</small></a></li>`).join("") + `</ul>`;
}

// Vocab box: key terms shown before each chunk. Visible by default,
// collapsible via the header. A global pref (rhadios-vocab) sets the initial state
// of every box on render; toggling a box flips it locally and updates the pref
// for next load. (Pinning — keeping it floating while scrolling — is deferred.)
function vocabPref(){
  return localStorage.getItem("rhadios-vocab") === "closed" ? "closed" : "open";
}

function chunkIntro(s){
  // Renders the vocab box + summary that precede a chunk's first paragraph.
  const terms = s.key_terms || [];
  if(!terms.length && !s.text) return "";
  let html = "";
  if(terms.length){
    const closed = vocabPref() === "closed" ? " collapsed" : "";
    html += `<div class="vocab${closed}">`;
    html += `<div class="vocab-head" role="button" tabindex="0" ` +
            `aria-expanded="${closed ? "false" : "true"}">`;
    html += `<span class="vocab-lab">Λέξεις κλειδιά</span>`;
    if(s.range) html += `<span class="vocab-range">(${esc(s.range)})</span>`;
    html += `<span class="vocab-caret" aria-hidden="true"></span></div>`;
    html += `<dl class="vocab-list">`;
    for(const t of terms){
      html += `<dt>${esc(t.lemma)}</dt><dd>${esc(t.gloss)}</dd>`;
    }
    html += `</dl></div>`;
  }
  if(s.text){
    html += `<div class="summary"><span class="lab">Σύνοψις (${esc(s.range||"")})</span>` +
            esc(s.text) + `</div>`;
  }
  return html;
}

function renderReader(doc){
  titleEl.textContent = doc.meta.title;
  authorEl.textContent = doc.meta.author || "";
  const summaries = {}; doc.summaries.forEach(s => summaries[s.chunk] = s);
  let html = "", lastChunk = -1;

  for(const sec of doc.sections){
    html += `<section class="text-section"><h2>§ ${esc(sec.n ?? "")}</h2>`;
    for(const p of sec.paragraphs){
      if(p.chunk !== lastChunk){
        const s = summaries[p.chunk];
        if(s) html += chunkIntro(s);
        lastChunk = p.chunk;
      }
      const body = renderTextWithGlosses(p.text, p.glosses || []);
      const spk = p.label ? `<span class="speaker">${esc(p.label)}</span>` : "";
      const ref = p.ref ? `<span class="ref">${esc(p.ref)}</span>` : "";
      if(p.lines){
        html += `<p class="verse">${spk}${ref}${esc(p.lines.join("\n"))}</p>`;
      }else{
        html += `<p class="speech">${spk}${ref}${body}</p>`;
      }
    }
    html += `</section>`;
  }
  app.innerHTML = html;

  // inline tooltip glosses: tap to toggle on touch
  app.querySelectorAll(".gloss").forEach(el=>{
    el.addEventListener("click", ()=> el.classList.toggle("show"));
  });
  // vocab box collapse: click/Enter on header toggles; remember pref
  app.querySelectorAll(".vocab-head").forEach(head=>{
    const toggle = () => {
      const box = head.closest(".vocab");
      const collapsed = box.classList.toggle("collapsed");
      head.setAttribute("aria-expanded", collapsed ? "false" : "true");
      localStorage.setItem("rhadios-vocab", collapsed ? "closed" : "open");
    };
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", e=>{
      if(e.key === "Enter" || e.key === " "){ e.preventDefault(); toggle(); }
    });
  });
}

async function main(){
  const params = new URLSearchParams(location.search);
  const id = params.get("w");
  if(!id) return showWorks();
  app.innerHTML = `<p class="loading">Φορτώνει…</p>`;
  try{
    const doc = await (await fetch(`${encodeURIComponent(id)}.json`)).json();
    renderReader(doc);
  }catch(e){
    app.innerHTML = `<p class="loading">Could not load ${esc(id)}.json — ${esc(e.message)}</p>`;
  }
}
main();
