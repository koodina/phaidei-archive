import json
import re
import zipfile
import html
import posixpath
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

FIC_DIR = Path("fics")
BOOK_DIR = Path("books")
OUT = Path("fics.json")



def clean(text):
    #unescape html stuff, remove tags
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"</p>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def links_in(dd):
    #gets text inside <a> tags n cleans it 
    return [clean(a) for a in re.findall(r"<a[^>]*>(.*?)</a>", dd, re.S)]



def read_epub(path):
    info = {}
    with zipfile.ZipFile(path) as z:
        opf_name = next(n for n in z.namelist() if n.endswith(".opf"))
        opf = z.read(opf_name).decode("utf-8")

        title = re.search(r"<dc:title>(.*?)</dc:title>", opf, re.S)
        if title:
            info["title"] = clean(title.group(1))

        authors = re.findall(r"<dc:creator[^>]*>(.*?)</dc:creator>", opf, re.S)
        if authors:
            info["author"] = ", ".join(clean(a) for a in authors)

        summary = re.search(r"<dc:description>(.*?)</dc:description>", opf, re.S)
        if summary:
            info["summary"] = clean(html.unescape(summary.group(1)))

        for name in sorted(z.namelist()):
            if not name.endswith((".xhtml", ".html")):
                continue
            page = z.read(name).decode("utf-8")
            if 'class="tags"' not in page and "class=\"calibre3\"" not in page:
                continue

            link = re.search(r"https://archiveofourown\.org/works/\d+", page)
            if link:
                info["ao3"] = link.group(0)

            fields = {}
            for dt, dd in re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", page, re.S):
                fields[clean(dt).rstrip(":")] = dd

            if "Rating" in fields:
                info["rating"] = clean(fields["Rating"])

            tags = []
            for key in ("Archive Warning", "Relationship", "Additional Tags"):
                if key in fields:
                    tags += links_in(fields[key])
            tags = [t for t in tags if t != "No Archive Warnings Apply"]
            info["tags"] = list(dict.fromkeys(tags))

            stats = clean(fields.get("Stats", ""))
            words = re.search(r"Words:\s*([\d,]+)", stats)
            if words:
                info["words"] = int(words.group(1).replace(",", ""))
            chapters = re.search(r"Chapters:\s*(\d+)/(\d+|\?)", stats)
            if chapters:
                done = chapters.group(1) == chapters.group(2) #verify if number/number of chapters so if fic complete
                info["status"] = "Complete" if done else "In Progress"
            break
    return info


#turnint epub into a json file for reader 
ALLOWED = {
    "p", "br", "hr", "em", "i", "strong", "b", "u", "s", "del", "sup", "sub",
    "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "dl", "dt", "dd", "pre", "code", "div", "span", "a", "details", "summary",
    "table", "thead", "tbody", "tr", "td", "th",
}
SKIP = {"script", "style", "head", "iframe", "object", "embed", "svg", "math", "form"}
VOID = {"br", "hr"}


class Cleaner(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith(("http://", "https://")):
                self.out.append(
                    f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
                self.links.append(True)
            else:
                self.links.append(False)  #no including internal links in reader copy
            return
        self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in SKIP:
            if self.skip:
                self.skip -= 1
            return
        if self.skip or tag not in ALLOWED or tag in VOID:
            return
        if tag == "a":
            if self.links and self.links.pop():
                self.out.append("</a>")
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False)) 


def tidy(s):
    s = re.sub(r"(?:\s*<br>)+\s*</p>", "</p>", s)   #this removes any br tags that are at the end of a paragraph so that it doesn't have a bunch of empty lines at the end of a paragraph
    s = re.sub(r"<p>\s*(?:<br>\s*)*</p>", "", s)     #empty paragraphs
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def read_sections(path):
    #returns a list of sections with title and html content from the epub file
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        opf_name = next(n for n in names if n.endswith(".opf")) #opf here means the package file that contains the metadata and manifest of the epub
        opf = z.read(opf_name).decode("utf-8-sig")
        base = posixpath.dirname(opf_name) # base is where opf is 

        items, ncx_href = {}, None
        for tag in re.findall(r"<item\b[^>]*>", opf):
            id = re.search(r'\bid="([^"]*)"', tag) #id of the item in the manifest,
            href = re.search(r'\bhref="([^"]*)"', tag) #pathto file
            mtype = re.search(r'media-type="([^"]*)"', tag) #media type
            if id and href:
                full = posixpath.normpath(posixpath.join(base, unquote(href.group(1))))
                items[id.group(1)] = full
                if mtype and mtype.group(1) == "application/x-dtbncx+xml":
                    ncx_href = full
        spine = [items[i] for i in re.findall(r'<itemref\b[^>]*idref="([^"]*)"', opf) if i in items] #to get in correct order

        #toc : table of contents , this is used to get the titles of each section in the epub so that they can be displayed in the reader
        toc = {}
        if ncx_href and ncx_href in names:
            ncx = z.read(ncx_href).decode("utf-8-sig")
            ncx_dir = posixpath.dirname(ncx_href)
            pattern = r"<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content[^>]*src=\"([^\"]+)\""
            for label, src in re.findall(pattern, ncx, re.S):
                target = posixpath.normpath(posixpath.join(ncx_dir, unquote(src.split("#")[0])))
                toc.setdefault(target, clean(label))
        
        #sections :  get the html of each section in the epub 
        sections = []
        for name in spine:
            if name not in names or not name.endswith((".xhtml", ".html", ".htm")):
                continue
            c = Cleaner()
            c.feed(z.read(name).decode("utf-8-sig", errors="replace"))
            c.close()
            body = tidy("".join(c.out))
            
            if name in toc or not sections or not toc:
                sections.append({"title": toc.get(name), "html": body})
            else:
                sections[-1]["html"] += "\n" + body


    result = []
    for i, s in enumerate(sections):
        if not re.sub(r"<[^>]+>", "", s["html"]).strip():
            continue
        title = s["title"]
        if not title:
            m = re.search(r"<h[1-3]>(.*?)</h[1-3]>", s["html"], re.S)
            title = clean(m.group(1)) if m else f"Part {i + 1}"
        #enleve dupe titles
        for m in re.finditer(r"<h[12]>(.*?)</h[12]>", s["html"], re.S):
            if clean(m.group(1)) == title:
                s["html"] = s["html"][:m.start()] + s["html"][m.end():]
                break
        result.append({"title": title, "html": s["html"].strip()})
    return result


