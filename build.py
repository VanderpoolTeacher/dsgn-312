#!/usr/bin/env python3
"""
Build the DSGN 312 student site.

Reads student-facing markdown from the DSGN 312 design folder and writes a
static HTML site into docs/, which GitHub Pages serves.

The design folder is the source of truth. Nothing in docs/ is edited by hand;
run this again after the markdown changes.

PUBLISHED
  02_source/week-NN/  01-lesson · 04-lab · 05-assignment
  01_design/          glossary · rubrics/competency-rubric
                      · assessments/sustainable-packaging-{student-brief,rubric,rubric-presentation}

WITHHELD
  02_source/week-NN/  00-week-outline · 02-slides · 02a-interactive-* · 03-demo
                      · 06-instructor-guide
  01_design/          course-specification · course-outline · assessment-evidence
                      · module-plans/ · readings-and-viewings
                      · assessments/sustainable-packaging-instructor-guide
                      · assessments/summative-assessment-…

  02a-interactive-* is a BUILD SPEC. It carries the answer key and the
  feedback for every wrong answer, and must never reach students.

Every link is resolved against the published set. A link to a withheld file is
unwrapped to plain text rather than left dangling, and internal tracking links
are dropped, so nothing in docs/ points at material students should not have.
"""

import datetime
import hashlib
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
DOCS = HERE / "docs"

DEFAULT_COURSE_DIR = (
    pathlib.Path.home()
    / "Library/CloudStorage/GoogleDrive-mvanderpool.edu@gmail.com/My Drive"
    / "_0 2026/DSGN Program/program-graphic-design/03_courses/DSGN-312"
)
COURSE_DIR = pathlib.Path(os.environ.get("DSGN312_COURSE", DEFAULT_COURSE_DIR)).resolve()
SRC = COURSE_DIR / "02_source"
DESIGN = COURSE_DIR / "01_design"

COURSE = "DSGN 312"
COURSE_LONG = "Packaging Design"

WEEK_DOCS = [
    ("01-lesson.md", "lesson", "Lesson"),
    ("04-lab.md", "lab", "Lab"),
    ("05-assignment.md", "assignment", "Assignment"),
    # DSGN 312 uses 05-assignment.md in all fifteen weeks and has no
    # checkpoints. The row below is kept deliberately: DSGN 205 lost six weeks'
    # deliverables by inheriting a config that omitted it, and a course that
    # later introduces checkpoints would lose them the same way. A missing file
    # is skipped harmlessly; a missing row is silent.
    ("05-checkpoint.md", "checkpoint", "Checkpoint"),
]

REFERENCE = [
    ("glossary.md", "reference/glossary.html", "Glossary",
     "Every term the course uses, by module."),
    ("rubrics/competency-rubric.md", "reference/competency-rubric.html", "Competency rubric",
     "The master rubric behind every assignment."),
    ("assessments/sustainable-packaging-student-brief.md", "reference/project-brief.html",
     "Project brief", "What the Sustainable Packaging System is and what it must contain."),
    ("assessments/sustainable-packaging-rubric.md", "reference/project-rubric.html",
     "Project rubric", "How components A to F are marked."),
    ("assessments/sustainable-packaging-rubric-presentation.md",
     "reference/presentation-rubric.html",
     "Presentation rubric", "How the account of the work is scored, separately from the work."),
]

LINK_RE = re.compile(r"\[([^\]\[]*)\]\(([^)\s]+)\)")
TRACKING_RE = re.compile(r"^\*\*Tracking card:\*\*.*$", re.M)
DROP_HOSTS = ("trello.com",)


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def first_h1(text):
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else None


SLIDE_SPLIT = "<!--SLIDE-->"

OPT_RE = re.compile(r"\b([A-D])\s*·\s*")


def inline_md(t):
    """Minimal inline markdown for feedback text. Italics are barred on slides."""
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"\1", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t


def parse_check(chunk, screen):
    """A single-answer check: options, the key, and per-option feedback.

    All three are authored in the slide spec. WATCH FOR is the instructor's
    note on the question and is never published.
    """
    if "[CHECK]" not in chunk:
        return None, screen
    key = re.search(r"\[CHECK\][^\n]*?\*\*([A-D])\*\*", chunk)
    fb = {k: v.strip() for k, _tick, v in
          re.findall(r"^\|\s*\*{0,2}([A-D])\*{0,2}\s*(✅)?\s*\|\s*(.+?)\s*\|$", chunk, re.M)}
    if not key or not fb:
        return None, screen

    opts, kept = [], []
    for line in screen.split("\n"):
        marks = list(OPT_RE.finditer(line))
        if marks and line.lstrip().startswith(tuple("ABCD")):
            for j, m in enumerate(marks):
                end = marks[j + 1].start() if j + 1 < len(marks) else len(line)
                opts.append((m.group(1), line[m.end():end].strip(" ·")))
        else:
            kept.append(line)
    if not opts:
        return None, screen
    return {"answer": key.group(1), "opts": opts, "fb": fb}, "\n".join(kept).strip()



def build_deck_dashed(wdir, wnum, wtitle):
    """The DSGN-family deck source.

    Slides are separated by `---`, each carrying one or more `##` headings, and
    the instructor's script sits in blockquotes. The source states the contract
    itself: "Speaker notes in blockquotes are not projected." Every line
    beginning with `>` is therefore dropped before anything is rendered — 203
    such lines across the three courses, none of them using lazy continuation,
    so the rule is complete rather than approximate.

    Two shapes share this parser. DSGN 312 and 412, and week 1 of 498, separate
    slides with `---`. From week 2 on, 498 uses one rule under the front matter
    and then numbered headings -- "## 1 · Title". Both are handled here.

    These decks carry no images, asset tags or embedded video, so there is no
    media column to build.
    """
    spec = wdir / "02-slides.md"
    if not spec.exists():
        return None
    raw = spec.read_text()
    parts = re.split(r"^---\s*$", raw, flags=re.M)
    if len(parts) >= 3:
        chunks = parts[1:]                       # parts[0] is the front matter
    else:
        # DSGN 498 from week 2 on: one rule under the front matter, then slides
        # delimited by numbered headings -- "## 1 · Title", "## 2 · ...".
        tail = parts[-1] if len(parts) > 1 else raw
        chunks = re.split(r"^(?=##\s+\d+\s*·)", tail, flags=re.M)
        chunks = [c for c in chunks if re.match(r"^##\s+\d+\s*·", c.strip())]
    if not chunks:
        return None

    titles, screens = [], []
    for chunk in chunks:
        kept = [l for l in chunk.split("\n") if not l.lstrip().startswith(">")]
        text = "\n".join(kept).strip()
        if not text:
            continue
        h = re.search(r"^#{1,6}\s+(.*)$", text, re.M)
        t = h.group(1).strip() if h else f"Slide {len(titles) + 1}"
        titles.append(re.sub(r"^\d+\s*·\s*", "", t))
        screens.append(text)
    if not screens:
        return None

    rendered = md_to_html(f"\n\n{SLIDE_SPLIT}\n\n".join(screens),
                          fmt="gfm+hard_line_breaks")
    rendered = re.sub(r"</?(?:em|i)>", "", rendered)   # slides are not italicised
    frags = rendered.split(SLIDE_SPLIT)

    # The eyebrow has its slide number stripped ("1 · Title" -> "Title"), so the
    # heading must be stripped the same way before comparing, or every numbered
    # slide renders its title twice.
    norm = lambda x: re.sub(r"[^a-z0-9]", "",
                            re.sub(r"^\s*\d+\s*·\s*", "", x).lower())
    cards = []
    for i, (title, frag) in enumerate(zip(titles, frags)):
        first = re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", frag, re.S)
        dup = first and norm(re.sub(r"<[^>]+>", "", first.group(1))) == norm(title)
        eyebrow = "" if dup else f'<h2>{html.escape(title)}</h2>'
        # The slide counter already numbers the slide, so "1 · Title" in the
        # heading numbers it twice.
        frag = re.sub(r"(<h[1-6][^>]*>)\s*\d+\s*·\s*", r"\1", frag, count=1)
        cards.append(f'<section class="slide">'
                     f'<div class="stext"><p class="sn">{i + 1} / {len(titles)}</p>'
                     f'{eyebrow}<div class="sbody">{frag}</div></div>'
                     f'</section>')
    return "\n".join(cards), len(titles)


