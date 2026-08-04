"""
_make_android.py — generate the STANDALONE G.I.L. Android app.

Design note (why this works at all):
GIL's desktop UI is a web app that talks to a local HTTP server over JSON-RPC +
SSE. So the phone app runs that *same* server inside the APK, in Java. The entire
existing web UI — chat, codemode, all ten features — is bundled into assets and
loads unchanged; only the server behind it is reimplemented natively.

That means no PC, no network pairing: GIL runs entirely on the phone, calling
Groq for the brain and Android APIs for the hands.

Run:  python _make_android.py     (writes ./android/)
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).parent
A = ROOT / "android"
MAIN = A / "app" / "src" / "main"
PKG = MAIN / "java" / "com" / "gil" / "app"
ASSETS = MAIN / "assets" / "web"


def w(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print("  ", path.relative_to(ROOT))


# ════════════════════════════════════════════════════════════════════ gradle ══
w(A / "settings.gradle", """
pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositories { google(); mavenCentral() } }
rootProject.name = "GIL"
include ":app"
""".lstrip())

w(A / "build.gradle", """
plugins { id 'com.android.application' version '8.5.2' apply false }
""".lstrip())

w(A / "gradle.properties", """
org.gradle.jvmargs=-Xmx2048m
android.useAndroidX=true
android.nonTransitiveRClass=true
""".lstrip())

w(A / "app" / "build.gradle", """
plugins { id 'com.android.application' }

android {
    namespace 'com.gil.app'
    compileSdk 34

    defaultConfig {
        applicationId "com.gil.app"
        minSdk 26
        targetSdk 34
        versionCode 1
        versionName "1.0"
    }
    buildTypes {
        release { minifyEnabled false }
    }
    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }
}

configurations.all {
    resolutionStrategy {
        // AndroidX pulls kotlin-stdlib 1.8.x while some transitive still asks for
        // the old kotlin-stdlib-jdk7/jdk8 1.6.x, whose classes were FOLDED INTO
        // kotlin-stdlib at 1.8 — so both on the classpath = duplicate classes and
        // a failed build. Pinning every kotlin artifact to one version makes the
        // jdk7/jdk8 jars the empty shims they're meant to be.
        force 'org.jetbrains.kotlin:kotlin-stdlib:1.8.22'
        force 'org.jetbrains.kotlin:kotlin-stdlib-jdk7:1.8.22'
        force 'org.jetbrains.kotlin:kotlin-stdlib-jdk8:1.8.22'
    }
}

dependencies {
    implementation 'androidx.appcompat:appcompat:1.7.0'
}
""".lstrip())

# ══════════════════════════════════════════════════════════════════ manifest ══
w(MAIN / "AndroidManifest.xml", """
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools">

    <!-- the brain talks to Groq -->
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <!-- voice mode -->
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <!-- vision / attachments -->
    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.READ_MEDIA_IMAGES" />
    <!-- keep working while you switch apps -->
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <!-- your files: Documents, Downloads, Photos… (granted by you in Settings) -->
    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />
    <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE"
        android:maxSdkVersion="28" />
    <uses-permission android:name="android.permission.MANAGE_EXTERNAL_STORAGE"
        tools:ignore="ScopedStorage" />

    <!-- so GIL can see which apps exist in order to open them -->
    <queries><intent><action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" /></intent></queries>

    <application
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="@string/app_name"
        android:roundIcon="@mipmap/ic_launcher"
        android:supportsRtl="true"
        android:usesCleartextTraffic="true"
        android:networkSecurityConfig="@xml/network_security_config"
        android:theme="@style/Theme.GIL">

        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:configChanges="orientation|screenSize|keyboardHidden|uiMode"
            android:windowSoftInputMode="adjustResize"
            android:launchMode="singleTask">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <!-- keeps GIL (and its local server) alive when the chat isn't in front -->
        <service
            android:name=".GilService"
            android:exported="false"
            android:foregroundServiceType="specialUse">
            <property
                android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE"
                android:value="On-device assistant: keeps the local server and any
                    in-flight request alive while the user is in another app." />
        </service>

        <!-- lets GIL hand one of your files to another app (open_file) -->
        <provider
            android:name="androidx.core.content.FileProvider"
            android:authorities="${applicationId}.files"
            android:exported="false"
            android:grantUriPermissions="true">
            <meta-data
                android:name="android.support.FILE_PROVIDER_PATHS"
                android:resource="@xml/file_paths" />
        </provider>

        <!-- Screen control. Android forces the user to switch this on by hand in
             Settings > Accessibility, and it can be revoked there at any time. -->
        <service
            android:name=".GilAccessibility"
            android:exported="false"
            android:label="G.I.L. screen control"
            android:permission="android.permission.BIND_ACCESSIBILITY_SERVICE">
            <intent-filter>
                <action android:name="android.accessibilityservice.AccessibilityService" />
            </intent-filter>
            <meta-data
                android:name="android.accessibilityservice"
                android:resource="@xml/accessibility_service_config" />
        </service>
    </application>
</manifest>
""".lstrip())

w(MAIN / "res" / "xml" / "network_security_config.xml", """
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <!-- GIL's UI is served by a server INSIDE this app on 127.0.0.1, so
         loopback cleartext must be allowed. Everything outbound stays TLS. -->
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="false">127.0.0.1</domain>
        <domain includeSubdomains="false">localhost</domain>
    </domain-config>
</network-security-config>
""".lstrip())

# ═════════════════════════════════════════════════════════════════════ server ══
w(PKG / "GilServer.java", r"""
package com.gil.app;

import android.content.Context;
import android.content.res.AssetManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.LinkedBlockingQueue;

/**
 * The same local HTTP + JSON-RPC + SSE contract GIL's desktop server speaks,
 * reimplemented in Java — so the bundled web UI runs completely unchanged.
 *
 * Binds 127.0.0.1 only: this server is an internal detail of the app and is
 * never reachable from the network.
 */
public class GilServer {

    public interface Rpc {
        /** Handle one RPC call; return the JSON-serialisable result (or null). */
        Object call(String method, JSONArray args) throws Exception;
    }

    private final Context ctx;
    private final Rpc rpc;
    private ServerSocket socket;
    private int port;
    private volatile boolean running;
    /** one queue per connected SSE client (the UI reconnects on its own) */
    private final CopyOnWriteArrayList<LinkedBlockingQueue<String>> clients =
            new CopyOnWriteArrayList<>();

