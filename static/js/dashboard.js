// Global variable container to store the chart instance
let metricsChartInstance = null;

// Escape server-provided strings before injecting them into innerHTML so a
// crafted advisory/alert value cannot execute script in a user's browser.
function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

document.addEventListener("DOMContentLoaded", function () {
  // Initial system load run
  triggerRiskEvaluationPipeline();
});

function triggerRiskEvaluationPipeline() {
  // Read the current selected county dropdown value
  const selectedCounty = document.getElementById("county-selector").value;

  // Locate your refresh button element
  // (Ensure your HTML refresh button has id="refresh-btn" or adjust this selector)
  const refreshBtn =
    document.getElementById("refresh-btn") ||
    document.querySelector("button[onclick*='triggerRiskEvaluationPipeline']");

  // Cache the original button text and design styles so we can restore them perfectly later
  let originalText = "";
  let originalClasses = "";

  if (refreshBtn) {
    originalText = refreshBtn.innerText;
    originalClasses = refreshBtn.className;

    // 1. Lock the button to prevent double-clicking while processing
    refreshBtn.disabled = true;
    refreshBtn.innerText = "Please Wait...";

    // 2. Switch classes to a neon pulsing red/blue color scheme
    refreshBtn.className =
      "px-4 py-2 rounded-lg font-bold text-white bg-gradient-to-r from-rose-600 to-blue-600 animate-pulse shadow-[0_0_15px_rgba(244,63,94,0.6)] cursor-not-allowed transition duration-500";
  }

  // Pass the selected county parameter safely down the endpoint string
  const apiStatusUrl = `/api/v1/risk-status?county=${encodeURIComponent(selectedCounty)}`;

  console.log(`Fetching latest risk updates for: ${selectedCounty}...`);

  fetch(apiStatusUrl)
    .then((response) => {
      if (!response.ok)
        throw new Error("Could not reach the server. Please try again.");
      return response.json();
    })
    .then((data) => {
      // 1. Refresh primary dashboard text metrics
      document.getElementById("current-county-title").innerText =
        `${data.county} Risk Status`;
      document.getElementById("risk-score").innerText =
        data.composite_risk_score;
      document.getElementById("sync-clock").innerText = data.timestamp;

      // 2. Format the UI Risk Badge based on severity levels
      const badge = document.getElementById("risk-badge");
      badge.innerText = data.risk_level.toUpperCase();

      badge.className = "px-3 py-1 rounded-full text-xs font-bold ";
      if (data.risk_level === "Low") {
        badge.className +=
          "bg-emerald-950/80 text-emerald-400 border border-emerald-800";
      } else if (data.risk_level === "Medium") {
        badge.className +=
          "bg-amber-950/80 text-amber-400 border border-amber-800";
      } else {
        badge.className +=
          "bg-rose-950/80 text-rose-400 border border-rose-800 animate-pulse";
      }

      // 3. Inject Predicted Calamity & Driver metrics
      document.getElementById("predicted-calamity").innerText =
        data.advisory.primary_calamity;
      document.getElementById("vulnerability-drivers").innerText =
        `Main Causes: ${data.advisory.vulnerability_drivers}`;

      // 4. Inject Proactive "Before It Happens" Action Checklist
      const solutionsContainer = document.getElementById("solutions-checklist");
      solutionsContainer.innerHTML = "";
      data.advisory.proactive_solutions.forEach((solution) => {
        solutionsContainer.innerHTML += `
                    <div class="flex items-start space-x-3 p-2.5 rounded-lg bg-slate-900 border border-slate-800 hover:border-amber-500/30 transition">
                        <input type="checkbox" class="mt-1 accent-amber-500 rounded h-4 w-4" checked onclick="return false;">
                        <span class="text-slate-200 text-xs leading-relaxed">${escapeHtml(solution)}</span>
                    </div>
                `;
      });

      // 5. Inject Cascading Disease Outbreak & Risk arrays
      const effectsContainer = document.getElementById("cascading-effects");
      effectsContainer.innerHTML = "";
      data.advisory.cascading_effects.forEach((effect) => {
        effectsContainer.innerHTML += `<li>${escapeHtml(effect)}</li>`;
      });

      // 6. Render the Multi-Domain Chart values
      renderDomainMetricsChart(data.metrics);

      // 7. Refresh historical rows table array
      loadStagedAlertHistory();
    })
    .catch((error) => {
      console.error("Dashboard controller synchronization failed: ", error);
    })
    .finally(() => {
      // 3. RESTORATION POINT: This executes after the network transaction completes (success or failure)
      if (refreshBtn) {
        refreshBtn.disabled = false;
        refreshBtn.innerText = originalText;
        refreshBtn.className = originalClasses;
      }
    });
}
function renderDomainMetricsChart(metrics) {
  const ctx = document.getElementById("metricsChart").getContext("2d");

  const chartLabels = [
    "Drought Risk",
    "Health Risk",
    "Storm Risk",
    "Hidden Risk Factor",
  ];

  // FIX #2: Convert percentage text strings (e.g., "42.0%") into standard 0.0 - 1.0 decimals for chart visualization
  const parsePercentageToDecimal = (val) => {
    if (typeof val === "string") {
      return parseFloat(val.replace("%", "")) / 100;
    }
    return val;
  };

  const chartDataValues = [
    parsePercentageToDecimal(metrics.climate_severity),
    parsePercentageToDecimal(metrics.health_severity),
    parsePercentageToDecimal(metrics.space_weather_severity),
    parsePercentageToDecimal(metrics.hidden_anomaly_factor),
  ];

  if (metricsChartInstance) {
    metricsChartInstance.destroy();
  }

  metricsChartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels: chartLabels,
      datasets: [
        {
          label: "Risk Level (0.0 - 1.0)",
          data: chartDataValues,
          backgroundColor: [
            "rgba(16, 185, 129, 0.2)",
            "rgba(59, 130, 246, 0.2)",
            "rgba(245, 158, 11, 0.2)",
            "rgba(139, 92, 246, 0.2)",
          ],
          borderColor: ["#10b981", "#3b82f6", "#f59e0b", "#8b5cf6"],
          borderWidth: 1.5,
          borderRadius: 6,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          max: 1.0,
          grid: { color: "#1e293b" },
          ticks: { color: "#94a3b8" },
        },
        x: {
          grid: { display: false },
          ticks: { color: "#94a3b8" },
        },
      },
      plugins: {
        legend: { display: false },
      },
    },
  });
}

