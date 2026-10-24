#!/usr/bin/env python3
"""Apply the narrowly scoped Gestor session, patient-selection and report-history fixes."""
from pathlib import Path
import os
import re
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ANDROID_PROJECT_DIR", ".")).resolve()
main_path = root / "gestor-app/src/main/java/com/example/gestoriaasistente/MainActivity.java"
sync_path = root / "gestor-app/src/main/java/com/example/gestoriaasistente/SupabaseSync.java"
db_path = root / "gestor-app/src/main/java/com/example/gestoriaasistente/CustomerDbHelper.java"
for p in (main_path, sync_path, db_path):
    if not p.is_file():
        raise SystemExit(f"No se encontró el archivo esperado de Gestor: {p}")

main = main_path.read_text(encoding="utf-8")
sync = sync_path.read_text(encoding="utf-8")
db = db_path.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# 1) Supabase session maintenance. Preserve the existing endpoints and schema;
#    only refresh access tokens using Supabase's existing refresh_token.
# ---------------------------------------------------------------------------
old_login = '''    public String token() { return context.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString("access_token", ""); }
    public String email() { return context.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString("email", ""); }
    public boolean connected() { return !token().isEmpty(); }
    public String managerId() { return context.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString("manager_id", ""); }
    public String managerName() { return context.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString("manager_name", ""); }
'''
new_login = '''    private SharedPreferences sessionPrefs() { return context.getSharedPreferences(PREF, Context.MODE_PRIVATE); }
    public String token() { return sessionPrefs().getString("access_token", ""); }
    public String email() { return sessionPrefs().getString("email", ""); }
    public boolean connected() { return !token().isEmpty() || !sessionPrefs().getString("refresh_token", "").isEmpty(); }
    public String managerId() { return sessionPrefs().getString("manager_id", ""); }
    public String managerName() { return sessionPrefs().getString("manager_name", ""); }

    private synchronized String ensureFreshAccessToken(boolean forceRefresh) throws Exception {
        SharedPreferences p = sessionPrefs();
        String access = p.getString("access_token", "");
        String refresh = p.getString("refresh_token", "");
        long expiresAt = p.getLong("access_token_expires_at", 0L);
        boolean missingExpiryForLegacySession = expiresAt <= 0L && !refresh.isEmpty();
        boolean expiringSoon = expiresAt > 0L && expiresAt <= System.currentTimeMillis() + 60000L;
        if (!refresh.isEmpty() && (forceRefresh || access.isEmpty() || missingExpiryForLegacySession || expiringSoon)) {
            return refreshSession();
        }
        if (access.isEmpty()) {
            throw new Exception("La sesión de Supabase venció y no hay token de actualización. Inicia sesión nuevamente.");
        }
        return access;
    }

    private synchronized String refreshSession() throws Exception {
        String refresh = sessionPrefs().getString("refresh_token", "");
        if (refresh.isEmpty()) {
            throw new Exception("No hay token de actualización disponible. Inicia sesión nuevamente.");
        }
        JSONObject body = new JSONObject();
        body.put("refresh_token", refresh);
        JSONObject result;
        try {
            // jwt=null makes this call use the public API key, not the expired access token.
            result = request("POST", "/auth/v1/token?grant_type=refresh_token", body, null, null);
        } catch (Exception ex) {
            throw new Exception("No se pudo renovar automáticamente la sesión de Supabase. Comprueba la conexión e inicia sesión otra vez si el problema continúa. " + (ex.getMessage() == null ? "" : ex.getMessage()), ex);
        }
        String access = result.optString("access_token", "");
        if (access.isEmpty()) throw new Exception("Supabase no devolvió un token renovado. Inicia sesión nuevamente.");
        String nextRefresh = result.optString("refresh_token", "");
        if (nextRefresh.isEmpty()) nextRefresh = refresh;
        long expiresIn = result.optLong("expires_in", 3600L);
        if (expiresIn <= 0L) expiresIn = 3600L;
        // Renew one minute early to avoid requests racing the expiry boundary.
        long lifetimeSeconds = Math.max(30L, expiresIn - 60L);
        sessionPrefs().edit()
                .putString("access_token", access)
                .putString("refresh_token", nextRefresh)
                .putLong("access_token_expires_at", System.currentTimeMillis() + lifetimeSeconds * 1000L)
                .apply();
        return access;
    }

    public org.json.JSONArray cachedReceiptHistory() {
        String key = receiptCacheKey();
        String raw = sessionPrefs().getString(key, "[]");
        try { return new org.json.JSONArray(raw); }
        catch (Exception ignored) { return new org.json.JSONArray(); }
    }

    public long cachedReceiptHistoryAt() { return sessionPrefs().getLong(receiptCacheKey() + "_at", 0L); }

    private String receiptCacheKey() {
        String manager = managerId();
        return "receipt_history_" + (manager.isEmpty() ? "own" : manager);
    }

    /** Returns only receipt rows visible to the signed-in Supabase account (RLS remains enforced). */
    public org.json.JSONArray fetchAccessibleReceiptHistory() throws Exception {
        String manager = managerId();
        String path = "/rest/v1/collection_receipts?select=id,folio,patient_name,clinic,payment_amount,net_amount,concept,notes,created_at,manager_id&order=created_at.desc&limit=1000";
        if (!manager.isEmpty()) path += "&manager_id=eq." + manager;
        org.json.JSONArray rows = requestArray("GET", path, null, ensureFreshAccessToken(false), null);
        sessionPrefs().edit().putString(receiptCacheKey(), rows.toString()).putLong(receiptCacheKey() + "_at", System.currentTimeMillis()).apply();
        return rows;
    }
'''
if old_login not in sync:
    raise SystemExit("No se encontró el bloque de sesión esperado en SupabaseSync.java; no se modificó la fuente.")