    public GilServer(Context ctx, Rpc rpc) {
        this.ctx = ctx;
        this.rpc = rpc;
    }

    public int getPort() { return port; }

    public void start() throws Exception {
        socket = new ServerSocket(0, 8, InetAddress.getByName("127.0.0.1"));
        port = socket.getLocalPort();
        running = true;
        new Thread(this::acceptLoop, "GilServer").start();
    }

    public void stop() {
        running = false;
        try { if (socket != null) socket.close(); } catch (Exception ignored) {}
    }

    /**
     * Push an event to the UI — the Java side of GIL's `_push`.
     *
     * The field names are part of the contract: the web UI destructures
     * {fn, arg}. Anything else and every push is silently swallowed by its
     * try/catch, which looks exactly like GIL never answering.
     */
    public void push(String fn, Object arg) {
        JSONObject msg = new JSONObject();
        try {
            msg.put("fn", fn);
            msg.put("arg", arg == null ? JSONObject.NULL : arg);
        } catch (Exception ignored) {}
        String line = msg.toString();
        for (LinkedBlockingQueue<String> q : clients) q.offer(line);
    }

    private void acceptLoop() {
        while (running) {
            try {
                Socket s = socket.accept();
                new Thread(() -> handle(s), "GilConn").start();
            } catch (Exception e) {
                if (running) continue; else break;
            }
        }
    }

    private void handle(Socket s) {
        try {
            InputStream in = s.getInputStream();
            OutputStream out = s.getOutputStream();
            BufferedReader r = new BufferedReader(
                    new InputStreamReader(in, StandardCharsets.UTF_8));

            String requestLine = r.readLine();
            if (requestLine == null) { s.close(); return; }
            String[] parts = requestLine.split(" ");
            String method = parts.length > 0 ? parts[0] : "GET";
            String path = parts.length > 1 ? parts[1] : "/";

            int contentLength = 0;
            String line;
            while ((line = r.readLine()) != null && !line.isEmpty()) {
                String low = line.toLowerCase();
                if (low.startsWith("content-length:")) {
                    try {
                        contentLength = Integer.parseInt(line.split(":")[1].trim());
                    } catch (Exception ignored) {}
                }
            }

            String route = path.split("\\?")[0];

            if ("POST".equals(method) && "/api".equals(route)) {
                char[] buf = new char[contentLength];
                int read = 0;
                while (read < contentLength) {
                    int n = r.read(buf, read, contentLength - read);
                    if (n < 0) break;
                    read += n;
                }
                handleApi(new String(buf, 0, Math.max(read, 0)), out);
            } else if ("/events".equals(route)) {
                streamEvents(out);            // blocks until the client leaves
            } else {
                serveAsset(route, out);
            }
            s.close();
        } catch (Exception ignored) {
            try { s.close(); } catch (Exception ignored2) {}
        }
    }

    private void handleApi(String body, OutputStream out) throws Exception {
        Object result = null;
        try {
            JSONObject req = new JSONObject(body.isEmpty() ? "{}" : body);
            String m = req.optString("method", "");
            JSONArray args = req.optJSONArray("args");
            if (args == null) args = new JSONArray();
            result = rpc.call(m, args);
        } catch (Exception ignored) {}
        JSONObject resp = new JSONObject();
        resp.put("result", result == null ? JSONObject.NULL : result);
        byte[] data = resp.toString().getBytes(StandardCharsets.UTF_8);
        send(out, "200 OK", "application/json", data);
    }

    private void streamEvents(OutputStream out) throws Exception {
        LinkedBlockingQueue<String> q = new LinkedBlockingQueue<>();
        clients.add(q);
        try {
            out.write(("HTTP/1.1 200 OK\r\n"
                    + "Content-Type: text/event-stream\r\n"
                    + "Cache-Control: no-cache\r\n"
                    + "Connection: keep-alive\r\n\r\n")
                    .getBytes(StandardCharsets.UTF_8));
            out.flush();
            while (running) {
                String msg = q.poll(15, java.util.concurrent.TimeUnit.SECONDS);
                if (msg == null) {
                    out.write(": ping\n\n".getBytes(StandardCharsets.UTF_8));
                } else {
                    out.write(("data: " + msg + "\n\n").getBytes(StandardCharsets.UTF_8));
                }
                out.flush();
            }
        } catch (Exception ignored) {
        } finally {
            clients.remove(q);
        }
    }

    /** The web UI is bundled in assets/web/; generated pictures live on disk. */
    private void serveAsset(String route, OutputStream out) throws Exception {
        // pictures GIL made — served from the app's own storage so the chat can
        // show them from the same origin, offline
        if (route.startsWith("/img/")) {
            String fname = new java.io.File(route).getName();
            java.io.File f = new java.io.File(GilImages.imageDir(ctx), fname);
            if (f.isFile()) {
                byte[] data = new byte[(int) f.length()];
                try (java.io.FileInputStream fis = new java.io.FileInputStream(f)) {
                    int read = 0;
                    while (read < data.length) {
                        int n = fis.read(data, read, data.length - read);
                        if (n < 0) break;
                        read += n;
                    }
                }
                send(out, "200 OK", "image/jpeg", data);
            } else {
                send(out, "404 Not Found", "text/plain", "no image".getBytes());
            }
            return;
        }
        String name = route.equals("/") ? "index.html" : route.substring(1);
        if (name.isEmpty()) name = "index.html";
        AssetManager am = ctx.getAssets();
        byte[] data;
        try (InputStream is = am.open("web/" + name)) {
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = is.read(chunk)) > 0) bos.write(chunk, 0, n);
            data = bos.toByteArray();
        } catch (Exception e) {
            send(out, "404 Not Found", "text/plain", "not found".getBytes());
            return;
        }
        send(out, "200 OK", contentType(name), data);
    }

    private static String contentType(String name) {
        String n = name.toLowerCase();
        if (n.endsWith(".html")) return "text/html; charset=utf-8";
        if (n.endsWith(".js"))   return "application/javascript";
        if (n.endsWith(".json")) return "application/json";
        if (n.endsWith(".css"))  return "text/css";
        if (n.endsWith(".png"))  return "image/png";
        if (n.endsWith(".ico"))  return "image/x-icon";
        if (n.endsWith(".svg"))  return "image/svg+xml";
        return "application/octet-stream";
    }

    private static void send(OutputStream out, String status, String type,
                             byte[] body) throws Exception {
        out.write(("HTTP/1.1 " + status + "\r\n"
                + "Content-Type: " + type + "\r\n"
                + "Content-Length: " + body.length + "\r\n"
                + "Access-Control-Allow-Origin: *\r\n\r\n")
                .getBytes(StandardCharsets.UTF_8));
        out.write(body);
        out.flush();
    }
}
""".lstrip())

# ══════════════════════════════════════════════════════════════════════ brain ══
w(PKG / "GilBrain.java", r"""
package com.gil.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * GIL's brain on the phone: the same contract as the desktop Python brain —
 * a system prompt, a Groq chat completion, and a JSON reply carrying speech +
 * actions — with the action vocabulary rewritten for what Android can do.
 */
