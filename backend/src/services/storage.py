import os, time, json, shutil, hashlib
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, func
from redis import Redis
from src.config.database import Session
from src.config.settings import ARTIFACT_DIR, ARTIFACT_DIR_NEXT, REDIS_URL
from src.models import Delivery, StorageState, DiskMigration, DeliveryMigration
from src.services.audit import record
INFLIGHT_PREFIX = '.inflight-'
LOCK_KEY = 'storage:migration:lock'
STALE_SECONDS = 600
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''): h.update(chunk)
    return h.hexdigest()
def atomic_write(path, data):
    tmp = path + '.tmp-' + uuid4().hex
    with open(tmp, 'wb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
def ensure_storage_state(db):
    s = db.get(StorageState, 1)
    if not s:
        s = StorageState(id=1, current_dir=ARTIFACT_DIR, updated_at=time.time())
        db.add(s); db.flush()
    return s
def current_dir(db):
    return ensure_storage_state(db).current_dir
# 在途写入以目标盘内的标记文件登记：写入前先落标记，写完即删。
# 文件标记对 backend 与 worker 均可见，崩溃残留可按时间清理，且不占用数据库事务。
def begin_inflight(directory):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, INFLIGHT_PREFIX + uuid4().hex)
    with open(path, 'w') as f: f.write(str(time.time()))
    return path
def end_inflight(path):
    try: os.remove(path)
    except FileNotFoundError: pass
def inflight_markers(*dirs):
    out = []
    for d in dict.fromkeys(d for d in dirs if d):
        if not os.path.isdir(d): continue
        for name in os.listdir(d):
            if name.startswith(INFLIGHT_PREFIX):
                p = os.path.join(d, name)
                out.append({'dir': d, 'marker': name, 'started_at': os.path.getmtime(p)})
    return out
def recover_storage():
    # worker 启动恢复：清理崩溃残留的标记与临时文件（正常写入以毫秒计，10 分钟兜底）。
    with Session() as db:
        dirs = {current_dir(db)}
        for m in db.scalars(select(DiskMigration)):
            dirs.add(m.old_dir); dirs.add(m.new_dir)
    cutoff = time.time() - STALE_SECONDS
    for d in dirs:
        if not d or not os.path.isdir(d): continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if (name.startswith(INFLIGHT_PREFIX) or '.tmp-' in name) and os.path.getmtime(p) < cutoff:
                try: os.remove(p)
                except OSError: pass
def _try_lock():
    noop = lambda: None
    try:
        r = Redis.from_url(REDIS_URL, socket_timeout=2)
        token = uuid4().hex
        if r.set(LOCK_KEY, token, nx=True, px=30000):
            def release():
                try:
                    if r.get(LOCK_KEY) == token.encode(): r.delete(LOCK_KEY)
                except Exception: pass
                finally: r.close()
            return release, True
        r.close()
        return noop, False
    except Exception:
        return noop, True  # Redis 不可用时退化为无锁（本地单进程开发），处理逻辑本身幂等
def verify_pair(directory, filename, checksum):
    # 每份简报两件附件：CSV 本体与 JSON 边车，二者齐全且校验和一致才算核验通过。
    errors = []
    csv_path = os.path.join(directory, filename)
    json_path = csv_path + '.json'
    if not os.path.exists(csv_path): errors.append('CSV 附件缺失')
    elif sha256_file(csv_path) != checksum: errors.append('CSV 校验和不符，文件可能损坏')
    if not os.path.exists(json_path): errors.append('JSON 附件缺失')
    else:
        try:
            with open(json_path) as f: meta = json.load(f)
            if meta.get('checksum') != checksum: errors.append('JSON 内嵌校验和与归档不符')
        except Exception: errors.append('JSON 附件无法解析')
    return errors
