"""Board schematics / boardview / datasheet / pinout lookup for the connected phone.

Offline-first and evidence-backed, like every other check in the program:

- The index (data/schematics_index.json + the technician's own at
  %LOCALAPPDATA%\\AndroidDoctorPro\\schematics_index.json) is a POINTER catalogue only.
  It never holds the documents; it maps models / codenames / IC part numbers to a file
  name and, optionally, a source URL the technician controls.
- Resolving a document looks in the technician's local folders FIRST, then the local
  cache, and only then reports that it is available on demand. A document is never
  invented: a model with no matching entry, and a file that is nowhere on disk, is
  UNKNOWN, never a made-up PASS.
- Every result records where the evidence came from (which local file, the cache, or the
  index's source URL) and a clear status:
    PASS       - a real document file is in hand (found locally/in cache, or fetched and,
                 when a sha256 is published, verified against it).
    FAIL       - an on-demand download did not match the published sha256 (tampered or
                 corrupt); the only fault this module ever asserts.
    UNKNOWN    - not on disk (whether or not a source URL exists); absence of a schematic
                 is not a phone fault.
  NEEDS_ROOT never applies here.
"""
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app_paths import user_data_dir, documents_dir, SOURCE_ROOT
from core.model_db import normalize_model
from core.result import Evidence, M, ProducesCheckResult, Status

logger = logging.getLogger("SchematicsDB")

BUNDLED_PATH = os.path.join(SOURCE_ROOT, "data", "schematics_index.json")

DOC_TYPES = ("schematic", "boardview", "datasheet", "pinout")

# Where a resolved document can come from.
SRC_LOCAL = "local"        # the technician's own schematics folder
SRC_CACHE = "cache"        # previously fetched on demand
SRC_ON_DEMAND = "on_demand"  # not on disk yet, but the index has a source URL
SRC_MISSING = "missing"    # not on disk and no source


@dataclass(frozen=True)
class SchematicDoc:
    id: str
    doc_type: str
    component: str
    models: tuple = ()
    codenames: tuple = ()
    manufacturer: str = ""
    file: str = ""
    source: str = ""
    sha256: str = ""
    notes: str = ""


class SchematicResult(ProducesCheckResult):
    """One resolved document request, as a uniform CheckResult-capable object."""
    check_id = "schematic.lookup"
    _check_detail_attr = "detail"

    def __init__(self, status: Status, origin: str, doc: Optional[SchematicDoc] = None,
                 path: str = "", detail: Any = None, evidence: Optional[List[Evidence]] = None):
        self.status = status
        self.origin = origin          # one of SRC_*
        self.doc = doc
        self.path = path              # the real file on disk, when status is PASS
        self.detail = detail
        self.evidence = evidence or []
        self.possible_causes: List[Any] = []

    def _check_value(self):
        if self.doc is None:
            return None, ""
        return f"{self.doc.component} ({self.doc.doc_type})", ""


def _as_tuple(value) -> tuple:
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    if value is None or value == "":
        return ()
    return (str(value).strip(),)


