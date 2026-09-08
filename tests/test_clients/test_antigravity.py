"""
Tests for Google Antigravity client manager
"""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from mcpm.clients.client_registry import ClientRegistry
from mcpm.clients.managers.antigravity import AntigravityManager, _strip_jsonc
from mcpm.commands.client import client, edit_client
from mcpm.core.schema import CustomServerConfig, RemoteServerConfig, STDIOServerConfig


@pytest.fixture
def temp_config_file(tmp_path):
    """Return path to a temporary Antigravity config file."""
    return str(tmp_path / "mcp_config.json")


@pytest.fixture
def antigravity_manager(temp_config_file):
    """Create an AntigravityManager with a temp config path."""
    return AntigravityManager(config_path_override=temp_config_file)


# ==============================================================================
# 1. Initialization and properties
# ==============================================================================


def test_antigravity_manager_initialization():
    """Test default initialization and attributes."""
    manager = AntigravityManager()
    assert manager.client_key == "antigravity"
    assert manager.display_name == "Antigravity"
    assert manager.download_url == "https://antigravity.google"
    assert manager.configure_key_name == "mcpServers"
    expected_path = str(Path.home() / ".gemini" / "config" / "mcp_config.json")
    assert manager.config_path == expected_path


def test_antigravity_manager_custom_config_path():
    """Test custom config path override."""
    custom = "/custom/mcp_config.json"
    manager = AntigravityManager(config_path_override=custom)
    assert manager.config_path == custom


def test_antigravity_manager_empty_config():
    """Test _get_empty_config returns expected structure."""
    manager = AntigravityManager()
    empty = manager._get_empty_config()
    assert empty == {"mcpServers": {}}


def test_antigravity_manager_client_info(antigravity_manager):
    """Test get_client_info dictionary."""
    info = antigravity_manager.get_client_info()
    assert info["name"] == "Antigravity"
    assert info["download_url"] == "https://antigravity.google"
    assert info["config_file"] == antigravity_manager.config_path
    assert "Antigravity" in info["description"]


# ==============================================================================
# 2. Client Detection (is_client_installed)
# ==============================================================================


def test_is_client_installed_via_agy_binary():
    """Detect when 'agy' is on PATH."""
    manager = AntigravityManager(config_path_override="/nonexistent/mcp_config.json")
    with patch("shutil.which", side_effect=lambda cmd: "/usr/local/bin/agy" if cmd == "agy" else None):
        assert manager.is_client_installed() is True


def test_is_client_installed_via_antigravity_binary():
    """Detect when 'antigravity' is on PATH."""
    manager = AntigravityManager(config_path_override="/nonexistent/mcp_config.json")
    with patch("shutil.which", side_effect=lambda cmd: "/usr/local/bin/antigravity" if cmd == "antigravity" else None):
        assert manager.is_client_installed() is True


def test_is_client_installed_via_directory(tmp_path, monkeypatch):
    """Detect when ~/.gemini/antigravity or ~/.gemini/antigravity-cli exists."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    manager = AntigravityManager(config_path_override=str(tmp_path / "nonexistent" / "mcp_config.json"))
    manager._system = "Linux"

    with patch("shutil.which", return_value=None):
        # Neither directory exists yet
        assert manager.is_client_installed() is False

        # Create antigravity-cli directory
        cli_dir = fake_home / ".gemini" / "antigravity-cli"
        cli_dir.mkdir(parents=True)
        assert manager.is_client_installed() is True


def test_is_client_installed_mac_app(tmp_path, monkeypatch):
    """Detect when /Applications/Antigravity.app exists on macOS."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    manager = AntigravityManager(config_path_override=str(tmp_path / "nonexistent" / "mcp_config.json"))
    manager._system = "Darwin"

    with patch("shutil.which", return_value=None):
        with patch("os.path.exists", side_effect=lambda p: p == "/Applications/Antigravity.app"):
            assert manager.is_client_installed() is True


