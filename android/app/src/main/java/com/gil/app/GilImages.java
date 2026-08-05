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

    /**
     * Outcome of one attempt. Carrying the REASON matters: swallowing the
     * exception and returning null gave "the picture service didn't come back"
     * for every possible cause — network, storage, HTTP status — which is
     * impossible to act on or report.
     */
    public static class Result {
        public final File file;
        public final String error;
        Result(File f, String e) { file = f; error = e; }
        public boolean ok() { return file != null; }
    }

    public static Result generate(Context ctx, String prompt) {
        File dir;
        try {
            dir = imageDir(ctx);
            if (!dir.exists() && !dir.mkdirs()) {
                return new Result(null, "I can't create a folder to save pictures in.");
            }
        } catch (Exception e) {
            return new Result(null, "No storage available to save the picture.");
        }

        // The provider 5xx's intermittently — the same request that fails can
        // succeed a second later — so retry before giving up, and drop the
        // optional model/enhance params on later tries in case they're the
        // thing upsetting it.
        String last = "the picture service didn't answer.";
        for (int attempt = 0; attempt < 3; attempt++) {
            if (attempt > 0) {
                try {
                    Thread.sleep(1500L * attempt);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    return new Result(null, "cancelled.");
                }
            }
            Result r = attempt(ctx, dir, prompt, attempt);
            if (r.ok()) return r;
            last = r.error;
            // a 4xx won't fix itself by asking again
            if (r.error != null && r.error.contains("answered 4")) break;
        }
        return new Result(null, last);
    }

    private static Result attempt(Context ctx, File dir, String prompt, int tryNo) {
        HttpURLConnection c = null;
        try {
            String encoded = URLEncoder.encode(prompt, "UTF-8").replace("+", "%20");
            int seed = 1 + RAND.nextInt(99_999);
            String extras = tryNo == 0 ? "&model=flux&enhance=true" : "";
            String url = "https://image.pollinations.ai/prompt/" + encoded
                    + "?width=1024&height=1024&seed=" + seed
                    + extras + "&nologo=true";

            c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(20000);
            c.setReadTimeout(180000);          // generation genuinely takes a while
            c.setInstanceFollowRedirects(true);
            // the provider 403s some default library user-agents, so be explicit
            c.setRequestProperty("User-Agent", "GIL-Android/1.0");
            c.setRequestProperty("Accept", "image/*");

            int code = c.getResponseCode();
            if (code != 200) {
                return new Result(null, "the picture service answered " + code + ".");
            }

            File out = new File(dir, "gil_" + System.currentTimeMillis() + ".jpg");
            long written = 0;
            try (InputStream in = c.getInputStream();
                 FileOutputStream fos = new FileOutputStream(out)) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) {
                    fos.write(buf, 0, n);
                    written += n;
                }
            }
            if (written <= 0) {
                out.delete();
                return new Result(null, "the picture came back empty.");
            }
            return new Result(out, null);
        } catch (java.net.SocketTimeoutException e) {
            return new Result(null, "the picture service took too long to answer.");
        } catch (java.net.UnknownHostException e) {
            return new Result(null, "I can't reach the internet right now.");
        } catch (Exception e) {
            String m = e.getClass().getSimpleName()
                     + (e.getMessage() == null ? "" : ": " + e.getMessage());
            return new Result(null, m);
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /**
     * Where pictures live. getExternalFilesDir can return null when external
     * storage isn't mounted, so fall back to internal storage rather than
     * NPE-ing into a silent failure.
     */
    public static File imageDir(Context ctx) {
        File base = ctx.getExternalFilesDir(null);
        if (base == null) base = ctx.getFilesDir();
        return new File(base, "images");
    }
}
