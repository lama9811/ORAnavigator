"""Read a WHOLE solicitation and return its requirements, each one quoted.

This is the module the feature turns on: everything downstream — what the draft
reviewer checks, what the PI is told they are missing — is only as good as what
comes out of here.

WHY THIS IS NOT ONE GEMINI CALL
-------------------------------
Two measured failure modes, both silent:

1. INPUT. solicitation_extractor.extract_from_text caps its prompt at 250k
   characters, and that module's own system prompt notes that the load-bearing
   facts appear well into the document — so a long FOA loses precisely the part
   that matters. Here the document is CHUNKED instead, so every character is
   read, and whatever could not be read is reported rather than assumed empty.

2. EXTRACTION. One pass drops requirements even with the whole text in the
   prompt. Measured on a real federal solicitation: gemini-3.6-flash returned 3
   required attachments on one run and 5 on the next — identical input,
   temperature 0. (The full measurement is recorded in CLAUDE.md.)
   So we chunk, union, then SWEEP: hand the model what we already have and ask
   what is missing, with a DIFFERENT job description. Asking the same prompt
   twice returns the same list; asking a second reader "what did the first
   reader miss?" is what finds the tail.

3. AGREEMENT. The sweep converging — a round finds nothing new — means the two
   readers AGREE, not that the document is exhausted. Measured on a real
   federal solicitation:
   both readers ran to convergence, no cap hit, and four requirements from the
   human-verified list were still missing, including an outright PROHIBITION
   ("voluntary committed cost sharing is prohibited") that the system prompt
   names in so many words. Running more rounds does not find them — that was
   measured too. A THIRD reader with a different TARGET does: see
   _TARGETED_SYSTEM. Recall over the same document went 20/24 -> 23/24, and the
   twenty-fourth is a matcher artifact rather than a miss.

Every row must quote the solicitation verbatim (golden rule 2); rows whose quote
cannot be found are dropped and COUNTED, never hidden. The model can widen the
requirement list, but it can never invent an ask that is not in the document.

BOUNDED, AND IT SAYS SO. Cloud Run kills a request at 300s and the backend is
single-worker, so this carries a wall-clock budget and a round cap. Both are
reported (`hit_time_cap` / `hit_round_cap`): stopping silently would read as
"we found everything" when it means "we stopped looking" — the same class of
lie as the truncation above.

Cost is ~10-18 model calls per solicitation. Affordable because it runs ONCE
when the solicitation is attached and the result is stored — never per review.
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from services import gemini_client
from services.text_match import normalize, quote_in

# gemini-3.6-flash is region-locked to the "global" endpoint — it 404s in
# us-central1 — so the model and its location are one env pair and move together.
MODEL = os.getenv("SOLICITATION_REQUIREMENTS_MODEL", "gemini-3.6-flash")
MODEL_LOCATION = os.getenv("SOLICITATION_REQUIREMENTS_LOCATION", "global")

# Well inside the model's window, so a chunk is never the binding constraint.
CHUNK_CHARS = 60_000
CHUNK_OVERLAP = 4_000
MAX_WORKERS = 4
# ~half the 300s Cloud Run request cap, leaving room for the PDF read and the
# response. A 100-page FOA that would otherwise run long stops and says so.
DEFAULT_BUDGET_S = 150.0

_SYSTEM = (
    "You extract, for a university research office, EVERY requirement a grant "
    "solicitation places on an applicant's proposal. You output DATA ONLY from the "
    "text given to you.\n\n"

    "WHAT COUNTS AS A REQUIREMENT\n"
    "A requirement is anything the applicant must INCLUDE in, DO for, or ADDRESS in "
    "their proposal: content, structure, format, limits, prohibitions, and conditions "
    "the proposal itself must demonstrate.\n"
    "Signal words, none of which may be skimmed past: must, shall, should, is/are "
    "required, will, is expected to, needs to, include, provide, describe, address, "
    "submit, attach, specify, identify, demonstrate, no more than, at least, may not, "
    "is prohibited, is not allowed, limited to.\n"
    "Requirements ALSO appear with none of those words — as bare imperative headings, "
    "and as the items of a list introduced by something like 'The Project Description "
    "must contain the following:'. Every item of such a list is its own requirement.\n\n"

    "COMPLETENESS IS THE POINT OF THIS TASK\n"
    "1. Read the text you are given from its FIRST line to its LAST. Do not stop "
    "early, do not sample, do not summarise. A partial answer here tells a faculty "
    "member their proposal is complete when it is not.\n"
    "2. There is no maximum. If the text holds forty requirements, return forty. Never "
    "trim the list for brevity, or because the items feel repetitive.\n"
    "3. Read the parts that are easy to skip: bulleted and numbered lists, tables, "
    "figure and table captions, footnotes, endnotes, appendices, boxed notes, "
    "parenthetical asides, and text under headings that look administrative.\n"
    "4. SPLIT, never merge. 'Include a timeline and a description of experimental "
    "methods' is TWO requirements. One row per distinct ask, even when several share "
    "one sentence.\n"
    "5. If the same ask is restated later with MORE specificity, return the specific "
    "statement as its own row and quote that statement.\n"
    "6. When the text imposes a requirement by pointing elsewhere ('prepared in "
    "accordance with PAPPG Chapter II.D.2'), return the pointer itself as a "
    "requirement, quoted. Never invent what the referenced document says.\n"
    "7. If a passage genuinely contains no requirement, return an empty array. Never "
    "manufacture one to appear thorough.\n\n"

    "QUOTING — ENFORCED IN CODE, NOT A STYLE NOTE\n"
    "8. 'source' MUST be a VERBATIM quote from the text given, copied character for "
    "character, <=300 characters. Do not paraphrase, do not tidy it, do not correct "
    "typos, do not join distant sentences. Every row is checked against the document "
    "and a row whose quote is not found there is DISCARDED — so an imperfect copy "
    "destroys a real requirement.\n\n"

    "FIELDS\n"
    "9. 'section' — the part of the APPLICANT'S PROPOSAL the requirement belongs to, "
    "snake_case: project_summary, project_description, research_strategy, "
    "budget_justification, data_management_plan, letters_of_collaboration, "
    "biosketch, references_cited, and so on. Name it as this solicitation names "
    "that part of a proposal — never translate one funder's vocabulary into "
    "another's.\n"
    "   This is NOT the solicitation's own document structure. 'Proposal Preparation "
    "and Submission Instructions', 'Award Information', 'Section V.A' and the like are "
    "headings in the SOLICITATION; they are not parts of a proposal and must never be "
    "used as a section. If a requirement is about the proposal as a whole, or you "
    "cannot tell which part it belongs to, use null.\n"
    "10. 'scored' — false ONLY where the text marks the ask conditional or optional "
    "('if applicable', 'if available', 'where appropriate', 'optional', 'may'). "
    "Otherwise true. Return conditional rows; never drop them.\n"
    "11. 'label' — a short imperative name (<=80 chars) for what the applicant must "
    "do. 'why' — one sentence on why a reviewer cares. 'keywords' — lowercase words or "
    "phrases a real draft would use for this.\n\n"

    "OUT OF SCOPE\n"
    "12. Do NOT extract administrative metadata: submission deadlines, submission "
    "portals, award sizes and durations, contact names, agency background, review "
    "timelines. Those are captured separately. A LIMIT the proposal must respect (a "
    "page count, a budget ceiling the narrative must fit) IS in scope."
)

# The sweep runs with a DIFFERENT job description on purpose. Asking the same
# prompt again returns the same list; asking "what did the first reader miss?"
# is what finds the tail — and the tail is the whole point, given the measured
# 3-vs-5 recall gap on identical input.
_SWEEP_SYSTEM = (
    "You audit a requirement list against the solicitation text it was extracted "
    "from, and report ONLY what is missing from it.\n\n"
    "You are the second reader. The first pass over this text missed requirements — "
    "that is why you exist. 'Nothing is missing' is the right answer only when it is "
    "true.\n"
    "1. Work through the text from its first line to its last. For each requirement "
    "you find, check the ALREADY EXTRACTED list: if it is there, ignore it; if it is "
    "not, return it.\n"
    "2. Match by MEANING, not wording. A different phrasing of an ask already on the "
    "list is not missing.\n"
    "3. Look hardest where a first pass fails: items after the third or fourth bullet "
    "of a long list, requirements inside tables and footnotes, asks buried mid-"
    "paragraph after a long descriptive passage, the second and third clauses of "
    "compound sentences, and anything in the last third of the text.\n"
    "4. An empty array is a valid and common answer. Never pad it with rephrasings of "
    "rows already on the list, and never invent.\n"
    "5. Same quoting rule, enforced the same way: 'source' is a VERBATIM quote from "
    "this text, <=300 characters. Unquotable rows are discarded."
)

# THE THIRD READER, and it exists because the sweep CONVERGING is not the same
# thing as the document being exhausted.
#
# Measured on a 54,065-char federal solicitation (one chunk): the first pass plus the
# generic sweep run to convergence — a round returns nothing new, no cap hit —
# and FOUR requirements the human-verified list contains are still missing. Both
# readers share a blind spot, so asking either of them again, or letting them run
# more rounds, cannot find these; more rounds was measured and changed nothing
# (3 rounds -> 43 rows, 12 rounds -> 41 rows, same four missing).
#
# What works is a different TARGET rather than a different job description: name
# the categories the misses cluster in and hunt each one. Measured over the same
# document: +5 rows in 4.5s with categories A-E, recovering the cost-sharing
# PROHIBITION the prompt already asked for in so many words; adding category F
# took recall from 20/24 to 23/24, and the twenty-fourth is a matcher artifact
# (the Letter of Institutional Support IS extracted, quoting one sentence where
# the curated row quotes two joined by an ellipsis).
#
# Category F is the one to keep if this is ever trimmed. The three requirements
# it recovered came from the one sentence defining what a funded project must
# ACCOMPLISH, and it sits mid-paragraph in the program-description prose — not
# in the preparation instructions, where every reader looks.
_TARGETED_SYSTEM = (
    "You are the THIRD reader of a grant solicitation, auditing a requirement list "
    "that two previous readers already agreed was complete. It is not complete. Your "
    "job is to sweep specific CATEGORIES of requirement that generic readers "
    "systematically miss, and to return only rows absent from the list you are given.\n\n"
    "Work these categories one at a time, over the WHOLE text:\n"
    "A. PROHIBITIONS and things the proposal must NOT do or include — 'is prohibited', "
    "'is not allowed', 'may not', 'will be returned without review', 'do not include'. "
    "A prohibition IS a requirement even when it concerns budget or cost sharing.\n"
    "B. COMPOUND CONTENT ASKS. A sentence of the form 'X must describe how the project "
    "will do A, B and C' states THREE requirements, one per clause. Return one row per "
    "clause, each quoting the same sentence. Do this for every multi-clause ask.\n"
    "C. What each NAMED PART of the proposal must contain, taken clause by clause.\n"
    "D. Required letters, attachments and supplementary documents, including who must "
    "sign them and what they must say.\n"
    "E. Formatting, ordering and labelling rules the document itself must follow.\n"
    "F. Asks stated OUTSIDE the preparation instructions — in the program description, "
    "the background, or prose about what makes a proposal competitive. Sentences like "
    "'the most competitive proposals will...', 'the project description should describe "
    "how...', 'projects are expected to...' read as advice but are requirements, and "
    "they are usually buried mid-paragraph after a long descriptive passage.\n\n"
    "Match by MEANING against the list you are given: a rephrasing of a row already "
    "there is NOT missing. 'source' must be a VERBATIM quote from the text, <=300 "
    "characters; unquotable rows are discarded. An empty array is valid.\n\n"

    "'section' — THE SAME RULE THE OTHER READERS FOLLOW, and category F makes it "
    "easy to break. It is the part of the APPLICANT'S PROPOSAL the requirement "
    "belongs to (project_summary, project_description, budget_justification, "
    "letter_of_intent, ...). It is NEVER a heading of the SOLICITATION: "
    "'Program Description', 'Budgetary Information', 'Eligibility Information', "
    "'Award Administration Information', 'Reporting Requirements' and the like name "
    "parts of the document you are reading, not parts of a proposal. WHERE an ask is "
    "STATED has nothing to do with WHICH PART OF THE PROPOSAL must satisfy it — an "
    "ask stated in the program description usually belongs to project_description, or "
    "to no section at all. If it is about the proposal as a whole, or you cannot tell, "
    "use null.\n"
    "This is not cosmetic. A section that names no real part of a proposal is never "
    "located in the applicant's draft, so the requirement is reported 'not assessed' "
    "and drops out of their completeness score — the row is recovered and then never "
    "checked, which is the failure this pass exists to prevent."
)

# ── READING A RULEBOOK, NOT A SOLICITATION ──────────────────────────────────
#
# When a solicitation points at a rulebook — "prepared in accordance with the
# PAPPG" — that rulebook's rules govern the proposal just as much as the
# solicitation's own. `services.delegated_rules.rulebooks_in()` identifies which
# rulebook, DETERMINISTICALLY, from the names and URLs in the solicitation; this
# prompt is what reads the rulebook once it has been fetched.
#
# IT IS A DIFFERENT PROMPT FROM `_SYSTEM` ON PURPOSE. A solicitation is written
# for ONE competition and nearly everything in it constrains the proposal. A
# rulebook is written for every proposal the agency will ever receive, and most
# of it is not about the document an applicant writes:
#
#   * submission mechanics — accounts, portals, who clicks submit, certifications
#   * post-award administration — reporting, payments, audits, terminations
#   * the agency's own review process — panels, timelines, reconsideration
#   * rules for proposal TYPES this applicant is not submitting
#
# Extracted wholesale, those swamp the real rules: a PI would be told their
# Project Description is missing "register in SAM.gov". So this prompt keeps only
# what constrains the CONTENT or FORM of the proposal document itself.
_RULEBOOK_SYSTEM = (
    "You extract, for a university research office, the rules a funding agency's "
    "RULEBOOK places on the PROPOSAL DOCUMENT an applicant writes. You output DATA "
    "ONLY from the text given to you.\n\n"

    "THIS IS A RULEBOOK, NOT A SOLICITATION\n"
    "It governs every proposal the agency receives, so most of it is NOT about the "
    "document an applicant writes. Keep only rules that constrain what the proposal "
    "must CONTAIN, how it must be STRUCTURED, or how it must be FORMATTED.\n\n"

    "OUT OF SCOPE — do not return these, however imperative they sound:\n"
    "1. Submission mechanics: registering an account, using a portal, who is "
    "authorised to submit, certifications signed at submission, file naming and "
    "upload steps, deadlines and time zones.\n"
    "2. Post-award administration: reporting, payments, audits, prior approvals, "
    "no-cost extensions, terminations, record retention.\n"
    "3. The agency's own internal process: how panels are convened, review "
    "timelines, reconsideration, how decisions are communicated.\n"
    "4. Definitions, background, and eligibility of the ORGANISATION or the PERSON "
    "— those are not things the written document can satisfy.\n\n"

    "IN SCOPE — the rules a reviewer could check by reading the proposal:\n"
    "5. What a named part of the proposal must contain, clause by clause. "
    "'The Project Summary must contain an overview, a statement on intellectual "
    "merit, and a statement on broader impacts' is THREE requirements.\n"
    "6. Structure and labelling: which sections must exist, what they must be "
    "called, whether a heading must appear.\n"
    "7. Limits the document must respect: page counts, character counts, how many "
    "of something may be listed.\n"
    "8. Formatting of the document itself: font, type size, margins, line spacing, "
    "page size. Return these — a later stage decides what can be verified from "
    "extracted text — but never invent one.\n"
    "9. Prohibitions: content that must NOT appear, and content that causes the "
    "proposal to be returned without review.\n\n"

    "CONDITIONAL RULES ARE REAL RULES\n"
    "10. A rulebook is full of asks that apply only sometimes ('for renewal "
    "proposals', 'if the project involves human subjects', 'collaborative proposals "
    "from multiple organizations'). Return them, and set \"scored\": false so they "
    "are shown as conditional rather than counted against a proposal they do not "
    "apply to. Never drop one, and never treat one as universal.\n\n"

    "QUOTING — ENFORCED IN CODE\n"
    "11. 'source' MUST be a VERBATIM quote from the text given, copied character "
    "for character, <=300 characters. Every row is checked against the document and "
    "a row whose quote is not found there is DISCARDED, so an imperfect copy "
    "destroys a real rule. Never paraphrase and never join distant sentences.\n\n"

    "FIELDS\n"
    "12. 'section' — the part of the APPLICANT'S PROPOSAL the rule belongs to, "
    "snake_case (project_summary, project_description, budget_justification, "
    "biosketch, data_management_plan, references_cited, ...). NEVER the rulebook's "
    "own chapter or section headings — 'Chapter II', 'Proposal Preparation "
    "Instructions', 'Award Administration' name parts of the document you are "
    "reading, not parts of a proposal. Use null when the rule is about the proposal "
    "as a whole or you cannot tell.\n"
    "13. 'label' is a short imperative name (<=80 chars). 'why' is one sentence on "
    "why it matters. 'keywords' are lowercase words a real draft would use.\n\n"

    "COMPLETENESS\n"
    "14. Read from the first line to the last. There is no maximum. If a passage "
    "holds no rule about the proposal document, return an empty array rather than "
    "manufacturing one."
)

_RULEBOOK_SWEEP_SYSTEM = (
    _SWEEP_SYSTEM
    + "\n\nSCOPE — this text is a RULEBOOK, not a solicitation, and the same limits "
      "apply to anything you add: keep only rules constraining what the PROPOSAL "
      "DOCUMENT must contain, how it is structured, or how it is formatted. Do NOT "
      "return submission mechanics (accounts, portals, who submits, certifications, "
      "uploads), post-award administration (reporting, payments, audits), or the "
      "agency's own review process. A rule that applies only to certain proposal "
      "types is real — return it with \"scored\": false."
)

_SCHEMA_HINT = (
    'Return JSON: {"requirements": [{"label": "<short imperative name, <=80 chars>", '
    '"section": "<snake_case section or null>", "source": "<verbatim quote>", '
    '"why": "<one sentence: why a reviewer cares>", '
    '"keywords": ["<lowercase word or phrase a draft would use>", ...], '
    '"scored": true}]}'
)

_MERIT_SYSTEM = (
    "You extract the REVIEW CRITERIA a grant solicitation says its panel will judge "
    "proposals on — not the requirements an applicant must include. Data only, from "
    "the text given. Each criterion needs a VERBATIM quote from the text (<=300 "
    "chars); a criterion you cannot quote will be discarded. Return an empty array if "
    "the text does not state review criteria."
)

_MERIT_HINT = (
    'Return JSON: {"criteria": [{"criterion": "<the criterion\'s name as written>", '
    '"asks": "<one sentence: what it asks of the proposal>", '
    '"source": "<verbatim quote>"}]}'
)

# Filler that carries no meaning in a section name, so "the_project_description"
# and "project_description" do not become two sections — each extra section costs
# the reviewer its own Gemini call.
_SECTION_FILLER = {"the", "a", "an", "of", "for", "and", "section", "sections",
                   "part", "your", "proposal", "proposals"}


def chunk_text(text: str, size: int = CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Overlapping windows covering the WHOLE document.

    The overlap is not padding: a requirement sentence split across a boundary
    would be quotable in neither half, so the verifier would drop it — a silent
    loss of exactly the kind of row this module exists to find."""
    text = text or ""
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    return [text[i:i + size] for i in range(0, len(text), step)]


