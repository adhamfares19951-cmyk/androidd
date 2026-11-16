"""
Offline log analysis: a saved UART capture, dmesg, last_kmsg or logcat file is checked line by line against
data/fault_signatures.json. Needs no phone and no root, so it also covers phones that do not boot.

Results follow the project's rules:
- FAIL only for a line that really matched a known fault signature (with the line as evidence and the
  rule's suspects as possible causes, never a definite diagnosis);
- UNKNOWN when nothing known matched: a log without the faults we know is not proof that the phone is fine;
- the chip maker is reported only when the log's own words point to one maker, with the line that did.
"""
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from core.board_diag import load_fault_signature_rules
from core.result import Evidence, M, ProducesCheckResult, Status

MAX_BYTES = 20 * 1024 * 1024        # larger files: only the end is read (a failed boot ends the log)
MAX_LINE_SHOWN = 300
SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
UNREADABLE_SHARE = 0.30             # above this share of undecodable / control characters: "mostly unreadable"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f�]")

# Words that appear in boot logs of one chip maker's platforms (case-insensitive).
SOC_KEYWORDS: List[Tuple[str, re.Pattern]] = [
    ("Qualcomm", re.compile(r"\bqcom\b|qcom,|qcom_|qualcomm|\bmsm\d{4}\b|\bsdm\d{3}\b|\bsm[4-8]\d{3}\b", re.I)),
    ("MediaTek", re.compile(r"\bmtk|mediatek|\bmt6\d{3}\b|\bmt8\d{3}\b|\[preloader\]", re.I)),
    ("Unisoc", re.compile(r"\bsprd|unisoc|\bums\d{3}\b|\bsc9\d{3}\b", re.I)),
    ("HiSilicon Kirin", re.compile(r"\bhisi|kirin|\bhi36\d{2}\b", re.I)),
    ("Samsung Exynos", re.compile(r"exynos|\bs5e\d{4}\b", re.I)),
]


@dataclass
class LogMatch:
    rule_id: str
    category: str
    severity: str
    line_no: int                      # first matching line, 1-based
    line: str
    count: int                        # matching lines in the file
    last_line_no: int
    suspect_components: List[str] = field(default_factory=list)
    description_en: str = ""
    description_ar: str = ""
    action_en: str = ""
    action_ar: str = ""


@dataclass
class LogAnalysis(ProducesCheckResult):
    check_id: str = "log.offline_analysis"
    source: str = ""
    lines: int = 0
    truncated: bool = False
    unreadable_share: float = 0.0
    status: str = "UNKNOWN"
    soc: Optional[str] = None
    soc_evidence: str = ""
    soc_candidates: List[Tuple[str, int]] = field(default_factory=list)
    matches: List[LogMatch] = field(default_factory=list)
    fatal_line: Optional[str] = None
    fatal_line_no: Optional[int] = None
    fatal_rule_id: Optional[str] = None
    diagnosis: Any = None
    possible_causes: List[Any] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() for e in self.evidence]
        return d


def _clean(text: str) -> List[str]:
    text = _ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n")


def _soc(lines: List[str]) -> Tuple[Optional[str], str, List[Tuple[str, int]]]:
    hits: Dict[str, int] = {}
    first: Dict[str, str] = {}
    for line in lines:
        for name, rx in SOC_KEYWORDS:
            if rx.search(line):
                hits[name] = hits.get(name, 0) + 1
                first.setdefault(name, line.strip()[:MAX_LINE_SHOWN])
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    if not ranked:
        return None, "", []
    # one maker must clearly lead (twice the hits of the next); otherwise the log does not say
    if len(ranked) > 1 and ranked[0][1] < 2 * ranked[1][1]:
        return None, "", ranked
    return ranked[0][0], first[ranked[0][0]], ranked


