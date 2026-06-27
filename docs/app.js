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

const printEl = document.getElementById("print-btn");
if (printEl) printEl.addEventListener("click", () => window.print());

function esc(s){return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}

function renderTextWithGlosses(text, glosses){
  // glosses: [{start,end,gloss}] sorted, non-overlapping (merger guarantees this).
  // Sparse by design: only rare one-off words. Key terms are taught up front in
  // the vocab box, not re-glossed inline, so recurring words carry no tooltip.
  //
  // Two presentations of the same data: on SCREEN the glossed word gets a hover
  // tooltip (.tip); in PRINT it gets a superscript number (.gn, hidden on screen)
  // and the gloss text is mirrored into a margin-gutter <aside class="gloss-notes">
  // beside the paragraph. Returns {body, notes} so the caller can place the
  // gutter notes in a print grid alongside the text.
  let body = "", pos = 0, n = 0;
  const items = [];
  for(const g of glosses){
    if(g.start < pos) continue;
    n++;
    const word = text.slice(g.start, g.end);
    body += esc(text.slice(pos, g.start));
    body += `<span class="gloss" tabindex="0">${esc(word)}` +
           `<sup class="gn">${n}</sup>` +
           `<span class="tip">${esc(g.gloss)}</span></span>`;
    items.push(`<li><span class="gw">${esc(word)}</span> <span class="gt">${esc(g.gloss)}</span></li>`);
    pos = g.end;
  }
  body += esc(text.slice(pos));
  const notes = items.length ? `<aside class="gloss-notes"><ol>${items.join("")}</ol></aside>` : "";
  return {body, notes};
}

async function showWorks(){
  titleEl.textContent = "ῥᾳδίως";
  authorEl.textContent = "";
  if (printEl) printEl.hidden = true;
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

function bookNav(doc){
  // βίβλος selector for multi-book works; rendered above the text.
  const m = doc.meta;
  if(!m.books || !m.books.length) return "";
  const cur = String(m.book);
  const idx = m.books.findIndex(b => String(b.n) === cur);
  const prev = idx > 0
    ? `<button class="book-prev" data-n="${esc(String(m.books[idx-1].n))}" aria-label="previous book">‹</button>`
    : `<span class="book-spacer"></span>`;
  const next = (idx >= 0 && idx < m.books.length-1)
    ? `<button class="book-next" data-n="${esc(String(m.books[idx+1].n))}" aria-label="next book">›</button>`
    : `<span class="book-spacer"></span>`;
  const opts = m.books.map(b =>
    `<option value="${esc(String(b.n))}"${String(b.n)===cur?" selected":""}>Βίβλος ${esc(String(b.n))}</option>`).join("");
  return `<nav class="book-nav">${prev}<label class="book-sel"><span class="lab">Βίβλος</span>` +
    `<select class="book-select">${opts}</select></label>${next}</nav>`;
}

async function loadBook(id, book){
  const n = String(book);
  history.pushState({id, book:n}, "", `?w=${encodeURIComponent(id)}&b=${encodeURIComponent(n)}`);
  app.innerHTML = `<p class="loading">Φορτώνει…</p>`;
  try{
    const doc = await (await fetch(`${encodeURIComponent(id)}/${encodeURIComponent(n)}.json`)).json();
    renderReader(doc);
    window.scrollTo(0, 0);
  }catch(e){
    app.innerHTML = `<p class="loading">Could not load book ${esc(n)} — ${esc(e.message)}</p>`;
  }
}

function renderReader(doc){
  titleEl.textContent = doc.meta.title;
  authorEl.textContent = doc.meta.author || "";
  if (printEl) printEl.hidden = false;
  const summaries = {}; doc.summaries.forEach(s => summaries[s.chunk] = s);
  let html = bookNav(doc), lastChunk = -1;

  for(const sec of doc.sections){
    const heading = doc.meta.books ? "" : `<h2>§ ${esc(sec.n ?? "")}</h2>`;
    html += `<section class="text-section">${heading}`;
    for(const p of sec.paragraphs){
      if(p.chunk !== lastChunk){
        const s = summaries[p.chunk];
        if(s) html += chunkIntro(s);
        lastChunk = p.chunk;
      }
      const {body, notes} = renderTextWithGlosses(p.text, p.glosses || []);
      const spk = p.label ? `<span class="speaker">${esc(p.label)}</span>` : "";
      const ref = p.ref ? `<span class="ref">${esc(p.ref)}</span>` : "";
      if(p.lines){
        // verse: ref goes in a left gutter so it isn't squished inline with the text
        const vref = p.ref ? `<span class="vref">${esc(p.ref)}</span>` : `<span class="vref"></span>`;
        html += `<div class="verse-row">${vref}<p class="verse">${spk}${body}</p></div>`;
      }else if(notes){
        // print: main text + gloss gutter; screen hides the gutter via CSS.
        html += `<div class="para"><p class="speech">${spk}${ref}${body}</p>${notes}</div>`;
      }else{
        html += `<p class="speech">${spk}${ref}${body}</p>`;
      }
    }
    html += `</section>`;
  }
  app.innerHTML = html;

  // βίβλος selector + prev/next for multi-book works
  const sel = app.querySelector(".book-select");
  if(sel){
    const id = doc.meta.id;
    sel.addEventListener("change", () => loadBook(id, sel.value));
    app.querySelectorAll(".book-prev,.book-next").forEach(b =>
      b.addEventListener("click", () => loadBook(id, b.dataset.n)));
  }

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
    let doc = await (await fetch(`${encodeURIComponent(id)}.json`)).json();
    // multi-book index? load the requested (or first) book's reader doc.
    if(doc.books && !doc.sections){
      const book = params.get("b") || String(doc.books[0].n);
      doc = await (await fetch(`${encodeURIComponent(id)}/${encodeURIComponent(book)}.json`)).json();
    }
    renderReader(doc);
  }catch(e){
    app.innerHTML = `<p class="loading">Could not load ${esc(id)} — ${esc(e.message)}</p>`;
  }
}
window.addEventListener("popstate", () => main());
main();
