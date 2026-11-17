"""Check the repository's Markdown entry pages.

For each page:

- every relative link points at a published file, spelled as it is on disk;
- every anchor matches a heading in its target;
- every figure quoted from a project README is still what that README says.

It also checks that the parts the project standard lists for every README are present
in every project README, in order, that every finished project under projects/ has a
row in the root README's table, and that the root README keeps its section order.

Run from anywhere, standard library only:

    python3 scripts/check_pages.py [page ...]

With no arguments every page in PAGES is checked. Exit code 1 on any problem.
"""

# The table of quoted figures reads better as one row per line.
# ruff: noqa: E501

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RFQ = "projects/rfq-to-quote/README.md"
AML = "projects/aml-alert-investigator/README.md"
REV = "projects/revenue-incident-agent/README.md"
TRIAGE = "projects/issue-triage-desk/README.md"
EDITS = "projects/edit-moderation-cascade/README.md"
PAGES = [
    "README.md",
    "docs/delivery-method.md",
    "docs/project-standard.md",
    RFQ,
    AML,
    REV,
    TRIAGE,
    EDITS,
]
STANDARD = "docs/project-standard.md"
# The root README's sections, in order.
ROOT_SECTIONS = [
    "Start here",
    "From request to measured system",
    "Projects",
    "Capabilities",
    "What every project includes",
    "Next",
    "Running a project",
    "License",
]
DIAGRAMS = ["request-to-system.png", "agent-flow.png", "agent-flowchart.png"]
# Parts of a README that are bold paragraphs, not headings, and the text that marks them.
UNNUMBERED = {"title and summary": None, "status": "**Status", "contents": "**Contents"}