def test_is_client_installed_fallback_config_dir(tmp_path, monkeypatch):
    """Detect when config dir exists as fallback."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_dir = tmp_path / "gemini_config"
    config_dir.mkdir()
    manager = AntigravityManager(config_path_override=str(config_dir / "mcp_config.json"))

    with patch("shutil.which", return_value=None):
        assert manager.is_client_installed() is True


def test_is_client_installed_none(tmp_path, monkeypatch):
    """Return False when client is not installed."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    manager = AntigravityManager(config_path_override=str(tmp_path / "nonexistent_dir" / "mcp_config.json"))
    manager._system = "Linux"

    with patch("shutil.which", return_value=None):
        assert manager.is_client_installed() is False


# ==============================================================================
# 3. JSONC stripping & Config Loading
# ==============================================================================


def test_strip_jsonc():
    """Test _strip_jsonc handles single-line, multi-line comments and trailing commas."""
    jsonc_text = """
    {
      // single line comment
      /* multi-line
         comment */
      "mcpServers": {
        "server1": {
          "command": "python", // inline comment
          "args": [
            "run.py",
            "--port",
            "8080",
          ],
        },
      },
    }
    """
    cleaned = _strip_jsonc(jsonc_text)
    data = json.loads(cleaned)
    assert "mcpServers" in data
    assert "server1" in data["mcpServers"]
    assert data["mcpServers"]["server1"]["args"] == ["run.py", "--port", "8080"]


def test_strip_jsonc_preserves_url_tokens():
    """Test _strip_jsonc preserves '//' and '/*' inside quoted strings."""
    jsonc_text = """
    {
      "mcpServers": {
        "remote": {
          "serverUrl": "https://example.com/api//v1/*pattern*/sse"
        }
      }
    }
    """
    cleaned = _strip_jsonc(jsonc_text)
    data = json.loads(cleaned)
    assert data["mcpServers"]["remote"]["serverUrl"] == "https://example.com/api//v1/*pattern*/sse"


def test_load_config_nonexistent(antigravity_manager):
    """Nonexistent config file returns empty config."""
    config = antigravity_manager._load_config()
    assert config == {"mcpServers": {}}


def test_load_config_empty_file(temp_config_file, antigravity_manager):
    """Zero-byte file returns empty config without error."""
    with open(temp_config_file, "w") as f:
        f.write("")
    config = antigravity_manager._load_config()
    assert config == {"mcpServers": {}}


def test_load_config_corrupt_file_creates_backup(temp_config_file, antigravity_manager):
    """Corrupt config creates .bak backup and returns empty config."""
    with open(temp_config_file, "w") as f:
        f.write("{ invalid json")

    config = antigravity_manager._load_config()
    assert config == {"mcpServers": {}}
    assert os.path.exists(f"{temp_config_file}.bak")


def test_save_and_load_config(temp_config_file, antigravity_manager):
    """Save config and load it back successfully."""
    test_config = {
        "mcpServers": {
            "test": {
                "command": "echo",
                "args": ["hi"],
            }
        }
    }
    assert antigravity_manager._save_config(test_config) is True

    loaded = antigravity_manager._load_config()
    assert loaded["mcpServers"]["test"]["command"] == "echo"
    assert loaded["mcpServers"]["test"]["args"] == ["hi"]


# ==============================================================================
# 4. Format Conversion: to_client_format & from_client_format
# ==============================================================================


def test_to_client_format_stdio():
    """Convert STDIOServerConfig to Antigravity format."""
    manager = AntigravityManager()
    server = STDIOServerConfig(
        name="test-stdio",
        command="node",
        args=["server.js", "--verbose"],
        env={"PORT": "3000"},
    )
    result = manager.to_client_format(server)
    assert result == {
        "command": "node",
        "args": ["server.js", "--verbose"],
        "env": {"PORT": "3000"},
    }


def test_to_client_format_stdio_disabled():
    """Convert disabled STDIOServerConfig sets disabled: true."""
    manager = AntigravityManager()
    server = STDIOServerConfig(
        name="test-stdio",
        command="node",
        args=["server.js"],
        enabled=False,
    )
    result = manager.to_client_format(server)
    assert result["disabled"] is True


def test_to_client_format_stdio_enabled():
    """Convert enabled STDIOServerConfig sets disabled: false."""
    manager = AntigravityManager()
    server = STDIOServerConfig(
        name="test-stdio",
        command="node",
        args=["server.js"],
        enabled=True,
    )
    result = manager.to_client_format(server)
    assert result["disabled"] is False