# Headings of the SOLICITATION, in their canonical form (filler stripped,
# singularised) — i.e. compare AFTER normalising, never against the raw string.
#
# `_SYSTEM` rule 9 already forbids these and the model mostly obeys, but a
# minority leak on every run. A section naming no real part of a proposal can
# never be located in a draft, so every requirement filed under it is reported
# "Not located" and drops out of the score's denominator — unchecked, silently.
# Resolving to None files the row at whole-document scope instead, where
# draft_review assesses it against the Project Description span or the whole
# paste. THAT is why a false positive here is tolerable and the status quo is
# not: the wrong outcome is a WIDER search, not a lost requirement.
#
# Kept to headings that are unambiguously FOA structure. Deliberately ABSENT:
# "introduction", "background", "results from prior support" — a PI's own
# Project Description routinely opens with those.
_DOCUMENT_HEADINGS = {
    "program_description",
    "award_information",
    "award_administration_information",
    "eligibility_information",
    "budgetary_information",
    "application_submission_information",
    "preparation_submission_instruction",
    "preparation_instruction",
    "submission_instruction",
    "application_review_information",
    "review_information",
    "agency_contact",
    "reporting_requirement",
    "additional_information",
    "other_information",
    "summary_program_requirement",
    "program_requirement",
}

# A leading "VII." / "2." is the solicitation's own section NUMBER. It has to be
# dropped as a TOKEN rather than by a numbering regex on the raw string: the
# non-alphanumeric scrub below removes the period first, so "VII. Award
# Administration Information" arrives as the word "vii" and reached the key as
# `vii_award_administration_information`.
_NUMBER_TOKEN = re.compile(r"^(?:\d+|[ivxlcdm]+)$")


