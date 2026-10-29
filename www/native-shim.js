/* Native helpers for the installed apps. In a normal browser this file does nothing. */
(function () {
  var C = window.Capacitor;
  var native = !!(C && C.isNativePlatform && C.isNativePlatform());
  var P = native ? (C.Plugins || {}) : {};
  function b64(blob) {
    return new Promise(function (ok, bad) { var r = new FileReader(); r.onload = function () { ok(String(r.result).split(',')[1]); }; r.onerror = bad; r.readAsDataURL(blob); });
  }
  function hash(s) { var h = 0; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h) % 200000; }
  window.NativeShim = {
    native: native,
    /** Phones: write the file, then open the share sheet so it can go to Files, Drive, email, etc. */
    saveFile: async function (name, blob) {
      if (!native || !P.Filesystem) return false;
      var safe = String(name).replace(/[\\/:*?"<>|]+/g, '_');
      var res = await P.Filesystem.writeFile({ path: safe, data: await b64(blob), directory: 'CACHE' });
      if (P.Share) { try { await P.Share.share({ title: safe, files: [res.uri] }); } catch (e) { /* closed the sheet */ } }
      return true;
    },
    /** Phones: system notifications (with sound) at each vault check-in, even when the app is closed. */
    syncVault: async function (rec) {
      if (!native || !P.LocalNotifications || !rec) return;
      var LN = P.LocalNotifications, base = hash(rec.id) * 40, ids = [];
      for (var i = 0; i < 40; i++) ids.push({ id: base + i });
      try { await LN.cancel({ notifications: ids }); } catch (e) { /* none yet */ }
      if (rec.terminated || rec.autoRenew) return;
      var perm = await LN.checkPermissions();
      if (perm.display !== 'granted') perm = await LN.requestPermissions();
      if (perm.display !== 'granted') return;
      var now = Date.now(), list = [];
      (rec.checkins || []).forEach(function (c, i) {
        if (c > now && !(rec.answers || {})[c]) list.push({ id: base + i, title: 'Vault Secure check-in', body: rec.nick + ': renew, auto-renew or terminate. Open Vault Secure > My vaults.', schedule: { at: new Date(c), allowWhileIdle: true } });
      });
      var endWarn = rec.expires - 30 * 86400000;
      if (endWarn > now) list.push({ id: base + 39, title: 'Vault Secure', body: rec.nick + ' stops opening in 30 days.', schedule: { at: new Date(endWarn), allowWhileIdle: true } });
      if (list.length) await LN.schedule({ notifications: list });
    },
  };
  var FEMALE = /female|woman|samantha|victoria|karen|moira|tessa|fiona|zira|serena|allison|ava|joanna|sonia|aria|jenny|emma|olivia|amy/i;
  var MALE = /\bmale\b|daniel|alex|fred|david|mark|george|james|ryan|guy|tom|oliver|arthur|aaron|thomas|matthew|brian/i;
  /** Phones: read text aloud with the system voice, choosing a female or male voice when the device names them. */
  window.NativeShim.speak = async function (text, opts) {
    if (!native || !P.TextToSpeech) throw new Error('No native voice');
    opts = opts || {};
    var voice; try {
      var list = (await P.TextToSpeech.getSupportedVoices()).voices || [];
      var re = opts.gender === 'male' ? MALE : FEMALE;
      for (var i = 0; i < list.length; i++) if (/^en/i.test(list[i].lang) && re.test(list[i].name || '')) { voice = i; break; }
    } catch (e) { /* voices unknown */ }
    var o = { text: text, lang: 'en-US', rate: opts.rate || 1, pitch: voice == null && opts.gender === 'male' ? 0.8 : 1, volume: 1, category: 'playback' };
    if (voice != null) o.voice = voice;
    return P.TextToSpeech.speak(o);
  };
  window.NativeShim.stopSpeaking = function () { if (native && P.TextToSpeech) return P.TextToSpeech.stop(); };
  // Phones: open web links in the system browser instead of inside the app.
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest && e.target.closest('a[href]');
    if (!a || !native) return;
    var href = a.getAttribute('href') || '';
    if (/^https?:/i.test(href) && P.Browser) { e.preventDefault(); P.Browser.open({ url: a.href }); }
  }, true);
})();
