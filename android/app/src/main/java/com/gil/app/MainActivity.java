package com.gil.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.view.ViewGroup;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

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

        if (brain.hasKey()) startChat();
        else showSetup();
    }

    /**
     * First-run setup. This is native on purpose: the web UI intercepts anything
     * starting with "/" as one of ITS commands, so a "/key ..." message never
     * reaches the app — and routing it through the chat would also echo the key
     * into the visible transcript. Asking here keeps it out of the chat entirely.
     */
    private void showSetup() {
        float d = getResources().getDisplayMetrics().density;
        int pad = (int) (24 * d);

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.parseColor("#15161B"));
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, (int) (pad * 1.8), pad, pad);
        scroll.addView(root);

        TextView logo = new TextView(this);
        logo.setText("◈");
        logo.setTextColor(Color.parseColor("#58C4DD"));
        logo.setTextSize(44);
        root.addView(logo);

        TextView title = new TextView(this);
        title.setText("Welcome to G.I.L.");
        title.setTextColor(Color.parseColor("#E9EAF2"));
        title.setTextSize(26);
        title.setPadding(0, (int) (8 * d), 0, 0);
        root.addView(title);

        TextView hint = new TextView(this);
        hint.setText("I run entirely on this phone — I just need a free Groq API "
                + "key to think with.\n\n"
                + "1.  Tap the button below to open console.groq.com\n"
                + "2.  Sign up (free) and create an API key\n"
                + "3.  Copy it, come back, and paste it here");
        hint.setTextColor(Color.parseColor("#9BA0B4"));
        hint.setTextSize(15);
        hint.setLineSpacing(0, 1.25f);
        hint.setPadding(0, (int) (14 * d), 0, (int) (18 * d));
        root.addView(hint);

        Button getKey = new Button(this);
        getKey.setText("Get a free key");
        getKey.setOnClickListener(v -> startActivity(
                new android.content.Intent(android.content.Intent.ACTION_VIEW,
                        android.net.Uri.parse("https://console.groq.com/keys"))));
        root.addView(getKey);

        final EditText input = new EditText(this);
        input.setHint("paste your key here (gsk_…)");
        input.setTextColor(Color.parseColor("#E9EAF2"));
        input.setHintTextColor(Color.parseColor("#787D91"));
        input.setSingleLine(true);
        LinearLayout.LayoutParams ip = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        ip.topMargin = (int) (18 * d);
        root.addView(input, ip);

        Button go = new Button(this);
        go.setText("Start");
        go.setOnClickListener(v -> {
            String k = input.getText().toString().trim();
            if (k.isEmpty()) {
                Toast.makeText(this, "Paste your key first", Toast.LENGTH_SHORT).show();
                return;
            }
            prefs().edit().putString(KEY_API, k).apply();
            brain.setApiKey(k);
            startChat();
        });
        LinearLayout.LayoutParams gp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        gp.topMargin = (int) (12 * d);
        root.addView(go, gp);

        setContentView(scroll);
    }

    /** Boot the local server and load GIL's real UI over it. */
    @SuppressLint("SetJavaScriptEnabled")
    private void startChat() {
        if (server == null) {
            server = new GilServer(this, this::rpc);
            try {
                server.start();
            } catch (Exception e) {
                Toast.makeText(this, "GIL couldn't start its server.",
                        Toast.LENGTH_LONG).show();
                return;
            }
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

                        // picture generation runs HERE (not on the UI thread) —
                        // it's a slow network call, and the result is a card in
                        // the chat rather than a sentence
                        if ("image".equalsIgnoreCase(name)) {
                            pushGil("_Painting \"" + target + "\"… this takes a moment._");
                            java.io.File img = GilImages.generate(this, target);
                            if (img != null) {
                                JSONObject card = new JSONObject()
                                        .put("kind", "image")
                                        .put("path", img.getAbsolutePath())
                                        .put("thumb", "/img/" + img.getName());
                                server.push("gil.richCard", card);
                            } else {
                                done.append(done.length() > 0 ? "\n" : "")
                                    .append("The picture service didn't come back — try again.");
                            }
                            continue;
                        }

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
                      + "`/key` changes my API key · `/clear` starts fresh");
                return null;
            case "clear":
                brain.clearHistory();
                return null;
            case "key":
                // reopen the native setup screen — never through the chat, so the
                // key is never echoed into the visible transcript
                runOnUiThread(this::showSetup);
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
