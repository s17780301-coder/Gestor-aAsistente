(() => {
"use strict";

const cfg = window.DENTAL_CONFIG || {};
const $ = id => document.getElementById(id);

const ready = Boolean(
  cfg.supabaseUrl &&
  cfg.supabaseAnonKey &&
  !String(cfg.supabaseUrl).includes("PEGA_AQUI") &&
  !String(cfg.supabaseAnonKey).includes("PEGA_AQUI")
);

let db = null;
let user = null;
let role = "user";
let selectedModule = "";
const moduleLabels = { patients: "Registro de clientes", collection: "Cobranza", admin: "Administración" };
let patients = [];
let payments = [];
let clinics = [];
let patientSaving = false;
let paymentSaving = false;
let currentPaymentRequestId = null;
let clinicSaving = false;

const money = n =>
  new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN"
  }).format(Number(n) || 0);

const safe = s =>
  String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[c]));

const date = s => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime())
    ? "—"
    : new Intl.DateTimeFormat("es-MX", {
        dateStyle: "medium",
        timeStyle: "short"
      }).format(d);
};

function note(s) {
  $("notice").textContent = s;
  $("notice").classList.remove("hidden");
}

function fail(err, where) {
  console.error(where, err);
  note(where + ": " + (err?.message || "Revisa permisos y esquema de Supabase."));
}

function showLogin(msg = "") {
  $("loginMsg").textContent = msg;
}

const roleMap = {
  admin: "admin",
  administrator: "admin",
  administrador: "admin",
  owner: "admin",
  cobranza: "cobranza",
  collection: "cobranza",
  collector: "cobranza",
  registro: "registro",
  registrador: "registro",
  registration: "registro",
  registro_clientes: "registro"
};

/* Los roles propios se validan en el servidor para evitar consultas de navegador
   con columnas antiguas y problemas de RLS. */
async function checkRole() {
  const { data, error } = await db.functions.invoke(
    "administrar-usuarios",
    { body: { action: "my_roles" } }
  );

  if (error) {
    let detail = error.message || "Error al comprobar el rol.";
    try {
      if (error.context && typeof error.context.json === "function") {
        const body = await error.context.json();
        detail = body.error || body.message || detail;
      }
    } catch (_) {}
    throw new Error("No se pudieron verificar los permisos: " + detail);
  }
  if (data?.error) throw new Error(data.error);

  const roles = (Array.isArray(data?.roles) ? data.roles : [])
    .map(value => roleMap[String(value || "").toLowerCase().trim()])
    .filter(Boolean);

  role = roles.includes("admin") ? "admin" :
    roles.includes("cobranza") ? "cobranza" :
    roles.includes("registro") ? "registro" : "";

  if (!role) {
    throw new Error(
      "La cuenta no tiene un rol autorizado (Administración, Cobranza o Registro)."
    );
  }
}

function allowed(viewName) {
  return (
    (role === "admin" &&
      ["home", "patients", "collection", "admin"].includes(viewName)) ||
    (role === "cobranza" && viewName === "collection") ||
    (role === "registro" && viewName === "patients")
  );
}

function setNavigation() {
  document.querySelectorAll(".nav").forEach(b => {
    const ok = allowed(b.dataset.view);
    b.classList.toggle("hidden", !ok);
  });

  $("userRole").textContent = ({
    admin: "Administración",
    cobranza: "Cobranza",
    registro: "Registro de clientes"
  })[role] || "Sin permiso";

  $("roleInfo").textContent =
    "Sesión autorizada: " + $("userRole").textContent +
    ". Los permisos de datos también deben estar configurados en Supabase.";
}

