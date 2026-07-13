(() => {
  "use strict";

  const params = new URLSearchParams(window.location.search);
  const next = params.get("next") || "/";
  const form = document.querySelector("#auth-form");
  const error = document.querySelector("#auth-error");
  form.action = `/auth?next=${encodeURIComponent(next)}`;

  if (params.get("error") === "invalid") {
    error.textContent = "The PIN is incorrect.";
  } else if (params.get("error") === "rate") {
    error.textContent = "Too many attempts. Try again in one minute.";
  }
})();
