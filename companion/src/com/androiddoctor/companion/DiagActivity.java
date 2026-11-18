package com.androiddoctor.companion;

import android.Manifest;
import android.app.Activity;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.Image;
import android.media.ImageReader;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.provider.MediaStore;
import android.util.Log;
import android.util.Size;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Runs the requested tests and writes one logcat line per result:
 *   ADP|{"type":"result","test":"battery","run":"<run_id>", ...raw readings...}
 * The desktop program judges the readings; this app never decides PASS or FAIL.
 * Intent extras: run_id (letters, digits, '-', '_'), tests ("all" or a comma list of
 * battery,sensors,vibration,audio,cameras).
 *
 * Without USB debugging the app is opened by the technician: from its icon, or from the page the
 * desktop program serves on the Wi-Fi network (adpcompanion://run?host=&port=&token=&run=&tests=).
 * It then waits for a tap on Start, runs the same tests, saves the same lines in
 * Download/AndroidDoctor and, when opened from that page, sends them to the computer (HTTP POST to a
 * private-network address only).
 */
public class DiagActivity extends Activity {
    static final String TAG = "AndroidDoctorCompanion";
    static final List<String> ALL = Arrays.asList("battery", "sensors", "vibration", "audio", "cameras");

    private String runId = "none";
    private TextView view;
    private Button startButton;
    /** Started by the desktop program over ADB (run_id extra): no Start button, results in logcat only. */
    private boolean adbRun = false;
    /** Opened from the desktop program's Wi-Fi page: where the results go. Null = saved file only. */
    private String host = null;
    private int port = 0;
    private String token = null;
    private final StringBuilder lines = new StringBuilder();

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        view = new TextView(this);
        view.setTextSize(16);
        view.setPadding(40, 80, 40, 40);
        startButton = new Button(this);
        startButton.setText("Start / ابدأ");
        startButton.setTextSize(18);
        startButton.setVisibility(android.view.View.GONE);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(view);
        box.addView(startButton);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(box);
        setContentView(scroll);

        Intent intent = getIntent();
        Uri link = intent.getData();
        String requested = intent.getStringExtra("tests");
        String id = intent.getStringExtra("run_id");
        if (id != null && id.matches("[A-Za-z0-9_-]{1,64}")) {
            runId = id;
            adbRun = true;
        } else {
            if (link != null && "adpcompanion".equals(link.getScheme())) {
                readLink(link);
                String t = link.getQueryParameter("tests");
                if (t != null) requested = t;
            }
            if (runId.equals("none")) runId = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        }
        final Set<String> tests = new HashSet<>();
        if (requested == null || requested.trim().isEmpty() || requested.trim().equals("all")) {
            tests.addAll(ALL);
        } else {
            for (String t : requested.split(",")) {
                if (ALL.contains(t.trim())) tests.add(t.trim());
            }
        }
        pending = tests;
        if (adbRun) {
            askPermissionsThenStart();
        } else {
            // Opened by a person (icon or the Wi-Fi page): nothing runs until they tap Start.
            show("Android Doctor Pro\n\n"
                    + (host != null ? "Results will be sent to the computer " + host + "\n"
                            + "سيتم إرسال النتائج إلى الكمبيوتر " + host + "\n\n"
                    : "Results will be saved in Download/AndroidDoctor\n"
                            + "سيتم حفظ النتائج في مجلد Download/AndroidDoctor\n\n")
                    + "Put the phone on a table, then tap Start.\n"
                    + "ضع الجوال على طاولة ثم اضغط ابدأ.\n\n");
            startButton.setVisibility(android.view.View.VISIBLE);
            startButton.setOnClickListener(v -> {
                startButton.setEnabled(false);
                startButton.setVisibility(android.view.View.GONE);
                askPermissionsThenStart();
            });
        }
    }

    /** host must be a private IPv4 address (10/8, 172.16/12, 192.168/16): results never leave the shop network. */
    private void readLink(Uri link) {
        String r = link.getQueryParameter("run");
        if (r != null && r.matches("[A-Za-z0-9_-]{1,64}")) runId = r;
        String h = link.getQueryParameter("host");
        String p = link.getQueryParameter("port");
        String k = link.getQueryParameter("token");
        int pn = 0;
        try {
            pn = p == null ? 0 : Integer.parseInt(p);
        } catch (NumberFormatException ignored) {
        }
        if (isPrivateIpv4(h) && pn >= 1024 && pn <= 65535 && k != null && k.matches("[A-Za-z0-9_-]{8,64}")) {
            host = h;
            port = pn;
            token = k;
        }
    }

    static boolean isPrivateIpv4(String h) {
        if (h == null || !h.matches("\\d{1,3}(\\.\\d{1,3}){3}")) return false;
        String[] p = h.split("\\.");
        int[] o = new int[4];
        for (int i = 0; i < 4; i++) {
            o[i] = Integer.parseInt(p[i]);
            if (o[i] > 255) return false;
        }
        return o[0] == 10 || (o[0] == 172 && o[1] >= 16 && o[1] <= 31) || (o[0] == 192 && o[1] == 168);
    }

    private void askPermissionsThenStart() {
        final Set<String> tests = pending;
        // `adb install -g` / `pm grant` are refused on some phones (MIUI without "USB debugging
        // (Security settings)"): ask for what the requested tests need, then start. A refusal is
        // reported per test as permission_denied, never as a hardware fault.
        List<String> missing = new ArrayList<>();
        if (tests.contains("cameras") && !granted(Manifest.permission.CAMERA)) missing.add(Manifest.permission.CAMERA);
        if (tests.contains("audio") && !granted(Manifest.permission.RECORD_AUDIO)) missing.add(Manifest.permission.RECORD_AUDIO);
        if (missing.isEmpty()) {
            start();
        } else {
            show("Android Doctor Pro\nTap Allow so the camera and microphone tests can run.\n\n");
            requestPermissions(missing.toArray(new String[0]), PERMISSION_REQUEST);
        }
    }

    static final int PERMISSION_REQUEST = 1;
    private Set<String> pending;
    private boolean started = false;

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        if (requestCode == PERMISSION_REQUEST) start();
    }

    private void start() {
        if (started) return;
        started = true;
        show("Android Doctor Pro\nRunning tests. Keep the phone still on a table.\n\n");
        final Set<String> tests = pending;
        new Thread(() -> runAll(tests), "adp-diag").start();
    }

    // ------------------------------------------------------------------ plumbing
    private void show(final String s) {
        runOnUiThread(() -> view.append(s));
    }

    private void emit(JSONObject o) {
        try {
            o.put("run", runId);
        } catch (JSONException ignored) {
        }
        String line = "ADP|" + o.toString();
        Log.i(TAG, line);
        synchronized (lines) {
            lines.append(line).append('\n');
        }
    }

    private static JSONObject obj(Object... kv) {
        JSONObject o = new JSONObject();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            try {
                o.put(String.valueOf(kv[i]), kv[i + 1]);
            } catch (JSONException ignored) {
            }
        }
        return o;
    }

    private void safe(String name, Callable<JSONObject> test) {
        show(name + "... ");
        JSONObject r;
        try {
            r = test.call();
            show("done\n");
        } catch (Throwable e) {
            r = obj("error", e.getClass().getSimpleName() + ": " + e.getMessage());
            show("error\n");
        }
        try {
            r.put("type", "result");
            r.put("test", name);
        } catch (JSONException ignored) {
        }
        emit(r);
    }

    private void runAll(Set<String> tests) {
        emit(obj("type", "start", "sdk", Build.VERSION.SDK_INT, "model", Build.MODEL,
                "manufacturer", Build.MANUFACTURER, "brand", Build.BRAND, "device", Build.DEVICE,
                "version", 2, "channel", adbRun ? "adb" : (host != null ? "wifi" : "file"),
                "time", System.currentTimeMillis()));
        for (String t : ALL) {
            if (!tests.contains(t)) continue;
            switch (t) {
                case "battery": safe(t, this::battery); break;
                case "sensors": safe(t, this::sensors); break;
                case "vibration": safe(t, this::vibration); break;
                case "audio": safe(t, this::audio); break;
                case "cameras": safe(t, this::cameras); break;
                default: break;
            }
        }
        emit(obj("type", "done"));
        if (adbRun) {
            show("\nFinished. Results were sent to the computer.\n");
            view.postDelayed(this::finish, 2500);
            return;
        }
        String text;
        synchronized (lines) {
            text = lines.toString();
        }
        String saved = saveFile(text);
        show(saved != null ? "\nSaved / تم الحفظ: " + saved + "\n" : "\nCould not save the file / تعذر حفظ الملف\n");
        if (host != null) {
            String sent = send(text);
            show(sent == null ? "\nSent to the computer / تم الإرسال إلى الكمبيوتر\n"
                    : "\nNot sent / لم يتم الإرسال: " + sent
                    + "\nImport the saved file on the computer / استورد الملف المحفوظ على الكمبيوتر\n");
        }
        show("\nYou can close the app / يمكنك إغلاق التطبيق\n");
    }

    /** Download/AndroidDoctor/ADP_<model>_<time>.txt (Android 10+), else the app's own folder. Path or null. */
    private String saveFile(String text) {
        String name = "ADP_" + Build.MODEL.replaceAll("[^A-Za-z0-9_-]", "_") + "_"
                + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()) + ".txt";
        byte[] data = text.getBytes(StandardCharsets.UTF_8);
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues v = new ContentValues();
                v.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
                v.put(MediaStore.MediaColumns.MIME_TYPE, "text/plain");
                v.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/AndroidDoctor");
                Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                if (uri == null) return null;
                try (OutputStream out = getContentResolver().openOutputStream(uri)) {
                    if (out == null) return null;
                    out.write(data);
                }
                return Environment.DIRECTORY_DOWNLOADS + "/AndroidDoctor/" + name;
            }
            File dir = getExternalFilesDir(null);
            if (dir == null) return null;
            File f = new File(dir, name);
            try (FileOutputStream out = new FileOutputStream(f)) {
                out.write(data);
            }
            return f.getAbsolutePath();
        } catch (Exception e) {
            Log.w(TAG, "save failed: " + e);
            return null;
        }
    }

    /** POSTs the lines to the computer, 3 tries. Null when the computer answered 200, else the reason. */
    private String send(String text) {
        String reason = "";
        for (int attempt = 0; attempt < 3; attempt++) {
            HttpURLConnection c = null;
            try {
                URL url = new URL("http://" + host + ":" + port + "/result?token=" + token);
                c = (HttpURLConnection) url.openConnection();
                c.setConnectTimeout(5000);
                c.setReadTimeout(10000);
                c.setDoOutput(true);
                c.setRequestMethod("POST");
                c.setRequestProperty("Content-Type", "text/plain; charset=utf-8");
                byte[] body = text.getBytes(StandardCharsets.UTF_8);
                c.setFixedLengthStreamingMode(body.length);
                try (OutputStream out = c.getOutputStream()) {
                    out.write(body);
                }
                int code = c.getResponseCode();
                if (code == 200) return null;
                reason = "HTTP " + code;
                if (code == 403 || code == 409) return reason;  // wrong session or already received: no retry
            } catch (Exception e) {
                reason = e.getClass().getSimpleName() + ": " + e.getMessage();
            } finally {
                if (c != null) c.disconnect();
            }
            try {
                Thread.sleep(2000);
            } catch (InterruptedException ie) {
                return reason;
            }
        }
        return reason;
    }

    private boolean granted(String permission) {
        return checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    // ------------------------------------------------------------------ battery
    private JSONObject battery() throws JSONException {
        BatteryManager bm = (BatteryManager) getSystemService(Context.BATTERY_SERVICE);
        JSONObject r = new JSONObject();
        // Integer.MIN_VALUE (or 0 on some devices) means "not supported by this fuel gauge"
        r.put("charge_counter_uah", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER));
        r.put("capacity_pct", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY));
        r.put("current_now_ua", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW));
        Intent i = registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (i != null) {
            r.put("level", i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1));
            r.put("scale", i.getIntExtra(BatteryManager.EXTRA_SCALE, -1));
            r.put("voltage_mv", i.getIntExtra(BatteryManager.EXTRA_VOLTAGE, -1));
            r.put("temp_decic", i.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Integer.MIN_VALUE));
            r.put("health", i.getIntExtra(BatteryManager.EXTRA_HEALTH, -1));
            r.put("status", i.getIntExtra(BatteryManager.EXTRA_STATUS, -1));
            r.put("plugged", i.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1));
            if (Build.VERSION.SDK_INT >= 34) {
                r.put("cycle_count", i.getIntExtra("android.os.extra.CYCLE_COUNT", -1));
            }
        }
        return r;
    }

    // ------------------------------------------------------------------ sensors
    private static final int[] SENSOR_TYPES = {Sensor.TYPE_ACCELEROMETER, Sensor.TYPE_GYROSCOPE,
            Sensor.TYPE_MAGNETIC_FIELD, Sensor.TYPE_PROXIMITY, Sensor.TYPE_LIGHT, Sensor.TYPE_PRESSURE};
    private static final String[] SENSOR_NAMES = {"accelerometer", "gyroscope", "magnetometer",
            "proximity", "light", "barometer"};

    /** Listens to one sensor for `ms` and returns events / min / max of values[0]. */
    private JSONObject sample(SensorManager sm, Sensor s, int ms) throws Exception {
        final int[] events = {0};
        final float[] range = {Float.MAX_VALUE, -Float.MAX_VALUE};
        HandlerThread ht = new HandlerThread("adp-sensor");
        ht.start();
        SensorEventListener l = new SensorEventListener() {
            @Override
            public void onSensorChanged(SensorEvent e) {
                synchronized (range) {
                    events[0]++;
                    range[0] = Math.min(range[0], e.values[0]);
                    range[1] = Math.max(range[1], e.values[0]);
                }
            }

            @Override
            public void onAccuracyChanged(Sensor sensor, int accuracy) {
            }
        };
        boolean registered = sm.registerListener(l, s, SensorManager.SENSOR_DELAY_GAME, new Handler(ht.getLooper()));
        Thread.sleep(ms);
        sm.unregisterListener(l);
        ht.quitSafely();
        JSONObject r = obj("present", true, "name", s.getName(), "vendor", s.getVendor(), "registered", registered);
        synchronized (range) {
            r.put("events", events[0]);
            if (events[0] > 0) {
                r.put("min", range[0]);
                r.put("max", range[1]);
            }
        }
        return r;
    }

    private JSONObject sensors() throws Exception {
        SensorManager sm = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        JSONObject r = new JSONObject();
        for (int i = 0; i < SENSOR_TYPES.length; i++) {
            Sensor s = sm.getDefaultSensor(SENSOR_TYPES[i]);
            r.put(SENSOR_NAMES[i], s == null ? obj("present", false) : sample(sm, s, 2500));
        }
        return r;
    }

    // ------------------------------------------------------------------ vibration (seen by the accelerometer)
    private double[] accelStd(SensorManager sm, Sensor acc, int ms, Runnable during) throws Exception {
        final List<Double> mags = new ArrayList<>();
        HandlerThread ht = new HandlerThread("adp-vib");
        ht.start();
        SensorEventListener l = new SensorEventListener() {
            @Override
            public void onSensorChanged(SensorEvent e) {
                synchronized (mags) {
                    mags.add(Math.sqrt(e.values[0] * e.values[0] + e.values[1] * e.values[1] + e.values[2] * e.values[2]));
                }
            }

            @Override
            public void onAccuracyChanged(Sensor sensor, int accuracy) {
            }
        };
        sm.registerListener(l, acc, SensorManager.SENSOR_DELAY_FASTEST, new Handler(ht.getLooper()));
        Thread.sleep(200);
        synchronized (mags) {
            mags.clear();
        }
        if (during != null) during.run();
        Thread.sleep(ms);
        sm.unregisterListener(l);
        ht.quitSafely();
        synchronized (mags) {
            int n = mags.size();
            if (n < 2) return new double[]{n, Double.NaN};
            double mean = 0;
            for (double m : mags) mean += m;
            mean /= n;
            double var = 0;
            for (double m : mags) var += (m - mean) * (m - mean);
            return new double[]{n, Math.sqrt(var / (n - 1))};
        }
    }

    private JSONObject vibration() throws Exception {
        Vibrator vib = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
        SensorManager sm = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        Sensor acc = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
        JSONObject r = obj("has_vibrator", vib != null && vib.hasVibrator(), "has_accelerometer", acc != null);
        if (vib == null || !vib.hasVibrator() || acc == null) return r;
        double[] still = accelStd(sm, acc, 1500, null);
        double[] buzzing = accelStd(sm, acc, 1500, () -> {
            if (Build.VERSION.SDK_INT >= 26) {
                vib.vibrate(VibrationEffect.createOneShot(1500, VibrationEffect.DEFAULT_AMPLITUDE));
            } else {
                vib.vibrate(1500);
            }
        });
        vib.cancel();
        r.put("still_samples", (int) still[0]);
        r.put("still_std", still[1]);
        r.put("vibrating_samples", (int) buzzing[0]);
        r.put("vibrating_std", buzzing[1]);
        return r;
    }

    // ------------------------------------------------------------------ speaker -> microphone loop
    private static double goertzel(short[] x, int n, int sampleRate, double freq) {
        double k = 2 * Math.cos(2 * Math.PI * freq / sampleRate);
        double s1 = 0, s2 = 0;
        for (int i = 0; i < n; i++) {
            double s0 = x[i] / 32768.0 + k * s1 - s2;
            s2 = s1;
            s1 = s0;
        }
        return (s1 * s1 + s2 * s2 - k * s1 * s2) / ((double) n * n);
    }

    private static int readFully(AudioRecord rec, short[] buf, int n) {
        int got = 0;
        while (got < n) {
            int r = rec.read(buf, got, n - got);
            if (r <= 0) break;
            got += r;
        }
        return got;
    }

    @SuppressWarnings("deprecation")
    private JSONObject audio() throws Exception {
        if (!granted(Manifest.permission.RECORD_AUDIO)) return obj("error", "permission_denied:RECORD_AUDIO");
        final int sr = 48000;
        final double tone = 1000.0;
        AudioManager am = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
        int stream = AudioManager.STREAM_MUSIC;
        int oldVolume = am.getStreamVolume(stream);
        int maxVolume = am.getStreamMaxVolume(stream);
        int minBuf = AudioRecord.getMinBufferSize(sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord rec = new AudioRecord(MediaRecorder.AudioSource.MIC, sr, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, Math.max(minBuf, sr));
        if (rec.getState() != AudioRecord.STATE_INITIALIZED) {
            rec.release();
            return obj("error", "mic_init_failed");
        }
        int toneSamples = (int) (sr * 1.6);
        short[] wave = new short[toneSamples];
        for (int i = 0; i < toneSamples; i++) {
            wave[i] = (short) (0.6 * 32767 * Math.sin(2 * Math.PI * tone * i / sr));
        }
        AudioTrack track = new AudioTrack(stream, sr, AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT, toneSamples * 2, AudioTrack.MODE_STATIC);
        JSONObject r = obj("sample_rate", sr, "tone_hz", tone, "volume_before", oldVolume, "volume_max", maxVolume);
        try {
            am.setStreamVolume(stream, Math.max(1, (int) Math.round(maxVolume * 0.8)), 0);
            int n = sr / 2;
            short[] quiet = new short[n];
            short[] loud = new short[n];
            rec.startRecording();
            readFully(rec, new short[sr / 5], sr / 5);            // let the mic settle
            int q = readFully(rec, quiet, n);                       // 0.5 s without the tone
            if (track.write(wave, 0, toneSamples) != toneSamples) return obj("error", "speaker_write_failed");
            track.play();
            readFully(rec, new short[sr / 5], sr / 5);            // skip the start of the tone
            int l = readFully(rec, loud, n);                        // 0.5 s with the tone
            track.stop();
            rec.stop();
            double pQuiet = goertzel(quiet, q, sr, tone);
            double pLoud = goertzel(loud, l, sr, tone);
            r.put("samples_quiet", q);
            r.put("samples_tone", l);
            r.put("tone_power_quiet", pQuiet);
            r.put("tone_power_playing", pLoud);
            r.put("snr_db", 10 * Math.log10(Math.max(pLoud, 1e-15) / Math.max(pQuiet, 1e-15)));
        } finally {
            am.setStreamVolume(stream, oldVolume, 0);
            track.release();
            rec.release();
        }
        return r;
    }

    // ------------------------------------------------------------------ cameras (one frame each; nothing stored)
    @SuppressWarnings("deprecation")
    private JSONObject camera(CameraManager cm, String id) throws Exception {
        CameraCharacteristics ch = cm.getCameraCharacteristics(id);
        Integer facing = ch.get(CameraCharacteristics.LENS_FACING);
        JSONObject r = obj("id", id, "facing", facing == null ? "unknown"
                : facing == CameraCharacteristics.LENS_FACING_FRONT ? "front"
                : facing == CameraCharacteristics.LENS_FACING_BACK ? "back" : "external");
        StreamConfigurationMap map = ch.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        Size[] sizes = map == null ? null : map.getOutputSizes(ImageFormat.YUV_420_888);
        if (sizes == null || sizes.length == 0) {
            r.put("error", "no_yuv_output");
            return r;
        }
        Size pick = sizes[0];
        for (Size s : sizes) {
            long area = (long) s.getWidth() * s.getHeight();
            long best = (long) pick.getWidth() * pick.getHeight();
            if (s.getWidth() >= 320 && (pick.getWidth() < 320 || area < best)) pick = s;
        }
        r.put("width", pick.getWidth());
        r.put("height", pick.getHeight());

        HandlerThread ht = new HandlerThread("adp-cam-" + id);
        ht.start();
        Handler h = new Handler(ht.getLooper());
        ImageReader reader = ImageReader.newInstance(pick.getWidth(), pick.getHeight(), ImageFormat.YUV_420_888, 3);
        final AtomicReference<CameraDevice> device = new AtomicReference<>();
        final AtomicReference<String> error = new AtomicReference<>();
        final AtomicReference<CameraCaptureSession> session = new AtomicReference<>();
        final CountDownLatch opened = new CountDownLatch(1);
        final CountDownLatch measured = new CountDownLatch(1);
        final int[] frames = {0};
        final double[] stats = {Double.NaN, Double.NaN};
        final long start = System.currentTimeMillis();
        try {
            reader.setOnImageAvailableListener(rd -> {
                Image img = rd.acquireLatestImage();
                if (img == null) return;
                try {
                    frames[0]++;
                    // let auto exposure settle: measure frame 15 or after 1.5 s
                    if (measured.getCount() > 0 && (frames[0] >= 15 || System.currentTimeMillis() - start > 1500)) {
                        Image.Plane y = img.getPlanes()[0];
                        ByteBuffer buf = y.getBuffer();
                        int rs = y.getRowStride(), ps = y.getPixelStride();
                        double sum = 0, sq = 0;
                        long cnt = 0;
                        for (int row = 0; row < img.getHeight(); row += 4) {
                            for (int col = 0; col < img.getWidth(); col += 4) {
                                int v = buf.get(row * rs + col * ps) & 0xFF;
                                sum += v;
                                sq += (double) v * v;
                                cnt++;
                            }
                        }
                        double mean = sum / cnt;
                        stats[0] = mean;
                        stats[1] = Math.sqrt(Math.max(0, sq / cnt - mean * mean));
                        measured.countDown();
                    }
                } finally {
                    img.close();
                }
            }, h);
            cm.openCamera(id, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice d) {
                    device.set(d);
                    opened.countDown();
                }

                @Override
                public void onDisconnected(CameraDevice d) {
                    error.compareAndSet(null, "disconnected");
                    d.close();
                    opened.countDown();
                }

                @Override
                public void onError(CameraDevice d, int code) {
                    error.compareAndSet(null, "open_error_" + code);
                    d.close();
                    opened.countDown();
                }
            }, h);
            if (!opened.await(5, TimeUnit.SECONDS)) error.compareAndSet(null, "open_timeout");
            CameraDevice d = device.get();
            if (d != null && error.get() == null) {
                final CaptureRequest.Builder req = d.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
                req.addTarget(reader.getSurface());
                d.createCaptureSession(Arrays.asList(reader.getSurface()), new CameraCaptureSession.StateCallback() {
                    @Override
                    public void onConfigured(CameraCaptureSession s) {
                        session.set(s);
                        try {
                            s.setRepeatingRequest(req.build(), null, h);
                        } catch (Exception e) {
                            error.compareAndSet(null, "capture_failed: " + e.getMessage());
                            measured.countDown();
                        }
                    }

                    @Override
                    public void onConfigureFailed(CameraCaptureSession s) {
                        error.compareAndSet(null, "session_configure_failed");
                        measured.countDown();
                    }
                }, h);
                if (!measured.await(5, TimeUnit.SECONDS)) error.compareAndSet(null, "no_frame_timeout");
            }
        } finally {
            CameraCaptureSession s = session.get();
            if (s != null) s.close();
            CameraDevice d = device.get();
            if (d != null) d.close();
            reader.close();
            ht.quitSafely();
        }
        r.put("frames", frames[0]);
        if (!Double.isNaN(stats[0])) {
            r.put("mean_luma", stats[0]);
            r.put("std_luma", stats[1]);
        }
        if (error.get() != null) r.put("error", error.get());
        return r;
    }

    private JSONObject cameras() throws Exception {
        if (!granted(Manifest.permission.CAMERA)) return obj("error", "permission_denied:CAMERA");
        CameraManager cm = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
        JSONArray list = new JSONArray();
        for (String id : cm.getCameraIdList()) {
            try {
                list.put(camera(cm, id));
            } catch (Throwable e) {
                list.put(obj("id", id, "error", e.getClass().getSimpleName() + ": " + e.getMessage()));
            }
        }
        return obj("cameras", list);
    }
}
