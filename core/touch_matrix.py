import os
import time
import logging
from typing import Dict, Any, Optional, Tuple
from core.adb_bridge import ADBBridge

logger = logging.getLogger("TouchMatrix")

HTML_TOUCH_GRID_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Touch Matrix Digitizer Test</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; user-select: none; -webkit-user-select: none; }
        body, html { width: 100%; height: 100%; overflow: hidden; background: #000; font-family: sans-serif; touch-action: none; }
        #header { position: absolute; top: 0; left: 0; right: 0; height: 40px; background: rgba(0,0,0,0.85); color: #fff; display: flex; justify-content: space-between; align-items: center; padding: 0 15px; font-size: 14px; z-index: 10; border-bottom: 1px solid #333; }
        #grid-container { width: 100%; height: 100%; display: grid; grid-template-columns: repeat(8, 1fr); grid-template-rows: repeat(14, 1fr); gap: 2px; padding: 45px 4px 4px 4px; }
        .grid-cell { background: #161b22; border-radius: 3px; transition: background 0.05s ease; }
        .grid-cell.touched { background: #10b981 !important; }
        #modal { display: none; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); background: #1f2937; border: 2px solid #10b981; padding: 25px; border-radius: 12px; text-align: center; color: #fff; z-index: 100; box-shadow: 0 10px 25px rgba(0,0,0,0.8); }
        #modal h2 { color: #10b981; margin-bottom: 10px; font-size: 20px; }
        #modal button { margin-top: 15px; padding: 10px 20px; background: #2563eb; color: #fff; border: none; border-radius: 6px; font-size: 14px; }
    </style>
</head>
<body>
    <div id="header">
        <span id="title">Android Doctor Pro — Touch Test</span>
        <span id="score">0% (0/112)</span>
    </div>
    <div id="grid-container"></div>
    <div id="modal">
        <h2>✓ 100% Digitizer Coverage Passed</h2>
        <p>No dead zones or broken touch lines detected.</p>
        <button onclick="resetGrid()">Repeat Test</button>
    </div>

    <script>
        const cols = 8;
        const rows = 14;
        const total = cols * rows;
        let touchedCount = 0;
        const container = document.getElementById('grid-container');

        for (let i = 0; i < total; i++) {
            const cell = document.createElement('div');
            cell.className = 'grid-cell';
            cell.id = 'cell-' + i;
            container.appendChild(cell);
        }

        function markCellAt(x, y) {
            const el = document.elementFromPoint(x, y);
            if (el && el.classList.contains('grid-cell') && !el.classList.contains('touched')) {
                el.classList.add('touched');
                touchedCount++;
                const pct = Math.round((touchedCount / total) * 100);
                document.getElementById('score').innerText = pct + '% (' + touchedCount + '/' + total + ')';
                if (touchedCount === total) {
                    document.getElementById('modal').style.display = 'block';
                }
            }
        }

        function handleTouch(e) {
            for (let i = 0; i < e.touches.length; i++) {
                markCellAt(e.touches[i].clientX, e.touches[i].clientY);
            }
        }

        window.addEventListener('touchmove', handleTouch, { passive: false });
        window.addEventListener('touchstart', handleTouch, { passive: false });

        window.addEventListener('mousemove', function(e) {
            if (e.buttons === 1) markCellAt(e.clientX, e.clientY);
        });

        function resetGrid() {
            document.querySelectorAll('.grid-cell').forEach(c => c.classList.remove('touched'));
            touchedCount = 0;
            document.getElementById('score').innerText = '0% (0/' + total + ')';
            document.getElementById('modal').style.display = 'none';
        }
    </script>
</body>
</html>
"""


class TouchMatrixTester:
    """
    Deploys a responsive, high-precision Touch Digitizer Grid directly to the phone's browser
    or launches pointer coordinates to test for dead touch zones after screen replacements.
    """
    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()

    def deploy_and_launch_on_phone(self, serial: str) -> Tuple[bool, str]:
        """
        Pushes the standalone HTML5 touch digitizer grid to /sdcard/touch_test.html
        and opens it in the default browser so the technician can touch the actual physical phone screen.
        """
        remote_path = "/sdcard/touch_test.html"
        
        # Save temporary local file
        local_temp = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "touch_matrix_test.html")
        try:
            with open(local_temp, "w", encoding="utf-8") as f:
                f.write(HTML_TOUCH_GRID_TEMPLATE)

            # Push to phone
            ok, msg = self.bridge.push(local_temp, remote_path, serial=serial)
            if not ok:
                # Fallback to /data/local/tmp
                remote_path = "/data/local/tmp/touch_test.html"
                ok, msg = self.bridge.push(local_temp, remote_path, serial=serial)

            if not ok:
                return False, f"Failed to push touch test HTML to phone: {msg}"

            # Open via Chrome / default browser intent
            launch_cmd = (
                f"am start -a android.intent.action.VIEW "
                f"-d 'file://{remote_path}' "
                f"-t 'text/html' 2>/dev/null"
            )
            code, _, _ = self.bridge.shell(launch_cmd, serial=serial, timeout=5)

            if code != 0:
                # Try opening via Chrome specifically
                self.bridge.shell(
                    f"am start -n com.android.chrome/com.google.android.apps.chrome.Main -d 'file://{remote_path}'",
                    serial=serial,
                    timeout=5
                )

            # Also enable Pointer Location overlay so touch lines are displayed simultaneously
            self.bridge.shell("settings put system pointer_location 1", serial=serial, timeout=4)

            logger.info(f"Touch matrix test launched on device {serial}")
            return True, f"Touch Digitizer Matrix launched on device. Swipe across all squares on the phone screen."
        except Exception as e:
            return False, str(e)

    deploy_and_launch_touch_test = deploy_and_launch_on_phone