class SchematicsDatabase:
    def __init__(self, index_paths: Optional[List[str]] = None,
                 local_dirs: Optional[List[str]] = None,
                 cache_dir: Optional[str] = None):
        self.errors: List[str] = []
        self.docs: List[SchematicDoc] = []

        if index_paths is None:
            index_paths = [BUNDLED_PATH, os.path.join(user_data_dir(), "schematics_index.json")]
        for p in index_paths:
            self._load(p)

        if local_dirs is None:
            local_dirs = [
                os.path.join(user_data_dir(), "schematics"),
                os.path.join(documents_dir(), "Android Doctor Pro", "Schematics"),
            ]
        # Keep only the directories that exist; order is the search order (technician first).
        self.local_dirs = [d for d in local_dirs if d and os.path.isdir(d)]
        self.cache_dir = cache_dir if cache_dir is not None else os.path.join(user_data_dir(), "schematics_cache")

    # ---------------------------------------------------------------- loading
    def _load(self, path: str) -> None:
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.errors.append(f"{path}: {e}")
            return
        for raw in data.get("documents", []) or []:
            doc = self._validate(raw, path)
            if doc is not None:
                self.docs.append(doc)

    def _validate(self, raw: Dict[str, Any], path: str) -> Optional[SchematicDoc]:
        """Reject (and log) a malformed entry rather than guess around it."""
        if not isinstance(raw, dict):
            self.errors.append(f"{path}: a document entry is not an object")
            return None
        doc_id = str(raw.get("id", "")).strip()
        doc_type = str(raw.get("doc_type", "")).strip().lower()
        component = str(raw.get("component", "")).strip()
        file_rel = str(raw.get("file", "")).strip()
        source = str(raw.get("source", "")).strip()
        if not doc_id:
            self.errors.append(f"{path}: a document has no id")
            return None
        if doc_type not in DOC_TYPES:
            self.errors.append(f"{path}: {doc_id} has an unknown doc_type '{doc_type}'")
            return None
        if not component:
            self.errors.append(f"{path}: {doc_id} has no component")
            return None
        if not file_rel and not source:
            self.errors.append(f"{path}: {doc_id} has neither a file nor a source")
            return None
        # A file path must stay relative and inside the folder (no absolute paths, no '..').
        if file_rel and (os.path.isabs(file_rel) or ".." in file_rel.replace("\\", "/").split("/")):
            self.errors.append(f"{path}: {doc_id} has an unsafe file path '{file_rel}'")
            return None
        return SchematicDoc(
            id=doc_id,
            doc_type=doc_type,
            component=component,
            models=_as_tuple(raw.get("models")),
            codenames=_as_tuple(raw.get("codenames")),
            manufacturer=str(raw.get("manufacturer", "")).strip().lower(),
            file=file_rel,
            source=source,
            sha256=str(raw.get("sha256", "")).strip().lower(),
            notes=str(raw.get("notes", "")).strip(),
        )

    # ---------------------------------------------------------------- lookup
    def lookup(self, model: str = "", codename: str = "", manufacturer: str = "",
               component: str = "", doc_type: str = "") -> List[SchematicDoc]:
        """Index entries for this phone / component. Model number matches first; a codename
        match also requires the manufacturer when the caller gives one, so a codename reused
        across makers (e.g. 'merlin') never pulls another maker's board."""
        model_n = normalize_model(model) if model else ""
        codename_l = (codename or "").strip().lower()
        manuf_l = (manufacturer or "").strip().lower()
        comp_l = (component or "").strip().lower()
        type_l = (doc_type or "").strip().lower()

        out: List[SchematicDoc] = []
        for d in self.docs:
            if type_l and d.doc_type != type_l:
                continue
            if comp_l and comp_l not in d.component.lower():
                continue
            matched = False
            if model_n and any(normalize_model(m) == model_n for m in d.models):
                matched = True
            elif codename_l and codename_l in (c.lower() for c in d.codenames):
                # A codename hit is only trusted when the maker agrees (if both are known).
                if not manuf_l or not d.manufacturer or d.manufacturer == manuf_l:
                    matched = True
            elif comp_l and not model_n and not codename_l:
                # A pure component search (e.g. a datasheet by IC part number) needs no device.
                matched = True
            if matched:
                out.append(d)
        return out

    # ---------------------------------------------------------------- resolve
    def _find_on_disk(self, file_rel: str) -> Optional[tuple]:
        """Return (path, origin) for the first existing copy: a local folder, then the cache."""
        if not file_rel:
            return None
        for base in self.local_dirs:
            cand = os.path.join(base, file_rel)
            if os.path.isfile(cand):
                return cand, SRC_LOCAL
        cand = os.path.join(self.cache_dir, file_rel)
        if os.path.isfile(cand):
            return cand, SRC_CACHE
        return None

    def resolve(self, doc: SchematicDoc) -> SchematicResult:
        """Offline-first: a real file on disk (local, then cache) is PASS; otherwise UNKNOWN,
        noting whether it can be fetched on demand. A document is never reported as present
        unless its file is actually there."""
        hit = self._find_on_disk(doc.file)
        if hit is not None:
            path, origin = hit
            ev = [Evidence(source=f"schematic:{origin}", raw_excerpt=path)]
            return SchematicResult(Status.PASS, origin, doc=doc, path=path,
                                   detail=M("schematic.found", component=doc.component,
                                            type=doc.doc_type, origin=origin),
                                   evidence=ev)
        if doc.source:
            ev = [Evidence(source="schematic:index", raw_excerpt=doc.source)]
            return SchematicResult(Status.UNKNOWN, SRC_ON_DEMAND, doc=doc,
                                   detail=M("schematic.on_demand", component=doc.component,
                                            type=doc.doc_type),
                                   evidence=ev)
        return SchematicResult(Status.UNKNOWN, SRC_MISSING, doc=doc,
                               detail=M("schematic.missing", component=doc.component,
                                        type=doc.doc_type))

    def find(self, model: str = "", codename: str = "", manufacturer: str = "",
             component: str = "", doc_type: str = "") -> List[SchematicResult]:
        """lookup() + resolve() for every match, offline-first."""
        return [self.resolve(d) for d in
                self.lookup(model, codename, manufacturer, component, doc_type)]

    # ---------------------------------------------------------------- on-demand fetch
    def _download(self, url: str, timeout: int = 30) -> bytes:
        """Fetch the bytes at url. Isolated so tests can inject content without a network."""
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec - tech-controlled URL
            if getattr(resp, "status", 200) not in (200, None):
                raise OSError(f"HTTP {resp.status}")
            return resp.read()

    def fetch(self, doc: SchematicDoc, timeout: int = 30) -> SchematicResult:
        """Explicit on-demand download into the cache. Verifies the published sha256 when
        present; a mismatch is a real FAIL and the file is discarded. Never auto-runs."""
        if not doc.source:
            return SchematicResult(Status.UNKNOWN, SRC_MISSING, doc=doc,
                                   detail=M("schematic.no_source", component=doc.component))
        if not doc.file:
            return SchematicResult(Status.UNKNOWN, SRC_MISSING, doc=doc,
                                   detail=M("schematic.no_file", component=doc.component))
        try:
            data = self._download(doc.source, timeout=timeout)
        except Exception as e:
            return SchematicResult(Status.UNKNOWN, SRC_ON_DEMAND, doc=doc,
                                   detail=M("schematic.fetch_failed", component=doc.component, error=str(e)),
                                   evidence=[Evidence(source="schematic:fetch", raw_excerpt=str(e))])

        digest = hashlib.sha256(data).hexdigest()
        if doc.sha256 and digest != doc.sha256:
            return SchematicResult(
                Status.FAIL, SRC_ON_DEMAND, doc=doc,
                detail=M("schematic.sha_mismatch", component=doc.component,
                         want=doc.sha256[:12], got=digest[:12]),
                evidence=[Evidence(source="schematic:sha256", raw_excerpt=f"expected {doc.sha256}, got {digest}")])

        dest = os.path.join(self.cache_dir, doc.file)
        os.makedirs(os.path.dirname(dest) or self.cache_dir, exist_ok=True)
        # Write to a temp file in the same folder, then atomically move into place.
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest) or self.cache_dir, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return SchematicResult(Status.UNKNOWN, SRC_ON_DEMAND, doc=doc,
                                   detail=M("schematic.cache_failed", component=doc.component, error=str(e)))

        ev = [Evidence(source="schematic:cache", raw_excerpt=dest)]
        if doc.sha256:
            ev.append(Evidence(source="schematic:sha256", raw_excerpt=f"verified {digest}"))
        return SchematicResult(Status.PASS, SRC_CACHE, doc=doc, path=dest,
                               detail=M("schematic.fetched", component=doc.component, type=doc.doc_type),
                               evidence=ev)


