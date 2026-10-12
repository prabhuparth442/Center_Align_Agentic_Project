"""PDF reading and the hallucination guard.

read_pdf gets the text out of the invoice PDF. grounded_check proves that a value really appears in that text
before it is written to the finance form (a model can invent a plausible amount). policy.py blocks the submit
without that proof. The TODO comments stay, with a short DONE note under each.
"""
import re
from pathlib import Path

from .base import ToolResult, tool
from .browser import get_session

# source_ref (the file name) -> text of that pdf. read_pdf fills it, grounded_check reads it.
sources = {}


def normalise(s: str) -> str:
    """Drop whitespace and Indian/western digit grouping commas so '1,18,000.00' == '118000.00'.

    Problem: the same amount can be printed differently:
        western grouping : 118,000.00
        Indian grouping  : 1,18,000.00   (lakh/crore style: groups of 2 after the first 3)
        stray spaces / line breaks from PDF extraction: "1,18, 000.00"
    If we compared raw strings, a correct value would look "not found". So both sides are
    reduced to a canonical form before comparing.

    How: `re.sub(r"[\\s,]", "", s)` deletes every character that is whitespace (\\s) or a comma;
    `.lower()` ignores upper/lower case differences (e.g. 'INV-001' vs 'inv-001').
    Note it removes ALL commas, which is fine for numbers but also means this is a lenient
    comparison - acceptable because we only need "is this value present", not exact formatting.
    """
    return re.sub(r"[\s,]", "", s).lower()


@tool("read_pdf", "Extract text per page from a downloaded PDF", {"path": "str"})
def read_pdf(path):
    # Takes a local file path (the one browser_download returned), not a URL.
    # TODO: pdfplumber; any parse error -> ERR_PDF_UNREADABLE.
    #   - pdfplumber        : Python library that extracts text from PDFs (see requirements.txt).
    #   - ERR_PDF_UNREADABLE: a truncated/corrupt file (pdf_corrupt_once fault) makes the parser
    #                         raise. We catch ANY parse error and return this stable code, so the
    #                         loop knows to re-download instead of extracting values from nothing.
    #   - also remember the text under a source_ref, so grounded_check can look it up later
    #     (see below) without the model having to re-send the whole document.
    #
    # DONE: only files inside the run's download folder can be opened, the model picks the path and
    # shouldn't be able to read anything else. The text is kept in `sources` under the file name,
    # and that name is the source_ref we return.
    file_path = Path(path).resolve()
    if not file_path.is_relative_to(get_session().download_dir.resolve()):
        return ToolResult.fail("ERR_POLICY", "read_pdf only opens files saved by browser_download")

    import pdfplumber    # imported here so the agent still starts on a machine without it
    pages = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
    except Exception as exc:
        # a broken file can fail in many ways, the answer is always "download it again"
        return ToolResult.fail("ERR_PDF_UNREADABLE", f"{file_path.name} could not be read: {str(exc)[:150]}")

    full_text = "\n".join(pages)
    if full_text.strip() == "":
        return ToolResult.fail("ERR_PDF_UNREADABLE", f"{file_path.name} has no readable text")

    sources[file_path.name] = full_text

    lines_per_page = []
    for page_text in pages:
        lines_per_page.append(page_text.splitlines())
    data = {"source_ref": file_path.name, "pages": lines_per_page}
    return ToolResult(True, data, evidence=f"{file_path.name}: {len(pages)} page(s), {len(full_text)} characters")


def value_in_line(line, value):
    """True if `value` is in `line`, but not as part of a longer number ("1000" is not in "11000.00")."""
    start = line.find(value)
    while start != -1:
        end = start + len(value)
        digit_before = start > 0 and line[start - 1].isdigit()
        digit_after = end < len(line) and line[end].isdigit()

        bad_start = value[0].isdigit() and digit_before
        bad_end = value[-1].isdigit() and digit_after
        if not bad_start and not bad_end:
            return True
        start = line.find(value, start + 1)
    return False


@tool("grounded_check", "Is this value literally present in the source text?",
      {"field": "str", "value": "str", "source_ref": "str"})
def grounded_check(field, value, source_ref):
    # Arguments:
    #   field      : which form field this is for (e.g. "amount") - used for logging/evidence.
    #   value      : what the model wants to write (e.g. "118000.00").
    #   source_ref : a handle for which document text to search (stored earlier by read_pdf).
    # TODO: look up source text by source_ref (stored by read_pdf), compare normalise(value) in normalise(text),
    #       return {grounded, quote}.
    #   - `normalise(value) in normalise(text)` is a plain substring test after cleaning both sides.
    #   - `grounded` (bool) is the verdict; `quote` is the surrounding snippet from the PDF.
    #     The quote is the *evidence*: it lets a human (or the verifier) see where the value came from.
    #   - Run on EVERY extracted value. A value the model inferred (e.g. due date computed from
    #     "Net 30") will NOT appear literally, so it fails here and is routed to request_approval.
    #
    # DONE: searches line by line, the matching line is the quote. An empty value is never grounded.
    # "Not grounded" is a normal answer (ok=True, grounded=False), not an error.
    text = sources.get(source_ref)
    if text is None:
        return ToolResult.fail("ERR_VALIDATION", f"unknown source_ref '{source_ref}', run read_pdf first")

    clean_value = normalise(str(value))
    if clean_value == "":
        return ToolResult(True, {"field": field, "grounded": False, "quote": ""}, evidence=f"{field} is empty")

    quote = ""
    for line in text.splitlines():
        if value_in_line(normalise(line), clean_value):
            quote = line.strip()
            break

    grounded = quote != ""
    if grounded:
        evidence = quote
    else:
        evidence = f"{field}='{value}' not found in {source_ref}"
    return ToolResult(True, {"field": field, "grounded": grounded, "quote": quote}, evidence=evidence)
