package com.gil.app;

import android.content.Context;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/**
 * Website generation on the phone.
 *
 * Desktop GIL writes a multi-file site to disk; a phone has no browser-visible
 * filesystem to speak of, so here a site is ONE self-contained HTML file (styles
 * and scripts inline). It's saved to the app's storage and served back through
 * GilServer, so it opens instantly in the chat or in a real browser.
 */
public class GilWeb {

    private static final String SYSTEM =
        "You are a senior front-end engineer. Produce ONE complete, self-contained "
      + "HTML file for the site the user describes.\n"
      + "Rules:\n"
      + "- Output ONLY the HTML. No markdown fences, no commentary, no explanation.\n"
      + "- Start at <!DOCTYPE html> and end at </html>.\n"
      + "- Everything inline: <style> in the head, <script> before </body>. No "
      + "external files, no build step, no frameworks, no CDN links.\n"
      + "- It must look genuinely designed: a considered colour palette, real "
      + "type hierarchy, generous spacing, hover states, and a responsive layout "
      + "that works on a phone screen first.\n"
      + "- Write real, specific copy for the subject — never lorem ipsum and never "
      + "placeholder brackets.\n"
      + "- Use CSS gradients, shapes or inline SVG for visuals instead of linking "
      + "images that won't load.";

    /** Generate and save; returns the saved file, or null. */
    public static File generate(Context ctx, GilBrain brain, String prompt) {
        // 6500, NOT 8000: the free tier's ceiling is 8000 tokens per minute for
        // the whole request, and max_tokens is reserved up front — asking for
        // 8000 plus the prompt is 8242 and gets rejected outright, so website
        // generation would have failed every single time.
        String html = brain.complete(SYSTEM, "Build this site: " + prompt, 6500);
        html = clean(html);
        if (html.isEmpty()) return null;
        try {
            File dir = siteDir(ctx);
            if (!dir.exists() && !dir.mkdirs()) return null;
            File out = new File(dir, "site_" + System.currentTimeMillis() + ".html");
            try (FileOutputStream fos = new FileOutputStream(out)) {
                fos.write(html.getBytes(StandardCharsets.UTF_8));
            }
            return out;
        } catch (Exception e) {
            return null;
        }
    }

    /** Models still wrap HTML in fences or a sentence — take just the document. */
    static String clean(String raw) {
        if (raw == null) return "";
        String s = raw.trim();
        s = s.replace("```html", "").replace("```HTML", "").replace("```", "");
        int start = s.toLowerCase(Locale.ROOT).indexOf("<!doctype");
        if (start < 0) start = s.toLowerCase(Locale.ROOT).indexOf("<html");
        if (start > 0) s = s.substring(start);
        int end = s.toLowerCase(Locale.ROOT).lastIndexOf("</html>");
        if (end > 0) s = s.substring(0, end + 7);
        s = s.trim();
        // must actually be a page, not an apology
        return s.toLowerCase(Locale.ROOT).contains("<html") ? s : "";
    }

    /** A readable title for the card, derived from the request. */
    static String titleFor(String prompt) {
        String t = prompt == null ? "" : prompt.trim();
        if (t.length() > 46) t = t.substring(0, 46).trim() + "…";
        if (t.isEmpty()) return "Website";
        return Character.toUpperCase(t.charAt(0)) + t.substring(1);
    }

    public static File siteDir(Context ctx) {
        return new File(ctx.getExternalFilesDir(null), "sites");
    }
}
