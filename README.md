# DSGN 312 — Packaging Design

Student site, generated from the course design folder.

**Live:** https://vanderpoolteacher.github.io/dsgn-312/

## Build

```bash
python3 build.py
```

Reads student-facing markdown from `03_courses/DSGN-312/` in the DSGN Program
folder and writes a static site into `docs/`, which GitHub Pages serves.

**The design folder is the source of truth.** Nothing in `docs/` is edited by
hand — run the build again after the markdown changes.

## What is published, and what is not

**Published:** each week's lesson, lab and assignment; the student files; the
glossary, competency rubric, project brief, project rubric and presentation
rubric.

**Withheld:** week outlines, slide decks, demos and instructor guides; the
course specification, outline, assessment evidence, module plans, time on task,
readings and viewings, the summative design, and the assessment instructor
guide.

Every link is resolved against the published set. A link to a withheld file is
unwrapped to plain text rather than left dangling.
