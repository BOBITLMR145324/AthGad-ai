/* Toggle password visibility for inputs wrapped in a .password-wrapper
   element that also contains a .js-toggle-password button. */
document.addEventListener("click", function (event) {
  var button = event.target.closest(".js-toggle-password");
  if (!button) return;

  var wrapper = button.closest(".password-wrapper");
  if (!wrapper) return;

  var input = wrapper.querySelector('input[name]');
  if (!input) return;

  var showPassword = input.type === "password";
  input.type = showPassword ? "text" : "password";

  var openIcon = button.querySelector(".eye-open");
  var closedIcon = button.querySelector(".eye-closed");
  if (openIcon) openIcon.classList.toggle("hidden", showPassword);
  if (closedIcon) closedIcon.classList.toggle("hidden", !showPassword);
  button.setAttribute(
    "aria-label",
    showPassword ? "Hide password" : "Show password"
  );
});
