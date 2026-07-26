from pathlib import Path


def test_generic_source_and_templates_have_no_instance_identifiers() -> None:
    root = Path(__file__).parents[1]
    forbidden = (
        "private-instance-absolute-path",
        "private-product-internal",
        "private/feature-name",
        "private_task_breakdown",
    )
    for base in (root / "src", root / "templates", root / "examples"):
        for path in base.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert not any(item in text for item in forbidden), path
