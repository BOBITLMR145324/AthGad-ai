// Realtime SMS / email dispatch debugging console.
var viewState = {
  current: "sms",
  statusFilter: "",
  dispatchPage: 1,
  dispatchPageSize: 10,
  dispatchLogs: [],
};

// Status badge styling for the SMS table.
function statusBadge(status) {
  var s = (status || "unknown").toUpperCase();
  var cls = "badge-unknown";
  if (s === "SUCCESS") cls = "badge-success";
  else if (s === "FAILED") cls = "badge-failed";
  else if (s === "SIMULATED") cls = "badge-simulated";
  return '<span class="badge ' + cls + '">' + s.toLowerCase() + "</span>";
}

function channelBadge(channel) {
  var c = (channel || "sms").toLowerCase();
  var cls = c === "email" ? "badge badge-success" : "badge badge-unknown";
  return '<span class="' + cls + '">' + c + "</span>";
}

function esc(v) {
  var s = String(v == null ? "" : v);
  // Build the HTML entities at runtime so no raw "&" literal that editors /
  // formatters could HTML-decode appears in the source file.
  var AMP = String.fromCharCode(38) + "amp;";
  var LT = String.fromCharCode(38) + "lt;";
  var GT = String.fromCharCode(38) + "gt;";
  var QUOT = String.fromCharCode(38) + "quot;";
  var APOS = String.fromCharCode(38) + "#39;";
  return s
    .replace(/&/g, AMP)
    .replace(/</g, LT)
    .replace(/>/g, GT)
    .replace(/"/g, QUOT)
    .replace(/'/g, APOS);
}

function switchView(name) {
  viewState.current = name;
  viewState.dispatchPage = 1;
  document
    .getElementById("view-sms")
    .classList.toggle("hidden", name !== "sms");
  document
    .getElementById("view-dispatch")
    .classList.toggle("hidden", name !== "dispatch");
  var smsTab = document.getElementById("tab-sms");
  var dispTab = document.getElementById("tab-dispatch");
  if (name === "sms") {
    smsTab.className =
      "cursor-pointer bg-emerald-600 text-white font-bold px-4 py-2 rounded-lg text-xs tracking-wider uppercase no-underline";
    dispTab.className =
      "cursor-pointer bg-slate-800 hover:bg-slate-700 text-slate-300 px-4 py-2 rounded-lg text-xs tracking-wider uppercase no-underline";
  } else {
    dispTab.className =
      "cursor-pointer bg-emerald-600 text-white font-bold px-4 py-2 rounded-lg text-xs tracking-wider uppercase no-underline";
    smsTab.className =
      "cursor-pointer bg-slate-800 hover:bg-slate-700 text-slate-300 px-4 py-2 rounded-lg text-xs tracking-wider uppercase no-underline";
  }
  loadData();
}

function setStatusFilter(btn) {
  viewState.statusFilter = btn.getAttribute("data-status") || "";
  viewState.dispatchPage = 1;
  var chips = document.querySelectorAll(".status-chip");
  chips.forEach(function (c) {
    c.classList.toggle(
      "status-chip-active",
      (c.getAttribute("data-status") || "") === viewState.statusFilter,
    );
  });
  loadData();
}

function setSummary(s) {
  var set = function (id, v) {
    var el = document.getElementById(id);
    if (el) el.textContent = v;
  };
  set("sum-total", s.total);
  set("sum-sms", s.sms);
  set("sum-email", s.email);
  set("sum-failed", s.failed);
  set("sum-simulated", s.simulated);
}

function renderSms(logs) {
  var body = document.getElementById("sms-table-body");
  if (!logs.length) {
    body.innerHTML =
      '<tr><td colspan="11" class="px-3 py-8 text-center text-slate-500">No SMS deliveries matched yet.</td></tr>';
    return;
  }
  body.innerHTML = logs
    .map(function (l) {
      return (
        "<tr class='border-b border-slate-800/60 hover:bg-slate-800/40'>" +
        "<td class='px-3 py-2.5 text-slate-400 truncate' title='" +
        esc(l.logged_at) +
        "'>" +
        esc(l.logged_at) +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-200 truncate' title='" +
        esc(l.phone_number) +
        "'>" +
        esc(l.phone_number) +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-300 truncate' title='" +
        esc(l.name) +
        "'>" +
        esc(l.name || "—") +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-400 truncate' title='" +
        esc(l.message_type) +
        "'>" +
        esc(l.message_type) +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-400 truncate' title='" +
        esc(l.tier) +
        "'>" +
        esc(l.tier) +
        "</td>" +
        "<td class='px-3 py-2.5'>" +
        statusBadge(l.status) +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-400 truncate' title='" +
        esc(l.at_status) +
        "'>" +
        esc(l.at_status || "—") +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-400 text-right truncate' title='" +
        esc(l.http_status || "—") +
        "'>" +
        esc(l.http_status || "—") +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-400 text-right truncate' title='" +
        esc(l.cost) +
        "'>" +
        esc(l.cost || "—") +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-400 truncate' title='" +
        esc(l.message_id) +
        "'>" +
        esc(l.message_id || "—") +
        "</td>" +
        "<td class='px-3 py-2.5 text-rose-300/80 whitespace-normal break-words leading-relaxed'>" +
        esc(l.error_detail || "—") +
        "</td>" +
        "</tr>"
      );
    })
    .join("");
}

function renderDispatch() {
  var body = document.getElementById("dispatch-table-body");
  var logs = viewState.dispatchLogs || [];
  var pageSize = viewState.dispatchPageSize;
  var pages = Math.max(1, Math.ceil(logs.length / pageSize));
  if (viewState.dispatchPage > pages) viewState.dispatchPage = pages;
  if (viewState.dispatchPage < 1) viewState.dispatchPage = 1;
  var start = (viewState.dispatchPage - 1) * pageSize;
  var pageLogs = logs.slice(start, start + pageSize);

  if (!logs.length) {
    body.innerHTML =
      '<tr><td colspan="7" class="px-3 py-8 text-center text-slate-500">No tracked dispatches matched yet.</td></tr>';
    renderDispatchPager(0);
    return;
  }
  body.innerHTML = pageLogs
    .map(function (l) {
      return (
        "<tr class='border-b border-slate-800/60 hover:bg-slate-800/40'>" +
        "<td class='px-3 py-2.5 text-slate-400 truncate' title='" +
        esc(l.dispatched_at) +
        "'>" +
        esc(l.dispatched_at) +
        "</td>" +
        "<td class='px-3 py-2.5'>" +
        channelBadge(l.channel) +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-200 truncate' title='" +
        esc(l.recipient) +
        "'>" +
        esc(l.recipient) +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-400 truncate' title='" +
        esc(l.message_type) +
        "'>" +
        esc(l.message_type) +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-400 truncate' title='" +
        esc(l.subscription_status) +
        "'>" +
        esc(l.subscription_status) +
        "</td>" +
        "<td class='px-3 py-2.5'>" +
        statusBadge(l.status) +
        "</td>" +
        "<td class='px-3 py-2.5 text-slate-300 whitespace-normal break-words leading-relaxed'>" +
        esc(l.message) +
        "</td>" +
        "</tr>"
      );
    })
    .join("");
  renderDispatchPager(logs.length);
}

// Build a compact page-number window around the current page, with
// ellipses for the gaps (e.g. 1 … 3 4 5 … 20).
function pagerWindow(pages, page) {
  var items = [];
  var start = Math.max(1, page - 2);
  var end = Math.min(pages, page + 2);
  if (start > 1) {
    items.push(1);
    if (start > 2) items.push("...");
  }
  for (var i = start; i <= end; i++) items.push(i);
  if (end < pages) {
    if (end < pages - 1) items.push("...");
    items.push(pages);
  }
  return items;
}

// Render the Prev / page numbers / Next bar. Page buttons grow
// dynamically as new dispatches stream in on each refresh.
function renderDispatchPager(total) {
  var bar = document.getElementById("dispatch-pager");
  if (!bar) return;
  var pageSize = viewState.dispatchPageSize;
  var pages = Math.max(1, Math.ceil(total / pageSize));
  var page = viewState.dispatchPage;
  var from = total ? (page - 1) * pageSize + 1 : 0;
  var to = Math.min(total, page * pageSize);

  var html =
    '<button type="button" class="pager-btn" data-page="' +
    (page - 1) +
    '"' +
    (page <= 1 ? " disabled" : "") +
    ">Prev</button>";

  pagerWindow(pages, page).forEach(function (it) {
    if (it === "...") {
      html += '<span class="pager-dots">…</span>';
    } else {
      html +=
        '<button type="button" class="pager-btn' +
        (it === page ? " pager-active" : "") +
        '" data-page="' +
        it +
        '">' +
        it +
        "</button>";
    }
  });

  html +=
    '<button type="button" class="pager-btn" data-page="' +
    (page + 1) +
    '"' +
    (page >= pages ? " disabled" : "") +
    ">Next</button>";

  html +=
    '<span class="pager-info">' +
    (total ? from + "–" + to + " of " + total + " logs" : "0 logs") +
    "</span>";

  bar.innerHTML = html;

  bar.querySelectorAll(".pager-btn").forEach(function (btn) {
    if (btn.disabled) return;
    btn.addEventListener("click", function () {
      var p = parseInt(btn.getAttribute("data-page"), 10);
      if (!isNaN(p)) {
        viewState.dispatchPage = p;
        renderDispatch();
      }
    });
  });
}

function loadData() {
  var stamp = document.getElementById("refresh-stamp");
  if (stamp) stamp.textContent = "Refreshing…";

  var smsUrl = "/api/v1/admin/sms-delivery?limit=200";
  var dispatchUrl =
    "/api/v1/admin/dispatch-logs?limit=500&status=" +
    encodeURIComponent(viewState.statusFilter || "ALL") +
    "&channel=all";

  Promise.all([
    fetch(smsUrl).then(function (r) {
      return r.json();
    }),
    fetch(dispatchUrl).then(function (r) {
      return r.json();
    }),
  ])
    .then(function (results) {
      var sms = results[0];
      var dispatch = results[1];
      if (sms.status === "ok") {
        renderSms(sms.logs);
        if (stamp) stamp.textContent = "Updated " + sms.last_sync;
      }
      if (dispatch.status === "ok") {
        setSummary(dispatch.summary);
        viewState.dispatchLogs = dispatch.logs;
        renderDispatch();
      }
    })
    .catch(function () {
      if (stamp) stamp.textContent = "Refresh failed — retrying…";
    });
}

document.addEventListener("DOMContentLoaded", function () {
  loadData();
  setInterval(loadData, 5000);
});
