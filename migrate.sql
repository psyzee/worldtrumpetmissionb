CREATE TABLE IF NOT EXISTS tokens (
  id serial PRIMARY KEY,
  realm_id varchar(128),
  access_token text NOT NULL,
  refresh_token text NOT NULL,
  token_type varchar(32),
  expires_at timestamp,
  raw jsonb,
  created_at timestamp default now()
);
