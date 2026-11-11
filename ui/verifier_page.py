"""
Offline verification page for signed report QR codes (`--export-verifier DIR --public-key PUB`).

One self-contained HTML file with the shop's public key built in. It checks an ADP1 summary (pasted,
scanned with the camera where the browser supports BarcodeDetector, or passed after '#' in the
link) with the browser's own Ed25519 (WebCrypto). No server, no network request, no library.
Host it anywhere (or send the file); set ADP_VERIFY_URL to its https address and report QR codes
become links that open it and verify on the spot.
"""

import html
import json
import os
from typing import Tuple

from core.report_signing import QR_FIELDS, QR_PREFIX, key_id_from_public, load_public_key, public_key_raw_b64
from ui.i18n import t

PAGE_NAME = "adp_verify.html"

_STRINGS = ("title", "intro", "paste_label", "verify", "scan", "scan_stop", "ok", "bad_signature", "bad_format",
            "other_key", "no_ed25519", "field_ref", "field_serial", "field_model", "field_status", "field_date",
            "offline_note")

_TEMPLATE = """<!DOCTYPE html>
<html lang="__LANG__" dir="__DIR__">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { --bg:#0f1012; --card:#16171a; --border:#24262b; --text:#ececee; --muted:#a1a4ab;
        --accent:#4c8dff; --ok:#56d364; --okbg:#12261a; --bad:#ff7b72; --badbg:#2d1517; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.6 "Segoe UI", system-ui, sans-serif; }
main { max-width:640px; margin:0 auto; padding:28px 16px 40px; }
h1 { font-size:22px; margin:0 0 6px; }
p.intro, .muted { color:var(--muted); margin:0 0 18px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; }
label { display:block; font-size:13px; color:var(--muted); margin-bottom:6px; }
textarea { width:100%; min-height:96px; resize:vertical; background:#111215; color:var(--text);
           border:1px solid #30333a; border-radius:8px; padding:10px; font:13px Consolas, monospace; direction:ltr; }
.row { display:flex; gap:8px; margin-top:12px; flex-wrap:wrap; }
button { border:0; border-radius:7px; padding:9px 16px; font:inherit; cursor:pointer; }
.primary { background:var(--accent); color:#fff; font-weight:600; }
.secondary { background:#1d1f23; color:var(--text); border:1px solid #30333a; }
video { width:100%; margin-top:12px; border-radius:8px; display:none; }
#result { margin-top:16px; border-radius:12px; padding:16px; display:none; }
#result.ok { display:block; background:var(--okbg); border:1px solid #1f4a2c; }
#result.bad { display:block; background:var(--badbg); border:1px solid #5c2427; }
#result h2 { margin:0 0 8px; font-size:17px; }
#result.ok h2 { color:var(--ok); } #result.bad h2 { color:var(--bad); }
dl { display:grid; grid-template-columns:max-content 1fr; gap:4px 14px; margin:8px 0 0; }
dt { color:var(--muted); } dd { margin:0; overflow-wrap:anywhere; }
footer { margin-top:20px; font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
</style>
</head>
<body>
<main>
  <h1>__TITLE__</h1>
  <p class="intro">__INTRO__</p>
  <div class="card">
    <label for="qr">__PASTE__</label>
    <textarea id="qr" spellcheck="false"></textarea>
    <div class="row">
      <button class="primary" id="verify">__VERIFY__</button>
      <button class="secondary" id="scan" hidden>__SCAN__</button>
    </div>
    <video id="cam" playsinline muted></video>
  </div>
  <div id="result" role="status" aria-live="polite"><h2 id="verdict"></h2><dl id="fields"></dl></div>
  <footer>__OFFLINE__<br>__KEYLINE__</footer>
</main>
<script>
"use strict";
const PUB_KEY_B64 = __PUBKEY__;
const KEY_ID = __KEYID__;
const PREFIX = __PREFIX__;
const FIELDS = __FIELDS__;
const T = __STRINGS__;

function bytesFromB64(s) { const b = atob(s); return Uint8Array.from(b, c => c.charCodeAt(0)); }
function bytesFromB64url(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return bytesFromB64(s);
}
function parse(text) {
  text = (text || "").trim();
  if (text.includes("#") && !text.startsWith(PREFIX + ";")) text = text.split("#")[1] || "";
  if (!text.startsWith(PREFIX + ";")) { try { text = decodeURIComponent(text); } catch (e) { return null; } }
  const p = text.split(";");
  if (p.length !== FIELDS.length + 3 || p[0] !== PREFIX) return null;
  const fields = {};
  try { FIELDS.forEach((k, i) => { fields[k] = decodeURIComponent(p[i + 1]); }); } catch (e) { return null; }
  return { fields: fields, kid: p[p.length - 2], sig: p[p.length - 1], message: p.slice(0, -1).join(";") };
}
function show(kind, fields) {
  const box = document.getElementById("result");
  box.className = kind === "ok" ? "ok" : "bad";
  document.getElementById("verdict").textContent = T[kind];
  const dl = document.getElementById("fields");
  dl.textContent = "";
  if (fields && kind === "ok") {
    FIELDS.forEach(k => {
      const dt = document.createElement("dt"); dt.textContent = T["field_" + k];
      const dd = document.createElement("dd"); dd.textContent = fields[k] || "-";
      dl.append(dt, dd);
    });
  }
  box.dataset.result = kind;
}
async function verify(text) {
  const r = parse(text);
  if (!r) return show("bad_format");
  if (r.kid !== KEY_ID) return show("other_key");
  let key;
  try {
    key = await crypto.subtle.importKey("raw", bytesFromB64(PUB_KEY_B64), { name: "Ed25519" }, false, ["verify"]);
  } catch (e) { return show("no_ed25519"); }
  let ok = false;
  try {
    ok = await crypto.subtle.verify({ name: "Ed25519" }, key, bytesFromB64url(r.sig), new TextEncoder().encode(r.message));
  } catch (e) { ok = false; }
  show(ok ? "ok" : "bad_signature", r.fields);
}
document.getElementById("verify").addEventListener("click", () => verify(document.getElementById("qr").value));

// Camera scanning where the browser has a barcode detector (e.g. Chrome on Android)
let stream = null;
if ("BarcodeDetector" in window && navigator.mediaDevices) {
  const btn = document.getElementById("scan");
  const video = document.getElementById("cam");
  btn.hidden = false;
  btn.addEventListener("click", async () => {
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; video.style.display = "none"; btn.textContent = T.scan; return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    } catch (e) { return; }
    video.srcObject = stream; video.style.display = "block"; await video.play(); btn.textContent = T.scan_stop;
    const detector = new BarcodeDetector({ formats: ["qr_code"] });
    const tick = async () => {
      if (!stream) return;
      try {
        const codes = await detector.detect(video);
        if (codes.length) {
          document.getElementById("qr").value = codes[0].rawValue;
          stream.getTracks().forEach(t => t.stop()); stream = null; video.style.display = "none"; btn.textContent = T.scan;
          return verify(codes[0].rawValue);
        }
      } catch (e) { /* keep scanning */ }
      requestAnimationFrame(tick);
    };
    tick();
  });
}
if (location.hash.length > 1) {
  const text = location.hash.slice(1);
  document.getElementById("qr").value = text;
  verify(text);
}
</script>
</body>
</html>
"""