function view(name) {
  if (!allowed(name)) {
    note("Tu cuenta no tiene permiso para abrir ese módulo.");
    return;
  }

  document.querySelectorAll(".view").forEach(element => {
    element.classList.add("hidden");
  });

  const target = $(name);
  if (!target) {
    note("No se encontró el módulo solicitado: " + name);
    return;
  }

  target.classList.remove("hidden");

  document.querySelectorAll(".nav").forEach(button => {
    button.classList.toggle("active", button.dataset.view === name);
  });

  $("viewTitle").textContent = ({
    home: "Inicio",
    patients: "Registro de clientes",
    collection: "Cobranza",
    admin: "Administración"
  })[name] || name;

  $("notice").classList.add("hidden");
}

function setCollectionPanel(panel) {
  document.querySelectorAll(".collection-panel").forEach(x =>
    x.classList.toggle("hidden", x.id !== panel)
  );

  document.querySelectorAll(".subtab").forEach(x =>
    x.classList.toggle("active", x.dataset.panel === panel)
  );

  if (panel === "receiptsPanel") loadReceipts();
  if (panel === "portfoliosPanel") loadPortfolios();
}

async function loadData() {
  $("connection").textContent = "● Sincronizando…";

  const tasks = [
    db.from("patients")
      .select("*")
      .order("created_at", { ascending: false })
  ];

  if (role === "admin" || role === "cobranza") {
    tasks.push(
      db.from("payments")
        .select("*")
        .order("paid_at", { ascending: false })
    );
  }

  if (role === "admin") {
    tasks.push(
      db.from("clinics")
        .select("*")
        .order("name")
    );
  }

  const results = await Promise.all(tasks);

  if (results[0].error) {
    fail(results[0].error, "No se pudieron leer los clientes");
    $("connection").textContent = "● Error";
    return;
  }

  patients = results[0].data || [];
  let idx = 1;

  if (role === "admin" || role === "cobranza") {
    if (results[idx].error) {
      fail(results[idx].error, "No se pudieron leer los pagos");
      payments = [];
    } else {
      payments = results[idx].data || [];
    }
    idx++;
  } else {
    payments = [];
  }

  if (role === "admin") {
    if (results[idx].error) {
      fail(results[idx].error, "No se pudieron leer las clínicas");
      clinics = [];
    } else {
      clinics = results[idx].data || [];
    }
  } else {
    clinics = [];
  }

  render();
  populateClinics();
  $("connection").textContent = "● Conectado a Supabase";
}

function render() {
  const term = ($("patientSearch").value || "").toLowerCase();

  const filtered = patients.filter(p =>
    [p.name, p.policy_number, p.clinic, p.phone]
      .some(v => String(v || "").toLowerCase().includes(term))
  );

  $("patientRows").innerHTML = filtered.map(p => `
    <tr>
      <td>${safe(p.name)}</td>
      <td>${safe(p.clinic || "—")}</td>
      <td>${safe(p.policy_number || "—")}</td>
      <td>${safe(p.treatment || "—")}</td>
      <td>${money(p.total)}</td>
      <td>${money(p.paid)}</td>
      <td>${money(Math.max(0, Number(p.total || 0) - Number(p.paid || 0)))}</td>
    </tr>
  `).join("") || '<tr><td colspan="7">No se encontraron clientes.</td></tr>';

  $("payPatient").innerHTML =
    '<option value="">Selecciona cliente</option>' +
    patients.map(p => `
      <option value="${safe(p.id)}">
        ${safe(p.name)} · póliza ${safe(p.policy_number || "—")} · saldo
        ${money(Math.max(0, Number(p.total || 0) - Number(p.paid || 0)))}
      </option>
    `).join("");

  const name = id =>
    patients.find(p => String(p.id) === String(id))?.name || "Cliente";

  const pterm = ($("paymentSearch").value || "").toLowerCase();

  $("paymentRows").innerHTML = payments
    .filter(p =>
      (p.patient_name || name(p.patient_id)).toLowerCase().includes(pterm) ||
      String(p.reference || "").toLowerCase().includes(pterm)
    )
    .map(p => `
      <tr>
        <td>${safe(p.patient_name || name(p.patient_id))}</td>
        <td>${safe(date(p.paid_at || p.created_at))}</td>
        <td>${safe(p.concept || "Abono")}</td>
        <td>${money(p.amount)}</td>
        <td>${safe(p.reference || "—")}</td>
      </tr>
    `).join("") || '<tr><td colspan="5">No hay pagos visibles.</td></tr>';

  $("clinicRows").innerHTML = clinics.map(c => `
    <tr>
      <td>${safe(c.name)}</td>
      <td>${patients.filter(p =>
        String(p.clinic || "").trim() === String(c.name || "").trim()
      ).length}</td>
    </tr>
  `).join("") || '<tr><td colspan="2">No hay clínicas visibles.</td></tr>';
}

