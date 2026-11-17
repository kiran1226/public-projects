import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DOCS = SCRIPTS.parent / "docs"


def load(name: str):  # type: ignore[no-untyped-def]
    sys.path.insert(0, str(SCRIPTS))  # the scripts import their shared helper by name
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


@pytest.mark.parametrize(
    ("script", "entry", "image"),
    [
        ("render_flow", "render", "agent-flow.png"),
        ("render_flowchart", "render", "agent-flowchart.png"),
        ("render_request_to_system", "main", "request-to-system.png"),
    ],
)
def test_every_readme_diagram_has_a_script_that_renders_it(
    script: str, entry: str, image: str, tmp_path: Path
) -> None:
    out = tmp_path / image
    getattr(load(script), entry)(out)
    assert out.stat().st_size > 50_000
    assert (DOCS / image).exists()  # the rendered image is in the repository
    assert f"docs/{image}" in (SCRIPTS.parent / "README.md").read_text()