def build_verifier_html(public_key_path: str, lang: str = "ar") -> str:
    public_key = load_public_key(public_key_path)
    key_id = key_id_from_public(public_key)
    strings = {k: t(f"verifier.{k}", lang) for k in _STRINGS}
    js = lambda v: json.dumps(v, ensure_ascii=False).replace("</", "<\\/")
    page = _TEMPLATE
    for token, value in (
        ("__LANG__", html.escape(lang)),
        ("__DIR__", "rtl" if lang == "ar" else "ltr"),
        ("__TITLE__", html.escape(strings["title"])),
        ("__INTRO__", html.escape(strings["intro"])),
        ("__PASTE__", html.escape(strings["paste_label"])),
        ("__VERIFY__", html.escape(strings["verify"])),
        ("__SCAN__", html.escape(strings["scan"])),
        ("__OFFLINE__", html.escape(strings["offline_note"])),
        ("__KEYLINE__", html.escape(t("verifier.key_line", lang, key_id=key_id))),
        ("__PUBKEY__", js(public_key_raw_b64(public_key_path))),
        ("__KEYID__", js(key_id)),
        ("__PREFIX__", js(QR_PREFIX)),
        ("__FIELDS__", js(list(QR_FIELDS))),
        ("__STRINGS__", js(strings)),
    ):
        page = page.replace(token, value)
    return page


def export_verifier(directory: str, public_key_path: str, lang: str = "ar") -> Tuple[str, str]:
    """Write DIR/adp_verify.html. Returns (path, key_id)."""
    page = build_verifier_html(public_key_path, lang)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, PAGE_NAME)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)
    return path, key_id_from_public(load_public_key(public_key_path))
