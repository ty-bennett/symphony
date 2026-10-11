import json
from pathlib import Path
from typing import Protocol

from edge_router.feedback.models import LocalExample


class LocalTrainingDataset(Protocol):
    def examples(self) -> list[LocalExample]: ...


class JsonlDataset:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def examples(self) -> list[LocalExample]:
        records = []
        with self.path.open() as file:
            for line_number, line in enumerate(file, 1):
                if not line.strip():
                    continue
                try:
                    records.append(LocalExample.model_validate(json.loads(line)))
                except Exception:
                    # Never echo validation input, which may contain local enterprise data.
                    raise ValueError(f"Invalid training record at line {line_number}") from None
        return records
