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
