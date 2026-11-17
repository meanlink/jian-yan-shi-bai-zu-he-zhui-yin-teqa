import {jobs,reports,storage} from '../api/entities.js';
import {download} from '../api/client.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
const migStatus={migrating:'迁移中',completed:'已核对 · 待退役',retired:'旧盘已退役'};
function storagePanel(store){
 const admin=session.user.role==='admin',m=store.migration;
 let h='<section class="panel storage-panel"><div class="section-title"><h2>归档盘迁移</h2><span>当前写入盘 <b>'+esc(store.current_dir)+'</b></span></div>';
 if(m){
  const pct=m.registered?Math.round(m.verified/m.registered*100):0;
  h+='<div class="disk-line"><div><small>源盘（旧）</small><strong>'+esc(m.old_dir)+'</strong></div><div class="disk-arrow">→</div><div><small>目标盘（新）</small><strong>'+esc(m.new_dir)+'</strong></div><span class="status">'+migStatus[m.status]+'</span></div>'
   +'<div class="migration-progress"><span style="width:'+pct+'%"></span></div>'
   +'<p class="hint">已核验 <b>'+m.verified+'</b> / 登记 '+m.registered+'（全部归档 '+m.total+'）· 待办 '+m.pending+' · 失败 '+m.failed+(store.inflight.length?' · 在途写入 '+store.inflight.length+' 个':'')+'</p>';
  if(m.failures&&m.failures.length)h+='<div class="warning"><strong>校验失败 · 保留旧盘读取</strong><ul>'+m.failures.map(f=>'<li>'+esc(f.filename)+'：'+esc(f.error)+'</li>').join('')+'</ul></div>';
  if(m.blockers&&m.blockers.length&&m.status!=='retired')h+='<p class="hint">退役阻塞：'+m.blockers.map(esc).join('；')+'</p>';
  h+='<div class="storage-actions">'
   +(m.status==='migrating'?'<button class="button small" id="resume-migration" data-id="'+m.id+'" '+(admin?'':'disabled')+'>恢复 / 重试迁盘</button>':'')
   +(m.status!=='retired'?'<button class="button small" id="retire-migration" data-id="'+m.id+'" '+(admin&&m.status==='completed'?'':'disabled')+'>退役旧盘</button>':'')
   +'</div>';
 }else{
  const retired=store.migrations.find(x=>x.status==='retired');
  h+=(retired?'<p class="hint">上次迁盘 #'+retired.id+'：'+esc(retired.old_dir)+' → '+esc(retired.new_dir)+' · 旧盘已于 '+date(retired.retired_at)+' 退役</p>':'')
   +'<form id="migration-form"><label>新盘位置<input name="new_dir" value="/artifacts-b" required minlength="2"></label><button type="submit" class="primary" '+(admin?'':'disabled')+'>发起迁盘</button></form>'
   +'<p class="hint">发起后新归档立即写入新盘；存量归档由后台逐份复制并核验两件附件（CSV + JSON），核验齐全才改读新盘，全部核对完成才允许退役旧盘。仅负责人可操作。</p>';
 }
 return h+'</section>';
}
export async function reportPage(){
 const [list,deliveries,allJobs,store]=await Promise.all([reports.list(),reports.deliveries(),jobs.list(),storage.status()]);workspace.reports=list;
 const approved=allJobs.filter(j=>j.approved&&j.status==='completed');
 return heading('05 / DISTRIBUTION','定时分发与归档','把批准后的结果交付到本地业务收件箱。每份 CSV 与 JSON 留有时间、目标组和校验指纹。','<button class="button" id="refresh-reports">刷新收件箱 ↻</button>')+storagePanel(store)+'<div class="report-grid"><section class="panel"><div class="section-title"><h2>创建分发计划</h2></div><form id="report-form"><label>简报名称<input name="name" required minlength="2" value="日间业务复核简报"></label><label>已批准分析<select name="job_id" required>'+approved.map(j=>'<option value="'+j.id+'">'+esc(j.name)+'</option>').join('')+'</select></label><label>业务接收组<input name="destination" value="业务复核组 · 本地收件箱" required></label><div class="parameter-grid"><label>分发周期<select name="interval_seconds"><option value="86400">每日</option><option value="604800">每周</option><option value="60">每分钟（验收）</option></select></label><label>首次执行<select name="delay_seconds"><option value="0">立即</option><option value="60">1 分钟后</option><option value="3600">1 小时后</option></select></label></div><button type="submit" class="primary" '+(session.user.role!=='admin'||!approved.length?'disabled':'')+'>保存分发计划</button></form><p class="hint">由后台 worker 按到期时间生成文件。仅负责人可创建或暂停；接收组为本地标签。</p></section><section><div class="section-title"><h2>运行中的计划</h2><span>'+list.filter(r=>r.enabled).length+' 条已启用</span></div><div class="schedule-list">'+list.map(r=>'<article><div><strong>'+esc(r.name)+'</strong><p>'+esc(r.destination)+'</p><small>下次 '+date(r.next_run)+' · 周期 '+r.interval_seconds+' 秒</small></div><button class="button small" data-toggle="'+r.id+'" '+(session.user.role!=='admin'?'disabled':'')+'>'+(r.enabled?'暂停':'启用')+'</button></article>').join('')+'</div><section class="panel inbox"><div class="section-title"><h2>本地收件箱</h2><span>'+deliveries.length+' 份归档</span></div>'+deliveries.map(d=>'<div class="delivery"><div><strong>'+esc(d.filename)+'</strong><p>'+esc(d.destination)+' · '+date(d.created_at)+'</p><small>SHA256 '+esc(d.checksum.slice(0,24))+'… · 读取盘 '+esc(d.disk||'—')+'</small></div><div><button class="text-button" data-download="'+d.id+'" data-format="csv">CSV ↓</button><button class="text-button" data-download="'+d.id+'" data-format="json">JSON ↓</button></div></div>').join('')+(!deliveries.length?'<p class="empty">计划执行后，文件会出现在此处。等待片刻后刷新。</p>':'')+'</section></section></div>';
}
export function bindReports(refresh){
 document.querySelector('#refresh-reports').onclick=refresh;
 submit(document.querySelector('#report-form'),async f=>{await reports.create({name:f.get('name'),job_id:Number(f.get('job_id')),destination:f.get('destination'),interval_seconds:Number(f.get('interval_seconds')),delay_seconds:Number(f.get('delay_seconds'))});notify('计划已保存，后台将按到期时间归档');await refresh();});
 document.querySelectorAll('[data-toggle]').forEach(b=>b.onclick=async()=>{try{await reports.toggle(b.dataset.toggle);await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-download]').forEach(b=>b.onclick=()=>download(b.dataset.download,b.dataset.format).catch(e=>notify(e.message,true)));
 const mf=document.querySelector('#migration-form');
 if(mf)submit(mf,async f=>{await storage.start(f.get('new_dir'));notify('迁盘已发起：新归档写入新盘，存量归档后台核验搬迁');await refresh();});
 const rm=document.querySelector('#resume-migration');
 if(rm)rm.onclick=async()=>{try{await storage.resume(rm.dataset.id);notify('已恢复原迁盘，失败项重新进入待办');await refresh();}catch(e){notify(e.message,true);}};
 const rt=document.querySelector('#retire-migration');
 if(rt)rt.onclick=async()=>{try{await storage.retire(rt.dataset.id);notify('旧盘已退役，已核验的源件已清理');await refresh();}catch(e){notify(e.message,true);}};
}