# (page, wording on the page, source file, wording in the source that backs it)
FACTS = [
    ("README.md", "every line item right", RFQ, "got every line item right"),
    ("README.md", "a tenth of a US cent", RFQ, "about a tenth of a US cent per request"),
    ("README.md", "F1 71.7%", RFQ, "F1 71.7%"),
    ("README.md", "38 to 40 of 40", AML, "| Haiku in every role | 38 of 40 |"),
    ("README.md", "38 to 40 of 40", AML, "| Routed: Haiku triage, Opus lead | 40 of 40 |"),
    ("README.md", "22 for rules alone", AML, "| Rules only | 22 of 40 |"),
    ("README.md", "no suspicious alert closed", AML, "None closed a suspicious alert"),
    ("README.md", "nine repeat runs", AML, "no run closed a suspicious alert"),
    ("README.md", "706 series", REV, "detector scores 706 series"),
    ("README.md", "706 series", REV, "pair: 706 series"),
    ("README.md", "706 series", REV, "706 series every hour"),
    ("README.md", "14 of 14", REV, "finds 14 of 14 injected incidents"),
    ("README.md", "5 of 7", REV, "it finds 5 of 7"),
    ("README.md", "18 of 19", REV, "cause for 18 of the 19 incidents"),
    ("README.md", "the scripted investigator for 16", REV, "scripted investigator for 16"),
    ("README.md", "one made an unsafe proposal that passed every check", REV, "made an unsafe proposal that passed every check"),
    ("README.md", "412 to 419 of the 638", TRIAGE, "412 to 419 of the 638 issues that have one"),
    ("README.md", "347 for the classifier", TRIAGE, "against 347 for a classical classifier"),
    ("README.md", "206 for keyword rules", TRIAGE, "206 for keyword rules"),
    ("README.md", "did as well (443)", TRIAGE, "got the area right on 443"),
    ("README.md", "one in four labels", TRIAGE, "One in four labels the desk would apply on its own is not on the issue"),
    ("README.md", "14 of the 74 in the top 5% of the queue", EDITS, "puts 14 of the 74 in the top 5% of the queue"),
    ("README.md", "against 4 for arrival order", EDITS, "where arrival order puts 4"),
    ("README.md", "16 to 17 over three runs", EDITS, "16 to 17 over three runs"),
    ("README.md", "passes on only 28 of the 74", EDITS, "its slice holds 28 of the 74 bad edits"),
    ("README.md", "did best (23)", EDITS, "(23 in the top 5%)"),
    ("README.md", "US$0.16 per 1,000 edits", EDITS, "US$0.16 per 1,000 edits against the cascade's US$0.03"),
    ("README.md", "about four seconds after it was made", EDITS, "about 4 seconds after it was made"),
    ("docs/delivery-method.md", "14 of 14", REV, "finds 14 of 14 injected incidents"),
    ("docs/delivery-method.md", "5 of 7", REV, "it finds 5 of 7"),
    ("docs/delivery-method.md", "F1 71.7%", RFQ, "F1 71.7%"),
    (
        "docs/delivery-method.md",
        "F1 71.7%",
        "projects/rfq-to-quote/docs/eval-outside-data.md",
        "71.7%",
    ),
    ("README.md", "38 to 40 of 40", AML, "| Opus alone, low effort | 40 of 40 |"),
    ("README.md", "40 hand-labelled requests", RFQ, "on 40 hand-labelled requests"),
    ("README.md", "38 to 40", RFQ, "every line was right on 38 to 40 of the 40 requests"),
    (
        "README.md",
        "auto-approved a wrong cable size",
        RFQ,
        "one unsafe approval in 86 auto-approvals",
    ),
    ("docs/delivery-method.md", "the best of six", RFQ, "The published run is the best of six"),
    ("README.md", "harder set of 32 requests", RFQ, "On a harder set of 32 requests"),
    ("README.md", "74.2% and 78.2%", RFQ, "| F1 over the labelled pairs | 71.7% | 74.2% | 78.2% |"),
    ("README.md", "31.4% for search alone", RFQ, "71.7% against 31.4% for the top search hit"),
    ("README.md", "28 of the 40", AML, "28 of the 40 alerts were used to tune the prompts"),
    ("README.md", "did not beat a single model", AML, "it did not beat the simpler single model"),
    (
        "README.md",
        "never run against the live endpoint",
        AML,
        "This path has not been run against the live endpoint",
    ),
    ("README.md", "mock admin service", REV, "mock admin service"),
    ("README.md", "customers are fictional", REV, "The customer is fictional"),
    ("docs/delivery-method.md", "In RFQ-to-quote every rule runs", RFQ, "All rules always run"),
    (
        "docs/delivery-method.md",
        "In RFQ-to-quote, what the reviewer corrects",
        RFQ,
        "A correction is written out as a labelled eval case",
    ),
    (
        "docs/delivery-method.md",
        "unit-tested",
        AML,
        "unit tests cover the request and reply conversion",
    ),
    (
        "docs/delivery-method.md",
        "did not beat a single model",
        AML,
        "it did not beat the simpler single model",
    ),
    (
        "README.md",
        "a leak scan found no real identifier",
        AML,
        "the leak scan found no real identifier",
    ),
    (
        "README.md",
        "projects/rfq-to-quote/docs/request-to-system.png",
        RFQ,
        "(docs/request-to-system.png)",
    ),
    (
        "docs/delivery-method.md",
        "found no real identifier",
        AML,
        "the leak scan found no real identifier",
    ),
    (
        "docs/project-standard.md",
        "Headline results on small sets are given as counts",
        AML,
        "Read counts, not percentages",
    ),
    (
        "docs/project-standard.md",
        "where it was not, the README says so",
        AML,
        "Routing did not earn its complexity here",
    ),
]

