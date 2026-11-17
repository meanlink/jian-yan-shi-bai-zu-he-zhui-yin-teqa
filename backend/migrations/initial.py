import time
from sqlalchemy import text, inspect
from src.config.database import Base, engine, Session
from src.config.settings import ARTIFACT_DIR
from src import models
def migrate():
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        cols=[c['name'] for c in inspect(conn).get_columns('deliveries')]
        if 'disk' not in cols:
            conn.execute(text('ALTER TABLE deliveries ADD COLUMN disk VARCHAR(255) NULL'))
        conn.execute(text('UPDATE deliveries SET disk=:d WHERE disk IS NULL'),{'d':ARTIFACT_DIR})
    with Session() as db:
        if not db.get(models.StorageState,1):
            db.add(models.StorageState(id=1,current_dir=ARTIFACT_DIR,updated_at=time.time()))
            db.commit()
if __name__=='__main__':migrate()
