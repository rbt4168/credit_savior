from dataclasses import replace

import pytest
from pypdf import PdfReader

from credit_savior.errors import WorkflowError
from credit_savior.solver import CodexSolver, decode_symbol_text
from credit_savior.validation import validate


def test_symbol_font_is_decoded_using_font_encoding():
    assert decode_symbol_text('\uf073 \uf064 \uf070', '/Subset+SymbolMT') == '\u03c3 \u03b4 \u03c0'
    with pytest.raises(WorkflowError, match='pdf_symbols_unsupported'):
        decode_symbol_text('\uf073', '/UnknownFont')


def test_cjk_pdf_render_and_code_runner_gate(config, assignment):
    output = config.data_dir / 'tasks/test/answer'
    output.mkdir(parents=True)
    solver = CodexSolver(config.data_dir)
    answer = solver.render({'answer_kind': 'files', 'artifacts': [
        {'logical_name': 'report', 'format': 'pdf', 'content': '\u89e3\u7b54\uff1a2+2=4'},
        {'logical_name': 'source', 'format': 'py', 'content': 'print(2+2)'}]}, output)
    upload = replace(assignment, submission_types=('online_upload',))
    assert len(PdfReader(config.data_dir / answer.files[0]).pages) == 1
    assert validate(upload, answer, config.data_dir) == ['runner_unavailable']
    (config.data_dir / answer.files[0]).write_bytes(b'tampered')
    assert 'artifact_invalid' in validate(upload, answer, config.data_dir)


def test_model_artifact_cannot_escape_task_directory(config):
    solver = CodexSolver(config.data_dir)
    with pytest.raises(WorkflowError, match='model_response_invalid'):
        solver.render({'answer_kind': 'files', 'artifacts': [
            {'logical_name': '../outside', 'format': 'txt', 'content': 'escape'}]}, config.data_dir)