sync = sync.replace(old_login, new_login, 1)

old_login_method = '''    public String login(String email, String password) throws Exception {
        JSONObject body = new JSONObject(); body.put("email", email); body.put("password", password);
        JSONObject result = request("POST", "/auth/v1/token?grant_type=password", body, null, null);
        String access = result.optString("access_token", "");
        if (access.isEmpty()) throw new Exception("Supabase no devolvió una sesión válida.");
        context.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit().putString("access_token", access).putString("refresh_token", result.optString("refresh_token", "")).putString("email", email).apply();
        return access;
    }
'''
new_login_method = '''    public String login(String email, String password) throws Exception {
        JSONObject body = new JSONObject(); body.put("email", email); body.put("password", password);
        JSONObject result = request("POST", "/auth/v1/token?grant_type=password", body, null, null);
        String access = result.optString("access_token", "");
        if (access.isEmpty()) throw new Exception("Supabase no devolvió una sesión válida.");
        String refresh = result.optString("refresh_token", "");
        long expiresIn = result.optLong("expires_in", 3600L);
        if (expiresIn <= 0L) expiresIn = 3600L;
        long lifetimeSeconds = Math.max(30L, expiresIn - 60L);
        sessionPrefs().edit().putString("access_token", access).putString("refresh_token", refresh)
                .putLong("access_token_expires_at", System.currentTimeMillis() + lifetimeSeconds * 1000L)
                .putString("email", email).apply();
        return access;
    }
'''
if old_login_method not in sync:
    raise SystemExit("No se encontró el método login esperado en SupabaseSync.java; no se modificó la fuente.")
sync = sync.replace(old_login_method, new_login_method, 1)

# Start every sync from a current token. Individual requests also resolve the
# latest stored token, so a refresh halfway through a long sync is respected.
sync = sync.replace('''        String jwt = token(); if (jwt.isEmpty()) throw new Exception("Primero inicia sesión con el mismo correo y contraseña del panel administrativo.");
''', '''        String jwt = ensureFreshAccessToken(false);
''', 1)

# Replace duplicated request methods with one 401-aware transport routine.
request_start = sync.find('    private JSONObject request(')
open_start = sync.find('    private HttpURLConnection open(', request_start)
if request_start < 0 or open_start < 0:
    raise SystemExit("No se encontró la capa HTTP esperada en SupabaseSync.java; no se modificó la fuente.")
old_transport = sync[request_start:open_start]
new_transport = '''    private JSONObject request(String method, String path, JSONObject body, String jwt, String prefer) throws Exception {
        String response = send(method, path, body, jwt, prefer, true);
        return new JSONObject(response.isEmpty() ? "{}" : response);
    }

    private JSONArray requestArray(String method, String path, JSONObject body, String jwt, String prefer) throws Exception {
        String response = send(method, path, body, jwt, prefer, true);
        return new JSONArray(response.isEmpty() ? "[]" : response);
    }

    private String send(String method, String path, JSONObject body, String jwt, String prefer, boolean retryAfterRefresh) throws Exception {
        String activeJwt = jwt == null ? null : ensureFreshAccessToken(false);
        HttpURLConnection c = open(method, path, activeJwt, prefer);
        int code;
        String response;
        try {
            if (body != null) {
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                try (OutputStream o = c.getOutputStream()) {
                    o.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
            }
            code = c.getResponseCode();
            response = read(code >= 200 && code < 400 ? c.getInputStream() : c.getErrorStream());
        } finally {
            c.disconnect();
        }
        if (code == 401 && jwt != null && retryAfterRefresh) {
            // The server can reject a token before its locally stored expiry. Refresh once and retry once.
            refreshSession();
            return send(method, path, body, token(), prefer, false);
        }
        if (code < 200 || code >= 300) throw new Exception(explain(response, code));
        return response;
    }

'''
sync = sync[:request_start] + new_transport + sync[open_start:]
# A network call authenticated with a user token must not keep using a token
# captured before a refresh. `send` passes a fresh token to open() on every call.
if 'access_token_expires_at' not in sync or 'grant_type=refresh_token' not in sync or 'retryAfterRefresh' not in sync:
    raise SystemExit("Faltan verificaciones de renovación de sesión en SupabaseSync.java.")
sync_path.write_text(sync, encoding="utf-8")

