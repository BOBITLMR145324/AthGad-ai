      // Show the sorrowful farewell popup after account deletion, then fade it
      // out 3 seconds later.
      document.addEventListener("DOMContentLoaded", function () {
        var params = new URLSearchParams(window.location.search);
        if (!params.get("account_deleted")) return;

        var popup = document.getElementById("farewell-popup");
        popup.style.display = "flex";

        setTimeout(function () {
          popup.classList.add("fading");
          setTimeout(function () {
            popup.style.display = "none";
          }, 1000);
        }, 3000);
      });
    

      // Populate the Live Risk Signals card with processed telemetry data.
      document.addEventListener("DOMContentLoaded", function () {
        fetch("/api/v1/live-summary")
          .then(function (res) {
            return res.json();
          })
          .then(function (data) {
            if (data.status !== "ok") return;

            var drought = document.getElementById("risk-drought");
            var disease = document.getElementById("risk-disease");
            var pattern = document.getElementById("risk-pattern");
            var status = document.getElementById("risk-status");

            if (drought) drought.textContent = data.avg_climate + "%";
            if (disease) disease.textContent = data.avg_health + "%";
            if (pattern) {
              pattern.textContent = data.hidden_pattern;
              pattern.className = data.hidden_pattern_color;
            }
            if (status) status.textContent = "● " + data.system_status;
          })
          .catch(function () {
            // Leave the default "Connected" status if the API is unavailable.
          });
      });
    