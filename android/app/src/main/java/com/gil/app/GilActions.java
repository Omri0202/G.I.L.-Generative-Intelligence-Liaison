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
