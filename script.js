let fics = [];
let savedOnly = false;
const saved = new Set(JSON.parse(localStorage.getItem("saved") || "[]"));
const expandedTags = new Set();


const tagFilter = new Map();
let allTags = [];      
let tagMatches = [];   //dropdown selection
let highlight = -1;    

const $ = id => document.getElementById(id); 
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TAG_LIMIT = 8;       
const DROPDOWN_LIMIT = 80; 

//lwr / up match
const normalize = s => String(s ?? "")
  .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
  .toLowerCase()
  .replace(/[^\p{L}\p{N}]+/gu, " ")
  .trim();



fetch("fics.json")
  .then(r => r.json())
  .then(data => {
    fics = data;
    fics.forEach(f => {
      f.id = f.files[0];
      f._search = normalize([f.title, f.author, f.summary].join(" "));
    });
    buildTagIndex();
    fillSelect("rating", "rating", "All ratings");
    fillSelect("status", "status", "All statuses");
    render();
  })
  .catch(() => {
    $("list").textContent = "Could not load fics.json :( check the file for typos!";
  });

function fillSelect(selectId, key, label) { 
  const values = [...new Set(fics.map(f => f[key]).filter(Boolean))].sort();
  $(selectId).innerHTML =
    `<option value="">${label}</option>` +
    values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
}


function buildTagIndex() {
  const counts = new Map();
  fics.forEach(f => (f.tags || []).forEach(t => counts.set(t, (counts.get(t) || 0) + 1)));
  allTags = [...counts]
    .map(([tag, count]) => ({ tag, count, norm: normalize(tag) }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
}

//phaidei tags glow w their colors hehe
function tagClass(tag) {
  const t = tag.toLowerCase();
  const m = t.includes("mydei");
  const p = t.includes("phainon");
  if (m && p) return "both";
  if (m) return "red";
  if (p) return "blue";
  return "";
}

//tag filtering
function setTag(tag, mode) { // mode here include / exclude 
  if (tagFilter.get(tag) === mode) tagFilter.delete(tag); // hitting the same tag twice removes it from the filter
  else tagFilter.set(tag, mode);
  render();
}

function renderActiveTags() { 
  if (!tagFilter.size) { $("activeTags").innerHTML = ""; return; }
  $("activeTags").innerHTML = [...tagFilter].map(([tag, mode]) => `
    <span class="chip ${mode}">
      <button class="chip-main" data-flip="${esc(tag)}" title="Click to switch between include and exclude">${mode === "exclude" ? '<span class="not">not</span>' : ""}${esc(tag)}</button>
      <button class="chip-x" data-remove="${esc(tag)}" aria-label="Remove ${esc(tag)}">✕</button>
    </span>`).join("") + `<button id="clearTags" class="chip-clear">Clear all</button>`;
}

function renderTagList() {
  const words = normalize($("tagInput").value).split(" ").filter(Boolean);
  const matches = allTags.filter(t => words.every(w => t.norm.includes(w)));
  tagMatches = matches.slice(0, DROPDOWN_LIMIT);
  if (highlight >= tagMatches.length) highlight = tagMatches.length - 1;

  if (!tagMatches.length) {
    $("tagList").innerHTML = `<li class="none">No tags match.</li>`;
    return;
  }
  $("tagList").innerHTML = tagMatches.map((t, i) => {
    const mode = tagFilter.get(t.tag) || "";
    return `
      <li role="option" class="opt ${mode} ${i === highlight ? "hl" : ""}" data-tag="${esc(t.tag)}" aria-selected="${mode === "include"}">
        <span class="mark">${mode === "include" ? "✓" : mode === "exclude" ? "✕" : ""}</span>
        <span class="name">${esc(t.tag)}</span>
        <span class="n">${t.count}</span>
        <button type="button" class="ex" data-exclude="${esc(t.tag)}" title="Hide fics with this tag">${mode === "exclude" ? "undo" : "exclude"}</button>
      </li>`;
  }).join("") + (matches.length > tagMatches.length
    ? `<li class="none">${matches.length - tagMatches.length} more… type to narrow down</li>` : "");
}

function openList() {
  $("tagList").hidden = false;
  $("tagInput").setAttribute("aria-expanded", "true");
  renderTagList();
}
function closeList() {
  $("tagList").hidden = true;
  $("tagInput").setAttribute("aria-expanded", "false");
  highlight = -1;
}
function pickTag(tag, mode) {
  $("tagInput").value = "";
  highlight = -1;
  setTag(tag, mode);
}

$("tagInput").addEventListener("focus", openList);
$("tagInput").addEventListener("click", openList);
$("tagInput").addEventListener("input", () => { highlight = -1; openList(); });
$("tagInput").addEventListener("keydown", e => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    openList();
    const step = e.key === "ArrowDown" ? 1 : -1;
    highlight = Math.max(0, Math.min(tagMatches.length - 1, highlight + step));
    renderTagList();
    const row = $("tagList").querySelector(".hl");
    if (row) row.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter") {
    e.preventDefault();
    const i = highlight >= 0 ? highlight : ($("tagInput").value.trim() ? 0 : -1);
    if (tagMatches[i]) pickTag(tagMatches[i].tag, "include");
  } else if (e.key === "Escape") {
    closeList();
  }
});


