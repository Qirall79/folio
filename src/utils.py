import pymupdf
import re
import hashlib


def is_valid_pdf(file_path: str) -> bool:
    try:
        with pymupdf.open(file_path) as doc:
            return doc.is_pdf and doc.page_count > 0 and not doc.needs_pass
    except (pymupdf.FileDataError, pymupdf.EmptyFileError):
        return False


def clean_statement(text: str) -> str:
    return re.sub(
        r"【\d+†L\d+(?:-L\d+)?】",
        "",
        text
    ).strip()


def calculate_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
