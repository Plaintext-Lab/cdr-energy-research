"""Exercise the release step offline, including same-day retries and failures."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def _publish_script() -> str:
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/publish-catalogue.yml"
    step = workflow.read_text(encoding="utf-8").split(
        "      - name: Publish GitHub Release\n", 1
    )[1].split("\n      - ", 1)[0]
    assert "        run: |\n" in step, "Release publishing must run without a blocked action"
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


@pytest.mark.parametrize(
    ("exists", "failure", "operations"),
    [
        (False, "", ["view", "create"]),
        (True, "", ["view", "upload", "edit"]),
        (False, "create", ["view", "create"]),
        (True, "upload", ["view", "upload"]),
        (True, "edit", ["view", "upload", "edit"]),
    ],
)
def test_publish_release(
    tmp_path: Path, exists: bool, failure: str, operations: list[str]
) -> None:
    script = _publish_script()
    cli = tmp_path / "gh"
    cli.write_text(
        f"#!{sys.executable}\n" + textwrap.dedent("""\
        import json
        import os
        import sys

        with open(os.environ["GH_CALLS"], "a", encoding="utf-8") as calls:
            calls.write(json.dumps(sys.argv[1:]) + "\\n")
        operation = sys.argv[2]
        if operation == "view":
            sys.exit(0 if os.environ["RELEASE_EXISTS"] == "1" else 1)
        sys.exit(1 if operation == os.environ["FAIL_COMMAND"] else 0)
        """),
        encoding="utf-8",
    )
    cli.chmod(0o755)
    notes = "Catalogue\n```json\n{}\n```\n$(touch injected)\n"
    env = {
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
        "GH_CALLS": str(tmp_path / "calls.jsonl"),
        "RELEASE_EXISTS": "1" if exists else "0",
        "FAIL_COMMAND": failure,
        "GH_REPO": "example/catalogue",
        "GITHUB_SHA": "a" * 40,
        "RELEASE_TAG": "catalogue-20260913",
        "RELEASE_BODY": notes,
    }
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == (1 if failure else 0), result.stderr
    calls = [json.loads(line) for line in Path(env["GH_CALLS"]).read_text().splitlines()]
    assert [call[1] for call in calls] == operations
    assert not (tmp_path / "injected").exists()
    for call in calls:
        assert call[:3] == ["release", call[1], env["RELEASE_TAG"]]
        if call[1] in {"create", "upload"}:
            assert "dist/catalogue.json.gz" in call
            assert "dist/manifest.json" in call
        if call[1] in {"create", "edit"}:
            assert call[call.index("--title") + 1] == env["RELEASE_TAG"]
            assert call[call.index("--notes") + 1] == notes
            assert "--latest" in call
        if call[1] == "create":
            assert call[call.index("--target") + 1] == env["GITHUB_SHA"]
        if call[1] == "upload":
            assert "--clobber" in call
        if call[1] == "edit":
            assert "--draft=false" in call
