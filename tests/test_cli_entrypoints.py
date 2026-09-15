"""Genuine end-to-end tests of the CLI entry point itself (nse.cli.main()
via sys.argv), not just the underlying functions -- these catch argparse-
wiring mistakes (a subcommand never getting `.set_defaults(func=...)`, a
typo in a flag name) that calling cmd_site()/cmd_verify_bundle() directly
would not.
"""

import json

import pytest

import nse.cli as cli_mod

from tests.test_sitebuilder_publish_gate import _synthetic_prices, _wire_cli


def test_main_site_end_to_end_via_argv(tmp_path, monkeypatch):
    symbols = [f"SYM{i}" for i in range(5)]
    prices = _synthetic_prices(symbols)
    _wire_cli(monkeypatch, symbols, prices, prices["SYM0"])

    out_dir = tmp_path / "site"
    monkeypatch.setattr("sys.argv", ["nse-scan", "site", "--out", str(out_dir), "--top", "5"])
    cli_mod.main()

    manifest = json.loads((out_dir / "data" / "manifest.json").read_text())
    assert manifest["universe_size"] == 5


def test_main_verify_bundle_end_to_end_via_argv(tmp_path, monkeypatch):
    symbols = [f"SYM{i}" for i in range(5)]
    prices = _synthetic_prices(symbols)
    _wire_cli(monkeypatch, symbols, prices, prices["SYM0"])

    out_dir = tmp_path / "site"
    monkeypatch.setattr("sys.argv", ["nse-scan", "site", "--out", str(out_dir), "--top", "5"])
    cli_mod.main()

    # Now drive verify-bundle through the real CLI dispatch, on the bundle
    # the real CLI dispatch of `site` just wrote -- a true round trip.
    monkeypatch.setattr("sys.argv", ["nse-scan", "verify-bundle", str(out_dir)])
    cli_mod.main()  # must not raise/exit non-zero


def test_main_verify_bundle_exits_nonzero_via_argv_on_a_bad_bundle(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    # No files at all -- verify_bundle() must fail this, and main() must
    # propagate that as a non-zero process exit for CI to catch.
    monkeypatch.setattr("sys.argv", ["nse-scan", "verify-bundle", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code != 0


def test_main_unknown_command_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["nse-scan", "not-a-real-command"])
    with pytest.raises(SystemExit):
        cli_mod.main()