def build_deck(wdir, wnum, wtitle, asset_files):
    """Student-visible slides: the ON SCREEN layer only.

    SAY, INTERACTION and WATCH FOR are the instructor's script and never leave
    the spec. Assets that do not exist yet are named, not faked.
    """
    spec = wdir / "02-slides.md"
    if not spec.exists():
        return None
    body = spec.read_text()
    chunks = re.split(r"^## Slide ", body, flags=re.M)[1:]
    if not chunks:
        # No "## Slide N" headings: this is the dashed DSGN format.
        return build_deck_dashed(wdir, wnum, wtitle)

    titles, screens, assets, checks = [], [], [], []
    for c in chunks:
        head = c.split("\n", 1)[0].strip()
        titles.append(re.sub(r"^\d+\s*·\s*", "", head))
        m = re.search(r"\*\*ON SCREEN\*\*\s*\n(.*?)"
                      r"(?=\n\*\*(?:ASSET|SAY|INTERACTION|WATCH FOR)\*\*|\n---)", c, re.S)
        raw = (m.group(1).strip() if m else "")
        # Screen copy is authored as a blockquote. An unquoted block is a stage
        # direction for the instructor ("Blank.", "Image A, full bleed") and is
        # not shown to students — except any heading in it, which is screen copy.
        keep = []
        for l in raw.split("\n"):
            if l.startswith("> "):
                keep.append(l[2:])
            elif l.strip() == ">":
                keep.append("")
            # anything unquoted is a stage direction and is dropped
        chk, body = parse_check(c, "\n".join(keep).strip())
        checks.append(chk)
        screens.append(body)

        tag = re.search(r"\*\*ASSET\*\*\s*·\s*`(\w+)`", c)
        # two formats in the specs: "image prompt **2.6**" and "2.6 in [course image prompts]"
        num = (re.search(r"image prompt \*\*(\d+)\.(\d+)\*\*", c)
               or re.search(r"\*\*ASSET\*\*[^\n]*?\b(\d+)\.(\d+)\b[^\n]*?course image prompts", c))
        alt = re.search(r"\|\s*\*\*Alt text\*\*\s*\|(.*?)\|", c)
        fn = None
        if num:
            want = f"{int(num.group(1)):02d}-{num.group(2)}-"
            fn = next((a for a in asset_files if a.startswith(want)), None)
        gen = bool(re.search(r"\*\(generated\)\*", c))
        vids = []
        for vm in re.finditer(r"youtube\.com/watch\?v=([A-Za-z0-9_-]{6,})", c):
            line = c[c.rfind("\n", 0, vm.start()) + 1: c.find("\n", vm.end())]
            lab = re.search(r"\*\*([^*]+)\*\*\s*·\s*([^·|]+)", line)
            vids.append((vm.group(1),
                         (lab.group(1).strip() if lab else "Listening"),
                         (lab.group(2).strip() if lab else "")))
        assets.append((tag.group(1) if tag else "NONE", fn,
                       (alt.group(1).strip() if alt else ""), gen, vids))

    rendered = md_to_html(f"\n\n{SLIDE_SPLIT}\n\n".join(screens),
                          fmt="gfm+hard_line_breaks")
    rendered = re.sub(r"</?(?:em|i)>", "", rendered)   # slides are not italicised
    html_chunks = rendered.split(SLIDE_SPLIT)
    cards = []
    for i, (title, frag) in enumerate(zip(titles, html_chunks)):
        tag, fn, alt, gen, vids = assets[i]
        media = ""
        for vid, vtitle, vby in vids:
            media += (f'<figure class="vid"><div class="vwrap"><iframe '
                      f'src="https://www.youtube-nocookie.com/embed/{html.escape(vid)}" '
                      f'title="{html.escape(vtitle)}" loading="lazy" allowfullscreen '
                      f'referrerpolicy="strict-origin-when-cross-origin"></iframe></div>'
                      f'<figcaption>{html.escape(vtitle)}'
                      + (f' &middot; {html.escape(vby)}' if vby else "")
                      + f' &middot; <a href="https://www.youtube.com/watch?v={html.escape(vid)}"'
                        f' target="_blank" rel="noopener">open on YouTube</a>'
                      + '</figcaption></figure>')
        if fn and not vids:
            cred = '<p class="cred">Generated image</p>' if gen else ""
            media = (f'<div class="sfig"><img src="../assets/{html.escape(fn)}" '
                     f'alt="{html.escape(alt)}" loading="lazy">{cred}</div>')
        elif tag in ("FIND", "SHOW", "MAKE", "GENERATE") and not vids:
            media = ('<p class="spend">Shown in class.</p>')
        norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
        first = re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", frag, re.S)
        dup = first and norm(re.sub(r"<[^>]+>", "", first.group(1))) == norm(title)
        eyebrow = "" if dup else f'<h2>{html.escape(title)}</h2>'
        chk = checks[i]
        quiz = ""
        if chk:
            btns = "".join(
                f'<button type="button" class="opt" data-k="{k}" '
                f'data-fb="{html.escape(inline_md(chk["fb"].get(k, "")), quote=True)}">'
                f'<b>{k}</b><span>{inline_md(t)}</span></button>'
                for k, t in chk["opts"])
            quiz = (f'<div class="check" data-answer="{chk["answer"]}">{btns}'
                    f'<p class="qfb" role="status" aria-live="polite"></p></div>')
        has_text = bool(re.sub(r"<[^>]+>", "", frag).strip()) or bool(quiz)
        split = " split" if (media and has_text) else ""
        cards.append(f'<section class="slide{split}">'
                     f'<div class="stext"><p class="sn">{i+1} / {len(titles)}</p>'
                     f'{eyebrow}<div class="sbody">{frag}</div>{quiz}</div>'
                     f'{media}</section>')
    return "\n".join(cards), len(titles)


def parse_assets():
    """The course diagrams, grouped by the week their number belongs to."""
    adir = SRC / "assets"
    if not adir.is_dir():
        return {}
    by_week = {}
    for f in sorted(adir.glob("*.svg")):   # photographs belong to slides and lessons
        m = re.match(r"^(\d{2})-", f.name)
        if not m:
            continue
        text = f.read_text()
        t = re.search(r"<title[^>]*>(.*?)</title>", text, re.S)
        d = re.search(r"<desc[^>]*>(.*?)</desc>", text, re.S)
        by_week.setdefault(int(m.group(1)), []).append((
            f.name,
            (t.group(1).strip() if t else f.stem),
            (d.group(1).strip() if d else ""),
        ))
    return by_week


def parse_review():
    """Per-week hours, and the standing expectation that applies to all of them."""
    doc = DESIGN / "readings-and-viewings.md"
    if not doc.exists():
        return {}, ""
    text = doc.read_text()

    hours = {}
    for w, rd, vw in re.findall(
            r"^\| (\d{1,2}) \| [^|]*\| [\d.]+ \| ([\d.—]+) \| ([\d.—]+) \| \*\*[\d.]+\*\* \|$",
            text, re.M):
        hours[int(w)] = (rd.strip(), vw.strip())

    standing = ""
    m = re.search(r"^## What students do — every week\s*(.*?)^Terms are in", text, re.M | re.S)
    if m:
        standing = "\n".join(
            l[2:] if l.startswith("> ") else ("" if l.strip() == ">" else l)
            for l in m.group(1).strip().split("\n")
            if not l.startswith("**This instruction")
        ).strip()
    return hours, standing


def collect():
    """Map every publishable source file to its output path in docs/."""
    pub = {}
    weeks = []

    for wdir in sorted(SRC.glob("week-*")):
        wnum = int(wdir.name.split("-")[1])
        items = []
        for fname, slug, label in WEEK_DOCS:
            f = wdir / fname
            if f.exists():
                out = f"{wdir.name}/{slug}.html"
                pub[f.resolve()] = out
                items.append((f, out, label, None))
        sfdir = wdir / "student-files"
        if sfdir.is_dir():
            for f in sorted(sfdir.glob("*.md")):
                out = f"{wdir.name}/{slugify(f.stem)}.html"
                pub[f.resolve()] = out
                items.append((f, out, "Sheet", None))
            for sub in sorted(p for p in sfdir.iterdir() if p.is_dir()):
                out = f"{wdir.name}/{sub.name}/index.html"
                pub[sub.resolve()] = out
                meta = {}
                mf = sub / "meta.json"
                if mf.exists():
                    meta = json.loads(mf.read_text())
                # a built artifact stands in for its withheld build spec, so the
                # lesson's existing link resolves to the thing instead of the spec
                if meta.get("spec"):
                    pub[(wdir / meta["spec"]).resolve()] = out
                items.append((sub, out, meta.get("label", "Starter files"),
                              meta.get("title", sub.name.replace("-", " "))))
        weeks.append((wdir, wnum, items))

    for rel, out, label, blurb in REFERENCE:
        f = DESIGN / rel
        if f.exists():
            pub[f.resolve()] = out

    return pub, weeks


def rewrite(text, src_file, out_path, pub):
    """Resolve links against the published set; unwrap anything withheld."""
    text = TRACKING_RE.sub("", text)
    srcdir = src_file.parent if src_file.is_file() else src_file
    outdir = pathlib.PurePosixPath(out_path).parent
    stats = {"kept": 0, "unwrapped": 0}

    def sub(m):
        if m.start() > 0 and text[m.start() - 1] == "!":
            return m.group(0)          # an image; ../assets/… resolves identically in the site
        label, target = m.group(1), m.group(2)
        if target.startswith("#"):
            return m.group(0)
        if target.startswith(("http://", "https://", "mailto:")):
            if any(h in target for h in DROP_HOSTS):
                stats["unwrapped"] += 1
                return label
            stats["kept"] += 1
            return m.group(0)
        try:
            resolved = (srcdir / target).resolve()
        except OSError:
            stats["unwrapped"] += 1
            return label
        dest = pub.get(resolved)
        if dest is None:
            stats["unwrapped"] += 1
            return label
        rel = os.path.relpath(dest, str(outdir)) if str(outdir) != "." else dest
        stats["kept"] += 1
        return f"[{label}]({rel})"

    return LINK_RE.sub(sub, text), stats