function loadStagedAlertHistory() {
  // Change the URL path right here ──────────────────────▼
  fetch("/api/v1/alerts/history")
    .then((response) => {
      if (!response.ok)
        throw new Error("Could not load alert history. Please try again.");
      return response.json();
    })
    .then((data) => {
      // Find the table element or the element reading "Querying alert records array..."
      let targetDOMElement = document.getElementById("historical-alerts-rows");
      if (!targetDOMElement) {
        // Fallback: If no explicit ID exists, locate the table body dynamically
        targetDOMElement = document.querySelector("table")
          ? document.querySelector("table").getElementsByTagName("tbody")[0]
          : null;
      }

      if (!targetDOMElement) return;

      // Clear out the "Querying alert records array..." placeholder loading row
      targetDOMElement.innerHTML = "";

      // Safety check: handle cases where data might arrive nested under an object key
      const alertsArray = Array.isArray(data) ? data : data.alerts || [];

      if (alertsArray.length === 0) {
        targetDOMElement.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-slate-500 text-xs">No recent alerts in your area.</td></tr>`;
        return;
      }

      // Loop through each historical county record row using our safe array tracker
      alertsArray.forEach((item) => {
        try {
          // Safe fallbacks for missing keys to prevent script crashes
          const calamity = item.calamity_type || "Weather Anomaly";
          const timestamp = item.timestamp || "Recent";
          const county = item.county || "Unknown Area";
          const rating = item.composite_rating || "0.0%";
          const level = item.risk_level || "Medium";

          // Determine table badge colors based on severity status layout
          let badgeClass =
            "bg-amber-950/40 text-amber-400 border border-amber-900/60";
          if (level.toLowerCase() === "low")
            badgeClass =
              "bg-emerald-950/40 text-emerald-400 border border-emerald-900/60";
          if (level.toLowerCase() === "high")
            badgeClass =
              "bg-rose-950/40 text-rose-400 border border-rose-900/60 animate-pulse";

          const rowHTML = `
    <tr class="border-b border-slate-800/80 hover:bg-slate-800/40 transition duration-200 text-[13px] text-slate-200 text-left">
        <td class="py-5 px-6 font-semibold tracking-wide text-slate-100 max-w-[280px] truncate border-r border-slate-800/40">
            ${escapeHtml(calamity)}
        </td>
        
        <td class="py-5 px-6 font-mono text-xs text-slate-400 border-r border-slate-800/40">
            ${escapeHtml(timestamp)}
        </td>
        
        <td class="py-5 px-6 font-bold text-white border-r border-slate-800/40 group cursor-pointer">
            <span class="text-white group-hover:text-emerald-400 transition-colors duration-300 ease-in-out tracking-wide">
                ${escapeHtml(county)}
            </span>
        </td>
        
        <td class="py-5 px-6 font-mono font-bold text-white text-sm border-r border-slate-800/40">
            ${escapeHtml(rating)}
        </td>
        
        <td class="py-5 px-6">
            <span class="px-3 py-1 rounded-md text-[11px] font-extrabold tracking-wider uppercase shadow-sm ${badgeClass}">
                ${escapeHtml(level)}
            </span>
        </td>
    </tr>
`;
          targetDOMElement.innerHTML += rowHTML;
        } catch (rowError) {
          console.error("Could not display an alert: ", rowError);
        }
      });
    })
    .catch((err) => {
      console.error("Could not load the risk board: ", err);
      const targetDOMElement =
        document.getElementById("historical-alerts-rows") ||
        document.querySelector("table tbody");
      if (targetDOMElement) {
        targetDOMElement.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-rose-400 text-xs">Something went wrong. Click refresh to try again.</td></tr>`;
      }
    });
}
