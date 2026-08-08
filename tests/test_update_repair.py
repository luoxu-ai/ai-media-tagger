from pathlib import Path

from update_repair import APP_EXE_NAME, replace_application


def test_repair_replaces_only_application_executable(tmp_path: Path):
    target = tmp_path / APP_EXE_NAME
    payload = tmp_path / "payload.exe"
    logs = tmp_path / "logs" / "history.log"
    settings = tmp_path / "settings.json"
    logs.parent.mkdir()
    target.write_bytes(b"old" * 400_000)
    payload.write_bytes(b"new" * 400_000)
    logs.write_text("keep log", encoding="utf-8")
    settings.write_text("keep settings", encoding="utf-8")

    replace_application(target, payload)

    assert target.read_bytes() == payload.read_bytes()
    assert logs.read_text(encoding="utf-8") == "keep log"
    assert settings.read_text(encoding="utf-8") == "keep settings"