def md_to_html(text, fmt="gfm"):
    out = subprocess.run(
        ["pandoc", "-f", fmt, "-t", "html5", "--no-highlight"],
        input=text, capture_output=True, text=True, check=True,
    )
    # let wide tables scroll inside their own container
    return out.stdout.replace("<table>", '<div class="tbl"><table>').replace(
        "</table>", "</table></div>"
    )


STYLE_V = ""



# --- Week overview and To Do list -------------------------------------------
#
# Sourced from 00-week-outline.md, which is itself WITHHELD. Only three parts
# are lifted: the one-line framing, the outcomes table, and the assessment
# declaration. The rest of that file — what must exist before the week runs,
# why the assessment is where it is — stays unpublished.

PROSE_HEADINGS = ("The week in one line", "What this week is", "What this week does")
OUTCOME_HEADINGS = ("Lesson outcomes", "By the end of this week you can")

TODO_VERB = {
    "Lesson": "Read", "Slides": "Review", "Demo": "Watch", "Lab": "Do",
    "Assignment": "Submit", "Checkpoint": "Submit", "Interactive": "Practise",
    "Knowledge check": "Review", "Worksheet": "Print", "Files": "Download",
}


def _section(md, heading):
    m = re.search(r"^##\s+" + re.escape(heading) + r"\s*\n(.*?)(?=^##\s|^---\s*$)",
                  md, re.S | re.M)
    return m.group(1).strip() if m else ""


def _plain(t):
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    return re.sub(r"\*(.+?)\*", r"\1", t).strip()


def _inline(t):
    """Bold survives; links are flattened — they would point at withheld files."""
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = html.escape(t)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)


def outline_assessment(md):
    """Two shapes: '**Assessment:** text' and '**Assessment: text.** rationale'."""
    m = re.search(r"^\*\*Assessment:\*\*\s*(.+?)\s*$", md, re.M)
    if m:
        return _plain(m.group(1))
    m = re.search(r"^\*\*Assessment:\s*(.+?)\*\*", md, re.M)
    return _plain(m.group(1)) if m else ""


def outline_outcomes(md):
    body = ""
    for h in OUTCOME_HEADINGS:
        body = _section(md, h)
        if body:
            break
    rows = []
    for line in body.splitlines():
        m = re.match(r"^\|\s*\*\*([\d.]+)\*\*\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows.append((m.group(1), m.group(2)))
    return rows


def overview_html(outline_md):
    """Panel contents only — the tab strip supplies the heading."""
    prose = ""
    for h in PROSE_HEADINGS:
        prose = _section(outline_md, h)
        if prose:
            break
    paras = [x.strip() for x in prose.split("\n\n") if x.strip()
             and not x.strip().startswith("**Assessment")]

    out = []
    for x in paras[:3]:
        out.append(f'<p>{_inline(x)}</p>')

    los = outline_outcomes(outline_md)
    if los:
        out.append('<p class="lolead">By the end of this week you can</p><ul class="los">')
        for n, text in los:
            out.append(f'<li data-n="{html.escape(n)}">{_inline(text)}</li>')
        out.append('</ul>')

    a = outline_assessment(outline_md)
    if a:
        out.append(f'<p class="asmt"><span>Assessment</span>{html.escape(a)}</p>')
    return "".join(out)


def todo_html(wnum, cards, review_hours):
    """Panel contents only. Mirrors the page top to bottom: the cards in order,
    then review material, which is the last section on the page."""
    lis = []
    for href, label, title in cards:
        verb = TODO_VERB.get(label, "Open")
        lis.append(f'<li><span class="v">{verb}</span>'
                   f'<a href="{href}">{html.escape(title or label)}</a></li>')
    if review_hours:
        lis.append(f'<li><span class="v">Review</span>'
                   f'<span>Provided material &mdash; {review_hours} '
                   f'this week. Distributed in class.</span></li>')
    if not lis:
        return ""
    return (f'<p class="rvnote">To successfully complete Week {wnum}, '
            'please do the following:</p><ol>' + "".join(lis) + '</ol>')


def week_tabs(wnum, overview, todo):
    """Two tabs, one panel at a time.

    No panel is marked hidden in the markup, so with JS off both panels render
    stacked and nothing is lost. TABS_JS hides the inactive one on load.
    """
    if not overview and not todo:
        return ""
    tabs, panels, first = [], [], True
    for key, label, inner in (("ov", "Overview", overview),
                              ("td", "To Do List", todo)):
        if not inner:
            continue
        on = " is-on" if first else ""
        sel = "true" if first else "false"
        tabs.append(f'<button type="button" role="tab" class="tab{on}" '
                    f'id="t-{key}-{wnum}" aria-controls="p-{key}-{wnum}" '
                    f'aria-selected="{sel}" tabindex="{"0" if first else "-1"}">'
                    f'{label}</button>')
        panels.append(f'<div class="tabpanel" id="p-{key}-{wnum}" role="tabpanel" '
                      f'aria-labelledby="t-{key}-{wnum}">{inner}</div>')
        first = False
    return (f'<div class="tabs" data-tabs>'
            f'<div class="tabstrip" role="tablist" '
            f'aria-label="Week {wnum} summary">{"".join(tabs)}</div>'
            f'{"".join(panels)}</div>')


HOME_ICON = (
    '<svg class="hico" viewBox="0 0 24 24" width="17" height="17" fill="none" '
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true" focusable="false">'
    '<path d="M2.9 10.7 11.3 3.5a1.1 1.1 0 0 1 1.4 0l8.4 7.2"/>'
    '<path d="M5.4 9.1v10.3a1 1 0 0 0 1 1h11.2a1 1 0 0 0 1-1V9.1"/>'
    '<path d="M9.7 20.4v-5.1h4.6v5.1"/>'
    '</svg>'
)



# --- Chrome for copied interactive pages ------------------------------------
#
# Interactives are copied wholesale from student-files/ and carry their own
# inlined stylesheet, including their own copy of the palette. That copy drifts:
# it still held the pre-2026-08-20 --faint values that fail WCAG AA. This puts
# the header on them and re-syncs the tokens.
#
# Applied only to pages that already brand themselves "· <COURSE>" in the title.
# week-12/landing-page is a starter template the student publishes as their own
# work — it must NOT get course chrome.

CANON_TOKENS = [
    ("--soft:rgba(26,29,31,.66)",     "--soft:rgba(26,29,31,.82)"),
    ("--faint:rgba(26,29,31,.45)",    "--faint:rgba(26,29,31,.63)"),
    ("--soft:rgba(233,230,222,.66)",  "--soft:rgba(233,230,222,.80)"),
    ("--faint:rgba(233,230,222,.42)", "--faint:rgba(233,230,222,.52)"),
    ("--rule:rgba(26,29,31,.16); --accent",
     "--rule:rgba(26,29,31,.16); --edge:rgba(26,29,31,.49); --accent"),
    ("--rule:rgba(233,230,222,.16); --accent",
     "--rule:rgba(233,230,222,.16); --edge:rgba(233,230,222,.38); --accent"),
]

CHROME_CSS = """
.bar{border-bottom:1px solid var(--rule);background:var(--panel)}
.home{display:flex;align-items:baseline;gap:.55rem;max-width:80ch;margin:0 auto;
  padding:.9rem clamp(1rem,4vw,2rem);text-decoration:none;color:var(--ink)}
.home span{color:var(--faint);font-size:.9rem}
.hico{flex:none;align-self:center;color:var(--accent);transition:transform .12s}
.home:hover .hico{transform:translateY(-1px)}
.home:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:2px}
@media (prefers-reduced-motion:reduce){.hico{transition:none}.home:hover .hico{transform:none}}
@media(max-width:26rem){.home span{display:none}}
footer{border-top:1px solid var(--rule);margin-top:4rem;padding:1.6rem clamp(1rem,4vw,2rem) 3rem}
footer p{max-width:80ch;margin:0 auto;color:var(--faint);font-size:.85rem}
footer .fine{margin-top:.3rem}
"""


def add_chrome(path, depth):
    """Header + palette sync for a copied interactive. Returns True if applied."""
    t = path.read_text()
    if f"&middot; {COURSE}</title>" not in t and f"· {COURSE}</title>" not in t:
        return False          # not a course page — leave it alone
    if 'class="home"' in t:
        return False          # already chromed

    for old, new in CANON_TOKENS:
        t = t.replace(old, new)

    up = "../" * depth
    header = (f'<header class="bar">'
              f'<a class="home" href="{up}index.html">{HOME_ICON}'
              f'<b>{COURSE}</b> <span>{COURSE_LONG}</span></a>'
              f'</header>')
    footer = (f'<footer><p>{COURSE} &middot; {COURSE_LONG}</p>'
              f'<p class="fine">Student materials, generated from course source. '
              f'<a href="{up}reference/accessibility.html">Accessibility</a></p>'
              f'</footer>')
    t = t.replace("</style>", CHROME_CSS + "</style>", 1)
    t = t.replace("<body>", "<body>\n" + header, 1)
    t = t.replace("</body>", footer + "\n</body>", 1)
    path.write_text(t)
    return True



# --- Accessibility report -----------------------------------------------------
#
# Generated from the built site, every build. The point is that it cannot make a
# claim the pages do not support: the ratios are computed from the emitted
# style.css and the structural counts come from walking docs/. If something
# regresses, the published report says so rather than going quietly stale.

# Deliberately institution-neutral: this site is general content and is not
# tied to one college. Naming a real office here would be wrong in any other
# context, and inventing one would be worse, so the route is the instructor.


def _srgb(c):
    c /= 255
    return c/12.92 if c <= 0.03928 else ((c+0.055)/1.055) ** 2.4


def _lum(rgb):
    r, g, b = (_srgb(x) for x in rgb)
    return .2126*r + .7152*g + .0722*b


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + .05) / (lo + .05)


