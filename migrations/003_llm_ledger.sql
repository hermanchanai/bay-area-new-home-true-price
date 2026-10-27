-- 003_llm_ledger: what the cost ledger needs beyond 001's llm_calls columns.

ALTER TABLE llm_calls ADD COLUMN cached_tokens INTEGER;
ALTER TABLE llm_calls ADD COLUMN reasoning_tokens INTEGER;
ALTER TABLE llm_calls ADD COLUMN usage_json TEXT;
ALTER TABLE llm_calls ADD COLUMN error TEXT;
-- 1 when served from the disk cache (cost 0, no request sent).
ALTER TABLE llm_calls ADD COLUMN from_cache INTEGER NOT NULL DEFAULT 0;

ALTER TABLE eval_results ADD COLUMN run_id TEXT;
ALTER TABLE eval_results ADD COLUMN model TEXT;