def grab_block(s, label):
   #find the block of text that is labeled with the given label and return it as a string
    m = re.search(r"<p>\s*" + re.escape(label) + r"\s*</p>\s*<blockquote>", s)
    if not m:
        return ""
    start, depth = m.end(), 1
    for t in re.finditer(r"<(/?)blockquote>", s[start:]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return s[start:start + t.start()].strip()
    return ""


def label_notes(s):
    #label the notes section with a class so that it can be styled differently in the reader
    s = re.sub(r"(?:<p>\s*Chapter Notes\s*</p>\s*)?<div>\s*See the end of the chapter for (?:more )?notes\s*</div>", "", s)
    s = re.sub(
        r"<p>\s*(Chapter Summary|Chapter Notes|Chapter End Notes|End Notes|Notes|Summary)\s*</p>",
        r'<p class="label">\1</p>', s)
    return s


def write_book(path, entry):
    sections = read_sections(path)
    if not sections:
        return False

    #first section ("Preface") holds the summary author's notes and tag list
    #reader shows those at the top, so wepull them out of the story
    extras = {"summary_html": "", "notes_html": "", "info_html": ""}
    if sections[0]["title"].strip().lower() == "preface":
        preface = sections[0]["html"]
        extras["summary_html"] = grab_block(preface, "Summary")
        extras["notes_html"] = grab_block(preface, "Notes")
        dl = re.search(r"<dl>.*?</dl>", preface, re.S)
        extras["info_html"] = dl.group(0) if dl else ""
        if any(extras.values()):
            sections = sections[1:]

    for sec in sections:
        sec["html"] = label_notes(sec["html"])
    if not sections:
        return False

    BOOK_DIR.mkdir(exist_ok=True)
    data = {
        "title": entry["title"],
        "author": entry["author"],
        "ao3": entry["ao3"],
        "rating": entry["rating"],
        "status": entry["status"],
        "words": entry["words"],
        "tags": entry["tags"],
        "summary": entry["summary"],
        **extras,
        "chapters": sections,
    }
    (BOOK_DIR / f"{path.stem}.json").write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return True


#da main
existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
by_stem = {Path(f["files"][0]).stem: f for f in existing}

for p in sorted(FIC_DIR.iterdir()):
    if p.suffix.lower() not in (".pdf", ".epub"):
        continue
    path = p.as_posix()

    if p.stem in by_stem:
        entry = by_stem[p.stem]
        if path not in entry["files"]:
            entry["files"].append(path)
    else:
        entry = {
            "files": [path],
            "title": p.stem.replace("_", " ").replace("-", " ").strip(),
            "author": "", "rating": "", "status": "",
            "words": 0, "tags": [], "summary": "", "ao3": "",
        }
        existing.append(entry)
        by_stem[p.stem] = entry
        print("Added:", path)

    if p.suffix.lower() == ".epub":
        #fill in if missing 
        try:
            info = read_epub(p)
        except Exception as e:
            print("Could not read", p.name, "-", e)
            continue
        for key, value in info.items():
            if entry.get(key) in ("", 0, [], None):
                entry[key] = value
        if entry["title"] == p.stem.replace("_", " ").replace("-", " ").strip():
            entry["title"] = info.get("title", entry["title"])

        #build reader copy if missing
        try:
            if write_book(p, entry):
                entry["book"] = p.stem
                print("Reader copy:", p.stem)
        except Exception as e:
            print("Could not build reader copy for", p.name, "-", e)


OUT.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
print("Done.", len(existing), "fics in fics.json")