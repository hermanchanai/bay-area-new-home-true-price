-- Day 4: fields the loader keeps from extraction, so every true-price figure can show its source.
ALTER TABLE incentives ADD COLUMN label TEXT;
ALTER TABLE incentives ADD COLUMN evidence TEXT;
ALTER TABLE incentives ADD COLUMN eligibility TEXT;
ALTER TABLE incentives ADD COLUMN lender TEXT;
ALTER TABLE incentives ADD COLUMN included_in_price INTEGER NOT NULL DEFAULT 0;
ALTER TABLE incentives ADD COLUMN filled_from_offers INTEGER NOT NULL DEFAULT 0;
ALTER TABLE homes ADD COLUMN source_page TEXT;
ALTER TABLE homes ADD COLUMN was_price INTEGER;
ALTER TABLE incentive_values ADD COLUMN flags TEXT;
