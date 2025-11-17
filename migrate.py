import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv()
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise SystemExit('Set DATABASE_URL before running migrate.py')
engine = create_engine(DATABASE_URL)
with engine.connect() as conn:
    with open('migrate.sql','r') as f:
        sql = f.read()
    conn.execute(text(sql))
    conn.commit()
    print('Migration complete.')
