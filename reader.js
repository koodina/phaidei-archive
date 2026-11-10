const $ = id => document.getElementById(id);

//wrapping browser localStorage access in try/catch so it doesn't break if the user has disabled it
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
let current = 0;



let fontSize = Number(store.get("readerFont", "21"));
function applyFont() {
  fontSize = Math.min(34, Math.max(15, fontSize));
  document.documentElement.style.setProperty("--fs", fontSize + "px");
  store.set("readerFont", String(fontSize));
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme").textContent = theme === "Night DemiGod" ? "Day DemiGod" : "Night DemiGod";
  store.set("readerTheme", theme);
}
applyFont();
applyTheme(store.get("readerTheme", "Night DemiGod"));

$("smaller").addEventListener("click", () => { fontSize -= 2; applyFont(); }); //this is a bit of a hack i saw, but it works I don't want to use a range input because it looks bad and doesn't work well on mobile how this works is it changes the font size variable and then calls applyFont which updates the CSS variable and saves it to localStorage
$("bigger").addEventListener("click", () => { fontSize += 2; applyFont(); }); 
$("theme").addEventListener("click", () =>
  applyTheme(document.documentElement.dataset.theme === "Night DemiGod" ? "Day Demigod" : "Night DemiGod"));

//loading the book jison file and starting the reader 
function showError(message) {
  $("text").innerHTML = `<p class="error"></p>`;
  $("text").firstChild.textContent = message;
  $("bookTitle").textContent = "Phaidei Archive";
  document.querySelector(".pager").style.display = "none";
}

if (!bookId) {
  showError("No fic selected! Go back to the archive and pick one!");
} else {
  fetch(`books/${encodeURIComponent(bookId)}.json`)
    .then(r => { if (!r.ok) throw new Error("missing"); return r.json(); }) 
    .then(start)
    .catch(() => showError("Could not load this fic :( check that the file exists in the books folder!"));
}

function start(data) {
  book = data;
  $("bookTitle").textContent = book.title;
  $("bookAuthor").textContent = book.author ? "by " + book.author : "";
  document.title = book.title + " · Phaidei Archive"; 

  $("chapterSelect").innerHTML = "";
  book.chapters.forEach((c, i) => {
    const opt = document.createElement("option");
    opt.value = i;
    opt.textContent = c.title;
    $("chapterSelect").appendChild(opt);
  });

  if (book.ao3) {
    const a = document.createElement("a");
    a.href = book.ao3;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = "Original on AO3 (please leave a kudos or comment when it’s back!)";
    $("ao3link").appendChild(a);
  }

  //strt hash, or saved position, or 0
  const fromHash = parseInt(location.hash.slice(1), 10);
  const saved = parseInt(store.get("reader:" + bookId, "0"), 10);
  show(fromHash ? fromHash - 1 : saved);
}

//showing a ch, and updating th Ui 
function show(i) {
  current = Math.min(book.chapters.length - 1, Math.max(0, i || 0));
  const chapter = book.chapters[current];

  $("text").innerHTML = chapter.html;   //already cleaned by make_json.py
  const heading = document.createElement("h2");
  heading.className = "chapter-title";
  heading.textContent = chapter.title;
  $("text").prepend(heading);

  $("chapterSelect").value = String(current);
  $("pos").textContent = `${current + 1} / ${book.chapters.length}`;
  $("prev").disabled = current === 0;
  $("next").disabled = current === book.chapters.length - 1;

  history.replaceState(null, "", "#" + (current + 1));
  store.set("reader:" + bookId, String(current));
  window.scrollTo(0, 0);
  updateProgress();
}

$("prev").addEventListener("click", () => show(current - 1));
$("next").addEventListener("click", () => show(current + 1));
$("chapterSelect").addEventListener("change", e => show(Number(e.target.value)));
window.addEventListener("hashchange", () => {
  const n = parseInt(location.hash.slice(1), 10);
  if (book && n && n - 1 !== current) show(n - 1);
});


document.addEventListener("keydown", e => { //adds arrow nav
  if (!book || e.target.matches("select, input, textarea")) return;
  if (e.key === "ArrowRight") show(current + 1);
  if (e.key === "ArrowLeft") show(current - 1);
});

//phaideiiii themed progress line eats
function updateProgress() {
  const d = document.documentElement;
  const max = d.scrollHeight - d.clientHeight;
  $("progress").style.width = (max > 0 ? (d.scrollTop / max) * 100 : 0) + "%";
}
window.addEventListener("scroll", updateProgress, { passive: true });