public class GilBrain {

    private static final String URL_CHAT = "https://api.groq.com/openai/v1/chat/completions";
    private static final String MODEL = "openai/gpt-oss-120b";

    private final List<JSONObject> history = new ArrayList<>();
    private String apiKey = "";

    public void setApiKey(String k) { this.apiKey = k == null ? "" : k.trim(); }
    public boolean hasKey() { return !apiKey.isEmpty(); }
    public void clearHistory() { history.clear(); }

    private static final String SYSTEM =
        "You are G.I.L. (Generative Intelligence Liaison), a capable AI assistant "
      + "running natively on the user's Android phone. You are warm, direct and "
      + "concise — never corporate, never padded.\n\n"
      + "You can act on the phone. Reply with ONE JSON object, nothing else:\n"
      + "{\"speech\": \"<what you say, 1-3 natural sentences>\", "
      + "\"actions\": [{\"action\": \"<name>\", \"target\": \"<argument>\"}], "
      + "\"report\": \"<optional long markdown answer>\"}\n\n"
      + "ACTIONS you can use:\n"
      + "- open_app     -> an app name on this phone (\"spotify\", \"gmail\")\n"
      + "- open_url     -> a full URL to open in the browser\n"
      + "- web_search   -> a search query\n"
      + "- call         -> a phone number to dial\n"
      + "- sms          -> \"number | message\" to open a text\n"
      + "- email        -> \"address | subject | body\"\n"
      + "- maps         -> a place or address to show\n"
      + "- alarm        -> \"HH:MM | label\"\n"
      + "- timer        -> seconds as a number\n"
      + "- camera       -> open the camera\n"
      + "- share        -> text to share to another app\n"
      + "- copy         -> text to put on the clipboard\n"
      + "- settings     -> a settings screen (\"wifi\", \"bluetooth\", \"display\")\n"
      + "- image        -> a vivid description of a picture to CREATE. Use this "
      + "whenever the user asks you to draw/generate/make an image — never "
      + "web_search for a picture, you can actually make one.\n"
      + "\nYOUR FILES (Documents, Downloads, Photos — paths are relative, e.g.\n"
      + "\"Documents/cv.pdf\"):\n"
      + "- list_files   -> a folder to list (\"\" for the top level)\n"
      + "- read_file    -> a file to read as text\n"
      + "- write_file   -> \"path | the full contents to save\"\n"
      + "- search_files -> a name to find anywhere on the phone\n"
      + "- open_file    -> a file to open in its usual app\n"
      + "- grant_files  -> open the setting so the user can give you file access\n"
      + "\nSCREEN CONTROL (seeing and driving OTHER apps):\n"
      + "- read_screen  -> read everything on screen right now\n"
      + "- tap          -> the visible text/label of what to tap\n"
      + "- type         -> text to type into the focused box\n"
      + "- scroll       -> \"up\" or \"down\"\n"
      + "- back / home / recents / notifications -> (target ignored)\n"
      + "- grant_screen -> open the setting so the user can switch screen control on\n"
      + "\n- none         -> no action needed (just answering)\n\n"
      + "To DO something in another app, chain it: open_app, then read_screen to see "
      + "where you are, then tap/type. Always read_screen before tapping blind.\n"
      + "Put a LONG answer (explanations, lists, documents, code) in \"report\" as "
      + "markdown, and keep \"speech\" to a short spoken summary. For a simple "
      + "reply use speech alone and actions: [].\n"
      + "Never invent an action that isn't listed. Never claim you did something "
      + "you did not do.";

    /** One turn. Returns the parsed reply; never throws. */
    public JSONObject query(String userText) {
        JSONObject fallback = new JSONObject();
        try {
            fallback.put("speech", "");
            fallback.put("actions", new JSONArray());
        } catch (Exception ignored) {}

        if (apiKey.isEmpty()) {
            try {
                fallback.put("speech", "I need a Groq API key first — open the menu "
                        + "and paste one in to switch me on.");
            } catch (Exception ignored) {}
            return fallback;
        }

        try {
            JSONObject userMsg = new JSONObject()
                    .put("role", "user").put("content", userText);
            history.add(userMsg);
            trimHistory();

            JSONArray messages = new JSONArray();
            messages.put(new JSONObject().put("role", "system").put("content", SYSTEM));
            for (JSONObject m : history) messages.put(m);

            JSONObject payload = new JSONObject()
                    .put("model", MODEL)
                    .put("messages", messages)
                    .put("temperature", 0.3)
                    .put("max_tokens", 2000)
                    .put("reasoning_effort", "low");

            String raw = post(payload);
            JSONObject parsed = parse(raw);

            String memory = parsed.optString("speech", "");
            if (memory.isEmpty()) memory = parsed.optString("report", "");
            history.add(new JSONObject().put("role", "assistant").put("content", memory));
            trimHistory();
            return parsed;
        } catch (Exception e) {
            try {
                fallback.put("speech", "I couldn't reach my AI service just then — "
                        + "check the connection and try again.");
            } catch (Exception ignored) {}
            return fallback;
        }
    }

    private void trimHistory() {
        while (history.size() > 12) history.remove(0);
    }

