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
