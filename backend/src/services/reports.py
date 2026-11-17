import time, os, csv, io, hashlib, json
from fastapi import HTTPException
from src.models import Job, Report, Delivery
from src.services.storage import current_dir, begin_inflight, end_inflight, atomic_write
def create(db, data):
    j=db.get(Job,data.job_id)
    if not j or j.status!='completed' or not j.approved:
        raise HTTPException(409,'请先选择已完成且获批准的分析结果')
    r=Report(name=data.name, job_id=j.id, destination=data.destination, interval_seconds=data.interval_seconds, next_run=time.time()+data.delay_seconds, enabled=True)
    db.add(r); db.flush()
    return r
def deliver(db, report):
    j=db.get(Job,report.job_id)
    if not j or not j.approved: return
    out=io.StringIO()
    out.write('\ufeff')
    table=j.result.get('table',{})
    cols=table.get('columns',[])
    writer=csv.writer(out)
    def safe(v):
        if isinstance(v,(int,float)) and not isinstance(v,bool): return v
        text=str(v)
        return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
    writer.writerow([safe(c['label']) for c in cols])
    for row in table.get('rows',[]): writer.writerow([safe(row.get(c['key'],'')) for c in cols])
    data=out.getvalue().encode('utf-8')
    directory=current_dir(db)
    os.makedirs(directory,exist_ok=True)
    delivery=Delivery(report_id=report.id, created_at=time.time(), checksum=hashlib.sha256(data).hexdigest(), filename='', destination=report.destination, disk=directory)
    db.add(delivery); db.flush()
    delivery.filename=f'report-{report.id}-delivery-{delivery.id}.csv'
    # 开写先落在途标记：迁盘核对与旧盘退役都会等待在途写入完结。
    marker=begin_inflight(directory)
    try:
        atomic_write(os.path.join(directory,delivery.filename), data)
        meta=json.dumps({'job':j.name,'params':j.params,'result':j.result,'checksum':delivery.checksum},ensure_ascii=False,allow_nan=False).encode('utf-8')
        atomic_write(os.path.join(directory,delivery.filename+'.json'), meta)
    finally:
        end_inflight(marker)
    report.next_run=time.time()+report.interval_seconds
    return delivery
