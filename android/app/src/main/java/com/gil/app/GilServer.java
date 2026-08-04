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

    /** Push an event to the UI — the Java side of GIL's `_push`. */
    public void push(String method, Object payload) {
        JSONObject msg = new JSONObject();
        try {
            msg.put("method", method);
            msg.put("payload", payload == null ? JSONObject.NULL : payload);
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

    /** The web UI is bundled in assets/web/. */
    private void serveAsset(String route, OutputStream out) throws Exception {
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
