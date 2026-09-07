from datetime import date
import pytest
from algo_trading.data import cache_retention, signal_data


def test_retention_boundary_and_historical_archive_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_retention, "ROOT", tmp_path)
    recent = tmp_path / "data" / "signal-bhavcopy"
    historical = tmp_path / "data" / "bhavcopy"
    recent.mkdir(parents=True)
    historical.mkdir()
    names = ["fo24AUG2026bhav.csv.zip", "fo25AUG2026bhav.csv.zip", "fo07SEP2026bhav.csv.zip", "notes.zip"]
    for name in names:
        (recent / name).write_bytes(b"fixture")
        (historical / name).write_bytes(b"historical evidence")
    removed = cache_retention.prune_signal_cache(date(2026, 9, 7))
    assert removed == [names[0]]
    assert sorted(p.name for p in recent.iterdir()) == sorted(names[1:])
    assert sorted(p.name for p in historical.iterdir()) == sorted(names)
    assert all(p.read_bytes() == b"historical evidence" for p in historical.iterdir())


def test_missing_cache_is_noop_and_invalid_retention_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_retention, "ROOT", tmp_path)
    assert cache_retention.prune_signal_cache(date(2026, 9, 7)) == []
    with pytest.raises(ValueError):
        cache_retention.prune_signal_cache(days=0)


def test_signal_cache_directory_override(tmp_path, monkeypatch):
    from algo_trading.core import backtester
    monkeypatch.setattr(signal_data, "ROOT", tmp_path)
    monkeypatch.setattr(backtester, "_chain_lookups", {})
    monkeypatch.setattr(backtester, "_chain_lookup", None)
    monkeypatch.setenv("SIGNAL_BHAVCOPY_FOLDER", "data/signal-bhavcopy")
    report = signal_data.prepare_signal_data(date(2026, 9, 7), download=False)
    assert report["directory"] == str(tmp_path / "data" / "signal-bhavcopy")
