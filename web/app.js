/* Infra Lang Web Playground — Monaco + Pyodide (v0.9.0).
 * Everything below runs client-side only; the compiler executes in
 * WebAssembly inside the browser tab. */
"use strict";

const PYODIDE_CDN = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
const MONACO_CDN = "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min";
const WHEEL_NAME = "infra_lang-0.9.0-py3-none-any.whl";

const statusEl = document.getElementById("engine-status");
const errorPanel = document.getElementById("error-panel");
const errorList = document.getElementById("error-list");
const exampleSelect = document.getElementById("example-select");
const dashFrame = document.getElementById("dash-frame");

/* Architecture templates (v0.8.0). Convention: sources are backtick
 * literals without backticks or ${ inside — tests extract them with a
 * regex and check they still parse + validate. New entries keep this. */
const TEMPLATES = [
  {
    id: "01_web_app",
    label: "01_web_app — Nginx + Redis + Postgres",
    source: `# 01_web_app — a classic web app: Nginx fronted service backed by
# a Redis cache and a PostgreSQL database.
secret db-creds {
    password: from env "DB_PASSWORD"
}

database main-db {
    type: postgres
}

cache session {
    type: redis
    maxmemory: 256Mi
}

service web {
    image: "nginx:1.25"
    port 80
    replicas: 2
    env {
      DB_PASS: from secret "db-creds".password
    }
    depends: [main-db, session]
    health http("/")
}
`,
  },
  {
    id: "02_microservices",
    label: "02_microservices — API + Worker + RabbitMQ + Auth",
    source: `# 02_microservices — three cooperating services exchanging work
# through a RabbitMQ queue, with a dedicated auth service.
queue events {
    type: rabbitmq
}

service auth {
    image: "myapp/auth:1.0"
    port 3001
    replicas: 2
    health http("/health")
}

service api {
    image: "myapp/api:1.0"
    port 8080
    env {
      AUTH_URL: "http://auth:3001"
    }
    depends_on: [auth, events]
    health http("/health")
}

service worker {
    image: "myapp/worker:1.0"
    env {
      QUEUE_URL: "amqp://events:5672"
    }
    depends_on: [events]
    health http("/health")
}
`,
  },
  {
    id: "03_cloud_native",
    label: "03_cloud_native — Autoscaling + Ingress + NetworkPolicy + SecretStore",
    source: `# 03_cloud_native — hardened production profile: autoscaling,
# TLS ingress, traffic whitelisting and an external secret store.
secret_store "vault-prod" {
    provider: "vault"
    address: "https://vault.internal:8200"
}

network_policy "api-ingress" {
    target: api
    allow_ingress: [frontend]
}

service frontend {
    image: "myapp/frontend:3.0"
    port 80
    health http("/")
}

service api {
    image: "myapp/api:3.0"
    port 8080
    ingress {
      host: "api.example.com"
      tls: true
    }
    autoscale {
      min: 2
      max: 12
      target_cpu: 75
    }
    health http("/health")
}
`,
  },
  {
    id: "04_scheduled_pipeline",
    label: "04_scheduled_pipeline — Cron Schedule + CI/CD Pipeline",
    source: `# 04_scheduled_pipeline — a nightly CI/CD pipeline plus a service
# whose replicas follow a cron schedule (business hours only).
pipeline nightly {
    trigger {
      schedule: "0 2 * * *"
      branches: ["main"]
    }
    stages {
      test: { runsOn: "ubuntu-latest" steps { s: { run: "make test" } } }
      deploy: { needs: [test] runsOn: "ubuntu-latest" steps { d: { run: "make deploy" } } }
    }
}

service report {
    image: "myapp/report:1.2"
    schedule {
      default: replicas 0
      "0 8 * * 1-5": replicas 1
    }
    health http("/health")
}
`,
  },
];

let pyodide = null;
let webApi = null;
let editor = null;
let dashBlobUrl = null;
let activeTab = "compose";
let compileTimer = null;

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "");
}

/* ---------- Monaco ---------- */

