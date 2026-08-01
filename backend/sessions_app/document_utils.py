import os

ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt"}
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024


def document_extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def is_allowed_document(filename: str) -> bool:
    return document_extension(filename) in ALLOWED_DOCUMENT_EXTENSIONS


def extract_text_from_document(file_path: str) -> str:
    ext = document_extension(file_path)
    if ext == ".pdf":
        return _extract_pdf(file_path)
    if ext == ".docx":
        return _extract_docx(file_path)
    if ext == ".txt":
        with open(file_path, encoding="utf-8", errors="replace") as f:
            return f.read()
    raise ValueError(f"Unsupported document type: {ext}")


def _extract_pdf(file_path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(file_path)
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    if not pages:
        raise ValueError("Could not extract text from PDF. It may be scanned/image-only.")
    return "\n\n".join(pages)


def _extract_docx(file_path: str) -> str:
    from docx import Document

    doc = Document(file_path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    if not paragraphs:
        raise ValueError("Document appears empty.")
    return "\n".join(paragraphs)