    private String post(JSONObject payload) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(URL_CHAT).openConnection();
        c.setRequestMethod("POST");
        c.setRequestProperty("Authorization", "Bearer " + apiKey);
        c.setRequestProperty("Content-Type", "application/json");
        c.setConnectTimeout(15000);
        c.setReadTimeout(60000);
        c.setDoOutput(true);
        try (OutputStream os = c.getOutputStream()) {
            os.write(payload.toString().getBytes(StandardCharsets.UTF_8));
        }
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(
                c.getResponseCode() >= 400 ? c.getErrorStream() : c.getInputStream(),
                StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) sb.append(line);
        }
        JSONObject resp = new JSONObject(sb.toString());
        return resp.getJSONArray("choices").getJSONObject(0)
                   .getJSONObject("message").optString("content", "");
    }

    /** Tolerant JSON extraction — models wrap replies in prose or fences. */
    static JSONObject parse(String raw) {
        JSONObject out = new JSONObject();
        try {
            out.put("speech", "");
            out.put("actions", new JSONArray());
        } catch (Exception ignored) {}
        if (raw == null) return out;
        String s = raw.replace("```json", "").replace("```", "").trim();
        int start = s.indexOf('{');
        int end = s.lastIndexOf('}');
        if (start >= 0 && end > start) {
            try {
                JSONObject o = new JSONObject(s.substring(start, end + 1));
                if (o.has("speech") || o.has("actions") || o.has("report")) return o;
            } catch (Exception ignored) {}
        }
        // not JSON at all — the model just answered in prose, which is fine
        try { out.put("speech", s); } catch (Exception ignored) {}
        return out;
    }
}
""".lstrip())

# ════════════════════════════════════════════════════════════════════ actions ══
w(PKG / "GilActions.java", r"""
package com.gil.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.hardware.camera2.CameraManager;
import android.net.Uri;
import android.provider.AlarmClock;
import android.provider.MediaStore;
import android.provider.Settings;

import java.util.List;
import java.util.Locale;

/**
 * GIL's hands on Android.
 *
 * Android sandboxes apps far more tightly than Windows, so these are the real,
 * permitted levers: launching apps and system activities via Intents. Each
 * returns a short human sentence describing what actually happened, so GIL can
 * report truthfully instead of assuming success.
 */
public class GilActions {

    private final Activity act;

    public GilActions(Activity act) { this.act = act; }

    public String run(String action, String target) {
        if (action == null) return "";
        String t = target == null ? "" : target.trim();
        try {
            switch (action.toLowerCase(Locale.ROOT)) {
                case "open_app":   return openApp(t);
                case "open_url":   return openUrl(t);
                case "web_search": return webSearch(t);
                case "call":       return dial(t);
                case "sms":        return sms(t);
                case "email":      return email(t);
                case "maps":       return maps(t);
                case "alarm":      return alarm(t);
                case "timer":      return timer(t);
                case "camera":     return camera();
                case "share":      return share(t);
                case "copy":       return copy(t);
                case "settings":   return settings(t);
                // ── your files ──
                case "list_files":  return GilFiles.list(t);
                case "read_file":   return GilFiles.read(t);
                case "write_file":  return GilFiles.write(t);
                case "search_files":return GilFiles.search(t);
                case "open_file":   return openFile(t);
                // ── screen control (other apps) ──
                case "read_screen": return GilAccessibility.readScreen();
                case "tap":         return GilAccessibility.tap(t);
                case "type":        return GilAccessibility.type(t);
                case "scroll":      return GilAccessibility.scroll(t);
                case "back":        return GilAccessibility.global("back");
                case "home":        return GilAccessibility.global("home");
                case "recents":     return GilAccessibility.global("recents");
                case "notifications": return GilAccessibility.global("notifications");
                // ── permission helpers ──
                case "grant_files":  return grantFiles();
                case "grant_screen": return grantScreen();
                case "none":
                case "":           return "";
                default:           return "I don't have a way to do \"" + action + "\" on this phone.";
            }
        } catch (ActivityNotFoundException e) {
            return "Nothing on this phone can handle that.";
        } catch (Exception e) {
            return "That didn't work: " + e.getMessage();
        }
    }

    /** Fuzzy-match a launchable app by its visible name. */
    private String openApp(String name) {
        if (name.isEmpty()) return "Which app?";
        PackageManager pm = act.getPackageManager();
        Intent probe = new Intent(Intent.ACTION_MAIN, null);
        probe.addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> apps = pm.queryIntentActivities(probe, 0);
        String want = name.toLowerCase(Locale.ROOT);
        ResolveInfo best = null;
        for (ResolveInfo ri : apps) {
            String label = String.valueOf(ri.loadLabel(pm)).toLowerCase(Locale.ROOT);
            if (label.equals(want)) { best = ri; break; }
            if (best == null && (label.contains(want) || want.contains(label))) best = ri;
        }
        if (best == null) return "I couldn't find an app called \"" + name + "\".";
        Intent launch = pm.getLaunchIntentForPackage(best.activityInfo.packageName);
        if (launch == null) return "\"" + name + "\" can't be opened directly.";
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        act.startActivity(launch);
        return "Opened " + best.loadLabel(pm) + ".";
    }

    private String openUrl(String url) {
        if (url.isEmpty()) return "No link given.";
        if (!url.startsWith("http")) url = "https://" + url;
        act.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return "Opened " + url;
    }

