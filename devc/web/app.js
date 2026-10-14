// devc-GUI: Single-Page-App mit Hash-Routen (SPEC 8.10). Nutzt ausschließlich die HTTP-API.
// Inhalte aus der API werden nur als Text eingesetzt (textContent), nie als HTML.
"use strict";

(function () {
  const main = document.getElementById("main");
  const state = { status: null, cleanup: [] };

  // --- DOM-Hilfen ---------------------------------------------------------

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value === true) node.setAttribute(key, "");
      else node.setAttribute(key, value);
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  const mono = (text) => h("span", { class: "mono" }, text);
  const btn = (label, onclick, kind) => h("button", { class: "btn" + (kind ? " btn-" + kind : ""), type: "button", onclick }, label);
  const link = (label, href, kind) => h("a", { class: "btn" + (kind ? " btn-" + kind : ""), href }, label);
  const enc = encodeURIComponent;

  function table(headers, rows, empty) {
    if (!rows.length) return h("p", { class: "empty" }, empty || "Keine Einträge.");
    return h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, headers.map((text) => h("th", { scope: "col" }, text)))),
      h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((cell) => h("td", {}, cell)))))));
  }

  function when(iso) {
    if (!iso) return "–";
    const date = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
    if (isNaN(date)) return String(iso);
    const today = new Date();
    const time = date.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    if (date.toDateString() === today.toDateString()) return "heute " + time;
    return date.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" }) + " " + time;
  }
  function stampDate(stamp) { // YYYYMMDD-HHMMSS (Ortszeit)
    const m = /^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)$/.exec(stamp || "");
    return m ? when(new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).toISOString()) : stamp;
  }
  function size(bytes) {
    if (bytes < 1024) return bytes + " B";
    const units = ["KiB", "MiB", "GiB", "TiB"];
    let value = bytes, index = -1;
    do { value /= 1024; index++; } while (value >= 1024 && index < units.length - 1);
    return value.toFixed(value < 10 ? 1 : 0).replace(".", ",") + " " + units[index];
  }
  const STATUS = { running: "läuft", stopped: "gestoppt", missing: "fehlt in LXD", foreign: "fremd" };
  function statusBadge(container) {
    const cls = container.status === "running" ? " running" : (container.status === "missing" ? " bad" : "");
    const text = (STATUS[container.status] || container.status) + (container.installing ? " · wird eingerichtet" : container.incomplete ? " · unvollständig" : "");
    return h("span", { class: "status" + cls }, text);
  }

  // --- API ----------------------------------------------------------------

  async function api(method, path, body, options) {
    const init = { method, headers: {}, credentials: "same-origin" };
    if (method !== "GET") init.headers["X-Devc"] = "1";
    if (body !== undefined && body !== null) {
      if (typeof body === "string" || body instanceof Blob || body instanceof ArrayBuffer) {
        init.body = body;
        init.headers["Content-Type"] = (options && options.type) || "text/plain; charset=utf-8";
      } else {
        init.body = JSON.stringify(body);
        init.headers["Content-Type"] = "application/json";
      }
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      throw { error: "E_NETWORK", message: "Server nicht erreichbar", details: {} };
    }
    const type = response.headers.get("Content-Type") || "";
    const data = response.status === 204 ? null : (type.startsWith("application/json") ? await response.json() : await response.text());
    if (!response.ok) {
      const failure = (data && data.error) ? data : { error: "E_INTERNAL", message: "Unerwartete Antwort (" + response.status + ")", details: {} };
      if (response.status === 401 && !(options && options.login)) { showLogin(); }
      throw failure;
    }
    return data;
  }

  function errorText(error) {
    if (!error || !error.error) return "Unerwarteter Fehler im Browser: " + error;
    const findings = (error.details && error.details.findings) || [];
    if (findings.length > 1) return findings.map((f) => f.check + " " + f.path + ": " + f.message).join(" · ");
    const check = error.details && error.details.check ? error.details.check + ": " : "";
    return check + error.message + " (" + error.error + ")";
  }

  // --- Meldungen (role=status direkt unter dem Titel, GUI-Regel 2 und 4) ----

  function messages() { return document.getElementById("messages"); }
  function say(text, kind, extra) {
    const box = messages();
    if (!box) return null;
    const node = h("div", { class: "msg" + (kind ? " " + kind : ""), role: kind === "error" ? "alert" : "status" }, text, extra);
    box.replaceChildren(node);
    return node;
  }
  const fail = (error) => say(errorText(error), "error");

  /** Verfolgt einen Job per SSE und meldet das Ergebnis. */
  function follow(jobId, container, labels) {
    const logLink = container ? h("a", { href: "#/c/" + enc(container) + "/log?job=" + enc(jobId) }, "Log ansehen") : null;
    const busy = say(labels.busy, "busy", logLink);
    // Stand langer Schritte (Backup, Restore): Balken unter der Meldung, solange der Job ihn meldet.
    const bar = h("progress", { max: "100" });
    const barText = h("span", {});
    const barBox = h("div", { class: "job-progress" }, bar, barText);
    barBox.hidden = true;
    if (busy) busy.append(barBox);
    return new Promise((resolve) => {
      const source = new EventSource("/api/jobs/" + enc(jobId) + "/events");
      let result = null;
      const stop = () => { source.close(); };
      state.cleanup.push(stop);
      source.addEventListener("state", (event) => { result = JSON.parse(event.data); });
      source.addEventListener("progress", (event) => {
        const step = JSON.parse(event.data);
        barBox.hidden = !step;
        if (!step) return;
        if (step.percent === null) bar.removeAttribute("value"); else bar.value = step.percent;
        barText.textContent = step.label + " · " + (step.percent === null ? "" : step.percent + " % · ") + size(step.done) + (step.total ? " von " + (step.exact ? "" : "etwa ") + size(step.total) : "");
      });
      source.addEventListener("end", () => {
        stop();
        const ok = result && result.state === "ok";
        if (ok) say(labels.ok, "", logLink);
        else say(labels.fail + (result && result.error ? " (" + result.error + ")" : ""), "error", logLink);
        resolve(ok);
      });
      source.onerror = () => { stop(); say("Verbindung zum Job-Log verloren; der Job läuft weiter.", "warn", logLink); resolve(false); };
    });
  }

  function confirmDialog(title, text, okLabel) {
    const dialog = document.getElementById("dialog");
    document.getElementById("dialog-title").textContent = title;
    document.getElementById("dialog-text").textContent = text;
    document.getElementById("dialog-ok").textContent = okLabel || "Bestätigen";
    return new Promise((resolve) => {
      dialog.returnValue = "cancel";
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
      dialog.showModal();
    });
  }

  // --- Seitenrahmen (GUI-Regel 4: Brotkrumen, Titel, Hauptaktion rechts, Meldungen) ----

  function page(options, ...content) {
    const crumbs = [["Dashboard", "#/"]].concat(options.crumbs || []);
    const trail = [];
    crumbs.forEach(([label, href], index) => {
      if (index) trail.push(h("span", { class: "sep" }, "/"));
      trail.push(href && index < crumbs.length - 1 ? h("a", { href }, label) : label);
    });
    // Auf den Seiten eines Containers steht sein Name im Titel des Tabs: Bei mehreren offenen Tabs sieht man, welcher zu welchem gehört.
    const inContainer = /^#\/c\/([^/?]+)/.exec(location.hash);
    const container = inContainer ? decodeURIComponent(inContainer[1]) : "";
    document.title = "devc" + (container ? " [" + container + "]" : "") + (options.title && options.title !== container ? " – " + options.title : "");
    for (const item of document.querySelectorAll("#nav a")) item.classList.toggle("active", item.dataset.key === (options.nav || "dash"));
    // replaceChildren würde null als Text "null" einsetzen, darum vorher filtern
    const parts = [
      crumbs.length > 1 ? h("p", { class: "crumb" }, trail) : null,
      options.compact ? null : h("div", { class: "head" },
        h("div", {}, h("div", { class: "row" }, h("h1", { class: options.mono ? "mono" : "" }, options.title), options.badge),
          options.sub ? h("p", { class: "sub" }, options.sub) : null),
        h("div", { class: "actions" }, options.actions || [])),
      h("div", { id: "messages" }),
      ...content.flat(Infinity)];
    main.replaceChildren(...parts.filter((part) => part !== null && part !== undefined && part !== false));
    if (state.flash) { say(state.flash.text, state.flash.kind); state.flash = null; }
    main.focus({ preventScroll: true });
  }
  const card = (title, ...content) => h("section", { class: "card stack" }, title ? h("h2", { class: "sec" }, title) : null, content);
  const flush = (title, ...content) => h("section", { class: "card flush" }, title ? h("div", { class: "card-head" }, h("h2", { class: "sec" }, title)) : null, content);
  const hint = (...text) => h("p", { class: "hint" }, text);
  const field = (label, control, note) => h("label", { class: "field" }, h("span", { class: "lbl" }, label), control, note ? hint(note) : null);
  const option = (input, ...text) => h("label", { class: "opt" }, input, h("span", {}, text));
  function go(hash, flash) { state.flash = flash || null; if (location.hash === hash) route(); else location.hash = hash; }
  const cPath = (name, rest) => "#/c/" + enc(name) + (rest ? "/" + rest : "");
  const cCrumbs = (name, ...more) => [[name, cPath(name)]].concat(more);

  // --- Anmeldung ------------------------------------------------------------

  function showLogin() {
    document.getElementById("top").hidden = true;
    document.title = "devc – Anmelden";
    const input = h("input", { class: "inp mono", type: "password", autocomplete: "current-password", required: true, id: "token" });
    const note = h("div", { id: "messages" });
    const form = h("form", { class: "card stack login", onsubmit: async (event) => {
      event.preventDefault();
      try {
        await api("POST", "/api/login", { token: input.value.trim() }, { login: true });
        await start();
      } catch (error) { note.replaceChildren(h("div", { class: "msg error", role: "alert" }, error.error === "E_AUTH" ? "Token ist falsch." : errorText(error))); }
    } },
      h("h1", {}, "devc"),
      h("p", {}, "Anmelden mit dem API-Token dieses Hosts."),
      note,
      field("Token", input, "Auf dem Host anzeigen mit: devc token show"),
      h("button", { class: "btn btn-primary", type: "submit" }, "Anmelden"));
    main.replaceChildren(form);
    input.focus();
  }

  // --- Dashboard --------------------------------------------------------------

  async function quickToggle(container, done) {
    const action = container.status === "running" ? "stop" : "start";
    try {
      await api("POST", "/api/containers/" + enc(container.name) + "/" + action);
      state.flash = { text: container.name + " wurde " + (action === "stop" ? "gestoppt." : "gestartet.") };
      done();
    } catch (error) { fail(error); }
  }

  async function dashboard() {
    const [list, templates, addons, secrets, jobs, vm] = await Promise.all([
      api("GET", "/api/containers"), api("GET", "/api/templates"), api("GET", "/api/addons"), api("GET", "/api/secrets"), api("GET", "/api/jobs"), api("GET", "/api/vm")]);
    const containers = list.containers;
    const backups = {};
    await Promise.all(containers.filter((c) => c.status !== "foreign").map(async (c) => {
      try { backups[c.name] = (await api("GET", "/api/containers/" + enc(c.name) + "/backups")).backups; } catch (error) { backups[c.name] = []; }
    }));
    const rows = containers.map((c) => {
      const managed = c.status === "running" || c.status === "stopped";
      const last = (backups[c.name] || []).slice(-1)[0];
      return [
        c.status === "foreign" ? mono(c.name) : h("a", { href: cPath(c.name), class: "mono" }, c.name),
        statusBadge(c),
        mono((c.template || "–").split("@")[0]),
        Object.keys(c.addons).sort().map((id) => [h("span", { class: "chip" }, id), " "]),
        h("span", { class: "muted" }, last ? stampDate(last.stamp) : "keins"),
        managed ? btn(c.status === "running" ? "Stoppen" : "Starten", () => quickToggle(c, route)) : h("span", { class: "muted" }, "–"),
      ];
    });
    const running = jobs.jobs.filter((job) => job.state === "running" || job.state === "queued");
    const own = (items) => items.filter((item) => item.origin !== "builtin").length;
    page({ title: "Dashboard", sub: containers.length + " Container · " + containers.filter((c) => c.status === "running").length + " laufen",
      actions: [link("Neuer Container", "#/new", "primary")] },
      flush("Container", table(["Name", "Status", "Template", "Addons", "Letztes Backup", "Schnellaktion"], rows, "Noch kein Container. Mit »Neuer Container« anlegen."),
        hint("Der Name führt zum Container-Dashboard. Starten und Stoppen sind die einzigen Schnellaktionen hier.")),
      h("div", { class: "cols" },
        h("section", { class: "wide stack" }, h("h2", { class: "sec" }, "Funktionen"), h("div", { class: "tiles" },
          h("a", { class: "tile", href: "#/templates" }, h("span", { class: "title" }, "Templates"),
            h("span", {}, templates.templates.length + " Templates · " + own(templates.templates) + " eigene"), hint("anlegen, bearbeiten, aus Container speichern")),
          h("a", { class: "tile", href: "#/addons" }, h("span", { class: "title" }, "Addons"),
            h("span", {}, addons.addons.length + " Addons · " + own(addons.addons) + " eigene"), hint("hochladen, herunterladen, löschen")),
          h("a", { class: "tile", href: "#/secrets" }, h("span", { class: "title" }, "Secret-Store"),
            h("span", {}, secrets.secrets.length + " Einträge"), hint("geteilte Secrets für Templates und Container")),
          // Die VM, in der devc läuft: nur dort, wo vm/install.sh sie eingerichtet hat (SPEC 8.14).
          vm.terminal || vm.update ? h("a", { class: "tile", href: "#/vm" }, h("span", { class: "title" }, "VM"),
            h("span", {}, vmBuild(vm)), hint([vm.terminal ? "Terminal der VM" : null, vm.update ? "devc aktualisieren" : null].filter(Boolean).join(", "))) : null)),
        h("section", { class: "narrow stack" }, h("h2", { class: "sec" }, "Laufende Jobs"),
          h("div", { class: "card stack" }, running.length ? running.map((job) => h("div", { class: "row" },
            mono(job.kind + (job.container ? " " + job.container : "")), h("span", { class: "pill on" }, "läuft"),
            job.container ? h("a", { href: cPath(job.container, "log?job=" + enc(job.id)) }, "Log ansehen") : null)) : h("p", { class: "muted" }, "Kein Job läuft.")))));
    if (running.length) { const timer = setTimeout(route, 4000); state.cleanup.push(() => clearTimeout(timer)); }
  }

  // --- Neuer Container --------------------------------------------------------

  async function newTemplate() {
    const [templates, secrets] = await Promise.all([api("GET", "/api/templates"), api("GET", "/api/secrets")]);
    const tiles = templates.templates.filter((t) => !t.error).map((t) => h("a", { class: "tile", href: "#/new/edit?template=" + enc(t.id) },
      h("span", { class: "row" }, h("span", { class: "title mono" }, t.id), h("span", { class: "hint" }, t.origin_label + " · " + t.version)),
      h("span", {}, t.description || t.title),
      h("span", {}, t.addons.map((id) => [h("span", { class: "chip" }, id), " "]))));
    tiles.push(h("a", { class: "tile", href: "#/new/edit" }, h("span", { class: "title" }, "Ohne Template"),
      h("span", {}, "Leere devc.toml mit Standardwerten aus der Config"), hint("Addons aus defaults.addons vorbelegt")));
    page({ title: "Neuer Container", crumbs: [["Neuer Container"]], actions: [link("Abbrechen", "#/")] },
      h("ol", { class: "steps" }, h("li", { class: "on" }, "1 · Template wählen"), h("li", {}, "2 · Instanz bearbeiten")),
      h("p", {}, "Wähle einen Ausgangspunkt. Im nächsten Schritt passt du die Konfiguration für diese Instanz an; das Template bleibt unverändert."),
      h("div", { class: "tiles" }, tiles),
      hint("Templates verwaltest du unter ", h("a", { href: "#/templates" }, "Templates"), ". ", secrets.secrets.length + " Einträge im Secret-Store."));
  }

  /** Liste der Prüfbefunde wie in den Mockups; liefert true, wenn kein Fehler dabei ist. */
  function renderChecks(target, result, okText) {
    const findings = result.findings || [];
    const errors = findings.filter((f) => f.severity === "error");
    const items = [];
    if (!errors.length) items.push(h("li", { class: "ok" }, okText || "TOML gültig, alle Prüfungen bestanden"));
    for (const f of findings) items.push(h("li", { class: f.severity === "error" ? "err" : "warn" }, f.check + " " + f.path + ": " + f.message));
    for (const slot of result.missing_secrets || []) items.push(h("li", { class: "err" }, "Secret " + slot + " hat keinen Wert: Eintrag im Secret-Store anlegen oder den Verweis entfernen"));
    target.replaceChildren(...items);
    return !errors.length && !(result.missing_secrets || []).length;
  }

  function debounce(fn, ms) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; }

  async function newEdit(query) {
    const template = query.get("template") || "";
    const name = h("input", { class: "inp mono", type: "text", value: "", maxlength: "31", autocomplete: "off", spellcheck: "false" });
    const editor = h("textarea", { class: "editor", spellcheck: "false", "aria-label": "devc.toml" });
    const checks = h("ul", { class: "check" });
    const create = btn("Container erstellen", submit, "primary");
    let valid = false;
    const validate = debounce(async () => {
      try {
        valid = renderChecks(checks, await api("POST", "/api/validate/instance", editor.value));
      } catch (error) { valid = false; checks.replaceChildren(h("li", { class: "err" }, errorText(error))); }
    }, 350);
    async function loadDraft() {
      if (!/^[a-z][a-z0-9-]{1,30}$/.test(name.value) || name.value.endsWith("-")) { checks.replaceChildren(h("li", { class: "err" }, "Name: a–z, 0–9 und Bindestrich, 2–31 Zeichen, beginnt mit einem Buchstaben")); return; }
      try { editor.value = (await api("POST", "/api/drafts", { template, name: name.value })).text; validate(); }
      catch (error) { checks.replaceChildren(h("li", { class: "err" }, errorText(error))); }
    }
    let touched = false;
    name.addEventListener("input", debounce(() => {
      if (!touched) { loadDraft(); return; }
      editor.value = editor.value.replace(/^(\s*name\s*=\s*).*$/m, '$1"' + name.value + '"');
      validate();
    }, 300));
    editor.addEventListener("input", () => { touched = true; validate(); });
    async function submit() {
      if (!valid) { say("Die Prüfung meldet noch Fehler.", "error"); return; }
      try {
        const result = await api("POST", "/api/containers", editor.value);
        const target = (/^\s*name\s*=\s*"([^"]+)"/m.exec(editor.value) || [])[1] || name.value;
        create.disabled = true;
        const ok = await follow(result.job, target, { busy: "Container " + target + " wird angelegt …", ok: "Container " + target + " ist angelegt.", fail: "Anlegen von " + target + " ist fehlgeschlagen" });
        if (ok) go(cPath(target), { text: "Container " + target + " ist angelegt." });
        create.disabled = false;
      } catch (error) { fail(error); }
    }
    page({ title: "Neuer Container", crumbs: [["Neuer Container", "#/new"], ["Instanz bearbeiten"]], actions: [link("Zurück", "#/new"), create] },
      h("ol", { class: "steps" }, h("li", {}, "1 · Template wählen"), h("li", { class: "on" }, "2 · Instanz bearbeiten")),
      h("p", {}, template ? ["Entwurf aus ", mono(template), ". Änderungen gelten nur für diese Instanz; das Template bleibt unverändert."] : "Entwurf ohne Template mit den Standardwerten aus der Config."),
      h("div", { class: "cols" },
        h("div", { class: "narrow stack" },
          card("Wichtigste Angaben", field("Name", name, "a–z, 0–9 und Bindestrich, 2–31 Zeichen"),
            hint("Formular und Text sind dieselbe Datei. Code-Ablage (", mono("[data]"), "), Addon-Parameter und Secrets stehen im TOML.")),
          card("Prüfung", checks)),
        h("div", { class: "wide" }, card("devc.toml", hint("wird gespeichert, wie du es schreibst; Kommentare bleiben"), editor))));
    checks.replaceChildren(h("li", { class: "warn" }, "Zuerst einen Namen eingeben."));
    name.focus();
  }

  // --- Templates ----------------------------------------------------------------

  async function templatesPage() {
    const [templates, containers, secrets] = await Promise.all([api("GET", "/api/templates"), api("GET", "/api/containers"), api("GET", "/api/secrets")]);
    const usedBy = (id) => containers.containers.filter((c) => (c.template || "").split("@")[0] === id).map((c) => c.name).join(", ") || "–";
    const rows = templates.templates.map((t) => [
      [h("a", { href: "#/templates/" + enc(t.id), class: "mono" }, t.id), h("div", { class: "hint" }, t.error ? "fehlerhaft: " + t.error : (t.title || ""))],
      t.version || "?", t.origin_label, (t.addons || []).map((id) => [h("span", { class: "chip" }, id), " "]), usedBy(t.id)]);
    async function create() {
      const id = (window.prompt("ID des neuen Templates (a–z, 0–9, Bindestrich):") || "").trim();
      if (!id) return;
      go("#/templates/" + enc(id) + "?new=1");
    }
    page({ title: "Templates", nav: "templates", crumbs: [["Templates"]], sub: "Eine TOML-Datei pro Template unter ~/.config/devc/templates/",
      actions: [btn("Neues Template", create, "primary")] },
      flush(null, table(["Template", "Version", "Herkunft", "Addons", "Verwendet von"], rows)),
      hint("Mitgelieferte Templates werden beim Bearbeiten als eigene Kopie gespeichert. " + secrets.secrets.length + " Einträge im Secret-Store."));
  }

  async function templateEdit(id, query) {
    let meta = null, text;
    if (query.get("new")) {
      text = 'id = "' + id + '"\ntitle = "' + id + '"\nversion = "1.0.0"\ndescription = ""\naddons = ["claude-code"]\n\n[data]\ncode = "volume"\n';
    } else {
      const shown = await api("GET", "/api/templates/" + enc(id));
      meta = shown.meta; text = shown.text;
    }
    const editor = h("textarea", { class: "editor", spellcheck: "false", "aria-label": id + ".toml" });
    editor.value = text;
    const checks = h("ul", { class: "check" });
    async function save() {
      try {
        const result = await api("PUT", "/api/templates/" + enc(id), editor.value);
        renderChecks(checks, result, "Gespeichert: TOML gültig, Addons und Parameter passen");
        text = editor.value;
        say("Template " + id + " gespeichert" + (meta && meta.origin === "builtin" ? " (als eigene Kopie)." : "."));
      } catch (error) {
        fail(error);
        checks.replaceChildren(...((error.details && error.details.findings) || [{ check: (error.details || {}).check || "", path: (error.details || {}).path || "", message: error.message }])
          .map((f) => h("li", { class: "err" }, f.check + " " + f.path + ": " + f.message)));
      }
    }
    async function remove() {
      if (!await confirmDialog("Template löschen", "Das eigene Template " + id + " wird gelöscht. Bestehende Container bleiben unverändert.", "Löschen")) return;
      try { await api("DELETE", "/api/templates/" + enc(id)); go("#/templates", { text: "Template " + id + " gelöscht." }); } catch (error) { fail(error); }
    }
    page({ title: id + ".toml", mono: true, nav: "templates", crumbs: [["Templates", "#/templates"], [id]],
      sub: meta ? meta.origin_label + " · Version " + meta.version : "Neues Template, noch nicht gespeichert",
      actions: [btn("Verwerfen", () => { editor.value = text; say("Änderungen verworfen."); }), btn("Speichern", save, "primary")] },
      h("div", { class: "cols" },
        h("div", { class: "wide" }, card("Inhalt", editor)),
        h("div", { class: "narrow stack" },
          card("Prüfung", checks, hint("Geprüft wird beim Speichern; gespeichert wird nur ein gültiges Template.")),
          card("Verwenden", link("Neuen Container aus diesem Template", "#/new/edit?template=" + enc(id))),
          meta && meta.origin !== "builtin" ? card("Template löschen", btn("Template löschen", remove, "danger")) : null)));
    checks.replaceChildren(h("li", { class: "warn" }, "Noch nicht geprüft."));
  }

  // --- Addons (global) ----------------------------------------------------------

  async function addonsPage() {
    const [addons, containers] = await Promise.all([api("GET", "/api/addons"), api("GET", "/api/containers")]);
    const usedBy = (id) => containers.containers.filter((c) => id in c.addons).map((c) => c.name);
    async function remove(addon) {
      if (!await confirmDialog("Addon löschen", "Das eigene Addon " + addon.id + " wird gelöscht.", "Löschen")) return;
      try { await api("DELETE", "/api/addons/" + enc(addon.id)); go("#/addons", { text: "Addon " + addon.id + " gelöscht." }); } catch (error) { fail(error); }
    }
    const rows = addons.addons.map((a) => [
      [mono(a.id), h("div", { class: "hint" }, a.description)], a.version, a.origin_label, String(a.actions_count), usedBy(a.id).join(", ") || "–",
      h("div", { class: "row" }, h("a", { class: "btn", href: "/api/addons/" + enc(a.id) + "/archive", download: "" }, "Herunterladen"),
        a.origin !== "builtin" ? btn("Löschen", () => remove(a), "danger") : null)]);
    const file = h("input", { class: "inp", type: "file", accept: ".gz,.tgz,.zip,application/gzip,application/zip" });
    async function upload() {
      if (!file.files.length) { say("Zuerst ein Archiv auswählen.", "error"); return; }
      try {
        const result = await api("PUT", "/api/addons", file.files[0], { type: "application/octet-stream" });
        go("#/addons", { text: result.id + " " + result.version + " hochgeladen und geprüft." });
      } catch (error) { fail(error); }
    }
    page({ title: "Addons", nav: "addons", crumbs: [["Addons"]], sub: "Eigene Addons liegen unter ~/.config/devc/addons/, mitgelieferte in devc selbst." },
      card("Addon hochladen", h("div", { class: "row" }, h("div", { style: "flex: 1 1 260px" }, file), btn("Hochladen", upload, "primary"))),
      flush(null, table(["Addon", "Version", "Herkunft", "Aktionen", "Genutzt von", "Verwalten"], rows)),
      addons.broken.length ? h("div", { class: "msg warn" }, "Fehlerhafte eigene Addons: " + addons.broken.map((b) => b.id + " (" + b.findings.map((f) => f.check + " " + f.message).join("; ") + ")").join(" · ")) : null,
      hint("Hochladen: ein Archiv (.tar.gz oder .zip) mit genau einem Addon-Ordner. devc prüft addon.toml, README.md und AI.md, bevor das Addon verfügbar wird. Gleiche id ersetzt das eigene Addon; Container übernehmen die neue Version erst über Konfiguration › Anwenden. Mitgelieferte Addons lassen sich nicht löschen, nur durch ein eigenes mit gleicher id überschreiben. Löschen geht nur, solange kein Container das Addon nutzt."));
  }

  // --- Secret-Store ---------------------------------------------------------------

  function secretSource(valueInput, fileInput) {
    return async () => {
      if (fileInput.files.length) return fileInput.files[0];
      if (valueInput.value) return new Blob([valueInput.value]);
      return null;
    };
  }

  async function secretsPage() {
    const secrets = await api("GET", "/api/secrets");
    async function remove(entry) {
      if (!await confirmDialog("Eintrag entfernen", "Der Store-Eintrag " + entry.name + " wird entfernt. Container, die den Wert schon haben, behalten ihn.", "Entfernen")) return;
      try { await api("DELETE", "/api/secrets/" + enc(entry.name)); go("#/secrets", { text: entry.name + " wurde entfernt." }); } catch (error) { fail(error); }
    }
    const rows = secrets.secrets.map((s) => [mono(s.name), s.used_by.join(", ") || "–", when(s.modified), btn("Entfernen", () => remove(s), "danger")]);
    const name = h("input", { class: "inp mono", type: "text", autocomplete: "off", spellcheck: "false", maxlength: "64" });
    const value = h("input", { class: "inp", type: "password", autocomplete: "new-password" });
    const file = h("input", { class: "inp", type: "file" });
    const read = secretSource(value, file);
    async function save(event) {
      event.preventDefault();
      const body = await read();
      if (!name.value || !body) { say("Name und Wert (oder Datei) angeben.", "error"); return; }
      try {
        await api("PUT", "/api/secrets/" + enc(name.value), body, { type: "application/octet-stream" });
        go("#/secrets", { text: name.value + " wurde gesetzt. Bestehende Container behalten ihre Kopie." });
      } catch (error) { fail(error); }
    }
    page({ title: "Secret-Store", nav: "secrets", crumbs: [["Secret-Store"]], sub: "Geteilte Secrets auf dem Host. Templates verweisen nur auf die Namen; Werte werden nie angezeigt." },
      h("div", { class: "cols" },
        h("div", { class: "wide" }, flush("Einträge", table(["Name", "Genutzt von", "Geändert", "Aktion"], rows, "Noch keine Einträge."),
          hint("Entfernen fragt einmal nach und meldet das Ergebnis. Container, die den Wert schon haben, behalten ihn."))),
        h("form", { class: "narrow card stack", onsubmit: save }, h("h2", { class: "sec" }, "Eintrag setzen"),
          field("Name", name, "vorhandener Name wird ersetzt"), field("Wert", value), field("oder aus Datei", file, "z. B. ein Deploy-Key; Ablage mit Modus 0600"),
          h("button", { class: "btn btn-primary", type: "submit" }, "Speichern"))));
  }

  // --- Container-Dashboard ---------------------------------------------------------

  // Adresse eines Container-Ports hinter dem Proxy (SPEC 8.11). Schema und Port kommen von der Adresse,
  // unter der die GUI selbst geladen wurde; so stimmt sie auch durch einen Tunnel.
  function portUrl(name, port, path) {
    return location.protocol + "//" + location.host.replace(/^devc\./, port + "-" + name + ".") + (path || "/");
  }
  const linkUrl = (name, l) => (state.status.behind_proxy && l.port && l.url ? portUrl(name, l.port, l.path) : l.url);

  async function containerPage(name) {
    const base = "/api/containers/" + enc(name);
    const info = await api("GET", base);
    const [snapshots, backups, jobs, secrets, addons, extra] = await Promise.all([
      api("GET", base + "/snapshots"), api("GET", base + "/backups"), api("GET", "/api/jobs?container=" + enc(name)),
      api("GET", base + "/secrets"), api("GET", base + "/addons").catch(() => ({ addons: [] })),
      api("GET", base + "/tiles").catch(() => ({ tiles: [] }))]);
    const running = info.status === "running";
    const container = { name, status: info.status, incomplete: info.incomplete, installing: info.installing };
    async function restart() {
      try { await api("POST", base + "/restart"); go(cPath(name), { text: name + " wurde neu gestartet." }); } catch (error) { fail(error); }
    }
    async function copyShell() {
      const command = "devc shell " + name;
      try { await navigator.clipboard.writeText(command); say(["Kopiert: ", mono(command)]); }
      catch (error) { say(["Befehl für die Shell: ", mono(command)]); }
    }
    const res = info.resources;
    const set = secrets.secrets.filter((s) => s.source !== "none").length;
    const lastJob = jobs.jobs[0];
    const runningActions = addons.addons.flatMap((a) => a.actions.filter((x) => x.status === "running").map(() => a.id));
    const tile = (title, text, go_, href) => h("a", { class: "tile", href: cPath(name, href) }, h("span", { class: "title" }, title), h("span", {}, text), h("span", { class: "go" }, go_));
    // Kacheln aus Addons (SPEC 8.13): eigene Seite in devc (view, chat) oder die Oberfläche des Addons (link).
    const TILE_GO = { view: "Ansehen", chat: "Öffnen", panel: "Öffnen", link: "In neuem Tab öffnen" };
    const addonTile = (t) => {
      const text = t.description || ("Addon " + t.addon);
      if (t.kind !== "link") return tile(t.title, running ? text : "Container läuft nicht", TILE_GO[t.kind], "tile/" + enc(t.ref));
      if (!t.active) return h("div", { class: "tile off" }, h("span", { class: "title" }, t.title), h("span", {}, running ? "Adresse fehlt" : "Container läuft nicht"));
      return h("a", { class: "tile", href: linkUrl(name, t), target: "_blank", rel: "noopener noreferrer" }, h("span", { class: "title" }, t.title), h("span", {}, text), h("span", { class: "go" }, TILE_GO.link));
    };
    page({ title: name, mono: true, crumbs: [[name]], badge: statusBadge(container),
      // Löschen braucht eine Angabe (Daten behalten?) und eine Bestätigung, darum führt der Knopf auf die Seite dafür.
      actions: [link("Container löschen …", cPath(name, "config?delete=1"), "danger")],
      sub: ["LXD · Template ", mono(info.template || "–"), " · " + res.cpu + " CPU · " + res.memory + " · " + res.disk + " · Code ", mono(info.data.shared || "Volume")] },
      info.status === "missing" ? h("div", { class: "msg error" }, "Der Container fehlt in LXD. Unter Konfiguration lässt er sich aufräumen (löschen).") : null,
      info.installing ? h("div", { class: "msg busy" }, "Der Container wird gerade eingerichtet. Bitte warten und diese Seite danach neu laden.", link("Fortschritt ansehen", cPath(name, "log")))
        : info.incomplete ? h("div", { class: "msg warn" }, "Die Einrichtung des Containers ist abgebrochen. Unter Konfiguration »Anwenden« setzt die Einrichtung fort.", link("Grund ansehen", cPath(name, "log"))) : null,
      info.findings.map((f) => h("div", { class: "msg warn" }, f.check + ": " + f.message)),
      h("section", { class: "qa", "aria-labelledby": "qa-h" }, h("h2", { id: "qa-h" }, "Schnellaktionen"),
        btn(running ? "Stoppen" : "Starten", () => quickToggle(container, route)),
        btn("Neu starten", restart), btn("Shell-Befehl kopieren", copyShell)),
      h("section", { class: "stack" }, h("h2", { class: "sec" }, "Funktionen"), h("div", { class: "tiles" },
        tile("Terminal", running ? "bereit" : "Container läuft nicht", "Öffnen", "terminal"),
        tile("Dateien", [mono("/work"), info.data.shared ? " · Shared Folder" : " · Volume"], "Durchsuchen, hoch- und herunterladen", "files"),
        tile("Konfiguration", [mono("devc.toml"), " · zuletzt angewendet " + when(info.applied_at)], "Bearbeiten, anwenden, Container löschen", "config"),
        tile("Addons", Object.keys(info.addons).length + " aktiv" + (runningActions.length ? " · " + runningActions.join(", ") + " läuft" : ""), "Hilfe und Aktionen", "addons"),
        tile("Secrets", set + " gesetzt · " + (secrets.secrets.length - set) + " offen", "Verwalten", "secrets"),
        tile("Snapshots", snapshots.snapshots.length + (snapshots.snapshots.length ? " · letzter " + when(snapshots.snapshots.slice(-1)[0].created_at) : ""), "Liste, erstellen, zurücksetzen", "snapshots"),
        tile("Backups", backups.backups.length + (backups.backups.length ? " · " + stampDate(backups.backups.slice(-1)[0].stamp) : ""), "Liste, erstellen, wiederherstellen", "backups"),
        tile("Log", lastJob ? "letzter Job: " + lastJob.kind + " · " + (lastJob.state === "ok" ? "erfolgreich" : lastJob.state === "failed" ? "fehlgeschlagen" : "läuft") : "noch kein Job", "Jobs ansehen", "log"),
        extra.tiles.map(addonTile))),
      hint("Kacheln führen auf die Seite der Funktion. Was ohne Eingabe in einem Schritt geht, steht oben unter Schnellaktionen und meldet das Ergebnis hier."));
  }

  // --- Container: Kacheln aus Addons (SPEC 8.13) ------------------------------------

  async function tilePage(name, ref, query) {
    const tiles = (await api("GET", "/api/containers/" + enc(name) + "/tiles")).tiles;
    const tile = tiles.find((t) => t.ref === ref);
    if (!tile || tile.kind === "link") throw { error: "E_NOT_FOUND", message: "Kachel fehlt: " + ref, details: {} };
    if (tile.kind === "panel") return panelPage(name, tile, query);
    return tile.kind === "chat" ? chatPage(name, tile, query) : tileView(name, tile);
  }

  // Art panel: Das Addon liefert Bausteine als Daten (Text, Tabelle, Knöpfe, Formular), devc zeichnet sie. Nie HTML.
  // Die Ansicht steht in der Adresse (?v=<ansicht>&a.<name>=<wert>), damit Zurück und Neuladen funktionieren.
  async function panelPage(name, tile, query) {
    const api_ = "/api/containers/" + enc(name) + "/tiles/" + enc(tile.ref) + "/panel";
    const here = cPath(name, "tile/" + enc(tile.ref));
    const current = { view: query.get("v") || "", args: {} };
    for (const [key, value] of query) if (key.startsWith("a.")) current.args[key.slice(2)] = value;
    const address = (target) => {
      const q = new URLSearchParams();
      if (target.view) q.set("v", target.view);
      for (const [key, value] of Object.entries(target.args || {})) q.set("a." + key, value);
      const text = q.toString();
      return here + (text ? "?" + text : "");
    };
    const body = h("div", { class: "stack panel" });
    const result = h("div", {});
    let busy = false, gone = false;
    state.cleanup.push(() => { gone = true; });

    async function act(button, values) {
      if (busy) return;
      if (button.confirm && !await confirmDialog(button.label, button.confirm, button.label)) return;
      busy = true;
      body.classList.add("busy");
      say(button.label + " läuft …", "busy");
      try {
        const done = await api("POST", api_, { action: button.action, args: button.args, values: values || {} });
        if (gone) return;
        result.replaceChildren(done.output ? h("details", { class: "card dense", open: !done.ok }, h("summary", {}, "Ausgabe"), h("pre", { class: "panel-code" }, done.output)) : "");
        const flash = { text: done.message || (done.ok ? button.label + ": erledigt." : button.label + ": fehlgeschlagen."), kind: done.ok ? null : "error" };
        const next = address(done.view || current);
        busy = false;
        if (next === location.hash) { await load(); say(flash.text, flash.kind); } else { state.keep = result.firstChild || null; go(next, flash); }
      } catch (error) { if (!gone) fail(error); }
      finally { busy = false; body.classList.remove("busy"); }
    }
    const button = (b, small) => {
      const cls = "btn" + (small ? " btn-s" : "") + (b.style ? " btn-" + b.style : "");
      return "view" in b ? h("a", { class: cls, href: address(b) }, b.label) : h("button", { class: cls, type: "button", onclick: () => act(b) }, b.label);
    };
    function form(block) {
      const inputs = block.fields.map((f) => {
        let input;
        if (f.type === "bool") { input = h("input", { type: "checkbox" }); input.checked = f.value; }
        else if (f.type === "choice") input = h("select", { class: "inp" }, f.options.map((o) => h("option", { value: o, selected: o === f.value }, o)));
        else if (f.type === "multiline") { input = h("textarea", { class: "inp", rows: "4", spellcheck: "true" }); input.value = f.value; }
        else input = h("input", { class: "inp", type: "text", value: f.value });
        return [f, input];
      });
      const send = () => {
        const values = {};
        for (const [f, input] of inputs) {
          values[f.name] = f.type === "bool" ? input.checked : input.value;
          if (f.required && f.type !== "bool" && !values[f.name].trim()) { say(f.label + " fehlt.", "error"); input.focus(); return; }
        }
        act(block.submit, values);
      };
      return card(block.title || null,
        inputs.map(([f, input]) => f.type === "bool" ? [option(input, f.label), f.hint ? hint(f.hint) : null] : field(f.label, input, f.hint || null)).flat(),
        h("div", { class: "row" }, h("button", { class: "btn" + (block.submit.style ? " btn-" + block.submit.style : ""), type: "button", onclick: send }, block.submit.label)));
    }
    function draw(block) {
      if (block.type === "text") return card(block.title || null, h("article", { class: "doc" }, mdBlocks(block.text)));
      if (block.type === "code") return card(block.title || null, h("pre", { class: "panel-code" }, block.text));
      if (block.type === "actions") return h("div", { class: "row" }, block.title ? h("span", { class: "lbl" }, block.title) : null, block.actions.map((b) => button(b)));
      if (block.type === "form") return form(block);
      if (block.type === "table") {
        const withActions = block.rows.some((r) => r.actions.length);
        const rows = block.rows.map((r) => r.cells.concat(withActions ? [h("div", { class: "row" }, r.actions.map((b) => button(b, true)))] : []));
        return flush(block.title || null, table(block.columns.concat(withActions ? [""] : []), rows, block.empty || "Keine Einträge."),
          block.truncated ? hint("Die Liste ist gekürzt.") : null);
      }
      return null;
    }
    async function load() {
      const data = await api("POST", api_, { view: current.view, args: current.args });
      if (gone) return;
      heading.textContent = data.title || tile.title;
      if (current.view && crumb) crumb.textContent = data.title || tile.title;
      note.textContent = data.note || "";
      body.replaceChildren(...data.blocks.map(draw).filter(Boolean));
    }
    const note = h("p", { class: "hint" });
    page({ title: tile.title, crumbs: cCrumbs(name, current.view ? [tile.title, here] : [tile.title]).concat(current.view ? [["…"]] : []), sub: tile.description || null,
      actions: [btn("Aktualisieren", () => load().then(() => { if (messages()) messages().replaceChildren(); }).catch(fail))] },
      result, body, note);
    const heading = document.querySelector("#main h1") || h("h1", {});
    const crumb = current.view && document.querySelector("#main .crumb") ? document.querySelector("#main .crumb").lastChild : null;
    if (state.keep) { result.replaceChildren(state.keep); state.keep = null; }
    await load();
  }

  // Art view: Das Addon liefert Markdown, devc zeichnet es mit dem eigenen Leser (nie als HTML).
  async function tileView(name, tile) {
    const path = "/api/containers/" + enc(name) + "/tiles/" + enc(tile.ref) + "/view";
    const body = h("article", { class: "doc" });
    const note = h("p", { class: "hint" });
    let timer = null, gone = false;
    state.cleanup.push(() => { gone = true; clearTimeout(timer); });
    async function load() {
      clearTimeout(timer);
      // Im Hintergrund-Tab nicht laden: jeder Aufruf startet einen Prozess im Container.
      if (!document.hidden || !body.childElementCount) {
        try {
          const data = await api("GET", path);
          if (gone) return;
          body.replaceChildren(...mdBlocks(data.text));
          note.textContent = "Geladen um " + new Date().toLocaleTimeString("de-DE") + (tile.refresh ? " · lädt alle " + tile.refresh + " Sekunden neu" : "") + (data.truncated ? " · gekürzt" : "");
          if (messages()) messages().replaceChildren();
        } catch (error) { if (gone) return; fail(error); }
      }
      if (tile.refresh) timer = setTimeout(load, tile.refresh * 1000);
    }
    page({ title: tile.title, crumbs: cCrumbs(name, [tile.title]), sub: tile.description || null, actions: [btn("Aktualisieren", load)] },
      card(null, body), note);
    await load();
  }

  // Art chat: Die Bridge des Addons liefert Ereignisse (SPEC 8.13), devc zeichnet sie. Inhalte nur als Text oder über den Markdown-Leser.
  const CHAT_STATE = { starting: "startet", idle: "bereit", working: "arbeitet …", waiting: "wartet auf deine Freigabe", retry: "neuer Versuch …", ending: "wird beendet …", ended: "beendet" };

  async function chatPage(name, tile, query) {
    const base = "/api/containers/" + enc(name);
    const here = cPath(name, "tile/" + enc(tile.ref));
    const crumbs = cCrumbs(name, [tile.title]);
    if (!state.status.terminal_allowed) {
      page({ title: tile.title, crumbs }, card("Chat nicht verfügbar", h("p", {}, "Der Chat ist wie das Web-Terminal für diesen Bind nicht freigegeben.")));
      return;
    }
    const begin = async (resume) => {
      try { await api("POST", base + "/chats", { tile: tile.ref, resume: resume || null }); go(here); } catch (error) { fail(error); }
    };
    const running = (await api("GET", base + "/chats")).sessions.find((s) => s.tile === tile.ref);
    const shown = query.get("session");
    if (shown && !(running && running.session === shown)) return chatShow(name, tile, shown, running ? null : begin);
    if (running) return chatRun(name, tile, running, begin);

    const earlier = h("div", {}, h("p", { class: "empty" }, "Lädt …"));
    page({ title: tile.title, crumbs, sub: tile.description || null, actions: [btn("Neue Unterhaltung", () => begin(), "primary")] },
      flush("Frühere Unterhaltungen", earlier),
      hint("Eine Unterhaltung läuft im Container weiter, wenn du die Seite verlässt. Sie endet mit »Beenden«, mit dem Stopp des Containers oder mit einem Neustart von devc."));
    try {
      const history = (await api("GET", base + "/chats/history?tile=" + enc(tile.ref))).sessions;
      earlier.replaceChildren(table(["Unterhaltung", "Zuletzt", ""],
        history.map((s) => [s.title || mono(s.id), when(s.updated), link("Öffnen", here + "?session=" + enc(s.id))]), "Noch keine Unterhaltung."));
    } catch (error) { earlier.replaceChildren(h("p", { class: "empty" }, errorText(error))); }
  }

  // Zeichnet die Ereignisse einer Unterhaltung. `post` schickt Antworten auf Freigaben; ohne `post` (nur ansehen) gibt es keine.
  // Lange Unterhaltungen: Sichtbar bleiben etwa die letzten CHAT_KEEP Zeichen. Ältere Einträge nimmt der Verlauf aus der Seite
  // und hängt sie erst auf »Ältere Einträge anzeigen« wieder ein, damit große Chats schnell bleiben.
  const CHAT_KEEP = 256 * 1024;
  function chatLog(post) {
    const log = h("div", { class: "chat-log", role: "log", "aria-live": "polite", "aria-label": "Unterhaltung" });
    const tools = new Map(), asks = new Map();
    const info = {};
    let live = null;
    const sizes = new WeakMap(), older = [];
    let shown = 0, limit = CHAT_KEEP;
    const more = h("button", { class: "btn btn-s chat-more", type: "button", hidden: true, onclick: reveal });
    log.append(more);
    const size = (node, n) => { sizes.set(node, (sizes.get(node) || 0) + n); shown += n; };
    function drawMore() {
      const chars = older.reduce((sum, [, n]) => sum + n, 0);
      more.hidden = !older.length;
      more.textContent = "Ältere Einträge anzeigen (" + older.length + " weitere, " + (chars >= 1048576 ? (chars / 1048576).toFixed(1).replace(".", ",") + " MB" : Math.max(1, Math.round(chars / 1024)) + " KB") + ")";
    }
    // Nur am Ende des Verlaufs kürzen: Wer weiter oben liest, dem springt nichts weg.
    function trim() {
      let cut = false;
      while (shown > limit) {
        const first = more.nextElementSibling;
        if (!first || first === log.lastElementChild || first === live) break;
        const n = sizes.get(first) || 0;
        first.remove();
        older.push([first, n]);
        shown -= n;
        cut = true;
      }
      if (cut) drawMore();
    }
    function reveal() {
      const before = log.scrollHeight;
      const nodes = [];
      let n = 0;
      while (older.length && n < CHAT_KEEP) { const [node, chars] = older.pop(); nodes.unshift(node); n += chars; }
      more.after(...nodes);
      shown += n;
      limit += n;  // Was der Nutzer geholt hat, bleibt sichtbar
      drawMore();
      log.scrollTop += log.scrollHeight - before;
    }
    const pinned = () => log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    function add(node, chars) { const stay = pinned(); log.append(node); size(node, chars === undefined ? node.textContent.length : chars); if (stay) { trim(); log.scrollTop = log.scrollHeight; } return node; }
    const stick = (change) => { const stay = pinned(); change(); if (stay) { trim(); log.scrollTop = log.scrollHeight; } };
    const pre = (label, text) => [h("p", { class: "lbl" }, label), h("pre", {}, text || "(leer)")];
    function handle(event) {
      if (event.type === "ready") {
        Object.assign(info, Object.fromEntries(Object.entries(event).filter(([, value]) => value !== null && value !== undefined)));
      } else if (event.type === "user") {
        live = null;
        add(h("div", { class: "chat-msg user" }, event.text));
      } else if (event.type === "text_delta") {
        if (!live) live = add(h("div", { class: "chat-msg agent live" }));
        const piece = String(event.text || "");
        stick(() => { live.append(piece); size(live, piece.length); });
      } else if (event.type === "text") {
        const box = live || add(h("div", { class: "chat-msg agent" }));
        live = null;
        stick(() => { box.className = "chat-msg agent doc"; box.replaceChildren(...mdBlocks(event.text || "")); size(box, box.textContent.length - (sizes.get(box) || 0)); });
      } else if (event.type === "tool_start") {
        live = null;
        const mark = h("span", { class: "chat-mark" }, post ? "läuft" : "");
        const box = h("details", { class: "chat-tool" }, h("summary", {}, h("strong", {}, event.name || "Werkzeug"), " ", mono(event.summary || ""), mark), pre("Eingabe", event.input));
        tools.set(event.id, { box, mark });
        add(box);
      } else if (event.type === "tool_end") {
        const tool = tools.get(event.id);
        if (!tool) return;
        tool.mark.textContent = event.ok ? "fertig" : "Fehler";
        tool.mark.className = "chat-mark " + (event.ok ? "ok" : "bad");
        stick(() => { tool.box.append(...pre("Ergebnis", event.output)); size(tool.box, String(event.output || "").length); });
      } else if (event.type === "permission" && post) {
        const answer = (allow, always, folder) => () => post({ type: "permit", id: event.id, allow, always, folder: folder === true });
        // Nennt die Bridge einen Ordner, gibt es die Freigabe für alle Dateien darin, auch in Unterordnern.
        const folder = typeof event.folder === "string" && event.folder ? event.folder.slice(0, 400) : "";
        const inFolder = folder ? btn("Im Ordner erlauben", answer(true, false, true)) : null;
        if (inFolder) inFolder.title = "Dateien in " + folder + " und seinen Unterordnern bis zum Ende der Unterhaltung ohne Rückfrage ändern";
        const buttons = h("div", { class: "row" }, btn("Erlauben", answer(true, false), "primary"), inFolder, btn("Immer erlauben", answer(true, true)), btn("Ablehnen", answer(false, false)));
        asks.set(event.id, buttons);
        add(h("div", { class: "chat-ask", role: "group", "aria-label": "Freigabe" },
          h("p", {}, "Freigabe nötig: ", h("strong", {}, event.tool || "Werkzeug"), " ", mono(event.summary || "")),
          folder ? h("p", { class: "hint" }, "»Im Ordner erlauben« gilt für ", mono(folder), " mit Unterordnern, bis zum Ende der Unterhaltung.") : null,
          h("details", {}, h("summary", {}, "Eingabe ansehen"), h("pre", {}, event.input || "")), buttons));
      } else if (event.type === "permission_done") {
        const buttons = asks.get(event.id);
        if (buttons) buttons.replaceChildren(h("span", { class: "chat-mark " + (event.allow ? "ok" : "bad") }, event.allow ? "erlaubt" : "abgelehnt"));
      } else if (event.type === "turn_end") {
        live = null;
        const parts = [];
        if (event.text) parts.push(event.text);
        else if (!event.ok) parts.push("Die Antwort endete mit einem Fehler.");
        if (typeof event.duration_ms === "number") parts.push((event.duration_ms / 1000).toFixed(1).replace(".", ",") + " s");
        if (typeof event.cost_usd === "number") parts.push(event.cost_usd.toFixed(3).replace(".", ",") + " $ bisher");
        if (parts.length) add(h("p", { class: "chat-note" + (event.ok ? "" : " bad") }, parts.join(" · ")));
      } else if (event.type === "error") {
        add(h("div", { class: "msg error", role: "alert" }, event.text || "Fehler"));
      }
    }
    return { log, add, handle, info };
  }

  // Der Chat füllt das Fenster: statt des großen Seitenkopfs eine Zeile über dem Verlauf (Titel, Zustand, Agent, Aktionen).
  // Textgröße im Chat: je nach Bildschirm ist die Schrift zu klein. Die Wahl gilt für alle Chats in diesem Browser.
  const CHAT_ZOOM = [0.85, 1, 1.15, 1.3, 1.5, 1.75, 2];
  function chatZoom(targets) {
    let step = 1;
    try { const saved = CHAT_ZOOM.indexOf(parseFloat(localStorage.getItem("devc.chat.zoom"))); if (saved >= 0) step = saved; } catch (error) { /* ohne Speicher gilt 100 % */ }
    const smaller = h("button", { class: "btn btn-s", type: "button", "aria-label": "Text kleiner", title: "Text kleiner", onclick: () => set(step - 1) }, "A−");
    const larger = h("button", { class: "btn btn-s", type: "button", "aria-label": "Text größer", title: "Text größer", onclick: () => set(step + 1) }, "A+");
    const value = h("button", { class: "btn btn-s btn-quiet chat-zoom-value", type: "button", title: "Textgröße zurücksetzen", onclick: () => set(1) });
    function set(next) {
      step = Math.max(0, Math.min(CHAT_ZOOM.length - 1, next));
      // `zoom` vergrößert alles im Verlauf gleichmäßig, auch Tabellen und Code mit fester Schriftgröße.
      for (const node of targets) if (node) node.style.zoom = CHAT_ZOOM[step] === 1 ? "" : String(CHAT_ZOOM[step]);
      value.textContent = Math.round(CHAT_ZOOM[step] * 100) + " %";
      value.setAttribute("aria-label", "Textgröße " + value.textContent + ", zurücksetzen");
      smaller.disabled = step === 0;
      larger.disabled = step === CHAT_ZOOM.length - 1;
      try { localStorage.setItem("devc.chat.zoom", String(CHAT_ZOOM[step])); } catch (error) { /* gilt dann nur für diese Seite */ }
    }
    set(step);
    return h("div", { class: "chat-zoom", role: "group", "aria-label": "Textgröße" }, smaller, value, larger);
  }

  // Zahlen zu einer Unterhaltung (Zeit, Tokens, Werkzeuge). Die Bridge liefert Markdown, devc zeigt es in einem Fenster über dem Chat.
  function chatStats(name, tile, session) {
    const body = h("article", { class: "doc" });
    const note = h("span", { class: "hint" });
    const dialog = h("dialog", { class: "stats", "aria-label": "Statistik der Unterhaltung" });
    async function load() {
      const id = session();
      if (!id) { body.replaceChildren(h("p", {}, "Die Unterhaltung hat noch keine Kennung. Sie entsteht mit der ersten Nachricht.")); return; }
      note.textContent = "lädt …";
      try {
        const data = await api("GET", "/api/containers/" + enc(name) + "/chats/stats?tile=" + enc(tile.ref) + "&session=" + enc(id));
        body.replaceChildren(...mdBlocks(data.text));
        note.textContent = "Stand " + new Date().toLocaleTimeString("de-DE");
      } catch (error) { body.replaceChildren(h("p", { class: "msg error" }, errorText(error))); note.textContent = ""; }
    }
    dialog.append(h("div", { class: "row" }, h("h2", {}, "Statistik"), note, h("span", { class: "grow" }), btn("Aktualisieren", load), btn("Schließen", () => dialog.close(), "primary")), body);
    dialog.addEventListener("close", () => dialog.remove());
    state.cleanup.push(() => dialog.remove());
    const open = btn("Statistik", () => { document.body.append(dialog); dialog.showModal(); load(); });
    open.classList.add("btn-s");
    open.title = "Zeit, Tokens und Werkzeuge dieser Unterhaltung";
    return open;
  }

  // Einstellungen des Agenten (`ready.settings`, SPEC 8.13): Die Bridge nennt sie, devc zeichnet je Einstellung eine Auswahl.
  // Eine Wahl geht sofort als `set` an die Bridge; sie antwortet mit `ready` und dem neuen Stand.
  function chatSettings(info, post) {
    const list = () => (Array.isArray(info.settings) ? info.settings : []).filter((s) => s && typeof s.name === "string" && /^[a-z0-9][a-z0-9_-]{0,39}$/.test(s.name)
      && Array.isArray(s.options) && s.options.length).slice(0, 10);
    const options = (s) => s.options.filter((o) => o && typeof o.value === "string" && o.value.length <= 200).slice(0, 12);
    const text = (value, fallback) => (typeof value === "string" && value ? value.slice(0, 300) : fallback);
    const body = h("div", {});
    const dialog = h("dialog", { class: "chat-settings", "aria-label": "Einstellungen des Chats" });
    const open = btn("Einstellungen", () => { document.body.append(dialog); dialog.showModal(); });
    open.classList.add("btn-s");
    open.hidden = true;
    function draw() {
      const found = list();
      open.hidden = !found.length;
      // Bei genau einer Einstellung steht ihr Stand gleich auf dem Knopf.
      const only = found.length === 1 ? options(found[0]).find((o) => o.value === found[0].value) : null;
      open.textContent = only ? text(found[0].label, found[0].name).slice(0, 30) + ": " + text(only.label, only.value).slice(0, 40) : "Einstellungen";
      body.replaceChildren(...found.map((s) => h("fieldset", { class: "chat-setting" }, h("legend", {}, text(s.label, s.name)),
        ...options(s).map((o) => {
          const radio = h("input", { type: "radio", name: "chat-set-" + s.name, onchange: () => post({ type: "set", name: s.name, value: o.value }) });
          radio.checked = o.value === s.value;
          return h("label", {}, radio, h("span", {}, h("strong", {}, text(o.label, o.value)), typeof o.hint === "string" && o.hint ? h("span", { class: "hint" }, o.hint.slice(0, 300)) : null));
        }),
        typeof s.hint === "string" && s.hint ? h("p", { class: "hint" }, s.hint.slice(0, 300)) : null)));
    }
    dialog.append(h("div", { class: "row" }, h("h2", {}, "Einstellungen"), h("span", { class: "grow" }), btn("Schließen", () => dialog.close(), "primary")), body);
    dialog.addEventListener("close", () => dialog.remove());
    state.cleanup.push(() => dialog.remove());
    return { open, draw };
  }

  function chatFrame(tile, crumbs, badge, agent, actions, log, bar, zoomed) {
    document.body.classList.add("chat-open");
    state.cleanup.push(() => document.body.classList.remove("chat-open"));
    page({ title: tile.title, crumbs, compact: true },
      h("section", { class: "card chat" },
        h("div", { class: "chat-head" }, h("h1", {}, tile.title), badge, agent, h("div", { class: "chat-actions" }, chatZoom([log].concat(zoomed || [])), actions)), log, bar));
  }

  // Eine frühere Unterhaltung nur ansehen. Erst »Fortsetzen« startet sie im Container.
  async function chatShow(name, tile, session, begin) {
    const here = cPath(name, "tile/" + enc(tile.ref));
    const data = await api("GET", "/api/containers/" + enc(name) + "/chats/transcript?tile=" + enc(tile.ref) + "&session=" + enc(session));
    const view = chatLog(null);
    const resume = begin ? btn("Fortsetzen", () => begin(session), "primary") : null;
    chatFrame(tile, cCrumbs(name, [tile.title, here], ["Frühere Unterhaltung"]), h("span", { class: "status" }, "nur ansehen"), null, [chatStats(name, tile, () => session)], view.log,
      h("div", { class: "chat-bar chat-choice" }, h("span", {}, begin ? "Du siehst eine frühere Unterhaltung." : "Du siehst eine frühere Unterhaltung. Gerade läuft eine andere; beende sie, um diese fortzusetzen."),
        h("div", { class: "row" }, resume, link("Zur Übersicht", here))));
    data.events.forEach(view.handle);
    if (!data.events.length) view.add(h("p", { class: "chat-note" }, "Zu dieser Unterhaltung ist kein Verlauf gespeichert."));
    view.log.scrollTop = view.log.scrollHeight;
  }

  function chatRun(name, tile, session, begin) {
    const base = "/api/containers/" + enc(name);
    const here = cPath(name, "tile/" + enc(tile.ref));
    const stateText = h("span", { class: "status" }, CHAT_STATE.starting);
    const agent = h("span", { class: "chat-agent" });
    const input = h("textarea", { class: "inp chat-input", rows: "2", placeholder: "Nachricht … (Strg+Enter sendet)", "aria-label": "Nachricht", "aria-controls": "chat-cmds" });
    const menu = h("div", { class: "chat-cmds", id: "chat-cmds", role: "listbox", "aria-label": "Befehle", hidden: true });
    const sendBtn = btn("Senden", send, "primary");
    const stopBtn = btn("Abbrechen", () => post({ type: "interrupt" }));
    const endBtn = btn("Beenden", end);
    endBtn.classList.add("btn-s");
    stopBtn.hidden = true;
    let ended = false, ws = null;
    function post(command) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(command)); }
    const view = chatLog(post);
    const settings = chatSettings(view.info, post);

    // Das Feld wächst mit dem Text, damit der Verlauf im Normalfall den Platz behält.
    function grow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight + 2, 220) + "px"; }
    function send() {
      const text = input.value;
      if (!text.trim() || ended) return;
      post({ type: "send", text });
      input.value = "";
      grow();
      drawMenu();
      input.focus();
    }

    // Befehle des Agenten (`ready.commands`, SPEC 8.13): Beginnt die Eingabe mit »/«, zeigt ein Popup die passenden Befehle.
    // Steht der Befehl schon da, bleibt seine Zeile als Hilfe zu den Angaben stehen.
    let picks = [], pick = 0, closed = false;
    const commands = () => (Array.isArray(view.info.commands) ? view.info.commands : []).filter((c) => c && typeof c.name === "string" && c.name).slice(0, 300);
    const cmdRow = (c, index) => h("div", { class: "chat-cmd" + (index === pick ? " on" : ""), role: "option", "aria-selected": index === pick ? "true" : "false",
      onmousedown: (event) => { event.preventDefault(); if (index >= 0) choose(c); } },
      h("span", { class: "mono" }, "/" + c.name, typeof c.hint === "string" && c.hint ? h("span", { class: "arg" }, " " + c.hint) : null),
      h("span", { class: "what" }, typeof c.description === "string" ? c.description : ""));
    function drawMenu() {
      const typing = /^\/(\S*)$/.exec(input.value);
      const given = /^\/(\S+)\s/.exec(input.value);
      const all = closed || ended ? [] : commands();
      picks = [];
      let rows = [];
      if (typing) {
        const query = typing[1].toLowerCase();
        const starts = all.filter((c) => c.name.toLowerCase().startsWith(query));
        picks = starts.concat(all.filter((c) => !starts.includes(c) && c.name.toLowerCase().includes(query)));
        pick = Math.min(pick, Math.max(picks.length - 1, 0));
        rows = picks.map(cmdRow);
      } else if (given && !input.value.includes("\n")) {
        rows = all.filter((c) => c.name === given[1]).map((c) => cmdRow(c, -1));
      }
      menu.hidden = !rows.length;
      menu.replaceChildren(...rows);
      const on = menu.querySelector(".on");
      if (on) on.scrollIntoView({ block: "nearest" });
    }
    function choose(c) { input.value = "/" + c.name + " "; pick = 0; drawMenu(); input.focus(); }
    input.addEventListener("input", () => { closed = false; pick = 0; grow(); drawMenu(); });
    input.addEventListener("blur", () => { menu.hidden = true; });
    input.addEventListener("focus", drawMenu);
    // Enter macht eine neue Zeile, wie in jedem Textfeld; gesendet wird mit Strg+Enter (am Mac auch Cmd+Enter).
    input.addEventListener("keydown", (event) => {
      if (event.isComposing) return;
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); send(); return; }
      if (menu.hidden) return;
      if (event.key === "Escape") { event.preventDefault(); closed = true; drawMenu(); return; }
      if (!picks.length) return;
      // Im Popup wählen die Pfeiltasten, Enter oder Tab übernimmt den Befehl.
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); pick = (pick + (event.key === "ArrowDown" ? 1 : picks.length - 1)) % picks.length; drawMenu(); }
      else if (event.key === "Enter" || event.key === "Tab") { event.preventDefault(); choose(picks[pick]); }
    });
    function lock() { input.disabled = sendBtn.disabled = endBtn.disabled = true; stopBtn.hidden = true; }
    // Zeile unter dem Verlauf, solange der Agent arbeitet: was er gerade tut (`status.text`) und wie lange schon.
    // Die Uhr läuft im Browser; die Tätigkeit kommt vom Agenten. So sieht man bei langer Arbeit, dass es weitergeht.
    const doing = h("span", {}), clock = h("span", { class: "chat-clock" });
    const activity = h("div", { class: "chat-activity", role: "status", hidden: true }, h("span", { class: "chat-pulse", "aria-hidden": "true" }), doing, clock);
    // Nach einem Seitenwechsel nennt der Server, wie lange der Agent schon arbeitet; sonst finge die Uhr bei null an.
    const opened = Date.now();
    const busyStart = typeof session.busy_for === "number" ? opened - session.busy_for * 1000 : 0;
    let since = 0, changed = 0;
    const span = (from) => { const s = Math.max(0, Math.floor((Date.now() - from) / 1000)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
    function tick() {
      if (activity.hidden) return;
      // Bleibt dieselbe Tätigkeit lange stehen, steht dabei, seit wann.
      const quiet = Date.now() - changed > 20000 ? " · seit " + span(changed) + " unverändert" : "";
      clock.textContent = span(since) + quiet;
    }
    const ticker = setInterval(tick, 1000);
    state.cleanup.push(() => clearInterval(ticker));
    function setState(value, text) {
      stateText.textContent = CHAT_STATE[value] || value;
      const busy = value === "working" || value === "waiting" || value === "retry";
      stateText.className = "status" + (busy || value === "ending" ? " running" : "");
      stopBtn.hidden = !busy;
      if (busy && activity.hidden) since = busyStart && Date.now() - opened < 1500 ? busyStart : Date.now();
      const label = (typeof text === "string" && text ? text : CHAT_STATE[value] || value).slice(0, 200);
      if (label !== doing.textContent) changed = Date.now();
      doing.textContent = label;
      activity.hidden = !busy;
      activity.classList.toggle("wait", value === "waiting");
      tick();
      if (value === "ending") lock();
    }
    function finish(text) {
      if (ended) return;
      ended = true;
      setState("ended");
      lock();
      view.add(h("div", { class: "chat-end" }, h("span", {}, text), view.info.session ? btn("Fortsetzen", () => begin(view.info.session), "primary") : null, btn("Zur Übersicht", () => go(here))));
    }
    function handle(event) {
      view.handle(event);
      if (event.type === "ready") {
        agent.textContent = [view.info.agent, view.info.version, view.info.model].filter((part) => typeof part === "string" && part).join(" · ");
        agent.title = agent.textContent;
        if (commands().length) input.placeholder = "Nachricht … (Strg+Enter sendet, / zeigt Befehle)";
        settings.draw();
      }
      else if (event.type === "status") setState(event.state, event.text);
      // Jedes Stück Antwort ist ein Lebenszeichen.
      else if (event.type === "text_delta" || event.type === "text") changed = Date.now();
      else if (event.type === "exit") finish("Die Unterhaltung ist beendet.");
    }
    async function end() {
      setState("ending");
      try { await api("DELETE", base + "/chats/" + enc(session.id)); } catch (error) { fail(error); }
    }
    chatFrame(tile, cCrumbs(name, [tile.title]), stateText, agent, [settings.open, chatStats(name, tile, () => view.info.session || session), endBtn], view.log,
      [activity, h("div", { class: "chat-bar" }, menu, input, h("div", { class: "chat-buttons" }, stopBtn, sendBtn))], [activity, input, menu]);
    const scheme = location.protocol === "https:" ? "wss://" : "ws://";
    ws = new WebSocket(scheme + location.host + base + "/chat?session=" + enc(session.id));
    ws.onmessage = (message) => { let event; try { event = JSON.parse(message.data); } catch (error) { return; } handle(event); };
    ws.onclose = () => { if (!ended) { ended = true; lock(); view.add(h("div", { class: "chat-end" }, h("span", {}, "Die Verbindung ist getrennt."), btn("Neu verbinden", route, "primary"))); } };
    state.cleanup.push(() => { ended = true; ws.close(); });
    input.focus();
  }

  // --- Hilfe (SPEC 8.12) -----------------------------------------------------------

  // Kleiner Markdown-Leser für die Hilfetexte: baut DOM-Knoten, setzt nie HTML ein.
  // Kann: Überschriften, Absätze (Zeilenumbruch mit zwei Leerzeichen am Zeilenende), Listen (auch verschachtelt), Tabellen, Code-Blöcke, `Code`, **fett**, [Text](https://…).
  function mdInline(text) {
    const out = [];
    const pattern = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;
    let last = 0;
    for (let m; (m = pattern.exec(text)); last = pattern.lastIndex) {
      if (m.index > last) out.push(text.slice(last, m.index));
      if (m[1] !== undefined) out.push(h("code", {}, m[1]));
      else if (m[2] !== undefined) out.push(h("strong", {}, mdInline(m[2])));
      else if (/^https?:\/\//.test(m[4])) out.push(h("a", { href: m[4], target: "_blank", rel: "noopener noreferrer" }, m[3]));
      else out.push(m[3], " (", h("code", {}, m[4]), ")");
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }
  function mdBlocks(source) {
    const lines = source.replace(/\r/g, "").split("\n");
    const out = [];
    const cells = (line) => line.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
    const itemStart = /^(\s*)([-*]|\d+\.)\s+(.*)$/;
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i += 1; continue; }
      const fence = /^(\s*)```/.exec(line);
      if (fence) {
        const body = [];
        for (i += 1; i < lines.length && !/^\s*```\s*$/.test(lines[i]); i += 1) body.push(lines[i].slice(Math.min(fence[1].length, lines[i].search(/\S|$/))));
        i += 1;
        out.push(h("pre", {}, h("code", {}, body.join("\n"))));
        continue;
      }
      const heading = /^(#{1,4})\s+(.*)$/.exec(line);
      if (heading) { out.push(h("h" + Math.min(heading[1].length + 1, 5), {}, mdInline(heading[2]))); i += 1; continue; }
      if (line.trim().startsWith("|") && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        const head = cells(line);
        const rows = [];
        for (i += 2; i < lines.length && lines[i].trim().startsWith("|"); i += 1) rows.push(cells(lines[i]));
        out.push(h("div", { class: "scroll" }, h("table", {}, h("thead", {}, h("tr", {}, head.map((c) => h("th", { scope: "col" }, mdInline(c))))),
          h("tbody", {}, rows.map((row) => h("tr", {}, row.map((c) => h("td", {}, mdInline(c)))))))));
        continue;
      }
      const first = itemStart.exec(line);
      if (first) {
        const indent = first[1].length;
        const ordered = /\d/.test(first[2]);
        const items = [];
        while (i < lines.length) {
          const start = itemStart.exec(lines[i]);
          if (!start || start[1].length !== indent || /\d/.test(start[2]) !== ordered) break;
          const body = [start[3]];
          // alles Eingerückte danach gehört zum Punkt, auch über Leerzeilen hinweg
          for (i += 1; i < lines.length; i += 1) {
            const blank = !lines[i].trim();
            const deeper = lines[i].search(/\S/) > indent;
            if (blank ? !(i + 1 < lines.length && lines[i + 1].search(/\S/) > indent) : !deeper) break;
            body.push(blank ? "" : lines[i].slice(indent + 2));
          }
          const inner = mdBlocks(body.join("\n"));
          // ein einzelner Absatz steht ohne <p> im Punkt
          items.push(h("li", {}, inner.length === 1 && inner[0].tagName === "P" ? Array.from(inner[0].childNodes) : inner));
        }
        out.push(h(ordered ? "ol" : "ul", {}, items));
        continue;
      }
      const para = [line];
      for (i += 1; i < lines.length && lines[i].trim() && !/^\s*(```|#{1,4}\s|\|)/.test(lines[i]) && !itemStart.test(lines[i]); i += 1) para.push(lines[i]);
      // Zwei Leerzeichen am Ende einer Zeile sind ein Zeilenumbruch; sonst fließen die Zeilen zusammen.
      const parts = [[]];
      para.forEach((raw, index) => { parts[parts.length - 1].push(raw.trim()); if (/ {2,}$/.test(raw) && index < para.length - 1) parts.push([]); });
      out.push(h("p", {}, parts.map((part, index) => [index ? h("br") : null, mdInline(part.join(" "))])));
    }
    return out;
  }

  async function helpPage(id) {
    const topics = (await api("GET", "/api/help")).topics;
    const topic = await api("GET", "/api/help/" + enc(id || topics[0].id));
    async function copy() {
      try { await navigator.clipboard.writeText(topic.text); say("»" + topic.title + "« als Markdown kopiert."); }
      catch (error) { say("Kopieren ist hier nicht möglich; »Herunterladen« benutzen.", "error"); }
    }
    const file = URL.createObjectURL(new Blob([topic.text], { type: "text/markdown;charset=utf-8" }));
    state.cleanup.push(() => URL.revokeObjectURL(file));
    page({ title: "Hilfe", nav: "help", crumbs: [["Hilfe", "#/help"], [topic.title]], sub: topic.summary,
      actions: [btn("Als Markdown kopieren", copy), h("a", { class: "btn", href: file, download: "devc-" + topic.id + ".md" }, "Herunterladen")] },
      h("nav", { class: "tabs", "aria-label": "Hilfethemen" }, topics.map((t) => h("a", { class: "tab-link" + (t.id === topic.id ? " on" : ""), href: "#/help/" + enc(t.id),
        "aria-current": t.id === topic.id ? "page" : null }, t.title))),
      topic.id === "ai" ? hint("Diesen Text kannst du einem KI-Agenten als Anweisung mitgeben: kopieren oder herunterladen und in seine Regeldatei einfügen.") : null,
      h("article", { class: "card doc" }, mdBlocks(topic.text)));
  }

  // --- Container: Addons und Aktionen ---------------------------------------------

  /** Eingabezeilen für die Parameter eines Addons. `inputs`: je Parameter Name, Lesefunktion und Ausgangswert.
   *  Beschriftung, Gruppen und Sichtbarkeit kommen aus der addon.toml (label, group, show_if, option_labels, hidden; SPEC 6.2). */
  function paramRows(params, secretsHref, secretInputs) {
    const inputs = [];
    const readers = {};
    const shown = params.filter((p) => !p.hidden);
    const switches = new Set(shown.filter((p) => p.show_if).map((p) => p.show_if.param));
    const optionText = (p, o, i) => (p.option_labels ? p.option_labels[i] : o === "" ? "(leer)" : o);
    const title = (p) => p.label ? h("span", { class: "param-name" }, p.label, h("span", { class: "mono param-id" }, p.name)) : h("span", { class: "mono" }, p.name);
    const row = (p) => {
      const note = h("span", { class: "hint" }, (p.required ? "Pflicht. " : "") + (p.description || ""));
      if (p.type === "secret" && secretInputs) {
        // Beim Hinzufügen gibt es den Slot noch nicht; der Wert geht mit dem Addon zusammen an devc.
        const secret = h("input", { class: "inp inp-s mono", type: "password", autocomplete: "new-password", placeholder: "Secret, leer = später setzen" });
        secretInputs.push([p.name, secret]);
        return h("label", { class: "param" }, title(p), secret, note);
      }
      if (p.type === "secret") return h("div", { class: "param" }, title(p), h("a", { href: secretsHref }, "Secret – unter Secrets setzen"), note);
      let input, read;
      if (p.type === "choice" && switches.has(p.name)) {
        // Ein Schalter für andere Felder: alle Möglichkeiten sichtbar als Knöpfe.
        const group = "param-" + p.name + "-" + Math.random().toString(36).slice(2, 8);
        const radios = p.options.map((o) => h("input", { type: "radio", name: group, value: o, checked: o === p.value }));
        input = h("span", { class: "param-radios", role: "radiogroup" }, radios.map((r, i) => h("label", { class: "param-radio" }, r, optionText(p, p.options[i], i))));
        read = () => { const on = radios.find((r) => r.checked); return on ? on.value : p.value; };
        inputs.push([p.name, read, read()]);
        readers[p.name] = read;
        return h("div", { class: "param" }, title(p), input, note);
      }
      if (p.type === "bool") { input = h("input", { type: "checkbox" }); input.checked = Boolean(p.value); }
      else if (p.type === "choice") input = h("select", { class: "inp inp-s" }, p.options.map((o, i) => h("option", { value: o, selected: o === p.value }, optionText(p, o, i))));
      else input = h("input", { class: "inp inp-s mono", type: p.type === "int" ? "number" : "text", min: p.min, max: p.max, value: p.value === null || p.value === undefined ? "" : p.value, spellcheck: "false" });
      read = () => (p.type === "bool" ? input.checked : input.value.trim());
      inputs.push([p.name, read, read()]);
      readers[p.name] = read;
      // Lange Texte (zum Beispiel eine Paketliste) bekommen die ganze Breite, der Hinweis rückt darunter.
      if (p.type === "string" && p.max_length > 1024) return h("label", { class: "param param-wide" }, title(p), input, note);
      return h("label", { class: "param" }, title(p), input, note);
    };
    // Ohne Gruppe zuerst, dann je Gruppe ein Kasten in der Reihenfolge des ersten Auftretens.
    const loose = [];
    const groups = new Map();
    const conditional = [];
    for (const p of shown) {
      const el = row(p);
      if (p.show_if) conditional.push([p, el]);
      if (!p.group) loose.push(el);
      else {
        if (!groups.has(p.group)) groups.set(p.group, []);
        groups.get(p.group).push(el);
      }
    }
    const boxes = [...groups].map(([label, els]) => h("fieldset", { class: "param-group" }, h("legend", {}, label), els));
    const rows = loose.concat(boxes);
    if (conditional.length) {
      const update = () => {
        for (const [p, el] of conditional) {
          const read = readers[p.show_if.param];
          el.hidden = Boolean(read) && !p.show_if.values.includes(String(read()));
        }
        for (const box of boxes) box.hidden = [...box.querySelectorAll(".param")].every((el) => el.hidden);
      };
      update();
      for (const el of rows) { el.addEventListener("change", update); el.addEventListener("input", update); }
    }
    return { rows, inputs };
  }

  async function containerAddons(name) {
    const base = "/api/containers/" + enc(name);
    const data = await api("GET", base + "/addons");
    async function run(addon, action) {
      try {
        const result = await api("POST", base + "/actions/" + enc(addon.id + "." + action.id));
        const ok = await follow(result.job, name, { busy: addon.id + ": »" + action.label + "« läuft …", ok: addon.id + ": »" + action.label + "« ausgeführt.", fail: addon.id + ": »" + action.label + "« fehlgeschlagen" });
        const text = ok ? addon.id + ": »" + action.label + "« ausgeführt." : null;
        if (ok) go(cPath(name, "addons"), { text });
      } catch (error) { fail(error); }
    }
    /** Einstellungen eines Addons: ändern, in die devc.toml schreiben und nur für dieses Addon anwenden. */
    function paramForm(addon) {
      if (!addon.params.length) return null;
      const { rows, inputs } = paramRows(addon.params, cPath(name, "secrets"));
      if (!inputs.length) return h("div", { class: "addon-config" }, rows);
      const changed = () => Object.fromEntries(inputs.filter(([, read, was]) => read() !== was).map(([id, read]) => [id, read()]));
      const apply = h("button", { class: "btn btn-s btn-primary", type: "button", disabled: true, onclick: save }, "Übernehmen");
      async function save() {
        apply.disabled = true;
        try {
          const result = await api("PUT", base + "/addons/" + enc(addon.id) + "/params", { params: changed() });
          const ok = await follow(result.job, name, { busy: addon.id + ": Einstellungen werden übernommen …", ok: addon.id + ": Einstellungen übernommen.", fail: addon.id + ": Übernehmen fehlgeschlagen" });
          if (ok) go(cPath(name, "addons"), { text: addon.id + ": Einstellungen übernommen." });
          else apply.disabled = false;
        } catch (error) { apply.disabled = false; fail(error); }
      }
      const form = h("div", { class: "addon-config" }, rows,
        h("div", { class: "row" }, apply));
      const refresh = () => { apply.disabled = !data.running || !Object.keys(changed()).length; };
      form.addEventListener("input", refresh);
      form.addEventListener("change", refresh);
      return form;
    }
    const cards = data.addons.slice().reverse().map((addon) => {
      const active = addon.actions.some((a) => a.status === "running");
      return h("section", { class: "card addon", id: "addon-" + addon.id, tabindex: "-1" },
        h("div", { class: "row" }, h("h2", { class: "name" }, addon.id), h("span", { class: "hint" }, addon.version + " · " + addon.title),
          addon.actions.some((a) => a.detach) ? h("span", { class: "pill" + (active ? " on" : "") }, active ? "läuft" : "gestoppt") : null,
          data.addons.length > 1 ? h("a", { class: "hint jump-top", href: "#", onclick: (event) => { event.preventDefault(); window.scrollTo(0, 0); } }, "↑ nach oben") : null),
        h("div", { class: "row" },
          addon.actions.map((action) => { const b = btn(action.label, () => run(addon, action)); if (action.description) b.title = action.description; if (!data.running) b.disabled = true; return b; }),
          addon.links.map((l) => l.active ? h("a", { class: "btn", href: linkUrl(name, l), target: "_blank", rel: "noopener noreferrer" }, l.label)
            : h("span", { class: "btn", "aria-disabled": "true", title: "Nur aktiv, solange die zugehörige Aktion läuft" }, l.label)),
          !addon.actions.length && !addon.links.length ? h("span", { class: "muted" }, "keine Aktionen") : null),
        h("p", { class: "hint" }, addon.description || addon.title, " · ", h("a", { href: cPath(name, "addons/" + enc(addon.id)) }, "Hilfe zu " + addon.id)),
        addon.links.filter((l) => l.url).map((l) => h("p", { class: "cmd" }, linkUrl(name, l), l.public ? h("strong", {}, " – ohne Anmeldung erreichbar") : null)),
        paramForm(addon));
    });
    page({ title: "Addons", crumbs: cCrumbs(name, ["Addons"]), sub: ["Aktive Addons mit Aktionen und Einstellungen. Entfernen über ", h("a", { href: cPath(name, "config") }, "Konfiguration"), "."],
      actions: [link("Addon hinzufügen", cPath(name, "add-addon"), "primary")] },
      data.running ? null : h("div", { class: "msg warn" }, "Der Container läuft nicht; Aktionen sind erst nach dem Start möglich."),
      cards.length > 1 ? jumpList(data.addons.slice().reverse().map((a) => a.id)) : null,
      cards,
      hint("»Übernehmen« schreibt die Einstellungen eines Addons in die Konfiguration und richtet nur dieses Addon neu ein. Läuft eine Aktion dieses Addons, startet sie mit den neuen Werten neu. Terminal-Sitzungen und andere Addons laufen weiter."),
      hint("Aktionen sind in der addon.toml festgelegt. devc schickt nur den dort hinterlegten Befehl mit eingesetzten Parametern an den Container; freie Befehle gibt es hier nicht. Ergebnis als Meldung, Ausgabe im ", h("a", { href: cPath(name, "log") }, "Log"), "."));
  }

  /** Sprungliste über den Karten. Kein Anker im Hash: Der Hash ist hier die Adresse der Seite. */
  function jumpList(ids) {
    const jump = (id) => (event) => {
      event.preventDefault();
      const target = document.getElementById("addon-" + id);
      if (target) { target.scrollIntoView({ block: "start" }); target.focus({ preventScroll: true }); }
    };
    return h("nav", { class: "jump", "aria-label": "Springen zu" }, h("span", { class: "muted" }, "Springen zu:"),
      ids.map((id) => h("a", { class: "mono", href: "#", onclick: jump(id) }, id)));
  }

  /** Addon hinzufügen, Schritt 1: Auswahl aus allen Addons (dieselbe Liste wie auf der Seite »Addons« von devc). */
  async function addonPick(name) {
    const [all, mine] = await Promise.all([api("GET", "/api/addons"), api("GET", "/api/containers/" + enc(name) + "/addons")]);
    const active = new Set(mine.addons.map((a) => a.id));
    const rows = all.addons.map((a) => [
      [mono(a.id), h("div", { class: "hint" }, a.description)], a.title, a.version, a.origin_label,
      active.has(a.id) ? h("span", { class: "pill on" }, "aktiv") : h("a", { class: "btn btn-s btn-primary", href: cPath(name, "add-addon/" + enc(a.id)) }, "Auswählen")]);
    page({ title: "Addon hinzufügen", crumbs: cCrumbs(name, ["Addons", cPath(name, "addons")], ["Hinzufügen"]), sub: "Wähle ein Addon für " + name + ". Im nächsten Schritt trägst du die Einstellungen ein.",
      actions: [link("Abbrechen", cPath(name, "addons"))] },
      mine.running ? null : h("div", { class: "msg warn" }, "Der Container läuft nicht. Starte ihn zuerst; ein Addon lässt sich nur im laufenden Container einrichten."),
      flush(null, table(["Addon", "Name", "Version", "Herkunft", ""], rows)),
      hint("Eigene Addons lädst du unter ", h("a", { href: "#/addons" }, "Addons"), " hoch."));
  }

  /** Addon hinzufügen, Schritt 2: Anleitung lesen, Einstellungen eintragen, einrichten. */
  async function addonAdd(name, id) {
    const base = "/api/containers/" + enc(name);
    const [addon, all, mine] = await Promise.all([api("GET", "/api/addons/" + enc(id)), api("GET", "/api/addons"), api("GET", base + "/addons")]);
    const active = new Set(mine.addons.map((a) => a.id));
    if (active.has(id)) { go(cPath(name, "addons"), { text: id + " ist in " + name + " schon aktiv." }); return; }
    // Was das Addon braucht, kommt mit; die Reihenfolge der Einrichtung bestimmt devc.
    const known = new Set(all.addons.map((a) => a.id));
    const needed = (addon.requires || []).filter((r) => !active.has(r) && r !== "base");
    const missing = needed.filter((r) => !known.has(r));
    const clash = (addon.conflicts || []).filter((c) => active.has(c));
    const params = addon.params.map((p) => Object.assign({}, p, { value: p.default }));
    const secretInputs = [];
    const { rows, inputs } = paramRows(params, cPath(name, "secrets"), secretInputs);
    const add = btn("Hinzufügen und einrichten", save, "primary");
    add.disabled = !mine.running || Boolean(clash.length) || Boolean(missing.length);
    async function save() {
      add.disabled = true;
      const values = Object.fromEntries(inputs.filter(([, read, was]) => read() !== was).map(([key, read]) => [key, read()]));
      try {
        const given = Object.fromEntries(secretInputs.filter(([, input]) => input.value).map(([key, input]) => [id + "." + key, input.value]));
        const open = secretInputs.filter(([, input]) => !input.value).map(([key]) => id + "." + key);
        const result = await api("POST", base + "/addons", { addons: needed.concat([id]), params: Object.keys(values).length ? { [id]: values } : {}, secrets: given });
        const ok = await follow(result.job, name, { busy: id + " wird eingerichtet …", ok: id + " hinzugefügt.", fail: id + ": Einrichten fehlgeschlagen" });
        if (ok) go(cPath(name, "addons"), { text: id + " hinzugefügt." + (open.length ? " Noch ohne Wert: " + open.join(", ") + ". Setzen unter Secrets." : "") });
        else add.disabled = false;
      } catch (error) { add.disabled = false; fail(error); }
    }
    page({ title: addon.id, mono: true, crumbs: cCrumbs(name, ["Addons", cPath(name, "addons")], ["Hinzufügen", cPath(name, "add-addon")], [addon.id]),
      sub: addon.version + " · " + addon.title + (addon.description ? " · " + addon.description : ""), actions: [link("Zurück zur Auswahl", cPath(name, "add-addon"))] },
      mine.running ? null : h("div", { class: "msg warn" }, "Der Container läuft nicht. Starte ihn zuerst."),
      clash.length ? h("div", { class: "msg error" }, "Geht nicht zusammen mit: " + clash.join(", ") + ". Dieses Addon ist im Container aktiv.") : null,
      missing.length ? h("div", { class: "msg error" }, "Braucht ein Addon, das es hier nicht gibt: " + missing.join(", ") + ".") : null,
      card("Einstellungen",
        needed.length && !missing.length ? h("p", {}, "Kommt mit, weil " + id + " es braucht: ", needed.map((r) => [h("span", { class: "chip" }, r), " "])) : null,
        rows.length ? h("div", { class: "addon-config addon-first" }, rows) : h("p", { class: "muted" }, "Dieses Addon hat keine Einstellungen."),
        secretInputs.length ? hint("Ein Secret kannst du hier gleich eintragen oder später unter Secrets setzen, dort auch aus dem Secret-Store.") : null,
        h("div", { class: "row" }, add),
        hint("Die Werte kannst du später auf der Seite Addons ändern. Hinzufügen schreibt die Konfiguration neu; Kommentare in der devc.toml gehen dabei verloren. Der Container startet nicht neu.")),
      h("article", { class: "card doc" }, mdBlocks(addon.readme)));
  }

  /** Hilfe zu einem Addon des Containers: README und Parameter. */
  async function addonHelp(name, id) {
    const data = await api("GET", "/api/containers/" + enc(name) + "/addons");
    const addon = data.addons.find((a) => a.id === id);
    if (!addon) throw { error: "E_NOT_FOUND", message: "Addon " + id + " ist in " + name + " nicht aktiv", details: {} };
    const rows = addon.params.map((p) => [[mono(p.name), p.label ? h("div", { class: "hint" }, p.label) : null], p.type, p.type === "secret" ? "–" : mono(String(p.default === null || p.default === undefined ? "" : p.default)), p.description]);
    page({ title: addon.id, mono: true, crumbs: cCrumbs(name, ["Addons", cPath(name, "addons")], [addon.id]), sub: addon.version + " · " + addon.title + (addon.description ? " · " + addon.description : "") },
      h("article", { class: "card doc" }, mdBlocks(addon.readme)),
      rows.length ? flush("Parameter", table(["Name", "Typ", "Standard", "Bedeutung"], rows)) : null,
      hint("Die Werte änderst du auf der Seite ", h("a", { href: cPath(name, "addons") }, "Addons"), " oder unter ", h("a", { href: cPath(name, "config") }, "Konfiguration"), "."));
  }

  // --- Container: Terminal -----------------------------------------------------------

  function terminalPage(name) {
    return terminalView({ title: "Terminal", crumbs: cCrumbs(name, ["Terminal"]), base: "/api/containers/" + enc(name), host: name, key: name, users: ["dev", "root"],
      command: "devc shell " + name, ends: "mit dem Container" });
  }

  // Terminal-Sitzungen in Tabs. `view` nennt die Adresse der API und die Texte; dieselbe Seite dient dem Container und der VM (SPEC 8.7, 8.14).
  async function terminalView(view) {
    const status = state.status;
    const { crumbs, command, base } = view;
    const name = view.host;
    if (!status.terminal || typeof window.Terminal !== "function") {
      page({ title: view.title, crumbs },
        card("Web-Terminal nicht verfügbar", h("p", {}, status.terminal_allowed === false
          ? "Das Web-Terminal ist für diesen Bind nicht freigegeben."
          : "xterm.js liegt dem Server nicht bei oder das Terminal ist in der Konfiguration abgeschaltet."),
          h("p", {}, "Ohne Browser: ", mono(command))));
      return;
    }
    const user = h("select", { class: "inp", "aria-label": "Benutzer der neuen Sitzung" }, view.users.map((u) => h("option", { value: u }, u)));
    user.hidden = view.users.length < 2;
    const holder = h("div", { class: "term" });
    const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "Terminal-Sitzungen" });
    const info = h("span", {}, "");
    // Sitzungen leben im Server: Neuladen oder Wegnavigieren trennt nur die Anzeige, ein Tab verbindet wieder.
    let sessions = [], active = null, socket = null, term = null;
    const storeKey = "devc.terminal." + view.key;
    function remember(id) { try { if (id) localStorage.setItem(storeKey, id); else localStorage.removeItem(storeKey); } catch (error) { /* ohne Speicher gilt der erste Tab */ } }
    function recall() { try { return localStorage.getItem(storeKey); } catch (error) { return null; } }
    function cellSize() {
      // xterm kennt die genaue Zellgröße; die Hilfsmessung greift nur, falls das interne Feld fehlt.
      try {
        const cell = term._core._renderService.dimensions.css.cell;
        if (cell.width > 0 && cell.height > 0) return cell;
      } catch (error) { /* weiter mit der Messung */ }
      // Das Mess-Element von xterm enthält mehrere Zeichen: Breite durch die Zeichenzahl teilen.
      const probe = holder.querySelector(".xterm-char-measure-element");
      const box = probe ? probe.getBoundingClientRect() : null;
      const count = probe ? Math.max(1, probe.textContent.length) : 1;
      return { width: box && box.width ? box.width / count : 8.4, height: box && box.height ? box.height : 17 };
    }
    function fit() {
      // Das Terminal füllt das Fenster bis zum unteren Rand, wie ein Terminalfenster auf dem Desktop.
      if (document.fullscreenElement === holder) { holder.style.height = ""; return; }
      const top = holder.getBoundingClientRect().top + window.scrollY;
      holder.style.height = Math.max(240, window.innerHeight - top - 64) + "px";
    }
    function size() {
      if (!term) return;
      const cell = cellSize();
      const style = getComputedStyle(holder);
      const scrollbar = 18;
      const width = holder.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - scrollbar;
      const height = holder.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
      const cols = Math.max(20, Math.floor(width / cell.width));
      const rows = Math.max(5, Math.floor(height / cell.height));
      if (cols !== term.cols || rows !== term.rows) term.resize(cols, rows);
      if (socket && socket.readyState === 1) socket.send(JSON.stringify({ type: "resize", cols, rows }));
    }
    function detach() { if (socket) { const old = socket; socket = null; old.close(); } }
    function drawTabs() {
      tabs.replaceChildren(...sessions.map((s) => {
        const title = s.user + " " + s.number;
        return h("div", { class: "tab" + (s.id === active ? " on" : "") },
          h("button", { class: "tab-name", type: "button", role: "tab", "aria-selected": s.id === active ? "true" : "false", onclick: () => attach(s.id, false) }, title),
          h("button", { class: "tab-close", type: "button", "aria-label": "Sitzung " + title + " beenden", title: "Sitzung beenden", onclick: () => closeTab(s.id) }, "×"));
      }), h("button", { class: "tab-new", type: "button", "aria-label": "Neue Sitzung", title: "Neue Sitzung als " + user.value, onclick: create }, "+"));
    }
    function attach(id, fresh) {
      const session = sessions.find((s) => s.id === id);
      if (!session) return;
      if (id === active && socket) { term.focus(); return; }
      detach();
      active = id;
      remember(id);
      drawTabs();
      if (term) term.dispose();
      term = new window.Terminal({ cursorBlink: true, fontSize: 14, fontFamily: 'ui-monospace, "IBM Plex Mono", Menlo, Consolas, monospace', scrollback: 5000 });
      holder.replaceChildren();
      term.open(holder);
      fit();
      size();
      const scheme = location.protocol === "https:" ? "wss://" : "ws://";
      const ws = new WebSocket(scheme + location.host + base + "/terminal?session=" + enc(id));
      socket = ws;
      ws.binaryType = "arraybuffer";
      const encoder = new TextEncoder();
      info.textContent = "verbinde …";
      ws.onopen = () => {
        info.textContent = "verbunden als " + session.user + "@" + name;
        if (fresh) size();
        else {
          // Gleiche Größe löst im Container kein Neuzeichnen aus: einmal eine Zeile weniger, dann die echte Größe.
          ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: Math.max(5, term.rows - 1) }));
          setTimeout(() => { if (socket === ws) size(); }, 150);
        }
        term.focus();
      };
      ws.onmessage = (event) => { if (typeof event.data !== "string") term.write(new Uint8Array(event.data)); };
      // Eine bereits ersetzte Verbindung ändert die Anzeige nicht mehr.
      ws.onclose = () => { if (socket === ws) { socket = null; refresh().catch(() => { info.textContent = "getrennt – Server nicht erreichbar"; }); } };
      term.onData((data) => { if (ws.readyState === 1) ws.send(encoder.encode(data)); });
    }
    function showNone() {
      active = null;
      remember(null);
      if (term) { term.dispose(); term = null; }
      holder.replaceChildren(h("p", { class: "term-empty" }, "Keine Sitzung offen. Mit + oder »Neue Sitzung« öffnest du eine."));
      info.textContent = "";
      drawTabs();
    }
    // Gleicht die Tabs mit dem Server ab, etwa nachdem eine Shell mit `exit` beendet wurde.
    async function refresh() {
      sessions = (await api("GET", base + "/terminals")).sessions;
      if (!sessions.length) { showNone(); return; }
      if (sessions.some((s) => s.id === active)) {
        drawTabs();
        if (!socket) info.textContent = "getrennt – Tab anklicken, um wieder zu verbinden";
        return;
      }
      active = null;
      attach(sessions[sessions.length - 1].id, false);
    }
    async function create() {
      try {
        const session = await api("POST", base + "/terminals", { user: user.value });
        sessions.push(session);
        attach(session.id, true);
      } catch (error) { say(error.message, "error"); }
    }
    async function closeTab(id) {
      if (id === active) { detach(); active = null; }
      try { await api("DELETE", base + "/terminals/" + enc(id)); } catch (error) { /* schon beendet */ }
      sessions = sessions.filter((s) => s.id !== id);
      if (active) drawTabs();
      else if (sessions.length) attach(sessions[sessions.length - 1].id, false);
      else showNone();
    }
    // Jede Größenänderung des Fensters oder der Terminalfläche passt Spalten und Zeilen an.
    const onResize = debounce(() => { fit(); size(); }, 80);
    window.addEventListener("resize", onResize);
    document.addEventListener("fullscreenchange", onResize);
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(debounce(size, 80)) : null;
    if (observer) observer.observe(holder);
    main.classList.add("wide");
    state.cleanup.push(() => {
      window.removeEventListener("resize", onResize);
      document.removeEventListener("fullscreenchange", onResize);
      if (observer) observer.disconnect();
      main.classList.remove("wide");
      detach();  // die Sitzungen laufen im Server weiter
      if (term) term.dispose();
    });
    user.addEventListener("change", drawTabs);
    sessions = (await api("GET", base + "/terminals")).sessions;
    page({ title: view.title, crumbs, sub: info,
      actions: [user, btn("Neue Sitzung", create), btn("Vollbild", () => { if (holder.requestFullscreen) holder.requestFullscreen(); }), view.actions || []] },
      tabs, holder,
      view.note ? hint(view.note) : null,
      hint("Sitzungen laufen weiter, wenn du die Seite neu lädst oder verlässt; × beendet eine Sitzung. Sie enden auch mit »exit«, " + view.ends + " und mit dem Server. Beginn und Ende werden im Log festgehalten, Inhalte nicht. Ohne Browser: ", mono(command)));
    if (sessions.length) attach((sessions.find((s) => s.id === recall()) || sessions[0]).id, false);
    else await create();
  }

  // --- VM: beschränktes Terminal und Update von devc (SPEC 8.14) --------------------------

  const vmBuild = (info) => "devc " + info.version + (info.build ? " · " + info.build : "");

  async function vmPage() {
    const info = await api("GET", "/api/vm");
    const update = info.update ? link("devc aktualisieren …", "#/vm/update") : null;
    if (!info.terminal) {
      page({ title: "VM", crumbs: [["VM"]], sub: vmBuild(info), actions: [update] },
        card("Terminal der VM nicht verfügbar", h("p", {}, info.allowed
          ? "Das Terminal der VM ist hier nicht eingerichtet. Das macht die Einrichtung der VM (vm/install.sh)."
          : "Das Terminal der VM ist wie das Web-Terminal für diesen Bind nicht freigegeben.")));
      return;
    }
    return terminalView({ title: "VM", crumbs: [["VM"]], base: "/api/vm", host: "vm", key: "@vm", users: [info.user], command: "devc vm shell", ends: "mit einem Update",
      actions: [update],
      note: ["Beschränkte Shell in der VM, in der devc läuft. Erlaubt sind: ", mono(info.commands.join(" ")), ". Es gibt kein ", mono("cd"), " und keine Umleitung in Dateien; Pipes gehen. ", mono("commands"), " zeigt die Liste."] });
  }

  async function vmUpdatePage() {
    const info = await api("GET", "/api/vm");
    const crumbs = [["VM", "#/vm"], ["devc aktualisieren"]];
    if (!info.update) {
      page({ title: "devc aktualisieren", crumbs },
        card("Update nicht verfügbar", h("p", {}, info.allowed ? "Das Update ist hier nicht eingerichtet. Das macht die Einrichtung der VM (vm/install.sh)." : "Das Update ist wie das Web-Terminal für diesen Bind nicht freigegeben.")));
      return;
    }
    const file = h("input", { class: "inp", type: "file", accept: ".pyz", "aria-label": "Neues devc.pyz" });
    const send = btn("Hochladen und aktualisieren", upload, "primary");
    const box = h("div", { class: "stack" });
    let timer = null;
    state.cleanup.push(() => clearTimeout(timer));
    // Nach dem Start des Updates: warten, bis die neue Fassung antwortet, dann die Seite frisch laden (auch app.js ist neu).
    // Der Neustart meldet alle Browser ab; die Antwort ist dann E_AUTH, und nach dem Neuladen steht die Anmeldung da.
    function wait(result) {
      const started = Date.now();
      let down = false;
      const text = h("p", {}, "Das Update läuft. devc startet gleich neu …");
      box.replaceChildren(h("div", { class: "msg busy", role: "status" }, text));
      async function poll() {
        let now = null;
        let again = false;
        try { now = await api("GET", "/api/vm", null, { login: true }); } catch (error) { again = down && error.error === "E_AUTH"; down = true; }
        if (again || (now && now.build === result.to.build)) { location.hash = "#/vm"; location.reload(); return; }
        if (down && !now) text.textContent = "devc startet neu …";
        if (Date.now() - started > 120000) {
          box.replaceChildren(h("div", { class: "msg error", role: "alert" }, "Die neue Fassung meldet sich nicht. Sieh in der VM nach: ", mono("journalctl -u 'devc-update-*' -n 50")));
          return;
        }
        timer = setTimeout(poll, 1500);
      }
      timer = setTimeout(poll, 1500);
    }
    async function upload() {
      if (!file.files.length) { say("Zuerst ein devc.pyz auswählen.", "error"); return; }
      const ok = await confirmDialog("devc aktualisieren", "Der Dienst startet neu. Alle Terminal-Sitzungen und Unterhaltungen enden, auch die im VM-Terminal. Du meldest dich danach neu an. Container laufen weiter.", "Aktualisieren");
      if (!ok) return;
      send.disabled = file.disabled = true;
      try { wait(await api("POST", "/api/vm/update", file.files[0], { type: "application/octet-stream" })); }
      catch (error) { send.disabled = file.disabled = false; fail(error); }
    }
    page({ title: "devc aktualisieren", crumbs, sub: "Es läuft " + vmBuild(info) },
      card("Neue Fassung einspielen", h("div", { class: "row" }, h("div", { style: "flex: 1 1 260px" }, file), send), box),
      hint("Du lädst ein neues ", mono("devc.pyz"), " hoch. devc prüft die Datei, richtet sie ein und startet neu. Container und ihre Daten bleiben, wie sie sind. Ohne Browser: ", mono("devc vm update <devc.pyz>")));
  }

  // --- Container: Dateien ----------------------------------------------------------------

  async function filesPage(name, query) {
    const base = "/api/containers/" + enc(name);
    const path = query.get("path") || "/work";
    const data = await api("GET", base + "/files?path=" + enc(path));
    const here = data.path;
    const raw = (target) => base + "/files/raw?path=" + enc(target);
    const linkTo = (target) => cPath(name, "files?path=" + enc(target));
    const rows = [];
    if (here !== "/work") rows.push([h("a", { href: linkTo(here.split("/").slice(0, -1).join("/")), class: "mono" }, ".."), "", "", ""]);
    for (const entry of data.entries) {
      const target = here + "/" + entry.name;
      rows.push([
        entry.type === "dir" ? h("a", { href: linkTo(target), class: "mono" }, entry.name + "/")
          : entry.type === "file" ? h("a", { href: cPath(name, "file?path=" + enc(target)), class: "mono", title: "Ansehen" }, entry.name) : mono(entry.name),
        entry.type === "dir" ? "Ordner" : size(entry.size), when(entry.modified),
        rowMenu(entry, target)]);
    }
    async function remove(entry, target) {
      const text = target + (entry.type === "dir" ? " samt Inhalt" : "") + " löschen? Das lässt sich nicht rückgängig machen.";
      if (!(await confirmDialog(entry.type === "dir" ? "Ordner löschen" : "Datei löschen", text, "Löschen"))) return;
      try { await api("DELETE", raw(target)); go(linkTo(here), { text: target + " gelöscht." }); } catch (error) { fail(error); }
    }
    /** Das Menü »…« einer Zeile: Herunterladen, Entpacken (nur Archive), Löschen. */
    function rowMenu(entry, target) {
      const list = h("div", { class: "menu-list", role: "menu" },
        entry.type === "link" ? null : h("a", { class: "menu-item", role: "menuitem", href: raw(target), download: "" }, entry.type === "dir" ? "Als tar.gz herunterladen" : "Herunterladen"),
        entry.type === "file" && ARCHIVE_NAME.test(entry.name) ? h("a", { class: "menu-item", role: "menuitem", href: cPath(name, "unpack?path=" + enc(target)) }, "Entpacken …") : null,
        h("button", { class: "menu-item danger", type: "button", role: "menuitem", onclick: () => { menu.open = false; remove(entry, target); } }, "Löschen …"));
      const menu = h("details", { class: "menu" }, h("summary", { class: "btn btn-s", "aria-label": "Aktionen für " + entry.name }, "…"), list);
      menu.addEventListener("toggle", () => {
        if (!menu.open) return;
        for (const other of document.querySelectorAll("details.menu[open]")) if (other !== menu) other.open = false;
        // fest am Fenster verankert: die Tabelle schneidet sonst das Menü der letzten Zeile ab
        const box = menu.getBoundingClientRect();
        list.style.top = Math.min(box.bottom + 2, window.innerHeight - list.offsetHeight - 8) + "px";
        list.style.left = Math.max(8, box.right - list.offsetWidth) + "px";
      });
      list.addEventListener("click", (event) => { if (event.target.closest("a")) menu.open = false; });
      return menu;
    }
    const closeMenus = (event) => { for (const menu of document.querySelectorAll("details.menu[open]")) if (event.type !== "click" || !menu.contains(event.target)) menu.open = false; };
    document.addEventListener("click", closeMenus);
    window.addEventListener("scroll", closeMenus, true);
    state.cleanup.push(() => { document.removeEventListener("click", closeMenus); window.removeEventListener("scroll", closeMenus, true); });
    const file = h("input", { class: "inp inp-s", type: "file", multiple: true, "aria-label": "Dateien zum Hochladen" });
    async function upload() {
      if (!file.files.length) { say("Zuerst Dateien auswählen.", "error"); return; }
      try {
        for (const item of file.files) await api("PUT", raw(here + "/" + item.name), item, { type: "application/octet-stream" });
        go(linkTo(here), { text: file.files.length + " Datei(en) nach " + here + " hochgeladen." });
      } catch (error) { fail(error); }
    }
    page({ title: "Dateien", compact: true, crumbs: cCrumbs(name, ["Dateien"]) },
      h("div", { class: "fv-bar" }, pathTrail(here, linkTo), file, btn("Hochladen", upload, "s"),
        h("a", { class: "btn btn-s", href: raw(here), download: "" }, "Ordner als tar")),
      h("section", { class: "card flush dense files-list" }, table(["Name", "Größe", "Geändert", ""], rows, "Leerer Ordner.")),
      hint("Nur unterhalb von /work. Ein Klick auf eine Datei öffnet sie zum Ansehen und Bearbeiten. Das Menü »…« bietet Herunterladen, Löschen und bei tar- und zip-Archiven Entpacken. Hochladen ersetzt Dateien gleichen Namens."));
  }

  // --- Container: Datei ansehen und bearbeiten ----------------------------------------------

  /** Pfad als Kette von Links auf die Ordner; der letzte Teil ist Text. */
  function pathTrail(path, linkTo) {
    const parts = path.split("/").filter(Boolean);
    return h("div", { class: "fv-path" }, parts.map((part, index) => [index ? h("span", { class: "sep" }, " / ") : null,
      index < parts.length - 1 ? h("a", { href: linkTo("/" + parts.slice(0, index + 1).join("/")) }, index ? part : "/" + part) : (index ? part : "/" + part)]));
  }

  // Bildformate, die jeder gängige Browser darstellt. Alles andere ist Text oder Hex.
  const IMAGE_TYPES = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp",
    svg: "image/svg+xml", bmp: "image/bmp", ico: "image/x-icon", avif: "image/avif" };
  const VIEW_MAX = 20 * 1024 * 1024;  // darüber nur Herunterladen
  const TEXT_MAX = 2 * 1024 * 1024;   // darüber kein Text, nur Hex
  const HEX_CHUNK = 64 * 1024;
  const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, "0"));

  /** Lädt eine Datei roh. Ist sie größer als `limit`, bleibt `bytes` leer. */
  async function fetchBytes(url, limit) {
    let response;
    try { response = await fetch(url, { credentials: "same-origin" }); }
    catch (error) { throw { error: "E_NETWORK", message: "Server nicht erreichbar", details: {} }; }
    if (!response.ok) {
      let failure = null;
      try { failure = await response.json(); } catch (error) { /* keine JSON-Antwort */ }
      if (response.status === 401) showLogin();
      throw (failure && failure.error) ? failure : { error: "E_INTERNAL", message: "Unerwartete Antwort (" + response.status + ")", details: {} };
    }
    const length = Number(response.headers.get("Content-Length") || 0);
    const folder = (response.headers.get("Content-Type") || "") === "application/gzip";  // ein Ordner kommt als tar.gz
    if (folder || length > limit) { if (response.body) response.body.cancel(); return { length, folder, bytes: null }; }
    return { length, folder, bytes: new Uint8Array(await response.arrayBuffer()) };
  }
  function decodeText(bytes) {
    if (bytes.length > TEXT_MAX || bytes.includes(0)) return null;
    try { return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes); } catch (error) { return null; }
  }
  function hexDump(bytes, from, to) {
    const out = [];
    for (let row = from; row < to; row += 16) {
      let hex = "", text = "";
      for (let i = row; i < row + 16; i += 1) {
        if (i - row === 8) hex += " ";
        if (i < to) { const b = bytes[i]; hex += HEX[b] + " "; text += b >= 32 && b < 127 ? String.fromCharCode(b) : "."; }
        else hex += "   ";
      }
      out.push(row.toString(16).padStart(8, "0") + "  " + hex + " " + text);
    }
    return out.join("\n");
  }

  async function filePage(name, query) {
    const path = query.get("path") || "";
    const raw = "/api/containers/" + enc(name) + "/files/raw?path=" + enc(path);
    const folderOf = (target) => cPath(name, "files?path=" + enc(target));
    const loaded = await fetchBytes(raw, VIEW_MAX);
    if (loaded.folder) { go(folderOf(path)); return; }
    const fileName = path.split("/").pop();
    const parent = path.split("/").slice(0, -1).join("/") || "/work";
    const ext = (/\.([A-Za-z0-9]+)$/.exec(fileName) || ["", ""])[1].toLowerCase();
    const imageType = IMAGE_TYPES[ext] || null;
    let bytes = loaded.bytes;
    const text = bytes ? decodeText(bytes) : null;
    // Windows-Zeilenenden bleiben beim Speichern erhalten; das Textfeld des Browsers kennt nur \n.
    const crlf = text !== null && text.includes("\r\n") && !/(^|[^\r])\n/.test(text);
    let baseline = text === null ? null : (crlf ? text.replace(/\r\n/g, "\n") : text);
    let draft = null;  // ungespeicherte Fassung; null = keine Änderung
    const current = () => (draft !== null ? draft : baseline);
    const modes = [];
    if (bytes && imageType) modes.push(["image", "Bild"]);
    if (text !== null) modes.push(["text", "Text"], ["markdown", "Markdown"]);
    if (bytes) modes.push(["hex", "Hex"]);
    let mode = !bytes ? "none" : imageType ? "image" : text === null ? "hex" : (ext === "md" || ext === "markdown") ? "markdown" : "text";
    let editing = false;
    let zoom = "fit", scale = 1, natural = null, relayout = () => {};
    const imageUrl = bytes && imageType ? URL.createObjectURL(new Blob([bytes], { type: imageType })) : null;
    const bar = h("div", { class: "fv-bar" });
    const body = h("div", { class: "fv-body" });
    const zoomLabel = h("span", { class: "fv-note fv-zoom", "aria-live": "polite" });
    const small = (label, onclick, on, title) => h("button", { class: "btn btn-s" + (on ? " on" : ""), type: "button", onclick, title,
      "aria-pressed": on === undefined ? null : String(Boolean(on)) }, label);
    const textual = () => mode === "text" || mode === "markdown";

    function drawBar() {
      const tools = [];
      if (modes.length > 1) tools.push(h("div", { class: "seg", role: "group", "aria-label": "Darstellung" },
        modes.map(([id, label]) => small(label, () => { mode = id; drawBar(); drawBody(); }, mode === id))));
      if (textual()) {
        tools.push(h("div", { class: "seg", role: "group", "aria-label": "Ansehen oder bearbeiten" },
          small("Ansicht", () => { editing = false; drawBar(); drawBody(); }, !editing),
          small("Bearbeiten", () => { editing = true; drawBar(); drawBody(); }, editing)));
        if (draft !== null) tools.push(h("span", { class: "fv-note dirty" }, "nicht gespeichert"),
          h("button", { class: "btn btn-s btn-primary", type: "button", onclick: save, title: "Strg+S" }, "Speichern"), small("Verwerfen", discard));
      }
      if (mode === "image") tools.push(h("div", { class: "seg", role: "group", "aria-label": "Zoom" },
        small("−", () => zoomBy(1 / 1.25), undefined, "Verkleinern"), small("100 %", () => { zoom = 1; relayout(); drawBar(); }, zoom === 1),
        small("+", () => zoomBy(1.25), undefined, "Vergrößern"), small("Fit", () => { zoom = "fit"; relayout(); drawBar(); }, zoom === "fit", "An das Fenster anpassen")), zoomLabel);
      bar.replaceChildren(pathTrail(path, folderOf), h("span", { class: "fv-note" }, size(bytes ? bytes.length : loaded.length)), ...tools,
        h("a", { class: "btn btn-s", href: raw, download: "" }, "Laden"));
    }

    function drawBody() {
      relayout = () => {};
      body.className = "fv-body fv-" + mode + (editing && textual() ? " fv-editing" : "");
      body.scrollTop = body.scrollLeft = 0;
      if (mode === "none") {
        body.replaceChildren(h("p", { class: "empty" }, "Die Datei ist mit " + size(loaded.length) + " zu groß für die Anzeige (Grenze " + size(VIEW_MAX) + "). Mit »Laden« holst du sie auf deinen Rechner."));
      } else if (mode === "image") {
        const img = h("img", { src: imageUrl, alt: fileName, draggable: "false" });
        relayout = () => {
          if (!natural) return;
          const fit = Math.min(1, (body.clientWidth - 16) / natural.w, (body.clientHeight - 16) / natural.h);
          // den Bildpunkt in der Mitte des Fensters beim Zoomen festhalten
          const cx = (body.scrollLeft + body.clientWidth / 2) / (body.scrollWidth || 1), cy = (body.scrollTop + body.clientHeight / 2) / (body.scrollHeight || 1);
          scale = zoom === "fit" ? Math.max(fit, 0.01) : zoom;
          img.style.width = Math.max(1, Math.round(natural.w * scale)) + "px";
          img.style.height = Math.max(1, Math.round(natural.h * scale)) + "px";
          body.scrollLeft = cx * body.scrollWidth - body.clientWidth / 2;
          body.scrollTop = cy * body.scrollHeight - body.clientHeight / 2;
          zoomLabel.textContent = Math.round(scale * 100) + " %";
        };
        img.addEventListener("load", () => { natural = { w: img.naturalWidth || 300, h: img.naturalHeight || 150 }; relayout(); });
        img.addEventListener("error", () => body.replaceChildren(h("p", { class: "empty" }, "Der Browser kann dieses Bild nicht darstellen. »Hex« zeigt den Inhalt.")));
        body.replaceChildren(img);
      } else if (mode === "hex") {
        let shown = 0;
        const more = small("", () => add());
        const add = () => {
          const end = Math.min(bytes.length, shown + HEX_CHUNK);
          more.before(h("pre", { class: "fv-hexdump" }, hexDump(bytes, shown, end)));
          shown = end;
          more.textContent = "Weitere " + size(Math.min(HEX_CHUNK, bytes.length - shown)) + " zeigen (" + size(shown) + " von " + size(bytes.length) + ")";
          more.hidden = shown >= bytes.length;
        };
        body.replaceChildren(bytes.length ? more : h("p", { class: "empty" }, "Die Datei ist leer."));
        if (bytes.length) add();
      } else if (editing) {
        const area = h("textarea", { class: "fv-edit", spellcheck: "false", autocapitalize: "off", autocomplete: "off", "aria-label": "Inhalt von " + fileName });
        area.value = current();
        area.addEventListener("input", () => {
          const was = draft !== null;
          draft = area.value === baseline ? null : area.value;
          if (was !== (draft !== null)) drawBar();
        });
        area.addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); } });
        body.replaceChildren(area);
        area.focus({ preventScroll: true });
        area.setSelectionRange(0, 0);
      } else if (mode === "markdown") {
        body.replaceChildren(h("article", { class: "doc" }, mdBlocks(current())));
      } else {
        const value = current();
        const count = Math.max(1, value.split("\n").length - (value.endsWith("\n") ? 1 : 0));
        let numbers = "";
        for (let i = 1; i <= count; i += 1) numbers += i + "\n";
        body.replaceChildren(h("div", { class: "fv-lines" }, h("pre", { class: "fv-ln", "aria-hidden": "true" }, numbers), h("pre", { class: "fv-code" }, value)));
      }
    }

    function zoomBy(factor) {
      zoom = Math.min(32, Math.max(0.02, scale * factor));
      relayout();
      drawBar();
    }
    function discard() { draft = null; drawBar(); drawBody(); }
    async function save() {
      if (draft === null) return;
      try {
        // Ein Agent im Container kann die Datei inzwischen geändert haben: nicht still überschreiben.
        let now = null;
        try { now = await fetchBytes(raw, VIEW_MAX); } catch (error) { if (error.error === "E_NETWORK") throw error; }
        const same = now && now.bytes && now.bytes.length === bytes.length && now.bytes.every((value, index) => value === bytes[index]);
        if (!same && !(await confirmDialog("Datei wurde geändert", "Die Datei wurde im Container geändert oder gelöscht, seit du sie geöffnet hast. Mit deiner Fassung überschreiben?", "Überschreiben"))) return;
        const saved = draft;
        const out = new TextEncoder().encode(crlf ? saved.replace(/\n/g, "\r\n") : saved);
        await api("PUT", raw, new Blob([out]), { type: "application/octet-stream" });
        bytes = out;
        baseline = saved;
        if (draft === saved) draft = null;
        drawBar();
        if (!editing) drawBody();
        say("Gespeichert: " + path);
      } catch (error) { fail(error); }
    }

    // Bild mit gedrückter Maustaste verschieben; Strg + Mausrad zoomt.
    let grab = null;
    body.addEventListener("pointerdown", (event) => {
      if (mode !== "image" || event.button !== 0) return;
      const box = body.getBoundingClientRect();
      if (event.clientX - box.left > body.clientWidth || event.clientY - box.top > body.clientHeight) return;  // Rollbalken
      grab = { x: event.clientX, y: event.clientY, left: body.scrollLeft, top: body.scrollTop };
      body.setPointerCapture(event.pointerId);
      body.classList.add("grabbing");
      event.preventDefault();
    });
    body.addEventListener("pointermove", (event) => {
      if (!grab) return;
      body.scrollLeft = grab.left - (event.clientX - grab.x);
      body.scrollTop = grab.top - (event.clientY - grab.y);
    });
    const release = () => { grab = null; body.classList.remove("grabbing"); };
    body.addEventListener("pointerup", release);
    body.addEventListener("pointercancel", release);
    body.addEventListener("wheel", (event) => {
      if (mode !== "image" || !(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      zoomBy(event.deltaY < 0 ? 1.1 : 1 / 1.1);
    }, { passive: false });

    // Die Fläche füllt das Fenster bis zum unteren Rand.
    const fitHeight = () => { body.style.height = Math.max(240, window.innerHeight - body.getBoundingClientRect().top - 20) + "px"; relayout(); };
    const warn = (event) => { if (draft !== null) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("resize", fitHeight);
    window.addEventListener("beforeunload", warn);
    main.classList.add("wide");
    state.cleanup.push(() => {
      window.removeEventListener("resize", fitHeight);
      window.removeEventListener("beforeunload", warn);
      main.classList.remove("wide");
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    });
    drawBar();
    page({ title: fileName, compact: true, crumbs: cCrumbs(name, ["Dateien", folderOf(parent)], [fileName]) }, bar, body);
    fitHeight();
    drawBody();
  }

  // --- Container: Archiv entpacken, mit Vorschau -------------------------------------------

  const ARCHIVE_NAME = /\.(zip|tar|tgz|tbz2|txz|tar\.(gz|bz2|xz))$/i;

  async function unpackPage(name, query) {
    const base = "/api/containers/" + enc(name);
    const path = query.get("path") || "";
    const folderOf = (target) => cPath(name, "files?path=" + enc(target));
    const info = await api("GET", base + "/files/archive?path=" + enc(path));
    const here = info.path.split("/").slice(0, -1).join("/") || "/work";
    const fileName = info.path.split("/").pop();
    const tops = info.top.map((top) => ({ name: top, dir: info.entries.some((e) => e.name === top ? e.type === "dir" : e.name.startsWith(top + "/")) }));
    // Ein Archiv mit genau einem Ordner bringt seinen Ordner mit; alles andere bekommt einen eigenen.
    const tidy = tops.length === 1 && tops[0].dir;
    const choice = (value, checked) => h("input", { type: "radio", name: "unpack-target", value, checked });
    const hereOpt = choice("here", tidy), folderOpt = choice("folder", !tidy);
    const folderName = h("input", { class: "inp inp-s mono", type: "text", value: fileName.replace(ARCHIVE_NAME, "") || "entpackt", "aria-label": "Name des neuen Ordners", spellcheck: "false" });
    const result = h("div", { class: "stack" });
    const go_ = h("button", { class: "btn btn-s btn-primary", type: "button", onclick: unpack }, "Entpacken");
    const target = () => (hereOpt.checked ? here : here + "/" + folderName.value.trim());
    let round = 0;

    async function preview() {
      const mine = ++round;
      const into = target();
      const valid = hereOpt.checked || /^[^/\\]+$/.test(folderName.value.trim()) && ![".", ".."].includes(folderName.value.trim());
      go_.disabled = !valid;
      if (!valid) { result.replaceChildren(h("div", { class: "msg error" }, "Der Name des Ordners darf nicht leer sein und keinen Schrägstrich enthalten.")); return; }
      let existing = new Map();
      try { existing = new Map((await api("GET", base + "/files?path=" + enc(into))).entries.map((e) => [e.name, e.type])); }
      catch (error) { if (error.error !== "E_NOT_FOUND") { fail(error); return; } }
      if (mine !== round) return;  // inzwischen wurde das Ziel geändert
      const rows = tops.map((top) => {
        const was = existing.get(top.name);
        return [mono(into + "/" + top.name + (top.dir ? "/" : "")), top.dir ? "Ordner" : "Datei",
          !was ? "neu" : h("strong", { class: "fv-note dirty" }, top.dir && was === "dir" ? "gibt es schon: Inhalt wird ergänzt, gleichnamige Dateien werden ersetzt" : "gibt es schon: wird ersetzt")];
      });
      const lines = info.entries.map((e) => into + "/" + e.name + (e.type === "dir" ? "/" : "")).join("\n")
        + (info.truncated ? "\n… und " + (info.count - info.entries.length) + " weitere" : "");
      result.replaceChildren(
        h("section", { class: "card flush dense" }, h("div", { class: "card-head" }, h("h2", { class: "sec" }, "Das entsteht in ", mono(into))),
          table(["Pfad", "Art", "Hinweis"], rows, "Das Archiv ist leer.")),
        info.skipped.length ? h("div", { class: "msg warn" }, info.skipped.length + " Einträge werden nicht entpackt, weil sie aus dem Zielordner herausführen oder keine Dateien sind: " + info.skipped.slice(0, 8).join(", ") + (info.skipped.length > 8 ? " …" : "")) : null,
        h("details", { class: "card dense" }, h("summary", {}, "Alle " + info.count + " Einträge zeigen"), h("pre", { class: "fv-hexdump fv-all" }, lines)));
    }
    async function unpack() {
      const into = target();
      go_.disabled = true;
      say("Entpacken läuft …", "busy");
      try {
        const done = await api("POST", base + "/files/unpack", { path: info.path, into });
        go(folderOf(done.into), { text: done.files + " Dateien und " + done.dirs + " Ordner nach " + done.into + " entpackt." + (done.skipped.length ? " " + done.skipped.length + " Einträge übersprungen." : "") });
      } catch (error) { go_.disabled = false; fail(error); }
    }
    for (const node of [hereOpt, folderOpt]) node.addEventListener("change", preview);
    let timer = null;
    folderName.addEventListener("input", () => { folderOpt.checked = true; clearTimeout(timer); timer = setTimeout(preview, 250); });
    state.cleanup.push(() => clearTimeout(timer));
    page({ title: fileName + " entpacken", compact: true, crumbs: cCrumbs(name, ["Dateien", folderOf(here)], [fileName, cPath(name, "file?path=" + enc(info.path))], ["Entpacken"]) },
      h("div", { class: "fv-bar" }, pathTrail(info.path, folderOf),
        h("span", { class: "fv-note" }, info.format + " · " + info.count + " Einträge · entpackt " + size(info.size)), go_, link("Abbrechen", folderOf(here), "s")),
      h("section", { class: "card dense unpack-target" }, h("strong", {}, "Wohin?"),
        h("label", {}, hereOpt, " Hier, in ", mono(here)),
        h("label", {}, folderOpt, " In einen neuen Ordner ", mono(here + "/"), folderName),
        hint(tidy ? "Das Archiv bringt genau einen Ordner mit. »Hier« legt diesen Ordner an." : "Das Archiv hat " + tops.length + " Einträge auf der obersten Ebene. Ein eigener Ordner hält sie zusammen.")),
      result);
    await preview();
  }

  // --- Container: Konfiguration ---------------------------------------------------------

  async function configPage(name, query) {
    const base = "/api/containers/" + enc(name);
    const [text, info] = await Promise.all([api("GET", base + "/config"), api("GET", base)]);
    const editor = h("textarea", { class: "editor", spellcheck: "false", "aria-label": "devc.toml" });
    editor.value = text;
    const keep = h("input", { type: "checkbox" });
    async function apply() {
      try {
        const saved = await api("PUT", base + "/config", editor.value);
        if (saved.missing_secrets.length) { say("Secrets ohne Wert: " + saved.missing_secrets.join(", ") + ". Unter Secrets setzen.", "error"); return; }
        const result = await api("POST", base + "/config/apply");
        await follow(result.job, name, { busy: "Konfiguration wird angewendet …", ok: "Konfiguration angewendet.", fail: "Anwenden fehlgeschlagen" });
      } catch (error) { fail(error); }
    }
    async function discard() {
      try {
        // der gespeicherte Stand; ein nicht angewendeter Entwurf bleibt bis zum nächsten Speichern erhalten
        editor.value = await api("GET", base + "/config");
        say("Änderungen im Editor verworfen.");
      } catch (error) { fail(error); }
    }
    async function remove() {
      const what = keep.checked ? "Die Volumes bleiben erhalten." : "Alle Daten in den Volumes gehen verloren.";
      if (!await confirmDialog(name + " löschen", "Der Container " + name + " wird gelöscht. " + what, "Löschen")) return;
      try {
        const result = await api("DELETE", base + (keep.checked ? "?keep_data=1" : ""));
        const ok = await follow(result.job, null, { busy: name + " wird gelöscht …", ok: name + " gelöscht.", fail: "Löschen fehlgeschlagen" });
        if (ok) go("#/", { text: "Container " + name + " gelöscht." });
      } catch (error) { fail(error); }
    }
    const deleteButton = btn(name + " löschen", remove, "danger");
    const deleteCard = card("Container löschen",
      h("p", {}, "Entfernt den Container aus LXD. Ohne Haken werden auch die Volumes mit /work und /home/dev gelöscht."),
      option(keep, "Datenbereich behalten"), h("div", {}, deleteButton));
    page({ title: "Konfiguration", crumbs: cCrumbs(name, ["Konfiguration"]), sub: [mono("devc.toml"), " von " + name + (info.template ? " · aus " + info.template : "")],
      actions: [link("Addon hinzufügen", cPath(name, "add-addon")), btn("Verwerfen", discard), btn("Anwenden", apply, "primary")] },
      card("devc.toml", editor, hint("Anwenden prüft die Datei und setzt Änderungen um: Addons installieren, Parameter neu anwenden, Ressourcen setzen, Agent-Doku neu erzeugen. Der Fortschritt steht im ", h("a", { href: cPath(name, "log") }, "Log"), ".")),
      deleteCard);
    if (query && query.get("delete")) { deleteCard.scrollIntoView({ block: "center" }); deleteButton.focus(); }
  }

  // --- Container: Secrets -------------------------------------------------------------------

  async function containerSecrets(name) {
    const base = "/api/containers/" + enc(name);
    const [data, store] = await Promise.all([api("GET", base + "/secrets"), api("GET", "/api/secrets")]);
    async function remove(entry) {
      if (!await confirmDialog("Secret entfernen", entry.slot + " wird aus diesem Container entfernt.", "Entfernen")) return;
      try { await api("DELETE", base + "/secrets/" + enc(entry.slot)); go(cPath(name, "secrets"), { text: entry.slot + " entfernt." }); } catch (error) { fail(error); }
    }
    const rows = data.secrets.map((s) => [mono(s.slot), s.source === "none" ? "keine" : s.source === "local" ? "nur dieser Container" : "Store: " + s.source.slice(6),
      s.source === "none" ? h("span", { class: "hint warn" }, s.required ? "offen (Pflicht)" : "offen") : "gesetzt " + when(s.modified),
      s.source === "none" ? h("span", { class: "muted" }, "rechts setzen") : btn("Entfernen", () => remove(s), "danger")]);
    const slot = h("select", { class: "inp mono" }, data.secrets.map((s) => h("option", { value: s.slot }, s.slot)));
    const fromStore = h("input", { type: "radio", name: "source", value: "store", checked: store.secrets.length > 0 });
    const local = h("input", { type: "radio", name: "source", value: "local", checked: store.secrets.length === 0 });
    const storeName = h("select", { class: "inp mono" }, store.secrets.map((s) => h("option", { value: s.name }, s.name)));
    const value = h("input", { class: "inp", type: "password", autocomplete: "new-password" });
    const file = h("input", { class: "inp", type: "file" });
    async function save(event) {
      event.preventDefault();
      try {
        let body;
        if (fromStore.checked) {
          if (!storeName.value) { say("Der Secret-Store ist leer.", "error"); return; }
          body = { store: storeName.value };
        } else {
          const text = file.files.length ? await file.files[0].text() : value.value;
          if (!text) { say("Wert oder Datei angeben.", "error"); return; }
          body = { value: text };
        }
        await api("PUT", base + "/secrets/" + enc(slot.value), body);
        go(cPath(name, "secrets"), { text: slot.value + " gesetzt." });
      } catch (error) { fail(error); }
    }
    page({ title: "Secrets", crumbs: cCrumbs(name, ["Secrets"]), sub: ["Im Container als Dateien unter ", mono("/run/devc/secrets/"), ". Werte werden nie angezeigt."] },
      h("div", { class: "cols" },
        h("div", { class: "wide" }, flush("Secrets dieses Containers", table(["Secret", "Quelle", "Status", "Aktion"], rows, "Dieser Container hat keine Secret-Slots."),
          hint("Änderungen im Store wirken hier erst, wenn du den Wert neu übernimmst."))),
        data.secrets.length ? h("form", { class: "narrow card stack", onsubmit: save }, h("h2", { class: "sec" }, "Secret setzen"),
          field("Secret", slot),
          h("fieldset", { style: "border: 0; padding: 0; margin: 0" }, h("legend", { class: "lbl" }, "Quelle"),
            option(fromStore, "aus dem Store übernehmen"), storeName, option(local, "nur für diesen Container"), field("Wert", value), field("oder Datei (Text)", file)),
          h("button", { class: "btn btn-primary", type: "submit" }, "Speichern")) : null));
  }

  // --- Container: Log ------------------------------------------------------------------------

  async function logPage(name, query) {
    const jobs = (await api("GET", "/api/jobs?container=" + enc(name))).jobs;
    const selected = query.get("job") || (jobs[0] && jobs[0].id);
    const stateText = { ok: "erfolgreich", failed: "fehlgeschlagen", running: "läuft", queued: "wartet" };
    const list = jobs.map((job) => h("a", { class: "job" + (job.id === selected ? " on" : ""), href: cPath(name, "log?job=" + enc(job.id)), style: "text-decoration: none" },
      h("span", { class: "mono" }, job.kind), h("span", { class: "muted" }, when(job.started_at) + " · " + (stateText[job.state] || job.state) + (job.error_code ? " (" + job.error_code + ")" : ""))));
    const output = h("pre", { class: "log", "aria-live": "polite", tabindex: "0" });
    const title = h("h2", { class: "sec" }, "Kein Job ausgewählt");
    page({ title: "Log", crumbs: cCrumbs(name, ["Log"]), sub: "Jobs dieses Containers, neueste zuerst. Laufende Jobs aktualisieren sich live." },
      h("div", { class: "cols" },
        h("div", { class: "narrow stack" }, list.length ? list : h("p", { class: "muted" }, "Noch keine Jobs.")),
        h("div", { class: "wide stack" }, title, output)));
    if (!selected) return;
    const job = jobs.find((item) => item.id === selected);
    title.textContent = (job ? job.kind + " · " + when(job.started_at) : selected);
    const source = new EventSource("/api/jobs/" + enc(selected) + "/events");
    state.cleanup.push(() => source.close());
    source.addEventListener("log", (event) => { output.append(event.data + "\n"); output.scrollTop = output.scrollHeight; });
    source.addEventListener("end", () => { source.close(); if (job && (job.state === "running" || job.state === "queued")) route(); });
    source.onerror = () => source.close();
  }

  // --- Container: Snapshots --------------------------------------------------------------------

  async function snapshotsPage(name) {
    const base = "/api/containers/" + enc(name);
    const data = await api("GET", base + "/snapshots");
    async function restore(snap) {
      if (!await confirmDialog("Zurücksetzen", "Die Daten von " + name + " werden auf den Snapshot " + snap.name + " zurückgesetzt. Neuere Änderungen gehen verloren; der Container wird dafür kurz gestoppt.", "Zurücksetzen")) return;
      try {
        const result = await api("POST", base + "/snapshots/" + enc(snap.name) + "/restore");
        await follow(result.job, name, { busy: "Zurücksetzen auf " + snap.name + " …", ok: "Auf " + snap.name + " zurückgesetzt.", fail: "Zurücksetzen fehlgeschlagen" });
      } catch (error) { fail(error); }
    }
    async function remove(snap) {
      if (!await confirmDialog("Snapshot löschen", "Der Snapshot " + snap.name + " wird gelöscht.", "Löschen")) return;
      try { await api("DELETE", base + "/snapshots/" + enc(snap.name)); go(cPath(name, "snapshots"), { text: "Snapshot " + snap.name + " gelöscht." }); } catch (error) { fail(error); }
    }
    const rows = data.snapshots.slice().reverse().map((s) => [mono(s.name), when(s.created_at), s.scope === "full" ? "Daten + Container" : "Daten",
      h("div", { class: "row" }, btn("Zurücksetzen", () => restore(s)), btn("Löschen", () => remove(s), "danger"))]);
    const label = h("input", { class: "inp mono", type: "text", maxlength: "63", autocomplete: "off", spellcheck: "false", placeholder: "auto-…" });
    const full = h("input", { type: "checkbox" });
    async function create(event) {
      event.preventDefault();
      try {
        const result = await api("POST", base + "/snapshots", { label: label.value || null, full: full.checked });
        const ok = await follow(result.job, name, { busy: "Snapshot wird erstellt …", ok: "Snapshot erstellt.", fail: "Snapshot fehlgeschlagen" });
        if (ok) go(cPath(name, "snapshots"), { text: "Snapshot erstellt." });
      } catch (error) { fail(error); }
    }
    page({ title: "Snapshots", crumbs: cCrumbs(name, ["Snapshots"]), sub: ["Schnelle Zeitpunkte auf diesem Host. Für Sicherungen an anderem Ort gibt es ", h("a", { href: cPath(name, "backups") }, "Backups"), "."] },
      h("div", { class: "cols" },
        h("div", { class: "wide" }, flush("Vorhandene Snapshots", table(["Label", "Zeitpunkt", "Umfang", "Aktionen"], rows, "Noch keine Snapshots."),
          hint("Zurücksetzen und Löschen fragen einmal nach und melden das Ergebnis oben. Zurücksetzen stoppt den Container kurz."))),
        h("form", { class: "narrow card stack", onsubmit: create }, h("h2", { class: "sec" }, "Snapshot erstellen"),
          field("Label", label, "a–z, 0–9 und Bindestrich; leer = automatischer Name"),
          option(full, "Container einschließen (stoppt ihn kurz)"),
          h("button", { class: "btn btn-primary", type: "submit" }, "Erstellen"))));
  }

  // --- Container: Backups ------------------------------------------------------------------------

  async function backupsPage(name) {
    const data = await api("GET", "/api/containers/" + enc(name) + "/backups");
    async function remove(item) {
      if (!await confirmDialog("Backup löschen", "Das Archiv " + item.file + " wird vom Host gelöscht.", "Löschen")) return;
      try { await api("DELETE", "/api/backups/" + enc(item.file)); go(cPath(name, "backups"), { text: item.file + " gelöscht." }); } catch (error) { fail(error); }
    }
    async function backupNow() {
      try {
        const result = await api("POST", "/api/containers/" + enc(name) + "/backups", { label: null, include_work: true, include_home: true });
        const ok = await follow(result.job, name, { busy: "Backup läuft …", ok: "Backup erstellt.", fail: "Backup fehlgeschlagen" });
        if (ok) go(cPath(name, "backups"), { text: "Backup erstellt." });
      } catch (error) { fail(error); }
    }
    const rows = data.backups.slice().reverse().map((b) => [mono(b.file), stampDate(b.stamp), size(b.size),
      h("div", { class: "row" }, h("a", { class: "btn", href: "/api/backups/" + enc(b.file), download: "" }, "Herunterladen"),
        link("Wiederherstellen", cPath(name, "restore?backup=" + enc(b.file))), btn("Löschen", () => remove(b), "danger"))]);
    page({ title: "Backups", crumbs: cCrumbs(name, ["Backups"]), sub: ["Portable Archive mit ", mono("devc.toml"), ", Daten und Prüfsummen, ohne Secrets."],
      actions: [link("Wiederherstellen", cPath(name, "restore")), link("Backup erstellen", cPath(name, "backups/new")), btn("Backup jetzt", backupNow, "primary")] },
      flush(null, table(["Archiv", "Zeitpunkt", "Größe", "Aktionen"], rows, "Noch keine Backups.")),
      hint("»Backup jetzt« sichert sofort alles (Code und Home), ohne Rückfrage. »Backup erstellen« fragt nach Inhalt und Namen. Wiederherstellen hat eine eigene Seite. Ablage auf dem Host: ~/.local/share/devc/backups/"));
  }

  async function backupNew(name) {
    const base = "/api/containers/" + enc(name);
    const info = await api("GET", base);
    const work = h("input", { type: "checkbox", checked: true });
    const home = h("input", { type: "checkbox", checked: true });
    const label = h("input", { class: "inp mono", type: "text", maxlength: "63", autocomplete: "off", spellcheck: "false" });
    const download = h("input", { type: "checkbox" });
    async function startBackup() {
      try {
        const result = await api("POST", base + "/backups", { label: label.value || null, include_work: work.checked, include_home: home.checked });
        const ok = await follow(result.job, name, { busy: "Backup läuft …", ok: "Backup erstellt.", fail: "Backup fehlgeschlagen" });
        if (!ok) return;
        if (download.checked) {
          const latest = (await api("GET", base + "/backups")).backups.slice(-1)[0];
          if (latest) location.href = "/api/backups/" + enc(latest.file);
        }
        go(cPath(name, "backups"), { text: "Backup erstellt." });
      } catch (error) { fail(error); }
    }
    page({ title: "Backup erstellen", crumbs: cCrumbs(name, ["Backups", cPath(name, "backups")], ["Erstellen"]),
      actions: [link("Abbrechen", cPath(name, "backups")), btn("Backup starten", startBackup, "primary")] },
      card("Inhalt",
        option(h("input", { type: "checkbox", checked: true, disabled: true }), mono("devc.toml"), " und Manifest", h("div", { class: "hint" }, "immer dabei")),
        option(work, "Code unter ", mono("/work"), h("div", { class: "hint" }, info.data.shared ? "liegt im Shared Folder " + info.data.shared : "liegt im Code-Volume")),
        option(home, "Home ", mono("/home/dev"), h("div", { class: "hint" }, "inkl. Agent-Memory ~/.claude, ~/.config/opencode"))),
      card(null, field("Label (optional)", label, "Dateiname: " + name + "-<zeitstempel>[-label].devc.tar")),
      card("Nach dem Erstellen", option(download, "Zusätzlich direkt herunterladen"), hint("Das Archiv bleibt immer im Backup-Ordner des Hosts.")),
      hint("Secrets sind nie enthalten; beim Wiederherstellen werden sie neu zugeordnet. Der Job läuft weiter, auch wenn du die Seite verlässt."));
  }

  async function restorePage(name, query) {
    const base = "/api/containers/" + enc(name);
    const backups = (await api("GET", base + "/backups")).backups.slice().reverse();
    const existing = h("input", { type: "radio", name: "source", checked: true });
    const uploadRadio = h("input", { type: "radio", name: "source" });
    const select = h("select", { class: "inp mono" }, backups.map((b) => h("option", { value: b.file, selected: b.file === query.get("backup") }, b.file)));
    const file = h("input", { class: "inp", type: "file", accept: ".tar" });
    const replace = h("input", { type: "radio", name: "target", checked: true });
    const asNew = h("input", { type: "radio", name: "target" });
    const newName = h("input", { class: "inp mono", type: "text", maxlength: "31", autocomplete: "off", spellcheck: "false" });
    const editor = h("textarea", { class: "editor", spellcheck: "false", "aria-label": "devc.toml" });
    const checks = h("ul", { class: "check" });
    const sure = h("input", { type: "checkbox" });
    let source = null;
    async function inspect() {
      source = null;
      checks.replaceChildren(h("li", { class: "warn" }, "Archiv wird geprüft …"));
      try {
        let result;
        if (uploadRadio.checked) {
          if (!file.files.length) { checks.replaceChildren(h("li", { class: "warn" }, "Archiv auswählen.")); return; }
          result = await api("POST", "/api/restore/inspect", file.files[0], { type: "application/x-tar" });
          source = { upload_id: result.upload_id };
        } else {
          if (!select.value) { checks.replaceChildren(h("li", { class: "warn" }, "Kein Backup vorhanden; Archiv hochladen.")); return; }
          result = await api("POST", "/api/restore/inspect", { backup: select.value });
          source = { backup: select.value };
        }
        editor.value = result.devc_toml;
        syncName();
        const m = result.manifest;
        checks.replaceChildren(h("li", { class: "ok" }, "Prüfsummen stimmen"),
          h("li", { class: "ok" }, "Backup von " + m.name + (m.template ? " · " + m.template : "") + " · devc " + m.devc_version),
          h("li", { class: "ok" }, "enthält " + (m.includes.map((a) => a === "work" ? "Code" : "Home").join(" und ") || "nur die Konfiguration")));
      } catch (error) { checks.replaceChildren(h("li", { class: "err" }, errorText(error))); }
    }
    function syncName() {
      const target = asNew.checked ? newName.value : name;
      if (target) editor.value = editor.value.replace(/^(\s*name\s*=\s*).*$/m, '$1"' + target + '"');
    }
    for (const control of [existing, uploadRadio, select, file]) control.addEventListener("change", inspect);
    for (const control of [replace, asNew]) control.addEventListener("change", syncName);
    newName.addEventListener("input", syncName);
    async function run() {
      if (!source) { say("Zuerst ein gültiges Archiv wählen.", "error"); return; }
      const target = asNew.checked ? newName.value : name;
      if (asNew.checked && !target) { say("Name des neuen Containers fehlt.", "error"); return; }
      if (!asNew.checked) {
        if (!sure.checked) { say("Bitte bestätigen, dass die Daten von " + name + " überschrieben werden.", "error"); return; }
        if (!await confirmDialog(name + " ersetzen", "/work und /home/dev von " + name + " werden durch den Stand aus dem Backup ersetzt.", "Wiederherstellen")) return;
      }
      try {
        const result = await api("POST", "/api/restore", { source, target: asNew.checked ? "new" : "replace", name: target, devc_toml: editor.value });
        const ok = await follow(result.job, target, { busy: "Wiederherstellen läuft …", ok: target + " wiederhergestellt.", fail: "Wiederherstellen fehlgeschlagen" });
        if (ok) go(cPath(target), { text: target + " wurde aus dem Backup hergestellt." });
      } catch (error) { fail(error); }
    }
    page({ title: "Wiederherstellen", crumbs: cCrumbs(name, ["Backups", cPath(name, "backups")], ["Wiederherstellen"]),
      actions: [link("Abbrechen", cPath(name, "backups")), btn("Wiederherstellen", run, "primary")] },
      h("div", { class: "cols" },
        h("div", { class: "narrow stack" },
          card("1 · Quelle", option(existing, "Vorhandenes Backup"), field("Archiv", select), option(uploadRadio, "Archiv hochladen"), field("Datei", file)),
          card("2 · Ziel", option(replace, "In " + name + " zurückspielen", h("div", { class: "hint" }, "überschreibt /work und /home/dev; der Container wird dafür neu angelegt")),
            option(asNew, "Als neuen Container anlegen"), field("Name des neuen Containers", newName)),
          card("Prüfung des Archivs", checks, option(sure, "Mir ist klar, dass /work und /home/dev von " + name + " überschrieben werden."),
            hint("Tipp: Vorher einen Snapshot machen, dann ist der jetzige Stand nicht verloren."))),
        h("div", { class: "wide" }, card("3 · Ziel-Konfiguration (devc.toml)",
          hint("Aus dem Archiv übernommen. Vor dem Zurückspielen anpassbar, z. B. Secret-Namen, Ressourcen oder Shared Folder. Secrets, die im Store und im Container fehlen, verhindern den Start."), editor))));
    inspect();
  }

  // --- Routing ---------------------------------------------------------------------------------------

  const ROUTES = [
    [/^\/$/, () => dashboard()],
    [/^\/new$/, () => newTemplate()],
    [/^\/new\/edit$/, (m, q) => newEdit(q)],
    [/^\/templates$/, () => templatesPage()],
    [/^\/templates\/([^/]+)$/, (m, q) => templateEdit(m[1], q)],
    [/^\/addons$/, () => addonsPage()],
    [/^\/secrets$/, () => secretsPage()],
    [/^\/vm$/, () => vmPage()],
    [/^\/vm\/update$/, () => vmUpdatePage()],
    [/^\/help$/, () => helpPage()],
    [/^\/help\/([^/]+)$/, (m) => helpPage(m[1])],
    [/^\/c\/([^/]+)$/, (m) => containerPage(m[1])],
    [/^\/c\/([^/]+)\/addons$/, (m) => containerAddons(m[1])],
    [/^\/c\/([^/]+)\/add-addon$/, (m) => addonPick(m[1])],
    [/^\/c\/([^/]+)\/add-addon\/([^/]+)$/, (m) => addonAdd(m[1], m[2])],
    [/^\/c\/([^/]+)\/addons\/([^/]+)$/, (m) => addonHelp(m[1], m[2])],
    [/^\/c\/([^/]+)\/terminal$/, (m) => terminalPage(m[1])],
    [/^\/c\/([^/]+)\/tile\/([^/]+)$/, (m, q) => tilePage(m[1], m[2], q)],
    [/^\/c\/([^/]+)\/files$/, (m, q) => filesPage(m[1], q)],
    [/^\/c\/([^/]+)\/file$/, (m, q) => filePage(m[1], q)],
    [/^\/c\/([^/]+)\/unpack$/, (m, q) => unpackPage(m[1], q)],
    [/^\/c\/([^/]+)\/config$/, (m, q) => configPage(m[1], q)],
    [/^\/c\/([^/]+)\/secrets$/, (m) => containerSecrets(m[1])],
    [/^\/c\/([^/]+)\/log$/, (m, q) => logPage(m[1], q)],
    [/^\/c\/([^/]+)\/snapshots$/, (m) => snapshotsPage(m[1])],
    [/^\/c\/([^/]+)\/backups$/, (m) => backupsPage(m[1])],
    [/^\/c\/([^/]+)\/backups\/new$/, (m) => backupNew(m[1])],
    [/^\/c\/([^/]+)\/restore$/, (m, q) => restorePage(m[1], q)],
  ];

  // Läuft für den Container noch ein Backup oder Restore, zeigt die Seite dessen Meldung samt Fortschritt wieder an,
  // auch wenn man zwischendurch woanders war. Andere Jobs melden sich auf ihren eigenen Seiten.
  const RESUME = {
    backup: { busy: "Backup läuft …", ok: "Backup erstellt.", fail: "Backup fehlgeschlagen" },
    restore: { busy: "Wiederherstellen läuft …", ok: "Wiederhergestellt.", fail: "Wiederherstellen fehlgeschlagen" },
  };
  async function resumeJob(path, raw) {
    const match = /^\/c\/([^/]+)(\/.*)?$/.exec(path);
    const box = messages();
    if (!match || /^\/(terminal|log)/.test(match[2] || "") || !box || box.childElementCount) return;
    let job;
    try {
      job = (await api("GET", "/api/jobs?container=" + enc(match[1]))).jobs.find((j) => j.state === "running" && j.active && RESUME[j.kind]);
    } catch (error) { return; }
    // Inzwischen woanders, oder die Seite meldet selbst etwas: nichts überschreiben.
    if (!job || (location.hash.replace(/^#/, "") || "/") !== raw || box.childElementCount) return;
    const ok = await follow(job.id, match[1], RESUME[job.kind]);
    if (ok && (location.hash.replace(/^#/, "") || "/") === raw) go(location.hash, { text: RESUME[job.kind].ok });  // Liste neu laden
  }

  async function route() {
    for (const stop of state.cleanup.splice(0)) { try { stop(); } catch (error) { /* Aufräumen darf nie stören */ } }
    if (!state.status) return;
    const raw = location.hash.replace(/^#/, "") || "/";
    const [pathPart, queryPart] = raw.split("?");
    const path = pathPart.split("/").map((part) => { try { return decodeURIComponent(part); } catch (error) { return part; } }).join("/");
    const query = new URLSearchParams(queryPart || "");
    try {
      for (const [pattern, handler] of ROUTES) {
        const match = pattern.exec(path);
        if (match) { await handler(match, query); resumeJob(path, raw); return; }
      }
      page({ title: "Seite nicht gefunden" }, h("p", {}, "Diese Adresse gibt es nicht. ", h("a", { href: "#/" }, "Zum Dashboard")));
    } catch (error) {
      if (error && error.error === "E_AUTH") return;
      page({ title: "Fehler" }, h("div", { class: "msg error", role: "alert" }, errorText(error)), h("p", {}, h("a", { href: "#/" }, "Zum Dashboard")));
    }
  }

  async function start() {
    try {
      state.status = await api("GET", "/api/status");
    } catch (error) {
      if (error.error !== "E_AUTH") main.replaceChildren(h("div", { class: "msg error", role: "alert" }, errorText(error)));
      return;
    }
    const status = state.status;
    // Anmeldeweg eines Container-Ports (SPEC 8.11): nach der Anmeldung zurück zum Dienst; der Server prüft das Ziel.
    const to = new URLSearchParams(location.search).get("to");
    if (to && status.behind_proxy) { location.replace("/api/proxy/login?to=" + enc(to)); return; }
    // GUI-Regel 6: rotes Banner bei unsicherem Bind
    document.getElementById("banner").replaceChildren(status.insecure ? h("div", { class: "banner", role: "alert" },
      h("strong", {}, "Unsicherer Bind: "), mono(status.bind), " ohne TLS. Nur für die Entwicklung im vertrauenswürdigen Netz." +
      (status.terminal_allowed ? " Das Web-Terminal ist trotzdem eingeschaltet." : " Das Web-Terminal ist abgeschaltet.")) : "");
    document.getElementById("nav").replaceChildren(...[["dash", "Dashboard", "#/"], ["templates", "Templates", "#/templates"], ["addons", "Addons", "#/addons"], ["secrets", "Secret-Store", "#/secrets"], ["help", "Hilfe", "#/help"]]
      .map(([key, label, href]) => { const a = h("a", { href }, label); a.dataset.key = key; return a; }));
    document.getElementById("platform").textContent = status.platform + " · devc " + status.version;
    document.getElementById("top").hidden = false;
    route();
  }

  document.getElementById("logout").addEventListener("click", async () => {
    try { await api("POST", "/api/logout"); } catch (error) { /* Session war schon weg */ }
    state.status = null;
    showLogin();
  });
  window.addEventListener("hashchange", route);
  start();
})();
