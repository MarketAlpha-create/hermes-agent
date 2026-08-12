"""Managed-mode detection across the Nix install shapes.

Hermes can be owned by the NixOS module (a system service) or by the Home
Manager module (a per-user service). Both refuse config mutations the CLI
cannot persist, but they are rebuilt by different commands, so the messages
have to name the right one — telling a Home Manager user to run
`sudo nixos-rebuild switch` sends them to a file that does not exist.
"""

import os
from pathlib import Path

import pytest

from hermes_cli import config as config_mod


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    return home


def _managed_system(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("HERMES_MANAGED", raising=False)
    else:
        monkeypatch.setenv("HERMES_MANAGED", value)
    return config_mod.get_managed_system()


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("true", "NixOS"),
        ("1", "NixOS"),
        ("nixos", "NixOS"),
        ("nix", "NixOS"),
        ("home-manager", "Home Manager"),
        ("HOME-MANAGER", "Home Manager"),
        ("homemanager", "Home Manager"),
    ],
)
def test_env_var_resolves_to_a_managed_system(hermes_home, monkeypatch, env_value, expected):
    assert _managed_system(monkeypatch, env_value) == expected


def test_unmanaged_install_has_no_managed_system(hermes_home, monkeypatch):
    assert _managed_system(monkeypatch, None) is None
    assert config_mod.is_managed() is False


@pytest.mark.parametrize(
    ("marker_text", "expected"),
    [
        # Historic markers were empty and always meant NixOS.
        ("", "NixOS"),
        ("nixos", "NixOS"),
        ("home-manager", "Home Manager"),
        # A marker naming something we do not recognise still means managed;
        # NixOS is the safe answer because it is the older shape.
        ("something-else", "NixOS"),
    ],
)
def test_marker_file_names_the_managing_system(hermes_home, monkeypatch, marker_text, expected):
    """An interactive shell sees .managed but not the service's HERMES_MANAGED."""
    (hermes_home / ".managed").write_text(marker_text)
    monkeypatch.delenv("HERMES_MANAGED", raising=False)

    assert config_mod.get_managed_system() == expected
    assert config_mod.is_managed() is True


def test_env_var_wins_over_the_marker(hermes_home, monkeypatch):
    (hermes_home / ".managed").write_text("nixos")
    monkeypatch.setenv("HERMES_MANAGED", "home-manager")

    assert config_mod.get_managed_system() == "Home Manager"


@pytest.mark.parametrize(
    ("managed_value", "rebuild_command", "wrong_command"),
    [
        ("nixos", "sudo nixos-rebuild switch", "home-manager switch"),
        ("home-manager", "home-manager switch", "nixos-rebuild"),
    ],
)
def test_refusal_names_the_matching_rebuild_command(
    hermes_home, monkeypatch, managed_value, rebuild_command, wrong_command
):
    monkeypatch.setenv("HERMES_MANAGED", managed_value)

    message = config_mod.format_managed_message("set model")

    assert rebuild_command in message
    assert wrong_command not in message
    # The action and the option to edit are what make the message actionable.
    assert "set model" in message
    assert "services.hermes-agent.settings" in message


def test_both_nix_shapes_offer_an_update_command(hermes_home, monkeypatch):
    for value in ("nixos", "home-manager"):
        monkeypatch.setenv("HERMES_MANAGED", value)
        assert config_mod.get_managed_update_command()


def test_unmanaged_install_offers_no_update_command(hermes_home, monkeypatch):
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    assert config_mod.get_managed_update_command() is None


def test_home_manager_home_missing_points_at_home_manager_switch(tmp_path, monkeypatch):
    """The bootstrap error is the first thing a misconfigured user sees."""
    missing = tmp_path / "never-activated"
    monkeypatch.setenv("HERMES_HOME", str(missing))
    monkeypatch.setenv("HERMES_MANAGED", "home-manager")

    with pytest.raises(RuntimeError) as excinfo:
        config_mod._ensure_hermes_home_managed(Path(missing))

    assert "home-manager switch" in str(excinfo.value)
    assert "nixos-rebuild" not in str(excinfo.value)


def test_nixos_home_missing_points_at_nixos_rebuild(tmp_path, monkeypatch):
    missing = tmp_path / "never-activated"
    monkeypatch.setenv("HERMES_HOME", str(missing))
    monkeypatch.setenv("HERMES_MANAGED", "true")

    with pytest.raises(RuntimeError) as excinfo:
        config_mod._ensure_hermes_home_managed(Path(missing))

    assert "sudo nixos-rebuild switch" in str(excinfo.value)


def test_homebrew_values_are_not_treated_as_managed(hermes_home, monkeypatch):
    """Homebrew is no longer a distribution method; those values must not block writes."""
    for value in ("brew", "homebrew"):
        monkeypatch.setenv("HERMES_MANAGED", value)
        assert config_mod.get_managed_system() is None


def test_unreadable_marker_still_reports_managed(hermes_home, monkeypatch):
    """A marker we cannot read is still a marker — fail closed, not open."""
    marker = hermes_home / ".managed"
    marker.write_text("home-manager")
    marker.chmod(0o000)
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    try:
        if os.access(marker, os.R_OK):
            pytest.skip("running as a user that ignores file modes (e.g. root)")
        assert config_mod.get_managed_system() == "NixOS"
        assert config_mod.is_managed() is True
    finally:
        marker.chmod(0o600)
