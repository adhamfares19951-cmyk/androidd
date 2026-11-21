"""Offline-first schematics / datasheet lookup: real files win, nothing is invented,
and an on-demand download is verified against its published checksum."""
import hashlib
import json
import os
import tempfile
import unittest

from core.result import Status
from core.schematics_db import SchematicsDatabase, SRC_LOCAL, SRC_CACHE, SRC_ON_DEMAND, SRC_MISSING


def _index(tmp, docs):
    p = os.path.join(tmp, "idx.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"schema": 1, "documents": docs}, f)
    return p


class SchematicsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.local = os.path.join(self.d, "local"); os.makedirs(self.local)
        self.cache = os.path.join(self.d, "cache"); os.makedirs(self.cache)

    def tearDown(self):
        self.tmp.cleanup()

    def _db(self, docs):
        return SchematicsDatabase(index_paths=[_index(self.d, docs)],
                                  local_dirs=[self.local], cache_dir=self.cache)

    def _write(self, base, rel, data=b"PDF"):
        full = os.path.join(base, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        return full

    # ---- lookup -------------------------------------------------------
    def test_lookup_by_model_number(self):
        db = self._db([{"id": "a", "doc_type": "schematic", "component": "main-board",
                        "models": ["M2003J15SC"], "file": "rn9.pdf"}])
        self.assertEqual([d.id for d in db.lookup(model="M2003J15SC")], ["a"])
        self.assertEqual(db.lookup(model="SM-G973F"), [])

    def test_codename_reuse_across_makers_is_isolated(self):
        db = self._db([
            {"id": "moto", "doc_type": "schematic", "component": "board", "codenames": ["merlin"],
             "manufacturer": "motorola", "file": "m.pdf"},
            {"id": "xiaomi", "doc_type": "schematic", "component": "board", "codenames": ["merlin"],
             "manufacturer": "xiaomi", "file": "x.pdf"},
        ])
        self.assertEqual([d.id for d in db.lookup(codename="merlin", manufacturer="xiaomi")], ["xiaomi"])

    def test_datasheet_by_ic_part_number_needs_no_device(self):
        db = self._db([{"id": "pm", "doc_type": "datasheet", "component": "PM6150",
                        "file": "pm6150.pdf"}])
        self.assertEqual([d.id for d in db.lookup(component="pm6150", doc_type="datasheet")], ["pm"])

    # ---- offline-first resolve ---------------------------------------
    def test_local_folder_wins(self):
        self._write(self.local, "rn9.pdf")
        self._write(self.cache, "rn9.pdf")       # also in cache, but local must win
        db = self._db([{"id": "a", "doc_type": "schematic", "component": "b",
                        "models": ["X"], "file": "rn9.pdf"}])
        r = db.find(model="X")[0]
        self.assertEqual(r.status, Status.PASS)
        self.assertEqual(r.origin, SRC_LOCAL)
        self.assertTrue(r.path.startswith(self.local))

    def test_cache_used_when_not_local(self):
        self._write(self.cache, "rn9.pdf")
        db = self._db([{"id": "a", "doc_type": "schematic", "component": "b",
                        "models": ["X"], "file": "rn9.pdf"}])
        r = db.find(model="X")[0]
        self.assertEqual((r.status, r.origin), (Status.PASS, SRC_CACHE))

    def test_source_only_is_unknown_on_demand(self):
        db = self._db([{"id": "a", "doc_type": "schematic", "component": "b", "models": ["X"],
                        "file": "rn9.pdf", "source": "https://example/rn9.pdf"}])
        r = db.find(model="X")[0]
        self.assertEqual((r.status, r.origin), (Status.UNKNOWN, SRC_ON_DEMAND))

    def test_no_file_no_source_entry_is_rejected(self):
        db = self._db([{"id": "a", "doc_type": "schematic", "component": "b", "models": ["X"]}])
        self.assertEqual(db.lookup(model="X"), [])     # nothing to point at -> not loaded
        self.assertTrue(db.errors)

    # ---- validation ---------------------------------------------------
    def test_unsafe_paths_and_bad_types_are_rejected(self):
        db = self._db([
            {"id": "bad1", "doc_type": "schematic", "component": "b", "file": "../secret.pdf"},
            {"id": "bad2", "doc_type": "nonsense", "component": "b", "file": "x.pdf"},
            {"doc_type": "schematic", "component": "b", "file": "noid.pdf"},
        ])
        self.assertEqual(db.docs, [])
        self.assertEqual(len(db.errors), 3)

    # ---- on-demand fetch + integrity ---------------------------------
    def test_fetch_verifies_checksum_and_caches(self):
        payload = b"%PDF-1.7 real schematic"
        good = hashlib.sha256(payload).hexdigest()
        db = self._db([{"id": "a", "doc_type": "schematic", "component": "b", "models": ["X"],
                        "file": "sub/rn9.pdf", "source": "https://x/rn9.pdf", "sha256": good}])
        db._download = lambda url, timeout=30: payload
        doc = db.lookup(model="X")[0]
        r = db.fetch(doc)
        self.assertEqual(r.status, Status.PASS)
        self.assertTrue(os.path.isfile(os.path.join(self.cache, "sub", "rn9.pdf")))
        # now resolve finds it in cache without any network
        self.assertEqual(db.resolve(doc).status, Status.PASS)

    def test_fetch_rejects_checksum_mismatch(self):
        db = self._db([{"id": "a", "doc_type": "schematic", "component": "b", "models": ["X"],
                        "file": "rn9.pdf", "source": "https://x/rn9.pdf", "sha256": "deadbeef"}])
        db._download = lambda url, timeout=30: b"tampered"
        r = db.fetch(db.lookup(model="X")[0])
        self.assertEqual(r.status, Status.FAIL)                       # a real, evidence-backed fault
        self.assertFalse(os.path.exists(os.path.join(self.cache, "rn9.pdf")))  # nothing cached

    def test_result_converts_to_check_result(self):
        self._write(self.local, "rn9.pdf")
        db = self._db([{"id": "a", "doc_type": "schematic", "component": "main-board",
                        "models": ["X"], "file": "rn9.pdf"}])
        cr = db.find(model="X")[0].to_check_result()
        self.assertEqual(cr.check_id, "schematic.lookup")
        self.assertEqual(cr.status, Status.PASS)
        self.assertTrue(cr.evidence)


class AddUserDocumentTests(unittest.TestCase):
    """The bundled index is empty: a technician's own files are the only documents, so adding one must work
    and must never break what is already in the technician's index."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.index = os.path.join(self.d, "user_index.json")
        self.local = os.path.join(self.d, "schematics")
        self.src = os.path.join(self.d, "Downloads", "A52 board.pdf")
        os.makedirs(os.path.dirname(self.src))
        with open(self.src, "wb") as f:
            f.write(b"%PDF-1.4 board")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, **kw):
        from core.schematics_db import add_user_document
        args = dict(src_path=self.src, doc_type="schematic", component="A52 main board", models=["SM-A525F"],
                    index_path=self.index, local_dir=self.local)
        args.update(kw)
        return add_user_document(**args)

    def db(self):
        return SchematicsDatabase(index_paths=[self.index], local_dirs=[self.local], cache_dir=os.path.join(self.d, "c"))

    def test_added_file_is_found_for_the_phone_as_a_real_local_file(self):
        doc, msg = self.add()
        self.assertIsNotNone(doc, msg)
        self.assertEqual(msg.key, "schematic.added")
        res = self.db().find(model="SM-A525F")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].status, Status.PASS)
        self.assertEqual(res[0].origin, SRC_LOCAL)
        with open(res[0].path, "rb") as f:
            self.assertEqual(f.read(), b"%PDF-1.4 board")                 # a copy: the original stays where it was
        self.assertTrue(os.path.isfile(self.src))
        with open(self.index, encoding="utf-8") as f:
            entry = json.load(f)["documents"][0]
        self.assertEqual(entry["sha256"], hashlib.sha256(b"%PDF-1.4 board").hexdigest())

    def test_existing_entries_are_kept_and_names_never_overwrite(self):
        self.add()
        other = os.path.join(self.d, "other", "A52 board.pdf")             # same name, different content
        os.makedirs(os.path.dirname(other))
        with open(other, "wb") as f:
            f.write(b"%PDF-1.4 another")
        doc2, _ = self.add(src_path=other, doc_type="boardview")
        with open(self.index, encoding="utf-8") as f:
            docs = json.load(f)["documents"]
        self.assertEqual(len(docs), 2)
        self.assertEqual(len({d["id"] for d in docs}), 2)
        self.assertNotEqual(docs[0]["file"], docs[1]["file"])
        res = {r.doc.doc_type: r for r in self.db().find(model="SM-A525F")}
        with open(res["schematic"].path, "rb") as f:
            self.assertEqual(f.read(), b"%PDF-1.4 board")                 # the first file was not replaced

    def test_a_datasheet_needs_no_phone(self):
        doc, _ = self.add(doc_type="datasheet", component="PM8150", models=[])
        self.assertIsNotNone(doc)
        self.assertEqual([r.status for r in self.db().find(component="PM8150")], [Status.PASS])

    def test_bad_input_is_refused_and_nothing_is_written(self):
        for kw, key in ((dict(doc_type="photo"), "schematic.add_bad_type"),
                        (dict(component="  "), "schematic.add_no_component"),
                        (dict(src_path=os.path.join(self.d, "missing.pdf")), "schematic.add_no_file")):
            doc, msg = self.add(**kw)
            self.assertIsNone(doc)
            self.assertEqual(msg.key, key)
        self.assertFalse(os.path.exists(self.index))
        self.assertFalse(os.path.exists(self.local))

    def test_a_broken_index_file_is_not_overwritten(self):
        with open(self.index, "w", encoding="utf-8") as f:
            f.write("{ not json")
        doc, msg = self.add()
        self.assertIsNone(doc)
        self.assertEqual(msg.key, "schematic.add_index_unreadable")
        with open(self.index, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ not json")                       # the technician's file is left as it was


if __name__ == "__main__":
    unittest.main()
