'use strict';
const $=id=>document.getElementById(id),rooms=['A-1','B-2','C-3','D-4','E-5','F-6','H-8'];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=s=>new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',hour:'2-digit',minute:'2-digit'}).format(new Date(s));
const df=s=>new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',day:'2-digit',month:'2-digit'}).format(new Date(s));
const params=new URLSearchParams(location.search),screen=rooms.includes(params.get('screen'))?params.get('screen'):'hall';
const orientation=params.get('orientation')||'auto',demo=params.get('demo')==='1';
document.body.dataset.orientation=['auto','portrait','landscape'].includes(orientation)?orientation:'auto';
document.body.classList.toggle('roomview',screen!=='hall');$('upcoming').hidden=screen!=='hall';
$('title').textContent=screen==='hall'?'رحلتكم تبدأ من هنا':'غرفة الإحاطة '+screen;
const person='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3M9 14l3 6 3-6"/></svg>';
let state=null,offset=0,lastFetch=0,busy=false;
function render(){
 const t=Date.now()+offset,flights=state?.flights||[];
 $('board').innerHTML=(screen==='hall'?rooms:[screen]).map(room=>{
  const f=flights.find(f=>f.room===room&&!f.done&&f.start&&new Date(f.end)>t);
  let content='<div class="idle">'+(state?'بانتظار الرحلة القادمة':'بانتظار جدول الرحلات')+'</div><p>نتمنى لكم رحلة سعيدة</p>';
  if(f)content='<div class="flight" dir="ltr">'+esc(f.number)+'</div><div class="destination">'+esc(f.destination)+'</div><div class="sccm">'+person+'<b>SCCM</b><span>'+esc(f.sccm||'لم يُحدّد رئيس الطاقم')+'</span></div><div class="times"><span>'+fmt(f.start)+' – '+fmt(f.end)+'</span><span class="countdown" data-end="'+esc(f.end)+'"></span></div>';
  return '<article class="card '+(f?'active':state?'free':'waiting')+'"><div class="cardtop"><span class="room" dir="ltr">'+room+'</span><span class="status">'+(f?'الإحاطة جارية':state?'متاحة':'بانتظار التحديث')+'</span></div>'+content+'</article>';
 }).join('');
 const queue=flights.filter(f=>!f.done&&!f.start),page=Math.floor(t/15000)%Math.max(1,Math.ceil(queue.length/4)),upcoming=queue.slice(page*4,page*4+4);
 $('queue').innerHTML=upcoming.length?'<div class="qrow"><span>الرحلة</span><span>الوجهة</span><span>البريفينج</span><span>الحالة</span></div>'+upcoming.map(f=>'<div class="qrow"><span dir="ltr">'+esc(f.number)+'</span><span>'+esc(f.destination)+'</span><span dir="ltr">'+df(f.briefing)+' '+fmt(f.briefing)+'</span><span>'+(new Date(f.briefing)<=t?'بانتظار غرفة':'قادمة')+'</span></div>').join(''):'<p>'+(state?'لا توجد رحلات في قائمة الانتظار':'بانتظار تحديث جدول الرحلات')+'</p>';
 clock();
}
function clock(){
 $('clock').textContent=fmt(Date.now()+offset);
 document.querySelectorAll('[data-end]').forEach(el=>{const n=Math.max(0,Math.ceil((new Date(el.dataset.end)-Date.now()-offset)/1000));el.textContent='متبقي '+Math.floor(n/60)+':'+String(n%60).padStart(2,'0')});
 if(!demo&&lastFetch&&Date.now()-lastFetch>20000){document.body.classList.add('stale');$('notice').textContent='التحديث متوقف — يرجى مراجعة ضابط الترحيلات'}
}
async function refresh(){
 if(demo||busy)return;
 const endpoint=localStorage.getItem('crew-endpoint'),token=sessionStorage.getItem('crew-token');
 if(!endpoint||!token){state=null;lastFetch=0;$('notice').textContent='بانتظار تحديث جدول الرحلات';$('updated').textContent='';render();return}
 busy=true;
 try{
  const res=await fetch(endpoint+'/api/state',{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(12000)});
  if(!res.ok)throw Error('offline');
  state=await res.json();offset=new Date(state.now)-Date.now();lastFetch=Date.now();document.body.classList.remove('stale');
  $('notice').textContent='';$('updated').textContent='آخر تحديث '+fmt(Date.now()+offset);render();
 }catch{document.body.classList.add('stale');$('notice').textContent='التحديث متوقف — يرجى مراجعة ضابط الترحيلات'}
 finally{busy=false}
}
if(demo){
 const t=Date.now();
 state={flights:[...rooms.map((r,i)=>({id:'demo-'+i,room:r,number:'DEMO '+(101+i),destination:['القاهرة','دبي','لندن','بانكوك','الرياض','الدوحة','جدة'][i],sccm:'طاقم تجريبي '+(i+1),start:new Date(t-60000).toISOString(),end:new Date(t+540000).toISOString(),done:0})),{number:'DEMO 108',destination:'رحلة تجريبية',briefing:new Date(t+600000).toISOString(),done:0}]};
 $('notice').textContent='معاينة تجريبية — بيانات غير تشغيلية';$('updated').textContent='بيانات وهمية';
}
render();refresh();setInterval(clock,1000);setInterval(()=>{if(demo)render();else refresh()},5000);