function populateClinics() {
  const current = $("pClinic").value;

  $("pClinic").innerHTML =
    '<option value="">Selecciona clínica</option>' +
    clinics.map(c =>
      `<option value="${safe(c.name)}">${safe(c.name)}</option>`
    ).join("");

  if (current && clinics.some(c => c.name === current)) {
    $("pClinic").value = current;
  }
}

async function loadReceipts() {
  if (!allowed("collection")) return;

  const body = $("receiptRows");
  body.innerHTML = '<tr><td colspan="5">Cargando…</td></tr>';

  const { data, error } = await db
    .from("collection_receipts")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(300);

  if (error) {
    fail(error, "No se pudieron consultar los recibos");
    body.innerHTML = '<tr><td colspan="5">No fue posible cargar recibos.</td></tr>';
    return;
  }

  body.innerHTML = (data || []).map(r => `
    <tr>
      <td>${safe(r.folio || "—")}</td>
      <td>${safe(r.patient_name || "—")}</td>
      <td>${safe(date(r.created_at))}</td>
      <td>${money(r.payment_amount)}</td>
      <td>${safe(r.concept || "—")}</td>
    </tr>
  `).join("") || '<tr><td colspan="5">No hay recibos visibles.</td></tr>';
}

async function loadPortfolios() {
  if (!allowed("collection")) return;

  const body = $("portfolioRows");
  body.innerHTML = '<tr><td colspan="6">Cargando…</td></tr>';

  const { data, error } = await db
    .from("collection_quincena_cartera")
    .select("*")
    .order("quincena_date", { ascending: false })
    .limit(500);

  if (error) {
    fail(error, "No se pudieron consultar las carteras");
    body.innerHTML = '<tr><td colspan="6">No fue posible cargar carteras.</td></tr>';
    return;
  }

  body.innerHTML = (data || []).map(p => `
    <tr>
      <td>${safe(p.quincena_date || "—")}</td>
      <td>${safe(p.patient_name || p.titular_name || "—")}</td>
      <td>${safe(p.clinic || "—")}</td>
      <td>${safe(p.policy_number || "—")}</td>
      <td>${money(p.balance_net)}</td>
      <td>${safe(p.manager_id || "—")}</td>
    </tr>
  `).join("") || '<tr><td colspan="6">No hay carteras visibles.</td></tr>';
}

