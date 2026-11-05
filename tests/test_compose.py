"""What `compose.yaml` must say, each from a failure seen starting the real containers."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_the_ocr_model_survives_an_empty_value_in_env_file() -> None:
    # `.env.example` sets PE_TESSDATA_DIR empty, and env_file beats the image's ENV, so OCR would
    # download its model at run time unless `environment`, which beats env_file, names it.
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]

    assert "PE_TESSDATA_DIR=\n" in (ROOT / ".env.example").read_text()
    for name in ("api", "pipeline"):
        assert services[name]["environment"]["PE_TESSDATA_DIR"] == "/opt/tessdata"
        dockerfile = (ROOT / "docker" / f"{name}.Dockerfile").read_text()
        assert "/opt/tessdata/eng.traineddata" in dockerfile
