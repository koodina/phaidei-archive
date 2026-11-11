import json
import re
import zipfile
import html
import posixpath
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
import fitz

FIC_DIR = Path("fics")
BOOK_DIR = Path("books")
OUT = Path("fics.json")



def clean(text):
    #unescape html stuff n remove tags
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


#pdfs annoying ass 

BODY_LABELS = {"Summary", "Notes", "Chapter Summary", "Chapter Notes", "Chapter End Notes", "End Notes"}
INFO_LABELS = {"Rating", "Archive Warning", "Archive Warnings", "Category", "Fandom", "Fandoms",
               "Relationship", "Relationships", "Character", "Characters", "Additional Tags",
               "Language", "Series", "Collections", "Stats"}
END_PUNCT = re.compile(r"[.!?…”\"’)\]*—–:;]$")


def pdf_inline(block, links):
    #turn the lines of one text block into one string of html (keeps italics bold and links)
    segs = []  
    for line in block["lines"]:
        pieces = []
        for s in line["spans"]:
            if s["text"] == "":
                continue
            font = s["font"]
            italic = "Italic" in font or "Oblique" in font or bool(s["flags"] & 2)
            bold = "Bold" in font or bool(s["flags"] & 16)
            href = ""
            cx, cy = (s["bbox"][0] + s["bbox"][2]) / 2, (s["bbox"][1] + s["bbox"][3]) / 2
            for rect, uri in links:
                if rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1:
                    href = uri
                    break
            pieces.append([s["text"].replace("\n", " "), (italic, bold, href)])
        if not pieces:
            continue
        if segs:
            prev = "".join(t for t, _ in segs)
            #a hyphen at the end of a line is a real hyphen so join with no space
            if not re.search(r"[A-Za-z]-$", prev.rstrip()):
                if segs[-1][1] == pieces[0][1]:
                    segs[-1][0] = segs[-1][0].rstrip() + " "
                else:
                    segs.append([" ", (False, False, "")])
            else:
                segs[-1][0] = segs[-1][0].rstrip()
        segs.extend(pieces)

    out = []
    for text, (italic, bold, href) in segs:
        t = html.escape(text, quote=False)
        if not t.strip():
            out.append(t)
            continue
        lead, core, trail = re.match(r"(\s*)(.*?)(\s*)$", t, re.S).groups()
        if italic: core = f"<em>{core}</em>"
        if bold: core = f"<strong>{core}</strong>"
        if href:
            core = f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">{core}</a>'
        out.append(lead + core + trail)
    s = "".join(out)
    s = re.sub(r"</em>(\s*)<em>", r"\1", s)
    s = re.sub(r"</strong>(\s*)<strong>", r"\1", s)
    s = re.sub(r'</a>(\s*)<a href="[^"]*"[^>]*>', r"\1", s)
    return re.sub(r"[ \t\r\n]{2,}", " ", s).strip()



