"""DRAFT of agent/tools/documents.py with both tools written out in plain, readable Python.
Read it top to bottom, then copy what you understand into documents.py. Delete this file afterwards.

Nothing imports this file. It is only here to learn from.

=====================================================================================================
WHAT THIS FILE IS FOR
=====================================================================================================
Invoices arrive as PDF attachments in the mail app. Before the agent types an amount into the finance
system it must (1) read the PDF, and (2) PROVE that every value it wants to type really is in that PDF.

    read_pdf        get the text out of a downloaded PDF file
    grounded_check  is this value literally in that text?  (the guard against made-up numbers)

=====================================================================================================
WHY grounded_check EXISTS (the most important idea in this file)
=====================================================================================================
A language model can sound completely sure and still be wrong. It may "read" the amount 1,18,000.00
from a PDF that really says 98,400.00. Typing a wrong amount into a finance system is expensive.

So the model is never trusted on its own. Before any value is written it must pass grounded_check:
"find this value in the document's text". If it is not there, the value is not "grounded" and
policy.py refuses to let the invoice be submitted. A made-up number is therefore caught twice: here, and
later by the verifier, which reads the saved record back and compares it.

"Grounded" simply means "anchored in the source document".

=====================================================================================================
THE ONE TRICKY PART: THE SAME NUMBER CAN BE WRITTEN DIFFERENTLY
=====================================================================================================
    Indian grouping   1,18,000.00     (after the first 3 digits, groups of 2)
    Western grouping  118,000.00
    no grouping       118000.00
    stray spaces      1,18, 000.00    (PDF text extraction often adds odd spaces)
If we compared the raw text, a correct value would look "not found". So both sides are first cleaned by
normalise(): spaces and commas removed, letters made lower case. Then 1,18,000.00 and 118000.00 are the
same string and the comparison works.
"""
import re
from pathlib import Path

from .base import ToolResult, tool
from .browser import get_session

# Memory shared by the two tools. read_pdf stores the text of each PDF here under a name
# (the "source_ref"); grounded_check looks the text up by that name. This way the model never has to
# send a whole document back to us, it only says "check it in INV-X-1042.pdf".
# It is a dictionary: name -> full text. It lives as long as the program runs.
SOURCES = {}


def normalise(text):
    """Make two spellings of the same value comparable.

    re.sub(pattern, replacement, text) replaces everything that matches `pattern`.
    The pattern r"[\\s,]" means "one whitespace character OR one comma".
    Replacing them with "" deletes them. .lower() then ignores upper / lower case.
    Example: normalise("Total: 1,18, 000.00") == "total:118000.00"
    """
    return re.sub(r"[\s,]", "", text).lower()


def is_inside_downloads(file_path):
    """True only if the file is inside this run's download folder.

    Why: the model chooses the path it passes to read_pdf. Without this check it could ask us to
    open any file on the computer. The agent only needs the PDFs that browser_download saved.
    """
    download_folder = get_session().download_dir.resolve()    # resolve() = the full, absolute path
    return file_path.is_relative_to(download_folder)


