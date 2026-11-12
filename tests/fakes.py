"""
Command-routed fake ADB bridge for tests.

Each route maps a substring of the shell command to (exit_code, stdout, stderr).
Unlike an ordered side_effect list, the result does not depend on how many commands
a check runs or in which order, so tests assert on what the check does with a given read.
"""

from typing import Dict, List, Optional, Tuple

Reply = Tuple[Optional[int], str, str]

# Standard scenario replies for the read matrix
NORMAL = "normal"
EMPTY = (0, "", "")
PERMISSION = (1, "", "cat: node: Permission denied")
MISSING = (1, "", "cat: node: No such file or directory")
MALFORMED = (0, "garbage@@value", "")


class FakeBridge:
    def __init__(self, routes: Optional[Dict[str, Reply]] = None, default: Reply = (1, "", ""),
                 props: Optional[Dict[str, str]] = None, root: bool = False):
        self.routes: List[Tuple[str, Reply]] = list((routes or {}).items())
        self.default = default
        self.props = props or {}
        self.root = root
        self.commands: List[str] = []
        self.fastboot_routes = {}

    def route(self, pattern: str, reply: Reply) -> "FakeBridge":
        self.routes.insert(0, (pattern, reply))
        return self

    def shell(self, command: str, serial: Optional[str] = None, timeout: int = 30, root: bool = False,
              use_socket: bool = True) -> Reply:
        self.commands.append(command)
        for pattern, reply in self.routes:
            if pattern in command:
                return reply
        return self.default

    # fastboot: routes keyed by the joined argument string, e.g. "getvar partition-type:cache"
    fastboot_routes: Dict[str, Reply] = {}

    def run_fastboot(self, args: List[str], serial: Optional[str] = None, timeout: int = 30) -> Reply:
        joined = " ".join(args)
        self.commands.append(f"fastboot {joined}")
        for pattern, reply in self.fastboot_routes.items():
            if pattern in joined:
                return reply
        return (1, "", "")

    def pull(self, remote_path: str, local_path: str, serial: Optional[str] = None, timeout: int = 60):
        self.commands.append(f"pull {remote_path}")
        return False, "remote object does not exist"

    devices: List[dict] = []

    def list_devices(self) -> List[dict]:
        return list(self.devices)

    def get_all_props(self, serial: Optional[str] = None) -> Dict[str, str]:
        return dict(self.props)

    def is_device_ready(self, serial: Optional[str] = None) -> bool:
        return True

    def get_prop(self, name: str, serial: Optional[str] = None) -> str:
        return self.props.get(name, "")

    def is_root_available(self, serial: Optional[str] = None) -> bool:
        return self.root

    def get_cached_capability(self, serial, key):
        return None

    def set_cached_capability(self, serial, key, value):
        pass
