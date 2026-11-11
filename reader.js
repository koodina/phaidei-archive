const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

//browser storage with fallback to nothing if it fails for priv
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : v; }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch {}
  }
};

const bookId = new URLSearchParams(location.search).get("book");
let book = null;
let chapterEls = [];
let ready = false;  


function scrollMax() {
  return document.documentElement.scrollHeight - window.innerHeight;
}
function scrollRatio() {
  const max = scrollMax();
  return max > 0 ? window.scrollY / max : 0;
}

//keep your place when the text size changes
function keepPosition(change) {
  const r = scrollRatio();
  change();
  requestAnimationFrame(() => window.scrollTo(0, r * scrollMax()));
}


let fontSize = Number(store.get("readerFont", "21"));
function applyFont() {
  fontSize = Math.min(34, Math.max(15, fontSize));
  document.documentElement.style.setProperty("--fs", fontSize + "px");
  store.set("readerFont", String(fontSize));
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme").textContent = theme === "night" ? "Day DemiGod" : "Night DemiGod";
  store.set("readerTheme", theme);
}
applyFont();
applyTheme(store.get("readerTheme", "night"));

$("smaller").addEventListener("click", () => keepPosition(() => { fontSize -= 2; applyFont(); }));
$("bigger").addEventListener("click", () => keepPosition(() => { fontSize += 2; applyFont(); }));
$("theme").addEventListener("click", () =>
  applyTheme(document.documentElement.dataset.theme === "night" ? "day" : "night"));


function showError(message) {
  $("text").innerHTML = `<p class="error"></p>`;
  $("text").firstChild.textContent = message;
  $("barTitle").textContent = "Phaidei Archive";
  document.querySelectorAll(".tools select").forEach(s => { s.hidden = true; });
}

if (!bookId) {
  showError("No fic selected! Go back to the archive and pick one!");
} else {
  fetch(`books/${encodeURIComponent(bookId)}.json`)
    .then(r => { if (!r.ok) throw new Error("missing"); return r.json(); })
    .then(start)
    .catch(() => showError("Could not load this fic :( check that the file exists in the books folder!"));
}

function detailsHtml() {
  if (book.info_html) return book.info_html; 
  const tags = book.tags || [];
  return tags.length ? `<dl><dt>Tags</dt><dd>${esc(tags.join(", "))}</dd></dl>` : ""; 
}

function fillSquare(id, label, body) {
  if (!body) return;
  $(id).innerHTML = `<h2 class="sq-label">${label}</h2><div class="sq-body">${body}</div>`;
  $(id).hidden = false;
}

function start(data) {
  book = data;
  const title = book.title || "Untitled";
  document.title = title + " · Phaidei Archive";
  $("barTitle").textContent = title;


  const storyChapters = book.chapters.filter(c => !/^afterword$/i.test(c.title));
  const badges = [
    book.rating ? `<span class="badge" data-rating="${esc(book.rating)}">${esc(book.rating)}</span>` : "",
    book.status ? `<span class="badge ${book.status === "Complete" ? "done" : ""}">${esc(book.status)}</span>` : "",
    book.words ? `<span class="meta">${Number(book.words).toLocaleString()} words</span>` : "",
    storyChapters.length > 1 ? `<span class="meta">${storyChapters.length} chapters</span>` : ""
  ].join("");
  const details = detailsHtml();
  $("hero").innerHTML = `
    <h1>${esc(title)}</h1>
    ${book.author ? `<p class="by">by ${esc(book.author)}</p>` : ""}
    <div class="badges">${badges}</div>
    ${details ? `<div class="hero-info">${details}</div>` : ""}`;
  $("hero").hidden = false;


  fillSquare("summarySquare", "Summary",
    book.summary_html || (book.summary ? `<p>${esc(book.summary)}</p>` : ""));
  fillSquare("notesSquare", "Author’s notes", book.notes_html);
  $("storyStart").hidden = false;


  const single = book.chapters.length === 1;
  $("text").innerHTML = book.chapters.map((c, i) => `
    <section class="chapter" id="chapter-${i + 1}">
      ${single ? "" : `<h2 class="chapter-title">${esc(c.title)}</h2>`}
      ${c.html}
    </section>`).join("");   // already cleaned by make_json.py
  chapterEls = [...document.querySelectorAll(".chapter")];


  if (single) {
    $("chapterSelect").hidden = true;
  } else {
    $("chapterSelect").innerHTML = `<option value="-1">Top</option>` +
      book.chapters.map((c, i) => `<option value="${i}">${esc(c.title)}</option>`).join("");
  }

  //end redirection to 
  const unfinished = book.status && book.status !== "Complete";
  $("theEnd").innerHTML = `
    <p class="the-end">${unfinished ? "To be continued, keep an eye on the ao3 link!" : "The End"}</p>
    <div class="end-links">
      <button id="toTop">Back to top</button>
      ${book.ao3 ? `<a href="${esc(book.ao3)}" target="_blank" rel="noopener noreferrer">Original on AO3 (please leave kudos &amp; comments when it’s back!)</a>` : ""}
      <a href="index.html">Back to the archive</a>
    </div>`; 
  $("theEnd").hidden = false;
  $("toTop").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

  // how the title in the top bar once the title square scrolls away hehe
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(([entry]) =>
      $("bar").classList.toggle("scrolled", !entry.isIntersecting)).observe($("hero")); 
  }


  const fromHash = (location.hash.match(/^#chapter-(\d+)$/) || [])[1];
  const savedPos = parseFloat(store.get("reader:" + bookId + ":pos", "0")) || 0;
  const restore = () => { // jump to where you left off or the chapter in the URL hash
    if (fromHash && chapterEls[fromHash - 1]) chapterEls[fromHash - 1].scrollIntoView();
    else if (savedPos > 0.005 && savedPos < 0.995) window.scrollTo(0, savedPos * scrollMax());
    ready = true;
    updateReading();
  };
  (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(restore);
}

//jump to a chapter when the menu changes
$("chapterSelect").addEventListener("change", e => {
  const i = Number(e.target.value);
  if (i < 0) window.scrollTo({ top: 0, behavior: "smooth" });
  else chapterEls[i].scrollIntoView({ behavior: "smooth", block: "start" }); 
});

//phaideiii progress line + works w chapter position
let ticking = false;
let saveTimer = null;
function updateReading() {
  ticking = false;
  const ratio = Math.min(1, Math.max(0, scrollRatio()));
  $("progress").style.width = ratio * 100 + "%";

  const y = window.scrollY + 140;
  let current = -1;
  chapterEls.forEach((el, i) => {
    if (el.getBoundingClientRect().top + window.scrollY <= y) current = i;
  });
  $("chapterSelect").value = String(current);

  if (ready) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => store.set("reader:" + bookId + ":pos", String(ratio)), 400);
  }
}
window.addEventListener("scroll", () => {
  if (!ticking) { ticking = true; requestAnimationFrame(updateReading); }
}, { passive: true });