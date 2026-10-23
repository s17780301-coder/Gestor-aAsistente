-- DENTAL JG 1.1.2 — Eliminar un expediente histórico desde Cobranza
-- Ejecutar una sola vez en Supabase SQL Editor, en el mismo proyecto usado por Cobranza.
-- No modifica clientes activos ni otras filas históricas; elimina exclusivamente el archivo indicado.
CREATE OR REPLACE FUNCTION public.delete_archived_patient(p_archive_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
  v_user_id uuid := auth.uid();
  v_deleted integer;
BEGIN
  IF v_user_id IS NULL THEN
    RAISE EXCEPTION 'Inicia sesión para eliminar un expediente histórico.';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.app_user_roles AS r
    WHERE r.user_id = v_user_id
      AND lower(btrim(r.role)) = 'admin'
  ) THEN
    RAISE EXCEPTION 'Solo Administración puede eliminar expedientes históricos.';
  END IF;

  IF p_archive_id IS NULL THEN
    RAISE EXCEPTION 'Falta el identificador del expediente histórico.';
  END IF;

  DELETE FROM public.patient_account_archive
  WHERE id = p_archive_id;

  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  IF v_deleted = 0 THEN
    RAISE EXCEPTION 'No se encontró el expediente histórico solicitado; no se eliminó ningún registro.';
  END IF;

  RETURN true;
END;
$function$;

REVOKE ALL ON FUNCTION public.delete_archived_patient(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.delete_archived_patient(uuid) FROM anon;
GRANT EXECUTE ON FUNCTION public.delete_archived_patient(uuid) TO authenticated;