def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _over(fg, a, bg):
    return tuple(round(f*a + b*(1-a)) for f, b in zip(fg, bg))


def contrast_audit(css):
    """Ratios computed from the stylesheet actually shipped."""
    def tok(name, scope):
        m = re.search(r"--%s:(#[0-9A-Fa-f]{6}|rgba\([^)]*\))" % name, scope)
        return m.group(1) if m else None

    blocks = {}
    m = re.search(r":root\{(.*?)\}", css, re.S)
    blocks["light"] = m.group(1) if m else ""
    m = re.search(r"prefers-color-scheme:dark\).*?:root\{(.*?)\}", css, re.S)
    blocks["dark"] = m.group(1) if m else ""

    rows = []
    for theme, scope in blocks.items():
        if not scope:
            continue
        ground, panel = _hex(tok("ground", scope)), _hex(tok("panel", scope))
        ink = _hex(tok("ink", scope))
        for name, need, kind in (("ink", 4.5, "body text"),
                                 ("soft", 4.5, "secondary text"),
                                 ("faint", 4.5, "captions, labels"),
                                 ("accent", 4.5, "links and active tab"),
                                 ("edge", 3.0, "control boundaries")):
            v = tok(name, scope)
            if not v:
                continue
            if v.startswith("rgba"):
                nums = re.findall(r"[\d.]+", v)
                col_on = lambda bg: _over(tuple(int(x) for x in nums[:3]), float(nums[3]), bg)
            else:
                col_on = lambda bg, c=_hex(v): c
            for bgname, bg in (("ground", ground), ("panel", panel)):
                r = _ratio(col_on(bg), bg)
                rows.append((theme, name, kind, bgname, r, need, r >= need))
    return rows


def structure_audit(docs):
    pages = sorted(docs.rglob("*.html"))
    res = dict(pages=len(pages), lang=0, main=0, title=0, imgs=0, img_alt=0,
               links=0, link_named=0, buttons=0, button_named=0,
               h1_one=0, no_h1=[], multi_h1=[], no_main=[], dead=[])
    for f in pages:
        t = f.read_text()
        b = re.sub(r"<(script|style).*?</\1>", "", t, flags=re.S)
        if "<html lang=" in t: res["lang"] += 1
        if "<main>" in t: res["main"] += 1
        else: res["no_main"].append(str(f.relative_to(docs)))
        if re.search(r"<title>.+?</title>", t): res["title"] += 1
        n1 = len(re.findall(r"<h1[ >]", b))
        if n1 == 1: res["h1_one"] += 1
        elif n1 == 0: res["no_h1"].append(str(f.relative_to(docs)))
        else: res["multi_h1"].append((str(f.relative_to(docs)), n1))
        for img in re.findall(r"<img [^>]*>", b):
            res["imgs"] += 1
            if "alt=" in img: res["img_alt"] += 1
        for a in re.findall(r"<a [^>]*>(.*?)</a>", b, re.S):
            res["links"] += 1
            if re.sub(r"<[^>]+>", "", a).strip(): res["link_named"] += 1
        for bt in re.findall(r"<button[^>]*>(.*?)</button>", b, re.S):
            res["buttons"] += 1
            if re.sub(r"<[^>]+>", "", bt).strip(): res["button_named"] += 1
        for h in re.findall(r'href="([^"#?]+)"', t):
            if h.startswith(("http", "mailto:")):
                continue
            if not (f.parent / h).resolve().exists():
                res["dead"].append((str(f.relative_to(docs)), h))
    return res


def accessibility_html(css, docs, built_on):
    cr = contrast_audit(css)
    st = structure_audit(docs)
    fails = [r for r in cr if not r[6]]

    def row(r):
        theme, tokn, kind, bg, val, need, ok = r
        return (f'<tr><td>{theme}</td><td><code>--{tokn}</code></td><td>{kind}</td>'
                f'<td>on {bg}</td><td class="n">{val:.2f}:1</td>'
                f'<td class="n">{need:.1f}:1</td>'
                f'<td class="{"y" if ok else "n0"}">{"pass" if ok else "FAIL"}</td></tr>')

    # The verdict must account for EVERY table on this page, not just contrast.
    # An earlier draft said "every measured check passes" while the structure
    # table underneath it read 54/91 for heading structure. A statement that
    # contradicts its own evidence is worse than no statement.
    short = []
    if fails:
        short.append(f"{len(fails)} contrast check(s)")
    if st["dead"]:
        short.append(f"{len(st['dead'])} broken link(s)")
    h1_short = st["pages"] - st["h1_one"]
    if h1_short:
        short.append(f"heading structure on {h1_short} page(s)")
    main_short = st["pages"] - st["main"]
    if main_short:
        short.append(f"the main landmark on {main_short} page(s)")
    if short:
        verdict = ("<b>Contrast, naming and link integrity pass throughout.</b> "
                   "Not everything does: " + ", ".join(short) +
                   " fall short. Each is described under "
                   "<a href=\"#known\">Known limitations</a> below.")
    else:
        verdict = "<b>Every check on this page passes.</b>"

    known = []
    if st["no_h1"]:
        known.append(f'<li data-n="&#8226;"><b>{len(st["no_h1"])} pages have no level-1 heading.</b> '
                     f'Their source begins at a second-level heading. '
                     f'{", ".join("<code>%s</code>" % x for x in st["no_h1"])}</li>')
    if st["multi_h1"]:
        worst = max(st["multi_h1"], key=lambda x: x[1])
        known.append(f'<li data-n="&#8226;"><b>{len(st["multi_h1"])} pages carry more than one level-1 '
                     f'heading</b>, the slide decks among them &mdash; each slide is '
                     f'marked as its own heading, so <code>{worst[0]}</code> has '
                     f'{worst[1]}. Only one slide is visible at a time, but all of '
                     f'them are in the page for a screen reader.</li>')
    if st["no_main"]:
        known.append(f'<li data-n="&#8226;"><b>{len(st["no_main"])} page has no main landmark:</b> '
                     f'<code>{st["no_main"][0]}</code>. It is a starter template a '
                     f'student fills in and publishes as their own work, so it '
                     f'deliberately carries none of this site&rsquo;s structure.</li>')

    return f"""<p class="lede">This site holds the student materials for {COURSE}.
It is measured against <b>WCAG 2.2 Level AA</b> on every build, and this page is
generated from those measurements rather than written by hand.</p>

<h2 class="sec">What was measured</h2>
<p>{built_on}. <b>{st['pages']} pages</b> were checked. {verdict}</p>

<h2 class="sec">Contrast</h2>
<p class="rvnote">Ratios computed from the stylesheet this site ships, against both
surface colours, in both the light and dark themes. Text needs 4.5:1 (WCAG 1.4.3);
the boundaries of controls need 3:1 (WCAG 1.4.11).</p>
<div class="scroll"><table>
<thead><tr><th>Theme</th><th>Token</th><th>Used for</th><th>Against</th>
<th>Measured</th><th>Required</th><th>Result</th></tr></thead>
<tbody>{''.join(row(r) for r in cr)}</tbody></table></div>

<h2 class="sec">Structure and names</h2>
<div class="scroll"><table>
<thead><tr><th>Check</th><th>Criterion</th><th>Result</th></tr></thead><tbody>
<tr><td>Page language declared</td><td>3.1.1</td><td class="n">{st['lang']} / {st['pages']}</td></tr>
<tr><td>Page has a title</td><td>2.4.2</td><td class="n">{st['title']} / {st['pages']}</td></tr>
<tr><td>Main landmark present</td><td>1.3.1</td><td class="n">{st['main']} / {st['pages']}</td></tr>
<tr><td>Images carry alt text</td><td>1.1.1</td><td class="n">{st['img_alt']} / {st['imgs']}</td></tr>
<tr><td>Links have discernible text</td><td>2.4.4</td><td class="n">{st['link_named']} / {st['links']}</td></tr>
<tr><td>Buttons have an accessible name</td><td>4.1.2</td><td class="n">{st['button_named']} / {st['buttons']}</td></tr>
<tr><td>Exactly one level-1 heading</td><td>1.3.1</td><td class="n">{st['h1_one']} / {st['pages']}</td></tr>
<tr><td>Internal links resolve</td><td>&mdash;</td><td class="n">{len(st['dead'])} broken</td></tr>
</tbody></table></div>

<h2 class="sec">Keyboard and assistive technology</h2>
<ul class="los">
<li data-n="&#8226;">The week Overview / To Do control is a real tab list. Arrow keys,
Home and End move between tabs; only the selected tab takes a tab stop.</li>
<li data-n="&#8226;">Selected state is exposed with <code>aria-selected</code>, and each
tab is tied to its panel with <code>aria-controls</code>.</li>
<li data-n="&#8226;">With JavaScript off, no panel is hidden &mdash; both render in full,
so nothing is lost.</li>
<li data-n="&#8226;">Every interactive element takes a visible focus outline.</li>
<li data-n="&#8226;">Movement is limited to a one-pixel hover shift, and that is
disabled under <code>prefers-reduced-motion</code>.</li>
<li data-n="&#8226;">Text reflows to a single column and the page does not scroll
sideways; zoom is not disabled.</li>
</ul>

<h2 class="sec" id="known">Known limitations</h2>
<ul class="los">{''.join(known) if known else
 '<li data-n="&#8226;">None recorded at this build.</li>'}</ul>

<h2 class="sec">What is not covered</h2>
<p>These checks are automated. They do not establish that the writing is clear, that
video and audio carry captions or transcripts, or that the site works well with a
particular screen reader. Automated testing finds a minority of real barriers.
<b>If something here does not work for you, that is worth reporting even if this
page claims everything passes.</b></p>

<h2 class="sec">Reporting a barrier</h2>
<p><b>Tell your instructor.</b> You do not need to have registered accommodations,
and you do not need to know which guideline is at issue &mdash; describing what you
could not do is enough.</p>
<p>Accommodations themselves, and access to course materials such as readings,
video and equipment, are arranged through your institution&rsquo;s accessibility or
disability services office.</p>
"""