# ---------------------------------------------------------------- the technician's own documents
def user_index_path() -> str:
    return os.path.join(user_data_dir(), "schematics_index.json")


def user_schematics_dir() -> str:
    """The first local folder searched (SchematicsDatabase's default local_dirs[0])."""
    return os.path.join(user_data_dir(), "schematics")


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def add_user_document(src_path: str, doc_type: str, component: str, models=(), codenames=(),
                      manufacturer: str = "", notes: str = "", index_path: Optional[str] = None,
                      local_dir: Optional[str] = None):
    """Copy a technician's file into their schematics folder and add it to their own index.
    Returns (SchematicDoc, Msg) on success, (None, Msg) when refused. The original file is left in place, an
    existing file is never overwritten (another name is chosen), and an index that cannot be read is left as it
    is instead of being replaced."""
    import shutil
    index_path = index_path or user_index_path()
    local_dir = local_dir or user_schematics_dir()
    doc_type = (doc_type or "").strip().lower()
    component = (component or "").strip()
    if doc_type not in DOC_TYPES:
        return None, M("schematic.add_bad_type", type=doc_type or "-")
    if not component:
        return None, M("schematic.add_no_component")
    if not src_path or not os.path.isfile(src_path):
        return None, M("schematic.add_no_file", path=src_path or "-")

    data: Dict[str, Any] = {"schema": 1, "documents": []}
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("documents", []), list):
                raise ValueError("not an index object")
        except (OSError, ValueError) as e:
            return None, M("schematic.add_index_unreadable", path=index_path, error=str(e))
    documents = data.setdefault("documents", [])

    try:
        digest = _file_sha256(src_path)
        os.makedirs(local_dir, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(src_path))
        name, n = stem + ext, 1
        while True:
            dest = os.path.join(local_dir, name)
            if not os.path.exists(dest):
                shutil.copy2(src_path, dest)
                break
            if _file_sha256(dest) == digest:        # the same file is already there: reuse it
                break
            n += 1
            name = f"{stem}-{n}{ext}"
    except OSError as e:
        return None, M("schematic.add_failed", error=str(e))

    used = {str(d.get("id")) for d in documents if isinstance(d, dict)}
    base_id = "user-" + "".join(c if c.isalnum() else "-" for c in (component + "-" + doc_type).lower()).strip("-")
    doc_id, k = base_id, 1
    while doc_id in used:
        k += 1
        doc_id = f"{base_id}-{k}"
    entry = {"id": doc_id, "doc_type": doc_type, "component": component,
             "models": [m.strip() for m in models if str(m).strip()],
             "codenames": [c.strip() for c in codenames if str(c).strip()],
             "manufacturer": (manufacturer or "").strip().lower(), "file": name, "sha256": digest,
             "notes": (notes or "").strip()}
    documents.append(entry)
    tmp = ""
    try:
        os.makedirs(os.path.dirname(index_path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(index_path) or ".", suffix=".part")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, index_path)
    except OSError as e:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return None, M("schematic.add_failed", error=str(e))
    doc = SchematicDoc(id=doc_id, doc_type=doc_type, component=component, models=tuple(entry["models"]),
                       codenames=tuple(entry["codenames"]), manufacturer=entry["manufacturer"], file=name,
                       sha256=digest, notes=entry["notes"])
    return doc, M("schematic.added", component=component, type=doc_type, file=name)