    private String webSearch(String q) {
        Intent i = new Intent(Intent.ACTION_WEB_SEARCH);
        i.putExtra(android.app.SearchManager.QUERY, q);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            act.startActivity(i);
        } catch (ActivityNotFoundException e) {
            return openUrl("https://www.google.com/search?q=" + Uri.encode(q));
        }
        return "Searched for " + q;
    }

    private String dial(String number) {
        // ACTION_DIAL only pre-fills the dialler — the user still presses call,
        // which is the right level of consent for placing a call.
        act.startActivity(new Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + number))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return "Dialler ready for " + number + " — press call when you're ready.";
    }

    private String sms(String spec) {
        String[] p = spec.split("\\|", 2);
        Intent i = new Intent(Intent.ACTION_SENDTO,
                Uri.parse("smsto:" + p[0].trim()));
        if (p.length > 1) i.putExtra("sms_body", p[1].trim());
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        act.startActivity(i);
        return "Text ready for " + p[0].trim() + " — send it when you're happy.";
    }

    private String email(String spec) {
        String[] p = spec.split("\\|", 3);
        Intent i = new Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:" + p[0].trim()));
        if (p.length > 1) i.putExtra(Intent.EXTRA_SUBJECT, p[1].trim());
        if (p.length > 2) i.putExtra(Intent.EXTRA_TEXT, p[2].trim());
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        act.startActivity(i);
        return "Email drafted to " + p[0].trim() + " — review and send.";
    }

    private String maps(String place) {
        act.startActivity(new Intent(Intent.ACTION_VIEW,
                Uri.parse("geo:0,0?q=" + Uri.encode(place)))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return "Showing " + place + " on the map.";
    }

    private String alarm(String spec) {
        String[] p = spec.split("\\|", 2);
        String[] hm = p[0].trim().split(":");
        Intent i = new Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, Integer.parseInt(hm[0].trim()))
                .putExtra(AlarmClock.EXTRA_MINUTES,
                        hm.length > 1 ? Integer.parseInt(hm[1].trim()) : 0)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (p.length > 1) i.putExtra(AlarmClock.EXTRA_MESSAGE, p[1].trim());
        act.startActivity(i);
        return "Alarm set for " + p[0].trim() + ".";
    }

    private String timer(String secs) {
        int s = Integer.parseInt(secs.trim());
        act.startActivity(new Intent(AlarmClock.ACTION_SET_TIMER)
                .putExtra(AlarmClock.EXTRA_LENGTH, s)
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return "Timer running for " + s + " seconds.";
    }

    private String camera() {
        act.startActivity(new Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return "Camera open.";
    }

    private String share(String text) {
        Intent i = new Intent(Intent.ACTION_SEND).setType("text/plain")
                .putExtra(Intent.EXTRA_TEXT, text);
        act.startActivity(Intent.createChooser(i, "Share")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return "Ready to share.";
    }

    private String copy(String text) {
        ClipboardManager cm = (ClipboardManager) act.getSystemService(Context.CLIPBOARD_SERVICE);
        cm.setPrimaryClip(ClipData.newPlainText("G.I.L.", text));
        return "Copied to the clipboard.";
    }

    /** Hand a file to whichever app owns that type. */
    private String openFile(String rel) {
        java.io.File f = GilFiles.resolve(rel);
        if (!f.isFile()) return "I can't find \"" + rel + "\".";
        Uri uri = androidx.core.content.FileProvider.getUriForFile(
                act, act.getPackageName() + ".files", f);
        Intent i = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, guessType(f.getName()))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                        | Intent.FLAG_ACTIVITY_NEW_TASK);
        act.startActivity(i);
        return "Opened " + f.getName() + ".";
    }

    private static String guessType(String name) {
        String ext = android.webkit.MimeTypeMap.getFileExtensionFromUrl(name);
        String mime = android.webkit.MimeTypeMap.getSingleton()
                .getMimeTypeFromExtension(ext == null ? "" : ext.toLowerCase(Locale.ROOT));
        return mime == null ? "*/*" : mime;
    }

    /** Open the "All files access" screen — the user grants it, not GIL. */
    private String grantFiles() {
        if (GilFiles.granted()) return "I already have access to your files.";
        Intent i = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                Uri.parse("package:" + act.getPackageName()))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            act.startActivity(i);
        } catch (ActivityNotFoundException e) {
            act.startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        }
        return "Opened the setting — switch on \"Allow access to manage all files\" "
             + "and come back.";
    }

    /** Open Accessibility settings — the user switches GIL on there. */
    private String grantScreen() {
        if (GilAccessibility.isRunning()) return "Screen control is already on.";
        act.startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return "Opened Accessibility settings — find \"G.I.L. screen control\" and "
             + "switch it on. It lets me read and tap what's on screen.";
    }

    private String settings(String which) {
        String a;
        switch (which.toLowerCase(Locale.ROOT)) {
            case "wifi":      a = Settings.ACTION_WIFI_SETTINGS; break;
            case "bluetooth": a = Settings.ACTION_BLUETOOTH_SETTINGS; break;
            case "display":   a = Settings.ACTION_DISPLAY_SETTINGS; break;
            case "sound":     a = Settings.ACTION_SOUND_SETTINGS; break;
            case "battery":   a = Settings.ACTION_BATTERY_SAVER_SETTINGS; break;
            case "apps":      a = Settings.ACTION_APPLICATION_SETTINGS; break;
            default:          a = Settings.ACTION_SETTINGS; break;
        }
        act.startActivity(new Intent(a).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return "Opened settings.";
    }
}
""".lstrip())

# ══════════════════════════════════════════════════════════════════════ files ══
w(MAIN / "res" / "xml" / "accessibility_service_config.xml", """
<?xml version="1.0" encoding="utf-8"?>
<accessibility-service xmlns:android="http://schemas.android.com/apk/res/android"
    android:accessibilityEventTypes="typeWindowStateChanged|typeWindowContentChanged"
    android:accessibilityFeedbackType="feedbackGeneric"
    android:accessibilityFlags="flagReportViewIds|flagRetrieveInteractiveWindows"
    android:canRetrieveWindowContent="true"
    android:canPerformGestures="true"
    android:notificationTimeout="100"
    android:description="@string/accessibility_description" />
""".lstrip())

w(PKG / "GilFiles.java", r"""
package com.gil.app;

