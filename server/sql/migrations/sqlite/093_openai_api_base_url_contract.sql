-- api_base_url is an OpenAI-compatible API base URL, including its version segment.
-- Move only the former canonical OpenAI default; custom compatible endpoints are opaque.
UPDATE ai_providers
SET api_base_url = 'https://api.openai.com/v1'
WHERE kind = 'openai'
  AND api_base_url = 'https://api.openai.com';