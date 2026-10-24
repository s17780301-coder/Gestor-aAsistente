-- DENTAL JG — reparación de permisos para gestores de Cobranza
-- Corrige el error PostgreSQL 42501: permission denied for table collection_managers.
-- Ejecutar una sola vez en Supabase > SQL Editor, en el proyecto usado por Cobranza.
-- No desactiva RLS ni concede acceso a anon. Las políticas RLS existentes siguen aplicándose.

BEGIN;

DO $$
BEGIN
  IF to_regclass('public.collection_managers') IS NULL THEN
    RAISE EXCEPTION 'No existe public.collection_managers. No se aplicaron cambios.';
  END IF;
END
$$;

GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLE public.collection_managers
TO authenticated;

COMMIT;

-- Verificación de privilegios (debe devolver true para SELECT):
SELECT
  has_table_privilege('authenticated', 'public.collection_managers', 'SELECT') AS authenticated_can_select,
  has_table_privilege('authenticated', 'public.collection_managers', 'INSERT') AS authenticated_can_insert,
  has_table_privilege('authenticated', 'public.collection_managers', 'UPDATE') AS authenticated_can_update,
  has_table_privilege('authenticated', 'public.collection_managers', 'DELETE') AS authenticated_can_delete;
