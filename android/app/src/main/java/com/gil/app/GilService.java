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
