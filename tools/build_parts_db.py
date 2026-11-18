"""
Builds data/parts_db.json: what each phone model is built with, from LineageOS public data.

Sources (fetched over the network into a cache folder, then parsed offline):
- LineageOS wiki device files (github.com/LineageOS/lineage_wiki, _data/devices/*.yml, MIT):
  name, vendor, codename, model numbers, SoC, battery capacity, screen, cameras (megapixels),
  peripherals, how to enter download / recovery mode.
- Each device tree's proprietary-files.txt (github.com/LineageOS/android_device_<vendor>_<codename>,
  and the common trees it depends on): the vendor files the phone ships with, whose names carry
  the camera sensor (and often its module maker, e.g. ov13b10_ofilm), the fingerprint maker and the
  NFC chip.

What the data means (core/parts_db.py repeats this to the user):
- A sensor listed for a model is one its stock firmware ships tuning files for. Makers use more
  than one supplier per model, so a model often lists two or three sensors for the same camera.
- A sensor found only in a shared common tree ("platform") may belong to a sibling model.
- No entry or an empty list means "not known", never "absent".

Usage (needs git and network; about one minute):
    python tools/build_parts_db.py --cache .parts_cache --out data/parts_db.json
    python tools/build_parts_db.py --cache .parts_cache --out data/parts_db.json --offline
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

WIKI_REPO = "https://github.com/LineageOS/lineage_wiki.git"
RAW = "https://raw.githubusercontent.com/LineageOS/{repo}/{branch}/{path}"
TREE_URL = "https://github.com/LineageOS/{repo}/tree/{branch}"
FILES = ("proprietary-files.txt", "lineage.dependencies")
SCHEMA = 1

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.part_names import fingerprint_makers, nfc_chips, sensors_in  # noqa: E402  (shared with the phone check)


def stock_codenames(text: str, lineage_codename: str):
    """'# Unpinned blobs from merlin-user 12 SP1A...' / '# ... - from lavender V10.3.6.0' -> ['merlin'] / ['lavender'].
    The LineageOS codename itself is left out; names that look like versions or build ids are skipped."""
    out = []
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        for name in re.findall(r"\bfrom\s+([A-Za-z][A-Za-z0-9_]{2,30})(?:[-_](?:user|userdebug|eng)\b|\s+(?:V\d|\d|[A-Z]{2,}\d))", line):
            n = name.lower()
            base = re.sub(r"(?:_global|_retail|_eea|_in|_cn|_ru|_g|_sprout)$", "", n)
            lc = lineage_codename.lower()
            # only the same phone: blobs are sometimes taken from a sibling's firmware ("from sagit")
            same = base == lc or lc in (base + "x", base + "_sprout") or base == lc + "in"
            if n != lc and same and n not in out:
                out.append(n)
            if base != n and base != lc and same and base not in out:
                out.append(base)
    return out[:4]


# ------------------------------------------------------------------ fetching
def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception:
        return None


def fetch_tree(cache: str, repo: str, branches):
    """Downloads the two files of a repo at the first branch that has one of them. Returns the branch."""
    for b in branches:
        d = os.path.join(cache, "trees", f"{repo}@{b}")
        if os.path.isfile(os.path.join(d, ".done")):
            return b if os.listdir(d) != [".done"] else None
    for b in branches:
        got = {f: _get(RAW.format(repo=repo, branch=b, path=f)) for f in FILES}
        if any(v is not None for v in got.values()):
            d = os.path.join(cache, "trees", f"{repo}@{b}")
            os.makedirs(d, exist_ok=True)
            for f, v in got.items():
                if v is not None:
                    with open(os.path.join(d, f), "w", encoding="utf-8") as fh:
                        fh.write(v)
            open(os.path.join(d, ".done"), "w").close()
            return b
    return None


def branch_candidates(current: str):
    c = str(current)
    major = c.split(".")[0]
    return [f"lineage-{c}", f"cm-{c}", f"lineage-{major}.0", f"lineage-{c}.0"]


def read_tree(cache: str, repo: str, branch: str):
    d = os.path.join(cache, "trees", f"{repo}@{branch}")
    pf = os.path.join(d, "proprietary-files.txt")
    deps = os.path.join(d, "lineage.dependencies")
    text = open(pf, encoding="utf-8").read() if os.path.isfile(pf) else ""
    dep_list = []
    if os.path.isfile(deps):
        try:
            for x in json.load(open(deps, encoding="utf-8")):
                r = x.get("repository", "")
                if r.startswith("android_device_"):
                    dep_list.append((r, x.get("branch") or branch))
        except ValueError:
            pass
    return text, dep_list


# ------------------------------------------------------------------ wiki
def _variants(v):
    """battery/screen: a dict, a list of {label: dict}, None or 'None' -> {label: dict} ('' = the only one)."""
    if isinstance(v, dict):
        return {"": v}
    out = {}
    if isinstance(v, list):
        for item in v:
            if isinstance(item, dict):
                for label, val in item.items():
                    if isinstance(val, dict):
                        out[str(label) if label not in out else f"{label} ({len(out) + 1})"] = val
    return out


def clean_html(s):
    if not isinstance(s, str):
        return None
    s = re.sub(r"<kbd>(.*?)</kbd>", r"\1", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip() or None


def load_wiki(cache: str, offline: bool):
    import yaml  # PyYAML, build-time only
    wiki = os.path.join(cache, "lineage_wiki")
    if not offline:
        if os.path.isdir(os.path.join(wiki, ".git")):
            subprocess.run(["git", "-C", wiki, "pull", "-q", "--depth", "1"], check=False)
        else:
            subprocess.run(["git", "clone", "-q", "--depth", "1", WIKI_REPO, wiki], check=True)
    commit = subprocess.run(["git", "-C", wiki, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    devices = []
    for f in sorted(glob.glob(os.path.join(wiki, "_data", "devices", "*.yml"))):
        with open(f, encoding="utf-8") as fh:
            devices.append((os.path.basename(f)[:-4], yaml.safe_load(fh)))
    return devices, commit


def build(cache: str, offline: bool, workers: int = 24):
    devices, wiki_commit = load_wiki(cache, offline)
    # 1. device trees
    wanted = {}
    for _, d in devices:
        wanted[d["tree"]] = branch_candidates(d.get("current_branch", ""))
    with cf.ThreadPoolExecutor(workers) as ex:
        tree_branch = dict(zip(wanted, ex.map(lambda r: fetch_tree(cache, r, wanted[r]), wanted)))
    # 2. common trees, two levels deep
    commons = {}
    frontier = set()
    for repo, b in tree_branch.items():
        if b:
            frontier.update(read_tree(cache, repo, b)[1])
    for _level in range(3):
        todo = [x for x in frontier if x not in commons]
        if not todo:
            break
        with cf.ThreadPoolExecutor(workers) as ex:
            got = list(ex.map(lambda rb: fetch_tree(cache, rb[0], [rb[1]]), todo))
        frontier = set()
        for (repo, b), ok in zip(todo, got):
            commons[(repo, b)] = ok
            if ok:
                frontier.update(read_tree(cache, repo, b)[1])

    def common_texts(repo, branch, seen=None):
        seen = seen or set()
        out = []
        for dep in read_tree(cache, repo, branch)[1]:
            if dep in seen or not commons.get(dep):
                continue
            seen.add(dep)
            text, _ = read_tree(cache, *dep)
            out.append((dep, text))
            out.extend(common_texts(dep[0], dep[1], seen))
        return out

    entries = []
    for key, d in devices:
        repo, b = d["tree"], tree_branch.get(d["tree"])
        dev_text, _ = read_tree(cache, repo, b) if b else ("", [])
        com = common_texts(repo, b) if b else []
        com_text = "\n".join(t for _, t in com)
        stock = stock_codenames(dev_text, str(d.get("codename")))
        dev_sensors = sensors_in(dev_text)
        dev_set = {s for s, _ in dev_sensors}
        plat_sensors = [x for x in sensors_in(com_text) if x[0] not in dev_set]
        batteries = _variants(d.get("battery"))
        caps = {k: v.get("capacity") for k, v in batteries.items() if isinstance(v.get("capacity"), (int, float))}
        screens = _variants(d.get("screen"))
        cams = []
        for c in d.get("cameras") or []:
            if isinstance(c, dict) and c.get("info"):
                cams.append(str(c["info"]))
        entries.append({
            "key": key,
            "codename": str(d.get("codename")),
            "name": str(d.get("name")),
            "vendor": str(d.get("vendor")),
            "type": d.get("type"),
            "models": [str(m) for m in (d.get("models") or [])],
            # codenames of the factory firmware the blobs came from ("# ... from merlin-user 12 ..."):
            # the phone reports these in ro.product.device, LineageOS may use another (merlinx)
            "stock_codenames": stock,
            "variant": d.get("variant"),
            "release": str(d.get("release")) if d.get("release") is not None else None,
            "soc": d.get("soc"),
            # one value when every variant agrees; variants differ -> battery_by_variant only (never a guess)
            "battery_mah": next(iter(set(caps.values()))) if len(set(caps.values())) == 1 else None,
            "battery_by_variant": caps if len(set(caps.values())) > 1 else {},
            "screens": {k: {f: v.get(f) for f in ("size", "resolution", "technology") if v.get(f) is not None}
                        for k, v in screens.items()},
            "cameras": cams,
            "peripherals": [str(p) for p in (d.get("peripherals") or [])],
            "download_boot": clean_html(d.get("download_boot")),
            "recovery_boot": clean_html(d.get("recovery_boot")),
            "camera_sensors": [{"sensor": s, "module": m or None} for s, m in dev_sensors],
            "platform_camera_sensors": [{"sensor": s, "module": m or None} for s, m in plat_sensors],
            "fingerprint_makers": fingerprint_makers(dev_text) or fingerprint_makers(com_text),
            "fingerprint_from": "device" if fingerprint_makers(dev_text) else ("platform" if fingerprint_makers(com_text) else None),
            "nfc_chips": nfc_chips(dev_text + "\n" + com_text),
            "source": {
                "wiki": f"https://wiki.lineageos.org/devices/{d.get('codename')}/",
                "tree": TREE_URL.format(repo=repo, branch=b) if b else None,
                "commons": [TREE_URL.format(repo=r, branch=cb) for (r, cb), _ in com],
            },
        })
    return {
        "schema": SCHEMA,
        "built": dt.date.today().isoformat(),
        "sources": {
            "lineage_wiki": {"url": "https://github.com/LineageOS/lineage_wiki", "commit": wiki_commit, "license": "MIT"},
            "device_trees": {"url": "https://github.com/LineageOS", "license": "Apache-2.0 (file lists only, no code copied)"},
        },
        "meaning": {
            "camera_sensors": "sensors the model's stock firmware ships tuning files for (device tree); several per camera are normal (second suppliers)",
            "platform_camera_sensors": "sensors only in a common tree shared with sibling models: weaker evidence",
            "empty": "not known; never 'absent'",
        },
        "devices": entries,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cache", default=".parts_cache")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "parts_db.json"))
    ap.add_argument("--offline", action="store_true", help="use only the cache")
    a = ap.parse_args(argv)
    os.makedirs(a.cache, exist_ok=True)
    db = build(a.cache, a.offline)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    n = len(db["devices"])
    with_s = sum(1 for d in db["devices"] if d["camera_sensors"])
    print(f"{n} devices, {with_s} with device-level camera sensors -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