$("tagList").addEventListener("mousedown", e => e.preventDefault()); //prevent focus loss 
$("tagList").addEventListener("click", e => {
  const ex = e.target.closest("[data-exclude]");
  if (ex) { pickTag(ex.dataset.exclude, "exclude"); return; }
  const opt = e.target.closest(".opt");
  if (opt) pickTag(opt.dataset.tag, "include");
});
document.addEventListener("mousedown", e => {
  if (!$("tagpicker").contains(e.target)) closeList();
});

$("activeTags").addEventListener("click", e => { 
  const flip = e.target.closest("[data-flip]");
  const remove = e.target.closest("[data-remove]");
  if (flip) {
    const tag = flip.dataset.flip;
    tagFilter.set(tag, tagFilter.get(tag) === "include" ? "exclude" : "include");
  } else if (remove) {
    tagFilter.delete(remove.dataset.remove);
  } else if (e.target.closest("#clearTags")) {
    tagFilter.clear();
  } else {
    return;
  }
  render();
});

//filtering part
function getVisible() {
  const words = normalize($("search").value).split(" ").filter(Boolean);
  const rating = $("rating").value;
  const status = $("status").value;
  const include = [...tagFilter].filter(([, m]) => m === "include").map(([t]) => t);
  const exclude = [...tagFilter].filter(([, m]) => m === "exclude").map(([t]) => t);

  const list = fics.filter(f => {
    const tags = f.tags || [];
    if (words.some(w => !f._search.includes(w))) return false;
    if (rating && f.rating !== rating) return false;
    if (status && f.status !== status) return false;
    if (include.some(t => !tags.includes(t))) return false;   
    if (exclude.some(t => tags.includes(t))) return false;    
    if (savedOnly && !saved.has(f.id)) return false;
    return true;
  });

  const sort = $("sort").value;
  if (sort === "title") list.sort((a, b) => a.title.localeCompare(b.title));
  if (sort === "author") list.sort((a, b) => a.author.localeCompare(b.author));
  if (sort === "words") list.sort((a, b) => (b.words || 0) - (a.words || 0));
  return list;
}




