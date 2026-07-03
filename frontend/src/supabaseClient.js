import { createClient } from '@supabase/supabase-js'

const supabaseUrl = 'https://obxvtwhagjmdgcxizcva.supabase.co'
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

// Don't hard-crash the whole app when the anon key is missing (local dev
// without a .env) — auth just won't work until the key is provided.
if (!supabaseAnonKey) {
  console.warn('VITE_SUPABASE_ANON_KEY is not set — auth is disabled in this build.')
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey || 'anon-key-missing.local-dev-placeholder')