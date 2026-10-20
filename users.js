(() => {
  "use strict";

  const cfg = window.DENTAL_CONFIG || {};
  const $ = id => document.getElementById(id);
  const FUNCTION_NAME = "administrar-usuarios";

  let db = null;
  let activeUserId = null;
  let initializing = false;
  let users = [];

  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;"
  })[c]);

  function say(text, error = false) {
    const el = $("userAdminMessage");
    if (!el) return;
    el.textContent = text;
    el.className = "user-message" + (error ? " error" : "");
    el.setAttribute("role", error ? "alert" : "status");
    el.setAttribute("aria-live", "polite");
  }

  async function invoke(action, fields = {}) {
    const { data, error } = await db.functions.invoke(
      FUNCTION_NAME,
      { body: { action, ...fields } }
    );

    if (error) {
      let msg = error.message || "Error al comunicarse con Supabase.";
      try {
        if (error.context && typeof error.context.json === "function") {
          const body = await error.context.json();
          msg = body.error || body.message || msg;
        }
      } catch (_) {}
      throw new Error(msg);
    }

    if (data?.error) throw new Error(data.error);
    return data;
  }

  function addPanel() {
    const admin = $("admin");
    if (!admin || $("userManagementPanel")) return;

    const panel = document.createElement("div");
    panel.id = "userManagementPanel";
    panel.className = "panel";
    panel.innerHTML = `
      <div class="section-head">
        <div>
          <h3>Gestión de usuarios</h3>
          <p>Crea cuentas, asigna roles, activa o desactiva accesos
          y restablece contraseñas.</p>
        </div>
        <button id="refreshUsers" class="secondary" type="button">
          Actualizar lista
        </button>
      </div>

      <form id="createUserForm" class="user-create-grid">
        <label>Correo electrónico *
          <input id="newUserEmail" type="email" required maxlength="254"
            autocomplete="email">
        </label>
        <label>Contraseña temporal *
          <input id="newUserPassword" type="password"
            required minlength="8" maxlength="128"
            autocomplete="new-password">
        </label>
        <label>Rol *
          <select id="newUserRole" required>
            <option value="registro">Registro de clientes</option>
            <option value="cobranza">Cobranza</option>
            <option value="admin">Administración</option>
          </select>
        </label>
        <div class="user-create-action">
          <button id="createUserButton" class="primary" type="submit">
            Crear cuenta
          </button>
        </div>
      </form>

      <p class="muted">
        Usa contraseñas temporales seguras y compártelas de forma privada.
      </p>
      <div id="userAdminMessage" class="user-message"
        role="status" aria-live="polite">Preparando módulo…</div>

      <div class="table-scroll">
        <table>
          <thead><tr>
            <th>Correo</th><th>Rol</th><th>Estado</th><th>Acciones</th>
          </tr></thead>
          <tbody id="userAdminRows">
            <tr><td colspan="4">Cargando usuarios…</td></tr>
          </tbody>
        </table>
      </div>
    `;
    admin.appendChild(panel);

    const accountPanel = document.createElement("div");
    accountPanel.id = "adminOwnAccountPanel";
    accountPanel.className = "panel admin-own-account";
    accountPanel.innerHTML = `
      <div class="section-head"><div>
        <h3>Mi cuenta de Administración</h3>
        <p>Gestiona los datos de tu propia sesión, por separado de las cuentas de otros usuarios.</p>
      </div></div>
      <form id="adminOwnAccountForm" class="user-create-grid">
        <label>Correo actual <input id="adminCurrentEmail" type="email" readonly autocomplete="username"></label>
        <label>Nuevo correo electrónico (opcional) <input id="adminNewEmail" type="email" maxlength="254" autocomplete="email" placeholder="Dejar vacío para conservarlo"></label>
        <label>Nueva contraseña (opcional) <input id="adminNewPassword" type="password" minlength="8" maxlength="128" autocomplete="new-password" placeholder="Mínimo 8 caracteres"></label>
        <label>Confirmar nueva contraseña <input id="adminConfirmPassword" type="password" minlength="8" maxlength="128" autocomplete="new-password"></label>
        <div class="user-create-action"><button id="saveAdminAccount" class="primary" type="submit">Guardar cambios de mi cuenta</button></div>
      </form>
      <p class="muted">Si cambias el correo, Supabase puede pedir confirmación por correo electrónico. La contraseña nunca se muestra ni se recupera en texto legible.</p>
      <div id="adminAccountMessage" class="user-message" role="status" aria-live="polite"></div>
    `;
    admin.appendChild(accountPanel);
    $("adminOwnAccountForm").addEventListener("submit", saveOwnAccount);

    $("createUserForm").addEventListener("submit", createUser);
    $("refreshUsers").addEventListener("click", () => loadUsers());
    $("userAdminRows").addEventListener("click", userAction);
  }

  function accountSay(text, error = false) {
    const el = $("adminAccountMessage");
    if (!el) return;
    el.textContent = text;
    el.className = "user-message" + (error ? " error" : "");
  }

  async function refreshOwnAccount() {
    if (!db) return;
    const { data, error } = await db.auth.getUser();
    if (error) { accountSay("No se pudieron consultar los datos de la sesión: " + error.message, true); return; }
    const email = data?.user?.email || "";
    if ($("adminCurrentEmail")) $("adminCurrentEmail").value = email;
  }

  async function saveOwnAccount(event) {
    event.preventDefault();
    const newEmail = $("adminNewEmail").value.trim().toLowerCase();
    const password = $("adminNewPassword").value;
    const confirmPassword = $("adminConfirmPassword").value;
    const button = $("saveAdminAccount");
    if (!newEmail && !password) { accountSay("Escribe un nuevo correo o una nueva contraseña para guardar cambios.", true); return; }
    if (password && password.length < 8) { accountSay("La contraseña debe tener al menos 8 caracteres.", true); return; }
    if (password !== confirmPassword) { accountSay("Las contraseñas no coinciden.", true); return; }
    if (!confirm("¿Confirmas actualizar los datos de tu propia cuenta de Administración?")) return;
    button.disabled = true;
    accountSay("Guardando cambios de la cuenta…");
    try {
      const changes = {};
      if (newEmail) changes.email = newEmail;
      if (password) changes.password = password;
      const { data, error } = await db.auth.updateUser(changes);
      if (error) throw error;
      $("adminNewEmail").value = "";
      $("adminNewPassword").value = "";
      $("adminConfirmPassword").value = "";
      if (data?.user?.email) $("adminCurrentEmail").value = data.user.email;
      accountSay(newEmail ? "Solicitud guardada. Si Supabase requiere confirmar el nuevo correo, revisa tu bandeja de entrada." : "Contraseña actualizada correctamente.");
    } catch (e) {
      accountSay("No se pudieron guardar los cambios: " + (e.message || "Error desconocido."), true);
    } finally { button.disabled = false; }
  }

  function isActive(u) {
    if (typeof u.active === "boolean") return u.active;
    if (typeof u.is_active === "boolean") return u.is_active;
    if (u.banned_until) {
      const t = new Date(u.banned_until).getTime();
      if (Number.isFinite(t) && t > Date.now()) return false;
    }
    return true;
  }

  function renderUsers() {
    const body = $("userAdminRows");
    if (!body) return;

    if (!users.length) {
      body.innerHTML =
        '<tr><td colspan="4">No se encontraron usuarios.</td></tr>';
      return;
    }

    body.innerHTML = users.map(u => {
      const id = String(u.id || u.user_id || "");
      const email = String(u.email || "");
      const roles = Array.isArray(u.roles) && u.roles.length
        ? u.roles.map(r => String(r).trim().toLowerCase()).filter(Boolean)
        : [String(u.role || u.role_name || u.app_role || "").toLowerCase()].filter(Boolean);
      const role = roles[0] || "";
      const roleLabel = roles.map(r => ({admin:"Administración", cobranza:"Cobranza", registro:"Registro"}[r] || r)).join(" + ");
      const active = isActive(u);

      return `<tr data-user-id="${esc(id)}">
        <td>${esc(email || "Sin correo")}</td>
        <td><span class="role-current">${esc(roleLabel || "Sin rol")}</span><div class="edit-user-roles" aria-label="Roles para ${esc(email)}">
          <label><input type="checkbox" value="registro" ${roles.includes("registro") ? "checked" : ""}> Registro</label>
          <label><input type="checkbox" value="cobranza" ${roles.includes("cobranza") ? "checked" : ""}> Cobranza</label>
          <label><input type="checkbox" value="admin" ${roles.includes("admin") ? "checked" : ""}> Administración</label>
        </div></td>
        <td><span class="status-pill ${active ? "is-active" : "is-inactive"}">${active ? "Activo" : "Inactivo"}</span></td>
        <td><div class="user-actions">
          <button type="button" class="secondary"
            data-action="role" data-id="${esc(id)}">Guardar rol</button>
          <button type="button" class="secondary"
            data-action="active" data-id="${esc(id)}"
            data-active="${active}">${active ? "Desactivar" : "Activar"}</button>
          <button type="button" class="secondary"
            data-action="password" data-id="${esc(id)}">Restablecer contraseña</button>
          <button type="button" class="danger"
            data-action="delete" data-id="${esc(id)}">Eliminar cuenta</button>
        </div></td>
      </tr>`;
    }).join("");
  }

  async function loadUsers(successMessage = "Lista de usuarios actualizada correctamente.") {
    if (!db || !activeUserId) return false;
    say("Consultando usuarios…");

    try {
      const result = await invoke("list_users");
      users = Array.isArray(result)
        ? result
        : (result.users || result.data?.users || []);
      renderUsers();
      say(successMessage);
      return true;
    } catch (e) {
      say("No se pudieron cargar los usuarios: " + e.message, true);
      const body = $("userAdminRows");
      if (body) body.innerHTML =
        '<tr><td colspan="4">Error al cargar. Revisa el mensaje anterior.</td></tr>';
      return false;
    }
  }

  async function createUser(event) {
    event.preventDefault();

    const email = $("newUserEmail").value.trim().toLowerCase();
    const password = $("newUserPassword").value;
    const role = $("newUserRole").value;
    const button = $("createUserButton");

    if (!email || password.length < 8) {
      say("Escribe un correo válido y una contraseña de al menos 8 caracteres.", true);
      return;
    }

    button.disabled = true;
    say("Creando cuenta…");

    try {
      await invoke("create_user", { email, password, role });
      $("createUserForm").reset();
      const refreshed = await loadUsers("Cuenta creada correctamente.");
      if (!refreshed) {
        say("La cuenta se creó correctamente, pero no se pudo actualizar la lista. Pulsa «Actualizar lista».", true);
      }
    } catch (e) {
      say("No se pudo crear la cuenta: " + e.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function userAction(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;

    const id = button.dataset.id;
    const action = button.dataset.action;
    const row = button.closest("tr");
    if (!id || !row) {
      say("No se encontró el identificador del usuario. Actualiza la lista e inténtalo de nuevo.", true);
      return;
    }

    try {
      button.disabled = true;

      if (action === "role") {
        const roles = [...row.querySelectorAll(".edit-user-roles input:checked")].map(input => input.value);
        if (!roles.length) {
          say("Selecciona al menos un rol para no dejar la cuenta sin permisos.", true);
          return;
        }
        if (!confirm("¿Confirmas guardar los roles seleccionados? Los roles desmarcados se retirarán.")) return;
        say("Guardando roles…");
        await invoke("set_roles", { user_id: id, roles });
        const refreshed = await loadUsers("Roles actualizados correctamente.");
        if (!refreshed) say("El rol se guardó, pero no se pudo actualizar la lista.", true);
      } else if (action === "active") {
        const active = button.dataset.active !== "true";
        if (!confirm(active ? "¿Activar esta cuenta?" : "¿Desactivar esta cuenta?")) return;
        say(active ? "Activando usuario…" : "Desactivando usuario…");
        await invoke("set_active", { user_id: id, active });
        const refreshed = await loadUsers(active
          ? "Usuario activado correctamente."
          : "Usuario desactivado correctamente.");
        if (!refreshed) say("El estado se guardó, pero no se pudo actualizar la lista.", true);
      } else if (action === "password") {
        const password = prompt("Escribe la nueva contraseña temporal (mínimo 8 caracteres):");
        if (password === null) {
          say("Operación cancelada.");
          return;
        }
        if (password.length < 8) {
          say("La contraseña debe tener al menos 8 caracteres.", true);
          return;
        }
        if (!confirm("¿Confirmas restablecer esta contraseña?")) return;
        say("Restableciendo contraseña…");
        await invoke("reset_password", { user_id: id, password });
        say("Contraseña restablecida correctamente.");
      } else if (action === "delete") {
        const email = row.cells?.[0]?.textContent?.trim() || "esta cuenta";
        if (!confirm(`¿Eliminar definitivamente ${email}? Esta acción no se puede deshacer.`)) return;
        if (!confirm("Confirmación final: se borrará la cuenta de acceso. ¿Continuar?")) return;
        say("Eliminando cuenta…");
        await invoke("delete_user", { user_id: id });
        await loadUsers("Cuenta eliminada correctamente.");
      }
    } catch (e) {
      say("No se pudo completar la acción: " + e.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function initialize() {
    if (initializing) return;
    if (!cfg.supabaseUrl || !cfg.supabaseAnonKey ||
        !window.supabase?.createClient) return;

    initializing = true;

    try {
      if (!db) {
        db = window.supabase.createClient(
          cfg.supabaseUrl,
          cfg.supabaseAnonKey
        );
      }

      const { data, error } = await db.auth.getSession();
      const session = data?.session;

      if (error || !session?.user) {
        activeUserId = null;
        return;
      }

      if (activeUserId === session.user.id && $("userManagementPanel")) {
        return;
      }

      // No consultar app_user_roles desde el navegador: en instalaciones con RLS
      // esa lectura puede fallar aunque el usuario sea administrador. La función
      // valida el token y el rol usando la clave de servicio en el servidor.
      activeUserId = session.user.id;
      addPanel();
      await refreshOwnAccount();
      const authorized = await loadUsers();
      if (!authorized) {
        // La lista no se habilita si el servidor rechaza el rol. La comprobación
        // de autorización sigue siendo obligatoria en cada operación sensible.
        activeUserId = null;
        const panel = $("userManagementPanel");
        if (panel) panel.dataset.authorization = "denied";
      } else {
        const panel = $("userManagementPanel");
        if (panel) panel.dataset.authorization = "verified";
      }
    } catch (e) {
      console.error("Gestión de usuarios:", e);
      say("Error al iniciar gestión de usuarios: " + e.message, true);
    } finally {
      initializing = false;
    }
  }

  function start() {
    initialize();
  }

  function watchLogin() {
    const app = $("app");
    if (app) {
      new MutationObserver(() => {
        if (!app.classList.contains("hidden")) start();
      }).observe(app, {
        attributes: true,
        attributeFilter: ["class"]
      });
    }

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) start();
    });

    window.addEventListener("focus", start);
    start();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watchLogin);
  } else {
    watchLogin();
  }
})();