def parse_pdf(path):
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            raise RuntimeError("PDF support needs PyMuPDF. Run this once: pip3 install pymupdf")

    doc = fitz.open(path)


    
    #read metadata across multiple pages in case tags r long it finishes it n gets to summary
    fields, order = {}, []
    ao3 = ""
    cur = None
    story_start = None

    for pno, page in enumerate(doc):
        page_dict = page.get_text("dict", sort=True)

        for b in page_dict["blocks"]:
            for l in b.get("lines", []):
                text = re.sub(
                    r"\s+",
                    " ",
                    "".join(s["text"] for s in l["spans"])
                ).strip()

                if not text:
                    continue

                #summary heading marks the beginning of the story copy
                if text == "Summary":
                    story_start = pno
                    break

                #extract ao3 link url
                m = re.search(
                    r"https?://archiveofourown\.org/works/\d+",
                    text
                )
                if m:
                    ao3 = m.group(0).replace("http://", "https://")

                #labels detection
                label_match = re.match(r"^(.+?):\s*(.*)$", text)

                if (
                    label_match
                    and label_match.group(1).strip() in INFO_LABELS
                ):
                    cur = label_match.group(1).strip()
                    value = label_match.group(2).strip()

                    fields[cur] = value
                    order.append(cur)
                    continue

                #stats field can be wrapped across multiple lines, so we append to it
                if cur == "Stats":
                    if re.match(
                        r"^(Published|Updated|Completed|Words|Chapters):",
                        text
                    ):
                        fields[cur] += " " + text
                    continue

               
                if cur:
                    if fields[cur] and not re.search(r"[A-Za-z]-$", fields[cur]):
                        fields[cur] += " "
                    fields[cur] += text

            if story_start is not None:
                break

        if story_start is not None:
            break

    #fall back to first page if no summary heading found, but only if there are metadata fields present
    if story_start is None:
        story_start = 1 if "Rating" in fields else 0


    #text 
    sizes, items = {}, []
    for pno in range(story_start, len(doc)):
        page = doc[pno]
        H = page.rect.height
        links = [(fitz.Rect(l["from"]), l["uri"]) for l in page.get_links()
                 if str(l.get("uri", "")).startswith(("http://", "https://"))]
        page_items = []
        for b in page.get_text("dict", sort=True)["blocks"]:
            if b["type"] != 0:
                continue
            x0, y0, x1, y1 = b["bbox"]
            if y1 <= 34 or y0 >= H - 36:          # running header / footer
                continue
            spans = [s for l in b["lines"] for s in l["spans"] if s["text"].strip()]
            if not spans:
                continue
            plain = re.sub(r"\s+", " ", " ".join("".join(s["text"] for s in l["spans"]) for l in b["lines"])).strip()
            size = max(s["size"] for s in spans)
            for s in spans:
                sizes[round(s["size"], 1)] = sizes.get(round(s["size"], 1), 0) + len(s["text"])
            page_items.append({
                "kind": "p", "page": pno, "x0": x0, "y0": y0, "plain": plain, "size": size,
                "bold": all(("Bold" in s["font"] or s["flags"] & 16) for s in spans),
                "lines": len(b["lines"]), "html": pdf_inline(b, links),
            })
        #thin horizontal lines drawn between paragraphs are scene breaks
        for d in page.get_drawings():
            r = d["rect"]
            if r.height <= 3 and r.width >= 200 and r.x0 >= 30 and r.x1 <= 590 and d.get("fill") != (0.0, 0.0, 0.9333000183105469):
                page_items.append({"kind": "hr", "page": pno, "x0": r.x0, "y0": r.y0, "plain": "", "html": ""})
        page_items.sort(key=lambda i: i["y0"])
        for n, it in enumerate(page_items):
            it["first"] = n == 0
            it["last"] = n == len(page_items) - 1
        items.extend(page_items)
    body = max(sizes, key=sizes.get) if sizes else 12.8

    def is_heading(it):
        if it["kind"] != "p" or it["lines"] != 1:
            return False
        if re.match(r"^Chapter \d+", it["plain"]) and it["size"] >= body and it["plain"] not in BODY_LABELS:
            return True
        return it["bold"] and it["size"] >= body + 0.5

    is_pointer = lambda t: bool(re.match(r"^See the end of the (work|chapter) for (more )?notes", t))
    is_afterword = lambda t: t.startswith("Please drop by the Archive and comment")

    #walk through the blocks: header (summary / notes), then chapters
    author, summary, notes = "", [], []
    chapters, cur_ch = [], None
    state, region, quote = "header", None, None
    prev = None   #the paragraph we last added, in case it continues on the next page

    def close_quote():
        nonlocal quote
        if quote and cur_ch:
            cur_ch["parts"].append("</blockquote>")
        quote = None

    def new_chapter(title):
        nonlocal cur_ch, prev
        close_quote()
        cur_ch = {"title": title, "parts": []}
        chapters.append(cur_ch)
        prev = None

    def add_part(part, it=None, joinable=False):
        nonlocal prev
        cur_ch["parts"].append(part)
        prev = (it, len(cur_ch["parts"]) - 1) if joinable else None

    for it in items:
        t = it["plain"]
        if state == "header":
            if it["kind"] == "hr":
                continue
            if is_heading(it) and not re.match(r"^Chapter \d+", t):
                continue                                   #the work's title
            if t.startswith("by ") and not author and it["lines"] == 1:
                author = t[3:].strip()
                continue
            if t in ("Summary", "Notes"):
                region = t
                continue
            if is_pointer(t) or is_afterword(t):
                if is_pointer(t) and region:
                    state, region = "story", None          #the header is over, the story starts next
                continue
            if region == "Summary":
                summary.append(it["html"])
                continue
            if region == "Notes" and it["x0"] >= 75:
                notes.append(it["html"])
                continue
            state, region = "story", None                  #anything else is the story itself

        #story
        if is_afterword(t) or is_pointer(t):
            continue
        if is_heading(it):
            if cur_ch and not cur_ch["parts"]:
                cur_ch["title"] = t
            else:
                new_chapter(t)
            region = None
            continue
        if cur_ch is None:
            new_chapter("Chapter 1")
        if it["kind"] == "hr":
            close_quote()
            add_part("<hr>")
            continue
        if t in BODY_LABELS:
            close_quote()
            region = t
            add_part(f'<p class="label">{html.escape(t, quote=False)}</p>')
            continue
        if region in ("Chapter Summary", "Chapter Notes") and it["x0"] < 75:
            close_quote()
            region = None                                  #indented text is notes; flush-left text is the story
        if region:
            if not quote:
                cur_ch["parts"].append("<blockquote>")
                quote = True
            cur_ch["parts"].append(f"<p>{it['html']}</p>")
            continue
        close_quote()
        #a paragraph that carries on over a page break
        if (prev and it["first"] and prev[0]["last"] and prev[0]["page"] == it["page"] - 1
                and (not END_PUNCT.search(prev[0]["plain"]) or t[:1].islower())):
            before = cur_ch["parts"][prev[1]][:-4]          #without the closing </p>
            glue = "" if re.search(r"[A-Za-z]-$", prev[0]["plain"]) else " "
            cur_ch["parts"][prev[1]] = before + glue + it["html"] + "</p>"
            prev = (dict(it, plain=t), prev[1])
            continue
        add_part(f"<p>{it['html']}</p>", it, joinable=True)

    close_quote()
    sections = [{"title": ch["title"], "html": "".join(ch["parts"])} for ch in chapters]

    #put everything tght chakalaka
    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip()
    author = author or (meta.get("author") or "").strip()
    info = {}
    if title: info["title"] = title
    if author: info["author"] = author
    if ao3: info["ao3"] = ao3
    if "Rating" in fields: info["rating"] = fields["Rating"]
    tags = []
    for key in ("Archive Warning", "Archive Warnings", "Relationship", "Relationships", "Additional Tags"):
        tags += [x.strip() for x in fields.get(key, "").split(", ") if x.strip()]
    tags = [x for x in tags if x != "No Archive Warnings Apply"]
    if tags: info["tags"] = list(dict.fromkeys(tags))
    stats = fields.get("Stats", "")
    m = re.search(r"Words:\s*([\d,]+)", stats)
    if m: info["words"] = int(m.group(1).replace(",", ""))
    m = re.search(r"Chapters:\s*(\d+)/(\d+|\?)", stats)
    if m: info["status"] = "Complete" if m.group(1) == m.group(2) else "In Progress"
    flat = " ".join(re.sub(r"<[^>]+>", "", s) for s in summary)
    if flat: info["summary"] = html.unescape(flat)

    info_html = ""
    if fields:
        rows = []
        for key in order:
            val = fields[key]
            if key == "Stats":
                val = " · ".join(re.findall(r"[A-Z][a-z]+: [^ ]+", val)) or val
            rows.append(f"<dt>{html.escape(key)}:</dt><dd>{html.escape(val, quote=False)}</dd>")
        info_html = "<dl>" + "".join(rows) + "</dl>"

    return {
        "info": info,
        "summary_html": "".join(f"<p>{s}</p>" for s in summary),
        "notes_html": "".join(f"<p>{s}</p>" for s in notes),
        "info_html": info_html,
        "sections": [s for s in sections if re.sub(r"<[^>]+>", "", s["html"]).strip()],
    }


