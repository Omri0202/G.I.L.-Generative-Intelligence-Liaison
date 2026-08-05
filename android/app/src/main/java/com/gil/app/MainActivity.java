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
    private static final String KEY_ONBOARDED = "onboarded";

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

        // Permissions are asked for on the onboarding screen, WITH an explanation
        // of what each unlocks — a cold system dialog on launch gets refused.
        if (!brain.hasKey()) showSetup();
        else if (!prefs().getBoolean(KEY_ONBOARDED, false)) showPermissions();
        else startChat();
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
            if (prefs().getBoolean(KEY_ONBOARDED, false)) startChat();
            else showPermissions();
        });
        LinearLayout.LayoutParams gp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        gp.topMargin = (int) (12 * d);
        root.addView(go, gp);

        setContentView(scroll);
    }

    /**
     * Second run-once screen: turn GIL's abilities on.
     *
     * These are deliberately explained before being asked for — "allow files"
     * means nothing on its own, and the two big ones (all-files access and screen
     * control) are Settings toggles Android only lets the USER flip, so an
     * unexplained jump to a settings page is baffling. Everything here is
     * skippable; GIL still chats without any of it.
     */
    private void showPermissions() {
        float d = getResources().getDisplayMetrics().density;
        int pad = (int) (24 * d);

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.parseColor("#15161B"));
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, (int) (pad * 1.6), pad, pad);
        scroll.addView(root);

        TextView title = new TextView(this);
        title.setText("Let me actually help");
        title.setTextColor(Color.parseColor("#E9EAF2"));
        title.setTextSize(25);
        root.addView(title);

        TextView sub = new TextView(this);
        sub.setText("Turn on what you want me to be able to do. You can change any "
                + "of these later, and skip anything you're unsure about.");
        sub.setTextColor(Color.parseColor("#9BA0B4"));
        sub.setTextSize(14.5f);
        sub.setLineSpacing(0, 1.25f);
        sub.setPadding(0, (int) (10 * d), 0, (int) (6 * d));
        root.addView(sub);

        addPerm(root, d, "Microphone & notifications",
                "So I can hear you and tell you when something's done.",
                "Allow", v -> ActivityCompat.requestPermissions(this, new String[]{
                        Manifest.permission.RECORD_AUDIO,
                        Manifest.permission.POST_NOTIFICATIONS,
                        Manifest.permission.CAMERA}, 1));

        addPerm(root, d, "Your files",
                "So I can read and write your Documents, Downloads and Photos — "
                + "\"what's in my CV\", \"save this as notes.md\".",
                "Open setting", v -> {
                    try {
                        startActivity(new android.content.Intent(
                                android.provider.Settings
                                        .ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                android.net.Uri.parse("package:" + getPackageName())));
                    } catch (Exception e) {
                        Toast.makeText(this, "Couldn't open that setting.",
                                Toast.LENGTH_SHORT).show();
                    }
                });

        addPerm(root, d, "Screen control",
                "So I can read what's on screen and tap for you inside other apps. "
                + "This is the powerful one — only turn it on if you want me acting "
                + "in other apps, and you can revoke it any time.",
                "Open setting", v -> {
                    try {
                        startActivity(new android.content.Intent(
                                android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS));
                        Toast.makeText(this, "Find \"G.I.L. screen control\" and turn it on",
                                Toast.LENGTH_LONG).show();
                    } catch (Exception e) {
                        Toast.makeText(this, "Couldn't open that setting.",
                                Toast.LENGTH_SHORT).show();
                    }
                });

        Button done = new Button(this);
        done.setText("Start using G.I.L.");
        done.setOnClickListener(v -> {
            prefs().edit().putBoolean(KEY_ONBOARDED, true).apply();
            startChat();
        });
        LinearLayout.LayoutParams dp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        dp.topMargin = (int) (22 * d);
        root.addView(done, dp);

        setContentView(scroll);
    }

    /** One explained permission row. */
    private void addPerm(LinearLayout root, float d, String name, String why,
                         String cta, android.view.View.OnClickListener onClick) {
        TextView t = new TextView(this);
        t.setText(name);
        t.setTextColor(Color.parseColor("#58C4DD"));
        t.setTextSize(17);
        t.setPadding(0, (int) (20 * d), 0, (int) (3 * d));
        root.addView(t);

        TextView p = new TextView(this);
        p.setText(why);
        p.setTextColor(Color.parseColor("#9BA0B4"));
        p.setTextSize(14);
        p.setLineSpacing(0, 1.2f);
        root.addView(p);

        Button b = new Button(this);
        b.setText(cta);
        b.setOnClickListener(onClick);
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        bp.topMargin = (int) (8 * d);
        root.addView(b, bp);
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
        // keep the process (and this server) alive once you switch away
        GilService.start(this);
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
            case "open_path": {
                // cards hand back "/site/x.html" or "/img/y.jpg" — both are served
                // by our own loopback server, which any app on the device can reach
                String p = args.optString(0, "");
                if (p.startsWith("/")) {
                    final String url = "http://127.0.0.1:" + server.getPort() + p;
                    runOnUiThread(() -> startActivity(
                            new android.content.Intent(android.content.Intent.ACTION_VIEW,
                                    android.net.Uri.parse(url))
                                    .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)));
                }
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
                GilService.status(this, "Thinking…");
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

                        // These produce a CARD, not a sentence, and are slow network
                        // work — so they run on their own thread via the same
                        // helpers the slash commands use, rather than on the UI.
                        if ("image".equalsIgnoreCase(name)) {
                            makeImage(target);
                            continue;
                        }
                        if ("website".equalsIgnoreCase(name)) {
                            makeSite(target);
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
                GilService.status(this, "Ready when you are");
            } catch (Exception e) {
                pushGil("Something went wrong handling that.");
                GilService.status(this, "Ready when you are");
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
            // These are CAPABILITIES, not conversation. Passing "/image cat" to
            // the brain as text made it guess (and web-search for a picture it
            // could have drawn), so run them directly instead.
            case "image":
                if (arg.trim().isEmpty()) {
                    pushGil("Tell me what to draw — `/image a fox in the snow`.");
                    return null;
                }
                makeImage(arg.trim());
                return null;
            case "website":
                if (arg.trim().isEmpty()) {
                    pushGil("Tell me what the site is for — "
                          + "`/website a page for my coffee shop`.");
                    return null;
                }
                makeSite(arg.trim());
                return null;
            default:
                return send("/" + name + (arg.isEmpty() ? "" : " " + arg));
        }
    }

    /** Draw a picture and drop it into the chat as a card. */
    private void makeImage(String prompt) {
        new Thread(() -> {
            GilService.status(this, "Painting…");
            pushGil("_Painting \"" + prompt + "\"… this takes a moment._");
            GilImages.Result r = GilImages.generate(this, prompt);
            if (r.ok()) {
                try {
                    server.push("gil.richCard", new JSONObject()
                            .put("kind", "image")
                            .put("path", "/img/" + r.file.getName())
                            .put("thumb", "/img/" + r.file.getName()));
                } catch (Exception ignored) {}
            } else {
                // say WHAT failed — a generic apology can't be acted on
                pushGil("I couldn't make that picture — " + r.error);
            }
            GilService.status(this, "Ready when you are");
        }, "GilImage").start();
    }

    /** Build a one-file site and drop it into the chat as an openable card. */
    private void makeSite(String prompt) {
        new Thread(() -> {
            GilService.status(this, "Building a site…");
            pushGil("_Designing \"" + prompt + "\"… give me a few seconds._");
            java.io.File site = GilWeb.generate(this, brain, prompt);
            if (site != null) {
                try {
                    server.push("gil.richCard", new JSONObject()
                            .put("kind", "website")
                            .put("path", "/site/" + site.getName())
                            .put("title", GilWeb.titleFor(prompt)));
                } catch (Exception ignored) {}
            } else if (brain.lastWasRateLimited()) {
                // a page is a big request against a small per-minute budget
                pushGil("I'm over my per-minute limit — building a page uses most of "
                      + "it. Give it a minute and ask again.");
            } else {
                pushGil("I couldn't build that one — try describing the site again.");
            }
            GilService.status(this, "Ready when you are");
        }, "GilSite").start();
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
        // Only tear down when the user is genuinely LEAVING (swiped the app away
        // or backed out). Merely switching apps must not kill the server — that
        // is the whole point of the foreground service, and stopping here would
        // leave a notification claiming GIL is alive when it isn't.
        if (isFinishing()) {
            if (tts != null) { tts.stop(); tts.shutdown(); tts = null; }
            if (server != null) { server.stop(); server = null; }
            GilService.stop(this);
        }
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(int req, @NonNull String[] p,
                                           @NonNull int[] r) {
        super.onRequestPermissionsResult(req, p, r);
    }
}