import android.os.Environment;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * GIL's access to YOUR files — Documents, Downloads, Photos, anything you can
 * see in a file manager. Requires "All files access", which the user grants by
 * hand in Settings (see MainActivity#needsFileAccess).
 *
 * Paths are resolved relative to shared storage, so GIL can say "Documents/cv.pdf"
 * rather than juggling absolute Android paths.
 */
public class GilFiles {

    /** Text-ish files GIL can actually read as text. */
    private static final List<String> TEXTY = Arrays.asList(
            "txt", "md", "json", "csv", "log", "xml", "html", "css", "js", "py",
            "java", "kt", "c", "cpp", "h", "yml", "yaml", "ini", "cfg", "sh");

    private static File root() { return Environment.getExternalStorageDirectory(); }

    public static boolean granted() {
        return Environment.isExternalStorageManager();
    }

    /** Resolve a user-friendly path safely inside shared storage. */
    static File resolve(String rel) {
        String p = rel == null ? "" : rel.trim();
        if (p.startsWith("/")) {
            File abs = new File(p);
            // keep it inside shared storage — never wander into app sandboxes
            if (abs.getAbsolutePath().startsWith(root().getAbsolutePath())) return abs;
        }
        return new File(root(), p);
    }

    public static String list(String rel) {
        if (!granted()) return needGrant();
        File dir = resolve(rel);
        if (!dir.isDirectory()) return "\"" + rel + "\" isn't a folder on this phone.";
        File[] kids = dir.listFiles();
        if (kids == null || kids.length == 0) return "\"" + rel + "\" is empty.";
        Arrays.sort(kids, (a, b) -> a.getName().compareToIgnoreCase(b.getName()));
        StringBuilder sb = new StringBuilder("**" + dir.getName() + "**\n");
        int n = 0;
        for (File f : kids) {
            if (f.isHidden()) continue;
            sb.append(f.isDirectory() ? "- 📁 " : "- 📄 ").append(f.getName());
            if (f.isFile()) sb.append("  _").append(human(f.length())).append("_");
            sb.append("\n");
            if (++n >= 60) { sb.append("- …\n"); break; }
        }
        return sb.toString();
    }

    public static String read(String rel) {
        if (!granted()) return needGrant();
        File f = resolve(rel);
        if (!f.isFile()) return "I can't find the file \"" + rel + "\".";
        String ext = ext(f.getName());
        if (!TEXTY.contains(ext)) {
            return "\"" + f.getName() + "\" is a " + ext.toUpperCase(Locale.ROOT)
                 + " file (" + human(f.length()) + ") — I can't read that as text.";
        }
        if (f.length() > 400_000) {
            return "\"" + f.getName() + "\" is too big to read in one go ("
                 + human(f.length()) + ").";
        }
        try {
            String body = new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8);
            if (body.length() > 12000) body = body.substring(0, 12000) + "\n…[truncated]";
            return "**" + f.getName() + "**\n```" + ext + "\n" + body + "\n```";
        } catch (Exception e) {
            return "I couldn't read \"" + rel + "\": " + e.getMessage();
        }
    }

    /** spec: "path | contents" */
    public static String write(String spec) {
        if (!granted()) return needGrant();
        String[] p = spec.split("\\|", 2);
        if (p.length < 2) return "Tell me the path and the contents, split by |.";
        File f = resolve(p[0].trim());
        try {
            File parent = f.getParentFile();
            if (parent != null && !parent.exists() && !parent.mkdirs()) {
                return "I couldn't create the folder for \"" + p[0].trim() + "\".";
            }
            Files.write(f.toPath(), p[1].trim().getBytes(StandardCharsets.UTF_8));
            return "Saved " + f.getName() + " (" + human(f.length()) + ").";
        } catch (Exception e) {
            return "I couldn't save that: " + e.getMessage();
        }
    }

    public static String search(String query) {
        if (!granted()) return needGrant();
        if (query == null || query.trim().isEmpty()) return "Search for what?";
        List<String> hits = new ArrayList<>();
        walk(root(), query.toLowerCase(Locale.ROOT), hits, 0);
        if (hits.isEmpty()) return "Nothing on the phone matches \"" + query + "\".";
        StringBuilder sb = new StringBuilder("Found " + hits.size() + ":\n");
        for (String h : hits) sb.append("- ").append(h).append("\n");
        return sb.toString();
    }

    private static void walk(File dir, String needle, List<String> hits, int depth) {
        if (depth > 6 || hits.size() >= 40) return;
        File[] kids = dir.listFiles();
        if (kids == null) return;
        for (File f : kids) {
            if (f.isHidden()) continue;
            if (f.getName().toLowerCase(Locale.ROOT).contains(needle)) {
                hits.add(rel(f) + (f.isDirectory() ? "/" : "  _" + human(f.length()) + "_"));
                if (hits.size() >= 40) return;
            }
            if (f.isDirectory()) walk(f, needle, hits, depth + 1);
        }
    }

    private static String rel(File f) {
        String r = root().getAbsolutePath();
        String a = f.getAbsolutePath();
        return a.startsWith(r) ? a.substring(r.length()).replaceFirst("^/", "") : a;
    }

    private static String ext(String name) {
        int i = name.lastIndexOf('.');
        return i < 0 ? "" : name.substring(i + 1).toLowerCase(Locale.ROOT);
    }

    private static String human(long bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024) + " KB";
        return String.format(Locale.ROOT, "%.1f MB", bytes / 1024.0 / 1024.0);
    }

    private static String needGrant() {
        return "I don't have file access yet — say \"grant file access\" and I'll "
             + "open the setting for you.";
    }
}
""".lstrip())

# ═══════════════════════════════════════════════════════════ background service ══
w(PKG / "GilService.java", r"""
package com.gil.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;

/**
 * Keeps G.I.L. alive when you leave the app.
 *
 * Without this Android is free to freeze or kill the process the moment the chat
 * isn't in front — which meant a long answer or a running task simply died, and
 * GIL "stopped replying". A foreground service (with the notification Android
 * requires) keeps the process, its local server and any in-flight work running.
 *
 * The notification is the honest cost of that: Android does not allow silent
 * background residency, and it doubles as the way back into the chat.
 */
public class GilService extends Service {

    private static final String CHANNEL = "gil_running";
    private static final int NOTIF_ID = 1;