def canon_section(raw: Optional[str]) -> Optional[str]:
    """Canonical snake_case section key, or None for a whole-document row.

    The model names sections per chunk, so one document can come back with
    "project_description", "the project description" and "project descriptions".
    Left alone those are three sections, and the reviewer spends one Gemini call
    on each."""
    if raw is None:
        return None
    s = re.sub(r"[^a-z0-9]+", " ", str(raw).strip().lower())
    words = [w for w in s.split() if w not in _SECTION_FILLER]
    # Leading numbering only: a numeral INSIDE a name can be meaningful, and
    # stripping from the front stops at the first real word.
    while words and _NUMBER_TOKEN.match(words[0]):
        words.pop(0)
    if not words:
        return None
    # Singularize EVERY token, not just the last: "letters of collaboration" and
    # "letter of collaboration" must land on one key, and "of" is filler so the
    # plural is not in final position. Deliberately naive, with a guard so
    # "analysis" does not become "analysi".
    words = [w[:-1] if (len(w) > 3 and w.endswith("s")
                        and not w.endswith(("ss", "us", "is"))) else w
             for w in words]
    key = "_".join(words) or None
    # Checked AFTER normalising, so one entry covers every way the model writes
    # it ("Proposal Preparation and Submission Instructions", "Preparation
    # Instruction", "V. Preparation Instructions").
    if key in _DOCUMENT_HEADINGS:
        return None
    return key