# ---------------------------------------------------------------------------
# 2) Use existing local `pagos` and `pacientes` data. No Supabase schema or
#    RLS policy is changed. The report can read real receipts where RLS grants
#    access, and always has the local/shared payments history as a second view.
# ---------------------------------------------------------------------------
old_payments = '''    public Cursor payments(long patientId) { return getReadableDatabase().rawQuery("SELECT monto,fecha,nota FROM pagos WHERE paciente_id=? ORDER BY fecha DESC",new String[]{String.valueOf(patientId)}); }
'''
new_payments = '''    public Cursor payments(long patientId) { return getReadableDatabase().rawQuery("SELECT monto,fecha,nota FROM pagos WHERE paciente_id=? ORDER BY fecha DESC",new String[]{String.valueOf(patientId)}); }
    public Cursor selectablePatients(String period, boolean pendingOnly) {
        String sql = "SELECT p.id,p.nombre,p.telefono,p.tratamiento,p.total,p.pagado,MAX(p.total-p.pagado,0) saldo,p.quincenas,p.pago_quincenal,p.numero_poliza,p.clinica,p.payment_method,CASE WHEN q.paciente_id IS NULL THEN 0 ELSE 1 END AS period_paid " +
                "FROM pacientes p LEFT JOIN quincenas_pagadas q ON q.paciente_id=p.id AND q.periodo=? ";
        if (pendingOnly) sql += "WHERE q.paciente_id IS NULL AND p.total>p.pagado ";
        sql += "ORDER BY p.nombre COLLATE NOCASE";
        return getReadableDatabase().rawQuery(sql, new String[]{period});
    }
    public Cursor allPayments() {
        return getReadableDatabase().rawQuery("SELECT pa.id,pa.paciente_id,COALESCE(p.nombre,'Paciente'),COALESCE(p.clinica,''),COALESCE(p.numero_poliza,''),pa.monto,pa.fecha,COALESCE(pa.nota,''),COALESCE(pa.remote_id,'') FROM pagos pa LEFT JOIN pacientes p ON p.id=pa.paciente_id ORDER BY pa.fecha DESC,pa.id DESC", null);
    }
    public double sumRecordedPayments() {
        Cursor c = getReadableDatabase().rawQuery("SELECT COALESCE(SUM(monto),0) FROM pagos", null);
        try { return c.moveToFirst() ? c.getDouble(0) : 0d; } finally { c.close(); }
    }
'''
if old_payments not in db:
    raise SystemExit("No se encontró el método payments esperado en CustomerDbHelper.java; no se modificó la fuente.")
db = db.replace(old_payments, new_payments, 1)
db_path.write_text(db, encoding="utf-8")

# ---------------------------------------------------------------------------
# 3) Replace the plain AlertDialog client-picker with a searchable, recycled
#    card list. Both receipt entry and WhatsApp use the same list component.
# ---------------------------------------------------------------------------
old_refresh = '    private void refresh(){if(collectedValue!=null){collectedValue.setText(money(db.sumQuincenal()));pendingValue.setText(money(db.sumPaid()));patientCount.setText(String.valueOf(db.count()));}}'
new_refresh = '    private void refresh(){if(collectedValue!=null){collectedValue.setText(money(db.sumQuincenal()));pendingValue.setText(money(db.sumRecordedPayments()));patientCount.setText(String.valueOf(db.count()));}}'
if old_refresh not in main:
    raise SystemExit("No se encontró refresh() esperado; no se modificó la fuente.")
main = main.replace(old_refresh, new_refresh, 1)
main = main.replace('    private android.speech.tts.TextToSpeech brandTts;\n', '', 1)
# Keep the splash/visual intro and its short notification tone; remove only speech.
tts_pattern = re.compile(r'\s*try \{ brandTts = new android\.speech\.tts\.TextToSpeech\(this, status -> \{.*?\}\); \} catch\(Exception ignored\) \{\}', re.DOTALL)
main, tts_count = tts_pattern.subn('', main, count=1)
if tts_count != 1:
    raise SystemExit("No se encontró el bloque de voz de inicio de Gestor; no se modificó la fuente.")
main = main.replace('if(brandTts!=null){brandTts.stop();brandTts.shutdown();brandTts=null;}', '', 1)

old_payment_picker = '''    private void choosePatientForPayment(){String period=currentPeriod();Cursor c=db.pendingFortnight(period);java.util.ArrayList<Long> ids=new java.util.ArrayList<>();java.util.ArrayList<String> names=new java.util.ArrayList<>();java.util.ArrayList<Double> balances=new java.util.ArrayList<>();java.util.ArrayList<String> labels=new java.util.ArrayList<>();while(c.moveToNext()){ids.add(c.getLong(0));names.add(c.getString(1));balances.add(c.getDouble(4));labels.add(c.getString(1)+" · pago quincenal "+money(c.getDouble(3))+" · pendiente "+money(c.getDouble(4)));}c.close();if(labels.isEmpty()){new AlertDialog.Builder(this).setTitle("Cobranza de esta quincena").setMessage("No hay pacientes pendientes para esta quincena. ¡Todo al corriente!").setPositiveButton("Aceptar",null).show();return;}new AlertDialog.Builder(this).setTitle("Pendientes: "+currentPeriodLabel()).setItems(labels.toArray(new String[0]),(d,w)->addPayment(ids.get(w),names.get(w),balances.get(w))).setNegativeButton("Cerrar",null).show();}
'''
new_payment_picker = '''    private void choosePatientForPayment(){
        showPatientSelector("Ingresar recibos · " + currentPeriodLabel(), true,
                (id, name, phone, balance) -> addPayment(id, name, balance));
    }
'''
if old_payment_picker not in main:
    raise SystemExit("No se encontró el selector de pago esperado en MainActivity.java; no se modificó la fuente.")
main = main.replace(old_payment_picker, new_payment_picker, 1)