$("loginForm").addEventListener("submit", async e => {
  e.preventDefault();

  if (!ready) {
    showLogin("Falta configurar config.js con URL y clave pública de Supabase.");
    return;
  }

  showLogin("Validando cuenta y permisos…");
  $("loginForm").querySelector("button").disabled = true;

  try {
    db = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey);

    const { data, error } = await db.auth.signInWithPassword({
      email: $("email").value.trim(),
      password: $("password").value
    });

    if (error) {
      throw new Error("No se pudo iniciar sesión. Verifica correo, contraseña y estado de la cuenta.");
    }

    user = data.user;
    await checkRole();

    if (!selectedModule) {
      throw new Error("Primero selecciona una aplicación para continuar.");
    }
    if (!allowed(selectedModule)) {
      const wanted = moduleLabels[selectedModule] || "la aplicación seleccionada";
      throw new Error("Tu cuenta no tiene permiso para entrar a " + wanted + ". Vuelve a aplicaciones y selecciona el área asignada a tu cuenta.");
    }

    $("appPicker").classList.add("hidden");
    $("login").classList.add("hidden");
    $("app").classList.remove("hidden");
    $("userEmail").textContent = user.email || "Usuario";
    $("today").textContent = new Intl.DateTimeFormat("es-MX", {
      dateStyle: "medium"
    }).format(new Date());

    setNavigation();
    document.dispatchEvent(new CustomEvent("dentaljg:session-ready", {
      detail: { userId: user.id, role }
    }));

    await loadData();

    view(selectedModule);
  } catch (err) {
    showLogin(err.message || "Acceso denegado.");
    if (db) await db.auth.signOut().catch(() => {});
    user = null;
    role = "user";
    $("app").classList.add("hidden");
  } finally {
    $("loginForm").querySelector("button").disabled = false;
  }
});

$("logout").addEventListener("click", async () => {
  if (db) await db.auth.signOut();
  location.reload();
});

function selectApplication(moduleName) {
  if (!moduleLabels[moduleName]) return;
  selectedModule = moduleName;
  $("selectedAppLabel").textContent = moduleLabels[moduleName];
  $("loginMsg").textContent = "";
  $("loginForm").reset();
  $("appPicker").classList.add("hidden");
  $("login").classList.remove("hidden");
  $("email").focus();
}

document.querySelectorAll("[data-select-app]").forEach(tile => {
  tile.addEventListener("click", () => selectApplication(tile.dataset.selectApp));
});

$("backToApps").addEventListener("click", () => {
  selectedModule = "";
  $("loginMsg").textContent = "";
  $("loginForm").reset();
  $("login").classList.add("hidden");
  $("appPicker").classList.remove("hidden");
});

document.querySelectorAll(".nav").forEach(button => {
  button.addEventListener("click", () => view(button.dataset.view));
});

document.querySelectorAll("[data-open-view]").forEach(button => {
  button.addEventListener("click", () => view(button.dataset.openView));
});

document.querySelectorAll(".subtab").forEach(b =>
  b.addEventListener("click", () => setCollectionPanel(b.dataset.panel))
);

$("refresh").addEventListener("click", loadData);
$("patientSearch").addEventListener("input", render);
$("paymentSearch").addEventListener("input", render);
$("loadReceipts").addEventListener("click", loadReceipts);
$("loadPortfolios").addEventListener("click", loadPortfolios);

$("patientForm").addEventListener("submit", async e => {
  e.preventDefault();
  if (patientSaving) return;

  if (role !== "admin" && role !== "registro") {
    note("Solo Registro de clientes o Administración puede dar de alta clientes.");
    return;
  }

  const name = $("pName").value.trim();
  const clinic = $("pClinic").value.trim();
  const policy = $("pPolicy").value.trim();
  const total = Number($("pTotal").value);
  const inst = Math.trunc(Number($("pInstallments").value));
  const ia = Number($("pInstallmentAmount").value);

  if (!name || !clinic || !policy || !Number.isFinite(total) || total < 0 ||
      !Number.isInteger(inst) || inst < 1 || !Number.isFinite(ia) || ia < 0) {
    $("patientMsg").textContent = "Verifica los campos obligatorios.";
    return;
  }

  // Protección preventiva; el índice único SQL cubre altas simultáneas.
  if (patients.some(p =>
    String(p.policy_number || "").trim().toLowerCase() === policy.toLowerCase() &&
    String(p.clinic || "").trim().toLowerCase() === clinic.toLowerCase()
  )) {
    $("patientMsg").textContent = "Esa póliza ya existe en esta clínica.";
    return;
  }

  const payload = {
    name,
    phone: $("pPhone").value.trim(),
    phone2: $("pPhone2").value.trim() || null,
    treatment: $("pTreatment").value,
    clinic,
    policy_number: policy,
    total,
    paid: 0,
    installments: inst,
    installment_amount: ia,
    address: $("pAddress").value.trim() || null,
    observations: $("pObservations").value.trim() || null,
    source_device: "DENTAL JG PANEL"
  };

  patientSaving = true;
  const submitButton = $("patientForm").querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  $("patientMsg").textContent = "Guardando…";
  try {
    const { error } = await db.from("patients").insert(payload);
    if (error) {
      if (error.code === "23505") $("patientMsg").textContent = "No se guardó: esa póliza ya está registrada en la clínica.";
      else {
        fail(error, "No se pudo guardar el cliente");
        $("patientMsg").textContent = "No guardado. Revisa el resultado antes de volver a intentarlo.";
      }
      return;
    }
    $("patientForm").reset();
    $("patientMsg").textContent = "Cliente guardado.";
    await loadData();
  } finally {
    patientSaving = false;
    if (submitButton) submitButton.disabled = false;
  }
});

