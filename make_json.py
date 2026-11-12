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


        metadata_found = False
        for name in sorted(z.namelist()):
            if not name.endswith((".xhtml", ".html")):
                continue

            page = z.read(name).decode("utf-8", errors="replace")

            # Find the original AO3 link on ANY EPUB page
            if not info.get("ao3"):
                link = re.search(
                    r"https?://(?:www\.)?archiveofourown\.org/works/\d+",
                    page,
                    re.I
                )
                if link:
                    info["ao3"] = link.group(0).replace("http://", "https://")

            # Only metadata extraction requires the tags page
            if metadata_found:
                continue

            if 'class="tags"' not in page and 'class="calibre3"' not in page:
                continue

            metadata_found = True

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
                done = chapters.group(1) == chapters.group(2)
                info["status"] = "Complete" if done else "In Progress"


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
    is_bold = lambda s: "Bold" in s["font"] or bool(s["flags"] & 16)

    # ---- the AO3 info table (Rating, Fandom, Tags, Stats...). It can run over more than one page. ----
    fields, order = {}, []
    ao3, cur, story_start, last_info_page = "", None, None, -1
    for pno, page in enumerate(doc):
        done = False
        for b in page.get_text("dict", sort=True)["blocks"]:
            if b["type"] != 0:
                continue
            spans = [s for l in b["lines"] for s in l["spans"] if s["text"].strip()]
            if not spans:
                continue
            # the big bold title of the next page means the info table is over
            if fields and pno > last_info_page and all(is_bold(s) for s in spans) and max(s["size"] for s in spans) >= 16:
                done = True
                break
            for l in b["lines"]:
                text = re.sub(r"\s+", " ", "".join(s["text"] for s in l["spans"])).strip()
                if not text:
                    continue
                if text.rstrip(":") in ("Summary", "Notes"):
                    done = True
                    break
                m = re.search(r"https?://archiveofourown\.org/works/\d+", text)
                if m and not ao3:
                    ao3 = m.group(0).replace("http://", "https://")
                label_match = re.match(r"^(.+?):\s*(.*)$", text)
                if label_match and label_match.group(1).strip() in INFO_LABELS:
                    cur = label_match.group(1).strip()
                    fields[cur] = label_match.group(2).strip()
                    order.append(cur)
                    last_info_page = pno
                    continue
                if cur == "Stats":
                    fields[cur] += " " + text
                    continue
                if cur and pno <= last_info_page + 1:
                    if fields[cur] and not re.search(r"[A-Za-z]-$", fields[cur]):
                        fields[cur] += " "
                    fields[cur] += text
            if done:
                break
        if done:
            story_start = pno
            break
    if story_start is None:
        story_start = last_info_page + 1 if "Rating" in fields else 0

    # ---- every text block after that, in reading order ----
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
                "kind": "p", "page": pno, "H": H, "x0": x0, "y0": y0, "y1": y1, "plain": plain, "size": size,
                "bold": all(is_bold(s) for s in spans),
                "lines": len(b["lines"]), "html": pdf_inline(b, links),
            })
        # thin horizontal lines drawn between paragraphs are scene breaks
        for d in page.get_drawings():
            r = d["rect"]
            if r.height <= 3 and r.width >= 200 and r.x0 >= 30 and r.x1 <= 590 and d.get("fill") != (0.0, 0.0, 0.9333000183105469):
                page_items.append({"kind": "hr", "page": pno, "H": H, "x0": r.x0, "y0": r.y0, "y1": r.y1, "plain": "", "html": ""})
        page_items.sort(key=lambda i: i["y0"])
        for n, it in enumerate(page_items):
            it["first"] = n == 0
            it["last"] = n == len(page_items) - 1
            it["gap"] = it["y0"] - page_items[n - 1]["y1"] if n else None
        items.extend(page_items)
    body = max(sizes, key=sizes.get) if sizes else 12.8

    # AO3 starts a new page for each part (info / title+summary / story / end notes), so a page that
    # ends early followed by a new page means one part is over. Big gaps inside a page mean the same.
    for n, it in enumerate(items):
        it["early_break"] = bool(n and it["first"] and items[n - 1]["page"] == it["page"] - 1
                                 and items[n - 1]["y1"] < it["H"] - 150)
    gaps = sorted(it["gap"] for it in items if it["gap"] is not None and 3 < it["gap"] < 30)
    big_gap = (gaps[len(gaps) // 2] if gaps else 14) + 9

    def is_heading(it):
        if it["kind"] != "p" or it["lines"] != 1:
            return False
        if re.match(r"^Chapter \d+", it["plain"]) and it["size"] >= body and it["plain"] not in BODY_LABELS:
            return True
        return it["bold"] and it["size"] >= body + 0.5

    is_pointer = lambda t: bool(re.match(r"^See the end of the (work|chapter) for (more )?notes", t))
    is_afterword = lambda t: t.startswith("Please drop by the Archive and comment")
    label_of = lambda t: (t[:-1] if t.endswith(":") else t)

    def walk(header_mode):
        author, summary, notes = "", [], []
        chapters, cur_ch = [], None
        state = "header" if header_mode else "story"
        reg = {"name": None, "chars": 0, "blocks": 0, "label": None}
        quote, prev = False, None

        def region_over(it):
            # notes and summaries have length limits on AO3 (about 1250 / 5000 characters)
            if not reg["name"] or reg["blocks"] == 0:
                return False
            cap = 1300 if reg["name"].endswith("Summary") else 5200
            if reg["chars"] + len(it["plain"]) > cap:
                return True
            if (it["gap"] is not None and it["gap"] >= big_gap
            and reg["name"] != "Summary"):
              return True
            return it["early_break"]

        def reset_region():
            reg.update(name=None, chars=0, blocks=0, label=None)

        def close_quote():
            nonlocal quote
            if quote and cur_ch:
                cur_ch["parts"].append("</blockquote>")
            quote = False

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
            lab = label_of(t)


            # A real chapter heading marks the end of the work's header.
            if (state == "header" and is_heading(it)
                    and re.match(r"^Chapter \d+\b", t)):
                state = "story"
                reset_region()

            if state == "header":
                if it["kind"] == "hr":
                    continue
                if is_heading(it) and not re.match(r"^Chapter \d+", t):
                    continue                                   # the work's title
                if t.startswith("by ") and not author and it["lines"] == 1:
                    author = t[3:].strip()
                    continue
                if lab in ("Summary", "Notes"):
                    reset_region()
                    reg["name"] = lab
                    continue
                if is_pointer(t):
                    # "...for more notes" means the notes are still to come; plain "...for notes" means the header is over
                    if not ("more notes" in t and reg["blocks"] == 0):
                        state = "story"
                        reset_region()
                    continue
                
                if reg["name"] in ("Summary", "Notes") and not region_over(it):
                    (summary if reg["name"] == "Summary" else notes).append(it["html"])
                    reg["chars"] += len(t)
                    reg["blocks"] += 1
                    continue
                state = "story"                                # first block after the header = the story
                reset_region()

            # ---- story ----
            
            if is_pointer(t):
                if reg["name"] and not ("more notes" in t and reg["blocks"] == 0):
                    if reg["blocks"] == 0 and reg["label"] is not None and cur_ch and cur_ch["parts"] and cur_ch["parts"][-1] == reg["label"]:
                        cur_ch["parts"].pop()                  # a label with nothing under it
                    close_quote()
                    reset_region()
                continue
            if is_heading(it):
                if cur_ch and not cur_ch["parts"]:
                    cur_ch["title"] = t
                else:
                    new_chapter(t)
                reset_region()
                continue
            if cur_ch is None:
                new_chapter("Chapter 1")
            if it["kind"] == "hr":
                close_quote()
                reset_region()
                add_part("<hr>")
                continue
            if lab in BODY_LABELS:
                close_quote()
                reset_region()
                label_html = f'<p class="label">{html.escape(lab, quote=False)}</p>'
                reg["name"], reg["label"] = lab, label_html
                add_part(label_html)
                continue
            if reg["name"] and region_over(it):
                close_quote()
                reset_region()
            if reg["name"]:
                if not quote:
                    cur_ch["parts"].append("<blockquote>")
                    quote = True
                cur_ch["parts"].append(f"<p>{it['html']}</p>")
                reg["chars"] += len(t)
                reg["blocks"] += 1
                continue
            close_quote()
            # a paragraph that carries on over a page break
            continues_afterword = (
                prev
                and is_afterword(prev[0]["plain"])
                and prev[0]["page"] == it["page"]
                and t[:1].islower()
            )

            continues_page = (
                prev
                and it["first"]
                and prev[0]["last"]
                and prev[0]["page"] == it["page"] - 1
                and (not END_PUNCT.search(prev[0]["plain"]) or t[:1].islower())
            )

            if continues_afterword or continues_page:
                before = cur_ch["parts"][prev[1]][:-4]
                glue = "" if re.search(r"[A-Za-z]-$", prev[0]["plain"]) else " "
                cur_ch["parts"][prev[1]] = before + glue + it["html"] + "</p>"
                prev = (dict(it, plain=t), prev[1])
                continue

            add_part(f"<p>{it['html']}</p>", it, joinable=True)

        close_quote()
        sections = [{"title": ch["title"], "html": "".join(ch["parts"])} for ch in chapters]
        sections = [s for s in sections if re.sub(r"<[^>]+>", "", s["html"]).strip()]
        return author, summary, notes, sections

    author, summary, notes, sections = walk(True)
    if not sections:
        # the header logic swallowed everything, so read the whole thing as story instead of failing
        author2, summary, notes, sections = walk(False)
        author = author or author2

    # ---- put the info together ----
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

    stats = re.sub(r"\s+", " ", fields.get("Stats", "")).strip()

    # Extract word count
    m = re.search(r"Words:\s*([\d,]+)", stats)
    if m:
        info["words"] = int(m.group(1).replace(",", ""))

    # Extract completion status
    m = re.search(r"Chapters:\s*(\d+)\s*/\s*(\d+|\?)", stats)

    if m:
        current = int(m.group(1))
        total = m.group(2)

        if total == "?":
            info["status"] = "In Progress"
        else:
            info["status"] = "Complete" if current == int(total) else "In Progress"


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
        "sections": sections,
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
            #keep the reader copy if this duplicate has one and the kept entry doesn't
            if not kept.get("book") and entry.get("book"):
                kept["book"] = entry["book"]
                
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