def link_sections(doc):
    """Give headings ids, then link table cells that name one.

    Deliberately conservative. The glossary alone has 98 bold first-column
    cells and almost all of them are terms, not sections — so a cell is linked
    only when its text is EXACTLY a heading on the same page, or exactly a
    heading minus a leading "Part ". Anything else is left as plain text.
    """
    heads = {}

    def tag(m):
        lvl, attrs, inner = m.group(1), m.group(2), m.group(3)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        if not text:
            return m.group(0)
        hid = slugify(text)
        if hid in heads.values():
            n = 2
            while f"{hid}-{n}" in heads.values():
                n += 1
            hid = f"{hid}-{n}"
        heads[text] = hid
        return f'<h{lvl}{attrs} id="{hid}">{inner}</h{lvl}>'

    doc = re.sub(r"<h([1-6])([^>]*)>(.*?)</h\1>", tag, doc, flags=re.S)

    lookup = {}
    for text, hid in heads.items():
        lookup[text] = hid
        if text.lower().startswith("part "):
            lookup[text[5:].strip()] = hid

    def cell(m):
        open_tag, inner, close = m.group(1), m.group(2), m.group(3)
        if "<a " in inner:
            return m.group(0)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        hid = lookup.get(text)
        if not hid:
            return m.group(0)
        return f'{open_tag}<a class="seclink" href="#{hid}">{inner}</a>{close}'

    return re.sub(r"(<t[dh][^>]*>)(.*?)(</t[dh]>)", cell, doc, flags=re.S)


def page(title, crumb, body, depth):
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} &middot; {COURSE}</title>
<link rel="stylesheet" href="{up}style.css?v={STYLE_V}">
</head>
<body>
<header class="bar">
  <a class="home" href="{up}index.html">{HOME_ICON}<b>{COURSE}</b> <span>{COURSE_LONG}</span></a>
</header>
<main>
{crumb}
{body}
</main>
<footer>
  <p>{COURSE} &middot; {COURSE_LONG}</p>
  <p class="fine">Student materials, generated from course source. <a href="{up}reference/accessibility.html">Accessibility</a></p>