function render() {
  const list = getVisible();

  $("count").textContent = `${list.length} fic${list.length === 1 ? "" : "s"}`;
  renderActiveTags();
  if (!$("tagList").hidden) renderTagList();

  if (!list.length) {
    $("list").innerHTML = `<p class="empty">No perfect read for you today :( Try removing a filter!</p>`;
    return;
  }

  $("list").innerHTML = list.map(f => {
    const readBtn = f.book
      ? `<a class="read" href="reader.html?book=${encodeURIComponent(f.book)}">Read</a>` : "";

    const links = f.files.map(file => {
      const isEpub = file.toLowerCase().endsWith(".epub");
      const label = isEpub ? "Download EPUB" : "Open PDF";
      const cls = f.book ? ' class="ghost"' : "";
      return `<a${cls} href="${encodeURI(file)}" target="_blank" rel="noopener"${isEpub ? " download" : ""}>${label}</a>`;
    }).join("");

    const original = f.ao3
      ? `<a class="ghost" href="${esc(f.ao3)}" target="_blank" rel="noopener">AO3 original</a>` : "";

    const allCardTags = f.tags || [];
    const open = expandedTags.has(f.id);
    const shownTags = open ? allCardTags : allCardTags.slice(0, TAG_LIMIT);
    const tagHtml = shownTags.map(t =>
      `<button class="tag ${tagClass(t)} ${tagFilter.get(t) === "include" ? "on" : ""}" data-tag="${esc(t)}">${esc(t)}</button>`).join("");
    const moreHtml = allCardTags.length > TAG_LIMIT
      ? `<button class="more" data-more="${esc(f.id)}">${open ? "show less" : "+" + (allCardTags.length - TAG_LIMIT) + " more"}</button>`
      : "";

    const badges = [
      f.rating ? `<span class="badge" data-rating="${esc(f.rating)}">${esc(f.rating)}</span>` : "",
      f.status ? `<span class="badge ${f.status === "Complete" ? "done" : ""}">${esc(f.status)}</span>` : "",
      f.words ? `<span class="words">${Number(f.words).toLocaleString()} words</span>` : ""
    ].join(""); //create a string of badges for eaach fic

    const isSaved = saved.has(f.id);

    return `
      <article class="fic" data-id="${esc(f.id)}">
        <h2>${esc(f.title)}</h2>
        <p class="by">by ${esc(f.author)}</p>
        <div class="badges">${badges}</div>
        ${f.summary ? `<p class="summary" title="Click to read more">${esc(f.summary)}</p>` : ""}
        <div class="tags">${tagHtml}${moreHtml}</div>
        <div class="actions">
          ${readBtn}${links}${original}
          <button class="save ${isSaved ? "on" : ""}" data-id="${esc(f.id)}">
            ${isSaved ? "★ Bookmarked" : "☆ Bookmark"}
          </button>
        </div>
      </article>`;
  }).join("");
}


$("list").addEventListener("click", e => {
  const tagBtn = e.target.closest(".tag");
  if (tagBtn) {
    setTag(tagBtn.dataset.tag, "include");   //click to include , double excludes 
    return;
  }

  const moreBtn = e.target.closest(".more");
  if (moreBtn) {
    const id = moreBtn.dataset.more;
    expandedTags.has(id) ? expandedTags.delete(id) : expandedTags.add(id);
    render();
    return;
  }

  const summary = e.target.closest(".summary");
  if (summary) {
    summary.classList.toggle("open");
    return; 
  }

  const saveBtn = e.target.closest(".save");
  if (saveBtn) {
    const id = saveBtn.dataset.id;
    saved.has(id) ? saved.delete(id) : saved.add(id);
    localStorage.setItem("saved", JSON.stringify([...saved]));
    render();
  }
});

["search", "rating", "status", "sort"].forEach(id =>
  $(id).addEventListener("input", render));

$("savedToggle").addEventListener("click", e => {
  savedOnly = !savedOnly;
  e.target.classList.toggle("on", savedOnly);
  render();
});



$("random").addEventListener("click", () => {
  const list = getVisible();
  if (!list.length) return;
  const pick = list[Math.floor(Math.random() * list.length)];
  const el = [...document.querySelectorAll(".fic")].find(a => a.dataset.id === pick.id);
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 2000);
}); 