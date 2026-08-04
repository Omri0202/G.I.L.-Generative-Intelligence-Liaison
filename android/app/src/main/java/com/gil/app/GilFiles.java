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