</footer>
</body>
</html>
"""


def write(out_path, text):
    p = DOCS / out_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def main():
    if not SRC.is_dir():
        sys.exit(f"source not found: {SRC}\nSet DSGN312_COURSE to override.")

    if DOCS.exists():
        shutil.rmtree(DOCS)
    DOCS.mkdir(parents=True)
    (DOCS / ".nojekyll").write_text("")
    (DOCS / "style.css").write_text(STYLE)
    global STYLE_V
    STYLE_V = hashlib.md5(STYLE.encode()).hexdigest()[:8]

    pub, weeks = collect()
    hours, standing = parse_review()
    WEEK_TITLES = {}
    assets = parse_assets()
    asset_names = sorted(p.name for p in (SRC / 'assets').iterdir()
                     if p.suffix.lower() in ('.svg', '.jpg', '.jpeg', '.png')) if (SRC / 'assets').is_dir() else []
    adir = SRC / 'assets'
    if adir.is_dir():
        shutil.copytree(adir, DOCS / 'assets', dirs_exist_ok=True)
    standing_html = md_to_html(standing) if standing else ""
    kept = unwrapped = pages = 0
    chromed = []
    index_rows = []

    for wdir, wnum, items in weeks:
        lesson = wdir / "01-lesson.md"
        wtitle = f"Week {wnum}"
        if lesson.exists():
            h1 = first_h1(lesson.read_text()) or ""
            h1 = re.sub(r"\s*—\s*Lesson\s*$", "", h1)
            h1 = re.sub(r"^Week\s+\d+\s*·\s*", "", h1)
            if h1:
                wtitle = h1

        cards = []
        for f, out, label, override in items:
            if f.is_dir():
                shutil.copytree(f, DOCS / pathlib.PurePosixPath(out).parent,
                                dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("meta.json"))
                for ix in (DOCS / pathlib.PurePosixPath(out).parent).rglob("index.html"):
                    d = len(ix.relative_to(DOCS).parts) - 1
                    if add_chrome(ix, d):
                        chromed.append(str(ix.relative_to(DOCS)))
                cards.append((os.path.relpath(out, wdir.name), label, override))
                continue
            text = f.read_text()
            title = override or first_h1(text) or label
            text, st = rewrite(text, f, out, pub)
            kept += st["kept"]; unwrapped += st["unwrapped"]
            crumb = (
                f'<nav class="crumb"><a href="../index.html">All weeks</a>'
                f'<span>/</span><a href="index.html">Week {wnum}</a>'
                f'<span>/</span><em>{html.escape(label)}</em></nav>'
            )
            write(out, page(title, crumb, f'<article class="doc">{link_sections(md_to_html(text))}</article>', 1))
            cards.append((os.path.relpath(out, wdir.name), label, title))
            pages += 1

        deck = build_deck(wdir, wnum, wtitle, asset_names)
        if deck:
            slides_html, nslides = deck
            crumbd = (f'<nav class="crumb"><a href="../index.html">All weeks</a>'
                      f'<span>/</span><a href="index.html">Week {wnum}</a>'
                      f'<span>/</span><em>Slides</em></nav>')
            dbody = (f'<h1>Week {wnum} slides &middot; {html.escape(wtitle)}</h1>'
                     f'<p class="rvnote">What was on screen in class. '
                     f'Arrow keys or click to move.</p>'
                     f'<div class="deckwrap" id="deckwrap">'
                     f'<div class="deck" id="deck" tabindex="0">{slides_html}</div>'
                     f'<div class="dhud"><button type="button" id="prev">&larr; Back</button>'
                     f'<span id="dcount"></span>'
                     f'<span class="dright">'
                     f'<button type="button" id="fs">Full screen</button>'
                     f'<button type="button" id="next">Next &rarr;</button></span></div>'
                     f'</div>'
                     f'{DECK_JS}')
            write(f"{wdir.name}/slides.html", page(f"Week {wnum} slides", crumbd, dbody, 1))
            pages += 1
            after = next((n for n, c in enumerate(cards) if c[1] == "Lesson"), -1) + 1
            cards.insert(after, ("slides.html", "Slides", f"{nslides} slides from class"))

        lis = "\n".join(
            f'<li><a href="{h}"><span class="kind">{html.escape(k)}</span>'
            f'<span class="t">{html.escape(t)}</span></a></li>' for h, k, t in cards
        )
        crumb = (f'<nav class="crumb"><a href="../index.html">All weeks</a>'
                 f'<span>/</span><em>Week {wnum}</em></nav>')

        rd, vw = hours.get(wnum, (None, None))
        if rd:
            total = sum(float(v) for v in (rd, vw) if v and v[0].isdigit())
            rv = (f'<section class="review"><h2>Review before class'
                  f'<span class="hrs">{total:.2f} h</span></h2>'
                  f'<p class="rvnote">Material is provided in class. This is on top of the '
                  f'lesson, the lab and the assignment.</p>'
                  f'<div class="standing">{standing_html}'
                  f'<p class="gl">Terms are in the '
                  f'<a href="../reference/glossary.html">Glossary</a>, by module.</p>'
                  f'</div></section>')
        else:
            rv = ('<section class="review"><h2>Review before class</h2>'
                  '<p class="rvnote">No set review this phase — these weeks are production. '
                  'The reading time stated in the lesson still applies.</p></section>')

        figs = assets.get(wnum, [])
        dia = ""
        if figs:
            items = "".join(
                f'<figure><img src="../assets/{html.escape(fn)}" alt="{html.escape(desc)}" '
                f'loading="lazy"><figcaption>{html.escape(cap)}</figcaption></figure>'
                for fn, cap, desc in figs)
            dia = (f'<section class="diagrams"><h2>Diagrams</h2>'
                   f'<p class="rvnote">From the lesson. Yours to keep &mdash; '
                   f'print them, mark them up.</p>{items}</section>')

        ol_path = wdir / "00-week-outline.md"
        ov = overview_html(ol_path.read_text()) if ol_path.exists() else ""
        hrs_label = f"{total:.2f} h" if rd else ""
        td = todo_html(wnum, cards, hrs_label)

        tabs = week_tabs(wnum, ov, td)
        body = (f'<h1>Week {wnum} &middot; {html.escape(wtitle)}</h1>'
                f'{tabs}'
                f'<ul class="cards">{lis}</ul>{dia}{rv}'
                f'{TABS_JS if tabs else ""}')
        write(f"{wdir.name}/index.html", page(f"Week {wnum}", crumb, body, 1))
        index_rows.append((wdir.name, wnum, wtitle, len(cards)))
        WEEK_TITLES[wnum] = wtitle
        pages += 1

    # review list — one page collecting every week's expectation
    if hours:
        # A reading list may cover more weeks than the course has built. DSGN 114
        # allocates fifteen weeks of review against seven week folders, which
        # produced eight links to pages that do not exist. Keep the row -- the
        # hours are real -- but only link a week that was actually built.
        def _wk_cell(w):
            title = html.escape(WEEK_TITLES.get(w, ""))
            if (DOCS / f"week-{w:02d}" / "index.html").exists():
                return f'<td><a href="../week-{w:02d}/index.html">{title}</a></td>'
            return f'<td>{title}</td>'

        trs = "".join(
            f'<tr><td class="n">{w}</td>'
            f'{_wk_cell(w)}'
            f'<td class="n">{sum(float(v) for v in hours[w] if v and v[0].isdigit()):.2f} h</td></tr>'
            for w in sorted(hours))
        tot = sum(sum(float(v) for v in hours[w] if v and v[0].isdigit()) for w in hours)
        body = (
            '<h1>Review list<span class="sub">What to read, watch and listen to, week by week</span></h1>'
            '<p class="lede">Material is provided in class. This is on top of the lesson, the lab and '
            'the assignment. Weeks 12&ndash;15 set no review &mdash; those weeks are production.</p>'
            f'<div class="standing">{standing_html}'
            '<p class="gl">Terms are in the <a href="glossary.html">Glossary</a>, by module.</p></div>'
            '<h2 class="sec">Hours by week</h2>'
            f'<div class="scroll"><table class="rvt"><thead><tr><th>Week</th><th>Topic</th>'
            f'<th>Review</th></tr></thead><tbody>{trs}'
            f'<tr><td></td><td><b>Total</b></td><td class="n"><b>{tot:.2f} h</b></td></tr>'
            '</tbody></table></div>')
        crumb = ('<nav class="crumb"><a href="../index.html">Course home</a>'
                 '<span>/</span><em>Review list</em></nav>')
        write("reference/review-list.html", page("Review list", crumb, body, 1))
        pages += 1

    # reference documents
    ref_rows = [("reference/review-list.html", "Review list",
                 "Every week's reading and viewing, and what to do with it.")] if hours else []
    for rel, out, label, blurb in REFERENCE:
        f = DESIGN / rel
        if not f.exists():
            print(f"  ! missing reference doc: {rel}")
            continue
        text = f.read_text()
        title = first_h1(text) or label
        text, st = rewrite(text, f, out, pub)
        kept += st["kept"]; unwrapped += st["unwrapped"]
        crumb = ('<nav class="crumb"><a href="../index.html">Course home</a>'
                 f'<span>/</span><em>{html.escape(label)}</em></nav>')
        write(out, page(title, crumb, f'<article class="doc">{link_sections(md_to_html(text))}</article>', 1))
        ref_rows.append((out, label, blurb))
        pages += 1

    wk = "\n".join(
        f'<li><a href="{s}/index.html"><span class="wk">{n:02d}</span>'
        f'<span class="t">{html.escape(t)}</span>'
        f'<span class="c">{c} item{"s" if c != 1 else ""}</span></a></li>'
        for s, n, t, c in index_rows
    )
    rf = "\n".join(
        f'<li><a href="{o}"><span class="t">{html.escape(l)}</span>'
        f'<span class="c">{html.escape(b)}</span></a></li>' for o, l, b in ref_rows
    )
    body = (
        f'<h1>{COURSE}<span class="sub">{COURSE_LONG}</span></h1>'
        f'<p class="lede">Everything to read, do and hand in — week by week.</p>'
        f'<h2 class="sec">Reference</h2><ul class="cards ref">{rf}</ul>'
        f'<h2 class="sec">The fifteen weeks</h2><ul class="weeks">{wk}</ul>'
    )
    write("index.html", page(COURSE, "", body, 0))
    pages += 1

    # Last, so it can audit everything else. Written twice: the first pass gives
    # the page something to be, the second re-audits with it present so the page
    # count it reports includes itself and is not quietly one short.
    built_on = "Last checked " + datetime.date.today().strftime("%-d %B %Y")
    crumb_a = ('<nav class="crumb"><a href="../index.html">Course home</a>'
               '<span>/</span><em>Accessibility</em></nav>')
    for _ in range(2):
        css_now = (DOCS / "style.css").read_text()
        write("reference/accessibility.html",
              page("Accessibility",
                   crumb_a,
                   "<h1>Accessibility</h1>"
                   + accessibility_html(css_now, DOCS, built_on),
                   1))
    pages += 1

    print(f"pages          : {pages}")
    print(f"weeks          : {len(index_rows)}")
    print(f"reference docs : {len(ref_rows)}")
    print(f"links kept     : {kept}")
    print(f"links unwrapped: {unwrapped}  (targets not published)")
    if chromed:
        print(f"chromed        : {len(chromed)}  {', '.join(chromed)}")
    print(f"source         : {COURSE_DIR}")



TABS_JS = """
<script>
(function(){
  var w = document.querySelector('[data-tabs]');
  if(!w) return;
  var tabs = [].slice.call(w.querySelectorAll('[role=tab]')),
      panels = [].slice.call(w.querySelectorAll('[role=tabpanel]'));
  if(tabs.length < 2) return;
  function show(n){
    tabs.forEach(function(t,i){
      var on = i === n;
      t.classList.toggle('is-on', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      panels[i].hidden = !on;
    });
  }
  tabs.forEach(function(t,i){
    t.addEventListener('click', function(){ show(i); });
    t.addEventListener('keydown', function(e){
      var n = null;
      if(e.key === 'ArrowRight') n = (i+1) % tabs.length;
      if(e.key === 'ArrowLeft')  n = (i-1+tabs.length) % tabs.length;
      if(e.key === 'Home') n = 0;
      if(e.key === 'End')  n = tabs.length-1;
      if(n === null) return;
      e.preventDefault(); show(n); tabs[n].focus();
    });
  });
  show(0);   // panels are unhidden in the markup, so JS-off shows both
})();
</script>
"""


DECK_JS = """
<script>
(function(){
  var d=document.getElementById('deck');
  if(!d) return;
  var s=[].slice.call(d.querySelectorAll('.slide')), i=0,
      c=document.getElementById('dcount');
  var pv=document.getElementById('prev'), nx=document.getElementById('next');
  function show(n){ i=Math.max(0,Math.min(s.length-1,n));
    s.forEach(function(el,k){el.classList.toggle('on',k===i);});
    c.textContent=(i+1)+' / '+s.length;
    pv.disabled=(i===0); nx.disabled=(i===s.length-1); }
  document.addEventListener('keydown',function(e){
    if(e.key==='ArrowRight'||e.key===' '){show(i+1);e.preventDefault();}
    else if(e.key==='ArrowLeft'){show(i-1);e.preventDefault();}
    else if(e.key==='Home'){show(0);e.preventDefault();}
    else if(e.key==='End'){show(s.length-1);e.preventDefault();}});
  d.addEventListener('click',function(){ if(i<s.length-1) show(i+1); });
  document.getElementById('next').addEventListener('click',function(e){e.stopPropagation();show(i+1);});
  document.getElementById('prev').addEventListener('click',function(e){e.stopPropagation();show(i-1);});
  var wrap=document.getElementById('deckwrap'), fsb=document.getElementById('fs');
  function inFS(){return document.fullscreenElement||document.webkitFullscreenElement;}
  function paint(on){ wrap.classList.toggle('presenting',on);
    document.body.classList.toggle('presenting',on);
    fsb.textContent=on?'Exit full screen':'Full screen';
    if(on) d.focus(); }
  fsb.addEventListener('click',function(e){ e.stopPropagation();
    var on=!wrap.classList.contains('presenting');
    paint(on);
    try{ if(on){ (wrap.requestFullscreen||wrap.webkitRequestFullscreen||function(){}).call(wrap); }
         else if(inFS()){ (document.exitFullscreen||document.webkitExitFullscreen).call(document); } }
    catch(err){}
  });
  ['fullscreenchange','webkitfullscreenchange'].forEach(function(ev){
    document.addEventListener(ev,function(){ if(!inFS()) paint(false); });});
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape'&&wrap.classList.contains('presenting')) paint(false);});
  d.addEventListener('click',function(e){
    var b=e.target.closest('.opt'); if(!b) return;
    e.stopPropagation();
    var box=b.closest('.check'), fb=box.querySelector('.qfb'),
        ok=b.dataset.k===box.dataset.answer;
    box.querySelectorAll('.opt').forEach(function(o){o.classList.remove('picked');});
    b.classList.add('picked');
    box.classList.toggle('right',ok); box.classList.toggle('wrong',!ok);
    var t=b.dataset.fb.replace(/^(Right|Yes|Correct)[.,]?\\s*/i,'');
    fb.innerHTML='<b>'+(ok?'Yes.':'Not this one.')+'</b> '+t;
  },true);
  var x=null;
  d.addEventListener('touchstart',function(e){x=e.changedTouches[0].clientX;},{passive:true});
  d.addEventListener('touchend',function(e){ if(x===null)return;
    var dx=e.changedTouches[0].clientX-x; if(Math.abs(dx)>45) show(dx<0?i+1:i-1); x=null;},{passive:true});
  show(0);
})();
</script>
"""

STYLE = """
:root{
  --ground:#E9E6DE; --panel:#F2F0EA; --ink:#1A1D1F;
  --soft:rgba(26,29,31,.82); --faint:rgba(26,29,31,.63);
  --rule:rgba(26,29,31,.16); --edge:rgba(26,29,31,.49);
  --accent:#2F5D50; --alert:#7A3B52;
}
@media (prefers-color-scheme:dark){
  :root{
    --ground:#15191A; --panel:#1D2223; --ink:#E9E6DE;
    --soft:rgba(233,230,222,.80); --faint:rgba(233,230,222,.52);
    --rule:rgba(233,230,222,.16); --edge:rgba(233,230,222,.38);
    --accent:#7FB3A0; --alert:#C98BA0;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font:16px/1.62 'Helvetica Neue',Helvetica,Arial,system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;
}
a{color:var(--accent)}
a:focus-visible{outline:3px solid var(--accent);outline-offset:2px}

.bar{border-bottom:1px solid var(--rule);background:var(--panel)}
.home{display:flex;align-items:baseline;gap:.55rem;max-width:80ch;margin:0 auto;
  padding:.9rem clamp(1rem,4vw,2rem);text-decoration:none;color:var(--ink)}
.home span{color:var(--faint);font-size:.9rem}
.hico{flex:none;align-self:center;color:var(--accent);transition:transform .12s}
.home:hover .hico{transform:translateY(-1px)}
.home:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:2px}
@media (prefers-reduced-motion:reduce){.hico{transition:none}.home:hover .hico{transform:none}}
@media(max-width:26rem){.home span{display:none}}

main{max-width:80ch;margin:0 auto;padding:clamp(1.4rem,5vw,3rem) clamp(1rem,4vw,2rem)}

.crumb{display:flex;flex-wrap:wrap;gap:.45rem;align-items:center;
  font-size:.82rem;color:var(--faint);margin-bottom:1.6rem}
.crumb a{color:var(--soft)}
.crumb em{font-style:normal;color:var(--ink)}

h1{font-size:clamp(1.8rem,5.5vw,2.7rem);line-height:1.12;letter-spacing:-.022em;
  margin:0 0 1.3rem;text-wrap:balance}
h1 .sub{display:block;font-size:.46em;font-weight:400;color:var(--soft);
  margin-top:.5rem;letter-spacing:0}
/* Week headings are a single line at one level: "Week 1 · Visual literacy".
   No lighter tail, and deliberately not nowrap -- a long topic must wrap on a
   phone rather than overflow. */
.lede{font-size:1.06rem;color:var(--soft);max-width:44ch;margin:0 0 2.4rem}
h2.sec{font-size:.8rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink);font-weight:600;margin:3rem 0 1rem;
  padding-top:1.3rem;border-top:1px solid var(--rule)}