old_choose_patient = '''    private void choosePatient(String title,PatientAction action){Cursor c=db.all();java.util.ArrayList<Long> ids=new java.util.ArrayList<>();java.util.ArrayList<String> labels=new java.util.ArrayList<>();java.util.ArrayList<String> names=new java.util.ArrayList<>();java.util.ArrayList<String> phones=new java.util.ArrayList<>();java.util.ArrayList<Double> balances=new java.util.ArrayList<>();while(c.moveToNext()){ids.add(c.getLong(0));names.add(c.getString(1));phones.add(c.getString(2));balances.add(c.getDouble(6));labels.add(c.getString(1)+" · pendiente "+money(c.getDouble(6)));}c.close();if(labels.isEmpty()){toast("Primero registra un paciente.");return;}new AlertDialog.Builder(this).setTitle(title).setItems(labels.toArray(new String[0]),(d,which)->action.run(ids.get(which),names.get(which),phones.get(which),balances.get(which))).setNegativeButton("Cancelar",null).show();}
'''
new_choose_patient = '''    private void choosePatient(String title, PatientAction action) {
        showPatientSelector(title, false, action);
    }

    private static final class PatientChoice {
        final long id; final String name, phone, treatment, policy, clinic;
        final double total, paid, balance, installment; final boolean periodPaid;
        PatientChoice(long id, String name, String phone, String treatment, double total, double paid,
                      double balance, double installment, String policy, String clinic, boolean periodPaid) {
            this.id=id; this.name=name; this.phone=phone; this.treatment=treatment; this.total=total;
            this.paid=paid; this.balance=balance; this.installment=installment; this.policy=policy;
            this.clinic=clinic; this.periodPaid=periodPaid;
        }
        String searchable() { return name+" "+phone+" "+treatment+" "+policy+" "+clinic; }
    }

    private void showPatientSelector(String title, boolean pendingOnly, PatientAction action) {
        java.util.ArrayList<PatientChoice> choices = new java.util.ArrayList<>();
        Cursor c = db.selectablePatients(currentPeriod(), pendingOnly);
        try {
            while (c.moveToNext()) choices.add(new PatientChoice(c.getLong(0), c.getString(1), c.getString(2),
                    c.getString(3), c.getDouble(4), c.getDouble(5), c.getDouble(6), c.getDouble(8),
                    c.getString(9), c.getString(10), c.getInt(12) != 0));
        } finally { c.close(); }
        if (choices.isEmpty()) {
            String message = pendingOnly ? "No hay clientes pendientes para esta quincena. Los clientes ya pagados no aparecen en esta lista." : "Todavía no hay clientes sincronizados en este dispositivo. Conecta Supabase y actualiza los datos.";
            new AlertDialog.Builder(this).setTitle(pendingOnly ? "Todo al corriente" : "Lista de clientes")
                    .setMessage(message).setPositiveButton("Aceptar", null).show();
            return;
        }
        LinearLayout box = new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(12), dp(5), dp(12), dp(8));
        TextView intro = txt(pendingOnly ? choices.size()+" clientes pendientes · toca una tarjeta para registrar el abono" : choices.size()+" clientes · busca por nombre, teléfono, póliza o clínica",
                12, dark ? Color.rgb(190,211,226) : muted, false);
        intro.setPadding(dp(2), dp(4), dp(2), dp(8)); box.addView(intro);
        EditText search = field(box, "Buscar nombre, teléfono, póliza o clínica…", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        search.setSingleLine(true);
        android.widget.ListView list = new android.widget.ListView(this); list.setDivider(null); list.setDividerHeight(dp(8));
        PatientChoiceAdapter adapter = new PatientChoiceAdapter(choices); list.setAdapter(adapter);
        TextView empty = txt("No hay coincidencias. Prueba otra búsqueda.", 14, muted, false); empty.setGravity(android.view.Gravity.CENTER); empty.setPadding(dp(10),dp(20),dp(10),dp(20));
        android.widget.LinearLayout.LayoutParams listParams = new android.widget.LinearLayout.LayoutParams(-1, Math.min(dp(360), (int)(getResources().getDisplayMetrics().heightPixels * .43f)));
        box.addView(list, listParams); box.addView(empty); list.setEmptyView(empty);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle(title).setView(box).setNegativeButton("Cerrar", null).create();
        list.setOnItemClickListener((parent, view, position, itemId) -> {
            PatientChoice selected = adapter.getItem(position);
            dialog.dismiss(); action.run(selected.id, selected.name, selected.phone, selected.balance);
        });
        search.addTextChangedListener(new android.text.TextWatcher() {
            public void beforeTextChanged(CharSequence s,int start,int count,int after) { }
            public void onTextChanged(CharSequence s,int start,int before,int count) { adapter.filter(s == null ? "" : s.toString()); intro.setText(adapter.getCount()+" de "+choices.size()+" clientes · toca una tarjeta para continuar"); }
            public void afterTextChanged(android.text.Editable e) { }
        });
        dialog.setOnShowListener(v -> styleDialog(dialog)); dialog.show(); styleDialog(dialog);
    }

    private final class PatientChoiceAdapter extends android.widget.BaseAdapter {
        private final java.util.ArrayList<PatientChoice> all = new java.util.ArrayList<>();
        private final java.util.ArrayList<PatientChoice> shown = new java.util.ArrayList<>();
        PatientChoiceAdapter(java.util.List<PatientChoice> rows) { all.addAll(rows); shown.addAll(rows); }
        void filter(String query) {
            String q = normalizeSearch(query); shown.clear();
            for (PatientChoice p : all) if (q.isEmpty() || normalizeSearch(p.searchable()).contains(q)) shown.add(p);
            notifyDataSetChanged();
        }
        public int getCount() { return shown.size(); }
        public PatientChoice getItem(int position) { return shown.get(position); }
        public long getItemId(int position) { return shown.get(position).id; }
        public android.view.View getView(int position, android.view.View convertView, android.view.ViewGroup parent) {
            PatientChoice p = getItem(position);
            LinearLayout card = new LinearLayout(MainActivity.this); card.setOrientation(LinearLayout.VERTICAL); card.setPadding(dp(14),dp(12),dp(14),dp(12));
            android.graphics.drawable.GradientDrawable shape = new android.graphics.drawable.GradientDrawable(); shape.setShape(android.graphics.drawable.GradientDrawable.RECTANGLE);
            shape.setCornerRadius(dp(15)); shape.setColor(dark ? Color.rgb(26,43,62) : Color.rgb(248,251,253));
            shape.setStroke(dp(1), dark ? Color.rgb(57,79,100) : Color.rgb(220,230,238)); card.setBackground(shape);
            TextView name = txt(p.name, 16, dark ? Color.WHITE : ink, true); card.addView(name);
            String details = (p.clinic == null || p.clinic.trim().isEmpty() ? "Sin clínica" : p.clinic.trim())
                    + "  ·  Póliza: " + (p.policy == null || p.policy.trim().isEmpty() ? "Sin póliza" : p.policy.trim());
            TextView meta = txt(details, 12, dark ? Color.rgb(190,211,226) : muted, false); meta.setPadding(0,dp(3),0,0); card.addView(meta);
            String contact = (p.phone == null || p.phone.trim().isEmpty() ? "Sin teléfono" : p.phone.trim())
                    + (p.treatment == null || p.treatment.trim().isEmpty() ? "" : "  ·  " + p.treatment.trim());
            TextView contactLine = txt(contact, 12, dark ? Color.rgb(190,211,226) : Color.rgb(66,91,113), false); contactLine.setPadding(0,dp(2),0,0); card.addView(contactLine);
            TextView moneyLine = txt("Pago quincenal: " + money(p.installment) + "   ·   Saldo: " + money(p.balance), 13,
                    p.balance > 0 ? Color.rgb(205,117,69) : Color.rgb(22,150,105), true); moneyLine.setPadding(0,dp(6),0,0); card.addView(moneyLine);
            TextView status = txt(p.periodPaid ? "✓ Quincena cubierta" : "• Pendiente esta quincena", 11,
                    p.periodPaid ? Color.rgb(22,150,105) : (dark ? Color.rgb(91,224,218) : Color.rgb(12,139,145)), true);
            status.setPadding(0,dp(2),0,0); card.addView(status);
            return card;
        }
    }

    private String normalizeSearch(String value) {
        String normalized = java.text.Normalizer.normalize(value == null ? "" : value, java.text.Normalizer.Form.NFD);
        StringBuilder clean = new StringBuilder(normalized.length());
        for (int i = 0; i < normalized.length(); i++) {
            char ch = normalized.charAt(i); int type = Character.getType(ch);
            if (type != Character.NON_SPACING_MARK && type != Character.COMBINING_SPACING_MARK && type != Character.ENCLOSING_MARK) clean.append(ch);
        }
        return clean.toString().toLowerCase(new Locale("es", "MX")).trim();
    }
'''
if old_choose_patient not in main:
    raise SystemExit("No se encontró el selector general esperado en MainActivity.java; no se modificó la fuente.")
