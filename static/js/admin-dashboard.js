// Live County Risk Trend — single combined line chart (all 8 counties).
const countyTrendPalette = [
  "#10b981",
  "#3b82f6",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#06b6d4",
  "#ec4899",
  "#84cc16",
];
const countyTrendState = {
  labels: [],
  series: [],
  colors: {}, // county -> line color
  visible: {}, // county -> bool (toggle state)
  chart: null, // single Chart instance
};

// Apply the risk-distribution percentage widths to the progress bars.
// Widths are read from data-fill-width attributes (rendered by Jinja)
// so the script body stays free of template tags.
document.addEventListener("DOMContentLoaded", function () {
  var bars = document.querySelectorAll("[data-fill-width]");
  bars.forEach(function (bar) {
    var width = parseFloat(bar.getAttribute("data-fill-width"));
    if (isNaN(width)) width = 0;
    var fill = bar.querySelector("div");
    if (fill) {
      fill.style.width = width + "%";
    }
  });

  // Fade out welcome / flash messages after 3 seconds.
  var flashMessages = document.querySelectorAll(".flash-message");
  flashMessages.forEach(function (msg) {
    setTimeout(function () {
      msg.classList.add("fading");
      // Remove from layout after the fade completes so it fully disappears.
      setTimeout(function () {
        msg.style.display = "none";
      }, 800);
    }, 3000);
  });

  // Wire up the county checkbox dropdown, then load the risk trend chart.
  setupCountyFilter();
  loadRiskTrend();
});

// Fetch and render the combined per-county risk trend line chart.
function loadRiskTrend() {
  var statusEl = document.getElementById("trend-refresh-status");
  if (statusEl) statusEl.textContent = "Refreshing...";

  fetch("/api/v1/admin/risk-trend")
    .then(function (res) {
      if (!res.ok) throw new Error("Could not load risk trend.");
      return res.json();
    })
    .then(function (data) {
      if (data.status !== "ok") throw new Error(data.message);
      renderCountyTrend(data);
      if (statusEl) statusEl.textContent = "Updated " + data.last_sync;
    })
    .catch(function (err) {
      console.error(err);
      if (statusEl) statusEl.textContent = "Refresh failed. Try again.";
    });
}

// Compute the Y-axis bounds. The range is re-centered on the visible
// risk data so the lines stay near the vertical center of the plot: a
// symmetric padded window is built around the midpoint of the visible
// values and clamped to the 0..1 risk domain. As risk rises or falls
// (or counties are toggled), the window grows/shrinks automatically.
function computeTrendAxis() {
  var vals = [];
  countyTrendState.series.forEach(function (s) {
    if (countyTrendState.visible[s.county] === false) return;
    (s.points || []).forEach(function (v) {
      if (typeof v === "number") vals.push(v);
    });
  });

  var min = 0;
  var max = 1;
  if (vals.length) {
    var dataMin = Math.min.apply(null, vals);
    var dataMax = Math.max.apply(null, vals);
    var center = (dataMin + dataMax) / 2;
    var span = dataMax - dataMin;
    // Keep a minimum spread so near-flat lines are not over-magnified.
    var pad = Math.max(span * 0.6, 0.06);
    min = center - pad;
    max = center + pad;
    // Clamp back into the 0..1 risk domain, shifting the window so the
    // data stays centered whenever possible.
    if (min < 0) {
      max += 0 - min;
      min = 0;
    }
    if (max > 1) {
      min -= max - 1;
      max = 1;
    }
    if (min < 0) min = 0;
  }
  return {
    min: Math.round(min * 1000) / 1000,
    max: Math.round(max * 1000) / 1000,
  };
}

// Build the datasets for the single combined line chart.
function buildTrendDatasets() {
  return countyTrendState.series.map(function (s) {
    var color = countyTrendState.colors[s.county];
    return {
      label: s.county,
      county: s.county,
      data: s.points || [],
      borderColor: color,
      backgroundColor: color + "22",
      borderWidth: 2,
      pointRadius: 2,
      pointHoverRadius: 5,
      tension: 0.4,
      fill: false,
    };
  });
}