/* Only drop the separation when the heading genuinely opens its container.
   :first-of-type fired on "Hours by week" and "Reference", which follow an h1
   and a lede, so both lost their top margin while sitting mid-page. */
h2.sec:first-child{margin-top:0;padding-top:0;border-top:0}

ul.weeks,ul.cards{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.5rem}
ul.weeks a,ul.cards a{display:flex;gap:1rem;align-items:baseline;padding:.9rem 1.1rem;
  background:var(--panel);border:1px solid var(--edge);text-decoration:none;color:var(--ink)}
ul.weeks a:hover,ul.cards a:hover{border-color:var(--accent);color:var(--accent)}
ul.weeks a:focus-visible,ul.cards a:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.wk{font-variant-numeric:tabular-nums;color:var(--accent);font-weight:700;min-width:2ch}
.t{flex:1;min-width:0}
.c{color:var(--faint);font-size:.85rem}
ul.weeks .c{white-space:nowrap}
.kind{color:var(--accent);font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;min-width:9ch}

.review{margin-top:2.4rem;border-top:1px solid var(--rule);padding-top:1.4rem}
.review h2{display:flex;flex-wrap:wrap;gap:.6rem;align-items:baseline;
  font-size:.8rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink);font-weight:600;margin:0 0 .5rem}
.review .hrs{color:var(--accent);font-weight:700;letter-spacing:0;
  text-transform:none;font-size:1rem;font-variant-numeric:tabular-nums}
.rvnote{color:var(--faint);font-size:.86rem;margin:0 0 1rem;max-width:80ch}
.deck{border:1px solid var(--rule);background:var(--panel);
  min-height:22rem;display:flex;cursor:pointer}
.deck:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.deck .slide{display:none;width:100%;padding:clamp(1.2rem,4vw,2.4rem);flex-direction:column;gap:.5rem}
.deck .slide.on{display:flex}
.sn{font-size:.72rem;letter-spacing:.14em;color:var(--faint);margin:0;
  font-variant-numeric:tabular-nums}
