SET search_path = psst, public;

-- How well known a lead is: the number of Wikipedia language editions with an article about it (its Wikidata
-- sitelinks). Briefs list the best-known leads first, so a session covers St Paul's before an office block.
ALTER TABLE research_leads ADD COLUMN fame integer;
CREATE INDEX research_leads_fame_idx ON research_leads (cell, fame DESC NULLS LAST) WHERE status = 'open';
