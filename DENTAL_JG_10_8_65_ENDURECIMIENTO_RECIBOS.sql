-- DENTAL JG 10.8.65 — endurecimiento de recibos y corrección de edición.
-- Ejecutar primero en una copia/entorno de prueba y revisar la auditoría. No borra datos.

-- AUDITORÍA: duplicados de recibo por pago dentro de la misma cuenta.
SELECT created_by, payment_id, count(*) AS recibos
FROM public.collection_receipts
WHERE payment_id IS NOT NULL
GROUP BY created_by, payment_id HAVING count(*) > 1;

-- AUDITORÍA: recibos con datos financieros sospechosos.
SELECT id, created_by, folio, payment_id, patient_id, payment_amount, tabulator,
       net_amount, previous_balance, new_balance, created_at
FROM public.collection_receipts
WHERE payment_amount <= 0 OR tabulator < 0 OR tabulator > payment_amount
   OR net_amount < 0 OR previous_balance < 0 OR new_balance < 0
ORDER BY created_at DESC;

-- Retira el índice global de la versión 10.8.64, cuya regla no coincidía con la búsqueda por cuenta.
-- La protección equivalente queda en el índice compuesto de abajo.
DROP INDEX IF EXISTS public.collection_receipts_payment_unique;

-- Unicidad alineada con la función: un recibo por pago y por cuenta creadora.
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM public.collection_receipts WHERE payment_id IS NOT NULL
    GROUP BY created_by, payment_id HAVING count(*) > 1
  ) THEN
    RAISE NOTICE 'No se creó el índice: hay recibos duplicados por cuenta/pago. Revisa primero los resultados de auditoría.';
  ELSE
    CREATE UNIQUE INDEX IF NOT EXISTS collection_receipts_created_payment_unique
      ON public.collection_receipts(created_by, payment_id) WHERE payment_id IS NOT NULL;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public.create_collection_receipt(
  p_payment_id uuid, p_patient_id uuid, p_patient_name text, p_clinic text,
  p_payment_amount numeric, p_previous_balance numeric, p_tabulator numeric,
  p_new_balance numeric, p_concept text, p_notes text, p_snapshot jsonb,
  p_manager_id uuid DEFAULT NULL
) RETURNS public.collection_receipts
LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE s public.collection_receipt_settings; r public.collection_receipts;
        v_folio text; v_payment_patient uuid; v_payment_amount numeric;
BEGIN
  IF auth.uid() IS NULL THEN RAISE EXCEPTION 'Inicia sesión para generar recibos.'; END IF;
  IF p_payment_amount IS NULL OR p_payment_amount <= 0 OR p_payment_amount > 9999999999.99 THEN RAISE EXCEPTION 'El importe debe ser mayor que cero y estar dentro del límite permitido.'; END IF;
  IF COALESCE(p_previous_balance,0) < 0 OR COALESCE(p_tabulator,0) < 0 OR COALESCE(p_new_balance,0) < 0 THEN RAISE EXCEPTION 'Los importes no pueden ser negativos.'; END IF;
  IF COALESCE(p_tabulator,0) > p_payment_amount THEN RAISE EXCEPTION 'El tabulador/descuento no puede superar el importe del recibo.'; END IF;
  IF length(COALESCE(p_patient_name,'')) > 200 OR length(COALESCE(p_clinic,'')) > 200 OR length(COALESCE(p_concept,'')) > 200 OR length(COALESCE(p_notes,'')) > 2000 THEN RAISE EXCEPTION 'Uno de los campos de texto supera el límite permitido.'; END IF;
  IF p_snapshot IS NOT NULL AND jsonb_typeof(p_snapshot) <> 'object' THEN RAISE EXCEPTION 'Los datos de impresión no tienen un formato válido.'; END IF;
  IF p_patient_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.patients pt WHERE pt.id=p_patient_id) THEN RAISE EXCEPTION 'El paciente no existe o no tienes permiso para consultarlo.'; END IF;
  IF p_manager_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.collection_managers m WHERE m.id=p_manager_id AND m.created_by=auth.uid()) THEN RAISE EXCEPTION 'El gestor seleccionado no pertenece a esta cuenta.'; END IF;
  IF p_payment_id IS NOT NULL THEN
    SELECT py.patient_id, py.amount INTO v_payment_patient, v_payment_amount FROM public.payments py WHERE py.id=p_payment_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'El pago no existe o no tienes permiso para consultarlo.'; END IF;
    IF p_patient_id IS NULL OR v_payment_patient IS DISTINCT FROM p_patient_id THEN RAISE EXCEPTION 'El pago no corresponde al paciente seleccionado.'; END IF;
    IF round(v_payment_amount::numeric,2) <> round(p_payment_amount::numeric,2) THEN RAISE EXCEPTION 'El importe del recibo debe coincidir con el pago registrado.'; END IF;
    PERFORM pg_advisory_xact_lock(hashtext(auth.uid()::text || ':' || p_payment_id::text));
    SELECT * INTO r FROM public.collection_receipts WHERE created_by=auth.uid() AND payment_id=p_payment_id LIMIT 1;
    IF FOUND THEN RETURN r; END IF;
  END IF;
  INSERT INTO public.collection_receipt_settings(user_id) VALUES(auth.uid()) ON CONFLICT(user_id) DO NOTHING;
  SELECT * INTO s FROM public.collection_receipt_settings WHERE user_id=auth.uid() FOR UPDATE;
  v_folio := COALESCE(s.folio_prefix,'JG-') || lpad(s.next_folio::text,greatest(s.folio_digits,length(s.next_folio::text)),'0');
  UPDATE public.collection_receipt_settings SET next_folio=next_folio+1,updated_at=now() WHERE user_id=auth.uid();
  INSERT INTO public.collection_receipts(created_by,folio,payment_id,patient_id,manager_id,patient_name,clinic,payment_amount,previous_balance,tabulator,net_amount,new_balance,concept,notes,receipt_snapshot)
  VALUES(auth.uid(),v_folio,p_payment_id,p_patient_id,p_manager_id,COALESCE(p_patient_name,''),COALESCE(p_clinic,''),p_payment_amount,COALESCE(p_previous_balance,0),COALESCE(p_tabulator,0),p_payment_amount-COALESCE(p_tabulator,0),COALESCE(p_new_balance,0),COALESCE(p_concept,'Abono'),COALESCE(p_notes,''),COALESCE(p_snapshot,'{}'::jsonb)) RETURNING * INTO r;
  RETURN r;