# A fenced code block: up to three spaces, three or more backticks or tildes, and a
# closing fence of the same character that is at least as long.
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[`~]*[ \t]*$", re.M | re.S)
CODE_SPAN = re.compile(r"`[^`\n]+`")
# [text](target) and [text](target "title"); also matches the target of an image.
INLINE_LINK = re.compile(r"""\]\(\s*<?([^)\s>]+)>?(?:\s+(?:"[^"]*"|'[^']*'))?\s*\)""")
# [label]: target
REFERENCE = re.compile(r"^ {0,3}\[[^\]]+\]:[ \t]*<?([^\s>]+)>?", re.M)
HTML_LINK = re.compile(r"""\b(?:href|src)\s*=\s*["']([^"']+)["']""", re.I)
HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.M)
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def prose(text: str) -> str:
    """The text with fenced code blocks removed."""
    return FENCE.sub("", text)


def flat(text: str) -> str:
    """The text with every run of whitespace collapsed to one space."""
    return re.sub(r"\s+", " ", text)


def slug(heading: str) -> str:
    """The anchor GitHub generates for a heading."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip().lower())
    text = re.sub(r"[`*]", "", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors_in(text: str) -> set[str]:
    return {slug(heading) for heading in HEADING.findall(prose(text))}


def link_targets(text: str) -> list[str]:
    """Every link target written in the text, outside code."""
    text = CODE_SPAN.sub("", prose(text))
    return [
        *INLINE_LINK.findall(text),
        *REFERENCE.findall(text),
        *HTML_LINK.findall(text),
    ]


def exact_case(path: Path) -> bool:
    """True when every part of the path is spelled as it is on disk.

    macOS finds a file under a wrong-case name; GitHub does not.
    """
    try:
        parts = path.relative_to(ROOT).parts
    except ValueError:
        return True
    here = ROOT
    for part in parts:
        if part not in os.listdir(here):
            return False
        here = here / part
    return True


def ignored(path: Path) -> bool:
    """True when git ignores the path, so it will not be in the published repository."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", str(path)], capture_output=True
    )
    return result.returncode == 0


def link_problems(page: str, text: str) -> list[str]:
    """Problems with the links in the text of a page that sits at the given path."""
    problems = []
    source = ROOT / page
    for target in link_targets(text):
        if SCHEME.match(target):
            continue
        path_part, _, anchor = target.partition("#")
        if not path_part:
            if anchor and anchor not in anchors_in(text):
                problems.append(f"{page}: no heading matches the anchor: {target}")
            continue
        dest = Path(os.path.normpath(source.parent / path_part))
        if not dest.exists():
            problems.append(f"{page}: link target does not exist: {target}")
            continue
        if not exact_case(dest):
            problems.append(f"{page}: link target is spelled differently on disk: {target}")
            continue
        if ignored(dest):
            problems.append(
                f"{page}: link target is git-ignored and will not be published: {target}"
            )
            continue
        if not anchor:
            continue
        if dest.is_dir():
            problems.append(f"{page}: anchor on a folder link is ignored by GitHub: {target}")
        elif anchor not in anchors_in(read(dest)):
            problems.append(f"{page}: no heading matches the anchor: {target}")
    return problems


def check_links(page: str) -> list[str]:
    return link_problems(page, read(ROOT / page))


def check_facts(page: str) -> list[str]:
    problems = []
    page_text = flat(read(ROOT / page))
    for fact_page, on_page, source, in_source in FACTS:
        if fact_page != page:
            continue
        if on_page not in page_text:
            problems.append(f"{page}: expected wording is missing: {on_page!r}")
        if in_source not in flat(read(ROOT / source)):
            problems.append(f"{page}: {on_page!r} is no longer backed by {source}: {in_source!r}")
    return problems


def listed_sections() -> list[str]:
    """The parts the standard says every README has, in order, lower-cased."""
    text = prose(read(ROOT / STANDARD))
    if "## README order" not in text:
        return []
    section = text.split("## README order", 1)[1]
    section = re.split(r"^#{2,6} ", section, maxsplit=1, flags=re.M)[0]
    names = re.findall(r"^(?:[-*]|\d+\.)\s+\*\*(.+?)\*\*", section, re.M)
    return [name.rstrip(".").lower() for name in names]


def listed_projects() -> list[str]:
    """Folder names of the projects in the root README's table."""
    text = read(ROOT / "README.md")
    if "## Projects" not in text:
        return []
    table = re.split(r"^## ", text.split("## Projects", 1)[1], maxsplit=1, flags=re.M)[0]
    return sorted(re.findall(r"^\| \[[^\]]+\]\(projects/([^/)]+)/\)", table, re.M))