def write_book_pdf(path, entry, parsed):
    if not parsed["sections"]:
        return False
    BOOK_DIR.mkdir(exist_ok=True)
    data = {
        "title": entry["title"], "author": entry["author"], "ao3": entry["ao3"],
        "rating": entry["rating"], "status": entry["status"], "words": entry["words"],
        "tags": entry["tags"], "summary": entry["summary"],
        "summary_html": parsed["summary_html"], "notes_html": parsed["notes_html"],
        "info_html": parsed["info_html"], "chapters": parsed["sections"],
    }
    (BOOK_DIR / f"{path.stem}.json").write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return True




def update_metadata(entry, new_info):
    """Overwrite existing values when the new source has useful data."""
    for key, value in new_info.items():
        if value not in ("", 0, [], None):
            entry[key] = value



def remove_duplicate_fics(entries):
    """Keep one entry per AO3 work, merging source files."""
    unique = {}
    result = []

    for entry in entries:
        ao3_id = re.search(r"/works/(\d+)", entry.get("ao3", ""))

        if ao3_id:
            key = "ao3:" + ao3_id.group(1)
        else:
            key = "file:" + Path(entry["files"][0]).stem

        if key not in unique:
            unique[key] = entry
            result.append(entry)
        else:
            kept = unique[key]

            #merge file paths without duplicates
            kept["files"] = list(dict.fromkeys(
                kept.get("files", []) + entry.get("files", [])
            ))

            #update metadata, but don't overwrite the merged files
            for field, value in entry.items():
                if field in ("files", "book"):
                    continue
                if value not in ("", 0, [], None):
                    kept[field] = value

    return result


#da main
existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
by_stem = {Path(f["files"][0]).stem: f for f in existing}

epub_stems = {
    p.stem for p in FIC_DIR.iterdir()
    if p.suffix.lower() == ".epub"
}

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
        update_metadata(entry, info)

        if entry["title"] == p.stem.replace("_", " ").replace("-", " ").strip():
            entry["title"] = info.get("title", entry["title"])

        #build reader copy if missing
        try:
            if write_book(p, entry):
                entry["book"] = p.stem
                print("Reader copy:", p.stem)
        except Exception as e:
            print("Could not build reader copy for", p.name, "-", e)

    elif p.suffix.lower() == ".pdf":
        #pick EPUB when both formats exist with the same filename
        if p.stem in epub_stems:
            continue
        try:
            parsed = parse_pdf(p)
        except Exception as e:
            print("Could not read", p.name, "-", e)
            continue
        update_metadata(entry, parsed["info"])
        if entry["title"] == p.stem.replace("_", " ").replace("-", " ").strip():
            entry["title"] = parsed["info"].get("title", entry["title"])

        #build the on-site reading copy
        try:
            if write_book_pdf(p, entry, parsed):
                entry["book"] = p.stem
                print("Reader copy:", p.stem)
        except Exception as e:
            print("Could not build reader copy for", p.name, "-", e)

existing = remove_duplicate_fics(existing)

OUT.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
print("Done.", len(existing), "fics in fics.json")