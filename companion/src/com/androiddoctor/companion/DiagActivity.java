package com.androiddoctor.companion;

import android.Manifest;
import android.app.Activity;
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
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.util.Log;
import android.util.Size;
import android.view.WindowManager;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
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
 */
public class DiagActivity extends Activity {
    static final String TAG = "AndroidDoctorCompanion";
    static final List<String> ALL = Arrays.asList("battery", "sensors", "vibration", "audio", "cameras");

    private String runId = "none";
    private TextView view;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        view = new TextView(this);
        view.setTextSize(16);
        view.setPadding(40, 80, 40, 40);
        setContentView(view);

        String id = getIntent().getStringExtra("run_id");
        if (id != null && id.matches("[A-Za-z0-9_-]{1,64}")) {
            runId = id;
        }
        final Set<String> tests = new HashSet<>();
        String requested = getIntent().getStringExtra("tests");
        if (requested == null || requested.trim().isEmpty() || requested.trim().equals("all")) {
            tests.addAll(ALL);
        } else {
            for (String t : requested.split(",")) {
                if (ALL.contains(t.trim())) tests.add(t.trim());
            }
        }
        show("Android Doctor Pro\nRunning tests. Keep the phone still on a table.\n\n");
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
        Log.i(TAG, "ADP|" + o.toString());
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
                "manufacturer", Build.MANUFACTURER, "version", 1));
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
        show("\nFinished. Results were sent to the computer.\n");
        view.postDelayed(this::finish, 2500);
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
