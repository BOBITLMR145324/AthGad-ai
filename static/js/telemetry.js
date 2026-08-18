// Set the dynamic risk bar width from the data-score attribute
var activeFilter = "all";
function applyBarWidths() {
  document.querySelectorAll("[data-score]").forEach(function (bar) {
    var score = bar.getAttribute("data-score");
    if (score && !isNaN(parseFloat(score))) {
      bar.style.width = score + "%";
    }
  });
}
document.addEventListener("DOMContentLoaded", applyBarWidths);

// Refresh the live telemetry data for all counties (no login required).
function refreshTelemetry() {
  var statusEl = document.getElementById("refresh-status");
  var btn = document.getElementById("btn-refresh");
  var originalHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-50", "cursor-not-allowed");
  }
  if (statusEl) statusEl.textContent = "Refreshing...";

  fetch("/api/v1/telemetry/refresh")
    .then(function (res) {
      return res.json();
    })
    .then(function (data) {
      if (data.status !== "ok") {
        if (statusEl)
          statusEl.textContent = "Refresh failed. Please try again.";
        return;
      }

      // Update global summary numbers.
      var highRiskText = document.querySelector(".min-w-\\[280px\\] p");
      if (highRiskText) {
        highRiskText.textContent =
          data.high_risk_count + " Counties at High Risk";
      }

      // Update climate & health average cards (stable IDs).
      var climateSpan = document.getElementById("avg-climate");
      var healthSpan = document.getElementById("avg-health");
      if (climateSpan) climateSpan.textContent = data.avg_climate + "%";
      if (healthSpan) healthSpan.textContent = data.avg_health + "%";

      // Update the "Updated:" timestamp.
      var lastSync = document.getElementById("last-sync");
      if (lastSync) lastSync.textContent = "Updated: " + data.last_sync;

      // Rebuild the county grid from the fresh status board.
      var grid = document.getElementById("county-grid");
      if (grid && data.status_board) {
        grid.innerHTML = buildCountyCards(data.status_board);
        applyBarWidths();
        // Preserve the currently selected filter across the rebuild.
        if (activeFilter && activeFilter !== "all") {
          filterCounties(activeFilter);
        }
      }

      if (statusEl) statusEl.textContent = "Updated " + data.last_sync;
    })
    .catch(function () {
      if (statusEl) statusEl.textContent = "Refresh failed. Please try again.";
    })
    .finally(function () {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("opacity-50", "cursor-not-allowed");
        btn.innerHTML = originalHtml;
      }
    });
}

// Build county card HTML for a status board item.
function buildCountyCards(items) {
  return items
    .map(function (item) {
      var barColor =
        item.risk_level === "High"
          ? "bg-rose-500"
          : item.risk_level === "Medium"
            ? "bg-amber-400"
            : item.risk_level === "Offline"
              ? "bg-slate-500"
              : "bg-emerald-400";
      var topColor =
        item.risk_level === "High"
          ? "text-rose-400"
          : item.risk_level === "Medium"
            ? "text-amber-400"
            : item.risk_level === "Offline"
              ? "text-slate-400"
              : "text-emerald-400";
      var badgeColor =
        item.risk_level === "High"
          ? "bg-rose-500/20 text-rose-300 border-rose-500/30"
          : item.risk_level === "Medium"
            ? "bg-amber-500/20 text-amber-300 border-amber-500/30"
            : item.risk_level === "Offline"
              ? "bg-slate-500/20 text-slate-300 border-slate-500/30"
              : "bg-emerald-500/20 text-emerald-300 border-emerald-500/30";
      var cardBorder =
        item.risk_level === "High"
          ? "border-rose-500/40"
          : item.risk_level === "Medium"
            ? "border-amber-500/40"
            : item.risk_level === "Offline"
              ? "border-slate-600"
              : "border-slate-800";
      var highClass = item.risk_level === "High" ? "high" : "";

      return (
        '<div class="county-card ' +
        highClass +
        " " +
        item.threat_category +
        " bg-slate-900/80 hover:bg-slate-900 border " +
        cardBorder +
        ' rounded-2xl p-5 space-y-4 transition duration-200">' +
        '<div class="flex justify-between items-start">' +
        '<div><h3 class="font-bold text-white text-base">' +
        item.county +
        ' County</h3><span class="text-[11px] text-slate-400">Eastern Region</span></div>' +
        '<span class="px-2 py-0.5 rounded ' +
        badgeColor +
        ' font-bold text-[10px] uppercase border">' +
        item.risk_level +
        "</span></div>" +
        '<div class="space-y-2">' +
        '<div class="flex justify-between text-xs"><span class="text-slate-400">Calculated Risk Index</span>' +
        '<span class="font-bold ' +
        topColor +
        '">' +
        item.score_pct +
        "%</span></div>" +
        '<div class="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">' +
        '<div class="' +
        barColor +
        ' h-full rounded-full transition-all duration-500" data-score="' +
        item.score_pct +
        '"></div></div></div>' +
        '<div class="text-[11px] text-slate-400 pt-2 border-t border-slate-800 flex justify-between">' +
        "<span>Threat: " +
        item.primary_threat +
        "</span></div></div>"
      );
    })
    .join("");
}

function filterCounties(type) {
  activeFilter = type;
  const cards = document.querySelectorAll(".county-card");
  const buttons = document.querySelectorAll(".filter-btn");

  buttons.forEach((btn) => {
    btn.classList.remove("bg-emerald-500", "text-slate-950", "font-bold");
    btn.classList.add("bg-slate-900", "text-slate-300", "font-medium");
  });

  const activeBtn = document.getElementById("btn-" + type);
  if (activeBtn) {
    activeBtn.classList.add("bg-emerald-500", "text-slate-950", "font-bold");
    activeBtn.classList.remove("bg-slate-900", "text-slate-300");
  }

  cards.forEach((card) => {
    if (type === "all") {
      card.style.display = "block";
    } else if (card.classList.contains(type)) {
      card.style.display = "block";
    } else {
      card.style.display = "none";
    }
  });
}
