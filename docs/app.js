'use strict';
const $=id=>document.getElementById(id),rooms=['A-1','B-2','C-3','D-4','E-5','F-6','H-8'];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=s=>new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',hour:'2-digit',minute:'2-digit'}).format(new Date(s));
const df=s=>new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',day:'2-digit',month:'2-digit'}).format(new Date(s));
let endpoint=localStorage.getItem('crew-endpoint')||'',token=sessionStorage.getItem('crew-token')||'',role=sessionStorage.getItem('crew-role')||'',state=null,demo=false,payload=null,offset=0,lastFetch=0;
const query=new URLSearchParams(location.search),screen=query.get('screen')||localStorage.getItem('crew-screen')||'hall';
rooms.forEach(r=>{const op=document.createElement('option');op.value=r;op.textContent='غرفة '+r;$('view').append(op)});
$('view').value=['hall','admin',...rooms].includes(screen)?screen:'hall';
$('orientation').value=query.get('orientation')||localStorage.getItem('crew-orientation-'+$('view').value)||'auto';
if(!['auto','portrait','landscape'].includes($('orientation').value))$('orientation').value='auto';
$('endpoint').value=endpoint;
const person='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3M9 14l3 6 3-6"/></svg>';
function notice(text,error=false){$('notice').textContent=text;$('notice').className=error?'error':''}
async function api(path,body){const res=await fetch(endpoint+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(12000)});let data;try{data=await res.json()}catch{throw Error('رابط الخدمة لا يعيد استجابة صحيحة')}if(!res.ok){if(res.status===401){token='';sessionStorage.removeItem('crew-token');$('setup').hidden=false}throw Error(data.error||'تعذر الاتصال')}return data}
function orientation(){document.body.dataset.orientation=$('orientation').value;localStorage.setItem('crew-orientation-'+$('view').value,$('orientation').value)}
function render(){
 orientation();const view=$('view').value,isRoom=rooms.includes(view);
 document.body.classList.toggle('roomview',isRoom);$('admin').hidden=view!=='admin'||role!=='admin'||demo;$('upcoming').hidden=isRoom;$('logout').hidden=!token;
 $('title').textContent=isRoom?'غرفة الإحاطة '+view:view==='admin'?'لوحة إدارة الإحاطة':'رحلتكم تبدأ من هنا';
 if(!state){$('board').innerHTML='';return}
 const flights=state.flights,t=Date.now()+offset;
 $('board').innerHTML=(isRoom?[view]:rooms).map(room=>{
  const f=flights.find(f=>f.room===room&&!f.done&&f.start&&new Date(f.end)>t);
  let content='<div class="idle">بانتظار الرحلة القادمة</div><p>نتمنى لكم رحلة سعيدة</p>';
  if(f)content='<div class="flight" dir="ltr">'+esc(f.number)+'</div><div class="destination">'+esc(f.destination)+'</div><div class="sccm">'+person+'<b>SCCM</b><span>'+esc(f.sccm||'لم يُحدّد رئيس الطاقم')+'</span></div><div class="times"><span>'+fmt(f.start)+' – '+fmt(f.end)+'</span><span class="countdown" data-end="'+esc(f.end)+'"></span></div>'+(view==='admin'&&role==='admin'&&!demo?'<button class="extend" data-id="'+esc(f.id)+'">تمديد 5 دقائق</button>':'');
  return '<article class="card '+(f?'active':'free')+'"><div class="cardtop"><span class="room" dir="ltr">'+room+'</span><span class="status">'+(f?'الإحاطة جارية':'متاحة')+'</span></div>'+content+'</article>';
 }).join('');
 const upcoming=flights.filter(f=>!f.done&&!f.start).slice(0,14);
 $('queue').innerHTML=upcoming.length?'<div class="qrow"><span>الرحلة</span><span>الوجهة</span><span>البريفينج</span><span>الحالة</span></div>'+upcoming.map(f=>'<div class="qrow"><span dir="ltr">'+esc(f.number)+'</span><span>'+esc(f.destination)+'</span><span dir="ltr">'+df(f.briefing)+' '+fmt(f.briefing)+'</span><span>'+(new Date(f.briefing)<=t?'بانتظار غرفة':'قادمة')+'</span></div>').join(''):'<p>لا توجد رحلات في قائمة الانتظار</p>';
 tickClock();
}
function tickClock(){
 $('clock').textContent=fmt(Date.now()+offset);
 document.querySelectorAll('[data-end]').forEach(el=>{const n=Math.max(0,Math.ceil((new Date(el.dataset.end)-Date.now()-offset)/1000));el.textContent='متبقي '+Math.floor(n/60)+':'+String(n%60).padStart(2,'0')});
 if(lastFetch&&!demo&&Date.now()-lastFetch>20000){$('connection').textContent='البيانات غير محدثة';notice('انقطع التحديث. لا تعتمد على مواعيد الشاشة حتى عودة الاتصال.',true)}
}
async function refresh(){if(demo||!token)return;try{state=await api('/api/state');offset=new Date(state.now)-Date.now();lastFetch=Date.now();$('connection').textContent='● مباشر';$('updated').textContent='آخر تحديث '+fmt(Date.now()+offset);if(!$('notice').dataset.import)notice('');render()}catch(e){$('connection').textContent='الاتصال متوقف';notice(e.message,true)}}
$('orientation').onchange=()=>{orientation();render()};
$('view').onchange=()=>{localStorage.setItem('crew-screen',$('view').value);$('orientation').value=localStorage.getItem('crew-orientation-'+$('view').value)||'auto';const q=new URLSearchParams(location.search);q.set('screen',$('view').value);q.delete('orientation');history.replaceState(null,'','?'+q);render()};
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch{notice('استخدم وضع ملء الشاشة من إعدادات المتصفح')}};
$('connect').onclick=()=>{$('setup').hidden=!$('setup').hidden};
$('login').onsubmit=async e=>{e.preventDefault();try{const u=new URL($('endpoint').value);if(u.protocol!=='https:'&&u.hostname!=='localhost'&&u.hostname!=='127.0.0.1')throw Error('استخدم رابط HTTPS آمن');if(u.username||u.password||u.search||u.hash)throw Error('أدخل رابط الخدمة فقط');endpoint=u.origin;const d=await api('/api/login',{password:$('password').value});token=d.token;role=d.role;demo=false;sessionStorage.setItem('crew-token',token);sessionStorage.setItem('crew-role',role);localStorage.setItem('crew-endpoint',endpoint);$('password').value='';$('setup').hidden=true;await refresh()}catch(e){notice(e.message,true)}};
$('logout').onclick=async()=>{try{await api('/api/logout',{})}catch{}token='';role='';state=null;demo=false;sessionStorage.removeItem('crew-token');sessionStorage.removeItem('crew-role');$('setup').hidden=false;render();notice('تم تسجيل الخروج')};
$('demo').onclick=()=>{demo=true;role='display';offset=0;const t=Date.now();state={flights:[...rooms.map((r,i)=>({id:'demo-'+i,room:r,number:'DEMO '+(101+i),destination:['القاهرة','دبي','لندن','بانكوك','الرياض','الدوحة','جدة'][i],sccm:'طاقم تجريبي '+(i+1),start:new Date(t-60000).toISOString(),end:new Date(t+540000).toISOString(),done:0})),{number:'DEMO 108',destination:'رحلة تجريبية',briefing:new Date(t+600000).toISOString(),done:0}]};$('setup').hidden=true;$('connection').textContent='عرض تجريبي';$('updated').textContent='بيانات وهمية · غير تشغيلية';notice('عرض تجريبي فقط — هذه ليست رحلات أو أسماء طاقم حقيقية.');render()};
$('file').onchange=$('kind').onchange=()=>{payload=null;$('commit').disabled=true;$('previewTable').innerHTML=''};
$('preview').onclick=async()=>{try{const f=$('file').files[0];if(!f)throw Error('اختر ملفًا أولًا');if(f.size>3000000)throw Error('الحد الأقصى 3 ميغابايت');const bytes=new Uint8Array(await f.arrayBuffer());let raw='';for(let i=0;i<bytes.length;i+=8192)raw+=String.fromCharCode(...bytes.subarray(i,i+8192));payload={name:f.name,data:btoa(raw),kind:$('kind').value};const result=await api('/api/preview',payload);const cols=payload.kind==='crew'?['number','date','sccm']:['number','destination','aircraft','source','briefing','sccm'];$('previewTable').innerHTML='<table><thead><tr>'+cols.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr></thead><tbody>'+result.rows.slice(0,100).map(r=>'<tr>'+cols.map(c=>'<td>'+esc(r[c])+'</td>').join('')+'</tr>').join('')+'</tbody></table>';$('commit').disabled=false;$('notice').dataset.import='1';notice('تمت معاينة '+result.rows.length+' صف. راجع المواعيد ثم اضغط اعتماد البيانات.')}catch(e){payload=null;$('commit').disabled=true;notice(e.message,true)}};
$('commit').onclick=async()=>{if(!payload)return;$('commit').disabled=true;try{const result=await api('/api/import',payload);payload=null;delete $('notice').dataset.import;await refresh();notice('تم حفظ '+result.count+' صف وتحديث الشاشات')}catch(e){notice(e.message,true);$('commit').disabled=false}};
$('board').onclick=async e=>{const btn=e.target.closest('[data-id]');if(!btn)return;btn.disabled=true;try{await api('/api/extend',{id:btn.dataset.id});await refresh()}catch(e){notice(e.message,true);btn.disabled=false}};
orientation();render();setInterval(tickClock,1000);setInterval(refresh,5000);if(token){$('setup').hidden=true;refresh()}