def make_id(row: dict) -> str:
    """Deterministic id from section + label, so the same requirement extracted
    from two overlapping chunks dedupes to one row."""
    section = canon_section(row.get("section")) or "general"
    label = re.sub(r"[^a-z0-9]+", "_", str(row.get("label") or "").lower()).strip("_")
    return f"{section}_{label}"[:80] or "requirement"


def _coerce(row: dict) -> Optional[dict]:
    """One model row -> a requirement row, or None if it is unusable."""
    if not isinstance(row, dict):
        return None
    label = " ".join(str(row.get("label") or "").split())[:120]
    source = " ".join(str(row.get("source") or "").split())[:300]
    if not label or not source:
        return None
    section = canon_section(row.get("section"))
    keywords = [str(k).strip().lower() for k in (row.get("keywords") or [])
                if str(k).strip()][:8]
    out = {
        "id": "",
        "label": label,
        "section": section,
        "kind": "semantic",
        "scored": bool(row.get("scored", True)),
        "source": source,
        "why": " ".join(str(row.get("why") or "").split())[:300],
        "keywords": keywords,
    }
    out["id"] = make_id(out)
    return out


def _ask(prompt: str, system: str = _SYSTEM, key: str = "requirements") -> list:
    # 32k out, not 8k. MEASURED against a real federal solicitation: one chunk
    # produced enough rows to hit an 8192-token ceiling mid-string, so the JSON
    # came back unterminated,
    # the parse failed, and EVERY requirement in that response was lost — a whole
    # chunk of the solicitation gone with nothing but a log line. The one failure
    # this module cannot tolerate is a silent one, and a truncated response looks
    # exactly like a chunk that had no requirements in it.
    # thinking_budget=0 — MEASURED, not a guess. gemini-3.6-flash thinks by
    # default and on this task the thinking dominates the wall clock without
    # improving the answer. On a real 50,508-char federal solicitation (-> ONE
    # chunk, so these are four SERIAL calls; the worker pool below only engages
    # past CHUNK_CHARS): 72.2s thinking-on vs 25.9s / 27.1s off, with recall
    # against the human-verified curated list going 79% -> 83% / 92%. See
    # tests/test_solicitation_requirements_recall.py. The fast runs were
    # also the MORE complete ones, because the slow run spent its own budget_s
    # on thinking and got through fewer sweep rounds. Same lesson
    # opportunity_finder already learned.
    ai = gemini_client.generate_json(
        prompt, temperature=0.0, max_output_tokens=32768, timeout_s=180,
        thinking_budget=0,
        system_instruction=system, model=MODEL, location=MODEL_LOCATION)
    if not ai or not isinstance(ai.get(key), list):
        return []
    return ai[key]