function buildCombinedChart() {
  var canvas = document.getElementById("countyTrendChart");
  if (!canvas) return;

  var axis = computeTrendAxis();

  countyTrendState.chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: countyTrendState.labels,
      datasets: buildTrendDatasets(),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 1000, easing: "easeOutQuart" },
      // Hover targets a single line; the tooltip still shows all counties.
      interaction: { mode: "nearest", intersect: true },
      onHover: function (event, activeElements, chart) {
        if (!activeElements.length) {
          clearHoverDim();
          return;
        }
        var county = chart.data.datasets[activeElements[0].datasetIndex].county;
        applyHoverDim(county);
      },
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            color: "#94a3b8",
            boxWidth: 12,
            padding: 12,
            font: { size: 11 },
            usePointStyle: true,
            pointStyle: "line",
          },
          onClick: legendClickHandler,
          onHover: function (event, legendItem, legend) {
            if (!legendItem) return;
            var county =
              legend.chart.data.datasets[legendItem.datasetIndex].county;
            applyHoverDim(county);
          },
          onLeave: function () {
            clearHoverDim();
          },
        },
        tooltip: {
          interaction: { mode: "index", intersect: false },
          callbacks: {
            title: function (items) {
              return items.length
                ? countyTrendState.labels[items[0].dataIndex]
                : "";
            },
            label: function (ctx) {
              return (
                " " +
                ctx.dataset.label +
                ": " +
                (ctx.parsed.y * 100).toFixed(1) +
                "%"
              );
            },
          },
        },
      },
      scales: {
        y: {
          min: axis.min,
          max: axis.max,
          grid: { color: "#1e293b" },
          border: { display: false },
          ticks: {
            color: "#94a3b8",
            maxTicksLimit: 6,
            callback: function (v) {
              return v.toFixed(2);
            },
          },
        },
        x: {
          grid: { color: "#1e293b" },
          border: { display: false },
          ticks: { color: "#94a3b8" },
        },
      },
    },
  });
}

function legendClickHandler(event, legendItem, legend) {
  var chart = legend.chart;
  var county = chart.data.datasets[legendItem.datasetIndex].county;
  var native = event.native;
  if (native && (native.shiftKey || native.metaKey || native.ctrlKey)) {
    soloCounty(county);
  } else {
    toggleCounty(county);
  }
}

// Populate the county checkbox dropdown.
function buildCountyFilter() {
  var list = document.getElementById("countyCheckboxList");
  if (!list) return;
  list.innerHTML = "";

  countyTrendState.series.forEach(function (s) {
    var label = document.createElement("label");
    label.className = "trend-filter-option";
    label.setAttribute("data-county", s.county);
    label.title = "Toggle " + s.county + " on the chart";

    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = countyTrendState.visible[s.county] !== false;
    cb.addEventListener("change", function () {
      countyTrendState.visible[s.county] = cb.checked;
      clearHoverDim();
      applyTrendView();
    });

    var dot = document.createElement("span");
    dot.className = "dot";
    dot.style.backgroundColor = countyTrendState.colors[s.county];

    label.appendChild(cb);
    label.appendChild(dot);
    label.appendChild(document.createTextNode(s.county));
    list.appendChild(label);
  });

  reflectCountyChecks();
}

