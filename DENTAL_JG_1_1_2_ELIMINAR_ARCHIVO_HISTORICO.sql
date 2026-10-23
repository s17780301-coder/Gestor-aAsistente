-- DENTAL JG 1.1.2 — Eliminar un expediente histórico desde Cobranza
-- Ejecutar una sola vez en Supabase SQL Editor, en el mismo proyecto usado por Cobranza.
-- No modifica clientes activos ni otras filas históricas; elimina exclusivamente el archivo indicado.
DROP FUNCTION IF EXISTS public.delete_archived_patient(uuid);

CREATE OR REPLACE FUNCTION public.delete_archived_patient(p_archive_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, auth
AS $function$
DECLARE
  v_uid uuid := auth.uid();
  v_is_admin boolean := false;
BEGIN
  IF v_uid IS NULL THEN
    RAISE EXCEPTION 'Se requiere una sesión autenticada.';
  END IF;
  IF p_archive_id IS NULL THEN
    RAISE EXCEPTION 'Falta el identificador del expediente histórico.';
  END IF;
  IF to_regclass('public.app_user_roles') IS NULL THEN
    RAISE EXCEPTION 'No existe public.app_user_roles; no se eliminó ningún expediente.';
  END IF;
  IF to_regclass('public.patient_account_archive') IS NULL THEN
    RAISE EXCEPTION 'No existe public.patient_account_archive.';
  END IF;

  SELECT EXISTS (
    SELECT 1
    FROM public.app_user_roles r
    WHERE lower(btrim(coalesce(to_jsonb(r)->>'role', ''))) = 'admin'
      AND (
        to_jsonb(r)->>'user_id' = v_uid::text
        OR to_jsonb(r)->>'id_usuario' = v_uid::text
      )
  ) INTO v_is_admin;

  IF NOT v_is_admin THEN
    RAISE EXCEPTION 'Solo Administración puede eliminar expedientes históricos.';
  END IF;

  DELETE FROM public.patient_account_archive WHERE id = p_archive_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'No se encontró el expediente histórico solicitado; no se eliminó ningún registro.';
  END IF;
END;
$function$;

REVOKE ALL ON FUNCTION public.delete_archived_patient(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.delete_archived_patient(uuid) FROM anon;
GRANT EXECUTE ON FUNCTION public.delete_archived_patient(uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.delete_archived_patient(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.delete_archived_patient(uuid) FROM anon;
GRANT EXECUTE ON FUNCTION public.delete_archived_patient(uuid) TO authenticated;
