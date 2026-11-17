import importlib

import pytest

pytest.importorskip("matplotlib")


@pytest.mark.parametrize("module", ["render_flow", "render_flowchart", "render_request_to_system"])
def test_diagram_renders(module, tmp_path):
    script = importlib.import_module(module)
    out = tmp_path / f"{module}.png"
    script.render(out)
    assert out.stat().st_size > 20_000