$("paymentForm").addEventListener("submit", async e => {
  e.preventDefault();
  if (paymentSaving) return;

  if (role !== "admin" && role !== "cobranza") {
    note("Tu rol no puede registrar pagos.");
    return;
  }

  const patient = patients.find(p => String(p.id) === $("payPatient").value);
  const amount = Number($("payAmount").value);

  if (!patient || !Number.isFinite(amount) || amount <= 0) {
    $("paymentMsg").textContent = "Selecciona cliente e importe válido.";
    return;
  }

  const remaining = Math.max(0, Number(patient.total || 0) - Number(patient.paid || 0));
  if (amount > remaining + 0.005) {
    $("paymentMsg").textContent = "El importe supera el saldo actual.";
    return;
  }

  paymentSaving = true;
  const submitButton = $("paymentForm").querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  $("paymentMsg").textContent = "Registrando mediante operación transaccional…";
  try {
    // El UUID se valida y registra en SQL para hacer idempotente cada intento.
    const requestId = currentPaymentRequestId || (currentPaymentRequestId = crypto.randomUUID());
    const { error } = await db.rpc("register_dental_jg_payment", {
      p_patient_id: patient.id,
      p_amount: amount,
      p_concept: $("payConcept").value.trim() || "Abono",
      p_reference: $("payReference").value.trim(),
      p_request_id: requestId
    });
    if (error) {
      fail(error, "No se registró el pago. No se hizo INSERT alternativo para evitar duplicados.");
      $("paymentMsg").textContent = "Resultado no confirmado. No repitas el pago todavía; verifica primero el historial.";
      return;
    }
    $("paymentForm").reset();
    currentPaymentRequestId = null;
    $("paymentMsg").textContent = "Pago registrado.";
    await loadData();
  } finally {
    paymentSaving = false;
    if (submitButton) submitButton.disabled = false;
  }
});

$("clinicForm").addEventListener("submit", async e => {
  e.preventDefault();
  if (clinicSaving) return;

  if (role !== "admin") {
    note("Solo Administración puede gestionar clínicas.");
    return;
  }

  const name = $("clinicName").value.trim();
  if (!name) return;

  if (clinics.some(c => String(c.name).toLowerCase() === name.toLowerCase())) {
    note("Esa clínica ya existe.");
    return;
  }

  clinicSaving = true;
  const submitButton = $("clinicForm").querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  try {
    const { error } = await db.from("clinics").insert({ name });
    if (error) {
      if (error.code === "23505") note("Esa clínica ya existe.");
      else fail(error, "No se pudo guardar la clínica");
      return;
    }
    $("clinicName").value = "";
    await loadData();
  } finally {
    clinicSaving = false;
    if (submitButton) submitButton.disabled = false;
  }
});

if (!ready) {
  showLogin("Configura config.js antes de iniciar sesión.");
}
})();
