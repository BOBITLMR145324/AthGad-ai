      document.addEventListener("DOMContentLoaded", function () {
        // Fade out flash messages linearly within 3 seconds, then remove them.
        var flashMessages = document.querySelectorAll(".flash-message");
        flashMessages.forEach(function (msg) {
          setTimeout(function () {
            msg.classList.add("fading");
            setTimeout(function () {
              msg.style.display = "none";
            }, 3000);
          }, 500);
        });

        // Prompt to validate the existing password before changing it.
        var passwordForm = document.getElementById("password-form");
        if (passwordForm) {
          passwordForm.addEventListener("submit", function (event) {
            var currentPassword = passwordForm.querySelector(
              'input[name="current_password"]'
            ).value;
            var newPassword = passwordForm.querySelector(
              'input[name="new_password"]'
            ).value;
            var confirmPassword = passwordForm.querySelector(
              'input[name="confirm_password"]'
            ).value;

            if (!currentPassword && !newPassword && !confirmPassword) {
              event.preventDefault();
              alert(
                "Please fill in all the fields to update the password."
              );
              passwordForm
                .querySelector('input[name="current_password"]')
                .focus();
              return;
            }

            if (newPassword || confirmPassword) {
              if (!currentPassword) {
                event.preventDefault();
                alert(
                  "Please enter your current password to validate before changing your password."
                );
                passwordForm
                  .querySelector('input[name="current_password"]')
                  .focus();
                return;
              }
              if (newPassword.length < 8) {
                event.preventDefault();
                alert("Your new password must be at least 8 characters long.");
                passwordForm
                  .querySelector('input[name="new_password"]')
                  .focus();
                return;
              }
              if (newPassword !== confirmPassword) {
                event.preventDefault();
                alert("The new passwords you entered do not match.");
                passwordForm
                  .querySelector('input[name="confirm_password"]')
                  .focus();
                return;
              }
            } else if (currentPassword) {
              event.preventDefault();
              alert("Please enter and confirm your new password first.");
              passwordForm
                .querySelector('input[name="new_password"]')
                .focus();
            }
          });
        }

        // Danger zone: open/close the account-deletion confirmation modal.
        var deleteBtn = document.getElementById("delete-account-btn");
        var deleteModal = document.getElementById("delete-modal");
        var cancelDeleteBtn = document.getElementById("cancel-delete-btn");
        var deleteForm = document.getElementById("delete-account-form");
        var deletePassword = document.getElementById("delete-password");

        if (deleteBtn && deleteModal) {
          deleteBtn.addEventListener("click", function () {
            deleteModal.style.display = "flex";
            if (deletePassword) deletePassword.focus();
          });
        }
        if (cancelDeleteBtn && deleteModal) {
          cancelDeleteBtn.addEventListener("click", function () {
            deleteModal.style.display = "none";
          });
        }
        if (deleteModal) {
          deleteModal.addEventListener("click", function (event) {
            if (event.target === deleteModal) {
              deleteModal.style.display = "none";
            }
          });
        }
        if (deleteForm) {
          deleteForm.addEventListener("submit", function (event) {
            if (!deletePassword || !deletePassword.value) {
              event.preventDefault();
              alert(
                "Please enter your password to confirm account deletion."
              );
              deletePassword.focus();
              return;
            }
            var confirmed = confirm(
              "This will permanently delete your account and all your data. " +
                "This cannot be undone. Continue?"
            );
            if (!confirmed) {
              event.preventDefault();
            }
          });
        }
      });
    