main = main.replace(old_choose_patient, new_choose_patient, 1)

# ---------------------------------------------------------------------------
# 4) Replace the misleading client-only report with real receipt history from
#    collection_receipts (only rows permitted by Supabase RLS) plus a separate
#    tab for the locally cached/shared payments history.
# ---------------------------------------------------------------------------
old_reports = '''    private void showReports(){Cursor c=db.all();StringBuilder s=new StringBuilder("Resumen de cobranza\\n\\nTotal cobrado: ").append(money(db.sumQuincenal())).append("\\nPagos registrados: ").append(money(db.sumPaid())).append("\\nPacientes registrados: ").append(db.count()).append("\\n\\nCuentas pendientes:\\n");int n=0;while(c.moveToNext()){double balance=c.getDouble(6);if(balance>0){n++;s.append("• ").append(c.getString(1)).append(" — ").append(money(balance)).append("; pago quincenal ").append(money(c.getDouble(8))).append("\\n");}}c.close();if(n==0)s.append("No hay saldos pendientes.");new AlertDialog.Builder(this).setTitle("Reporte de cobranza").setMessage(s.toString()).setPositiveButton("Cerrar",null).show();}
'''
new_reports = '''    private static final class ReceiptHistoryItem {
        final String folio, patient, clinic, concept, notes, createdAt;
        final double amount, netAmount;
        ReceiptHistoryItem(String folio,String patient,String clinic,String concept,String notes,String createdAt,double amount,double netAmount) {
            this.folio=folio;this.patient=patient;this.clinic=clinic;this.concept=concept;this.notes=notes;this.createdAt=createdAt;this.amount=amount;this.netAmount=netAmount;
        }
        String searchable(){return folio+" "+patient+" "+clinic+" "+concept+" "+notes+" "+createdAt;}
    }
    private static final class PaymentHistoryItem {
        final long id, date; final String patient, clinic, policy, note, remoteId; final double amount;
        PaymentHistoryItem(long id,long date,String patient,String clinic,String policy,double amount,String note,String remoteId){
            this.id=id;this.date=date;this.patient=patient;this.clinic=clinic;this.policy=policy;this.amount=amount;this.note=note;this.remoteId=remoteId;
        }
        String searchable(){return patient+" "+clinic+" "+policy+" "+note+" "+remoteId;}
    }

    private void showReports() {
        java.util.ArrayList<PaymentHistoryItem> payments = readPaymentHistory();
        java.util.ArrayList<ReceiptHistoryItem> receipts = readCachedReceiptHistory();
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setPadding(dp(12),dp(5),dp(12),dp(8));
        LinearLayout summary = new LinearLayout(this); summary.setOrientation(LinearLayout.VERTICAL); summary.setPadding(dp(15),dp(12),dp(15),dp(12));
        android.graphics.drawable.GradientDrawable summaryBg = new android.graphics.drawable.GradientDrawable(android.graphics.drawable.GradientDrawable.Orientation.TL_BR,
                new int[]{Color.rgb(20,47,75),Color.rgb(25,68,91)}); summaryBg.setCornerRadius(dp(18)); summary.setBackground(summaryBg);
        TextView heading = txt("RESUMEN DE COBRANZA",11,Color.rgb(100,230,222),true); heading.setLetterSpacing(.10f); summary.addView(heading);
        TextView amount = txt(money(db.sumRecordedPayments()),25,Color.WHITE,true); amount.setPadding(0,dp(5),0,0); summary.addView(amount);
        TextView caption = txt("Total acumulado de pagos guardados",12,Color.rgb(198,216,232),false); summary.addView(caption);
        TextView stats = txt("Pagos: "+payments.size()+"   ·   Pacientes: "+db.count()+"   ·   Saldo pendiente: "+money(db.sumPending()),12,Color.rgb(220,235,246),true);
        stats.setPadding(0,dp(8),0,0); summary.addView(stats); root.addView(summary);

        LinearLayout tabs = new LinearLayout(this); tabs.setOrientation(LinearLayout.HORIZONTAL); tabs.setPadding(0,dp(9),0,dp(3));
        Button receiptsTab = miniButton("Recibos de Cobranza"); Button paymentsTab = miniButton("Pagos reportados");
        tabs.addView(receiptsTab,new LinearLayout.LayoutParams(0,-2,1)); tabs.addView(paymentsTab,new LinearLayout.LayoutParams(0,-2,1)); root.addView(tabs);
        EditText search = field(root,"Buscar folio, cliente, clínica o concepto…",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_CAP_SENTENCES); search.setSingleLine(true);
        TextView status = txt("Mostrando el historial guardado en este dispositivo; se intentará actualizar desde Supabase.",11,muted,false); status.setPadding(dp(2),dp(4),dp(2),dp(5)); root.addView(status);
        android.widget.ListView list = new android.widget.ListView(this); list.setDivider(null); list.setDividerHeight(dp(8));
        TextView empty = txt("No hay registros para mostrar.",14,muted,false); empty.setGravity(android.view.Gravity.CENTER); empty.setPadding(dp(10),dp(20),dp(10),dp(20));
        root.addView(list,new android.widget.LinearLayout.LayoutParams(-1,Math.min(dp(300),(int)(getResources().getDisplayMetrics().heightPixels*.34f)))); root.addView(empty); list.setEmptyView(empty);
        Button reload = miniButton("Actualizar histórico desde Supabase"); root.addView(reload);
        ReceiptHistoryAdapter receiptAdapter = new ReceiptHistoryAdapter(receipts); PaymentHistoryAdapter paymentAdapter = new PaymentHistoryAdapter(payments);
        final boolean[] receiptsSelected = {true};
        Runnable applyTab = () -> {
            receiptsTab.setTextColor(receiptsSelected[0] ? Color.WHITE : (dark ? Color.WHITE : Color.rgb(17,110,121)));
            receiptsTab.setBackgroundTintList(android.content.res.ColorStateList.valueOf(receiptsSelected[0] ? Color.rgb(12,139,145) : (dark ? Color.rgb(31,53,74) : Color.rgb(224,242,242))));
            paymentsTab.setTextColor(!receiptsSelected[0] ? Color.WHITE : (dark ? Color.WHITE : Color.rgb(17,110,121)));
            paymentsTab.setBackgroundTintList(android.content.res.ColorStateList.valueOf(!receiptsSelected[0] ? Color.rgb(12,139,145) : (dark ? Color.rgb(31,53,74) : Color.rgb(224,242,242))));
            list.setAdapter(receiptsSelected[0] ? receiptAdapter : paymentAdapter);
            search.setHint(receiptsSelected[0] ? "Buscar folio, cliente, clínica o concepto…" : "Buscar cliente, clínica, póliza o nota…");
            search.setText("");
            if(receiptsSelected[0]) {
                long cachedAt = cloud.cachedReceiptHistoryAt();
                status.setText(receiptAdapter.getCount()==0 ? "No hay recibos guardados en caché. Pulsa Actualizar para consultar los recibos visibles para esta cuenta." : "Recibos visibles: "+receiptAdapter.getCount()+(cachedAt>0 ? " · caché del "+formatTimestamp(cachedAt) : ""));
                empty.setText("No hay recibos de Cobranza visibles para esta cuenta. La consulta respeta los permisos de Supabase.");
            } else {
                status.setText("Pagos registrados localmente y pagos compartidos que se hayan sincronizado desde public.payments: "+payments.size());
                empty.setText("No hay pagos guardados. Conecta la cuenta desde Ajustes y sincroniza pacientes y pagos.");
            }
        };
        receiptsTab.setOnClickListener(v->{receiptsSelected[0]=true;applyTab.run();});
        paymentsTab.setOnClickListener(v->{receiptsSelected[0]=false;applyTab.run();});
        search.addTextChangedListener(new android.text.TextWatcher(){
            public void beforeTextChanged(CharSequence s,int start,int count,int after){}
            public void onTextChanged(CharSequence s,int start,int before,int count){if(receiptsSelected[0])receiptAdapter.filter(s==null?"":s.toString());else paymentAdapter.filter(s==null?"":s.toString());}
            public void afterTextChanged(android.text.Editable e){}
        });
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("Reporte de cobranza · histórico").setView(root).setNegativeButton("Cerrar",null).create();
        reload.setOnClickListener(v->loadReceiptHistory(receiptAdapter,status));
        dialog.setOnShowListener(v->styleDialog(dialog)); dialog.show(); styleDialog(dialog); applyTab.run();
        loadReceiptHistory(receiptAdapter,status);
    }

    private java.util.ArrayList<PaymentHistoryItem> readPaymentHistory() {
        java.util.ArrayList<PaymentHistoryItem> rows = new java.util.ArrayList<>(); Cursor c = db.allPayments();
        try { while(c.moveToNext()) rows.add(new PaymentHistoryItem(c.getLong(0),c.getLong(6),c.getString(2),c.getString(3),c.getString(4),c.getDouble(5),c.getString(7),c.getString(8))); }
        finally { c.close(); } return rows;
    }

    private java.util.ArrayList<ReceiptHistoryItem> readCachedReceiptHistory() {
        java.util.ArrayList<ReceiptHistoryItem> rows = new java.util.ArrayList<>(); org.json.JSONArray data = cloud.cachedReceiptHistory();
        for(int i=0;i<data.length();i++){org.json.JSONObject r=data.optJSONObject(i);if(r==null)continue;rows.add(new ReceiptHistoryItem(r.optString("folio","(sin folio)"),r.optString("patient_name","Paciente"),r.optString("clinic",""),r.optString("concept","Abono"),r.optString("notes",""),r.optString("created_at",""),r.optDouble("payment_amount",0),r.optDouble("net_amount",r.optDouble("payment_amount",0))));}
        return rows;
    }

    private void loadReceiptHistory(ReceiptHistoryAdapter adapter, TextView status) {
        if(!cloud.connected()){status.setText("Sin sesión de Supabase. Se conserva la caché disponible; conecta la cuenta en Ajustes para actualizar recibos.");return;}
        status.setText("Consultando recibos de Cobranza con los permisos de la cuenta…");
        new Thread(()->{
            String error=null; java.util.ArrayList<ReceiptHistoryItem> rows=new java.util.ArrayList<>();
            try { org.json.JSONArray data=cloud.fetchAccessibleReceiptHistory(); for(int i=0;i<data.length();i++){org.json.JSONObject r=data.optJSONObject(i);if(r==null)continue;rows.add(new ReceiptHistoryItem(r.optString("folio","(sin folio)"),r.optString("patient_name","Paciente"),r.optString("clinic",""),r.optString("concept","Abono"),r.optString("notes",""),r.optString("created_at",""),r.optDouble("payment_amount",0),r.optDouble("net_amount",r.optDouble("payment_amount",0))));} }
            catch(Exception ex){error=ex.getMessage()==null?"No se pudo consultar el historial.":ex.getMessage();}
            final String result=error; final java.util.ArrayList<ReceiptHistoryItem> loaded=rows;
            runOnUiThread(()->{if(isFinishing())return;if(result==null){adapter.setItems(loaded);status.setText(loaded.size()+" recibos accesibles desde Supabase · actualizado "+formatTimestamp(System.currentTimeMillis()));}else{status.setText("No se pudieron actualizar los recibos: "+result+". Se conserva la información disponible en caché.");}});
        }).start();
    }

    private String formatTimestamp(long millis) { return new SimpleDateFormat("dd/MM/yyyy HH:mm",new Locale("es","MX")).format(new Date(millis)); }
    private String formatRemoteTimestamp(String value) {
        if(value==null||value.trim().isEmpty())return "Fecha no disponible";
        String[] patterns={"yyyy-MM-dd'T'HH:mm:ss.SSSX","yyyy-MM-dd'T'HH:mm:ssX","yyyy-MM-dd'T'HH:mm:ss.SSS'Z'","yyyy-MM-dd'T'HH:mm:ss'Z'"};
        for(String pattern:patterns){try{SimpleDateFormat source=new SimpleDateFormat(pattern,Locale.US);source.setTimeZone(java.util.TimeZone.getTimeZone("UTC"));java.text.ParsePosition pos=new java.text.ParsePosition(0);Date d=source.parse(value,pos);if(d!=null&&pos.getIndex()==value.length())return formatTimestamp(d.getTime());}catch(Exception ignored){}}
        return value.replace('T',' ').replace("Z","");
    }

    private final class ReceiptHistoryAdapter extends android.widget.BaseAdapter {
        private final java.util.ArrayList<ReceiptHistoryItem> all=new java.util.ArrayList<>(),shown=new java.util.ArrayList<>();
        ReceiptHistoryAdapter(java.util.List<ReceiptHistoryItem> rows){setItems(rows);}
        void setItems(java.util.List<ReceiptHistoryItem> rows){all.clear();shown.clear();all.addAll(rows);shown.addAll(rows);notifyDataSetChanged();}
        void filter(String query){String q=normalizeSearch(query);shown.clear();for(ReceiptHistoryItem r:all)if(q.isEmpty()||normalizeSearch(r.searchable()).contains(q))shown.add(r);notifyDataSetChanged();}
        public int getCount(){return shown.size();} public ReceiptHistoryItem getItem(int p){return shown.get(p);} public long getItemId(int p){return p;}
        public android.view.View getView(int position,android.view.View convertView,android.view.ViewGroup parent){ReceiptHistoryItem r=getItem(position);LinearLayout card=historyCard();card.addView(txt("RECIBO  ·  "+r.folio,15,dark?Color.rgb(91,224,218):Color.rgb(12,139,145),true));card.addView(txt(r.patient,15,dark?Color.WHITE:ink,true));card.addView(txt(formatRemoteTimestamp(r.createdAt)+(r.clinic.isEmpty()?"":"  ·  "+r.clinic),12,dark?Color.rgb(190,211,226):muted,false));card.addView(txt((r.concept.isEmpty()?"Abono":r.concept)+"  ·  Cobrado: "+money(r.amount)+"  ·  Neto: "+money(r.netAmount),13,dark?Color.WHITE:ink,true));if(!r.notes.isEmpty())card.addView(txt(r.notes,12,muted,false));return card;}
    }

    private final class PaymentHistoryAdapter extends android.widget.BaseAdapter {
        private final java.util.ArrayList<PaymentHistoryItem> all=new java.util.ArrayList<>(),shown=new java.util.ArrayList<>();
        PaymentHistoryAdapter(java.util.List<PaymentHistoryItem> rows){setItems(rows);}
        void setItems(java.util.List<PaymentHistoryItem> rows){all.clear();shown.clear();all.addAll(rows);shown.addAll(rows);notifyDataSetChanged();}
        void filter(String query){String q=normalizeSearch(query);shown.clear();for(PaymentHistoryItem p:all)if(q.isEmpty()||normalizeSearch(p.searchable()).contains(q))shown.add(p);notifyDataSetChanged();}
        public int getCount(){return shown.size();} public PaymentHistoryItem getItem(int p){return shown.get(p);} public long getItemId(int p){return shown.get(p).id;}
        public android.view.View getView(int position,android.view.View convertView,android.view.ViewGroup parent){PaymentHistoryItem p=getItem(position);LinearLayout card=historyCard();card.addView(txt("PAGO REPORTADO  ·  "+money(p.amount),15,dark?Color.rgb(91,224,218):Color.rgb(12,139,145),true));card.addView(txt(p.patient,15,dark?Color.WHITE:ink,true));String meta=formatTimestamp(p.date)+(p.clinic.isEmpty()?"":"  ·  "+p.clinic)+(p.policy.isEmpty()?"":"  ·  Póliza "+p.policy);card.addView(txt(meta,12,dark?Color.rgb(190,211,226):muted,false));if(!p.note.isEmpty())card.addView(txt(p.note,12,dark?Color.rgb(190,211,226):Color.rgb(66,91,113),false));card.addView(txt(p.remoteId.isEmpty()?"Pendiente de sincronizar":"Sincronizado con Supabase",11,p.remoteId.isEmpty()?Color.rgb(205,117,69):Color.rgb(22,150,105),true));return card;}
    }

    private LinearLayout historyCard(){LinearLayout card=new LinearLayout(this);card.setOrientation(LinearLayout.VERTICAL);card.setPadding(dp(13),dp(12),dp(13),dp(12));android.graphics.drawable.GradientDrawable bg=new android.graphics.drawable.GradientDrawable();bg.setShape(android.graphics.drawable.GradientDrawable.RECTANGLE);bg.setCornerRadius(dp(14));bg.setColor(dark?Color.rgb(26,43,62):Color.rgb(248,251,253));bg.setStroke(dp(1),dark?Color.rgb(57,79,100):Color.rgb(220,230,238));card.setBackground(bg);return card;}
'''
if old_reports not in main:
    raise SystemExit("No se encontró el reporte original esperado en MainActivity.java; no se modificó la fuente.")
main = main.replace(old_reports, new_reports, 1)

# Confirm no startup speech remains, and make sure the new paths are wired in.
if 'TextToSpeech' in main or 'brandTts' in main:
    raise SystemExit("Quedó una referencia a la voz de inicio; se cancela la compilación.")
for marker in ('showPatientSelector(', 'class ReceiptHistoryAdapter', 'class PaymentHistoryAdapter', 'loadReceiptHistory(', 'db.sumRecordedPayments()'):
    if marker not in main:
        raise SystemExit("No se encontró la mejora requerida en MainActivity.java: " + marker)
if 'public Cursor selectablePatients(' not in db or 'public Cursor allPayments()' not in db:
    raise SystemExit("Faltan consultas locales de pacientes o historial en CustomerDbHelper.java.")
main_path.write_text(main, encoding="utf-8")

print("Gestor: renovación de sesión, selector moderno con búsqueda, historial de recibos/pagos y voz de inicio eliminada.")