def analyze_text(text: str, source: str = "log", rules: Optional[List[Dict[str, Any]]] = None,
                 truncated: bool = False) -> LogAnalysis:
    r = LogAnalysis(source=source, truncated=truncated)
    lines = _clean(text or "")
    if lines and lines[-1] == "":
        lines.pop()
    r.lines = len(lines)
    total = sum(len(l) for l in lines)
    if total == 0:
        r.diagnosis = M("logscan.empty")
        return r
    r.unreadable_share = round(sum(len(_CONTROL.findall(l)) for l in lines) / total, 3)

    compiled = []
    for rule in (load_fault_signature_rules() if rules is None else rules):
        try:
            compiled.append((rule, re.compile(rule.get("pattern", ""), re.I)))
        except re.error:
            continue                  # a broken pattern in the database must not stop the analysis
    found: Dict[str, LogMatch] = {}
    for no, line in enumerate(lines, start=1):
        for rule, rx in compiled:
            if rule.get("pattern") and rx.search(line):
                m = found.get(rule["id"])
                if m is None:
                    found[rule["id"]] = LogMatch(
                        rule_id=rule["id"], category=rule.get("category", ""), severity=rule.get("severity", ""),
                        line_no=no, line=line.strip()[:MAX_LINE_SHOWN], count=1, last_line_no=no,
                        suspect_components=list(rule.get("suspect_components", [])),
                        description_en=rule.get("description_en", ""), description_ar=rule.get("description_ar", ""),
                        action_en=rule.get("action_en", ""), action_ar=rule.get("action_ar", ""))
                else:
                    m.count += 1
                    m.last_line_no = no
    r.matches = sorted(found.values(), key=lambda m: (-SEVERITY_RANK.get(m.severity, 0), m.line_no))
    r.soc, r.soc_evidence, r.soc_candidates = _soc(lines)

    if r.matches:
        # The isolated fatal line: the most severe kind, and of those the latest line (where the boot stopped).
        top = max(SEVERITY_RANK.get(m.severity, 0) for m in r.matches)
        worst = max((m for m in r.matches if SEVERITY_RANK.get(m.severity, 0) == top), key=lambda m: m.last_line_no)
        r.fatal_rule_id, r.fatal_line_no = worst.rule_id, worst.last_line_no
        r.fatal_line = lines[worst.last_line_no - 1].strip()[:MAX_LINE_SHOWN]
        r.status = str(Status.FAIL)
        r.diagnosis = M("logscan.found", count=len(r.matches), line=r.fatal_line_no, rule=r.fatal_rule_id)
        seen = []
        for m in r.matches:
            for s in m.suspect_components:
                if s not in seen:
                    seen.append(s)
        r.possible_causes = seen
        for m in r.matches:
            r.evidence.append(Evidence(source=f"{source}, line {m.line_no}",
                                       raw_excerpt=f"line {m.line_no}: {m.line}\n[parsed] {m.rule_id} ({m.severity}), "
                                                   f"{m.count} matching line(s), last at line {m.last_line_no}",
                                       exit_code=None))
    elif r.unreadable_share > UNREADABLE_SHARE:
        r.diagnosis = M("logscan.mostly_unreadable", pct=round(r.unreadable_share * 100))
    else:
        r.diagnosis = M("logscan.no_known_fault", lines=r.lines)
    if r.soc:
        r.evidence.append(Evidence(source=f"{source} (chip maker words)", raw_excerpt=r.soc_evidence, exit_code=None))
    return r


def analyze_file(path: str, max_bytes: int = MAX_BYTES, rules: Optional[List[Dict[str, Any]]] = None) -> LogAnalysis:
    """Read and analyze a log file. Raises OSError when the file cannot be read."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            data = f.read()
            data = data[data.find(b"\n") + 1:] if b"\n" in data else data    # drop the cut first line
        else:
            data = f.read()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("utf-8", errors="replace")
    return analyze_text(text, source=os.path.basename(path), rules=rules, truncated=size > max_bytes)


def description(m: LogMatch, lang: str) -> str:
    return (m.description_ar if lang == "ar" else m.description_en) or m.description_en


def action(m: LogMatch, lang: str) -> str:
    return (m.action_ar if lang == "ar" else m.action_en) or m.action_en
