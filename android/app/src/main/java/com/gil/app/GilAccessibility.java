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
