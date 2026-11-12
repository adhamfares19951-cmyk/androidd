"""
Shared device-read helpers.

Every sysfs / command based check reads the device through these helpers so that:
- stdout, stderr and the real exit code are kept together with the source of the read,
- the pre-parse status (NEEDS_ROOT / UNKNOWN) always comes from status_from_read,
- evidence always carries the exact command or path and a trimmed raw excerpt.

Only a parser that successfully reads a real value may turn a read into PASS or FAIL.
"""

import re
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from core.result import Evidence, Status, status_from_read, PERMISSION_PATTERNS

_MISSING_PATTERN = "no such file or directory"
_ERROR_LINE_PATTERNS = PERMISSION_PATTERNS + (_MISSING_PATTERN, "can't find service", "inaccessible or not found")


@dataclass
class ReadResult:
    source: str
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    path_exists: bool = True
    log_content: bool = False

    @property
    def status(self) -> Status:
        """Status before parsing: NEEDS_ROOT or UNKNOWN (never PASS/FAIL)."""
        return status_from_read(self.stdout, self.stderr, self.exit_code, self.path_exists, self.log_content)

    @property
    def text(self) -> str:
        return (self.stdout or "").strip()

    @property
    def ok(self) -> bool:
        """True when there is output worth parsing (no permission error, path present, command succeeded)."""
        if not self.text or not self.path_exists:
            return False
        if self.status == Status.NEEDS_ROOT:
            return False
        return self.exit_code in (0, None)

    @property
    def service_missing(self) -> bool:
        return "can't find service" in f"{self.stdout}\n{self.stderr}".lower()

    def evidence(self, note: str = "") -> Evidence:
        body = self.text or (self.stderr or "").strip() or "(empty output)"
        if self.text and (self.stderr or "").strip():
            body = f"{self.text}\n[stderr] {(self.stderr or '').strip()}"
        if note:
            body = f"{body}\n[parsed] {note}"
        return Evidence(source=self.source, raw_excerpt=body, exit_code=self.exit_code)


def _split_merged_errors(stdout: str, stderr: str, exit_code: Optional[int]):
    """
    Legacy shell (exit_code None) merges stderr into stdout. Move the command's own error
    lines (e.g. "cat: x: No such file or directory") back to stderr so they are never parsed
    as data.
    """
    if exit_code is not None or not stdout:
        return stdout, stderr
    keep, errs = [], []
    for line in stdout.splitlines():
        low = line.lower()
        if any(p in low for p in _ERROR_LINE_PATTERNS) and len(line) < 300:
            errs.append(line)
        else:
            keep.append(line)
    if not errs:
        return stdout, stderr
    return "\n".join(keep), "\n".join([e for e in [stderr] + errs if e])


def run_read(bridge: Any, command: str, serial: Optional[str] = None, timeout: int = 8,
             root: bool = False, source: Optional[str] = None, log_content: bool = False) -> ReadResult:
    """Run a shell command and wrap the raw result. Never raises."""
    try:
        code, out, err = bridge.shell(command, serial=serial, timeout=timeout, root=root) if root else \
            bridge.shell(command, serial=serial, timeout=timeout)
    except Exception as e:  # bridge failure must never crash a check
        code, out, err = None, "", f"bridge error: {e}"
    out = out if isinstance(out, str) else ""
    err = err if isinstance(err, str) else ""
    if not log_content:
        out, err = _split_merged_errors(out, err, code)
    missing = _MISSING_PATTERN in err.lower() and not out.strip()
    return ReadResult(source=source or command, stdout=out, stderr=err, exit_code=code,
                      path_exists=not missing, log_content=log_content)


def read_node(bridge: Any, paths: Sequence[str], serial: Optional[str] = None, timeout: int = 5,
              root: bool = False) -> ReadResult:
    """
    Read the first readable sysfs/proc node out of `paths` (glob patterns allowed).
    If none is readable, return the most informative failure:
    permission error > existing-but-empty/unreadable node > missing path.
    """
    best: Optional[ReadResult] = None

    def rank(r: ReadResult) -> int:
        if r.status == Status.NEEDS_ROOT:
            return 3
        if r.path_exists:
            return 2
        return 1

    for path in paths:
        target = path if "*" in path else f"'{path}'"
        res = run_read(bridge, f"cat {target}", serial=serial, timeout=timeout, root=root,
                       source=f"{path} (su)" if root else path)
        if res.ok:
            return res
        if best is None or rank(res) > rank(best):
            best = res
    return best or ReadResult(source=", ".join(paths), path_exists=False)


def failure_status(reads: List[ReadResult]) -> Status:
    """Status when no value could be parsed from any of `reads`: NEEDS_ROOT if any read was denied, else UNKNOWN."""
    return Status.NEEDS_ROOT if any(r.status == Status.NEEDS_ROOT for r in reads) else Status.UNKNOWN


_INT_RE = re.compile(r"^-?\d+$")


def parse_int(text: Optional[str]) -> Optional[int]:
    """Strictly parse the first line as an integer. Anything else is malformed -> None."""
    if not text:
        return None
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not _INT_RE.match(first):
        return None
    return int(first)


def parse_bool_feature(text: Optional[str]) -> Optional[bool]:
    """Parse `pm has-system-feature` output ("true"/"false")."""
    t = (text or "").strip().lower()
    if t == "true":
        return True
    if t == "false":
        return False
    return None