def looks_finished(readme_text: str) -> bool:
    """True when a project README has a status paragraph and its last standard section."""
    headings = [heading.lower() for heading in HEADING.findall(readme_text)]
    return "**Status" in readme_text and any("limits and next steps" in h for h in headings)


def work_outstanding(folder: Path) -> bool:
    """True while a project is unfinished: no complete README, or a hand-over file.

    A build that stops before its recorded model run leaves a HANDOVER.md naming what
    is left. The project gets its row in the root README when that file is gone.
    """
    readme = folder / "README.md"
    if not readme.is_file() or not looks_finished(read(readme)):
        return True
    return (folder / "HANDOVER.md").exists()


def check_standard() -> list[str]:
    """What the standard says of every README must be true of every README."""
    names = listed_sections()
    if not names:
        return [f"{STANDARD}: no list of sections found under 'README order'"]
    problems = []
    for project in listed_projects():
        readme = f"projects/{project}/README.md"
        text = prose(read(ROOT / readme))
        headings = [heading.lower() for heading in HEADING.findall(text)]
        position = 0
        for name in names:
            if name in UNNUMBERED:
                marker = UNNUMBERED[name]
                if marker and marker not in text:
                    problems.append(
                        f"{STANDARD}: '{name}' is listed for every README; {readme} has none"
                    )
                continue
            found = next((i for i in range(position, len(headings)) if name in headings[i]), None)
            if found is None:
                problems.append(
                    f"{STANDARD}: '{name}' is listed for every README; "
                    f"{readme} has no such heading at that point"
                )
            else:
                position = found + 1
        for diagram in DIAGRAMS:
            if f"(docs/{diagram})" not in text:
                problems.append(f"{STANDARD}: {readme} does not show docs/{diagram}")
    return problems


def check_project_list() -> list[str]:
    """Every finished project has a row in the table; planned work carries no link."""
    text = read(ROOT / "README.md")
    problems = []
    if "## Next" in text:
        planned = re.split(r"^## ", text.split("## Next", 1)[1], maxsplit=1, flags=re.M)[0]
        if "](" in planned:
            problems.append("README.md: the Next list must carry no links")
    if "## Projects" not in text:
        return [*problems, "README.md: no Projects section"]
    listed = listed_projects()
    for folder in sorted(p for p in (ROOT / "projects").iterdir() if p.is_dir()):
        if folder.name in listed:
            continue
        if work_outstanding(folder):
            print(f"note: projects/{folder.name} is not in the project table (work outstanding)")
        else:
            problems.append(f"README.md: projects/{folder.name} is finished and has no row")
    return problems


def check_root_readme() -> list[str]:
    """The root README keeps its section order and names each project one way."""
    text = read(ROOT / "README.md")
    problems = []
    sections = re.findall(r"^## (.+?)[ \t]*$", prose(text), re.M)
    if sections != ROOT_SECTIONS:
        problems.append(f"README.md: sections are {sections}, expected {ROOT_SECTIONS}")
    if "## Projects" in text and "## Capabilities" in text:
        table = re.split(r"^## ", text.split("## Projects", 1)[1], maxsplit=1, flags=re.M)[0]
        names = re.findall(r"^\| \[([^\]]+)\]\(projects/", table, re.M)
        matrix = re.split(r"^## ", text.split("## Capabilities", 1)[1], maxsplit=1, flags=re.M)[0]
        header = next((line for line in matrix.splitlines() if line.startswith("|")), "")
        columns = [cell.strip() for cell in header.strip("|").split("|")][1:]
        if names != columns:
            problems.append(
                f"README.md: project table names {names}, capability matrix names {columns}"
            )
    return problems


def main(argv: list[str]) -> int:
    pages = argv or PAGES
    problems = []
    for page in pages:
        if not (ROOT / page).is_file():
            problems.append(f"{page}: page does not exist")
            continue
        problems += check_links(page)
        problems += check_facts(page)
        if page == STANDARD:
            problems += check_standard()
        if page == "README.md":
            problems += check_project_list()
            problems += check_root_readme()
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"OK: {len(pages)} page(s) checked")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
