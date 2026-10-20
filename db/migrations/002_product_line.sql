-- Which product line a chunk applies to: electronics | fashion | home | general (applies to everything).
ALTER TABLE kb.chunks ADD COLUMN IF NOT EXISTS product_line TEXT NOT NULL DEFAULT 'general';
CREATE INDEX IF NOT EXISTS chunks_product_line ON kb.chunks (product_line);