def copy_pair(old_dir, new_dir, filename):
    # 幂等复制：目标已存在且与源一致则跳过；不一致则重拷。绝不删除源件。
    errors = []
    for suffix, label in (('', 'CSV'), ('.json', 'JSON')):
        src = os.path.join(old_dir, filename + suffix)
        dst = os.path.join(new_dir, filename + suffix)
        if not os.path.exists(src):
            errors.append(label + ' 附件在源盘缺失'); continue
        if os.path.exists(dst) and sha256_file(dst) == sha256_file(src): continue
        tmp = dst + '.tmp-' + uuid4().hex
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    return errors
def register_pending(db, migration):
    # 补登记：切换前已开写、切换后才提交的归档也会在此被纳入搬迁，避免漏件。
    registered = set(db.scalars(select(DeliveryMigration.delivery_id).where(DeliveryMigration.migration_id == migration.id)))
    for d in db.scalars(select(Delivery)):
        if d.id not in registered:
            db.add(DeliveryMigration(migration_id=migration.id, delivery_id=d.id, status='pending'))
    db.flush()
def process_item(db, migration, dm):
    d = db.get(Delivery, dm.delivery_id)
    if not d:
        dm.status = 'failed'; dm.error = '归档记录不存在'; return
    if (d.disk or migration.old_dir) == migration.new_dir:
        # 切换后出生在新盘的归档：原地核验，无需复制。
        errors = verify_pair(migration.new_dir, d.filename, d.checksum)
        if errors: dm.status = 'failed'; dm.error = '新盘原地核验失败：' + '；'.join(errors)
        else: dm.status = 'verified'; dm.verified_at = time.time(); dm.error = None
        return
    src_errors = verify_pair(migration.old_dir, d.filename, d.checksum)
    if src_errors:
        dm.status = 'failed'
        dm.error = '源盘核验失败（保留旧盘读取）：' + '；'.join(src_errors)
        return
    copy_errors = copy_pair(migration.old_dir, migration.new_dir, d.filename)
    if copy_errors:
        dm.status = 'failed'; dm.error = '复制失败（保留旧盘读取）：' + '；'.join(copy_errors)
        return
    dm.copied_at = time.time()
    dst_errors = verify_pair(migration.new_dir, d.filename, d.checksum)
    if dst_errors:
        dm.status = 'failed'
        dm.error = '新盘核验失败（保留旧盘读取）：' + '；'.join(dst_errors)
        return
    # 两件附件核验齐全，该简报改读新盘。
    dm.status = 'verified'; dm.verified_at = time.time(); dm.error = None
    d.disk = migration.new_dir
def check_completion(db, migration, actor):
    if migration.status != 'migrating': return
    total = db.scalar(select(func.count()).select_from(Delivery))
    verified = db.scalar(select(func.count()).select_from(DeliveryMigration).where(DeliveryMigration.migration_id == migration.id, DeliveryMigration.status == 'verified'))
    if total == verified and not inflight_markers(migration.old_dir, migration.new_dir):
        migration.status = 'completed'; migration.finished_at = time.time(); migration.updated_at = time.time()
        record(db, actor, 'storage.migration.completed', {'id': migration.id, 'deliveries': total})
def process_pass(db, migration, actor='worker', limit=50):
    release, acquired = _try_lock()
    if not acquired: return False
    try:
        register_pending(db, migration)
        items = list(db.scalars(select(DeliveryMigration).where(DeliveryMigration.migration_id == migration.id, DeliveryMigration.status == 'pending').limit(limit)))
        for dm in items: process_item(db, migration, dm)
        migration.updated_at = time.time()
        db.flush()
        check_completion(db, migration, actor)
        return True
    finally:
        release()
def start_migration(db, new_dir):
    new_dir = os.path.abspath((new_dir or '').strip() or ARTIFACT_DIR_NEXT)
    state = ensure_storage_state(db)
    old_dir = state.current_dir
    active = db.scalar(select(DiskMigration).where(DiskMigration.status.in_(['migrating', 'completed'])))
    if active: raise HTTPException(409, f'已有进行中的迁盘会话 #{active.id}，请先恢复完成或退役旧盘')
    if new_dir == os.path.abspath(old_dir): raise HTTPException(422, '新盘位置不能与当前盘相同')
    try:
        os.makedirs(new_dir, exist_ok=True)
        probe = os.path.join(new_dir, '.probe-' + uuid4().hex)
        with open(probe, 'w') as f: f.write('ok')
        os.remove(probe)
    except Exception:
        raise HTTPException(422, '新盘位置不可写：' + new_dir)
    now = time.time()
    m = DiskMigration(old_dir=old_dir, new_dir=new_dir, status='migrating', created_at=now, updated_at=now)
    db.add(m); db.flush()
    # 切换写入盘：此后新归档直接落入新盘。
    state.current_dir = new_dir; state.updated_at = now
    db.flush()
    return m
