package com.gil.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Locale;

/**
 * G.I.L. on Android — standalone.
 *
 * The app starts GIL's own web UI (bundled in assets) against a local server it
 * runs itself, so the phone needs no PC. The brain calls Groq; the actions use
 * Android Intents.
 */
public class MainActivity extends AppCompatActivity {

    private static final String PREFS = "gil";
    private static final String KEY_API = "groq_key";

    private WebView web;
    private GilServer server;
    private GilBrain brain;
    private GilActions actions;
    private TextToSpeech tts;
    private boolean voiceOn = true;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);

        brain = new GilBrain();
        brain.setApiKey(prefs().getString(KEY_API, ""));
        actions = new GilActions(this);
        tts = new TextToSpeech(this, status -> {
            if (status == TextToSpeech.SUCCESS) tts.setLanguage(Locale.getDefault());
        });

        ActivityCompat.requestPermissions(this, new String[]{
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.POST_NOTIFICATIONS}, 1);

        server = new GilServer(this, this::rpc);
        try {
            server.start();
        } catch (Exception e) {
            throw new RuntimeException("GIL server failed to start", e);
        }

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        web.setBackgroundColor(Color.parseColor("#15161B"));
        web.setWebViewClient(new WebViewClient());
        web.loadUrl("http://127.0.0.1:" + server.getPort() + "/");
        setContentView(web);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** The JSON-RPC surface the web UI calls — the Java twin of chat_web.py. */
    private Object rpc(String method, JSONArray args) throws Exception {
        switch (method) {
            case "ready": {
                JSONObject o = new JSONObject();
                o.put("version", "1.0-mobile");
                o.put("voice", voiceOn);
                o.put("sessions", new JSONArray());
                if (!brain.hasKey()) {
                    server.push("gil.addMessage", msg(
                            "**Welcome to G.I.L.** ◈\n\nI need a free Groq API key "
                          + "to think. Get one at `console.groq.com/keys`, then send it "
                          + "to me here as: `/key <your-key>`", "gil"));
                }
                return o;
            }
            case "send":  return send(args.optString(0, ""));
            case "command": return command(args.optString(0, ""), args.optString(1, ""));
            case "toggle_voice": {
                voiceOn = !voiceOn;
                if (!voiceOn && tts != null) tts.stop();
                server.push("gil.setVoice", voiceOn);
                return voiceOn;
            }
            case "new_chat": {
                brain.clearHistory();
                return true;
            }
            default:
                return null;    // unknown methods are harmless no-ops
        }
    }

    private JSONObject msg(String text, String sender) throws Exception {
        return new JSONObject().put("text", text).put("sender", sender);
    }

    private Object send(String text) {
        if (text.trim().isEmpty()) return null;
        if (text.startsWith("/key ")) {
            String k = text.substring(5).trim();
            prefs().edit().putString(KEY_API, k).apply();
            brain.setApiKey(k);
            pushGil("Key saved — I'm awake. What do you need?");
            return null;
        }
        new Thread(() -> {
            try {
                JSONObject reply = brain.query(text);
                String speech = reply.optString("speech", "");
                String report = reply.optString("report", "");

                // run whatever GIL asked for, and collect what REALLY happened
                StringBuilder done = new StringBuilder();
                JSONArray acts = reply.optJSONArray("actions");
                if (acts != null) {
                    for (int i = 0; i < acts.length(); i++) {
                        JSONObject a = acts.optJSONObject(i);
                        if (a == null) continue;
                        final String name = a.optString("action", "");
                        final String target = a.optString("target", "");
                        final String[] out = new String[1];
                        runOnUiThread(() -> out[0] = actions.run(name, target));
                        Thread.sleep(120);          // let the UI thread finish
                        if (out[0] != null && !out[0].isEmpty()) {
                            done.append(done.length() > 0 ? "\n" : "").append(out[0]);
                        }
                    }
                }

                StringBuilder shown = new StringBuilder();
                if (!report.isEmpty()) shown.append(report);
                else if (!speech.isEmpty()) shown.append(speech);
                if (done.length() > 0) {
                    if (shown.length() > 0) shown.append("\n\n");
                    shown.append("_").append(done).append("_");
                }
                if (shown.length() == 0) shown.append("Done.");

                pushGil(shown.toString());
                if (voiceOn && !speech.isEmpty()) speak(speech);
            } catch (Exception e) {
                pushGil("Something went wrong handling that.");
            }
        }, "GilTurn").start();
        return null;
    }

    private Object command(String name, String arg) {
        switch (name) {
            case "help":
                pushGil("**What I can do on your phone**\n"
                      + "- Open apps, links and settings\n"
                      + "- Search the web · show places on the map\n"
                      + "- Draft a text or email · start a call\n"
                      + "- Set alarms and timers · open the camera\n"
                      + "- Copy or share text\n"
                      + "- Answer anything, in any language\n\n"
                      + "`/key <groq-key>` sets my API key · `/clear` starts fresh");
                return null;
            case "clear":
                brain.clearHistory();
                return null;
            default:
                return send("/" + name + (arg.isEmpty() ? "" : " " + arg));
        }
    }

    private void pushGil(String text) {
        try {
            server.push("gil.addMessage", msg(text, "gil"));
        } catch (Exception ignored) {}
    }

    private void speak(String text) {
        if (tts == null) return;
        String clean = text.replaceAll("[*#`_>]", "").trim();
        if (!clean.isEmpty()) {
            tts.speak(clean, TextToSpeech.QUEUE_FLUSH, null, "gil");
        }
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (tts != null) { tts.stop(); tts.shutdown(); }
        if (server != null) server.stop();
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(int req, @NonNull String[] p,
                                           @NonNull int[] r) {
        super.onRequestPermissionsResult(req, p, r);
    }
}
