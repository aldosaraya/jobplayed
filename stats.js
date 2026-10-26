/* RadarJobs — live counters.
 * Fills every [data-stat] element from /jobs.json so no number on the page
 * is hardcoded. stats: jobs | sources | fresh
 */
(function () {
  "use strict";

  function setAll(key, value) {
    document.querySelectorAll('[data-stat="' + key + '"]').forEach(function (el) {
      el.textContent = value;
    });
  }

  fetch("jobs.json", { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (jobs) {
      var total = jobs.length;
      var sources = new Set(jobs.map(function (j) { return j.source; })).size;
      var weekAgo = Date.now() - 7 * 864e5;
      var fresh = jobs.filter(function (j) {
        return j.posted_at && new Date(j.posted_at).getTime() >= weekAgo;
      }).length;
      var pct = total ? Math.round((fresh / total) * 100) : 0;

      setAll("jobs", total.toLocaleString("en-US"));
      setAll("sources", String(sources));
      setAll("fresh", pct + "%");

      // legacy ids kept for the landing metrics grid
      var legacy = {
        "metric-jobs": total.toLocaleString("en-US"),
        "metric-jobs-ticker": total.toLocaleString("en-US"),
        "metric-total-inline": total.toLocaleString("en-US"),
        "metric-sources": String(sources),
        "metric-fresh": pct + "%"
      };
      Object.keys(legacy).forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.textContent = legacy[id];
      });
    })
    .catch(function (err) {
      console.warn("[radarjobs] stats unavailable", err);
    });
})();
