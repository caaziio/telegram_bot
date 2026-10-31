-- Run as the owner of these tables. On current self-hosted Supabase, public
-- objects should be owned by postgres (use utils/reassign-owner.sh if needed).
-- The application uses a server-side secret key, so public clients do not need
-- direct access to these backend tables.

BEGIN;

ALTER TABLE public.settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cto_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dex_validated ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wallet_payments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all operations on settings" ON public.settings;
DROP POLICY IF EXISTS "Allow all operations on workflows" ON public.workflows;
DROP POLICY IF EXISTS "Allow all operations on rules" ON public.rules;
DROP POLICY IF EXISTS "Allow all operations on cto_signals" ON public.cto_signals;
DROP POLICY IF EXISTS "Allow all operations on dex_validated" ON public.dex_validated;
DROP POLICY IF EXISTS "Allow all operations on wallet_payments" ON public.wallet_payments;

REVOKE ALL ON TABLE
    public.settings,
    public.workflows,
    public.rules,
    public.cto_signals,
    public.dex_validated,
    public.wallet_payments
FROM anon, authenticated;

REVOKE ALL ON SEQUENCE
    public.workflows_id_seq,
    public.rules_id_seq,
    public.cto_signals_id_seq,
    public.dex_validated_id_seq,
    public.wallet_payments_id_seq
FROM anon, authenticated;

COMMIT;
