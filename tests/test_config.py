from pathlib import Path

from village_insight.config import Settings


def test_hermes_is_enabled_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.hermes_enabled is True


def test_csv_settings_are_split(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        import_roots=f"{tmp_path}/one,{tmp_path}/two",
        cors_origins="http://localhost:5173,http://localhost:4173",
    )

    assert settings.import_roots == [tmp_path / "one", tmp_path / "two"]
    assert settings.cors_origins == [
        "http://localhost:5173",
        "http://localhost:4173",
    ]


def test_optional_source_path_manifest_is_resolved(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        source_path_manifest=tmp_path / "source-manifest.json",
    )

    assert settings.resolved_source_path_manifest() == (
        tmp_path / "source-manifest.json"
    ).resolve()