.deck h2{font-size:1.05rem;margin:0;color:var(--accent);letter-spacing:-.005em}
.sbody{font-size:1.05rem}
.sbody h1{font-size:clamp(1.4rem,4vw,2rem);line-height:1.15;margin:.4rem 0 .6rem;letter-spacing:-.02em}
.sbody h2,.sbody h3{font-size:1.15rem;margin:.8rem 0 .4rem;color:var(--ink)}
.sbody p,.sbody li{max-width:56ch}
.sbody ul,.sbody ol{padding-left:1.2rem;margin:.4rem 0}
.sfig{margin-top:.8rem}
.sfig img{display:block;width:100%;max-width:34rem;height:auto;
  background:#E9E6DE;border:1px solid var(--rule)}
.check{display:flex;flex-direction:column;gap:.5rem;margin:1rem 0 0;width:100%}
.opt{display:flex;gap:.7rem;align-items:baseline;text-align:left;font:inherit;
  padding:.6rem .85rem;background:var(--ground);color:var(--ink);
  border:1px solid var(--rule);cursor:pointer;width:100%}
.opt:hover{border-color:var(--accent)}
.opt b{color:var(--accent);min-width:1.2em}
.opt.picked{border-color:var(--ink);border-width:2px}
.check.right .opt.picked{border-color:var(--accent);background:rgba(47,93,80,.10)}
.check.wrong .opt.picked{border-color:var(--alert);background:rgba(122,59,82,.10)}
.qfb{margin:.3rem 0 0;font-size:.95rem;line-height:1.5;color:var(--soft)}
.qfb:empty{display:none}
.check.right .qfb b{color:var(--accent)}
.check.wrong .qfb b{color:var(--alert)}
.deckwrap.presenting .check{width:min(74vw,1180px);margin:2vmin auto 0;gap:1vmin}
.deckwrap.presenting .opt{font-size:2.6vmin;padding:1.4vmin 2vmin}
.deckwrap.presenting .qfb{font-size:2.4vmin}
.vid{margin:1rem 0 0;width:100%}
.vwrap{position:relative;padding-top:56.25%;background:var(--panel);border:1px solid var(--rule)}
.vwrap iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
.vid figcaption{margin-top:.4rem;font-size:.86rem;color:var(--soft)}
.deckwrap.presenting .vid{width:min(74vw,1180px);margin:2vmin auto 0}
.deckwrap.presenting .vwrap{padding-top:min(56.25%,52vh)}
.deckwrap.presenting .vid figcaption{font-size:2vmin}
.cred{margin:.4rem 0 0;font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:var(--faint)}
.deckwrap.presenting .cred{font-size:1.5vmin}
.doc figcaption,.doc .cred{color:var(--faint);font-size:.85rem}
.spend{margin:.8rem 0 0;font-size:.88rem;color:var(--faint)}
.deckwrap{display:flex;flex-direction:column;gap:.8rem}
body.presenting{overflow:hidden}
.deckwrap.presenting{position:fixed;inset:0;z-index:9999;width:100vw;height:100vh;
  background:var(--ground);padding:3vmin 4vmin;gap:2vmin;margin:0}
.deckwrap.presenting .deck{flex:1;min-height:0;border:0;background:transparent;outline:none;
  display:flex;align-items:center;justify-content:center}
.deckwrap.presenting .slide{max-width:none}
.deckwrap.presenting .slide.on{width:100%;height:100%;justify-content:center;
  align-items:center;text-align:left;padding:2vmin 0;gap:1.5vmin;overflow:auto}
.deckwrap.presenting .sn{font-size:1.6vmin;letter-spacing:.3em}
.deckwrap.presenting .deck h2{font-size:2.4vmin}
.deckwrap.presenting .sbody{font-size:3.4vmin}
.deckwrap.presenting .sbody h1{font-size:7.5vmin;line-height:1.08;margin:.2em 0 .3em}
.deckwrap.presenting .sbody h2,.deckwrap.presenting .sbody h3{font-size:4vmin}
.deckwrap.presenting .stext,.deckwrap.presenting .sfig,
.deckwrap.presenting .vid,.deckwrap.presenting .spend{width:min(74vw,1180px);
  max-width:none;margin-left:auto;margin-right:auto;text-align:left}
/* text beside the picture, not stacked under it */
.deckwrap.presenting .slide.split.on{display:grid;grid-template-columns:1.02fr .98fr;
  gap:4vmin;align-items:center;align-content:center}
.deckwrap.presenting .split .stext,.deckwrap.presenting .split .sfig,
.deckwrap.presenting .split .vid{width:100%;margin:0}
.deckwrap.presenting .split .sbody{font-size:2.6vmin}
.deckwrap.presenting .split .sbody h1{font-size:5vmin}
.deckwrap.presenting .split .sfig img{max-height:70vh;max-width:100%}
@media (max-aspect-ratio:1/1){.deckwrap.presenting .slide.split.on{grid-template-columns:1fr}}
.deckwrap.presenting .sbody{text-align:left}
.deckwrap.presenting .sbody p,.deckwrap.presenting .sbody li{max-width:none;margin:.45em 0}
.deckwrap.presenting .sbody ul,.deckwrap.presenting .sbody ol{padding-left:1.3em;margin:.4em 0}
.deckwrap.presenting .sbody blockquote{margin:.5em 0;border:0;padding:0}
.deckwrap.presenting .sbody table{margin:.6em 0;width:100%}
.deckwrap.presenting .sbody th,.deckwrap.presenting .sbody td{padding:.45em .6em;font-size:.9em}
.deckwrap.presenting .sfig{margin-top:2vmin}
.deckwrap.presenting .sfig{max-width:none}
.deckwrap.presenting .sfig img{width:auto;height:auto;max-width:86vw;max-height:56vh;
  margin:0 auto;border:0;background:transparent}
.deckwrap.presenting .dhud{padding:0 1vmin}
.deckwrap.presenting .spend{font-size:2vmin}
.dright{display:flex;gap:.5rem}
.dhud{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-top:.8rem}
.dhud button{font:inherit;font-size:.9rem;padding:.45rem .9rem;background:var(--ground);
  color:var(--ink);border:1px solid var(--rule);cursor:pointer}
.dhud button:hover:not(:disabled){border-color:var(--accent)}
.dhud button:disabled{opacity:.35;cursor:default}
.dhud span{font-size:.85rem;color:var(--soft);font-variant-numeric:tabular-nums}


.tabs{margin:0 0 1.9rem}
.tabstrip{display:flex;gap:.35rem;border-bottom:1px solid var(--edge);margin-bottom:1.2rem}
.tab{appearance:none;background:none;border:0;border-bottom:2px solid transparent;
  margin-bottom:-1px;padding:.5rem .85rem;cursor:pointer;font:inherit;
  font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;
  color:var(--faint);transition:color .12s,border-color .12s}
.tab:hover{color:var(--ink)}
.tab.is-on{color:var(--accent);border-bottom-color:var(--accent)}
.tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}

.tabpanel p{font-size:.98rem;color:var(--soft);max-width:80ch;margin:0 0 .85rem}
.tabpanel strong{color:var(--ink)}
.tabpanel .lolead{font-size:.86rem;color:var(--faint);margin:1.1rem 0 .5rem}
ul.los{list-style:none;margin:0 0 1rem;padding:0;display:flex;flex-direction:column;gap:.42rem}
ul.los li{font-size:.93rem;color:var(--soft);max-width:80ch;padding-left:2.6rem;position:relative}
ul.los li::before{content:attr(data-n);position:absolute;left:0;top:.1em;
  font-size:.72rem;letter-spacing:.06em;color:var(--accent);font-variant-numeric:tabular-nums}
.asmt{border-left:3px solid var(--accent);padding-left:.9rem;font-size:.9rem;
  color:var(--soft);margin:0}
.asmt span{display:block;font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;
  color:var(--faint);margin-bottom:.15rem}
.tabpanel ol{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.5rem}
.tabpanel ol li{display:flex;gap:1rem;align-items:baseline;font-size:.94rem;color:var(--soft)}
.tabpanel ol li a{display:inline-flex;align-items:center;gap:.5rem;
  padding:.4rem .8rem;border:1px solid var(--edge);border-radius:2px;
  background:var(--panel);color:var(--ink);text-decoration:none;
  min-height:2.2rem;transition:border-color .12s,color .12s}
.tabpanel ol li a:hover{border-color:var(--accent);color:var(--accent)}
.tabpanel ol li a:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.tabpanel ol li>span:not(.v){padding:.4rem 0}
@media (prefers-reduced-motion:reduce){.tabpanel ol li a{transition:none}}
.tabpanel .v{color:var(--accent);font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;
  min-width:9ch;flex:none}
@media(max-width:34rem){
  .tabpanel ol li{flex-direction:column;gap:.15rem}
  .tab{padding:.5rem .6rem}
}

.diagrams{margin-top:2.4rem;border-top:1px solid var(--rule);padding-top:1.4rem}
.diagrams h2{font-size:.8rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink);font-weight:600;margin:0 0 .5rem}
.diagrams figure{margin:0 0 1.2rem}
.diagrams img{display:block;width:100%;max-width:100%;height:auto;
  background:#E9E6DE;border:1px solid var(--rule)}
.diagrams figcaption{margin-top:.45rem;font-size:.88rem;color:var(--soft)}

.standing{border-left:3px solid var(--accent);padding-left:1.1rem;margin-top:1rem}
.standing blockquote{margin:0;padding:0;border:0}
.standing p{margin:0 0 .7rem;font-size:.95rem;color:var(--soft);max-width:80ch}
.standing p:last-child{margin-bottom:0}
.standing .gl{margin-top:.9rem;font-size:.9rem}
.standing strong{color:var(--ink)}

.doc{font-size:1.02rem}
.doc h1{font-size:clamp(1.7rem,5vw,2.4rem)}
.doc h2{font-size:1.35rem;line-height:1.25;margin:2.4rem 0 .8rem;letter-spacing:-.012em}
.doc h3{font-size:1.1rem;margin:1.8rem 0 .6rem}
.doc p,.doc li{max-width:80ch}
.doc h1,.doc h2,.doc h3{scroll-margin-top:1.5rem}
.seclink{color:inherit;text-decoration:none;border-bottom:1px solid var(--edge)}
.seclink:hover{color:var(--accent);border-bottom-color:var(--accent)}
.seclink:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.doc ul,.doc ol{padding-left:1.3rem}
.doc li{margin:.35rem 0}
.doc hr{border:0;border-top:1px solid var(--rule);margin:2.2rem 0}
.doc blockquote{margin:1.4rem 0;padding:.2rem 0 .2rem 1.1rem;
  border-left:3px solid var(--accent);color:var(--soft)}
.doc code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.9em;
  background:var(--panel);padding:.1em .35em;border:1px solid var(--rule)}
.doc pre{background:var(--panel);border:1px solid var(--rule);padding:1rem;overflow-x:auto}
.doc pre code{background:none;border:0;padding:0}
.tbl{overflow-x:auto;margin:1.4rem 0}
.doc table{border-collapse:collapse;width:100%;min-width:28rem;font-size:.94rem}
.doc th,.doc td{text-align:left;padding:.55em .7em;border-bottom:1px solid var(--rule);
  vertical-align:top}
.doc th{font-size:.8rem;letter-spacing:.05em;text-transform:uppercase;
  color:var(--faint);font-weight:400}
.doc img{max-width:100%;height:auto}

footer{border-top:1px solid var(--rule);margin-top:4rem;padding:1.6rem clamp(1rem,4vw,2rem) 3rem}
footer p{max-width:80ch;margin:0 auto;color:var(--faint);font-size:.85rem}
footer .fine{margin-top:.3rem}

@media (max-width:34rem){
  ul.cards a{flex-direction:column;gap:.25rem}
  .kind{min-width:0}
}
"""

if __name__ == "__main__":
    main()
