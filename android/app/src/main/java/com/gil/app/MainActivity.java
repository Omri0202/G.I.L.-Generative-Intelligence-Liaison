package com.gil.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

/**
 * G.I.L. — a thin native shell around GIL's web chat.
 *
 * GIL's brain runs on the PC; this is the phone client. On first launch it asks
 * for the pairing URL (the one /mobile shows as a QR), stores it, and from then
 * on opens straight into the chat with no browser chrome.
 */
public class MainActivity extends AppCompatActivity {

    private static final String PREFS = "gil";
    private static final String KEY_URL = "pairing_url";

    private WebView web;
    private ValueCallback<Uri[]> filePicker;
    private static final int FILE_REQ = 1001;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        String url = prefs().getString(KEY_URL, "");
        if (TextUtils.isEmpty(url)) {
            showSetup(null);
        } else {
            showChat(url);
        }
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** First-run (or "change server") screen: paste the pairing URL. */
    private void showSetup(String prefill) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#15161B"));
        int pad = (int) (24 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad * 2, pad, pad);

        TextView title = new TextView(this);
        title.setText("Connect to G.I.L.");
        title.setTextColor(Color.parseColor("#E9EAF2"));
        title.setTextSize(22);
        root.addView(title);

        TextView hint = new TextView(this);
        hint.setText("On your PC run /mobile in GIL's chat, then paste the link "
                + "it shows (or scan the QR and copy the URL).\n\n"
                + "Your phone must be on the same Wi-Fi as the PC.");
        hint.setTextColor(Color.parseColor("#9BA0B4"));
        hint.setTextSize(14);
        hint.setPadding(0, pad / 2, 0, pad);
        root.addView(hint);

        final EditText input = new EditText(this);
        input.setHint("http://192.168.x.x:8731/?t=...");
        input.setTextColor(Color.parseColor("#E9EAF2"));
        input.setHintTextColor(Color.parseColor("#787D91"));
        input.setSingleLine(true);
        if (prefill != null) input.setText(prefill);
        root.addView(input);

        Button go = new Button(this);
        go.setText("Connect");
        go.setOnClickListener(v -> {
            String u = input.getText().toString().trim();
            if (TextUtils.isEmpty(u)) {
                Toast.makeText(this, "Paste the link from /mobile first",
                        Toast.LENGTH_SHORT).show();
                return;
            }
            if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://" + u;
            prefs().edit().putString(KEY_URL, u).apply();
            showChat(u);
        });
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = pad;
        root.addView(go, lp);

        setContentView(root);
    }

    /** The chat itself. */
    @SuppressLint("SetJavaScriptEnabled")
    private void showChat(String url) {
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // the pairing token lives here
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        web.setBackgroundColor(Color.parseColor("#15161B"));

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                return false;                  // keep navigation inside the app
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest req,
                                        WebResourceError err) {
                if (req != null && req.isForMainFrame()) {
                    Toast.makeText(MainActivity.this,
                            "Can't reach GIL — is the PC awake and on the same Wi-Fi?",
                            Toast.LENGTH_LONG).show();
                }
            }
        });

        // file chooser so the chat's attach button works
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb,
                                             FileChooserParams params) {
                filePicker = cb;
                try {
                    startActivityForResult(params.createIntent(), FILE_REQ);
                } catch (Exception e) {
                    filePicker = null;
                    return false;
                }
                return true;
            }
        });

        web.loadUrl(url);
        setContentView(web);
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == FILE_REQ) {
            if (filePicker != null) {
                filePicker.onReceiveValue(
                        FileChooserParams.parseResult(res, data));
                filePicker = null;
            }
            return;
        }
        super.onActivityResult(req, res, data);
    }

    /** Back navigates the chat; long-press back re-opens the setup screen. */
    @Override
    public boolean onKeyDown(int code, KeyEvent event) {
        if (code == KeyEvent.KEYCODE_BACK && web != null) {
            if (event.isLongPress()) {
                showSetup(prefs().getString(KEY_URL, ""));
                return true;
            }
            if (web.canGoBack()) {
                web.goBack();
                return true;
            }
        }
        return super.onKeyDown(code, event);
    }
}
