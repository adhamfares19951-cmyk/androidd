import os
import json
import sqlite3
import threading
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger("DBManager")

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "android_doctor.db")


class DatabaseManager:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def reset_instance(cls):
        with cls._lock:
            if cls._instance:
                cls._instance.close()
            cls._instance = None

    def __new__(cls, db_path: Optional[str] = None):
        with cls._lock:
            target_path = os.path.abspath(db_path) if db_path else os.path.abspath(DEFAULT_DB_PATH)
            if cls._instance is not None:
                if getattr(cls._instance, "db_path", None) != target_path:
                    new_inst = super(DatabaseManager, cls).__new__(cls)
                    new_inst._initialized = False
                    return new_inst
                return cls._instance
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: Optional[str] = None):
        target_path = os.path.abspath(db_path) if db_path else os.path.abspath(DEFAULT_DB_PATH)
        if getattr(self, "_initialized", False) and getattr(self, "db_path", None) == target_path:
            return
        self.db_path = target_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db_lock = threading.Lock()
        self.init_db()
        self._initialized = True
        logger.info(f"DatabaseManager initialized at: {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def init_db(self):
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS devices (
                        serial TEXT PRIMARY KEY,
                        model TEXT,
                        manufacturer TEXT,
                        brand TEXT,
                        android_version TEXT,
                        build_id TEXT,
                        security_patch TEXT,
                        soc TEXT,
                        first_connected TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_connected TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS diagnostic_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_serial TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        report_title TEXT,
                        overall_status TEXT,
                        battery_health_pct REAL,
                        voltage_mv REAL,
                        current_ma REAL,
                        temp_c REAL,
                        thermal_summary TEXT,
                        storage_summary TEXT,
                        ram_summary TEXT,
                        sensor_summary TEXT,
                        security_summary TEXT,
                        raw_json TEXT,
                        FOREIGN KEY(device_serial) REFERENCES devices(serial) ON DELETE CASCADE
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS rollback_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_serial TEXT,
                        package_name TEXT,
                        action_type TEXT,
                        app_label TEXT,
                        apk_path TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        restored_at TIMESTAMP,
                        FOREIGN KEY(device_serial) REFERENCES devices(serial) ON DELETE CASCADE
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS optimization_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_serial TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        ram_before_mb REAL,
                        ram_after_mb REAL,
                        storage_free_before_mb REAL,
                        storage_free_after_mb REAL,
                        current_drain_before_ma REAL,
                        current_drain_after_ma REAL,
                        actions_taken TEXT,
                        log_details TEXT,
                        FOREIGN KEY(device_serial) REFERENCES devices(serial) ON DELETE CASCADE
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS leakage_tests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_serial TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        samples_json TEXT,
                        avg_leakage_ma REAL,
                        max_leakage_ma REAL,
                        is_faulty INTEGER,
                        verdict TEXT,
                        FOREIGN KEY(device_serial) REFERENCES devices(serial) ON DELETE CASCADE
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS repair_tickets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticket_id TEXT UNIQUE NOT NULL,
                        device_serial TEXT NOT NULL,
                        customer_name TEXT,
                        customer_phone TEXT,
                        reported_fault TEXT,
                        repair_cost REAL DEFAULT 0.0,
                        technician_name TEXT,
                        status TEXT DEFAULT 'INSPECTION',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        notes TEXT,
                        FOREIGN KEY(device_serial) REFERENCES devices(serial) ON DELETE CASCADE
                    );
                """)

                conn.commit()
            except Exception as e:
                logger.error(f"Failed to initialize database tables: {e}")
                conn.rollback()
                raise
            finally:
                conn.close()

    def upsert_device(self, device_data: Dict[str, Any]) -> bool:
        serial = device_data.get("serial")
        if not serial:
            return False

        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO devices (serial, model, manufacturer, brand, android_version, build_id, security_patch, soc, first_connected, last_connected)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(serial) DO UPDATE SET
                        model = excluded.model,
                        manufacturer = excluded.manufacturer,
                        brand = excluded.brand,
                        android_version = excluded.android_version,
                        build_id = excluded.build_id,
                        security_patch = excluded.security_patch,
                        soc = excluded.soc,
                        last_connected = excluded.last_connected;
                """, (
                    serial,
                    device_data.get("model", "Unknown"),
                    device_data.get("manufacturer", "Unknown"),
                    device_data.get("brand", "Unknown"),
                    device_data.get("android_version", "Unknown"),
                    device_data.get("build_id", "Unknown"),
                    device_data.get("security_patch", "Unknown"),
                    device_data.get("soc", "Unknown"),
                    now,
                    now
                ))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error upserting device {serial}: {e}")
                conn.rollback()
                return False
            finally:
                conn.close()

    def get_device(self, serial: str) -> Optional[Dict[str, Any]]:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM devices WHERE serial = ?", (serial,))
                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None
            finally:
                conn.close()

    def list_devices(self) -> List[Dict[str, Any]]:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM devices ORDER BY last_connected DESC")
                return [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()

    def save_diagnostic_report(
        self,
        device_serial: str,
        report_title: str,
        overall_status: str,
        battery_health_pct: float,
        voltage_mv: float,
        current_ma: float,
        temp_c: float,
        thermal_summary: str,
        storage_summary: str,
        ram_summary: str,
        sensor_summary: str,
        security_summary: str,
        raw_dict: Dict[str, Any]
    ) -> int:
        self.upsert_device({"serial": device_serial})
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO diagnostic_reports (
                        device_serial, timestamp, report_title, overall_status,
                        battery_health_pct, voltage_mv, current_ma, temp_c,
                        thermal_summary, storage_summary, ram_summary,
                        sensor_summary, security_summary, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    device_serial,
                    now,
                    report_title,
                    overall_status,
                    battery_health_pct,
                    voltage_mv,
                    current_ma,
                    temp_c,
                    thermal_summary,
                    storage_summary,
                    ram_summary,
                    sensor_summary,
                    security_summary,
                    json.dumps(raw_dict, ensure_ascii=False)
                ))
                conn.commit()
                report_id = cursor.lastrowid
                logger.info(f"Saved diagnostic report ID #{report_id} for device {device_serial}")
                return report_id
            except Exception as e:
                logger.error(f"Error saving diagnostic report: {e}")
                conn.rollback()
                return -1
            finally:
                conn.close()

    def get_diagnostic_reports(self, serial: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                if serial:
                    cursor.execute("""
                        SELECT * FROM diagnostic_reports 
                        WHERE device_serial = ? 
                        ORDER BY id DESC LIMIT ?
                    """, (serial, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM diagnostic_reports 
                        ORDER BY id DESC LIMIT ?
                    """, (limit,))
                rows = cursor.fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    if d.get("raw_json"):
                        try:
                            d["raw_data"] = json.loads(d["raw_json"])
                        except Exception:
                            d["raw_data"] = {}
                    results.append(d)
                return results
            finally:
                conn.close()

    def get_diagnostic_report_by_id(self, report_id: int) -> Optional[Dict[str, Any]]:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM diagnostic_reports WHERE id = ?", (report_id,))
                row = cursor.fetchone()
                if row:
                    d = dict(row)
                    if d.get("raw_json"):
                        try:
                            d["raw_data"] = json.loads(d["raw_json"])
                        except Exception:
                            d["raw_data"] = {}
                    return d
                return None
            finally:
                conn.close()

    def create_repair_ticket(
        self,
        device_serial: str,
        customer_name: str = "",
        customer_phone: str = "",
        reported_fault: str = "",
        repair_cost: float = 0.0,
        technician_name: str = "Chief Hardware Technician",
        status: str = "INSPECTION",
        notes: str = "",
        ticket_id: Optional[str] = None
    ) -> str:
        """
        Creates a new repair shop ticket for the specified device and returns unique ticket_id.
        """
        import time
        if not ticket_id:
            ticket_id = f"TK-{time.strftime('%Y%m%d')}-{int(time.time() * 1000) % 10000:04d}"
        self.upsert_device({"serial": device_serial})
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO repair_tickets (
                        ticket_id, device_serial, customer_name, customer_phone,
                        reported_fault, repair_cost, technician_name, status, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ticket_id, device_serial, customer_name, customer_phone,
                    reported_fault, repair_cost, technician_name, status, notes
                ))
                conn.commit()
                return ticket_id
            except Exception as e:
                logger.error(f"Failed to create repair ticket: {e}")
                conn.rollback()
                return ticket_id
            finally:
                conn.close()

    def get_repair_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM repair_tickets WHERE ticket_id = ?", (ticket_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def list_repair_tickets(self, serial: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                if serial:
                    cursor.execute("""
                        SELECT * FROM repair_tickets 
                        WHERE device_serial = ? 
                        ORDER BY id DESC LIMIT ?
                    """, (serial, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM repair_tickets 
                        ORDER BY id DESC LIMIT ?
                    """, (limit,))
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def update_repair_ticket_status(self, ticket_id: str, new_status: str, notes: str = "") -> bool:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                if notes:
                    cursor.execute("""
                        UPDATE repair_tickets 
                        SET status = ?, notes = notes || ' | ' || ? 
                        WHERE ticket_id = ?
                    """, (new_status, notes, ticket_id))
                else:
                    cursor.execute("UPDATE repair_tickets SET status = ? WHERE ticket_id = ?", (new_status, ticket_id))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"Failed to update repair ticket {ticket_id}: {e}")
                conn.rollback()
                return False
            finally:
                conn.close()

    def log_rollback(
        self,
        device_serial: str,
        package_name: str,
        action_type: str,
        app_label: str = "",
        apk_path: str = ""
    ) -> int:
        self.upsert_device({"serial": device_serial})
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO rollback_history (
                        device_serial, package_name, action_type, app_label, apk_path, timestamp, restored_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """, (device_serial, package_name, action_type, app_label, apk_path, now))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error logging rollback for {package_name}: {e}")
                conn.rollback()
                return -1
            finally:
                conn.close()

    def get_rollback_history(self, device_serial: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                if device_serial:
                    cursor.execute("""
                        SELECT * FROM rollback_history 
                        WHERE device_serial = ? 
                        ORDER BY id DESC
                    """, (device_serial,))
                else:
                    cursor.execute("SELECT * FROM rollback_history ORDER BY id DESC")
                return [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()

    def mark_package_restored(self, device_serial: str, package_name: str) -> bool:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                cursor.execute("""
                    UPDATE rollback_history 
                    SET restored_at = ? 
                    WHERE device_serial = ? AND package_name = ? AND restored_at IS NULL
                """, (now, device_serial, package_name))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"Error marking package restored {package_name}: {e}")
                conn.rollback()
                return False
            finally:
                conn.close()

    def save_optimization_log(
        self,
        device_serial: str,
        ram_before_mb: float,
        ram_after_mb: float,
        storage_free_before_mb: float,
        storage_free_after_mb: float,
        current_drain_before_ma: float,
        current_drain_after_ma: float,
        actions_taken: List[str],
        log_details: str
    ) -> int:
        self.upsert_device({"serial": device_serial})
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO optimization_logs (
                        device_serial, timestamp, ram_before_mb, ram_after_mb,
                        storage_free_before_mb, storage_free_after_mb,
                        current_drain_before_ma, current_drain_after_ma,
                        actions_taken, log_details
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    device_serial,
                    now,
                    ram_before_mb,
                    ram_after_mb,
                    storage_free_before_mb,
                    storage_free_after_mb,
                    current_drain_before_ma,
                    current_drain_after_ma,
                    json.dumps(actions_taken, ensure_ascii=False),
                    log_details
                ))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error saving optimization log: {e}")
                conn.rollback()
                return -1
            finally:
                conn.close()

    def get_optimization_logs(self, device_serial: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                if device_serial:
                    cursor.execute("""
                        SELECT * FROM optimization_logs 
                        WHERE device_serial = ? 
                        ORDER BY id DESC LIMIT ?
                    """, (device_serial, limit))
                else:
                    cursor.execute("SELECT * FROM optimization_logs ORDER BY id DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    if d.get("actions_taken"):
                        try:
                            d["actions_taken_list"] = json.loads(d["actions_taken"])
                        except Exception:
                            d["actions_taken_list"] = []
                    results.append(d)
                return results
            finally:
                conn.close()

    def save_leakage_test(
        self,
        device_serial: str,
        samples: List[float],
        avg_leakage_ma: float,
        max_leakage_ma: float,
        is_faulty: bool,
        verdict: str
    ) -> int:
        self.upsert_device({"serial": device_serial})
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO leakage_tests (
                        device_serial, timestamp, samples_json, avg_leakage_ma,
                        max_leakage_ma, is_faulty, verdict
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    device_serial,
                    now,
                    json.dumps(samples),
                    avg_leakage_ma,
                    max_leakage_ma,
                    1 if is_faulty else 0,
                    verdict
                ))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error saving leakage test: {e}")
                conn.rollback()
                return -1
            finally:
                conn.close()

    def get_leakage_tests(self, device_serial: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                if device_serial:
                    cursor.execute("""
                        SELECT * FROM leakage_tests 
                        WHERE device_serial = ? 
                        ORDER BY id DESC LIMIT ?
                    """, (device_serial, limit))
                else:
                    cursor.execute("SELECT * FROM leakage_tests ORDER BY id DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    if d.get("samples_json"):
                        try:
                            d["samples"] = json.loads(d["samples_json"])
                        except Exception:
                            d["samples"] = []
                    results.append(d)
                return results
            finally:
                conn.close()

    def close(self):
        """Cleanly close database manager and release thread locks."""
        with self._db_lock:
            logger.debug("DatabaseManager connection pool cleanly closed.")