    public static void start(Context ctx) {
        Intent i = new Intent(ctx, GilService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(i);
        else ctx.startService(i);
    }

    public static void stop(Context ctx) {
        ctx.stopService(new Intent(ctx, GilService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        channel();
        startForeground(NOTIF_ID, build("Ready when you are"));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String status = intent == null ? null : intent.getStringExtra("status");
        if (status != null) {
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.notify(NOTIF_ID, build(status));
        }
        // restart if Android reclaims us under memory pressure
        return START_STICKY;
    }

    /** Update the notification text — e.g. while GIL is working on something. */
    public static void status(Context ctx, String text) {
        Intent i = new Intent(ctx, GilService.class).putExtra("status", text);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(i);
        else ctx.startService(i);
    }

    private void channel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm == null || nm.getNotificationChannel(CHANNEL) != null) return;
        NotificationChannel ch = new NotificationChannel(
                CHANNEL, "G.I.L. running", NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("Keeps G.I.L. awake so it can finish what you asked.");
        ch.setShowBadge(false);
        nm.createNotificationChannel(ch);
    }

    private Notification build(String text) {
        PendingIntent open = PendingIntent.getActivity(
                this, 0, new Intent(this, MainActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new NotificationCompat.Builder(this, CHANNEL)
                .setContentTitle("G.I.L.")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.ic_menu_compass)
                .setContentIntent(open)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
                .build();
    }

    /** Swiped out of Recents — the user is done; don't linger in the shade. */
    @Override
    public void onTaskRemoved(Intent rootIntent) {
        stopSelf();
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }
}
""".lstrip())

# ═════════════════════════════════════════════════════════════════════ images ══
w(PKG / "GilImages.java", r"""
package com.gil.app;

import android.content.Context;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.Random;

/**
 * Picture generation, same provider desktop GIL uses (Pollinations) — free and
 * keyless, so it needs no extra setup on the phone.
 *
 * The file is saved into the app's own pictures dir and served back through
 * GilServer, so the chat can display it from the same origin and it survives
 * offline.
 */
public class GilImages {

    private static final Random RAND = new Random();

    /** Generate and save; returns the saved file, or null on failure. */
    public static File generate(Context ctx, String prompt) {
        try {
            String encoded = URLEncoder.encode(prompt, "UTF-8").replace("+", "%20");
            int seed = 1 + RAND.nextInt(99_999);
            String url = "https://image.pollinations.ai/prompt/" + encoded
                    + "?width=1024&height=1024&seed=" + seed
                    + "&model=flux&enhance=true&nologo=true";

            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(20000);
            c.setReadTimeout(120000);          // generation genuinely takes a while
            c.setInstanceFollowRedirects(true);
            // the provider 403s some default library user-agents, so be explicit
            c.setRequestProperty("User-Agent", "GIL-Android/1.0");
            if (c.getResponseCode() != 200) return null;

            File dir = imageDir(ctx);
            if (!dir.exists() && !dir.mkdirs()) return null;
            File out = new File(dir, "gil_" + System.currentTimeMillis() + ".jpg");
            try (InputStream in = c.getInputStream();
                 FileOutputStream fos = new FileOutputStream(out)) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) fos.write(buf, 0, n);
            }
            return out.length() > 0 ? out : null;
        } catch (Exception e) {
            return null;
        }
    }

    public static File imageDir(Context ctx) {
        return new File(ctx.getExternalFilesDir(null), "images");
    }
}
""".lstrip())

# ═════════════════════════════════════════════════════════════ screen control ══
w(PKG / "GilAccessibility.java", r"""
package com.gil.app;

import android.accessibilityservice.AccessibilityService;
import android.graphics.Rect;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * GIL's eyes and fingers for OTHER apps.
 *
 * This is the sanctioned Android route to reading what's on screen and tapping
 * it. It is deliberately inert until the user switches it on in
 * Settings > Accessibility, and they can revoke it there at any moment — GIL
 * never enables it silently.
 */
public class GilAccessibility extends AccessibilityService {

    private static GilAccessibility instance;

    public static boolean isRunning() { return instance != null; }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
    }

    @Override
    public void onDestroy() {
        instance = null;
        super.onDestroy();
    }

    @Override public void onAccessibilityEvent(AccessibilityEvent event) { }
    @Override public void onInterrupt() { }

    // ── what GIL calls ────────────────────────────────────────────────────────

    /** Everything readable on the current screen, as plain text. */
    public static String readScreen() {
        if (instance == null) return offMessage();
        AccessibilityNodeInfo root = instance.getRootInActiveWindow();
        if (root == null) return "I can't see the screen right now.";
        List<String> lines = new ArrayList<>();
        collect(root, lines);
        if (lines.isEmpty()) return "There's no readable text on this screen.";
        StringBuilder sb = new StringBuilder("**On screen now**\n");
        int n = 0;
        for (String l : lines) {
            sb.append("- ").append(l).append("\n");
            if (++n >= 60) { sb.append("- …\n"); break; }
        }
        return sb.toString();
    }

    /** Tap the first thing whose text/description matches. */
    public static String tap(String label) {
        if (instance == null) return offMessage();
        if (label == null || label.trim().isEmpty()) return "Tap what?";
        AccessibilityNodeInfo root = instance.getRootInActiveWindow();
        if (root == null) return "I can't see the screen right now.";
        AccessibilityNodeInfo hit = find(root, label.trim().toLowerCase(Locale.ROOT));
        if (hit == null) return "I can't find \"" + label + "\" on this screen.";
        AccessibilityNodeInfo target = hit;
        while (target != null && !target.isClickable()) target = target.getParent();
        if (target == null) return "\"" + label + "\" isn't something I can tap.";
        boolean ok = target.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        return ok ? "Tapped \"" + label + "\"." : "That tap didn't register.";
    }

    /** Type into whatever field is focused. */
    public static String type(String text) {
        if (instance == null) return offMessage();
        AccessibilityNodeInfo root = instance.getRootInActiveWindow();
        if (root == null) return "I can't see the screen right now.";
        AccessibilityNodeInfo field = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (field == null) return "No text box is focused — tap one first.";
        android.os.Bundle args = new android.os.Bundle();
        args.putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        boolean ok = field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        return ok ? "Typed it in." : "I couldn't type into that field.";
    }

    public static String scroll(String dir) {
        if (instance == null) return offMessage();
        AccessibilityNodeInfo root = instance.getRootInActiveWindow();
        if (root == null) return "I can't see the screen right now.";
        boolean back = dir != null && dir.toLowerCase(Locale.ROOT).startsWith("up");
        AccessibilityNodeInfo s = findScrollable(root);
        if (s == null) return "Nothing on this screen scrolls.";
        boolean ok = s.performAction(back
                ? AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
                : AccessibilityNodeInfo.ACTION_SCROLL_FORWARD);
        return ok ? ("Scrolled " + (back ? "up." : "down.")) : "It wouldn't scroll further.";
    }

    /** back · home · recents · notifications */
    public static String global(String what) {
        if (instance == null) return offMessage();
        int action;
        switch (what == null ? "" : what.toLowerCase(Locale.ROOT)) {
            case "home":          action = GLOBAL_ACTION_HOME; break;
            case "recents":       action = GLOBAL_ACTION_RECENTS; break;
            case "notifications": action = GLOBAL_ACTION_NOTIFICATIONS; break;
            default:              action = GLOBAL_ACTION_BACK; break;
        }
        boolean ok = instance.performGlobalAction(action);
        return ok ? ("Done — " + (what == null || what.isEmpty() ? "back" : what) + ".")
                  : "That didn't work.";
    }

    // ── tree helpers ──────────────────────────────────────────────────────────

    private static void collect(AccessibilityNodeInfo n, List<String> out) {
        if (n == null || out.size() > 200) return;
        CharSequence t = n.getText();
        CharSequence d = n.getContentDescription();
        String s = t != null ? t.toString() : (d != null ? d.toString() : "");
        s = s.trim();
        if (!s.isEmpty() && !out.contains(s)) {
            Rect r = new Rect();
            n.getBoundsInScreen(r);
            if (r.width() > 0 && r.height() > 0) out.add(s);
        }
        for (int i = 0; i < n.getChildCount(); i++) collect(n.getChild(i), out);
    }

