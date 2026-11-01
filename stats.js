/* RadarJobs — live counters.
 * Reads the feed_stats view from Supabase (live); falls back to /jobs.json.
 * Fills every [data-stat] element: jobs | sources | fresh
 */
(function () {
  "use strict";

  var SUPA_URL = "https://juijmkcfbjthohjpgflr.supabase.co";
  var SUPA_ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp1aWpta2NmYmp0aG9oanBnZmxyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTI1NzQ3MDEsImV4cCI6MjEwODE1MDcwMX0.nXQcCeRno_SXhQziizZ4sM4vQZxLKqgO6uv1tWcmAAU"; // replaced at build time from auth.js

  function setAll(key, value) {
    document.querySelectorAll('[data-stat="' + key + '"]').forEach(function (el) {
      el.textContent = value;
    });
  }

  function paint(s) {
    var total = Number(s.total) || 0;
    var pct = total ? Math.round(((Number(s.fresh) || 0) / total) * 100) : 0;
    setAll("jobs", total.toLocaleString("en-US"));
    setAll("sources", String(s.sources));
    setAll("fresh", pct + "%");

    // legacy ids kept for the landing metrics grid
    var legacy = {
      "metric-jobs": total.toLocaleString("en-US"),
      "metric-jobs-ticker": total.toLocaleString("en-US"),
      "metric-total-inline": total.toLocaleString("en-US"),
      "metric-sources": String(s.sources),
      "metric-fresh": pct + "%"
    };
    Object.keys(legacy).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.textContent = legacy[id];
    });
  }

  function fallback() {
    return fetch("jobs.json", { cache: "no-cache" })
      .then(function (r) { return r.json(); })
      .then(function (jobs) {
        var sources = {};
        var weekAgo = Date.now() - 7 * 864e5;
        var fresh = 0;
        jobs.forEach(function (j) {
          sources[j.source] = 1;
          if (j.posted_at && new Date(j.posted_at).getTime() >= weekAgo) fresh++;
        });
        paint({ total: jobs.length, sources: Object.keys(sources).length, fresh: fresh });
      });
  }

  fetch(SUPA_URL + "/rest/v1/feed_stats?select=*", {
    headers: { apikey: SUPA_ANON, Authorization: "Bearer " + SUPA_ANON },
    cache: "no-cache",
  })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (rows) {
      if (!rows || !rows.length) throw new Error("empty stats");
      paint(rows[0]);
    })
    .catch(function (err) {
      console.warn("[radarjobs] stats via supabase fallo, fallback:", err.message);
      return fallback();
    });
})();
