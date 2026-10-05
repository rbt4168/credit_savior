from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .artifacts import artifact_path, digest
from .models import Answer, Assignment

CODE_EXTENSIONS = {"py", "js", "ts", "java", "c", "cpp", "sql"}


def validate(assignment: Assignment, answer: Answer, data_dir: Path) -> list[str]:
    errors = []
    if answer.kind == "text":
        if "online_text_entry" not in assignment.submission_types:
            errors.append("unsupported_output_format")
        if not answer.text.strip():
            errors.append("empty_answer")
    elif answer.kind == "files":
        if "online_upload" not in assignment.submission_types:
            errors.append("unsupported_output_format")
        if not answer.files or len(answer.files) != len(answer.hashes):
            errors.append("artifact_invalid")
        total = 0
        for relative, expected in zip(answer.files, answer.hashes):
            try:
                path = artifact_path(data_dir, relative)
                if not path.is_file():
                    errors.append("artifact_missing")
                    continue
                total += path.stat().st_size
                extension = path.suffix.lower().lstrip(".")
                if assignment.extensions and extension not in assignment.extensions:
                    errors.append("unsupported_output_format")
                if path.stat().st_size > 25 * 1024 * 1024:
                    errors.append("artifact_too_large")
                if digest(path.read_bytes()) != expected:
                    errors.append("artifact_invalid")
                    continue
                if extension in CODE_EXTENSIONS:
                    errors.append("runner_unavailable")
                if extension == "pdf":
                    pdf = PdfReader(path)
                    if not pdf.pages or not any(page.extract_text() for page in pdf.pages):
                        errors.append("artifact_invalid")
            except (OSError, ValueError, PyPdfError):
                errors.append("artifact_invalid")
        if total > 100 * 1024 * 1024:
            errors.append("artifact_too_large")
    else:
        errors.append("unsupported_output_format")
    return sorted(set(errors))