def test_to_client_format_remote():
    """Convert RemoteServerConfig to Antigravity format using serverUrl."""
    manager = AntigravityManager()
    server = RemoteServerConfig(
        name="test-remote",
        url="https://api.example.com/sse",
        headers={"Authorization": "Bearer tok"},
    )
    result = manager.to_client_format(server)
    assert result == {
        "serverUrl": "https://api.example.com/sse",
        "headers": {"Authorization": "Bearer tok"},
    }


def test_to_client_format_remote_disabled():
    """Convert disabled RemoteServerConfig sets disabled: true."""
    manager = AntigravityManager()
    server = RemoteServerConfig(
        name="test-remote",
        url="https://api.example.com/sse",
        enabled=False,
    )
    result = manager.to_client_format(server)
    assert result["serverUrl"] == "https://api.example.com/sse"
    assert result["disabled"] is True


def test_to_client_format_custom():
    """Convert CustomServerConfig passes config through."""
    manager = AntigravityManager()
    server = CustomServerConfig(
        name="custom-srv",
        config={"customField": 123},
    )
    result = manager.to_client_format(server)
    assert result == {"customField": 123}


def test_from_client_format_stdio():
    """Parse stdio Antigravity config to STDIOServerConfig."""
    client_config = {
        "command": "python",
        "args": ["-m", "mcp_server"],
        "env": {"DEBUG": "1"},
    }
    server = AntigravityManager.from_client_format("my-stdio", client_config)
    assert isinstance(server, STDIOServerConfig)
    assert server.name == "my-stdio"
    assert server.command == "python"
    assert server.args == ["-m", "mcp_server"]
    assert server.env == {"DEBUG": "1"}
    assert server.enabled is None


def test_from_client_format_stdio_disabled():
    """Parse stdio Antigravity config with disabled: true sets enabled: False."""
    client_config = {
        "command": "python",
        "args": ["-m", "mcp_server"],
        "disabled": True,
    }
    server = AntigravityManager.from_client_format("my-stdio", client_config)
    assert isinstance(server, STDIOServerConfig)
    assert server.enabled is False


def test_from_client_format_stdio_enabled():
    """Parse stdio Antigravity config with disabled: false sets enabled: True."""
    client_config = {
        "command": "python",
        "args": ["-m", "mcp_server"],
        "disabled": False,
    }
    server = AntigravityManager.from_client_format("my-stdio", client_config)
    assert isinstance(server, STDIOServerConfig)
    assert server.enabled is True


def test_from_client_format_remote_server_url():
    """Parse remote config with serverUrl to RemoteServerConfig."""
    client_config = {
        "serverUrl": "https://mcp.example.com/sse",
        "headers": {"X-Key": "secret"},
    }
    server = AntigravityManager.from_client_format("my-remote", client_config)
    assert isinstance(server, RemoteServerConfig)
    assert server.name == "my-remote"
    assert server.url == "https://mcp.example.com/sse"
    assert server.headers == {"X-Key": "secret"}
    assert server.enabled is None


def test_from_client_format_remote_url():
    """Parse remote config with url to RemoteServerConfig."""
    client_config = {
        "url": "https://mcp.example.com/sse",
    }
    server = AntigravityManager.from_client_format("my-remote", client_config)
    assert isinstance(server, RemoteServerConfig)
    assert server.name == "my-remote"
    assert server.url == "https://mcp.example.com/sse"


def test_from_client_format_remote_disabled():
    """Parse remote config with disabled: true sets enabled: False."""
    client_config = {
        "serverUrl": "https://mcp.example.com/sse",
        "disabled": True,
    }
    server = AntigravityManager.from_client_format("my-remote", client_config)
    assert isinstance(server, RemoteServerConfig)
    assert server.enabled is False


def test_from_client_format_command_list():
    """Parse stdio config where command is given as a list."""
    client_config = {
        "command": ["uvx", "mcp-server-git"],
    }
    server = AntigravityManager.from_client_format("git-mcp", client_config)
    assert isinstance(server, STDIOServerConfig)
    assert server.command == "uvx"
    assert server.args == ["mcp-server-git"]


