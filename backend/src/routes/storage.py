from fastapi import APIRouter, Depends, HTTPException
from src.config.database import get_db
from src.models import DiskMigration
from src.schemas.storage import MigrationInput
from src.middlewares.auth import current_user, admin_only
from src.services import storage
from src.services.audit import record
router=APIRouter()
@router.get('/storage')
def get_storage(db=Depends(get_db),u=Depends(current_user)):
    return storage.overview(db)
@router.post('/storage/migrations',status_code=201)
def start(data:MigrationInput,db=Depends(get_db),u=Depends(admin_only)):
    m=storage.start_migration(db,data.new_dir)
    record(db,u.email,'storage.migration.start',{'id':m.id,'old_dir':m.old_dir,'new_dir':m.new_dir})
    storage.process_pass(db,m,actor=u.email)
    db.commit()
    return storage.overview(db)
@router.post('/storage/migrations/{id}/resume')
def resume(id:int,db=Depends(get_db),u=Depends(admin_only)):
    m=db.get(DiskMigration,id)
    if not m:raise HTTPException(404,'迁盘会话不存在')
    storage.resume_migration(db,m,actor=u.email)
    record(db,u.email,'storage.migration.resume',{'id':id})
    db.commit()
    return storage.overview(db)
@router.post('/storage/migrations/{id}/retire')
def retire(id:int,db=Depends(get_db),u=Depends(admin_only)):
    m=db.get(DiskMigration,id)
    if not m:raise HTTPException(404,'迁盘会话不存在')
    removed=storage.retire(db,m)
    record(db,u.email,'storage.migration.retired',{'id':id,'removed_files':removed})
    db.commit()
    return storage.overview(db)