    private static AccessibilityNodeInfo find(AccessibilityNodeInfo n, String needle) {
        if (n == null) return null;
        CharSequence t = n.getText();
        CharSequence d = n.getContentDescription();
        String s = (t != null ? t.toString() : (d != null ? d.toString() : ""))
                .toLowerCase(Locale.ROOT);
        if (!s.isEmpty() && s.contains(needle)) return n;
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo hit = find(n.getChild(i), needle);
            if (hit != null) return hit;
        }
        return null;
    }

    private static AccessibilityNodeInfo findScrollable(AccessibilityNodeInfo n) {
        if (n == null) return null;
        if (n.isScrollable()) return n;
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo hit = findScrollable(n.getChild(i));
            if (hit != null) return hit;
        }
        return null;
    }

    private static String offMessage() {
        return "Screen control is off. Say \"enable screen control\" and I'll open "
             + "the setting — you switch G.I.L. on there.";
    }
}
""".lstrip())

# ═══════════════════════════════════════════════════════════════ main activity ══
w(PKG / "MainActivity.java", r"""
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
""".lstrip())

# ══════════════════════════════════════════════════════════════════ resources ══
w(MAIN / "res" / "values" / "strings.xml", """
<resources>
    <string name="app_name">G.I.L.</string>
    <string name="accessibility_description">Lets G.I.L. read what is on your
        screen and tap, type and scroll for you, so it can act inside other apps
        when you ask. You can switch this off here at any time.</string>
</resources>
""".lstrip())

w(MAIN / "res" / "xml" / "file_paths.xml", """
<?xml version="1.0" encoding="utf-8"?>
<paths>
    <external-path name="shared" path="." />
</paths>
""".lstrip())

w(MAIN / "res" / "values" / "themes.xml", """
<resources>
    <style name="Theme.GIL" parent="Theme.AppCompat.DayNight.NoActionBar">
        <item name="android:statusBarColor">#15161B</item>
        <item name="android:navigationBarColor">#15161B</item>
        <item name="android:windowBackground">#15161B</item>
    </style>
</resources>
""".lstrip())

# ── bundle GIL's real web UI into the APK ─────────────────────────────────────
ASSETS.mkdir(parents=True, exist_ok=True)
web_src = ROOT / "web_chat"
copied = 0
for f in web_src.iterdir():
    if f.is_file() and f.suffix.lower() in (".html", ".json", ".js", ".png", ".ico", ".svg"):
        if f.name.startswith("_"):
            continue                       # skip scratch files like _test.html
        shutil.copy2(f, ASSETS / f.name)
        copied += 1
print(f"   bundled {copied} web UI files -> assets/web/")

# The web UI validates slash commands against its OWN list and rejects unknown
# ones before they ever reach the app — which is why "/key ..." silently failed.
# Register the phone-only commands in the bundled copy. `/key` takes NO argument
# (it reopens the native key screen), so the key is never echoed into the chat.
_index = ASSETS / "index.html"
_anchor = '    {cmd: "/help",'
_mobile_cmds = (
    '    {cmd: "/key",      desc: "Change your Groq API key",                kind: "meta"},\n'
    + _anchor
)
_html = _index.read_text(encoding="utf-8")
if '{cmd: "/key"' not in _html:
    if _anchor in _html:
        _index.write_text(_html.replace(_anchor, _mobile_cmds, 1), encoding="utf-8")
        print("   registered /key in the bundled UI")
    else:
        print("   [warn] could not register /key — command-list anchor not found")

# launcher icons from the official emblem
ICONS = {"mipmap-mdpi": 48, "mipmap-hdpi": 72, "mipmap-xhdpi": 96,
         "mipmap-xxhdpi": 144, "mipmap-xxxhdpi": 192}
try:
    from PIL import Image
    src = Image.open(ROOT / "web_chat" / "gil_icon_512.png").convert("RGB")
    for folder, px in ICONS.items():
        d = MAIN / "res" / folder
        d.mkdir(parents=True, exist_ok=True)
        src.resize((px, px), Image.LANCZOS).save(d / "ic_launcher.png")
    print("   launcher icons ->", ", ".join(ICONS))
except Exception as exc:
    print("   [warn] launcher icons:", exc)

w(A / "README.md", """
# G.I.L. for Android — standalone

The full GIL experience on the phone, with **no PC required**. GIL's web UI is
bundled into the APK and served by a small HTTP server running inside the app, so
the chat you know runs natively; the brain calls Groq and the actions use Android.

## What GIL can do on the phone

**Out of the box:** open apps, links and settings · search the web · show places
on the map · draft a text or an email · start a call · set alarms and timers ·
open the camera · copy and share text · talk out loud · answer anything.

**With "All files access"** (GIL asks; you grant it in Settings): read and write
your Documents, Downloads and Photos — "what's in my CV", "save this as
notes.md", "find that PDF".

**With "Screen control"** (an Accessibility Service; you switch it on in
Settings > Accessibility): GIL can read what is on screen in *other* apps and
tap, type and scroll for you — "open WhatsApp and reply to Omri", "summarise
what's on screen". Revoke it any time in the same place.

### The honest limits

Android sandboxes apps far more tightly than Windows, and these cannot be worked
around by any app:

- **No arbitrary shell** — GIL can't install/uninstall apps, change system
  settings directly or kill processes. It opens the relevant screen; you decide.
- **No reading another app's private data** — WhatsApp's message database,
  Instagram's cache and saved logins are walled off from every other app.
- **No silent screen recording** — that always needs an explicit consent dialog
  and shows a visible indicator.

Screen control is the closest thing to real automation Android allows, and it is
deliberately gated behind a switch only you can flip.

## Build

Needs Android Studio (or Android SDK + JDK 17).

1. Open this `android/` folder in Android Studio.
2. Let Gradle sync.
3. **Build -> Build Bundle(s) / APK(s) -> Build APK(s)**
4. APK: `app/build/outputs/apk/debug/app-debug.apk`

CLI, if the SDK is configured: `cd android && ./gradlew assembleDebug`

## Install & first run

Copy the APK to the phone and tap it (allow install from unknown sources), or
`adb install app-debug.apk`.

On first launch GIL asks for a **free Groq API key** (console.groq.com/keys).
Send it in the chat as:

```
/key gsk_your_key_here
```

That's the only setup. The key is stored on the phone.
""".lstrip())

print("\nAndroid standalone project written to:", A)
