      document.addEventListener("DOMContentLoaded", () => {
        const alerts = document.querySelectorAll(".flash-alert");

        if (alerts.length > 0) {
          setTimeout(() => {
            alerts.forEach((alert) => {
              // Skip auto-dismiss if notification requires user interaction (e.g. Account Match warning)

              if (alert.dataset.persistent === "true") return;

              // Fade out opacity

              alert.classList.replace("opacity-100", "opacity-0");

              // Remove element after fade completes

              setTimeout(() => {
                alert.remove();
              }, 500);
            });
          }, 2000); // 2000ms = 2 seconds
        }
      });

      document.addEventListener("DOMContentLoaded", function () {
        var flashMessages = document.querySelectorAll(".flash-message");
        flashMessages.forEach(function (msg) {
          setTimeout(function () {
            msg.classList.add("fading");
            setTimeout(function () {
              msg.style.display = "none";
            }, 3000);
          }, 500);
        });
      });
    