def extract_requirements(text: str, *, use_ai: bool = True, max_rounds: int = 8,
                         budget_s: float = DEFAULT_BUDGET_S,
                         targeted: bool = True,
                         system: Optional[str] = None,
                         sweep_system: Optional[str] = None) -> dict:
    """Every requirement in `text`, each backed by a verbatim quote from it.

    max_rounds was 3, which was the binding constraint for the wrong reason.
    Disabling Gemini's thinking (2026-08-12) made a round ~3x cheaper, so the
    counter started stopping the sweep while the wall-clock budget was ~80%
    unspent — and a run that stops on a counter reports `hit_round_cap`, telling
    the PI their list may be incomplete when nothing had actually run out.
    Measured on a real solicitation: the sweep CONVERGES in 3-7 rounds and
    27-35s of the 150s budget, so 8 lets convergence be the reason it stops
    while `budget_s` stays the real bound.

    `system` / `sweep_system` override the reader's job description. They exist
    for RULEBOOKS — a document written for every proposal the agency will ever
    receive, where most of the text is submission mechanics and post-award
    administration rather than rules about the applicant's draft. Both must move
    together: leaving the sweep on the solicitation prompt lets round two quietly
    re-import everything round one was told to leave out.

    targeted=False runs the first pass and the sweep only. It exists so a test
    can isolate ONE reader: a fake that answers every call otherwise has its
    rows counted once per reader, which turns an exact assertion about dropped
    rows into an assertion about how many readers there are."""
    text = text or ""
    out: dict = {"requirements": [], "ai": False, "rounds": 0, "chunks": 0,
                 "chars": len(text), "dropped_unverified": 0,
                 "hit_round_cap": False, "hit_time_cap": False,
                 "targeted_added": 0, "targeted_ran": False, "elapsed_s": 0.0}
    if not text.strip() or not use_ai:
        return out

    started = time.monotonic()
    deadline = started + max(0.0, budget_s)
    chunks = chunk_text(text)
    out["chunks"] = len(chunks)

    # Normalized ONCE. Verifying ~40 rows against a 300k-char document would
    # otherwise re-normalize the whole document 40 times.
    haystack = normalize(text, drop_list_noise=True)

    by_id: dict = {}
    dropped = 0

    def absorb(rows: list) -> int:
        nonlocal dropped
        added = 0
        for raw in rows:
            row = _coerce(raw)
            if row is None:
                continue
            # GOLDEN RULE 2, against the WHOLE document rather than the chunk, so
            # a quote the model pulled from an overlapping region still verifies.
            # drop_list_noise because this is model output vs PDF-extracted text,
            # and bulleted items are the commonest shape of a real requirement.
            # ONE DEFINITION OF VERIFIED, with a fast path in front of it.
            #
            # This used to BE the definition — `normalize(...) not in haystack`,
            # with the normalised document hoisted out of the loop. Reasonable
            # as an optimisation, and it made this module the only place golden
            # rule 2 has a second, private implementation. So when `quote_in`
            # learned to read dash-broken words and then to tolerate ligatures
            # a typeset PDF loses, the extractor got neither — and it is the
            # component that reads solicitations out of typeset PDFs, which is
            # exactly where both artifacts occur. Measured on a real NSF
            # solicitation: two genuine requirements dropped, both quoting a
            # word the PDF had mangled (`justification` -> `justication`).
            #
            # The hoisted haystack is kept because re-normalising 50,000
            # characters per row would be waste; `quote_in` is consulted only
            # for the rows the fast path would otherwise throw away.
            if normalize(row["source"], drop_list_noise=True) not in haystack:
                if not quote_in(text, row["source"], drop_list_noise=True):
                    dropped += 1
                    continue
            if row["id"] in by_id:
                continue
            by_id[row["id"]] = row
            added += 1
        return added

    def _sweep_prompt(chunk: str, known: str) -> str:
        return (f'SOLICITATION TEXT:\n"""\n{chunk}\n"""\n\n'
                f"ALREADY EXTRACTED (do NOT repeat these): {known}\n\n"
                "List ONLY requirements present in the text above that are missing "
                f"from that list. Return an empty array if there are none.\n{_SCHEMA_HINT}")

    def _known() -> str:
        return "; ".join(sorted(r["label"] for r in by_id.values()))[:6000]

    def _run(prompts: list, system: str) -> int:
        """Fan the chunk prompts out concurrently and absorb what comes back.

        Concurrent because sequential is the difference between finishing and
        timing out: 6 chunks x ~30s serial is 180s, over half the request cap
        before a single sweep round has run."""
        if not prompts:
            return 0
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(prompts))) as pool:
            results = list(pool.map(lambda p: _ask(p, system), prompts))
        return sum(absorb(r) for r in results)

    first = [f'SOLICITATION TEXT (part {i + 1} of {len(chunks)}):\n"""\n{c}\n"""\n\n{_SCHEMA_HINT}'
             for i, c in enumerate(chunks)]
    _run(first, system or _SYSTEM)

    # SWEEP UNTIL DRY. Bounded twice — by rounds and by wall clock — and both
    # bounds are reported, because a silent stop reads as completeness.
    for round_no in range(1, max_rounds + 1):
        if time.monotonic() >= deadline:
            out["hit_time_cap"] = True
            break
        out["rounds"] = round_no
        known = _known()
        remaining = [c for c in chunks if time.monotonic() < deadline]
        if len(remaining) < len(chunks):
            out["hit_time_cap"] = True
        if _run([_sweep_prompt(c, known) for c in remaining],
                sweep_system or _SWEEP_SYSTEM) == 0:
            break
    else:
        if max_rounds > 0:
            out["hit_round_cap"] = True

    # THE TARGETED PASS. Runs AFTER the sweep, including after it converged —
    # convergence means both readers agree, not that the document is exhausted,
    # and the four requirements this recovers in measurement were all missed by a
    # sweep that had reported nothing left to find. One call per chunk, so it is
    # ~3-5s on a typical single-chunk solicitation.
    if targeted and time.monotonic() < deadline:
        reachable = [c for c in chunks if time.monotonic() < deadline]
        if len(reachable) < len(chunks):
            out["hit_time_cap"] = True
        known = _known()
        prompts = [f'SOLICITATION TEXT:\n"""\n{c}\n"""\n\n'
                   f"ALREADY EXTRACTED (do NOT repeat these): {known}\n\n"
                   f"Return ONLY requirements missing from that list.\n{_SCHEMA_HINT}"
                   for c in reachable]
        out["targeted_added"] = _run(prompts, _TARGETED_SYSTEM)
        out["targeted_ran"] = bool(prompts)
    elif targeted:
        # Ran out of clock before the third reader could start. Reported, never
        # silent: it is the difference between a list every reader went over and
        # one that two of three did. `targeted=False` is a deliberate skip and
        # must NOT be reported as a time cap.
        out["hit_time_cap"] = True

    out["requirements"] = list(by_id.values())
    out["dropped_unverified"] = dropped
    out["ai"] = True
    out["elapsed_s"] = round(time.monotonic() - started, 1)
    return out