def resume_migration(db, migration, actor):
    if migration.status == 'retired': raise HTTPException(409, '旧盘已退役，请发起新的迁盘')
    if migration.status != 'migrating': raise HTTPException(409, '迁盘已完成核对，可直接退役旧盘')
    # 重试仅把失败项置回待办：登记行有唯一约束不会重复，复制按校验和幂等，源件不删。
    for dm in db.scalars(select(DeliveryMigration).where(DeliveryMigration.migration_id == migration.id, DeliveryMigration.status == 'failed')):
        dm.status = 'pending'; dm.error = None
    db.flush()
    process_pass(db, migration, actor=actor)
def retire_blockers(db, migration):
    if migration.status == 'retired': return ['旧盘已退役']
    blockers = []
    total = db.scalar(select(func.count()).select_from(Delivery))
    rows = list(db.scalars(select(DeliveryMigration).where(DeliveryMigration.migration_id == migration.id)))
    pending = sum(1 for r in rows if r.status == 'pending')
    failed = sum(1 for r in rows if r.status == 'failed')
    unregistered = total - len(rows)
    if unregistered > 0: blockers.append(f'{unregistered} 份归档尚未纳入搬迁登记')
    if pending: blockers.append(f'{pending} 份归档待搬迁核验')
    if failed: blockers.append(f'{failed} 份归档校验失败，需修复后重试')
    markers = inflight_markers(migration.old_dir, migration.new_dir)
    if markers: blockers.append(f'{len(markers)} 个在途写入未完结')
    if migration.status != 'completed' and not blockers: blockers.append('迁盘核对尚未完成')
    return blockers
def retire(db, migration):
    blockers = retire_blockers(db, migration)
    if blockers: raise HTTPException(409, '；'.join(blockers))
    # 只清理本次迁盘中已核验登记的源件，逐文件名删除，避免误删。
    removed = 0
    for dm in db.scalars(select(DeliveryMigration).where(DeliveryMigration.migration_id == migration.id, DeliveryMigration.status == 'verified')):
        d = db.get(Delivery, dm.delivery_id)
        if not d: continue
        for suffix in ('', '.json'):
            p = os.path.join(migration.old_dir, d.filename + suffix)
            if os.path.exists(p):
                os.remove(p); removed += 1
    migration.status = 'retired'; migration.retired_at = time.time(); migration.updated_at = time.time()
    return removed
def overview(db):
    state = ensure_storage_state(db)
    migrations = list(db.scalars(select(DiskMigration).order_by(DiskMigration.id.desc())))
    total = db.scalar(select(func.count()).select_from(Delivery))
    def brief(m):
        rows = list(db.scalars(select(DeliveryMigration).where(DeliveryMigration.migration_id == m.id)))
        count = lambda s: sum(1 for r in rows if r.status == s)
        return {'id': m.id, 'old_dir': m.old_dir, 'new_dir': m.new_dir, 'status': m.status,
                'created_at': m.created_at, 'finished_at': m.finished_at, 'retired_at': m.retired_at,
                'total': total, 'registered': len(rows), 'pending': count('pending'),
                'verified': count('verified'), 'failed': count('failed')}
    data = {'current_dir': state.current_dir, 'migration': None, 'migrations': [brief(m) for m in migrations], 'inflight': []}
    active = next((m for m in migrations if m.status in ('migrating', 'completed')), None)
    if active:
        b = brief(active)
        failures = []
        for r in db.scalars(select(DeliveryMigration).where(DeliveryMigration.migration_id == active.id, DeliveryMigration.status == 'failed')):
            d = db.get(Delivery, r.delivery_id)
            failures.append({'delivery_id': r.delivery_id, 'filename': d.filename if d else '?', 'error': r.error})
        b['failures'] = failures
        b['blockers'] = retire_blockers(db, active)
        data['migration'] = b
        data['inflight'] = inflight_markers(active.old_dir, active.new_dir)
    else:
        data['inflight'] = inflight_markers(state.current_dir)
    return data
