// Standalone login/register page. Stores JWT in localStorage, then redirects.
const API = "";
const params = new URLSearchParams(location.search);
let mode = "login";

window.addEventListener("error", (e) => {
  const err = document.getElementById("auth-error");
  if (err) err.textContent = "JS ERROR: " + (e.message || e.error);
});

const form = document.getElementById("auth-form");
const errBox = document.getElementById("auth-error");
const submitBtn = document.getElementById("auth-submit");

// Hide the Register tab once the first user has been created (self-registration closed).
(async () => {
  try {
    const r = await fetch(API + "/api/auth/registration-open");
    const data = await r.json();
    if (data && data.open === false) {
      const regTab = document.getElementById("tab-register");
      if (regTab) regTab.style.display = "none";
      const hint = document.getElementById("auth-hint");
      if (hint) hint.textContent = "Contact an admin to get an account.";
      // With only the Login tab left, the tab switcher is redundant — hide it
      // so it doesn't look like two identical "Login" buttons.
      const tabs = document.querySelector(".auth-tabs");
      if (tabs) tabs.style.display = "none";
    }
  } catch (_) { /* leave register visible if check fails */ }
})();

form.onsubmit = async (e) => {
  e.preventDefault();
  console.log("login submit, mode=", mode);
  errBox.textContent = "";
  const username = document.getElementById("a-username").value.trim();
  const password = document.getElementById("a-password").value;
  if (!username || !password) {
    errBox.textContent = "Username and password are required";
    return;
  }
  submitBtn.disabled = true;
  try {
    const endpoint = mode === "login" ? "/api/auth/login" : "/api/auth/register";
    let res;
    if (mode === "login") {
      res = await fetch(API + endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ username, password }),
      });
    } else {
      res = await fetch(API + endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
    }
    if (!res.ok) {
      let msg = "Authentication failed";
      try { msg = (await res.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    const data = await res.json();
    localStorage.setItem("shelldeck_token", data.access_token);
    console.log("token saved, redirecting");
    location.href = "/";
  } catch (err) {
    errBox.textContent = err.message;
  } finally {
    submitBtn.disabled = false;
  }
};

function setMode(m) {
  mode = m;
  document.getElementById("tab-login").classList.toggle("active", m === "login");
  document.getElementById("tab-register").classList.toggle("active", m === "register");
  submitBtn.textContent = m === "login" ? "Login" : "Register";
  document.getElementById("auth-hint").textContent = m === "register"
    ? "Create your account. The first user becomes admin."
    : "Don't have an account? Switch to Register.";
  document.getElementById("a-password").setAttribute(
    "autocomplete", m === "login" ? "current-password" : "new-password"
  );
}

document.getElementById("tab-login").onclick = () => setMode("login");
document.getElementById("tab-register").onclick = () => setMode("register");
if (params.get("register") === "1") {
  // Only allow deep-link to register if self-registration is still open.
  fetch(API + "/api/auth/registration-open").then(r => r.json()).then(d => {
    if (d && d.open === true) setMode("register");
    else setMode("login");
  }).catch(() => setMode("login"));
}
