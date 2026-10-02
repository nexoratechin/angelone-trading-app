"use strict";

const form = document.getElementById("login-form");
const msg = document.getElementById("login-msg");
const btn = document.getElementById("login-btn");

document.getElementById("pw-toggle").onclick = () => {
  const pw = document.getElementById("password");
  pw.type = pw.type === "password" ? "text" : "password";
  pw.focus();
};

// Only offer the register link when sign-ups are actually open.
fetch("/api/registration-status")
  .then((r) => r.json())
  .then((s) => {
    if (s.open) document.getElementById("register-alt").style.display = "";
  })
  .catch(() => {});

function safeNext() {
  const next = new URLSearchParams(location.search).get("next") || "/";
  // only allow same-site absolute paths
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  msg.textContent = "";
  btn.disabled = true;
  btn.textContent = "Signing in…";
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("username").value.trim(),
        password: document.getElementById("password").value,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || "Sign in failed");
    location.href = safeNext();
  } catch (err) {
    msg.textContent = err.message || "Sign in failed";
    btn.disabled = false;
    btn.textContent = "Sign in";
    document.getElementById("password").select();
  }
});
