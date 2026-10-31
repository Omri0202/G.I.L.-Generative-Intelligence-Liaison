// Runs web_chat/vigil_sw.js in a fake browser and checks what it does with a
// push and with taps on its notifications. Called by tests/test_vigil_sw.py.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(new URL("../web_chat/vigil_sw.js", import.meta.url), "utf8");
const handlers = {};
const shown = [];
const fetches = [];
const store = new Map();
const opened = [];
let apiAnswer = { ok: true, json: async () => ({ result: { meta: { resolved: "Done ✓ I'll remind you" } } }) };
const windows = [];

const self = {
  location: { origin: "https://omri-pc.tail1234.ts.net" },
  addEventListener: (type, fn) => { handlers[type] = fn; },
  skipWaiting: () => {},
  registration: { showNotification: async (title, opts) => { shown.push({ title, ...opts }); } },
  clients: {
    claim: async () => {},
    matchAll: async () => windows,
    openWindow: async (url) => { opened.push(url); },
  },
};
const caches = {
  open: async () => ({
    put: async (key, res) => { store.set(key, await res.text()); },
    match: async (key) => (store.has(key) ? { text: async () => store.get(key) } : undefined),
    addAll: async () => {},
  }),
  keys: async () => [],
};
class Resp { constructor(body) { this.body = body; } async text() { return this.body; } }
const fetch = async (url, init) => { fetches.push({ url, init }); return apiAnswer; };
const ctx = vm.createContext({ self, caches, fetch, Response: Resp, URL, JSON, Date, Promise, String,
                               console, Error });
vm.runInContext(src, ctx);

const fire = async (type, event) => {
  let done;
  event.waitUntil = (p) => { done = p; };
  handlers[type](event);
  await done;
};

const out = {};
// the page hands the service worker the pairing key
await fire("message", { data: { key: "secret-key" } });
// a push with Do it / Don't on it
await fire("push", { data: { json: () => ({ title: "G.I.L. · Uni", body: "Can I? Remind you", tag: "msg-7", id: 7,
  urgent: false, actions: [{ action: "approve", title: "Do it" }, { action: "deny", title: "Don't" }, { action: "x", title: "x" }] }) } });
out.shown = shown[0];
// tapping "Do it" presses the button on the PC, without opening the app
const note = { close: () => { out.closed = true; }, data: { id: 7 } };
await fire("notificationclick", { notification: note, action: "approve" });
out.fetch = { url: fetches[0].url, headers: fetches[0].init.headers, body: JSON.parse(fetches[0].init.body) };
out.confirm = shown[1];
out.openedAfterAction = opened.length;
// PC out of reach: a tap on "Do it" opens the app instead
apiAnswer = { ok: false, json: async () => null };
await fire("notificationclick", { notification: { close() {}, data: { id: 9 } }, action: "approve" });
out.openedWhenUnreachable = opened.slice();
// a plain tap with the app already open focuses it and jumps to the message
let focused = 0, posted = null;
windows.push({ focus: async () => { focused++; }, postMessage: (m) => { posted = m; } });
await fire("notificationclick", { notification: { close() {}, data: { id: 11 } }, action: "" });
out.focused = focused; out.posted = posted;
// a silent "working on it" note doesn't buzz
await fire("push", { data: { json: () => ({ title: "G.I.L.", body: "Working on it", tag: "gil-job", silent: true }) } });
out.silent = shown[shown.length - 1];
console.log(JSON.stringify(out));