def extract_merit_criteria(text: str, *, use_ai: bool = True) -> list[dict]:
    """The criteria a panel will judge against, for the reviewer-notes pass.

    Shaped as draft_review._reviewer_notes consumes it ({"criterion", "asks"}).
    Without this the advisory panel-impressions feature returns [] for every
    proposal in the product, silently, forever."""
    text = (text or "").strip()
    if not text or not use_ai:
        return []
    # The criteria are stated once, in a compact section — the first chunk is
    # where solicitations put them, and this is a nice-to-have next to the
    # requirement sweep, so it does not get its own fan-out budget.
    chunk = chunk_text(text)[0]
    rows = _ask(f'SOLICITATION TEXT:\n"""\n{chunk}\n"""\n\n{_MERIT_HINT}',
                _MERIT_SYSTEM, key="criteria")
    out = []
    seen = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        criterion = " ".join(str(raw.get("criterion") or "").split())[:120]
        asks = " ".join(str(raw.get("asks") or "").split())[:300]
        source = " ".join(str(raw.get("source") or "").split())[:300]
        if not criterion or not asks:
            continue
        if not quote_in(text, source, drop_list_noise=True):
            continue                      # golden rule 2: unquotable is dropped
        if criterion.lower() in seen:
            continue
        seen.add(criterion.lower())
        out.append({"criterion": criterion, "asks": asks, "source": source})
    return out