@tool("read_pdf", "Extract text per page from a downloaded PDF", {"path": "str"})
def read_pdf(path):
    """Read a PDF and remember its text so grounded_check can search it.

    Returns {"source_ref": <file name>, "pages": [[line, line, ...], ...]}: the text split into lines,
    one list per page. Invoices are one page, so this is small enough to put in a prompt.
    """
    try:
        file_path = Path(path).resolve()
    except (OSError, ValueError):
        return ToolResult.fail("ERR_PDF_UNREADABLE", f"bad path: {path}")

    if not is_inside_downloads(file_path):
        return ToolResult.fail("ERR_POLICY", "read_pdf only opens files saved by browser_download")

    # pdfplumber is the library that pulls text out of PDFs. It is imported here, not at the top of the
    # file, so that everything else still works on a computer where it is not installed.
    try:
        import pdfplumber
    except ImportError:
        return ToolResult.fail("ERR_PDF_UNREADABLE", "pdfplumber is not installed: pip install pdfplumber")

    pages = []
    try:
        with pdfplumber.open(file_path) as pdf:            # "with" closes the file for us when we are done
            for page in pdf.pages:
                pages.append(page.extract_text() or "")    # extract_text() gives None for an empty page
    except Exception as error:
        # A truncated or corrupt file (the pdf_corrupt_once fault) makes pdfplumber raise some error.
        # We do not care which one. The answer is always the same: "unreadable, download it again".
        return ToolResult.fail("ERR_PDF_UNREADABLE", f"{file_path.name} could not be read: {error}")

    full_text = "\n".join(pages)
    if full_text.strip() == "":
        # A scanned picture inside a PDF has no text to read. Same answer: we cannot use this file.
        return ToolResult.fail("ERR_PDF_UNREADABLE", f"{file_path.name} has no readable text")

    SOURCES[file_path.name] = full_text                    # remember it for grounded_check

    lines_per_page = [page_text.splitlines() for page_text in pages]
    data = {"source_ref": file_path.name, "pages": lines_per_page}
    return ToolResult(True, data, evidence=f"{file_path.name}: {len(pages)} page(s), {len(full_text)} characters")


def contains_value(clean_line, clean_value):
    """Is `clean_value` inside `clean_line`, and not just as a piece of a LONGER number?

    A plain `clean_value in clean_line` has a trap: "1000" is inside "11000.00", so the amount 1000 would
    wrongly be called grounded. So when the value starts or ends with a digit, the character right
    before / after the match must not be a digit.
    """
    start = clean_line.find(clean_value)                   # position of the first match, or -1
    while start != -1:
        end = start + len(clean_value)

        digit_before = start > 0 and clean_line[start - 1].isdigit()
        digit_after = end < len(clean_line) and clean_line[end].isdigit()
        starts_badly = clean_value[0].isdigit() and digit_before
        ends_badly = clean_value[-1].isdigit() and digit_after

        if not starts_badly and not ends_badly:
            return True
        start = clean_line.find(clean_value, start + 1)    # that match was part of a longer number, look further
    return False


@tool("grounded_check", "Is this value literally present in the source text?",
      {"field": "str", "value": "str", "source_ref": "str"})
def grounded_check(field, value, source_ref):
    """Is `value` really in the document called `source_ref`?

    field       which form field this is for ("amount", "invoice_no"...). Only used in the answer.
    value       what the model wants to type.
    source_ref  the document's name, as returned by read_pdf.

    Returns {"field", "grounded": True/False, "quote": "<the PDF line that contains it>"}.
    The quote is EVIDENCE: a human can see exactly where the value came from.

    "Not grounded" is a normal, useful answer, so ok is True in that case. It is not an error. The model
    must then NOT write the value: it asks a human or requests approval instead. (A date the model worked
    out itself, such as "Net 30 days from the invoice date", is never literally in the PDF, so it also
    ends up here. That is exactly the case that needs approval.)
    """
    text = SOURCES.get(source_ref)
    if text is None:
        return ToolResult.fail("ERR_VALIDATION", f"unknown source_ref '{source_ref}', run read_pdf first")

    clean_value = normalise(str(value))
    if clean_value == "":
        # An empty value would be "found" everywhere. It is never grounded.
        return ToolResult(True, {"field": field, "grounded": False, "quote": ""},
                          evidence=f"{field} is empty")

    # Look at the document line by line. The first line that contains the value is the quote.
    quote = ""
    for line in text.splitlines():
        if contains_value(normalise(line), clean_value):
            quote = line.strip()
            break

    grounded = quote != ""
    data = {"field": field, "grounded": grounded, "quote": quote}
    if grounded:
        evidence = quote
    else:
        evidence = f"{field}='{value}' not found in {source_ref}"
    return ToolResult(True, data, evidence=evidence)