def test_from_client_format_custom():
    """Parse unknown/custom config to CustomServerConfig."""
    client_config = {
        "nonStandardKey": "value",
    }
    server = AntigravityManager.from_client_format("custom", client_config)
    assert isinstance(server, CustomServerConfig)
    assert server.name == "custom"
    assert server.config == client_config


# ==============================================================================
# 5. Server Management Operations
# ==============================================================================


def test_add_get_list_remove_server(antigravity_manager):
    """Test full lifecycle: add, get, list, and remove server."""
    server = STDIOServerConfig(
        name="test-server",
        command="node",
        args=["index.js"],
    )

    # Add
    assert antigravity_manager.add_server(server) is True

    # List
    servers = antigravity_manager.list_servers()
    assert "test-server" in servers

    # Get
    retrieved = antigravity_manager.get_server("test-server")
    assert retrieved is not None
    assert retrieved.name == "test-server"
    assert retrieved.command == "node"
    assert retrieved.args == ["index.js"]

    # Remove
    assert antigravity_manager.remove_server("test-server") is True
    assert "test-server" not in antigravity_manager.list_servers()
    assert antigravity_manager.get_server("test-server") is None


def test_disable_enable_server(antigravity_manager):
    """Test disabling and enabling a server."""
    server = STDIOServerConfig(
        name="toggle-srv",
        command="echo",
        args=["hello"],
    )
    antigravity_manager.add_server(server)
    assert antigravity_manager.is_server_disabled("toggle-srv") is False

    # Disable
    assert antigravity_manager.disable_server("toggle-srv") is True
    assert antigravity_manager.is_server_disabled("toggle-srv") is True

    # Check file content
    config = antigravity_manager._load_config()
    assert config["mcpServers"]["toggle-srv"]["disabled"] is True

    # Enable
    assert antigravity_manager.enable_server("toggle-srv") is True
    assert antigravity_manager.is_server_disabled("toggle-srv") is False

    # Check file content
    config = antigravity_manager._load_config()
    assert config["mcpServers"]["toggle-srv"].get("disabled") is not True


def test_disable_nonexistent_server(antigravity_manager):
    """Disabling nonexistent server returns False."""
    assert antigravity_manager.disable_server("nonexistent") is False


def test_enable_nonexistent_server(antigravity_manager):
    """Enabling nonexistent server returns False."""
    assert antigravity_manager.enable_server("nonexistent") is False


# ==============================================================================
# 6. ClientRegistry Integration & Aliases
# ==============================================================================


def test_client_registry_antigravity_registration():
    """Verify 'antigravity' is registered in ClientRegistry."""
    supported = ClientRegistry.get_supported_clients()
    assert "antigravity" in supported

    manager = ClientRegistry.get_client_manager("antigravity")
    assert isinstance(manager, AntigravityManager)
    assert manager.client_key == "antigravity"


def test_client_registry_agy_alias():
    """Verify 'agy' works as an alias for 'antigravity' in ClientRegistry."""
    manager = ClientRegistry.get_client_manager("agy")
    assert isinstance(manager, AntigravityManager)
    assert manager.client_key == "antigravity"

    info = ClientRegistry.get_client_info("agy")
    assert info["name"] == "Antigravity"
    assert info["download_url"] == "https://antigravity.google"


# ==============================================================================
# 7. CLI Command Integration
# ==============================================================================


def test_client_edit_antigravity_add_server(monkeypatch, tmp_path):
    """Test mcpm client edit antigravity --add-server."""
    cfg_path = str(tmp_path / "mcp_config.json")
    mock_server = STDIOServerConfig(name="test-tool", command="python", args=["tool.py"])

    mock_global_config = Mock()
    mock_global_config.list_servers.return_value = {"test-tool": mock_server}
    mock_global_config.get_server.return_value = mock_server
    monkeypatch.setattr("mcpm.commands.client.global_config_manager", mock_global_config)

    manager = AntigravityManager(config_path_override=cfg_path)
    monkeypatch.setattr("mcpm.commands.client.ClientRegistry.get_client_manager", Mock(return_value=manager))
    monkeypatch.setattr(
        "mcpm.commands.client.ClientRegistry.get_client_info", Mock(return_value={"name": "Antigravity"})
    )

    runner = CliRunner()
    result = runner.invoke(edit_client, ["antigravity", "--add-server", "test-tool", "--force"])

    assert result.exit_code == 0
    assert "Successfully updated" in result.output

    with open(cfg_path) as f:
        data = json.load(f)

    assert "mcpm_test-tool" in data["mcpServers"]
    assert data["mcpServers"]["mcpm_test-tool"]["command"] == "mcpm"
    assert data["mcpServers"]["mcpm_test-tool"]["args"] == ["run", "test-tool"]


