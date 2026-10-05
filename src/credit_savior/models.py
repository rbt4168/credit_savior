from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from .artifacts import canonical_hash


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class Course:
    course_id: str
    name: str
    url: str
    student: bool = True
    course_code: str | None = None


@dataclass(frozen=True)
class Attachment:
    file_id: str
    name: str
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Submission:
    presence: str = "unknown"
    text: str | None = None
    files: tuple[str, ...] = ()
    attempt_id: str | None = None
    submitted_at_ms: int | None = None
    file_hashes: tuple[str, ...] = ()

    def __post_init__(self):
        if self.presence not in ("present", "absent", "unknown"):
            raise ValueError("invalid_submission_presence")


@dataclass(frozen=True)
class Assignment:
    course_id: str
    assignment_id: str
    url: str
    title: str
    prompt: str
    submission_types: tuple[str, ...]
    extensions: tuple[str, ...] = ()
    due_at_ms: int | None = None
    unlock_at_ms: int | None = None
    lock_at_ms: int | None = None
    rubric: str = ""
    attachments: tuple[Attachment, ...] = ()
    submission: Submission = field(default_factory=Submission)
    unsupported: str | None = None

    @property
    def content_hash(self) -> str:
        value = asdict(self)
        for key in ("url", "submission", "unsupported"):
            value.pop(key)
        for attachment in value["attachments"]:
            attachment.pop("path")
            attachment.pop("name")
        value["attachments"] = sorted(value["attachments"], key=lambda x: x["file_id"])
        return canonical_hash(value)

    def eligibility(self, at_ms: int) -> str:
        if self.submission.presence == "present":
            return "already_submitted"
        if self.submission.presence != "absent":
            return "submission_state_unknown"
        if self.due_at_ms is not None and at_ms >= self.due_at_ms:
            return "assignment_expired"
        if self.lock_at_ms is not None and at_ms >= self.lock_at_ms:
            return "assignment_closed"
        if self.unlock_at_ms is not None and at_ms < self.unlock_at_ms:
            return "not_open"
        if self.unsupported:
            return self.unsupported
        if not self.prompt.strip() and not self.attachments:
            return "missing_problem_material"
        if not set(self.submission_types).intersection({"online_text_entry", "online_upload"}):
            return "unsupported_submission_type"
        return "eligible"

    def to_dict(self) -> dict:
        return {"schema_version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, value: dict) -> Assignment:
        fields = {key: val for key, val in value.items() if key != "schema_version"}
        fields["attachments"] = tuple(Attachment(**a) for a in fields.get("attachments", []))
        submission = dict(fields.get("submission", {}))
        submission["files"] = tuple(submission.get("files", ()))
        submission["file_hashes"] = tuple(submission.get("file_hashes", ()))
        fields["submission"] = Submission(**submission)
        fields["submission_types"] = tuple(fields["submission_types"])
        fields["extensions"] = tuple(fields.get("extensions", ()))
        return cls(**fields)


@dataclass(frozen=True)
class Video:
    course_id: str
    video_id: str
    url: str
    revision: str
    duration_s: float | None = None
    player_kind: str = "html5"
    order: int = 0
    completion: str = "unknown"
    title: str = ''

    def to_dict(self) -> dict:
        return {"schema_version": 1, **asdict(self)}


@dataclass(frozen=True)
class Answer:
    kind: str
    text: str = ""
    files: tuple[str, ...] = ()
    hashes: tuple[str, ...] = ()
    model: str = "gpt-6.1-sol"

    @property
    def answer_hash(self) -> str:
        return canonical_hash({"kind": self.kind, "text": self.text, "hashes": self.hashes})

    def to_dict(self) -> dict:
        return {"schema_version": 1, **asdict(self), "answer_hash": self.answer_hash}

    @classmethod
    def from_dict(cls, value: dict) -> Answer:
        return cls(kind=value["kind"], text=value.get("text", ""),
                   files=tuple(value.get("files", ())), hashes=tuple(value.get("hashes", ())),
                   model=value.get("model", "gpt-6.1-sol"))
