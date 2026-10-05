from dataclasses import replace
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from credit_savior.errors import WorkflowError
from credit_savior.solver import CodexSolver, decode_symbol_text, render_latex_pdf
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


@pytest.mark.asyncio
async def test_homework_preferences_reach_model_prompt(config, assignment, monkeypatch):
    from credit_savior import solver as module
    from credit_savior.artifacts import write_json
    write_json(config.data_dir.parent / 'preferences.json', {'homework': {
        'language': 'English', 'pdf_format': 'LaTeX', 'include_reference_section': False}})
    output = config.data_dir / 'tasks/preference-test/answer'
    captured = {}

    async def launch(*args, **kwargs):
        async def communicate(prompt):
            captured['prompt'] = prompt.decode('utf-8')
            write_json(output / 'model-response.json', {
                'answer_kind': 'text', 'answer_text': 'The answer is four.',
                'artifacts': [], 'missing_information': []})
        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(module, 'codex_command', lambda: ['unused-codex'])
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', launch)
    await CodexSolver(config.data_dir).solve(assignment, output)
    assert 'Write the answer in English by default' in captured['prompt']
    assert 'complete, self-contained LaTeX document' in captured['prompt']
    assert 'Do not add a references or sources section' in captured['prompt']


def test_latex_compilation_retains_source_and_excludes_credentials(config, monkeypatch):
    from credit_savior import solver as module
    from credit_savior.artifacts import atomic_write
    output = config.data_dir / 'tasks/latex-test/answer'
    output.mkdir(parents=True)
    document = r'\documentclass{article}\begin{document}$\int_0^1 x\,dx=1/2$\end{document}'
    captured = {}

    def compile_document(args, **kwargs):
        captured.update(args=args, **kwargs)
        atomic_write(output / '01_report.pdf', b'%PDF-1.7\nsynthetic compiler output')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.shutil, 'which', lambda name: 'tectonic')
    monkeypatch.setattr(module.subprocess, 'run', compile_document)
    monkeypatch.setenv('COOL_PASSWORD', 'test-private-value')
    answer = CodexSolver(config.data_dir).render({'answer_kind': 'files', 'artifacts': [
        {'logical_name': 'report', 'format': 'pdf', 'content': document}]}, output)
    assert (output / '01_report.tex').read_text(encoding='utf-8') == document
    assert '--untrusted' in captured['args']
    assert captured['env']['TECTONIC_UNTRUSTED_MODE'] == '1'
    assert 'COOL_PASSWORD' not in captured['env']
    assert answer.files == (str((output / '01_report.pdf').relative_to(config.data_dir)),)


@pytest.mark.parametrize('command', [r'\input{../../.env}', r'\openin1=../../.env',
                                     r'\write18{some-command}', r'\csname input\endcsname'])
def test_latex_file_access_is_rejected_before_compilation(tmp_path, command):
    with pytest.raises(WorkflowError, match='latex_external_access_unsupported'):
        render_latex_pdf(tmp_path / 'report.pdf', r'\documentclass{article}' + command)
