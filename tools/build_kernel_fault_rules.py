"""
Builds data/fault_signatures_kernel.json from tools/kernel_fault_rules.py.

The rules are written by hand, but every message a rule cites must exist verbatim in a real kernel driver:
this script downloads the drivers (sparse clones of three LineageOS kernels), extracts every format string
passed to the kernel's print functions, and resolves each cited message to the file it comes from. A message
that is not found stops the build, so no rule can cite a message that no driver prints.

Usage (needs git and network the first time):
    python tools/build_kernel_fault_rules.py --cache .kernel_cache --out data/fault_signatures_kernel.json
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kernel_fault_rules import R  # noqa: E402

KERNELS = {
    "android_kernel_xiaomi_mt6768": "lineage-23.2",       # MediaTek Helio G80/G85 (Redmi 9, Redmi Note 9)
    "android_kernel_xiaomi_sm6150": "lineage-23.2",       # Qualcomm Snapdragon 730 family
    "android_kernel_samsung_exynos9820": "lineage-23.2",  # Samsung Galaxy S10 family
}
SPARSE = ["drivers/power/", "drivers/battery/", "drivers/misc/mediatek/power/", "drivers/misc/mediatek/pmic/",
          "drivers/misc/mediatek/typec/", "drivers/misc/mediatek/lcm/", "drivers/input/fingerprint/",
          "drivers/input/touchscreen/", "drivers/mmc/", "drivers/scsi/ufs/", "drivers/gpu/drm/msm/",
          "drivers/usb/typec/", "drivers/ccic/", "drivers/muic/"]
CALL = re.compile(r"\b(?:pr_err|pr_warn|pr_warning|pr_crit|pr_alert|pr_emerg|pr_notice|pr_info|dev_err|dev_warn|"
                  r"dev_crit|dev_notice|dev_info|printk|chr_err|chr_info|bm_err|bm_notice|bm_info|battery_log|"
                  r"dev_err_ratelimited|pr_err_ratelimited|smblib_err|pr_smb|qg_dbg|pr_qg|FG_ERR|pr_ccic|pr_muic|"
                  r"BAT_LOG|chr_debug|fpc_log|gf_dbg|FPC_LOGE|pr_dbg)\s*\(([^;]*?)\)\s*;", re.S)
STR = re.compile(r'"((?:[^"\\]|\\.)*)"')


def fetch(cache: str):
    os.makedirs(cache, exist_ok=True)
    for repo, branch in KERNELS.items():
        d = os.path.join(cache, repo)
        if not os.path.isdir(d):
            subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", "-b", branch,
                            f"https://github.com/LineageOS/{repo}.git", d], check=True)
        subprocess.run(["git", "-C", d, "sparse-checkout", "set", "--no-cone", *SPARSE], check=True)


def strings(cache: str):
    out = {}
    for repo in KERNELS:
        root = os.path.join(cache, repo)
        for dp, _, files in os.walk(root):
            if "/.git" in dp:
                continue
            for f in files:
                if not f.endswith((".c", ".h")):
                    continue
                p = os.path.join(dp, f)
                try:
                    text = open(p, errors="replace").read()
                except OSError:
                    continue
                for m in CALL.finditer(text):
                    s = re.sub(r"KERN_[A-Z]+", "", "".join(STR.findall(m.group(1)))).strip()
                    if len(s) >= 8:
                        out.setdefault(s, []).append((repo, os.path.relpath(p, root)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=".kernel_cache")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                  "data", "fault_signatures_kernel.json"))
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    if not a.offline:
        fetch(a.cache)
    index = strings(a.cache)
    missing = []
    for r in R:
        resolved = []
        for ks in r["kernel_source"]:
            msg = ks["message"].strip()
            want = ks["url"].split("/blob/")[0].rsplit("/", 1)[1]
            places = index.get(msg)
            if not places:
                missing.append((r["id"], msg))
                continue
            repo, path = next((x for x in places if x[0] == want), places[0])
            resolved.append({"url": f"https://github.com/LineageOS/{repo}/blob/{KERNELS[repo]}/{path}", "message": msg})
        r["kernel_source"] = resolved
    if missing:
        for rid, msg in missing:
            print(f"not found in any driver: {rid}: {msg!r}", file=sys.stderr)
        return 1
    doc = {"version": "1.0", "updated_at": "2026-11-18",
           "description": "Fault signatures taken from the error messages of real phone kernel drivers (LineageOS "
                          "kernel sources for a MediaTek MT6768, a Qualcomm SM6150 and a Samsung Exynos 9820 phone). "
                          "Each rule lists the driver file and the exact format string it matches. Loaded together "
                          "with fault_signatures.json.",
           "rules": R}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"{len(R)} rules -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