// Wire up the dropdown button, outside-click close, and All/None actions.
function setupCountyFilter() {
  var wrap = document.getElementById("countyFilter");
  var btn = document.getElementById("btnCountyFilter");
  if (!wrap || !btn) return;

  btn.addEventListener("click", function (e) {
    e.stopPropagation();
    var open = wrap.classList.toggle("open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  });

  document.addEventListener("click", function (e) {
    if (wrap.classList.contains("open") && !wrap.contains(e.target)) {
      wrap.classList.remove("open");
      btn.setAttribute("aria-expanded", "false");
    }
  });

  document.querySelectorAll("[data-filter-action]").forEach(function (b) {
    b.addEventListener("click", function () {
      var show = b.getAttribute("data-filter-action") === "all";
      countyTrendState.series.forEach(function (s) {
        countyTrendState.visible[s.county] = show;
      });
      clearHoverDim();
      applyTrendView();
      reflectCountyChecks();
    });
  });
}

// Sync the checkbox rows + dropdown label with the visibility state.
function reflectCountyChecks() {
  var list = document.getElementById("countyCheckboxList");
  if (list) {
    list.querySelectorAll(".trend-filter-option").forEach(function (label) {
      var county = label.getAttribute("data-county");
      var cb = label.querySelector("input[type=checkbox]");
      var vis = countyTrendState.visible[county] !== false;
      if (cb) cb.checked = vis;
      label.classList.toggle("off", !vis);
    });
  }
  updateCountyFilterLabel();
}

function updateCountyFilterLabel() {
  var labelEl = document.getElementById("countyFilterLabel");
  if (!labelEl) return;
  var total = countyTrendState.series.length;
  var shown = countyTrendState.series.filter(function (s) {
    return countyTrendState.visible[s.county] !== false;
  }).length;
  labelEl.textContent =
    shown === 0
      ? "No counties"
      : shown === total
        ? "All " + total + " counties"
        : shown + "/" + total + " counties shown";
}

// Update the single chart in place (data, zoomed axis, visibility).
function applyTrendView() {
  var chart = countyTrendState.chart;
  if (!chart) return;

  var axis = computeTrendAxis();
  chart.options.scales.y.min = axis.min;
  chart.options.scales.y.max = axis.max;

  chart.data.datasets.forEach(function (ds) {
    var hidden = countyTrendState.visible[ds.county] === false;
    ds.hidden = hidden;
    if (!hidden) {
      ds.borderColor = countyTrendState.colors[ds.county];
      ds.borderWidth = 2;
      ds.backgroundColor = countyTrendState.colors[ds.county] + "22";
    }
  });
  chart.update();

  reflectCountyChecks();
}

function toggleCounty(county) {
  countyTrendState.visible[county] = !countyTrendState.visible[county];
  applyTrendView();
}

function soloCounty(county) {
  countyTrendState.series.forEach(function (s) {
    countyTrendState.visible[s.county] = s.county === county;
  });
  applyTrendView();
}

// Hover focus: brighten one line and dim the rest to ~18% opacity.
function applyHoverDim(county) {
  var chart = countyTrendState.chart;
  if (!chart) return;

  chart.data.datasets.forEach(function (ds) {
    if (countyTrendState.visible[ds.county] === false) return;
    if (ds.county === county) {
      ds.borderColor = countyTrendState.colors[ds.county];
      ds.borderWidth = 3.5;
      ds.backgroundColor = countyTrendState.colors[ds.county] + "33";
    } else {
      ds.borderColor = countyTrendState.colors[ds.county] + "2E";
      ds.borderWidth = 1.5;
      ds.backgroundColor = countyTrendState.colors[ds.county] + "0D";
    }
  });
  chart.update("none");

  var options = document.querySelectorAll(
    "#countyCheckboxList .trend-filter-option",
  );
  options.forEach(function (option) {
    option.classList.toggle(
      "focus",
      option.getAttribute("data-county") === county,
    );
  });
}

function clearHoverDim() {
  var chart = countyTrendState.chart;
  if (!chart) return;

  chart.data.datasets.forEach(function (ds) {
    if (countyTrendState.visible[ds.county] === false) return;
    ds.borderColor = countyTrendState.colors[ds.county];
    ds.borderWidth = 2;
    ds.backgroundColor = countyTrendState.colors[ds.county] + "22";
  });
  chart.update("none");

  var options = document.querySelectorAll(
    "#countyCheckboxList .trend-filter-option",
  );
  options.forEach(function (option) {
    option.classList.remove("focus");
  });
}

// Render (or refresh) the combined per-county risk trend chart.
function renderCountyTrend(data) {
  countyTrendState.labels = data.labels || [];
  countyTrendState.series = data.series || [];

  countyTrendState.series.forEach(function (s, i) {
    countyTrendState.colors[s.county] =
      countyTrendPalette[i % countyTrendPalette.length];
    if (countyTrendState.visible[s.county] === undefined) {
      countyTrendState.visible[s.county] = true;
    }
  });

  buildCountyFilter();

  if (!countyTrendState.chart) {
    buildCombinedChart();
  } else {
    countyTrendState.chart.data.labels = countyTrendState.labels;
    countyTrendState.chart.data.datasets = buildTrendDatasets();
  }
  applyTrendView();
}