END; $$;
REVOKE ALL ON FUNCTION public.create_collection_receipt(uuid,uuid,text,text,numeric,numeric,numeric,numeric,text,text,jsonb,uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.create_collection_receipt(uuid,uuid,text,text,numeric,numeric,numeric,numeric,text,text,jsonb,uuid) TO authenticated;

-- Edición por RPC: no permite cambiar folio, propietario, pago o paciente de un recibo ya emitido.
CREATE OR REPLACE FUNCTION public.update_collection_receipt(
  p_receipt_id uuid, p_patient_name text, p_clinic text, p_payment_amount numeric,
  p_previous_balance numeric, p_tabulator numeric, p_new_balance numeric,
  p_concept text, p_notes text, p_snapshot jsonb, p_manager_id uuid DEFAULT NULL
) RETURNS public.collection_receipts
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public, pg_temp AS $$
DECLARE r public.collection_receipts; v_payment_amount numeric; v_payment_patient uuid;
BEGIN
  IF auth.uid() IS NULL THEN RAISE EXCEPTION 'Inicia sesión para editar recibos.'; END IF;
  IF p_payment_amount IS NULL OR p_payment_amount<=0 OR p_payment_amount>9999999999.99 OR COALESCE(p_previous_balance,0)<0 OR COALESCE(p_tabulator,0)<0 OR COALESCE(p_new_balance,0)<0 OR COALESCE(p_tabulator,0)>p_payment_amount THEN RAISE EXCEPTION 'Importes inválidos.'; END IF;
  IF length(COALESCE(p_patient_name,''))>200 OR length(COALESCE(p_clinic,''))>200 OR length(COALESCE(p_concept,''))>200 OR length(COALESCE(p_notes,''))>2000 THEN RAISE EXCEPTION 'Uno de los campos de texto supera el límite permitido.'; END IF;
  IF p_snapshot IS NOT NULL AND jsonb_typeof(p_snapshot)<>'object' THEN RAISE EXCEPTION 'Los datos de impresión no tienen un formato válido.'; END IF;
  SELECT * INTO r FROM public.collection_receipts WHERE id=p_receipt_id AND created_by=auth.uid() FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'El recibo no existe o no tienes permiso para editarlo.'; END IF;
  IF r.payment_id IS NOT NULL THEN
    SELECT py.patient_id, py.amount INTO v_payment_patient, v_payment_amount FROM public.payments py WHERE py.id=r.payment_id;
    IF NOT FOUND OR v_payment_patient IS DISTINCT FROM r.patient_id THEN RAISE EXCEPTION 'El pago asociado ya no es válido o no coincide con el paciente.'; END IF;
    IF round(v_payment_amount::numeric,2)<>round(p_payment_amount::numeric,2) THEN RAISE EXCEPTION 'No puedes cambiar el importe de un recibo vinculado a un pago registrado.'; END IF;
  END IF;
  IF p_manager_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.collection_managers m WHERE m.id=p_manager_id AND m.created_by=auth.uid()) THEN RAISE EXCEPTION 'El gestor seleccionado no pertenece a esta cuenta.'; END IF;
  UPDATE public.collection_receipts SET patient_name=COALESCE(p_patient_name,''), clinic=COALESCE(p_clinic,''), payment_amount=p_payment_amount, previous_balance=COALESCE(p_previous_balance,0), tabulator=COALESCE(p_tabulator,0), net_amount=p_payment_amount-COALESCE(p_tabulator,0), new_balance=COALESCE(p_new_balance,0), concept=COALESCE(p_concept,'Abono'), notes=COALESCE(p_notes,''), receipt_snapshot=COALESCE(p_snapshot,'{}'::jsonb), manager_id=p_manager_id
  WHERE id=r.id AND created_by=auth.uid() RETURNING * INTO r;
  RETURN r;
END; $$;
REVOKE ALL ON FUNCTION public.update_collection_receipt(uuid,text,text,numeric,numeric,numeric,numeric,text,text,jsonb,uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.update_collection_receipt(uuid,text,text,numeric,numeric,numeric,numeric,text,text,jsonb,uuid) TO authenticated;

-- Esta migración no crea pagos ni altera saldos de pacientes. Verifica y genera recibos únicamente.
