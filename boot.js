/* RadarJobs — pre-paint session flag.
 * Loaded synchronously in <head> so the first paint already knows whether the
 * visitor is signed in: guest CTAs never flash at a logged-in member.
 * app.js re-validates against Supabase and corrects the class afterwards.
 */
(function () {
  var root = document.documentElement;
  var signed = false;
  try {
    var tokenKey = null;
    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (k && k.indexOf("sb-") === 0 && k.indexOf("auth-token") !== -1) { tokenKey = k; break; }
    }
    var raw = tokenKey ? localStorage.getItem(tokenKey) : null;
    if (raw) {
      var session = JSON.parse(raw);
      var exp = session && session.expires_at ? session.expires_at * 1000 : 0;
      signed = Boolean(session && session.access_token && (!exp || exp > Date.now()));
    }
  } catch (e) {
    signed = false;
  }
  root.classList.add(signed ? "auth" : "guest");
})();
