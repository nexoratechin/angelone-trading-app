"use strict";

const form = document.getElementById("register-form");
const msg = document.getElementById("register-msg");
const notice = document.getElementById("register-notice");
const btn = document.getElementById("register-btn");
const codeLabel = document.getElementById("code-label");
const codeInput = document.getElementById("code");

document.getElementById("pw-toggle").onclick = () => {
  const pw = document.getElementById("password");
  pw.type = pw.type === "password" ? "text" : "password";
  pw.focus();
};

// Ask the server whether registration is open and what it requires.
fetch("/api/registration-status")
  .then((r) => r.json())
  .then((s) => {
    if (!s.open) {
      notice.textContent = "Registration is closed. Ask an administrator for an account.";
      notice.className = "notice warn";
      form.querySelectorAll("label, .login-btn").forEach((el) => (el.style.display = "none"));
      return;
    }
    if (s.first_run) {
      notice.textContent = "You're the first account — it will be created as an administrator.";
      notice.className = "notice ok";
    } else if (s.requires_code) {
      notice.textContent = "An invite code is required to sign up.";
      notice.className = "notice";
      codeLabel.style.display = "";
      codeInput.required = true;
    }
  })
  .catch(() => { /* leave the form as-is */ });

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  msg.textContent = "";
  const password = document.getElementById("password").value;
  const confirm = document.getElementById("confirm").value;
  if (password !== confirm) {
    msg.textContent = "Passwords do not match";
    return;
  }
  btn.disabled = true;
  btn.textContent = "Creating account…";
  try {
    const res = await fetch("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("username").value.trim(),
        password,
        confirm,
        code: codeInput ? codeInput.value : "",
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || "Registration failed");
    location.href = "/";
  } catch (err) {
    msg.textContent = err.message || "Registration failed";
    btn.disabled = false;
    btn.textContent = "Create account";
  }
});
