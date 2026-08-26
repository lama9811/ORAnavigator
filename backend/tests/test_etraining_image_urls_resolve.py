"""The mirrored screenshot URLs must be the same in the manifest as everywhere else.

WHAT WENT WRONG
---------------
A Rise asset key arrives already URL-encoded, so the mirrored blob is genuinely
NAMED `...Screenshot%25202024-11-11%2520133421.png` — the `%` characters are
part of the object name. Referencing it takes a second round of escaping
(`%2525...`); emitting the name verbatim makes the server decode it back to a
name that does not exist, and the browser renders a broken-image box under the
chat answer.

`scripts/fix_etraining_image_urls.py` repaired that on 2026-08-07 by HEADing the
bucket — and it repaired `_etraining_lessons.json` and
`kb_structured/trainings/e_training/*.json`, which is EVERY copy of the data
except the one that is served. `forms_catalog.images_for_titles` reads
`_all_documents.jsonl` and nothing else, so the manifest kept the broken
spelling and the fix changed nothing a PI could see. Measured 2026-08-26:
**73 of 262 manifest URLs still dead**, across 25 lesson rows — the same 25
whose per-doc files had been correctly repaired a fortnight earlier.

WHY THIS TEST IS A DRIFT CHECK AND NOT AN HTTP CHECK
----------------------------------------------------
Asking the bucket is what the repair script does and is the right instrument
THERE — it is the only authority on which spelling names a real object. It is
the wrong instrument here: a unit test that needs the network is a test that is
skipped in CI and red on a plane. The defect was never that a URL was
unreachable; it was that four copies of one string drifted apart and nothing
compared them. So compare them.

Third instance of this repo's two-copies-and-nothing-syncs-them family, after
the datastore-vs-snapshot split and the section-key mismatches in draft review.
"""

import glob
import json
import os

KB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "kb_structured")
MANIFEST = os.path.join(KB, "_all_documents.jsonl")
LESSON_DOCS = os.path.join(KB, "trainings", "e_training", "*.json")


def _manifest_rows():
    rows = {}
    with open(MANIFEST, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            doc = json.loads(line)
            if doc.get("doc_id"):
                rows[doc["doc_id"]] = doc
    return rows


def _urls(doc):
    return [im.get("url") for im in (doc.get("images") or [])]


def test_the_manifest_agrees_with_the_lesson_files_on_every_image_url():
    """The served copy must say what the repaired copy says.

    Reported as one list of offending doc_ids rather than failing on the first,
    because 25 rows drifted together and a one-at-a-time failure would make a
    single systematic defect look like twenty-five separate ones.
    """
    rows = _manifest_rows()
    drifted = []
    for path in sorted(glob.glob(LESSON_DOCS)):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        did = doc.get("doc_id")
        row = rows.get(did)
        if row is None:
            continue
        if _urls(doc) != _urls(row):
            drifted.append(did)

    assert not drifted, (
        f"{len(drifted)} lesson(s) whose screenshot URLs differ between "
        f"kb_structured/trainings/e_training/ and the served manifest "
        f"_all_documents.jsonl: {drifted[:5]}"
        + (" ..." if len(drifted) > 5 else ""))


def test_no_manifest_screenshot_url_carries_a_singly_escaped_object_name():
    """The specific broken spelling, named so a regression says WHICH bug it is.

    A mirrored blob whose name contains `%` must be referenced with that `%`
    escaped, so a real URL reads `%2525` (an escaped `%25`) and never a bare
    `%2520`. Deliberately narrow: it asserts the shape of the defect that
    actually shipped, so a future failure here points straight at the
    public_url() contract in scripts/mirror_etraining_images.py rather than at
    "some URL looks odd".
    """
    bad = []
    for doc in _manifest_rows().values():
        for url in _urls(doc):
            if not isinstance(url, str):
                continue
            if "storage.googleapis.com" not in url:
                continue
            name = url.rsplit("/", 1)[-1]
            if "%25" in name and "%2525" not in name:
                bad.append(name)

    assert not bad, (
        f"{len(bad)} manifest screenshot URL(s) name the object with its `%` "
        f"unescaped, so the server decodes them to a blob that does not exist: "
        f"{bad[:3]}")