function setupMonaco() {
  // Cross-origin worker bootstrap required for the CDN build.
  window.MonacoEnvironment = {
    getWorkerUrl: function () {
      return (
        "data:text/javascript;charset=utf-8," +
        encodeURIComponent(
          "self.MonacoEnvironment={baseUrl:'" + MONACO_CDN + "/'};" +
            "importScripts('" + MONACO_CDN + "/vs/base/worker/workerMain.js');"
        )
      );
    },
  };
  require.config({ paths: { vs: MONACO_CDN + "/vs" } });
  require(["vs/editor/editor.main"], function () {
    registerInfraLanguage();
    editor = monaco.editor.create(document.getElementById("editor"), {
      value: initialCode(),
      language: "infra",
      theme: "infra-dark",
      automaticLayout: true,
      minimap: { enabled: false },
      fontSize: 13,
      tabSize: 2,
      scrollBeyondLastLine: false,
    });
    editor.onDidChangeModelContent(function () {
      clearTimeout(compileTimer);
      compileTimer = setTimeout(runActiveTab, 500);
    });
    fillTemplateSelect(); // templates need no compiler — load instantly
  });
}

function registerInfraLanguage() {
  monaco.languages.register({ id: "infra" });
  monaco.editor.defineTheme("infra-dark", {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "keyword.infra", foreground: "38bdf8", fontStyle: "bold" },
      { token: "type.infra", foreground: "818cf8" },
      { token: "number.infra", foreground: "fbbf24" },
      { token: "string.infra", foreground: "34d399" },
      { token: "comment.infra", foreground: "64748b", fontStyle: "italic" },
    ],
    colors: { "editor.background": "#0f172a" },
  });
  monaco.languages.setMonarchTokensProvider("infra", {
    defaultToken: "",
    keywords: [
      "service", "database", "cache", "queue", "secret", "secret_store",
      "network", "network_policy", "environment", "ingress", "env", "env_from",
      "import", "as", "extends", "if", "else", "for", "in", "const",
      "depends", "depends_on", "resources", "requests", "limits", "health",
      "port", "ports", "expose", "image", "replicas", "type", "version",
      "storage", "size", "labels", "annotations", "from", "build",
      "dockerfile", "context", "schedule", "backup", "volumes", "strategy",
    ],
    sourcekinds: ["secret", "configmap", "field", "env"],
    tokenizer: {
      root: [
        [/#.*$/, "comment.infra"],
        [/"/, { token: "string.infra", next: "@string" }],
        [/\d+(\.\d+)?(m|Mi|Gi|Ki|Ti|cores|s|min|h)?\b/, "number.infra"],
        [
          /@?[a-zA-Z_][\w-]*/,
          {
            cases: {
              "@keywords": "keyword.infra",
              "@sourcekinds": "type.infra",
              "@default": "identifier",
            },
          },
        ],
        [/->/, "operator"],
        [/[:{}[\],]/, "delimiter"],
      ],
      string: [
        [/[^\\"]+/, "string.infra"],
        [/\\./, "string.escape"],
        [/"/, { token: "string.infra", next: "@pop" }],
      ],
    },
  });
  monaco.languages.setLanguageConfiguration("infra", {
    comments: { lineComment: "#" },
    brackets: [["{", "}"], ["[", "]"]],
    autoClosingPairs: [
      { open: "{", close: "}" },
      { open: "[", close: "]" },
      { open: '"', close: '"' },
    ],
  });
}

/* ---------- shared code state (URL) ---------- */

function encodeShare(code) {
  return btoa(unescape(encodeURIComponent(code)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}
function decodeShare(b64) {
  var s = b64.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return decodeURIComponent(escape(atob(s)));
}

function initialCode() {
  var params = new URLSearchParams(window.location.search);
  var shared = params.get("code");
  if (shared) {
    try {
      return decodeShare(shared);
    } catch (e) {
      /* fall through to the default example */
    }
  }
  return (
    '# Welcome to the Infra Lang playground!\n' +
    '# Pick an example above — the Python compiler is loading…\n\n' +
    'service hello {\n' +
    '    image: "nginx:1.25.3"\n' +
    "    port 80\n" +
    '    health http("/")\n' +
    "}\n"
  );
}

/* ---------- Pyodide ---------- */

async function setupPyodide() {
  try {
    setStatus("Loading Pyodide…");
    pyodide = await loadPyodide({ indexURL: PYODIDE_CDN });
    setStatus("Installing compiler…");
    await pyodide.loadPackage("micropip");
    const micropip = pyodide.pyimport("micropip");
    // Pure-Python runtime deps of the compiler chain used by infra.web_api.
    await micropip.install(["lark", "ruamel.yaml", "pyyaml"]);
    const params = new URLSearchParams(window.location.search);
    const wheel = params.get("wheel") || ("./" + WHEEL_NAME);
    try {
      // Prefer the wheel shipped next to the page (works for GitHub Pages).
      await micropip.install(wheel, { deps: false });
    } catch (localErr) {
      await micropip.install("infra-lang"); // released package from PyPI
    }
    webApi = pyodide.pyimport("infra.web_api");
    fillExamples();
    setStatus("Compiler ready (WASM)", "ready");
    runActiveTab();
  } catch (err) {
    console.error(err);
    setStatus("Compiler failed to load — " + err, "error");
    showErrors(["Pyodide / wheel bootstrap failed: " + err]);
  }
}

/* ---------- template / example selector ---------- */

// Templates are local JS — the selector works even while the compiler
// is still booting. Engine examples are appended once Pyodide is ready.
function fillTemplateSelect() {
  const group = document.createElement("optgroup");
  group.label = "Architecture templates";
  for (const tpl of TEMPLATES) {
    const opt = document.createElement("option");
    opt.value = "tpl:" + tpl.id;
    opt.textContent = tpl.label;
    group.appendChild(opt);
  }
  exampleSelect.innerHTML = "";
  exampleSelect.appendChild(group);
  exampleSelect.onchange = function () {
    const value = this.value;
    if (value.startsWith("tpl:")) {
      const tpl = TEMPLATES.find(function (t) { return "tpl:" + t.id === value; });
      if (tpl && editor) {
        editor.setValue(tpl.source);
        runActiveTab();
      }
      return;
    }
    if (value.startsWith("ex:")) {
      const source = exampleSelect._engineSources || {};
      if (source[value] && editor) {
        editor.setValue(source[value]);
        runActiveTab();
      }
    }
  };
  // First template replaces the placeholder when no ?code= was given.
  if (!new URLSearchParams(window.location.search).get("code") && editor) {
    editor.setValue(TEMPLATES[0].source);
  }
}

function fillExamples() {
  const examples = webApi.list_examples().toJs({ dict_converter: Object.fromEntries });
  const sources = {};
  const group = document.createElement("optgroup");
  group.label = "Engine examples (from compiler)";
  for (const name of Object.keys(examples)) {
    const opt = document.createElement("option");
    opt.value = "ex:" + name;
    opt.textContent = name;
    group.appendChild(opt);
    sources["ex:" + name] = examples[name];
  }
  exampleSelect._engineSources = sources;
  exampleSelect.appendChild(group);
}

/* ---------- compile all backends -> ZIP bundle ---------- */

const BUNDLE_TARGETS = ["compose", "kubernetes", "terraform", "helm"];

async function downloadAllManifests() {
  if (!webApi || !editor) {
    showErrors(["The compiler is still loading — try again in a moment."]);
    return;
  }
  if (typeof JSZip === "undefined") {
    showErrors(["JSZip failed to load from the CDN — cannot build the bundle."]);
    return;
  }
  const source = editor.getValue();
  clearErrors();

  const zip = new JSZip();
  const failures = [];
  for (const target of BUNDLE_TARGETS) {
    const result = toJs(webApi.compile_to_target(source, target));
    if (result.success) {
      const folder = zip.folder(target);
      for (const name of Object.keys(result.files)) {
        folder.file(name, result.files[name]);
      }
    } else {
      (result.errors || ["unknown error"]).forEach(function (m) {
        failures.push(target + ": " + m);
      });
    }
  }
  if (failures.length) {
    showErrors(failures);
    return;
  }

  const blob = await zip.generateAsync({ type: "blob" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "infra-manifests.zip";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  setTimeout(function () { URL.revokeObjectURL(url); }, 5000);
}

/* ---------- compile / render ---------- */

function toJs(value) {
  return value.toJs ? value.toJs({ dict_converter: Object.fromEntries }) : value;
}

function showErrors(messages) {
  errorList.innerHTML = "";
  messages.forEach(function (m) {
    const li = document.createElement("li");
    li.textContent = m;
    errorList.appendChild(li);
  });
  errorPanel.classList.remove("hidden");
}
function clearErrors() {
  errorPanel.classList.add("hidden");
  errorList.innerHTML = "";
}

function setCodeOutput(id, text) {
  document.querySelector("#out-" + id + " code").textContent = text;
}

function runActiveTab() {
  if (!webApi || !editor) return;
  const source = editor.getValue();
  clearErrors();
  if (activeTab === "dashboard") return renderDashboard(source);
  if (activeTab === "dag") return renderDag(source);
  if (activeTab === "insight") return renderInsight(source);
  return renderCompile(source, activeTab);
}

function renderInsight(source) {
  try {
    const result = toJs(webApi.generate_explain_report(source, "markdown"));
    if (result.success) {
      setCodeOutput("insight", result.report);
    } else {
      setCodeOutput("insight", "# insight report failed — see below");
      showErrors(result.errors || ["Unknown insight error."]);
    }
  } catch (err) {
    showErrors([pythonMessage(err)]);
  }
}

function renderCompile(source, target) {
  try {
    const result = toJs(webApi.compile_to_target(source, target));
    if (result.success) {
      const parts = Object.keys(result.files).map(function (name) {
        return "# ── " + name + " ──\n" + result.files[name];
      });
      setCodeOutput(target, parts.join("\n"));
    } else {
      setCodeOutput(target, "# compilation failed — see below");
      showErrors(result.errors || ["Unknown compilation error."]);
    }
  } catch (err) {
    showErrors([String(err)]);
  }
}

function renderDag(source) {
  const host = document.getElementById("out-dag");
  try {
    host.innerHTML = String(webApi.export_dag_svg(source));
  } catch (err) {
    host.textContent = "DAG render failed — see below";
    showErrors([pythonMessage(err)]);
  }
}

function renderDashboard(source) {
  try {
    const html = String(webApi.generate_ui_report(source));
    if (dashBlobUrl) URL.revokeObjectURL(dashBlobUrl);
    dashBlobUrl = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    dashFrame.src = dashBlobUrl;
  } catch (err) {
    showErrors([pythonMessage(err)]);
  }
}

function pythonMessage(err) {
  // Pyodide wraps Python tracebacks; surface the last meaningful line.
  const msg = String(err && err.message ? err.message : err);
  const lines = msg.trim().split("\n");
  return lines[lines.length - 1];
}

/* ---------- tabs ---------- */

document.querySelectorAll(".tab").forEach(function (btn) {
  btn.addEventListener("click", function () {
    document.querySelectorAll(".tab").forEach(function (b) {
      b.classList.toggle("active", b === btn);
      b.setAttribute("aria-selected", b === btn ? "true" : "false");
    });
    document.querySelectorAll(".output").forEach(function (o) {
      o.classList.remove("active");
    });
    activeTab = btn.dataset.tab;
    document.getElementById("out-" + activeTab).classList.add("active");
    runActiveTab();
  });
});

/* ---------- share ---------- */

document.getElementById("share-btn").addEventListener("click", function () {
  if (!editor) return;
  const url = new URL(window.location.href);
  url.searchParams.set("code", encodeShare(editor.getValue()));
  url.searchParams.delete("wheel");
  history.replaceState(null, "", url);
  const btn = this;
  navigator.clipboard.writeText(url.toString()).then(
    function () {
      btn.textContent = "Copied!";
      setTimeout(function () { btn.textContent = "Share"; }, 1500);
    },
    function () {
      window.prompt("Share URL:", url.toString());
    }
  );
});

/* ---------- bundle download ---------- */

document.getElementById("bundle-btn").addEventListener("click", function () {
  downloadAllManifests();
});

/* ---------- boot ---------- */

setupMonaco();
setupPyodide();
