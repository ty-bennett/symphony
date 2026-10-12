"""Run directly from the repository, without reinstalling the editable package."""

import sys
from pathlib import Path


def main():
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / "src"))
    from edge_router.testing.cli import main as run

    return run(project_root=project)


if __name__ == "__main__":
    raise SystemExit(main())
