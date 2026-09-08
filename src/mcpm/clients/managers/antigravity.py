"""
Antigravity integration utilities for MCP
https://antigravity.google
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from mcpm.clients.base import JSONClientManager
from mcpm.core.schema import CustomServerConfig, RemoteServerConfig, ServerConfig, STDIOServerConfig

logger = logging.getLogger(__name__)


def _strip_jsonc(text: str) -> str:
    """Remove single-line comments, multi-line comments, and trailing commas from JSONC text.

    Walks character-by-character to respect string boundaries so that
    comment tokens inside quoted values (e.g. URLs) are preserved.
    """
    result = []
    i = 0
    length = len(text)
    while i < length:
        c = text[i]
        if c == '"':
            # Consume entire quoted string (respecting backslash escapes)
            result.append(c)
            i += 1
            while i < length:
                sc = text[i]
                result.append(sc)
                if sc == "\\" and i + 1 < length:
                    i += 1
                    result.append(text[i])
                elif sc == '"':
                    break
                i += 1
        elif c == "/" and i + 1 < length and text[i + 1] == "/":
            # Skip single-line comment until end of line
            i += 2
            while i < length and text[i] != "\n":
                i += 1
            continue
        elif c == "/" and i + 1 < length and text[i + 1] == "*":
            # Skip multi-line comment until */
            i += 2
            while i + 1 < length and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        elif c == ",":
            # Trailing comma: scan ahead past whitespace and comments for } or ]
            j = i + 1
            while j < length:
                if text[j] in " \t\r\n":
                    j += 1
                elif text[j] == "/" and j + 1 < length and text[j + 1] == "/":
                    j += 2
                    while j < length and text[j] != "\n":
                        j += 1
                elif text[j] == "/" and j + 1 < length and text[j + 1] == "*":
                    j += 2
                    while j + 1 < length and not (text[j] == "*" and text[j + 1] == "/"):
                        j += 1
                    j += 2
                else:
                    break
            if j < length and text[j] in "}]":
                i += 1
                continue
            result.append(c)
        else:
            result.append(c)
        i += 1
    return "".join(result)


class AntigravityManager(JSONClientManager):
    """Manages Google Antigravity MCP server configurations"""

    # Client information
    client_key = "antigravity"
    display_name = "Antigravity"
    download_url = "https://antigravity.google"
    configure_key_name = "mcpServers"

    def __init__(self, config_path_override: Optional[str] = None):
        """Initialize the Antigravity client manager

        Args:
            config_path_override: Optional path to override the default config file location
        """
        super().__init__(config_path_override=config_path_override)

        if config_path_override:
            self.config_path = config_path_override
        else:
            # Antigravity stores its global MCP config in ~/.gemini/config/mcp_config.json
            self.config_path = str(Path.home() / ".gemini" / "config" / "mcp_config.json")

    def _get_empty_config(self) -> Dict[str, Any]:
        """Get empty config structure for Antigravity"""
        return {self.configure_key_name: {}}

    def _load_config(self) -> Dict[str, Any]:
        """Load Antigravity client configuration file, handling JSONC comments and trailing commas."""
        empty_config = self._get_empty_config()

        if not os.path.exists(self.config_path):
            logger.debug(f"Client config file not found at: {self.config_path}")
            return empty_config

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw = f.read()

            if not raw.strip():
                return empty_config

            stripped = _strip_jsonc(raw)
            if not stripped.strip():
                return empty_config

            config = json.loads(stripped)
            if self.configure_key_name not in config:
                config[self.configure_key_name] = {}
            return config
        except json.JSONDecodeError:
            logger.error(f"Error parsing Antigravity config: {self.config_path}")
            if os.path.exists(self.config_path):
                backup_path = f"{self.config_path}.bak"
                try:
                    os.rename(self.config_path, backup_path)
                    logger.info(f"Backed up corrupt config file to: {backup_path}")
                except Exception as e:
                    logger.error(f"Failed to backup corrupt file: {str(e)}")
            return empty_config

    def is_client_installed(self) -> bool:
        """Check if Antigravity is installed

        Returns:
            bool: True if agy or antigravity command is available, or Antigravity directories exist
        """
        # Check if agy or antigravity binary is on PATH
        if shutil.which("agy") is not None or shutil.which("antigravity") is not None:
            return True

        # Check common Antigravity directory locations
        gemini_dir = Path.home() / ".gemini"
        if (gemini_dir / "antigravity").is_dir() or (gemini_dir / "antigravity-cli").is_dir():
            return True

        # Check macOS application bundle
        if self._system == "Darwin":
            if os.path.exists("/Applications/Antigravity.app") or os.path.exists(
                str(Path.home() / "Applications" / "Antigravity.app")
            ):
                return True

        # Fallback: check if the config directory exists
        return os.path.isdir(os.path.dirname(self.config_path))

    def get_client_info(self) -> Dict[str, str]:
        """Get information about this client

        Returns:
            Dict: Information about the client including display name, download URL, and config path
        """
        return {
            "name": self.display_name,
            "download_url": self.download_url,
            "config_file": self.config_path,
            "description": "Google's Antigravity AI-first development platform",
        }

    def to_client_format(self, server_config: ServerConfig) -> Dict[str, Any]:
        """Convert ServerConfig to Antigravity-specific format"""
        if isinstance(server_config, STDIOServerConfig):
            result: Dict[str, Any] = {
                "command": server_config.command,
                "args": server_config.args,
            }

            non_empty_env = server_config.get_filtered_env_vars(os.environ)
            if non_empty_env:
                result["env"] = non_empty_env
        elif isinstance(server_config, RemoteServerConfig):
            result = {
                "serverUrl": server_config.url,
            }
            if server_config.headers:
                result["headers"] = server_config.headers
        elif isinstance(server_config, CustomServerConfig):
            result = dict(server_config.config)
        else:
            result = server_config.to_dict()

        if not isinstance(server_config, CustomServerConfig) and server_config.enabled is not None:
            result["disabled"] = not server_config.enabled

        return result

    @classmethod
    def from_client_format(cls, server_name: str, client_config: Dict[str, Any]) -> ServerConfig:
        """Convert Antigravity format to ServerConfig"""
        config = dict(client_config)

        # Handle disabled / enabled flags
        enabled = None
        if "disabled" in config:
            is_disabled = config.pop("disabled")
            enabled = not is_disabled
        elif "enabled" in config:
            enabled = config.pop("enabled")

        # Handle remote servers (Antigravity supports both serverUrl and url)
        if "serverUrl" in config and "url" not in config:
            config["url"] = config.pop("serverUrl")

        if "url" in config:
            return RemoteServerConfig(
                name=server_name,
                url=config["url"],
                headers=config.get("headers", {}),
                enabled=enabled,
            )

        # Handle stdio servers
        if "command" in config:
            raw_command = config["command"]
            if isinstance(raw_command, list):
                command = raw_command[0] if raw_command else ""
                args = raw_command[1:] if len(raw_command) > 1 else config.get("args", [])
            else:
                command = raw_command
                args = config.get("args", [])

            env = config.get("env", {})
            return STDIOServerConfig(
                name=server_name,
                command=command,
                args=args,
                env=env,
                enabled=enabled,
            )

        # Fallback to custom server config
        return CustomServerConfig(
            name=server_name,
            config=client_config,
            enabled=enabled,
        )

    def disable_server(self, server_name: str) -> bool:
        """Disable a server in the Antigravity configuration.

        Args:
            server_name: Name of the server to disable

        Returns:
            bool: Success or failure
        """
        config = self._load_config()
        servers = config.get(self.configure_key_name, {})
        if server_name not in servers:
            logger.warning(f"Server '{server_name}' not found in {self.display_name} config")
            return False

        servers[server_name]["disabled"] = True
        return self._save_config(config)

    def enable_server(self, server_name: str) -> bool:
        """Enable a server in the Antigravity configuration.

        Args:
            server_name: Name of the server to enable

        Returns:
            bool: Success or failure
        """
        config = self._load_config()
        servers = config.get(self.configure_key_name, {})
        if server_name not in servers:
            logger.warning(f"Server '{server_name}' not found in {self.display_name} config")
            return False

        if "disabled" in servers[server_name]:
            del servers[server_name]["disabled"]
        return self._save_config(config)

    def is_server_disabled(self, server_name: str) -> bool:
        """Check if a server is currently disabled.

        Args:
            server_name: Name of the server to check

        Returns:
            bool: True if server is disabled, False otherwise
        """
        config = self._load_config()
        servers = config.get(self.configure_key_name, {})
        if server_name in servers:
            return bool(servers[server_name].get("disabled", False))
        return False