def test_client_edit_antigravity_add_server_disabled(monkeypatch, tmp_path):
    """Test mcpm client edit antigravity --add-server --disabled."""
    cfg_path = str(tmp_path / "mcp_config.json")
    mock_server = STDIOServerConfig(name="test-tool", command="python", args=["tool.py"])

    mock_global_config = Mock()
    mock_global_config.list_servers.return_value = {"test-tool": mock_server}
    mock_global_config.get_server.return_value = mock_server
    monkeypatch.setattr("mcpm.commands.client.global_config_manager", mock_global_config)

    manager = AntigravityManager(config_path_override=cfg_path)
    monkeypatch.setattr("mcpm.commands.client.ClientRegistry.get_client_manager", Mock(return_value=manager))
    monkeypatch.setattr(
        "mcpm.commands.client.ClientRegistry.get_client_info", Mock(return_value={"name": "Antigravity"})
    )

    runner = CliRunner()
    result = runner.invoke(edit_client, ["antigravity", "--add-server", "test-tool", "--disabled", "--force"])

    assert result.exit_code == 0
    assert "Successfully updated" in result.output

    with open(cfg_path) as f:
        data = json.load(f)

    assert "mcpm_test-tool" in data["mcpServers"]
    assert data["mcpServers"]["mcpm_test-tool"]["disabled"] is True


def test_client_edit_agy_alias(monkeypatch, tmp_path):
    """Test mcpm client edit agy resolves to Antigravity."""
    cfg_path = str(tmp_path / "mcp_config.json")
    mock_server = STDIOServerConfig(name="srv1", command="echo", args=["1"])

    mock_global_config = Mock()
    mock_global_config.list_servers.return_value = {"srv1": mock_server}
    mock_global_config.get_server.return_value = mock_server
    monkeypatch.setattr("mcpm.commands.client.global_config_manager", mock_global_config)

    manager = AntigravityManager(config_path_override=cfg_path)
    monkeypatch.setattr(
        "mcpm.commands.client.ClientRegistry.get_client_manager",
        lambda name, **kw: manager if name in ("antigravity", "agy") else None,
    )
    monkeypatch.setattr(
        "mcpm.commands.client.ClientRegistry.get_client_info", Mock(return_value={"name": "Antigravity"})
    )

    runner = CliRunner()
    result = runner.invoke(edit_client, ["agy", "--add-server", "srv1", "--force"])

    assert result.exit_code == 0
    assert "Successfully updated" in result.output


def test_client_ls_shows_antigravity(monkeypatch):
    """Test client ls includes Antigravity."""
    installed = {"antigravity": True}
    monkeypatch.setattr("mcpm.commands.client.ClientRegistry.get_supported_clients", Mock(return_value=["antigravity"]))
    monkeypatch.setattr("mcpm.commands.client.ClientRegistry.detect_installed_clients", Mock(return_value=installed))
    monkeypatch.setattr(
        "mcpm.commands.client.ClientRegistry.get_client_info",
        Mock(return_value={"name": "Antigravity", "download_url": "https://antigravity.google"}),
    )

    mock_manager = Mock()
    mock_manager.get_servers.return_value = {}
    monkeypatch.setattr("mcpm.commands.client.ClientRegistry.get_client_manager", Mock(return_value=mock_manager))

    runner = CliRunner()
    result = runner.invoke(client, ["ls"])

    assert result.exit_code == 0
    assert "Antigravity" in result.output
    assert "antigravity" in result.output
